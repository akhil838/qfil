"""Minimal Firehose XML/program/patch implementation."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import BinaryIO, cast
import xml.etree.ElementTree as ET

from qfil.images import SparseImageReader, is_sparse_image
from qfil.software_fix import ProgramEntry
from qfil.usb import QualcommUsbTransport


class FirehoseError(RuntimeError):
    pass


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


class FirehoseClient:
    def __init__(
        self, transport: QualcommUsbTransport, config: FirehoseConfig | None = None
    ):
        self.transport = transport
        self.config = config or FirehoseConfig()

    def configure(self) -> None:
        xml = (
            '<?xml version="1.0" encoding="UTF-8" ?><data>'
            f'<configure MemoryName="{self.config.memory}" Verbose="0" AlwaysValidate="0" '
            f'MaxDigestTableSizeInBytes="2048" '
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

    def reset(self, mode: str = "reset") -> None:
        self.xml(
            f'<?xml version="1.0" ?><data><power value="{mode}"/></data>', timeout_s=3
        )

    def erase(self, elem: ET.Element) -> None:
        self.xml(_wrap_element(elem))

    def send_element(self, elem: ET.Element) -> None:
        self.xml(_wrap_element(elem))

    def process_xml_file(self, xml_path: Path, image_dir: Path, progress=None) -> None:
        """Process a Firehose XML file in fh_loader-style tag order.

        fh_loader sorts XML tags so configuration and erase commands run before
        programs, patches run late, and power/reset commands run last.  This
        keeps rawprogram files with embedded erase/configure tags from being
        silently reduced to only file-backed program entries.
        """
        elems_by_order: dict[int, list[ET.Element]] = {index: [] for index in range(5)}
        for _, elem in ET.iterparse(xml_path, events=("end",)):
            if elem.tag == "data":
                continue
            elems_by_order[_firehose_tag_order(elem.tag)].append(
                ET.fromstring(ET.tostring(elem))
            )
            elem.clear()
        for index in sorted(elems_by_order):
            for elem in elems_by_order[index]:
                if elem.tag == "program" and elem.get("filename", ""):
                    self.program(
                        _program_entry_from_element(xml_path, elem),
                        image_dir,
                        progress=progress,
                    )
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
        return FirehoseResponse.from_bytes(raw)


@dataclass(frozen=True)
class FirehoseResponse:
    ack: bool
    attributes: dict[str, str]
    raw_text: str

    @classmethod
    def from_bytes(cls, data: bytes) -> "FirehoseResponse":
        text = data.decode("utf-8", errors="replace")
        attrs: dict[str, str] = {}
        try:
            root = ET.fromstring(_extract_xml(text))
            response = root.find(".//response")
            if response is not None:
                attrs = dict(response.attrib)
        except ET.ParseError:
            pass
        return cls(
            ack=attrs.get("value", "").upper() == "ACK", attributes=attrs, raw_text=text
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
        "erase": 1,
        "patch": 3,
        "power": 4,
    }
    return order.get(tag, 2)


def _program_entry_from_element(xml_path: Path, elem: ET.Element) -> ProgramEntry:
    def int_attr(name: str) -> int | None:
        value = elem.get(name)
        if value in (None, ""):
            return None
        try:
            return int(value, 0)
        except ValueError:
            return None

    filename = elem.get("filename", "")
    return ProgramEntry(
        xml=Path(xml_path),
        filename=filename,
        label=elem.get("label") or Path(filename).stem,
        lun=int(elem.get("physical_partition_number") or "0", 0),
        start_sector=elem.get("start_sector") or "0",
        sectors=int_attr("num_partition_sectors"),
        file_sector_offset=int(elem.get("file_sector_offset") or "0", 0),
        sector_size=int_attr("SECTOR_SIZE_IN_BYTES"),
    )
