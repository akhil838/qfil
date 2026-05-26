"""Firehose XML/program/patch implementation."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
from typing import BinaryIO, Protocol, cast
import xml.etree.ElementTree as ET

from qfil.images import SparseImageReader, is_sparse_image
from qfil.software_fix import ProgramEntry


class FirehoseError(RuntimeError):
    pass


class FirehoseTransport(Protocol):
    def write(self, data: bytes) -> None: ...

    def read(self, size: int = 1024 * 1024, timeout_ms: int | None = None) -> bytes: ...

    def read_until(self, marker: bytes, timeout_s: float = 10.0) -> bytes: ...


@dataclass
class FirehoseConfig:
    memory: str = "ufs"
    sector_size: int = 4096
    max_payload_to_target: int = 1024 * 1024
    max_payload_from_target: int = 8192
    max_xml_size: int = 4096
    zlpawarehost: int = 1
    skip_storage_init: int = 0
    skip_write: int = 0
    verbose: int = 0
    always_validate: int = 0
    max_digest_table_size: int = 2048


class FirehoseClient:
    def __init__(
        self, transport: FirehoseTransport, config: FirehoseConfig | None = None
    ):
        self.transport = transport
        self.config = config or FirehoseConfig()

    def configure(self) -> None:
        xml = (
            '<?xml version="1.0" encoding="UTF-8" ?><data>'
            f'<configure MemoryName="{self.config.memory}" '
            f'Verbose="{self.config.verbose}" '
            f'AlwaysValidate="{self.config.always_validate}" '
            f'MaxDigestTableSizeInBytes="{self.config.max_digest_table_size}" '
            f'MaxPayloadSizeToTargetInBytes="{self.config.max_payload_to_target}" '
            f'ZLPAwareHost="{self.config.zlpawarehost}" '
            f'SkipStorageInit="{self.config.skip_storage_init}" '
            f'SkipWrite="{self.config.skip_write}"/>'
            "</data>"
        )
        response = self.xml(xml)
        attrs = response.attributes
        self.config.memory = attrs.get("MemoryName", self.config.memory).lower()
        self.config.max_payload_to_target = int(
            attrs.get("MaxPayloadSizeToTargetInBytesSupported")
            or attrs.get("MaxPayloadSizeToTargetInBytes")
            or self.config.max_payload_to_target
        )
        self.config.max_payload_from_target = int(
            attrs.get("MaxPayloadSizeFromTargetInBytes")
            or self.config.max_payload_from_target
        )
        self.config.max_xml_size = int(
            attrs.get("MaxXMLSizeInBytes") or self.config.max_xml_size
        )
        if "SECTOR_SIZE_IN_BYTES" in attrs:
            self.config.sector_size = int(attrs["SECTOR_SIZE_IN_BYTES"])

    def set_bootable_storage_drive(self, lun: int) -> None:
        self.xml(
            f'<?xml version="1.0" ?><data><setbootablestoragedrive value="{lun}" /></data>'
        )

    def nop(self) -> "FirehoseResponse":
        return self.xml('<?xml version="1.0" ?><data><nop /></data>')

    def get_storage_info(self, lun: int = 0) -> "FirehoseResponse":
        return self.xml(
            '<?xml version="1.0" ?><data>'
            f'<getstorageinfo physical_partition_number="{lun}" />'
            "</data>",
            timeout_s=30,
        )

    def fix_gpt(self, lun: int = 0) -> "FirehoseResponse":
        return self.xml(
            '<?xml version="1.0" ?><data>'
            f'<fixgpt physical_partition_number="{lun}" />'
            "</data>",
            timeout_s=30,
        )

    def get_sha256_digest(self, entry: ProgramEntry) -> str:
        sectors = entry.sectors
        if sectors is None:
            raise FirehoseError(
                f"Cannot verify {entry.label}: num_partition_sectors is required."
            )
        sector_size = entry.sector_size or self.config.sector_size
        response = self.xml(
            '<?xml version="1.0" ?><data>'
            f'<getsha256digest SECTOR_SIZE_IN_BYTES="{sector_size}" '
            f'num_partition_sectors="{sectors}" '
            f'physical_partition_number="{entry.lun}" '
            f'start_sector="{entry.start_sector}" />'
            "</data>",
            timeout_s=30,
        )
        digest = _response_digest(response)
        if not digest:
            raise FirehoseError(
                f"Target did not return a SHA-256 digest for {entry.label}."
            )
        return digest

    def verify_programming(self, entry: ProgramEntry, image_dir: Path) -> None:
        expected = _sha256_program_payload(entry, image_dir, self.config.sector_size)
        actual = self.get_sha256_digest(entry)
        if actual.lower() != expected.lower():
            raise FirehoseError(
                f"Verify failed for {entry.label}: target={actual} local={expected}"
            )

    def reset(self, mode: str = "reset") -> None:
        self.xml(
            f'<?xml version="1.0" ?><data><power value="{mode}"/></data>', timeout_s=3
        )

    def erase(self, elem: ET.Element) -> None:
        self.xml(_wrap_element(elem))

    def send_element(self, elem: ET.Element) -> None:
        self.xml(_wrap_element(elem))

    def process_xml_file(
        self,
        xml_path: Path,
        image_dir: Path,
        progress=None,
        verify_programming: bool = False,
    ) -> None:
        """Process a Firehose XML file in fh_loader-style tag order.

        fh_loader sorts XML tags so configuration and erase commands run before
        programs, patches run late, and power/reset commands run last.
        """
        elems_by_order: dict[int, list[ET.Element]] = {index: [] for index in range(5)}
        for _, elem in ET.iterparse(xml_path, events=("end",)):
            if elem.tag in {"data", "patches"}:
                continue
            if elem.tag == "program" and not elem.get("filename", ""):
                elem.clear()
                continue
            elems_by_order[_firehose_tag_order(elem.tag)].append(
                ET.fromstring(ET.tostring(elem))
            )
            elem.clear()
        for index in sorted(elems_by_order):
            for elem in elems_by_order[index]:
                if elem.tag == "program" and elem.get("filename", ""):
                    entry = _program_entry_from_element(xml_path, elem)
                    self.program(entry, image_dir, progress=progress)
                    if verify_programming:
                        self.verify_programming(entry, image_dir)
                elif elem.tag == "read":
                    self.read_to_file(elem, image_dir, progress=progress)
                elif elem.tag == "firmwarewrite" and elem.get("filename", ""):
                    self.write_file_element(elem, image_dir, progress=progress)
                elif elem.tag == "patch":
                    self.send_element(elem)
                elif elem.tag == "erase":
                    self.erase(elem)
                else:
                    self.send_element(elem)

    def program(self, entry: ProgramEntry, image_dir: Path, progress=None) -> None:
        image_path = image_dir / entry.filename
        if not image_path.exists():
            raise FirehoseError(f"Missing program image: {image_path}")
        sparse = is_sparse_image(image_path)
        sectors = entry.sectors
        total_bytes = os.path.getsize(image_path)
        if sparse:
            with SparseImageReader(image_path) as sparse_reader:
                total_bytes = sparse_reader.total_size
        if sectors is None:
            sectors = (
                total_bytes + self.config.sector_size - 1
            ) // self.config.sector_size
        else:
            total_bytes = sectors * self.config.sector_size
        start_sector = entry.start_sector
        sector_size = entry.sector_size or self.config.sector_size
        xml = (
            '<?xml version="1.0" ?><data>\n'
            f'<program SECTOR_SIZE_IN_BYTES="{sector_size}" '
            f'num_partition_sectors="{sectors}" '
            f'physical_partition_number="{entry.lun}" '
            f'start_sector="{start_sector}" />\n'
            "</data>"
        )
        self.xml(xml)
        written = 0
        reader = SparseImageReader(image_path) if sparse else image_path.open("rb")
        with reader as handle:
            if entry.file_sector_offset and not sparse:
                cast(BinaryIO, handle).seek(
                    entry.file_sector_offset * self.config.sector_size
                )
            while written < total_bytes:
                size = min(total_bytes - written, self.config.max_payload_to_target)
                chunk = handle.read(size)
                if len(chunk) < size:
                    chunk += b"\x00" * (size - len(chunk))
                if len(chunk) % self.config.sector_size:
                    padded = (
                        (len(chunk) // self.config.sector_size) + 1
                    ) * self.config.sector_size
                    chunk += b"\x00" * (padded - len(chunk))
                self.transport.write(chunk)
                self.transport.write(b"")
                written += size
                if progress:
                    progress(entry, written, total_bytes)
        response = self._read_response(timeout_s=30)
        if not response.ack:
            raise FirehoseError(
                f"Program failed for {entry.label}: {response.raw_text[:500]}"
            )

    def write_file_element(
        self, elem: ET.Element, image_dir: Path, progress=None
    ) -> None:
        filename = elem.get("filename", "")
        if not filename:
            self.send_element(elem)
            return
        image_path = Path(image_dir) / filename
        if not image_path.exists():
            raise FirehoseError(f"Missing Firehose payload: {image_path}")
        total_bytes = image_path.stat().st_size
        self.xml(_wrap_element(elem), timeout_s=30)
        written = 0
        with image_path.open("rb") as handle:
            while written < total_bytes:
                chunk = handle.read(
                    min(total_bytes - written, self.config.max_payload_to_target)
                )
                if not chunk:
                    break
                self.transport.write(chunk)
                written += len(chunk)
                if progress:
                    progress(
                        _entry_from_payload_element(elem, image_path),
                        written,
                        total_bytes,
                    )
        response = self._read_response(timeout_s=30)
        if not response.ack:
            raise FirehoseError(
                f"File transfer failed for {filename}: {response.raw_text[:500]}"
            )

    def read_to_file(self, elem: ET.Element, image_dir: Path, progress=None) -> Path:
        sectors = _int_attr(elem, "num_partition_sectors")
        sector_size = _int_attr(elem, "SECTOR_SIZE_IN_BYTES") or self.config.sector_size
        if sectors is None:
            raise FirehoseError("Firehose read requires num_partition_sectors.")
        total_bytes = sectors * sector_size
        filename = elem.get("filename") or _default_read_filename(elem)
        output = Path(image_dir) / filename
        output.parent.mkdir(parents=True, exist_ok=True)
        self.xml(_wrap_element(elem), timeout_s=30)
        written = 0
        with output.open("wb") as handle:
            while written < total_bytes:
                chunk = self.transport.read(
                    size=min(
                        total_bytes - written,
                        self.config.max_payload_from_target,
                    ),
                    timeout_ms=30000,
                )
                if not chunk:
                    raise FirehoseError(
                        f"Timed out during Firehose read: {written}/{total_bytes} bytes"
                    )
                handle.write(chunk)
                written += len(chunk)
                if progress:
                    progress(
                        _entry_from_payload_element(elem, output),
                        written,
                        total_bytes,
                    )
        response = self._read_response(timeout_s=30)
        if not response.ack:
            raise FirehoseError(
                f"Read failed for {filename}: {response.raw_text[:500]}"
            )
        return output

    def patch_file(self, patch_xml: Path) -> None:
        for _, elem in ET.iterparse(patch_xml, events=("end",)):
            if elem.tag != "patch":
                continue
            self.send_element(elem)
            elem.clear()

    def xml(self, xml: str, timeout_s: float = 10.0) -> "FirehoseResponse":
        data = xml.encode("utf-8")
        if len(data) > self.config.max_xml_size:
            raise FirehoseError(
                f"XML payload exceeds max XML size: {len(data)} > {self.config.max_xml_size}"
            )
        self.transport.write(data)
        response = self._read_response(timeout_s=timeout_s)
        if not response.ack:
            raise FirehoseError(response.raw_text[:800] or "Firehose returned NAK.")
        return response

    def _read_response(self, timeout_s: float) -> "FirehoseResponse":
        raw = self.transport.read_until(b"<response", timeout_s=timeout_s)
        if b"<response" in raw and b"</data>" not in raw:
            raw += self.transport.read_until(b"</data>", timeout_s=2)
        response = FirehoseResponse.from_bytes(raw)
        if not raw:
            raise FirehoseError("Timed out waiting for Firehose response.")
        return response


@dataclass(frozen=True)
class FirehoseResponse:
    ack: bool
    attributes: dict[str, str]
    logs: tuple[str, ...]
    raw_text: str

    @classmethod
    def from_bytes(cls, data: bytes) -> "FirehoseResponse":
        text = data.decode("utf-8", errors="replace")
        attrs: dict[str, str] = {}
        logs: list[str] = []
        try:
            root = ET.fromstring(_extract_xml(text))
            logs.extend(
                log.get("value", "")
                for log in root.findall(".//log")
                if log.get("value")
            )
            response = root.find(".//response")
            if response is not None:
                attrs = dict(response.attrib)
        except ET.ParseError:
            pass
        return cls(
            ack=attrs.get("value", "").upper() == "ACK",
            attributes=attrs,
            logs=tuple(logs),
            raw_text=text,
        )


def _extract_xml(text: str) -> str:
    start = text.find("<?xml")
    if start == -1:
        start = text.find("<data")
    if start == -1:
        return text
    end = text.rfind("</data>")
    if end == -1:
        return text[start:]
    return text[start : end + len("</data>")]


def _wrap_element(elem: ET.Element) -> str:
    payload = ET.tostring(elem, encoding="unicode")
    return f'<?xml version="1.0" ?><data>\n {payload} </data>'


def _firehose_tag_order(tag: str) -> int:
    order = {
        "configure": 0,
        "nop": 0,
        "getstorageinfo": 0,
        "fixgpt": 1,
        "erase": 1,
        "getsha256digest": 3,
        "patch": 3,
        "power": 4,
    }
    return order.get(tag, 2)


def _int_attr(elem: ET.Element, name: str) -> int | None:
    value = elem.get(name)
    if value in (None, ""):
        return None
    try:
        return int(value, 0)
    except ValueError:
        return None


def _default_read_filename(elem: ET.Element) -> str:
    lun = elem.get("physical_partition_number") or "0"
    start = elem.get("start_sector") or "0"
    sectors = elem.get("num_partition_sectors") or "unknown"
    return f"read_lun{lun}_sector{start}_sectors{sectors}.bin"


def _entry_from_payload_element(elem: ET.Element, path: Path) -> ProgramEntry:
    filename = elem.get("filename") or path.name
    return ProgramEntry(
        xml=Path(path),
        filename=filename,
        label=elem.get("label") or Path(filename).stem,
        lun=int(elem.get("physical_partition_number") or "0", 0),
        start_sector=elem.get("start_sector") or "0",
        sectors=_int_attr(elem, "num_partition_sectors"),
        file_sector_offset=int(elem.get("file_sector_offset") or "0", 0),
        sector_size=_int_attr(elem, "SECTOR_SIZE_IN_BYTES"),
    )


def _response_digest(response: FirehoseResponse) -> str | None:
    for key, value in response.attributes.items():
        if key.lower() not in {"digest", "sha256digest", "sha256", "hash"}:
            continue
        digest = _find_sha256(value)
        if digest:
            return digest
    for value in (*response.logs, response.raw_text):
        digest = _find_sha256(value)
        if digest:
            return digest
    return None


def _find_sha256(value: str) -> str | None:
    match = re.search(r"\b[0-9a-fA-F]{64}\b", value)
    return match.group(0) if match else None


def _sha256_program_payload(
    entry: ProgramEntry, image_dir: Path, default_sector_size: int
) -> str:
    image_path = Path(image_dir) / entry.filename
    if not image_path.exists():
        raise FirehoseError(f"Missing program image: {image_path}")
    sector_size = entry.sector_size or default_sector_size
    total_bytes = image_path.stat().st_size
    if is_sparse_image(image_path):
        with SparseImageReader(image_path) as sparse_reader:
            total_bytes = sparse_reader.total_size
    if entry.sectors is not None:
        total_bytes = entry.sectors * sector_size

    digest = hashlib.sha256()
    written = 0
    reader = (
        SparseImageReader(image_path)
        if is_sparse_image(image_path)
        else image_path.open("rb")
    )
    with reader as handle:
        if entry.file_sector_offset and not is_sparse_image(image_path):
            cast(BinaryIO, handle).seek(entry.file_sector_offset * default_sector_size)
        while written < total_bytes:
            size = min(total_bytes - written, 1024 * 1024)
            chunk = handle.read(size)
            if len(chunk) < size:
                chunk += b"\x00" * (size - len(chunk))
            digest.update(chunk)
            written += size
    return digest.hexdigest()


def _program_entry_from_element(xml_path: Path, elem: ET.Element) -> ProgramEntry:
    filename = elem.get("filename", "")
    return ProgramEntry(
        xml=Path(xml_path),
        filename=filename,
        label=elem.get("label") or Path(filename).stem,
        lun=int(elem.get("physical_partition_number") or "0", 0),
        start_sector=elem.get("start_sector") or "0",
        sectors=_int_attr(elem, "num_partition_sectors"),
        file_sector_offset=int(elem.get("file_sector_offset") or "0", 0),
        sector_size=_int_attr(elem, "SECTOR_SIZE_IN_BYTES"),
    )
