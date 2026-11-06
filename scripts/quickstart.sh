#!/usr/bin/env bash
# token-tithe quickstart — one command from a fresh clone to a working, safe (dry-run) setup.
#
#   ./scripts/quickstart.sh          # or: make quickstart
#
# Safe to re-run any time. It never opens a PR, never comments, never stores a secret:
# everything it does is local, or read-only against GitHub. Each install step asks first.
set -euo pipefail
cd "$(dirname "$0")/.."

bold() { printf '\n\033[1m%s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
ask()  {  # ask "question" -> 0 for yes. Non-interactive (no TTY / CI): default no.
  [ -t 0 ] || return 1
  local a; read -r -p "  $1 [y/N] " a; [[ "$a" =~ ^[Yy] ]]
}
have() { command -v "$1" >/dev/null 2>&1; }

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

# --------------------------------------------------------------------------- 1. prerequisites
bold "1/6  Prerequisites"
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
    || { echo "Need Python ≥ 3.11 or uv. Install one and re-run."; exit 1; }
fi
have git || { echo "git is required. Install it and re-run."; exit 1; }
ok "git"
try_install gh gh gh "https://cli.github.com" \
  || { echo "The GitHub CLI (gh) is required. Install it and re-run."; exit 1; }
ok "gh (GitHub CLI)"
if have claude; then ok "Claude Code CLI"
else
  warn "Claude Code CLI not found — needed to do real work (not for simulate)."
  if have npm && ask "Install it now with: npm i -g @anthropic-ai/claude-code ?"; then
    npm i -g @anthropic-ai/claude-code && ok "Claude Code CLI installed"
  else
    warn "later: npm i -g @anthropic-ai/claude-code   (or another agent, see docs/AGENT_INTEGRATION.md)"
  fi
fi
if have docker && docker info >/dev/null 2>&1; then ok "Docker running (needed only for --live)"
else warn "Docker not running — fine for now; live PRs need it (Docker Desktop / docker.io)."; fi

# --------------------------------------------------------------------------- 2. venv
bold "2/6  Installing token-tithe into ./.venv (nothing touches your system Python)"
make --no-print-directory setup
ok "installed: .venv/bin/donate"

# --------------------------------------------------------------------------- 3. GitHub
bold "3/6  GitHub sign-in"
if ! gh auth status >/dev/null 2>&1; then
  if ask "You're not signed in to gh. Run 'gh auth login' now?"; then gh auth login
  else warn "skipped — run 'gh auth login' before discover/run"; fi
fi
GH_LOGIN=""
if gh auth status >/dev/null 2>&1; then
  GH_LOGIN="$(gh api user --jq .login 2>/dev/null || true)"
  ok "signed in as ${GH_LOGIN:-?}"
  export GITHUB_TOKEN="${GITHUB_TOKEN:-$(gh auth token)}"   # this process only, never written
fi

# --------------------------------------------------------------------------- 4. identity
bold "4/6  Commit identity (who the PR commits are from)"
POLICY=config/policy.yaml
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
    NAME="$def_name" EMAIL="$def_email" .venv/bin/python - "$POLICY" <<'PY'
import os, re, sys
p = sys.argv[1]; t = open(p, encoding="utf-8").read()
for key, env in (("committer_name", "NAME"), ("committer_email", "EMAIL")):
    val = os.environ[env].replace('"', "'")
    t = re.sub(rf'^(  {key}: )"[^"]*"', lambda m: f'{m.group(1)}"{val}"', t, count=1, flags=re.M)
open(p, "w", encoding="utf-8").write(t)
PY
    ok "identity written"
  else warn "skipped — edit signing.committer_name/email in $POLICY later"; fi
else
  warn "sign in to gh first, then re-run to set the identity"
fi

KEY_HINT=""
for k in id_ed25519.pub id_ecdsa.pub id_rsa.pub; do
  [ -f "$HOME/.ssh/$k" ] && { KEY_HINT="\$HOME/.ssh/$k"; break; }
done
if [ -n "${TITHE_SIGNING_KEY:-}" ]; then ok "TITHE_SIGNING_KEY is set"
elif [ -n "$KEY_HINT" ]; then
  warn "commits must be signed for --live. Add to your shell profile:"
  echo "      export TITHE_SIGNING_KEY=$KEY_HINT"
  echo "    and add that key on GitHub as a *Signing* key: gh ssh-key add $KEY_HINT --type signing"
else
  warn "no SSH key found. For --live: ssh-keygen -t ed25519, then set TITHE_SIGNING_KEY (ONBOARDING §7)"
fi

# --------------------------------------------------------------------------- 5. prove it
bold "5/6  Offline self-test (fixture repos, no network, no GitHub writes)"
make --no-print-directory simulate
bold "     Health check"
.venv/bin/donate doctor || warn "doctor reported FAILs above — fix those first (WARNs only block --live)"

# --------------------------------------------------------------------------- 6. next
bold "6/6  Done. What next"
cat <<'TXT'
  Everything below is safe: nothing reaches GitHub until you add --live yourself.

    export GITHUB_TOKEN=$(gh auth token)     # each new terminal (or add to your shell profile)
    .venv/bin/donate capacity                # what this run can afford
    .venv/bin/donate discover --dry-run      # issues it would look at
    .venv/bin/donate run                     # agents work locally; proposals -> state/review/
    .venv/bin/donate review                  # read each proposal's diff

  When a proposal looks good and you want to open the PR:
    make sandbox-image                       # once; needs Docker running
    .venv/bin/donate review --open 1 --live

  Using Claude Code? Just open this folder and say "run a donation round" —
  CLAUDE.md tells it how to operate the engine safely.

  Full walkthrough: docs/ONBOARDING.md · Stop everything: .venv/bin/donate halt
TXT
