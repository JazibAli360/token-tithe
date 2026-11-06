# Learning — the Brain

Two memory layers, never mixed:

| | Run state | The Brain |
|---|---|---|
| Where | `state/` (gitignored) | `brain/BRAIN.md` + `brain/knowledge/` (committed) |
| Lifetime | one run, disposable | durable, versioned, portable |
| Answers | where am I? | what do we know? |
| Read | first (`state/HANDOVER.md`) | second |

The Brain is the transferable asset of the project: any fresh clone, machine or agent
session can be handed it and inherit everything learned.

## Format

Sections: **AGENT QUIRKS · WHAT GETS MERGED · REJECTIONS & INCIDENTS · PROMPT PATTERNS ·
ECOSYSTEM PLAYBOOKS · PENDING LESSONS · REVIEW LOG**. One lesson per line:

```
- **L-0007** · 2026-10-08 · confirmed · tags: sandbox, python · prov: commit bbd057b — <text>
- **P-0003** → PROMPT PATTERNS · 2026-10-08 · observed-once · simulate · tags: … · prov: … — <text>
- 2026-10-08 · promoted P-0003 → L-0012
```

- **Confidence**: `observed-once → repeated → confirmed`. Effective confidence rises as
  `learn` reinforces a lesson; it is computed, never written back.
- **Provenance**: a ledger run id, a PR URL, an eval run or a commit — always.
- **Origin** (pending only): `live`, `dry-run`, `simulate`, `eval`, `feedback`, `import`.
  Cheap experiments still teach, but they are labelled.

## The loop

```bash
.venv/bin/donate learn                    # ledger + latest eval → candidates → PENDING
.venv/bin/donate learn --apply            # promote all open pending (human approval)
.venv/bin/donate learn --apply --ids P-0004,P-0007
.venv/bin/donate learn --reject P-0005
```

`learn` is deterministic and spends no tokens: rules over ledger events since the last
`learned through` mark (rejections, abandonments by reason, timeouts, model switches, merges,
closures, incidents) plus real-agent failures in the latest eval report. Candidates are
deduplicated against the Brain: same section, near-identical text **and** the same subject
(tags) — a duplicate reinforces the existing lesson instead of adding a new one.

**Learning must change behavior.** With `brain.auto_inject: true`, the executor injects the
most relevant active lessons (by tag overlap with language, donation type, repo, agent, plus
`general`) into every task brief ("Lessons that apply here") and every second-review prompt.

## R19 — append-only

The engine never edits an approved lesson and never deletes anything:

- promotions, rejections, reinforcements, provenance unions and stale flags are REVIEW LOG lines;
- corrections are new lessons: `donate brain supersede L-0004 "new text" --prov "PR …"`;
  superseded lessons stop being injected but stay in the file;
- `donate brain review` flags lessons not reinforced in `brain.stale_after_days` (marked, not
  removed; stale lessons rank lower for injection).

## The line cap

`BRAIN.md` stays under `brain.max_lines` (500). When it would go over:

- new lessons are written in full to `knowledge/<section>.md`, and BRAIN.md shows a computed
  `> +N more in knowledge/<section>.md` line;
- lessons longer than 280 characters always go to `knowledge/`;
- resolved pending lines and the oldest review-log lines **move** to
  `knowledge/pending-archive.md` and `knowledge/review-log.md`.

Lessons in `knowledge/` are still lessons: they are parsed, deduplicated and injected.

## Portability

```bash
.venv/bin/donate brain export                       # → state/reports/tithe-brain-YYYY-MM-DD.md
.venv/bin/donate brain import tithe-brain-2026-10-08.md
```

The export is **one file**: BRAIN.md plus every knowledge file inlined. Attach it to a fresh
agent chat, commit it elsewhere, or import it on another machine. Import never overwrites
silently:

- if everything you have is already in the export, it fast-forwards (a fresh clone ends up
  byte-identical to the exporter);
- otherwise matching lessons get a provenance union, and anything new or conflicting lands
  in PENDING for you to approve;
- a differing knowledge file is saved beside yours as `<name>.incoming.md`.

### Optional: one shared Brain for several clones

Off by default — the in-repo Brain always works alone. To share one Brain across several
token-tithe clones, create a tiny private git repo and set `brain.sync_remote` to its URL:

```bash
.venv/bin/donate brain sync      # pull + import, then export + push (state/brain-sync/)
```

## What belongs in the Brain

**Yes:** evidence-backed patterns that should change future behavior — agent quirks, what
gets merged, why PRs were rejected, brief fragments that worked, ecosystem pitfalls.

**No:** per-run state (that's the handover), secrets, personal data about maintainers,
opinions without provenance, or anything a maintainer asked us to forget.
