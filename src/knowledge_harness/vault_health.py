from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from urllib.parse import unquote


WIKILINK_RE = re.compile(r"(?P<embed>!)?\[\[(?P<body>[^\]\n]+)\]\]")


@dataclass(frozen=True)
class Note:
    path: Path
    rel_path: str
    title: str
    content: str
    modified_at: datetime

    @property
    def body_without_frontmatter(self) -> str:
        return strip_yaml_frontmatter(self.content)

    @property
    def is_empty(self) -> bool:
        return self.body_without_frontmatter.strip() == ""

    @property
    def word_count(self) -> int:
        return len(re.findall(r"\b\w+\b", self.body_without_frontmatter))


@dataclass(frozen=True)
class WikiLink:
    source_path: str
    line: int
    raw: str
    target: str


@dataclass(frozen=True)
class DuplicateTitle:
    title: str
    paths: list[str]


@dataclass(frozen=True)
class StaleNote:
    path: str
    title: str
    days_since_modified: int


@dataclass(frozen=True)
class PotentialNote:
    path: str
    title: str
    score: int
    inbound_links: int
    outbound_links: int
    word_count: int
    days_since_modified: int


@dataclass(frozen=True)
class VaultHealthReport:
    vault_path: Path
    generated_at: datetime
    stale_days: int
    note_count: int
    orphan_notes: list[str]
    broken_wikilinks: list[WikiLink]
    empty_notes: list[str]
    duplicate_titles: list[DuplicateTitle]
    stale_notes: list[StaleNote]
    high_potential_notes: list[PotentialNote]

    def to_dict(self) -> dict[str, object]:
        return {
            "vault_path": str(self.vault_path),
            "generated_at": self.generated_at.isoformat(),
            "stale_days": self.stale_days,
            "note_count": self.note_count,
            "orphan_notes": self.orphan_notes,
            "broken_wikilinks": [
                {
                    "source_path": link.source_path,
                    "line": link.line,
                    "raw": link.raw,
                    "target": link.target,
                }
                for link in self.broken_wikilinks
            ],
            "empty_notes": self.empty_notes,
            "duplicate_titles": [
                {"title": duplicate.title, "paths": duplicate.paths}
                for duplicate in self.duplicate_titles
            ],
            "stale_notes": [
                {
                    "path": note.path,
                    "title": note.title,
                    "days_since_modified": note.days_since_modified,
                }
                for note in self.stale_notes
            ],
            "high_potential_notes": [
                {
                    "path": note.path,
                    "title": note.title,
                    "score": note.score,
                    "inbound_links": note.inbound_links,
                    "outbound_links": note.outbound_links,
                    "word_count": note.word_count,
                    "days_since_modified": note.days_since_modified,
                }
                for note in self.high_potential_notes
            ],
        }


def scan_vault_health(
    vault_path: Path,
    *,
    stale_days: int = 180,
    high_potential_limit: int = 10,
    now: datetime | None = None,
) -> VaultHealthReport:
    if stale_days < 1:
        raise ValueError("stale_days must be at least 1")
    if high_potential_limit < 1:
        raise ValueError("high_potential_limit must be at least 1")

    vault_path = vault_path.expanduser().resolve()
    if not vault_path.is_dir():
        raise FileNotFoundError(f"Vault path is not a directory: {vault_path}")

    generated_at = as_utc(now or datetime.now(timezone.utc))
    notes = read_notes(vault_path)
    path_index, title_index = build_note_indexes(notes)
    outgoing_counts = {note.rel_path: 0 for note in notes}
    incoming_counts = {note.rel_path: 0 for note in notes}
    broken_wikilinks: list[WikiLink] = []

    for note in notes:
        for link in extract_wikilinks(note):
            if not is_markdown_note_target(link.target):
                continue
            outgoing_counts[note.rel_path] += 1
            resolved_paths = resolve_target(link.target, path_index, title_index)
            if not resolved_paths:
                broken_wikilinks.append(link)
                continue
            for rel_path in resolved_paths:
                incoming_counts[rel_path] = incoming_counts.get(rel_path, 0) + 1

    empty_notes = sorted(note.rel_path for note in notes if note.is_empty)
    duplicate_titles = find_duplicate_titles(notes)
    stale_notes = find_stale_notes(notes, generated_at, stale_days)
    orphan_notes = sorted(
        note.rel_path
        for note in notes
        if incoming_counts.get(note.rel_path, 0) == 0
        and outgoing_counts.get(note.rel_path, 0) == 0
    )
    high_potential_notes = find_high_potential_notes(
        notes=notes,
        incoming_counts=incoming_counts,
        outgoing_counts=outgoing_counts,
        now=generated_at,
        stale_days=stale_days,
        limit=high_potential_limit,
    )

    return VaultHealthReport(
        vault_path=vault_path,
        generated_at=generated_at,
        stale_days=stale_days,
        note_count=len(notes),
        orphan_notes=orphan_notes,
        broken_wikilinks=sorted(
            broken_wikilinks, key=lambda link: (link.source_path, link.line, link.target)
        ),
        empty_notes=empty_notes,
        duplicate_titles=duplicate_titles,
        stale_notes=stale_notes,
        high_potential_notes=high_potential_notes,
    )


def read_notes(vault_path: Path) -> list[Note]:
    notes: list[Note] = []
    for path in sorted(vault_path.rglob("*.md")):
        rel_path = path.relative_to(vault_path)
        if is_hidden_path(rel_path):
            continue
        stat = path.stat()
        notes.append(
            Note(
                path=path,
                rel_path=rel_path.as_posix(),
                title=path.stem,
                content=path.read_text(encoding="utf-8", errors="replace"),
                modified_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
            )
        )
    return notes


def is_hidden_path(rel_path: Path) -> bool:
    return any(part.startswith(".") for part in rel_path.parts)


def strip_yaml_frontmatter(content: str) -> str:
    lines = content.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return content

    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return "".join(lines[index + 1 :])
    return content


def extract_wikilinks(note: Note) -> list[WikiLink]:
    links: list[WikiLink] = []
    for match in WIKILINK_RE.finditer(note.content):
        body = match.group("body").strip()
        target = normalize_link_target(body)
        if not target:
            continue
        links.append(
            WikiLink(
                source_path=note.rel_path,
                line=note.content.count("\n", 0, match.start()) + 1,
                raw=match.group(0),
                target=target,
            )
        )
    return links


def normalize_link_target(body: str) -> str:
    without_alias = body.split("|", 1)[0].strip()
    without_heading = without_alias.split("#", 1)[0].strip()
    target = unquote(without_heading).replace("\\", "/").strip().lstrip("/")
    while target.startswith("./"):
        target = target[2:]
    return target


def is_markdown_note_target(target: str) -> bool:
    suffix = PurePosixPath(target).suffix.casefold()
    return suffix in {"", ".md"}


def build_note_indexes(
    notes: list[Note],
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    path_index: dict[str, list[str]] = {}
    title_index: dict[str, list[str]] = {}
    for note in notes:
        for key in path_keys(note.rel_path):
            path_index.setdefault(key, []).append(note.rel_path)
        title_index.setdefault(note.title.casefold(), []).append(note.rel_path)
    return path_index, title_index


def path_keys(rel_path: str) -> set[str]:
    keys = {rel_path.casefold()}
    if rel_path.casefold().endswith(".md"):
        keys.add(rel_path[:-3].casefold())
    return keys


def resolve_target(
    target: str,
    path_index: dict[str, list[str]],
    title_index: dict[str, list[str]],
) -> list[str]:
    target_key = target.casefold()
    if target_key.endswith(".md"):
        target_key_without_suffix = target_key[:-3]
    else:
        target_key_without_suffix = target_key

    if "/" in target_key_without_suffix:
        return sorted(
            set(
                path_index.get(target_key, [])
                + path_index.get(target_key_without_suffix, [])
            )
        )

    title = PurePosixPath(target_key_without_suffix).name
    return sorted(
        set(
            title_index.get(title, [])
            + path_index.get(target_key, [])
            + path_index.get(target_key_without_suffix, [])
        )
    )


def find_duplicate_titles(notes: list[Note]) -> list[DuplicateTitle]:
    grouped: dict[str, list[Note]] = {}
    for note in notes:
        grouped.setdefault(note.title.casefold(), []).append(note)

    duplicates: list[DuplicateTitle] = []
    for matching_notes in grouped.values():
        if len(matching_notes) < 2:
            continue
        sorted_notes = sorted(matching_notes, key=lambda note: note.rel_path)
        duplicates.append(
            DuplicateTitle(
                title=sorted_notes[0].title,
                paths=[note.rel_path for note in sorted_notes],
            )
        )
    return sorted(duplicates, key=lambda duplicate: duplicate.title.casefold())


def find_stale_notes(
    notes: list[Note], now: datetime, stale_days: int
) -> list[StaleNote]:
    stale_notes: list[StaleNote] = []
    for note in notes:
        days_since_modified = max(0, (now - note.modified_at).days)
        if days_since_modified >= stale_days:
            stale_notes.append(
                StaleNote(
                    path=note.rel_path,
                    title=note.title,
                    days_since_modified=days_since_modified,
                )
            )
    return sorted(
        stale_notes, key=lambda note: (-note.days_since_modified, note.path.casefold())
    )


def find_high_potential_notes(
    *,
    notes: list[Note],
    incoming_counts: dict[str, int],
    outgoing_counts: dict[str, int],
    now: datetime,
    stale_days: int,
    limit: int,
) -> list[PotentialNote]:
    candidates: list[PotentialNote] = []
    for note in notes:
        if note.is_empty:
            continue
        inbound_links = incoming_counts.get(note.rel_path, 0)
        outbound_links = outgoing_counts.get(note.rel_path, 0)
        word_count = note.word_count
        days_since_modified = max(0, (now - note.modified_at).days)
        if outbound_links < 3 and inbound_links < 3 and word_count < 100:
            continue

        score = (
            outbound_links * 3
            + inbound_links * 2
            + min(word_count // 50, 10)
            + (3 if days_since_modified >= stale_days else 0)
        )
        candidates.append(
            PotentialNote(
                path=note.rel_path,
                title=note.title,
                score=score,
                inbound_links=inbound_links,
                outbound_links=outbound_links,
                word_count=word_count,
                days_since_modified=days_since_modified,
            )
        )

    return sorted(
        candidates,
        key=lambda note: (-note.score, -note.outbound_links, note.path.casefold()),
    )[:limit]


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
