#!/usr/bin/env bash
# token-tithe quickstart — one command from a fresh clone to a working, safe (dry-run) setup.
#
#   make quickstart                       # interactive: asks before every install/write
#   ./scripts/quickstart.sh --yes         # agents: the human already said OK to installs + config
#   ./scripts/quickstart.sh --agent codex # pick the task agent (claude | codex | aider)
#
# Safe to re-run any time. It never opens a PR, never comments, never stores a secret:
# everything it does is local, or read-only against GitHub. Without a terminal and without
# --yes it changes nothing outside .venv/ and lists what it skipped at the end.
set -euo pipefail
cd "$(dirname "$0")/.."

ASSUME_YES=0
AGENT=""
while [ $# -gt 0 ]; do
  case "$1" in
    -y|--yes) ASSUME_YES=1 ;;
    --agent) AGENT="${2:-}"; shift ;;
    --agent=*) AGENT="${1#--agent=}" ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "unknown option: $1 (try --help)"; exit 2 ;;
  esac
  shift
done

HUMAN=()     # things only the human can do (sign-ins, passwords)
SKIPPED=()   # things skipped for lack of consent
bold() { printf '\n\033[1m%s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
have() { command -v "$1" >/dev/null 2>&1; }
ask()  {  # ask "question" -> 0 for yes
  if [ "$ASSUME_YES" = 1 ]; then echo "  $1 → yes (--yes)"; return 0; fi
  if [ -t 0 ]; then local a; read -r -p "  $1 [y/N] " a; [[ "$a" =~ ^[Yy] ]]; return; fi
  echo "  $1 → skipped (no terminal; re-run with --yes to allow)"; SKIPPED+=("$1"); return 1
}

OS="$(uname -s)"
install_hint() {  # install_hint <brew-formula> <apt-package> <url>
  if [ "$OS" = Darwin ] && have brew; then echo "brew install $1"
  elif have apt-get; then echo "sudo apt-get install -y $2"
  else echo "see $3"; fi
}
try_install() {  # try_install <cmd> <brew-formula> <apt-package> <url>
  local cmd="$1" hint; hint="$(install_hint "$2" "$3" "$4")"
  have "$cmd" && return 0
  warn "$cmd is missing ($hint)"
  if [[ "$hint" != see* ]] && ask "Install $cmd now with: $hint ?"; then
    eval "$hint" && have "$cmd" && ok "$cmd installed" && return 0
  fi
  return 1
}
set_yaml() {  # set_yaml <file> <indented key> <value>: replace one scalar in place, keep comments
  .venv/bin/python - "$@" <<'PY'
import re, sys
path, key, val = sys.argv[1], sys.argv[2], sys.argv[3].replace('"', "'")
text = open(path, encoding="utf-8").read()
new, n = re.subn(rf'^({re.escape(key)}:\s*)("[^"]*"|[^\s#]+)', lambda m: f'{m.group(1)}"{val}"'
                 if m.group(2).startswith('"') else f"{m.group(1)}{val}", text, count=1, flags=re.M)
if not n:
    sys.exit(f"{path}: {key.strip()} not found")
open(path, "w", encoding="utf-8").write(new)
PY
}
POLICY=config/policy.yaml

# --------------------------------------------------------------------------- 1. prerequisites
bold "1/7  Prerequisites"
py_ok=""
for p in python3.13 python3.12 python3.11 python3; do
  if have "$p" && "$p" -c 'import sys; sys.exit(sys.version_info < (3,11))' 2>/dev/null; then
    py_ok="$p"; break
  fi
done
if [ -n "$py_ok" ]; then ok "Python ≥ 3.11 ($py_ok)"
elif have uv; then ok "uv (will provision Python 3.12 inside .venv/)"
else
  try_install uv uv pipx "https://docs.astral.sh/uv/" \
    || { echo "BLOCKED: need Python ≥ 3.11 or uv. Install one and re-run."; exit 1; }
fi
have git || { echo "BLOCKED: git is required. Install it and re-run."; exit 1; }
ok "git"
try_install gh gh gh "https://cli.github.com" \
  || { echo "BLOCKED: the GitHub CLI (gh) is required. Install it and re-run."; exit 1; }
ok "gh (GitHub CLI)"
if have docker && docker info >/dev/null 2>&1; then ok "Docker running (needed only for --live)"
else warn "Docker not running — fine for now; opening real PRs needs it (Docker Desktop / docker.io)."; fi

# --------------------------------------------------------------------------- 2. venv
bold "2/7  Installing token-tithe into ./.venv (nothing touches your system Python)"
make --no-print-directory setup
ok "installed: .venv/bin/donate"

# --------------------------------------------------------------------------- 3. task agent
bold "3/7  Task agent (the coding agent that writes the fixes)"
npm_pkg() { case "$1" in claude) echo "@anthropic-ai/claude-code" ;; codex) echo "@openai/codex" ;; esac; }
if [ -z "$AGENT" ]; then
  for a in claude codex aider; do have "$a" && { AGENT="$a"; break; }; done
fi
AGENT="${AGENT:-claude}"
case "$AGENT" in claude|codex|aider) ;; *)
  echo "BLOCKED: --agent must be claude, codex or aider (others: docs/AGENT_INTEGRATION.md)"; exit 2 ;;
esac
if ! have "$AGENT"; then
  pkg="$(npm_pkg "$AGENT")"
  if [ -n "$pkg" ] && have npm && ask "Install the $AGENT CLI with: npm i -g $pkg ?"; then
    npm i -g "$pkg" && ok "$AGENT CLI installed"
  elif [ "$AGENT" = aider ] && have pipx && ask "Install aider with: pipx install aider-chat ?"; then
    pipx install aider-chat && ok "aider installed"
  else
    warn "$AGENT CLI not installed — needed for real runs (not for the offline self-test)"
  fi
fi
cur_agent="$(sed -n 's/^  default: *\([a-z0-9_-]*\).*/\1/p' "$POLICY" | head -1)"
if [ "$cur_agent" = "$AGENT" ]; then ok "routing.default: $AGENT"
elif ask "Route tasks to $AGENT (routing.default in $POLICY, now '$cur_agent')?"; then
  set_yaml "$POLICY" "  default" "$AGENT" && ok "routing.default: $AGENT"
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
bold "4/7  GitHub sign-in"
if ! gh auth status >/dev/null 2>&1; then
  if [ -t 0 ] && ask "You're not signed in to gh. Run 'gh auth login' now?"; then gh auth login
  else HUMAN+=("Sign in to GitHub:  gh auth login   (then re-run make quickstart)"); fi
fi
GH_LOGIN=""
if gh auth status >/dev/null 2>&1; then
  GH_LOGIN="$(gh api user --jq .login 2>/dev/null || true)"
  ok "signed in as ${GH_LOGIN:-?}"
  export GITHUB_TOKEN="${GITHUB_TOKEN:-$(gh auth token)}"   # this process only, never written
fi

# --------------------------------------------------------------------------- 5. identity
bold "5/7  Commit identity (who the PR commits are from)"
cur_name="$(sed -n 's/^  committer_name: "\(.*\)".*/\1/p' "$POLICY")"
cur_email="$(sed -n 's/^  committer_email: "\(.*\)".*/\1/p' "$POLICY")"
if [ -n "$cur_name" ] && [ -n "$cur_email" ]; then
  ok "already set: $cur_name <$cur_email>"
elif [ -n "$GH_LOGIN" ]; then
  gh_id="$(gh api user --jq .id)"
  def_name="$(gh api user --jq '.name // .login')"
  def_email="${gh_id}+${GH_LOGIN}@users.noreply.github.com"
  echo "  Suggested: $def_name <$def_email>  (GitHub's private noreply address)"
  echo "  Tip: a dedicated bot account is cleanest — see docs/ONBOARDING.md §7."
  if ask "Write this identity to $POLICY?"; then
    set_yaml "$POLICY" "  committer_name" "$def_name"
    set_yaml "$POLICY" "  committer_email" "$def_email"
    ok "identity written"
  fi
else
  warn "sign in to gh first, then re-run to set the identity"
fi

KEY_HINT=""
for k in id_ed25519.pub id_ecdsa.pub id_rsa.pub; do
  [ -f "$HOME/.ssh/$k" ] && { KEY_HINT="\$HOME/.ssh/$k"; break; }
done
if [ -n "${TITHE_SIGNING_KEY:-}" ]; then ok "TITHE_SIGNING_KEY is set"
elif [ -n "$KEY_HINT" ]; then
  HUMAN+=("For real PRs (signed commits): add to your shell profile  export TITHE_SIGNING_KEY=$KEY_HINT  and register it once:  gh ssh-key add $KEY_HINT --type signing")
else
  HUMAN+=("For real PRs (signed commits): ssh-keygen -t ed25519, then export TITHE_SIGNING_KEY=\$HOME/.ssh/id_ed25519.pub and gh ssh-key add it --type signing")
fi

# --------------------------------------------------------------------------- 6. prove it
bold "6/7  Offline self-test (fixture repos, no network, no GitHub writes)"
SIM=FAIL; DOC=FAIL
make --no-print-directory simulate && SIM=PASS
bold "     Health check"
.venv/bin/donate doctor && DOC=PASS || warn "doctor reported FAILs above — fix those first (WARNs only block --live)"

# --------------------------------------------------------------------------- 7. summary
bold "7/7  Summary"
echo "  self-test (simulate): $SIM · doctor: $DOC · task agent: $AGENT · GitHub: ${GH_LOGIN:-not signed in}"
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
    export GITHUB_TOKEN=$(gh auth token)     # each new terminal (or add to your shell profile)
    .venv/bin/donate capacity                # what this run can afford
    .venv/bin/donate discover --dry-run      # issues it would look at
    .venv/bin/donate run                     # agents work locally; proposals -> state/review/
    .venv/bin/donate review                  # read each proposal's diff
  To open one as a real PR:  make sandbox-image  (once, Docker)  then
    .venv/bin/donate review --open 1 --live

  Driving this with an AI agent? Tell it: "Follow docs/AGENT_SETUP.md".
  Full walkthrough: docs/ONBOARDING.md · Stop everything: .venv/bin/donate halt
TXT
[ "$SIM" = PASS ]
