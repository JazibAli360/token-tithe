"""Phase 2: policy.yaml / repos.yaml / agents/*.yaml load strictly and enforce rules."""

from datetime import date

import pytest
import yaml

from token_tithe import paths
from token_tithe import policy as P


def _edit(path, mutate):
    data = yaml.safe_load(path.read_text())
    mutate(data)
    path.write_text(yaml.safe_dump(data))


def test_shipped_policy_is_valid_and_conservative():
    pol = P.load_policy()
    assert pol.trust is False  # review mode by default
    assert pol.confidence_threshold == 70  # R11
    assert pol.rate_limits.max_prs_per_repo_per_30_days == 3  # R4
    assert pol.rate_limits.max_prs_per_day == 5
    assert pol.rate_limits.max_comments_per_issue == 1
    assert pol.limits.max_diff_lines == 400  # R8
    assert pol.subagents.allow_subagents is False  # R13
    assert pol.sandbox.allow_unsandboxed is False  # R16
    assert pol.signing.required is True  # R18
    assert 0 < pol.limits.task_timeout_minutes <= 120  # D3 (owner set 90 on 2026-10-26)


def test_unknown_key_is_an_error(config_copy):
    f = config_copy / "policy.yaml"
    _edit(f, lambda d: d["rate_limits"].update(max_pr_per_day=99))
    with pytest.raises(P.PolicyError, match=r"rate_limits: unknown key"):
        P.load_policy(f)


def test_missing_key_is_an_error(config_copy):
    f = config_copy / "policy.yaml"
    _edit(f, lambda d: d.pop("confidence_threshold"))
    with pytest.raises(P.PolicyError, match="confidence_threshold: missing"):
        P.load_policy(f)


def test_wrong_type_is_an_error(config_copy):
    f = config_copy / "policy.yaml"
    _edit(f, lambda d: d.update(trust="yes"))
    with pytest.raises(P.PolicyError, match="trust: expected true/false"):
        P.load_policy(f)


@pytest.mark.parametrize(
    "mutate, msg",
    [
        (lambda d: d["limits"].update(max_diff_lines=900), "R8"),
        (lambda d: d["rate_limits"].update(max_comments_per_issue=2), "R4"),
        (lambda d: d.update(confidence_threshold=150), "0..100"),
        (lambda d: d["sandbox"].update(network="bridge"), "must be one of"),
        (
            lambda d: d["routing"]["by_donation_type"].update(memes="claude"),
            "unknown donation type",
        ),
    ],
)
def test_hard_rule_ceilings_cannot_be_configured_away(config_copy, mutate, msg):
    f = config_copy / "policy.yaml"
    _edit(f, mutate)
    with pytest.raises(P.PolicyError, match=msg):
        P.load_policy(f)


def test_licenses_permissive_only_unless_gpl_opt_in(config_copy):
    pol = P.load_policy()
    assert P.license_allowed(pol, "MIT")
    assert P.license_allowed(pol, "apache-2.0")
    assert not P.license_allowed(pol, "GPL-3.0")
    assert not P.license_allowed(pol, None)
    assert not P.license_allowed(pol, "NOASSERTION")
    f = config_copy / "policy.yaml"
    _edit(f, lambda d: d["licenses"].update(gpl_opt_in=True))
    assert P.license_allowed(P.load_policy(f), "GPL-3.0")


def test_rate_limits():
    pol = P.load_policy()
    ok = P.RateCounts(prs_to_repo_last_30_days=2, prs_today=4, comments_on_issue=0)
    assert P.rate_limit_violation(pol, ok, "pr") is None
    assert P.rate_limit_violation(pol, ok, "comment") is None
    assert P.rate_limit_violation(pol, P.RateCounts(3, 0, 0), "pr")
    assert P.rate_limit_violation(pol, P.RateCounts(0, 5, 0), "pr")
    assert P.rate_limit_violation(pol, P.RateCounts(0, 0, 1), "comment")


def test_review_mode_requires_live_and_trust(config_copy):
    pol = P.load_policy()
    assert P.review_mode(pol, live=False)
    assert P.review_mode(pol, live=True)  # trust is false by default
    f = config_copy / "policy.yaml"
    _edit(f, lambda d: d.update(trust=True))
    trusted = P.load_policy(f)
    assert P.review_mode(trusted, live=False)
    assert not P.review_mode(trusted, live=True)


def test_routing_precedence(config_copy):
    f = config_copy / "policy.yaml"
    _edit(
        f,
        lambda d: d["routing"].update(
            by_language={"rust": "codex"}, by_donation_type={"docs": "aider"}
        ),
    )
    pol = P.load_policy(f)
    assert P.route_agent(pol, "Rust", "issue_fix") == "codex"
    assert P.route_agent(pol, "rust", "docs") == "aider"
    assert P.route_agent(pol, "python", "tests") == "claude"


def test_blocklist_with_cooldown(config_copy):
    f = config_copy / "repos.yaml"
    f.write_text(
        "schema_version: 1\nblocklist:\n"
        "  - {repo: a/forever, reason: anti-ai, until: null}\n"
        "  - {repo: b/cooldown, reason: incident, until: 2026-12-01}\n"
    )
    repos = P.load_repos(f)
    assert P.blocked(repos, "A/Forever", date(2030, 1, 1))
    assert P.blocked(repos, "b/cooldown", date(2026, 11, 1))
    assert P.blocked(repos, "b/cooldown", date(2026, 12, 2)) is None
    assert P.blocked(repos, "c/other") is None


def test_blocklist_whole_owner(config_copy):
    f = config_copy / "repos.yaml"
    f.write_text(
        "schema_version: 1\nblocklist:\n  - {repo: meshery/*, reason: skip, until: null}\n"
    )
    repos = P.load_repos(f)
    assert P.blocked(repos, "Meshery/meshery.io") and P.blocked(repos, "meshery/play")
    assert P.blocked(repos, "meshery-extensions/meshery-kuma") is None  # owner must match exactly


def test_all_agent_configs_validate():
    agents = P.load_agents()
    assert {"claude", "codex", "aider", "generic"} <= set(agents)
    assert agents["claude"].accounted
    assert not agents["aider"].accounted  # rounded stdout numbers are not real accounting
    assert not agents["claude"].capacity_aware  # no CLI quota probe exists today


def test_claude_adapter_disables_subagent_tools():
    claude = P.load_agent("claude")
    i = claude.invoke.args.index("--disallowedTools")
    assert {"Task", "Agent"} <= set(claude.invoke.args[i + 1 :])


def test_agent_name_must_match_file(config_copy):
    f = config_copy / "agents" / "claude.yaml"
    f.write_text(f.read_text().replace("name: claude", "name: imposter"))
    with pytest.raises(P.PolicyError, match="must match file name"):
        P.load_agent(f)


def test_local_overlay_overrides_policy_and_extends_repos(config_copy):
    from token_tithe import local_config

    pol, repos = config_copy / "policy.yaml", config_copy / "repos.yaml"
    base = P.load_policy(pol)
    local_config.set_value(pol, "signing.committer_name", "Ada")
    local_config.set_value(pol, "budget.max_tokens_per_run", 123456)
    over = P.load_policy(pol)
    assert over.signing.committer_name == "Ada" and over.budget.max_tokens_per_run == 123456
    assert over.signing.format == base.signing.format, "untouched keys keep shared defaults"
    assert 'committer_name: ""' in pol.read_text(), "shared file never edited"

    shared = len(P.load_repos(repos).blocklist)
    local_config.append_item(repos, "blocklist", {"repo": "a/b", "reason": "mine", "until": None})
    assert len(P.load_repos(repos).blocklist) == shared + 1, "repos lists combine"


def test_bad_overlay_is_a_clear_policy_error(config_copy):
    (config_copy / "policy.local.yaml").write_text("budget: {max_tokens_per_run: lots}\n")
    with pytest.raises(P.PolicyError, match="max_tokens_per_run"):
        P.load_policy(config_copy / "policy.yaml")
    (config_copy / "policy.local.yaml").write_text("- not a mapping\n")
    with pytest.raises(P.PolicyError, match="policy.local.yaml"):
        P.load_policy(config_copy / "policy.yaml")


def test_real_config_ignores_overlay_under_tests():
    from token_tithe import local_config

    assert not local_config.overlay_enabled(paths.POLICY_FILE)
