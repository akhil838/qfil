"""Progress helpers for low-level Sahara/Firehose device writes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tqdm.auto import tqdm


class EntryProgress:
    """Render cumulative per-entry write callbacks as stable tqdm progress bars."""

    def __init__(self, prefix: str = "flash"):
        self.prefix = prefix
        self._bar: tqdm | None = None
        self._key: tuple[str, str, str] | None = None
        self._written = 0

    def __call__(self, entry: Any, written: int, total: int) -> None:
        key = (
            str(getattr(entry, "xml", "")),
            str(getattr(entry, "filename", "")),
            str(getattr(entry, "label", "")),
        )
        if key != self._key:
            self.close()
            label = str(getattr(entry, "label", "") or "partition")
            filename = Path(str(getattr(entry, "filename", "") or label)).name
            self._bar = tqdm(
                total=total or None,
                unit="B",
                unit_scale=True,
                unit_divisor=1024,
                desc=f"{self.prefix} {label} ({filename})",
            )
            self._key = key
            self._written = 0

        if self._bar is None:
            return
        delta = max(0, written - self._written)
        self._bar.update(delta)
        self._written = written
        if total and written >= total:
            self.close()

    def close(self) -> None:
        if self._bar is not None:
            self._bar.close()
        self._bar = None
        self._key = None
        self._written = 0
