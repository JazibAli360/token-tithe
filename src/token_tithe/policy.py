"""Load, validate and enforce everything in config/.

Config is parsed into frozen dataclasses. Unknown keys, missing keys and wrong types are
errors (PolicyError) naming the exact path, so a typo can never silently disable a guard.
"""

from __future__ import annotations

import dataclasses
import types
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Literal, Union, get_args, get_origin, get_type_hints

import yaml

from token_tithe import paths

DONATION_TYPES = ("issue_fix", "tests", "docs", "ci_repro", "triage_report")


class PolicyError(ValueError):
    """Config is invalid. The message names the file and key path."""


# --------------------------------------------------------------------------- schema


@dataclass(frozen=True)
class Budget:
    max_tokens_per_run: int
    min_viable_tokens: int
    default_task_tokens: dict[str, int]


@dataclass(frozen=True)
class RateLimits:
    max_prs_per_repo_per_30_days: int
    max_prs_per_day: int
    max_comments_per_issue: int


@dataclass(frozen=True)
class Licenses:
    allow: list[str]
    gpl_opt_in: bool
    gpl_family: list[str]


@dataclass(frozen=True)
class Discovery:
    languages: list[str]
    labels: list[str]
    min_stars: int
    max_stars: int
    pushed_within_days: int
    per_query_limit: int
    exclude_recent_repo_days: int
    claim_window_days: int = 14
    impact_topics: list[str] = field(default_factory=list)  # ranked up; also searched
    impact_repos_per_topic: int = 5
    # extra labels searched only in impact-topic repos (star-filtered), ORed into one query
    impact_labels: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Limits:
    max_diff_lines: int
    task_timeout_minutes: int
    max_tasks_per_run: int


@dataclass(frozen=True)
class Subagents:
    allow_subagents: bool
    max_subagents_per_task: int


@dataclass(frozen=True)
class Sandbox:
    allow_unsandboxed: bool
    image: str
    network: Literal["none"]
    agent_network: Literal["none", "bridge"]
    install_proxy: str
    install_allowlist: list[str]
    # extra env for one repo's install/test/lint runs, keyed "owner/name": only switches the
    # repo's own CI sets for runners like ours (e.g. no GPU), never to skip failing tests
    repo_env: dict[str, dict[str, str]] = field(default_factory=dict)
    # extra Python packages for one repo's venv, keyed "owner/name": only optional dependencies
    # the repo itself declares, so a code path its CI covers is verified here too
    repo_extra_deps: dict[str, list[str]] = field(default_factory=dict)


@dataclass(frozen=True)
class Signing:
    required: bool
    format: Literal["ssh", "openpgp"]
    committer_name: str
    committer_email: str
    signing_key_env: str
    dco_signoff: bool = False  # add `Signed-off-by` when the target repo requires the DCO


@dataclass(frozen=True)
class SecondReview:
    required: bool
    weight: float
    agent: str
    model: str


@dataclass(frozen=True)
class Address:
    max_rounds: int
    window_days: int
    max_diff_lines: int


@dataclass(frozen=True)
class DonationWindow:
    cycle_end: date | None
    expiring_days: int
    review_mode_hours_before: int


@dataclass(frozen=True)
class Security:
    secrets_scan: bool
    report_via_advisory: bool


@dataclass(frozen=True)
class Routing:
    default: str
    by_language: dict[str, str]
    by_donation_type: dict[str, str]


@dataclass(frozen=True)
class DonationTypeCfg:
    enabled: bool
    languages: list[str]


@dataclass(frozen=True)
class BrainCfg:
    max_lines: int
    stale_after_days: int
    auto_inject: bool
    max_injected: int
    sync_remote: str


@dataclass(frozen=True)
class FeedbackCfg:
    stale_after_days: int
    incident_cooldown_days: int


@dataclass(frozen=True)
class Policy:
    schema_version: int
    trust: bool
    budget: Budget
    confidence_threshold: int
    rate_limits: RateLimits
    licenses: Licenses
    discovery: Discovery
    limits: Limits
    subagents: Subagents
    sandbox: Sandbox
    signing: Signing
    second_review: SecondReview
    address: Address
    donation_window: DonationWindow
    security: Security
    routing: Routing
    donation_types: dict[str, DonationTypeCfg]
    feedback: FeedbackCfg
    brain: BrainCfg


@dataclass(frozen=True)
class BlockEntry:
    repo: str
    reason: str
    until: date | None = None


@dataclass(frozen=True)
class Repos:
    schema_version: int
    favorites: list[str] = field(default_factory=list)
    adopted: list[str] = field(default_factory=list)
    blocklist: list[BlockEntry] = field(default_factory=list)
    cla_signed: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Invoke:
    args: list[str]
    prompt_entry: Literal["argument", "stdin", "file"]
    forbidden_args: list[str]
    env_passthrough: list[str]
    result_path: str | None = None  # where the final text answer lives in the output


@dataclass(frozen=True)
class UsageSpec:
    source: Literal["json_stdout", "jsonl_stdout", "none"]
    event_type: str | None
    model_path: str | None
    input_paths: list[str]
    output_paths: list[str]
    cost_path: str | None


@dataclass(frozen=True)
class CapacityProbe:
    auth: list[str] | None
    models: list[str] | None
    quota: list[str] | None


@dataclass(frozen=True)
class AgentConfig:
    schema_version: int
    name: str
    display_name: str
    binary: str
    invoke: Invoke
    usage: UsageSpec
    capacity_probe: CapacityProbe
    models: list[str]
    notes: str = ""

    @property
    def accounted(self) -> bool:
        """True if the agent reports real usage (R10)."""
        return self.usage.source != "none" and bool(self.usage.output_paths)

    @property
    def capacity_aware(self) -> bool:
        """True if the agent exposes a real remaining-quota probe (Addendum A)."""
        return self.capacity_probe.quota is not None


# --------------------------------------------------------------------------- builder


def _type_name(tp: Any) -> str:
    return getattr(tp, "__name__", None) or str(tp).replace("typing.", "")


def _build(tp: Any, value: Any, where: str) -> Any:
    """Coerce `value` into type `tp`, raising PolicyError with the key path on mismatch."""
    origin = get_origin(tp)

    if origin in (Union, types.UnionType):
        options = get_args(tp)
        if value is None and type(None) in options:
            return None
        errors = []
        for opt in options:
            if opt is type(None):
                continue
            try:
                return _build(opt, value, where)
            except PolicyError as e:
                errors.append(str(e))
        raise PolicyError(errors[-1] if errors else f"{where}: invalid value {value!r}")

    if origin is Literal:
        if value not in get_args(tp):
            raise PolicyError(f"{where}: must be one of {list(get_args(tp))}, got {value!r}")
        return value

    if dataclasses.is_dataclass(tp):
        if not isinstance(value, dict):
            raise PolicyError(f"{where}: expected a mapping, got {type(value).__name__}")
        hints = get_type_hints(tp)
        known = {f.name for f in dataclasses.fields(tp)}
        unknown = set(value) - known
        if unknown:
            raise PolicyError(f"{where}: unknown key(s) {sorted(unknown)}")
        kwargs = {}
        for f in dataclasses.fields(tp):
            sub = f"{where}.{f.name}" if where else f.name
            if f.name in value:
                kwargs[f.name] = _build(hints[f.name], value[f.name], sub)
            elif f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING:
                raise PolicyError(f"{sub}: missing required key")
        return tp(**kwargs)

    if origin is list:
        if not isinstance(value, list):
            raise PolicyError(f"{where}: expected a list, got {type(value).__name__}")
        (item,) = get_args(tp)
        return [_build(item, v, f"{where}[{i}]") for i, v in enumerate(value)]

    if origin is dict:
        if not isinstance(value, dict):
            raise PolicyError(f"{where}: expected a mapping, got {type(value).__name__}")
        k_tp, v_tp = get_args(tp)
        return {_build(k_tp, k, where): _build(v_tp, v, f"{where}.{k}") for k, v in value.items()}

    if tp is bool:
        if not isinstance(value, bool):
            raise PolicyError(f"{where}: expected true/false, got {value!r}")
        return value
    if tp is int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise PolicyError(f"{where}: expected an integer, got {value!r}")
        return value
    if tp is float:
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise PolicyError(f"{where}: expected a number, got {value!r}")
        return float(value)
    if tp is str:
        if not isinstance(value, str):
            raise PolicyError(f"{where}: expected a string, got {value!r}")
        return value
    if tp is date:
        if isinstance(value, date):
            return value
        try:
            return date.fromisoformat(str(value))
        except ValueError:
            raise PolicyError(f"{where}: expected a YYYY-MM-DD date, got {value!r}") from None
    raise PolicyError(f"{where}: unsupported type {_type_name(tp)}")  # pragma: no cover


def _read_yaml(path: Path) -> dict:
    if not path.is_file():
        raise PolicyError(f"{paths.rel(path)}: file not found")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise PolicyError(f"{paths.rel(path)}: invalid YAML: {e}") from None
    if not isinstance(data, dict):
        raise PolicyError(f"{paths.rel(path)}: top level must be a mapping")
    return data


def _load(tp: type, path: Path) -> Any:
    try:
        return _build(tp, _read_yaml(path), "")
    except PolicyError as e:
        msg = str(e)
        if not msg.startswith(paths.rel(path)):
            msg = f"{paths.rel(path)}: {msg}"
        raise PolicyError(msg) from None


# --------------------------------------------------------------------------- loading


def load_policy(path: Path = paths.POLICY_FILE) -> Policy:
    policy: Policy = _load(Policy, path)
    _check_policy(policy, path)
    return policy


# The eval fixtures' repos have 95-812 stars. The owner's discovery.min_stars/max_stars pick real
# repos; offline runs (tests, simulate, --fixtures) swap in this range so they still exercise
# the pipeline.
FIXTURE_STARS = (50, 5000)


def for_fixtures(policy: Policy) -> Policy:
    lo, hi = FIXTURE_STARS
    return dataclasses.replace(
        policy, discovery=dataclasses.replace(policy.discovery, min_stars=lo, max_stars=hi)
    )


def load_repos(path: Path = paths.REPOS_FILE) -> Repos:
    return _load(Repos, path)


def load_agent(name_or_path: str | Path, directory: Path = paths.AGENTS_CONFIG) -> AgentConfig:
    path = Path(name_or_path)
    if path.suffix != ".yaml":
        path = directory / f"{name_or_path}.yaml"
    cfg: AgentConfig = _load(AgentConfig, path)
    if cfg.name != path.stem:
        raise PolicyError(f"{paths.rel(path)}: name {cfg.name!r} must match file name")
    if not cfg.models:
        raise PolicyError(f"{paths.rel(path)}: models must list at least one entry")
    return cfg


def load_agents(directory: Path = paths.AGENTS_CONFIG) -> dict[str, AgentConfig]:
    return {p.stem: load_agent(p, directory) for p in sorted(directory.glob("*.yaml"))}


def _check_policy(p: Policy, path: Path) -> None:
    where = paths.rel(path)

    def fail(msg: str) -> None:
        raise PolicyError(f"{where}: {msg}")

    if p.schema_version != 1:
        fail(f"schema_version {p.schema_version} is not supported (expected 1)")
    if not 0 <= p.confidence_threshold <= 100:
        fail("confidence_threshold must be 0..100")
    if p.budget.min_viable_tokens > p.budget.max_tokens_per_run:
        fail("budget.min_viable_tokens cannot exceed budget.max_tokens_per_run")
    if set(p.budget.default_task_tokens) != set(DONATION_TYPES):
        fail(f"budget.default_task_tokens must define exactly {list(DONATION_TYPES)}")
    if set(p.donation_types) != set(DONATION_TYPES):
        fail(f"donation_types must define exactly {list(DONATION_TYPES)}")
    for t in p.routing.by_donation_type:
        if t not in DONATION_TYPES:
            fail(f"routing.by_donation_type: unknown donation type {t!r}")
    if p.discovery.min_stars > p.discovery.max_stars:
        fail("discovery.min_stars cannot exceed discovery.max_stars")
    if not 0.0 <= p.second_review.weight <= 1.0:
        fail("second_review.weight must be 0..1")
    if p.limits.max_diff_lines > 400:
        fail("limits.max_diff_lines may not exceed 400 (R8)")
    if p.rate_limits.max_comments_per_issue > 1:
        fail("rate_limits.max_comments_per_issue may not exceed 1 (R4)")
    if p.subagents.allow_subagents and p.subagents.max_subagents_per_task < 1:
        fail("subagents.max_subagents_per_task must be >= 1 when subagents are allowed")
    if not p.subagents.allow_subagents and p.subagents.max_subagents_per_task != 1:
        fail("subagents.max_subagents_per_task must stay 1 while allow_subagents is false")


# --------------------------------------------------------------------------- enforcement


def license_allowed(policy: Policy, spdx_id: str | None) -> bool:
    """R3: permissive allowlist; GPL family only when opted in; unknown/none is refused."""
    if not spdx_id:
        return False
    norm = spdx_id.strip().lower()
    if norm in {x.lower() for x in policy.licenses.allow}:
        return True
    if policy.licenses.gpl_opt_in and norm in {x.lower() for x in policy.licenses.gpl_family}:
        return True
    return False


def blocked(repos: Repos, repo: str, today: date | None = None) -> BlockEntry | None:
    """Return the active blocklist entry for `repo`, if any (cooldowns expire).
    `owner/*` blocks every repo of that owner."""
    today = today or date.today()
    owner = repo.split("/", 1)[0].lower()
    for entry in repos.blocklist:
        hit = entry.repo.lower() in (repo.lower(), f"{owner}/*")
        if hit and (entry.until is None or entry.until > today):
            return entry
    return None


def route_agent(policy: Policy, language: str | None, donation_type: str) -> str:
    """§6 routing: donation type beats language beats default."""
    if donation_type in policy.routing.by_donation_type:
        return policy.routing.by_donation_type[donation_type]
    if language and language.lower() in {k.lower() for k in policy.routing.by_language}:
        return next(
            v for k, v in policy.routing.by_language.items() if k.lower() == language.lower()
        )
    return policy.routing.default


def donation_type_allowed(policy: Policy, donation_type: str, language: str | None) -> bool:
    cfg = policy.donation_types.get(donation_type)
    if cfg is None or not cfg.enabled:
        return False
    if not cfg.languages:
        return True
    return bool(language) and language.lower() in {x.lower() for x in cfg.languages}


@dataclass(frozen=True)
class RateCounts:
    """Counts the caller derives from the ledger for one prospective action."""

    prs_to_repo_last_30_days: int
    prs_today: int
    comments_on_issue: int


def rate_limit_violation(policy: Policy, counts: RateCounts, action: str) -> str | None:
    """R4: return a reason string if `action` ('pr' | 'comment') would exceed a limit."""
    rl = policy.rate_limits
    if action == "pr":
        if counts.prs_to_repo_last_30_days >= rl.max_prs_per_repo_per_30_days:
            return f"repo already has {counts.prs_to_repo_last_30_days} PRs from us in 30 days"
        if counts.prs_today >= rl.max_prs_per_day:
            return f"daily PR cap reached ({counts.prs_today}/{rl.max_prs_per_day})"
        return None
    if action == "comment":
        if counts.comments_on_issue >= rl.max_comments_per_issue:
            return "already commented on this issue (max 1 ever)"
        return None
    raise ValueError(f"unknown action {action!r}")


def review_mode(policy: Policy, live: bool) -> bool:
    """R5/§4: proposals go to the review queue unless live AND trusted."""
    return not (live and policy.trust)


def parse_typed(tp: type, data: object, source: str) -> Any:
    """Strictly parse `data` into dataclass `tp`; errors are prefixed with `source`."""
    try:
        return _build(tp, data, "")
    except PolicyError as e:
        raise PolicyError(f"{source}: {e}") from None
