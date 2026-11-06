# token-tithe — run from the repo root; every target uses the in-repo .venv only (S3).
# Paths are relative on purpose: portable (S2/S7) and safe for checkouts whose path has spaces.
SHELL := /bin/bash
VENV  := .venv
PY    := $(VENV)/bin/python
DONATE := $(VENV)/bin/donate
IMAGE := token-tithe:local

# First python >= 3.11 on PATH; otherwise `uv` provisions one for the venv.
HOST_PY := $(shell for p in python3.13 python3.12 python3.11 python3; do \
	command -v $$p >/dev/null 2>&1 && $$p -c 'import sys; sys.exit(sys.version_info < (3,11))' 2>/dev/null && { echo $$p; break; }; done)

.PHONY: help quickstart setup doctor lint test eval sandbox-image discover donate report adopt halt resume stop \
        capacity handover simulate container container-run clean

help:  ## list targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-14s %s\n", $$1, $$2}'

quickstart:  ## first time? start here: prereqs, install, sign-in, identity, self-test
	./scripts/quickstart.sh

$(PY):
ifneq ($(HOST_PY),)
	$(HOST_PY) -m venv $(VENV)
	$(PY) -m pip install --quiet --upgrade pip
else
	@command -v uv >/dev/null || { echo "Need python >= 3.11 or uv on PATH"; exit 1; }
	uv venv --quiet --python 3.12 --seed $(VENV)
endif

setup: $(PY)  ## create .venv inside the repo and install everything into it
	$(PY) -m pip install --quiet -e '.[dev]'
	@echo "ready: $(DONATE)"

doctor: setup  ## verify primitives + self-containment
	$(DONATE) doctor

lint: setup  ## ruff
	$(VENV)/bin/ruff check src tests eval
	$(VENV)/bin/ruff format --check src tests eval

test: setup  ## pytest
	$(VENV)/bin/pytest

eval: setup  ## controls vs fixtures; real agents: make eval AGENTS=reference,null,claude (spends tokens)
	$(PY) eval/run_eval.py $(if $(AGENTS),--agents $(AGENTS))

sandbox-image:  ## build the R16 sandbox image used by live runs
	docker build -f container/sandbox.Dockerfile -t token-tithe-sandbox:latest .

simulate: setup  ## full pipeline against local fixtures, no network
	$(DONATE) simulate

discover: setup  ## find candidates (dry-run)
	$(DONATE) discover --dry-run

donate: setup  ## one run (review mode / dry-run unless policy + --live say otherwise)
	$(DONATE) run

report: setup  ## monthly ledger report
	$(DONATE) report

adopt: setup  ## adopt a repo: make adopt REPO=owner/name
	$(DONATE) adopt $(REPO)

capacity: setup  ## live capacity table
	$(DONATE) capacity

halt: setup  ## kill switch on
	$(DONATE) halt

resume: setup  ## kill switch off / resume from handover
	$(DONATE) resume

stop: setup  ## immediate cooperative stop of a running run
	$(DONATE) stop

handover: setup  ## print current handover
	$(DONATE) handover

container:  ## build the runtime image
	docker build -f container/Dockerfile -t $(IMAGE) .

container-run: container  ## run a command in the image: make container-run CMD="doctor"
	docker run --rm -it -e GITHUB_TOKEN -v "$(CURDIR)/state:/app/state" $(IMAGE) $(or $(CMD),doctor)

clean:  ## remove venv and caches (state/ is kept — delete it yourself to reset)
	rm -rf $(VENV) .pytest_cache .ruff_cache build dist src/*.egg-info
