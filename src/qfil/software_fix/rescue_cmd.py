"""Parse Lenovo Software Fix Rescue.cmd into a QFIL execution plan."""

from __future__ import annotations

from dataclasses import dataclass
import re
import xml.etree.ElementTree as ET
from pathlib import Path


@dataclass(frozen=True)
class SaharaProgrammer:
    image_id: int
    loader: Path


@dataclass(frozen=True)
class FirehoseOptions:
    rawprograms: tuple[Path, ...]
    patches: tuple[Path, ...]
    search_path: Path
    memory: str | None
    set_active_partition: int | None
    reset: bool
    zlpawarehost: bool
    noprompt: bool
    showpercentagecomplete: bool


@dataclass(frozen=True)
class QfilPlan:
    startup_file: Path
    image_dir: Path
    programmer: SaharaProgrammer | None
    firehose: FirehoseOptions


@dataclass(frozen=True)
class ProgramEntry:
    xml: Path
    filename: str
    label: str
    lun: int
    start_sector: str
    sectors: int | None
    file_sector_offset: int
    sector_size: int | None


@dataclass(frozen=True)
class PatchEntry:
    xml: Path
    filename: str
    lun: int
    start_sector: str
    byte_offset: int | None
    size_in_bytes: int | None
    value: str
    what: str


def _split_startup_lines(text: str) -> list[str]:
    lines: list[str] = []
    pending = ""
    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw_line.strip()
        if not line or line.lower().startswith("rem "):
            continue
        if line.endswith("^"):
            pending += line[:-1].strip() + " "
            continue
        line = pending + line
        pending = ""
        if line:
            lines.append(line)
    if pending.strip():
        lines.append(pending.strip())
    return lines


def _normalize_command_path(
    value: str, startup_dir: Path, image_dir: Path | None = None
) -> Path:
    value = value.strip().strip('"').strip("'")
    value = re.sub(r"%~dp0", str(startup_dir) + "/", value, flags=re.IGNORECASE)
    value = value.replace("\\", "/")
    path = Path(value)
    if not path.is_absolute():
        base = image_dir if image_dir else startup_dir
        path = base / path.name if "/" not in value else startup_dir / path
    return path.resolve()


def _option_values(line: str, name: str) -> list[str]:
    pattern = rf"--{re.escape(name)}(?:=|\s+)(\"[^\"]+\"|'[^']+'|[^\s]+)"
    return [
        match.strip("\"'") for match in re.findall(pattern, line, flags=re.IGNORECASE)
    ]


def _split_option_list(values: list[str]) -> list[str]:
    items: list[str] = []
    for value in values:
        for item in value.split(","):
            item = item.strip().strip("\"'")
            if item:
                items.append(item)
    return items


def _has_option(line: str, name: str) -> bool:
    return bool(
        re.search(rf"(^|\s)--{re.escape(name)}(\s|$)", line, flags=re.IGNORECASE)
    )


def _find_line(lines: list[str], executable: str) -> str | None:
    pattern = re.compile(
        rf"(^|\s|[\\/]){re.escape(executable)}(\.exe)?(\s|$)", re.IGNORECASE
    )
    for line in lines:
        if pattern.search(line):
            return line
    return None


def _parse_sahara(
    line: str | None, startup_dir: Path, image_dir: Path
) -> SaharaProgrammer | None:
    if not line:
        return None
    match = re.search(r"(?:^|\s)-s\s+([0-9A-Fa-fx]+):(\"[^\"]+\"|'[^']+'|[^\s]+)", line)
    if not match:
        return None
    return SaharaProgrammer(
        image_id=int(match.group(1), 0),
        loader=_normalize_command_path(match.group(2), startup_dir, image_dir),
    )


def _parse_firehose(
    line: str | None, startup_dir: Path, fallback_image_dir: Path
) -> FirehoseOptions:
    if not line:
        raise RuntimeError("Rescue.cmd does not contain an fh_loader.exe command.")
    search_values = _option_values(line, "search_path")
    search_path = (
        _normalize_command_path(search_values[-1], startup_dir, fallback_image_dir)
        if search_values
        else fallback_image_dir.resolve()
    )
    rawprograms: list[Path] = []
    patches: list[Path] = []
    for value in _split_option_list(_option_values(line, "sendxml")):
        path = _normalize_command_path(value, startup_dir, search_path)
        if path.name.lower().startswith("rawprogram"):
            rawprograms.append(path)
        elif path.name.lower().startswith("patch"):
            patches.append(path)
    if not rawprograms:
        raise RuntimeError(
            "Rescue.cmd fh_loader command did not list any rawprogram XMLs."
        )
    memory_values = _option_values(line, "memoryname")
    active_values = _option_values(line, "setactivepartition")
    return FirehoseOptions(
        rawprograms=tuple(rawprograms),
        patches=tuple(patches),
        search_path=search_path,
        memory=memory_values[-1].lower() if memory_values else None,
        set_active_partition=int(active_values[-1], 0) if active_values else None,
        reset=_has_option(line, "reset"),
        zlpawarehost=_has_option(line, "zlpawarehost"),
        noprompt=_has_option(line, "noprompt"),
        showpercentagecomplete=_has_option(line, "showpercentagecomplete"),
    )


def parse_rescue_cmd(startup_file: Path, image_dir: Path | None = None) -> QfilPlan:
    startup_file = Path(startup_file).resolve()
    if not startup_file.exists():
        raise RuntimeError(f"Startup file does not exist: {startup_file}")
    startup_dir = startup_file.parent
    fallback_image_dir = (
        Path(image_dir).resolve() if image_dir else (startup_dir / "image").resolve()
    )
    lines = _split_startup_lines(
        startup_file.read_text(encoding="utf-8", errors="ignore")
    )
    programmer = _parse_sahara(
        _find_line(lines, "Qsaharaserver"), startup_dir, fallback_image_dir
    )
    firehose = _parse_firehose(
        _find_line(lines, "fh_loader"), startup_dir, fallback_image_dir
    )
    missing = [
        path for path in [*firehose.rawprograms, *firehose.patches] if not path.exists()
    ]
    if programmer and not programmer.loader.exists():
        missing.insert(0, programmer.loader)
    if missing:
        raise RuntimeError(
            "Rescue.cmd references missing files: "
            + ", ".join(str(path) for path in missing)
        )
    return QfilPlan(
        startup_file=startup_file,
        image_dir=firehose.search_path,
        programmer=programmer,
        firehose=firehose,
    )


def _int_attr(elem: ET.Element, name: str) -> int | None:
    value = elem.get(name)
    if value in (None, ""):
        return None
    try:
        return int(value, 0)
    except ValueError:
        return None


def parse_program_entries(
    rawprograms: tuple[Path, ...] | list[Path],
) -> list[ProgramEntry]:
    entries: list[ProgramEntry] = []
    for xml_path in rawprograms:
        xml_path = Path(xml_path)
        for _, elem in ET.iterparse(xml_path, events=("end",)):
            if elem.tag == "program" and elem.get("filename", ""):
                filename = elem.get("filename", "")
                entries.append(
                    ProgramEntry(
                        xml=xml_path,
                        filename=filename,
                        label=elem.get("label") or Path(filename).stem,
                        lun=int(elem.get("physical_partition_number") or "0", 0),
                        start_sector=elem.get("start_sector") or "0",
                        sectors=_int_attr(elem, "num_partition_sectors"),
                        file_sector_offset=int(
                            elem.get("file_sector_offset") or "0", 0
                        ),
                        sector_size=_int_attr(elem, "SECTOR_SIZE_IN_BYTES"),
                    )
                )
            elem.clear()
    return entries


def parse_patch_entries(patches: tuple[Path, ...] | list[Path]) -> list[PatchEntry]:
    entries: list[PatchEntry] = []
    for xml_path in patches:
        xml_path = Path(xml_path)
        for _, elem in ET.iterparse(xml_path, events=("end",)):
            if elem.tag == "patch":
                entries.append(
                    PatchEntry(
                        xml=xml_path,
                        filename=elem.get("filename") or "",
                        lun=int(elem.get("physical_partition_number") or "0", 0),
                        start_sector=elem.get("start_sector") or "0",
                        byte_offset=_int_attr(elem, "byte_offset"),
                        size_in_bytes=_int_attr(elem, "size_in_bytes"),
                        value=elem.get("value") or "",
                        what=elem.get("what") or "",
                    )
                )
            elem.clear()
    return entries


def summarize_plan(plan: QfilPlan) -> list[str]:
    program_entries = parse_program_entries(plan.firehose.rawprograms)
    patch_entries = parse_patch_entries(plan.firehose.patches)
    lines = [
        f"Startup: {plan.startup_file.name}",
        f"Search path: {plan.image_dir}",
    ]
    if plan.programmer:
        lines.append(
            f"QSahara: -s {plan.programmer.image_id}:{plan.programmer.loader.name}"
        )
    lines.append(f"fh_loader memory: {plan.firehose.memory or '(auto)'}")
    if plan.firehose.set_active_partition is not None:
        lines.append(
            f"fh_loader setactivepartition: {plan.firehose.set_active_partition}"
        )
    lines.append(f"fh_loader reset: {'yes' if plan.firehose.reset else 'no'}")
    lines.append(
        f"Rawprogram XMLs: {', '.join(path.name for path in plan.firehose.rawprograms)}"
    )
    lines.append(
        f"Patch XMLs: {', '.join(path.name for path in plan.firehose.patches) if plan.firehose.patches else '(none)'}"
    )
    lines.append(f"Program entries: {len(program_entries)}")
    lines.append(f"Patch entries: {len(patch_entries)}")
    return lines
