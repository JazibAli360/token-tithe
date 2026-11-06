#!/usr/bin/env bash
# token-tithe quickstart — one command from a fresh clone to a working, safe (dry-run) setup.
#
#   make quickstart                       # interactive: asks before every install/write
#   ./scripts/quickstart.sh --yes         # agents: the human already said OK to installs + config
#   ./scripts/quickstart.sh --agent codex # pick the task agent (claude | codex | aider)
#
# Safe to re-run any time. It never opens a PR, never comments, never stores a secret in the
# repo. Your settings go to config/*.local.yaml (gitignored), so `git pull` stays clean.
# Without a terminal and without --yes it changes nothing outside .venv/ and lists what it skipped.
set -euo pipefail
cd "$(dirname "$0")/.."

ASSUME_YES=0
AGENT=""
while [ $# -gt 0 ]; do
  case "$1" in
    -y|--yes) ASSUME_YES=1 ;;
    --agent)
      [ $# -ge 2 ] || { echo "--agent needs a value: claude, codex or aider"; exit 2; }
      AGENT="$2"; shift ;;
    --agent=*) AGENT="${1#--agent=}" ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "unknown option: $1 (try --help)"; exit 2 ;;
  esac
  shift
done

HUMAN=()     # things only the human can do (sign-ins, browser approvals, shell profile)
SKIPPED=()   # things skipped for lack of consent
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then B=$'\033[1m'; G=$'\033[32m'; Y=$'\033[33m'; R=$'\033[0m'
else B=""; G=""; Y=""; R=""; fi
bold() { printf '\n%s%s%s\n' "$B" "$*" "$R"; }
ok()   { printf '  %s✓%s %s\n' "$G" "$R" "$*"; }
warn() { printf '  %s!%s %s\n' "$Y" "$R" "$*"; }
have() { command -v "$1" >/dev/null 2>&1; }
ask()  {  # ask "question" -> 0 for yes
  if [ "$ASSUME_YES" = 1 ]; then echo "  $1 → yes (--yes)"; return 0; fi
  if [ -t 0 ]; then local a; read -r -p "  $1 [y/N] " a; [[ "$a" =~ ^[Yy] ]]; return; fi
  echo "  $1 → skipped (no terminal; re-run with --yes to allow)"; SKIPPED+=("$1"); return 1
}
OS="$(uname -s)"
PY=.venv/bin/python
pol() {  # pol <dotted.key>: read the EFFECTIVE policy value (shared + your overlay)
  "$PY" -c 'import sys, dataclasses
from token_tithe.policy import load_policy
v = load_policy()
for k in sys.argv[1].split("."): v = getattr(v, k)
print(v)' "$1"
}
setlocal() {  # setlocal <dotted.key> <value>: write to config/policy.local.yaml (never the shared file)
  "$PY" -c 'import sys
from token_tithe import local_config, paths
from token_tithe.policy import load_policy
local_config.set_value(paths.POLICY_FILE, sys.argv[1], sys.argv[2]); load_policy()' "$1" "$2"
}

# --------------------------------------------------------------------------- 1. prerequisites
bold "1/8  Prerequisites"
py_ok=""
for p in python3.13 python3.12 python3.11 python3; do
  if have "$p" && "$p" -c 'import sys; sys.exit(sys.version_info < (3,11))' 2>/dev/null; then
    py_ok="$p"; break
  fi
done
if [ -n "$py_ok" ]; then ok "Python ≥ 3.11 ($py_ok)"
elif have uv; then ok "uv (will provision Python 3.12 inside .venv/)"
else
  hint=""
  if [ "$OS" = Darwin ] && have brew; then hint="brew install uv"
  elif have pipx; then hint="pipx install uv"; fi
  if [ -n "$hint" ] && ask "Python ≥ 3.11 not found. Install uv (it provides one) with: $hint ?"; then
    $hint || true
  fi
  have uv || { echo "BLOCKED: need Python ≥ 3.11 or uv — see https://docs.astral.sh/uv/ and re-run."; exit 1; }
  ok "uv installed"
fi
have git || { echo "BLOCKED: git is required. Install it and re-run."; exit 1; }
ok "git"
if ! have gh; then
  if [ "$OS" = Darwin ] && have brew && ask "GitHub CLI missing. Install with: brew install gh ?"; then
    brew install gh || true
  fi
  have gh || { echo "BLOCKED: the GitHub CLI (gh) is required — https://cli.github.com/ (Linux: use its apt repo; distro packages are often too old). Re-run after."; exit 1; }
fi
gh_ver="$(gh --version | sed -n 's/^gh version \([0-9]*\)\.\([0-9]*\).*/\1 \2/p')"
set -- $gh_ver
if [ "${1:-0}" -lt 2 ] || { [ "${1:-0}" -eq 2 ] && [ "${2:-0}" -lt 17 ]; }; then
  echo "BLOCKED: gh ${1:-?}.${2:-?} is too old (need ≥ 2.17) — install from https://cli.github.com/ and re-run."; exit 1
fi
ok "gh (GitHub CLI)"
DOCKER=0
if have docker && docker info >/dev/null 2>&1; then DOCKER=1; ok "Docker running"
else warn "Docker not running — recommended: it isolates the repos' code from your machine; required for real PRs."; fi

# --------------------------------------------------------------------------- 2. venv
bold "2/8  Installing token-tithe into ./.venv (nothing touches your system Python)"
make --no-print-directory setup
ok "installed: .venv/bin/donate"

# --------------------------------------------------------------------------- 3. task agent
bold "3/8  Task agent (the coding agent that writes the fixes)"
cur_agent="$(pol routing.default)"
if [ -z "$AGENT" ]; then
  if have "$cur_agent"; then AGENT="$cur_agent"
  else for a in claude codex aider; do have "$a" && { AGENT="$a"; break; }; done; fi
fi
AGENT="${AGENT:-$cur_agent}"
case "$AGENT" in claude|codex|aider) ;; *)
  echo "BLOCKED: --agent must be claude, codex or aider (others: docs/AGENT_INTEGRATION.md)"; exit 2 ;;
esac
if ! have "$AGENT"; then
  case "$AGENT" in claude) pkg="@anthropic-ai/claude-code" ;; codex) pkg="@openai/codex" ;; *) pkg="" ;; esac
  if [ -n "$pkg" ] && have npm && ask "Install the $AGENT CLI with: npm i -g $pkg ?"; then
    npm i -g "$pkg" || warn "npm install failed (permissions? see https://docs.npmjs.com/resolving-eacces-permissions-errors)"
  elif [ "$AGENT" = aider ] && have pipx && ask "Install aider with: pipx install aider-chat ?"; then
    pipx install aider-chat || warn "pipx install aider-chat failed"
  fi
  if have "$AGENT"; then ok "$AGENT CLI installed"
  else warn "$AGENT CLI not installed — needed for real runs (not for the offline self-test)"; fi
fi
if [ "$cur_agent" = "$AGENT" ]; then ok "routing.default: $AGENT"
elif ask "Route tasks to $AGENT (now '$cur_agent'; saved in config/policy.local.yaml)?"; then
  setlocal routing.default "$AGENT" && ok "routing.default: $AGENT"
  [ "$AGENT" = claude ] || warn "recommended once: make eval AGENTS=reference,null,$AGENT  (spends tokens)"
fi
case "$AGENT" in
  claude) have claude && { claude auth status >/dev/null 2>&1 && ok "claude signed in" \
            || HUMAN+=("Sign in the Claude Code CLI:  claude auth login"); } ;;
  codex)  have codex && { codex login status >/dev/null 2>&1 && ok "codex signed in" \
            || HUMAN+=("Sign in the Codex CLI:  codex login"); } ;;
  aider)  HUMAN+=("Give aider a model key in your shell profile, e.g. export ANTHROPIC_API_KEY=… or OPENAI_API_KEY=…") ;;
esac

# --------------------------------------------------------------------------- 4. GitHub
bold "4/8  GitHub sign-in"
if ! gh auth status >/dev/null 2>&1; then
  if [ -t 0 ] && ask "You're not signed in to gh. Run 'gh auth login' now?"; then gh auth login || true
  else HUMAN+=("Sign in to GitHub:  gh auth login   (then re-run make quickstart)"); fi
fi
GH_LOGIN=""
if gh auth status >/dev/null 2>&1; then
  GH_LOGIN="$(gh api user --jq .login 2>/dev/null || true)"
  ok "signed in as ${GH_LOGIN:-?}"
  if [ -z "${GITHUB_TOKEN:-}" ]; then GITHUB_TOKEN="$(gh auth token 2>/dev/null || true)"; export GITHUB_TOKEN; fi
fi

# --------------------------------------------------------------------------- 5. identity + signing
bold "5/8  Commit identity and signing (who the PR commits are from)"
cur_name="$(pol signing.committer_name)"; cur_email="$(pol signing.committer_email)"
if [ -n "$cur_name" ] && [ -n "$cur_email" ]; then
  ok "identity: $cur_name <$cur_email>"
elif [ -n "$GH_LOGIN" ]; then
  gh_id="$(gh api user --jq .id 2>/dev/null || true)"
  def_name="$(gh api user --jq '.name // .login' 2>/dev/null || echo "$GH_LOGIN")"
  if [ -n "$gh_id" ]; then
    def_email="${gh_id}+${GH_LOGIN}@users.noreply.github.com"
    echo "  Suggested: $def_name <$def_email>  (GitHub's private noreply address)"
    echo "  A dedicated bot account is cleanest for R18 — see docs/ONBOARDING.md §7."
    if ask "Use this identity (saved in config/policy.local.yaml)?"; then
      setlocal signing.committer_name "$def_name"
      setlocal signing.committer_email "$def_email"
      ok "identity saved"
    fi
  else warn "couldn't read your GitHub profile (network?) — re-run to set the identity"; fi
else
  warn "sign in to gh first, then re-run to set the identity"
fi

SIGNKEY="$HOME/.ssh/token_tithe_signing"
if [ -n "${TITHE_SIGNING_KEY:-}" ]; then ok "TITHE_SIGNING_KEY is set"
else
  if [ ! -f "$SIGNKEY.pub" ] && have ssh-keygen \
     && ask "Create a dedicated commit-signing key at ~/.ssh/token_tithe_signing?"; then
    mkdir -p "$HOME/.ssh" && chmod 700 "$HOME/.ssh"
    ssh-keygen -q -t ed25519 -N "" -C "token-tithe signing" -f "$SIGNKEY" && ok "signing key created"
  fi
  if [ -f "$SIGNKEY.pub" ]; then
    if [ -n "$GH_LOGIN" ] && gh ssh-key list 2>/dev/null | grep -q "token-tithe"; then
      ok "signing key registered on GitHub"
    elif [ -n "$GH_LOGIN" ] && ask "Register it on GitHub as a *signing* key (gh ssh-key add)?"; then
      if gh ssh-key add "$SIGNKEY.pub" --type signing --title "token-tithe" >/dev/null 2>&1; then
        ok "signing key registered on GitHub"
      else
        HUMAN+=("Let gh add signing keys, then register it:  gh auth refresh -h github.com -s admin:ssh_signing_key && gh ssh-key add ~/.ssh/token_tithe_signing.pub --type signing --title token-tithe")
      fi
    fi
    HUMAN+=("For real PRs, add to your shell profile (~/.zshrc or ~/.bashrc):  export TITHE_SIGNING_KEY=\$HOME/.ssh/token_tithe_signing.pub")
  else
    HUMAN+=("For real PRs: re-run make quickstart and say yes to the signing key (or see docs/ONBOARDING.md §7)")
  fi
fi

# --------------------------------------------------------------------------- 6. sandbox image
bold "6/8  Sandbox image (isolates the repos' code; needed for real PRs)"
if [ "$DOCKER" = 1 ]; then
  if docker image inspect token-tithe-sandbox:latest >/dev/null 2>&1; then ok "sandbox image present"
  elif ask "Build the sandbox image now (make sandbox-image, a few minutes, ~1–2 GB)?"; then
    make --no-print-directory sandbox-image && ok "sandbox image built"
  else warn "later: make sandbox-image"; fi
else
  HUMAN+=("Install/start Docker (Docker Desktop on macOS), then: make sandbox-image")
fi

# --------------------------------------------------------------------------- 7. prove it
bold "7/8  Offline self-test (fixture repos, no network, no GitHub writes)"
SIM=FAIL; DOC=FAIL
make --no-print-directory simulate && SIM=PASS
bold "     Health check"
if .venv/bin/donate doctor; then DOC=PASS
else warn "doctor reported FAILs above — fix those first (WARNs only block --live)"; fi

# --------------------------------------------------------------------------- 8. summary
bold "8/8  Summary"
echo "  self-test (simulate): $SIM · doctor: $DOC · task agent: $AGENT · GitHub: ${GH_LOGIN:-not signed in}"
echo "  your settings: config/policy.local.yaml (gitignored — edit freely; 'make update' never touches it)"
if [ ${#HUMAN[@]} -gt 0 ]; then
  echo; echo "  NEEDS A HUMAN (an agent can't do these for you):"
  for h in "${HUMAN[@]}"; do echo "    - $h"; done
fi
if [ ${#SKIPPED[@]} -gt 0 ]; then
  echo; echo "  SKIPPED (no consent given; re-run with --yes once you're OK with them):"
  for s in "${SKIPPED[@]}"; do echo "    - $s"; done
fi
cat <<'TXT'

  NEXT — all safe, nothing reaches GitHub until you add --live yourself:
    export GITHUB_TOKEN=$(gh auth token)     # or a classic token with only `public_repo` (safer)
    .venv/bin/donate capacity                # what this run can afford
    .venv/bin/donate discover --dry-run      # issues it would look at
    .venv/bin/donate run                     # agents work locally; proposals -> state/review/
    .venv/bin/donate review                  # read each proposal's diff
  To open one as a real PR:  .venv/bin/donate review --open 1 --live
  Stay current:  make update     ·   Stop everything:  .venv/bin/donate halt
  In Claude Code: /donate, /proposals, /donate-status   ·   Full guide: docs/ONBOARDING.md
TXT
[ "$SIM" = PASS ]
