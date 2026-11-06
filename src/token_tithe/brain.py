"""The Brain (Addendum E): durable, committed, portable knowledge — brain/BRAIN.md + knowledge/.

R19: the engine never edits approved lessons and never deletes anything. Every operation
APPENDS: promotions, rejections, reinforcements, provenance unions and stale flags are lines in
the REVIEW LOG section; corrections are new lessons that supersede old ones. Effective state
(active vs superseded, last reinforced, resolved pending) is computed from those appends.

Line formats:
    - **L-0007** · 2026-10-08 · confirmed · tags: python, issue_fix · prov: <ref> — <text>
    - **P-0003** → PROMPT PATTERNS · 2026-10-08 · observed-once · live · tags: … · prov: … — <text>
    - 2026-10-08 · promoted P-0003 → L-0007
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

from token_tithe import paths

SECTIONS = ["AGENT QUIRKS", "WHAT GETS MERGED", "REJECTIONS & INCIDENTS", "PROMPT PATTERNS",
            "ECOSYSTEM PLAYBOOKS", "PENDING LESSONS", "REVIEW LOG"]  # fmt: skip
LESSON_SECTIONS = SECTIONS[:5]
CONFIDENCE = ("observed-once", "repeated", "confirmed")
MAX_INLINE_CHARS = 280
EMPTY = "_(none yet)_"
MORE_PREFIX = "> +"
LOG_ARCHIVE = "review-log.md"
PENDING_ARCHIVE = "pending-archive.md"
DUP_RATIO = 0.82

LESSON_RE = re.compile(
    r"^- \*\*(?P<id>L-\d{4})\*\* · (?P<date>\d{4}-\d{2}-\d{2}) · (?P<conf>[\w-]+) · "
    r"tags: (?P<tags>[^·]*?) · prov: (?P<prov>.*?) — (?P<text>.*)$"
)
PENDING_RE = re.compile(
    r"^- \*\*(?P<id>P-\d{4})\*\* → (?P<section>[A-Z &]+?) · (?P<date>\d{4}-\d{2}-\d{2}) · "
    r"(?P<conf>[\w-]+) · (?P<origin>[\w-]+) · tags: (?P<tags>[^·]*?) · prov: (?P<prov>.*?) — (?P<text>.*)$"
)
LOG_RE = re.compile(r"^- (?P<date>\d{4}-\d{2}-\d{2}) · (?P<what>.*)$")
SUPERSEDES = re.compile(r"^Supersedes (L-\d{4}):")
POINTER = re.compile(r"→ (knowledge/[\w.-]+\.md)#(l-\d{4})$")

HEADER = """# BRAIN — token-tithe's durable knowledge

> Read this second, after `state/HANDOVER.md`. Handover = where am I (volatile); Brain = what
> do we know (durable, committed, portable). Append-only (R19): approved lessons are never
> edited or deleted — corrections are new lessons that supersede old ones. Managed with
> `donate learn` (candidates → PENDING, `--apply` promotes) and `donate brain`.
> Confidence: observed-once → repeated → confirmed. Provenance: ledger run id, PR URL or commit.
"""


@dataclass
class Lesson:
    id: str
    section: str
    date: str
    confidence: str
    tags: list[str]
    prov: str
    text: str
    line: str

    @property
    def supersedes(self) -> str | None:
        m = SUPERSEDES.match(self.text)
        return m.group(1) if m else None


@dataclass
class Pending:
    id: str
    section: str
    date: str
    confidence: str
    origin: str
    tags: list[str]
    prov: str
    text: str
    line: str


@dataclass
class Brain:
    path: Path = field(default_factory=lambda: paths.BRAIN)
    max_lines: int = 500
    blocks: dict[str, list[str]] = field(default_factory=dict)
    header: str = HEADER

    # ----------------------------------------------------------------- io

    @classmethod
    def load(cls, path: Path | None = None, max_lines: int = 500) -> Brain:
        b = cls(path or paths.BRAIN, max_lines)
        b.blocks = {s: [] for s in SECTIONS}
        if not b.path.is_file():
            return b
        current = None
        head: list[str] = []
        for raw in b.path.read_text(encoding="utf-8").splitlines():
            if raw.startswith("## ") and raw[3:].strip() in SECTIONS:
                current = raw[3:].strip()
                continue
            if current is None:
                head.append(raw)
            elif raw.strip() and raw.strip() != EMPTY and not raw.startswith(MORE_PREFIX):
                b.blocks[current].append(raw)
        if any(line.strip() for line in head):
            b.header = "\n".join(head).rstrip() + "\n"
        return b

    def render(self) -> str:
        out = [self.header.rstrip(), ""]
        for s in SECTIONS:
            out += [f"## {s}", ""]
            out += self.blocks.get(s) or [EMPTY]
            if s in LESSON_SECTIONS and (n := len(self._kfile_lines(f"{_slug(s)}.md"))):
                out.append(f"{MORE_PREFIX}{n} more in knowledge/{_slug(s)}.md")
            out.append("")
        return "\n".join(out)

    def save(self) -> None:
        self._enforce_cap()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(self.render(), encoding="utf-8")

    def _enforce_cap(self) -> None:
        """Keep BRAIN.md ≤ max_lines by MOVING (never deleting) resolved pending lessons and
        older review-log lines into knowledge/ archives. Approved lessons never move (R19)."""
        if len(self.render().splitlines()) <= self.max_lines:
            return
        done = self.resolved_pending()
        resolved = [
            ln
            for ln in self.blocks["PENDING LESSONS"]
            if (m := PENDING_RE.match(ln)) and m["id"] in done
        ]
        if resolved:
            self._archive(PENDING_ARCHIVE, "Pending lessons — resolved archive", resolved)
            self.blocks["PENDING LESSONS"] = [
                ln for ln in self.blocks["PENDING LESSONS"] if ln not in resolved
            ]
        log = self.blocks["REVIEW LOG"]
        excess = len(self.render().splitlines()) - self.max_lines
        if excess > 0 and log:
            n = min(len(log), excess)  # move exactly what the cap requires, oldest first
            self._archive(LOG_ARCHIVE, "Review log — archive", log[:n])
            self.blocks["REVIEW LOG"] = log[n:]

    def _archive(self, name: str, title: str, lines: list[str]) -> None:
        f = self.knowledge_dir / name
        f.parent.mkdir(parents=True, exist_ok=True)
        if not f.is_file():
            f.write_text(f"# {title}\n\nMoved out of brain/BRAIN.md to respect its line cap. "
                         "Append-only (R19).\n\n", encoding="utf-8")  # fmt: skip
        with f.open("a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")

    def _kfile_lines(self, name: str) -> list[str]:
        f = self.knowledge_dir / name
        return [ln for ln in f.read_text(encoding="utf-8").splitlines() if ln.startswith("- ")] \
            if f.is_file() else []  # fmt: skip

    @property
    def knowledge_dir(self) -> Path:
        return self.path.parent / "knowledge"

    # ----------------------------------------------------------------- parsing

    def lessons(self) -> list[Lesson]:
        out = []
        for s in LESSON_SECTIONS:
            for line in [*self.blocks.get(s, []), *self._kfile_lines(f"{_slug(s)}.md")]:
                if m := LESSON_RE.match(line):
                    out.append(Lesson(m["id"], s, m["date"], m["conf"], _tags(m["tags"]),
                                      m["prov"], m["text"], line))  # fmt: skip
        return out

    def pending(self) -> list[Pending]:
        out = []
        for line in [*self._kfile_lines(PENDING_ARCHIVE), *self.blocks.get("PENDING LESSONS", [])]:
            if m := PENDING_RE.match(line):
                out.append(Pending(m["id"], m["section"].strip(), m["date"], m["conf"],
                                   m["origin"], _tags(m["tags"]), m["prov"], m["text"], line))  # fmt: skip
        return out

    def log(self) -> list[tuple[str, str]]:
        lines = [*self._kfile_lines(LOG_ARCHIVE), *self.blocks.get("REVIEW LOG", [])]
        return [(m["date"], m["what"]) for line in lines
                if (m := LOG_RE.match(line))]  # fmt: skip

    def resolved_pending(self) -> set[str]:
        return {m.group(1) for _, what in self.log()
                if (m := re.match(r"(?:promoted|rejected) (P-\d{4})", what))}  # fmt: skip

    def open_pending(self) -> list[Pending]:
        done = self.resolved_pending()
        return [p for p in self.pending() if p.id not in done]

    def superseded(self) -> set[str]:
        return {s for le in self.lessons() if (s := le.supersedes)}

    def active(self) -> list[Lesson]:
        gone = self.superseded()
        return [le for le in self.lessons() if le.id not in gone]

    def last_reinforced(self, lesson: Lesson) -> str:
        dates = [lesson.date] + [d for d, what in self.log() if f"reinforced {lesson.id}" in what]
        return max(dates)

    def reinforcements(self, lesson_id: str) -> int:
        return sum(1 for _, what in self.log() if what.startswith(f"reinforced {lesson_id}"))

    def effective_confidence(self, lesson: Lesson) -> str:
        """Confidence grows with reinforcement — computed, never written back (R19)."""
        base = CONFIDENCE.index(lesson.confidence) if lesson.confidence in CONFIDENCE else 0
        return CONFIDENCE[min(2, base + min(2, self.reinforcements(lesson.id)))]

    # ----------------------------------------------------------------- appends

    def _today(self) -> str:
        return datetime.now(UTC).date().isoformat()

    def _next(self, prefix: str) -> str:
        ids = [le.id for le in self.lessons()] if prefix == "L" else [p.id for p in self.pending()]
        n = max([int(i.split("-")[1]) for i in ids] or [0]) + 1
        return f"{prefix}-{n:04d}"

    def append_log(self, what: str) -> None:
        self.blocks.setdefault("REVIEW LOG", []).append(f"- {self._today()} · {what}")

    def find_duplicate(self, section: str, text: str, tags: list[str] | None = None):
        """Same section, near-identical text AND the same subject (tags). Templated lessons
        about different repos/languages differ only in their tags — they are not duplicates."""
        norm = _norm(text)
        want = {t.lower() for t in tags} if tags is not None else None
        for item in [*self.active(), *self.open_pending()]:
            if item.section != section:
                continue
            have = {t.lower() for t in item.tags}
            if want is not None and not (want <= have or have <= want):
                continue
            if difflib.SequenceMatcher(None, _norm(item.text), norm).ratio() >= DUP_RATIO:
                return item
        return None

    def add_pending(self, section: str, text: str, tags: list[str], prov: str,
                    origin: str = "live", confidence: str = "observed-once") -> str | None:  # fmt: skip
        """Queue a candidate lesson. Duplicates reinforce instead (logged, never edited)."""
        if section not in LESSON_SECTIONS:
            raise ValueError(f"unknown section {section!r}")
        text = " ".join(text.split())
        if dup := self.find_duplicate(section, text, tags):
            if isinstance(dup, Lesson):
                self.append_log(f"reinforced {dup.id} · prov: {prov}")
            return None
        pid = self._next("P")
        self.blocks["PENDING LESSONS"].append(
            f"- **{pid}** → {section} · {self._today()} · {confidence} · {origin} · "
            f"tags: {', '.join(tags)} · prov: {prov} — {text}"
        )
        return pid

    def promote(self, pid: str) -> str:
        p = next((x for x in self.open_pending() if x.id == pid), None)
        if p is None:
            raise KeyError(f"no open pending lesson {pid}")
        lid = self._lesson(p.section, p.text, p.tags, p.prov, p.confidence)
        self.append_log(f"promoted {pid} → {lid}")
        return lid

    def reject(self, pid: str, reason: str = "") -> None:
        if pid not in {p.id for p in self.open_pending()}:
            raise KeyError(f"no open pending lesson {pid}")
        self.append_log(f"rejected {pid}" + (f" — {reason}" if reason else ""))

    def supersede(self, lesson_id: str, text: str, prov: str) -> str:
        old = next((le for le in self.lessons() if le.id == lesson_id), None)
        if old is None:
            raise KeyError(f"no lesson {lesson_id}")
        lid = self._lesson(
            old.section, f"Supersedes {lesson_id}: {text}", old.tags, prov, "confirmed"
        )
        self.append_log(f"{lid} supersedes {lesson_id}")
        return lid

    def _lesson(self, section: str, text: str, tags: list[str], prov: str, conf: str) -> str:
        lid = self._next("L")
        line = f"- **{lid}** · {self._today()} · {conf} · tags: {', '.join(tags)} · prov: {prov} — {text}"
        projected = len(self.render().splitlines()) + 1
        if len(text) > MAX_INLINE_CHARS or projected > self.max_lines:
            name = f"{_slug(section)}.md"
            self._archive(name, f"{section.title()} — overflow lessons", [line])
        else:
            self.blocks[section].append(line)
        return lid

    def review_stale(self, days: int, today: date | None = None) -> list[str]:
        today = today or datetime.now(UTC).date()
        flagged = []
        already = {w.split()[1] for d, w in self.log() if w.startswith("stale: ")}
        for le in self.active():
            last = date.fromisoformat(self.last_reinforced(le))
            if (today - last).days > days and le.id not in already:
                self.append_log(f"stale: {le.id} (last reinforced {last.isoformat()})")
                flagged.append(le.id)
        return flagged

    # ----------------------------------------------------------------- injection

    def relevant(self, tags: set[str], limit: int = 6) -> list[Lesson]:
        tags = {t.lower() for t in tags if t}
        stale = {w.split()[1] for _, w in self.log() if w.startswith("stale: ")}
        scored = []
        for le in self.active():
            lt = {t.lower() for t in le.tags}
            overlap = len(lt & tags)
            if overlap or "general" in lt:
                rank = (
                    overlap * 10
                    + CONFIDENCE.index(self.effective_confidence(le))
                    - (5 if le.id in stale else 0)
                )
                scored.append((rank, le.id, le))
        return [le for _, _, le in sorted(scored, key=lambda x: (-x[0], x[1]))[:limit]]

    def relevant_text(self, tags: set[str], limit: int = 6) -> str:
        found = self.relevant(tags, limit)
        if not found:
            return "None recorded yet."
        return "\n".join(f"- ({le.id}, {self.effective_confidence(le)}) {POINTER.sub('', le.text).strip()}"
                         for le in found)  # fmt: skip

    # ----------------------------------------------------------------- portability

    def export(self, dest_dir: Path) -> Path:
        stamp = datetime.now(UTC).strftime("%Y-%m-%d")
        out = [f"<!-- tithe-brain export v1 · {stamp} -->", self.render().rstrip(), ""]
        for f in sorted(self.knowledge_dir.glob("*.md")) if self.knowledge_dir.is_dir() else []:
            out += [f"<!-- tithe-knowledge: {f.name} -->", f.read_text(encoding="utf-8").rstrip(),
                    "<!-- /tithe-knowledge -->", ""]  # fmt: skip
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"tithe-brain-{stamp}.md"
        dest.write_text("\n".join(out), encoding="utf-8")
        return dest

    def import_(self, file: Path) -> dict:
        """Merge an export. Never silently overwrites: conflicts land in PENDING."""
        text = file.read_text(encoding="utf-8")
        if not text.startswith("<!-- tithe-brain export v1"):
            raise ValueError("not a tithe-brain export")
        knowledge = dict(re.findall(r"<!-- tithe-knowledge: ([\w.-]+) -->\n(.*?)\n<!-- /tithe-knowledge -->",
                                    text, re.S))  # fmt: skip
        body = re.sub(
            r"<!-- tithe-knowledge: .*?<!-- /tithe-knowledge -->\n?", "", text, flags=re.S
        )
        body = body.split("\n", 1)[1]
        tmp = self.path.parent / ".incoming-brain.md"
        tmp.write_text(body, encoding="utf-8")
        incoming = Brain.load(tmp, self.max_lines)
        tmp.unlink()

        mine = {le.id: le for le in self.lessons()}
        theirs = {le.id: le for le in incoming.lessons()}
        my_lines = set(self.blocks_flat())
        stats = {"fast_forward": False, "union": 0, "pending": 0, "knowledge_conflicts": 0}
        # Fast-forward: everything we have is already in theirs, verbatim → adopt theirs whole.
        if my_lines <= set(incoming.blocks_flat()):
            self.blocks = incoming.blocks
            self.header = incoming.header
            stats["fast_forward"] = True
        else:
            for lid, le in theirs.items():
                if lid in mine and _norm(mine[lid].text) == _norm(le.text):
                    if le.prov != mine[lid].prov:
                        self.append_log(f"provenance union {lid} · prov: {le.prov}")
                        stats["union"] += 1
                    continue
                if self.find_duplicate(le.section, le.text, le.tags):
                    continue
                self.add_pending(le.section, le.text, le.tags, f"import {file.name} ({le.prov})",
                                 origin="import", confidence=le.confidence)  # fmt: skip
                stats["pending"] += 1
            self.append_log(
                f"imported {file.name}: {stats['pending']} to PENDING, {stats['union']} unions"
            )
        for name, content in knowledge.items():
            f = self.knowledge_dir / name
            f.parent.mkdir(parents=True, exist_ok=True)
            if not f.is_file():
                f.write_text(content + "\n", encoding="utf-8")
            elif f.read_text(encoding="utf-8").rstrip() != content.rstrip():
                (self.knowledge_dir / f"{f.stem}.incoming.md").write_text(
                    content + "\n", encoding="utf-8"
                )
                stats["knowledge_conflicts"] += 1
        return stats

    def blocks_flat(self) -> list[str]:
        return [line for s in SECTIONS for line in self.blocks.get(s, [])]


def _tags(raw: str) -> list[str]:
    return [t.strip() for t in raw.split(",") if t.strip()]


def _norm(text: str) -> str:
    text = POINTER.sub("", SUPERSEDES.sub("", text))
    return re.sub(r"[^a-z0-9 ]+", " ", text.lower()).strip()


def _slug(section: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", section.lower()).strip("-")
