"""Minimal Sahara programmer upload implementation."""

from __future__ import annotations

import struct
from pathlib import Path

from qfil.usb import QualcommUsbTransport


class SaharaError(RuntimeError):
    pass


HELLO_REQ = 0x01
HELLO_RSP = 0x02
READ_DATA = 0x03
END_TRANSFER = 0x04
DONE_REQ = 0x05
DONE_RSP = 0x06
RESET_REQ = 0x07
READ_DATA_64 = 0x12
IMAGE_TX_PENDING = 0
STATUS_SUCCESS = 0


class SaharaClient:
    def __init__(self, transport: QualcommUsbTransport):
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

    def _send_hello(self) -> None:
        self.transport.write(
            struct.pack(
                "<IIIIIIIIIIII",
                HELLO_RSP,
                0x30,
                2,
                1,
                0,
                IMAGE_TX_PENDING,
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
