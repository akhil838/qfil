"""Minimal Sahara programmer upload implementation."""

from __future__ import annotations

import struct
from pathlib import Path
from dataclasses import dataclass
from typing import Protocol


class SaharaError(RuntimeError):
    pass


class SaharaTransport(Protocol):
    def write(self, data: bytes) -> None: ...

    def read(self, size: int = 1024 * 1024, timeout_ms: int | None = None) -> bytes: ...


HELLO_REQ = 0x01
HELLO_RSP = 0x02
READ_DATA = 0x03
END_TRANSFER = 0x04
DONE_REQ = 0x05
DONE_RSP = 0x06
RESET_REQ = 0x07
RESET_RSP = 0x08
MEMORY_DEBUG = 0x09
MEMORY_READ = 0x0A
CMD_READY = 0x0B
CMD_SWITCH_MODE = 0x0C
CMD_EXEC = 0x0D
CMD_EXEC_RSP = 0x0E
CMD_EXEC_DATA = 0x0F
MEMORY_DEBUG_64 = 0x10
MEMORY_READ_64 = 0x11
READ_DATA_64 = 0x12
IMAGE_TX_PENDING = 0
MEMORY_DEBUG_MODE = 2
COMMAND_MODE = 3
STATUS_SUCCESS = 0


@dataclass(frozen=True)
class SaharaMemoryRegion:
    save_pref: int
    address: int
    length: int
    description: str
    filename: str


class SaharaClient:
    def __init__(self, transport: SaharaTransport):
        self.transport = transport

    def upload_programmer(
        self, loader: Path, expected_image_id: int | None = None
    ) -> str:
        data = Path(loader).read_bytes()
        while True:
            packet = self._read_packet()
            cmd, length = struct.unpack_from("<II", packet, 0)
            if cmd == HELLO_REQ:
                self._send_hello()
            elif cmd == READ_DATA:
                image_id, offset, size = struct.unpack_from("<III", packet, 8)
                self._send_loader_chunk(data, image_id, offset, size, expected_image_id)
            elif cmd == READ_DATA_64:
                image_id, offset, size = struct.unpack_from("<QQQ", packet, 8)
                self._send_loader_chunk(data, image_id, offset, size, expected_image_id)
            elif cmd == END_TRANSFER:
                image_id, status = struct.unpack_from("<II", packet, 8)
                if status != STATUS_SUCCESS:
                    raise SaharaError(
                        f"Sahara rejected image {image_id}: status={status}"
                    )
                self.transport.write(struct.pack("<II", DONE_REQ, 8))
            elif cmd == DONE_RSP:
                return "firehose"
            else:
                raise SaharaError(
                    f"Unexpected Sahara packet cmd=0x{cmd:x} len={length}"
                )

    def reset(self) -> None:
        self.transport.write(struct.pack("<II", RESET_REQ, 8))

    def reset_state_machine(self) -> None:
        self.reset()
        packet = self._read_packet()
        cmd, length = struct.unpack_from("<II", packet, 0)
        if cmd != RESET_RSP:
            raise SaharaError(
                f"Expected Sahara reset response, got cmd=0x{cmd:x} len={length}."
            )

    def execute_command(self, command_id: int) -> bytes:
        packet = self._read_packet()
        cmd, length = struct.unpack_from("<II", packet, 0)
        if cmd == HELLO_REQ:
            self._send_hello(mode=COMMAND_MODE)
            packet = self._read_packet()
            cmd, length = struct.unpack_from("<II", packet, 0)
        if cmd != CMD_READY:
            raise SaharaError(
                f"Expected Sahara command-ready packet, got cmd=0x{cmd:x} len={length}."
            )
        self.transport.write(struct.pack("<III", CMD_EXEC, 12, command_id))
        packet = self._read_packet()
        cmd, length = struct.unpack_from("<II", packet, 0)
        if cmd != CMD_EXEC_RSP:
            raise SaharaError(
                f"Expected Sahara command response, got cmd=0x{cmd:x} len={length}."
            )
        response_command, response_size = struct.unpack_from("<II", packet, 8)
        if response_command != command_id:
            raise SaharaError(
                f"Sahara command response id {response_command} did not match {command_id}."
            )
        if response_size == 0:
            return b""
        self.transport.write(struct.pack("<III", CMD_EXEC_DATA, 12, command_id))
        return self._read_exact(response_size)

    def dump_memory(
        self, output_dir: Path, max_region_bytes: int | None = None
    ) -> list[Path]:
        output_dir.mkdir(parents=True, exist_ok=True)
        packet = self._read_packet()
        cmd, length = struct.unpack_from("<II", packet, 0)
        if cmd == HELLO_REQ:
            self._send_hello(mode=MEMORY_DEBUG_MODE)
            packet = self._read_packet()
            cmd, length = struct.unpack_from("<II", packet, 0)

        if cmd == MEMORY_DEBUG:
            table_address, table_length = struct.unpack_from("<II", packet, 8)
            is_64 = False
        elif cmd == MEMORY_DEBUG_64:
            table_address, table_length = struct.unpack_from("<QQ", packet, 8)
            is_64 = True
        else:
            raise SaharaError(
                f"Expected Sahara memory debug packet, got cmd=0x{cmd:x} len={length}."
            )

        table = self.read_memory(table_address, table_length, is_64=is_64)
        table_path = output_dir / "memory_table.bin"
        table_path.write_bytes(table)
        written = [table_path]
        for index, region in enumerate(_parse_memory_table(table, is_64=is_64)):
            if region.length <= 0:
                continue
            size = region.length
            if max_region_bytes is not None:
                size = min(size, max_region_bytes)
            payload = self.read_memory(region.address, size, is_64=is_64)
            path = output_dir / _memory_region_filename(index, region)
            path.write_bytes(payload)
            written.append(path)
        return written

    def read_memory(self, address: int, size: int, is_64: bool = True) -> bytes:
        if is_64:
            self.transport.write(
                struct.pack("<IIQQ", MEMORY_READ_64, 24, address, size)
            )
        else:
            self.transport.write(struct.pack("<IIII", MEMORY_READ, 16, address, size))
        return self._read_exact(size)

    def _read_packet(self) -> bytes:
        packet = self.transport.read(size=4096, timeout_ms=5000)
        if len(packet) < 8:
            raise SaharaError("Timed out waiting for Sahara packet.")
        cmd, length = struct.unpack_from("<II", packet, 0)
        while len(packet) < length:
            chunk = self.transport.read(size=length - len(packet), timeout_ms=5000)
            if not chunk:
                raise SaharaError(
                    f"Short Sahara packet cmd=0x{cmd:x}: {len(packet)}/{length}"
                )
            packet += chunk
        return packet[:length]

    def _read_exact(self, size: int) -> bytes:
        data = bytearray()
        while len(data) < size:
            chunk = self.transport.read(size=size - len(data), timeout_ms=5000)
            if not chunk:
                raise SaharaError(f"Short Sahara data read: {len(data)}/{size}")
            data += chunk
        return bytes(data)

    def _send_hello(self, mode: int = IMAGE_TX_PENDING) -> None:
        self.transport.write(
            struct.pack(
                "<IIIIIIIIIIII",
                HELLO_RSP,
                0x30,
                2,
                1,
                0,
                mode,
                0,
                0,
                0,
                0,
                0,
                0,
            )
        )

    def _send_loader_chunk(
        self,
        data: bytes,
        image_id: int,
        offset: int,
        size: int,
        expected_image_id: int | None,
    ) -> None:
        if expected_image_id is not None and image_id != expected_image_id:
            raise SaharaError(
                f"Device requested image id {image_id}, expected {expected_image_id}."
            )
        end = offset + size
        chunk = data[offset:end]
        if len(chunk) < size:
            chunk += b"\xff" * (size - len(chunk))
        self.transport.write(chunk)


def _parse_memory_table(data: bytes, is_64: bool) -> list[SaharaMemoryRegion]:
    entry_size = 64 if is_64 else 52
    if not data or len(data) % entry_size:
        return []
    regions: list[SaharaMemoryRegion] = []
    for offset in range(0, len(data), entry_size):
        entry = data[offset : offset + entry_size]
        if is_64:
            save_pref, address, length = struct.unpack_from("<QQQ", entry, 0)
            description = _decode_c_string(entry[24:44])
            filename = _decode_c_string(entry[44:64])
        else:
            save_pref, address, length = struct.unpack_from("<III", entry, 0)
            description = _decode_c_string(entry[12:32])
            filename = _decode_c_string(entry[32:52])
        regions.append(
            SaharaMemoryRegion(
                save_pref=save_pref,
                address=address,
                length=length,
                description=description,
                filename=filename,
            )
        )
    return regions


def _decode_c_string(data: bytes) -> str:
    return data.split(b"\x00", 1)[0].decode("ascii", errors="ignore").strip()


def _memory_region_filename(index: int, region: SaharaMemoryRegion) -> str:
    if region.filename:
        return Path(region.filename).name
    description = region.description.lower().replace(" ", "_") or "region"
    safe = "".join(
        char if char.isalnum() or char in "._-" else "_" for char in description
    )
    return f"{index:03d}_{safe}_{region.address:016x}.bin"
