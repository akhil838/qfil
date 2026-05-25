"""Progress parsing helpers for QFIL/Firehose output."""

from __future__ import annotations

from dataclasses import dataclass
import re

from .software_fix import ProgramEntry


@dataclass(frozen=True)
class FirehoseProgress:
    percent: float
    sector: int
    total_sectors: int
    speed: str
    label: str | None = None
    filename: str | None = None
    lun: int | None = None


_PROGRESS_RE = re.compile(
    r"Progress:\s+\|[^|]*\|\s*"
    r"(?P<percent>[0-9]+(?:\.[0-9]+)?)%\s+Write\s+"
    r"\(Sector\s+(?P<sector>0x[0-9A-Fa-f]+|\d+)\s+of\s+"
    r"(?P<total>0x[0-9A-Fa-f]+|\d+)[^)]*\)\s*"
    r"(?P<speed>[0-9.]+\s+\S+/s)?"
)


def parse_firehose_progress(
    line: str, entries: list[ProgramEntry] | None = None
) -> FirehoseProgress | None:
    match = _PROGRESS_RE.search(line)
    if not match:
        return None
    total = int(match.group("total"), 0)
    entry = _match_entry(total, entries or [])
    return FirehoseProgress(
        percent=float(match.group("percent")),
        sector=int(match.group("sector"), 0),
        total_sectors=total,
        speed=(match.group("speed") or "").strip(),
        label=entry.label if entry else None,
        filename=entry.filename if entry else None,
        lun=entry.lun if entry else None,
    )


def _match_entry(
    total_sectors: int, entries: list[ProgramEntry]
) -> ProgramEntry | None:
    matches = [entry for entry in entries if entry.sectors == total_sectors]
    if len(matches) == 1:
        return matches[0]
    return None
