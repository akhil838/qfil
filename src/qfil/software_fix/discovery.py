"""QFIL package discovery helpers shared by LRSA workflows."""

from __future__ import annotations

import re
from pathlib import Path


def discover_qfil_files(image_dir: Path) -> tuple[list[Path], list[Path]]:
    image_dir = Path(image_dir)
    rawprograms = sorted(
        path
        for path in image_dir.rglob("rawprogram*.xml")
        if path.is_file() and not path.name.endswith(".x")
    )
    patches = sorted(
        path
        for path in image_dir.rglob("patch*.xml")
        if path.is_file() and not path.name.endswith(".x")
    )
    if not rawprograms:
        encrypted = sorted(image_dir.rglob("rawprogram*.x"))
        if encrypted:
            raise RuntimeError(
                "Only encrypted QFIL rawprogram .x files were found. "
                "Decrypt/generate XMLs before using the native qfil module."
            )
        raise RuntimeError(f"No rawprogram*.xml files found under {image_dir}")
    return rawprograms, patches


def has_qfil_files(path: Path) -> bool:
    path = Path(path)
    return any(path.glob("rawprogram*.xml")) and any(path.glob("patch*.xml"))


def resolve_qfil_image_dir(
    base_dir: Path, startup_file: Path | str | None = None
) -> Path:
    """Resolve the directory that should be used as fh_loader search_path."""
    base_dir = Path(base_dir)
    candidates: list[Path] = []
    if startup_file:
        startup = Path(startup_file)
        candidates.extend([startup.parent / "image", startup.parent])
    candidates.extend([base_dir, base_dir / "image"])

    for candidate in candidates:
        if candidate.exists() and has_qfil_files(candidate):
            return candidate.resolve()

    nested = sorted(
        path for path in base_dir.rglob("*") if path.is_dir() and has_qfil_files(path)
    )
    if nested:
        return nested[0].resolve()
    return base_dir.resolve()


def select_qfil_set(
    rawprograms: list[Path],
    patches: list[Path],
    prefer_full: bool = True,
) -> tuple[list[Path], list[Path]]:
    official = [
        path
        for path in rawprograms
        if re.fullmatch(r"rawprogram(?:_unsparse)?\d+\.xml", path.name, re.IGNORECASE)
    ]
    if official:
        official_patches = [
            path
            for path in patches
            if re.fullmatch(r"patch\d+\.xml", path.name, re.IGNORECASE)
        ]
        return official, official_patches or patches

    if prefer_full:
        full = [path for path in rawprograms if "full" in path.name]
        if full:
            patch_names = {path.name.replace("rawprogram", "patch") for path in full}
            full_patches = [path for path in patches if path.name in patch_names]
            return full, full_patches or patches
    return rawprograms, patches


def discover_firehose_loader(image_dir: Path) -> Path | None:
    image_dir = Path(image_dir)
    preferred_names = (
        "xbl_s_devprg_ns.melf",
        "prog_firehose_ddr.elf",
        "prog_firehose_ddr.mbn",
        "prog_ufs_firehose_*.elf",
        "prog_ufs_firehose_*.mbn",
        "prog_emmc_firehose_*.mbn",
    )
    for pattern in preferred_names:
        matches = sorted(path for path in image_dir.rglob(pattern) if path.is_file())
        if matches:
            return matches[0]
    return None
