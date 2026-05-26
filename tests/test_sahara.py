from __future__ import annotations

import struct
import tempfile
from pathlib import Path
import unittest

from qfil.protocol.sahara import (
    CMD_EXEC,
    CMD_EXEC_DATA,
    CMD_EXEC_RSP,
    CMD_READY,
    COMMAND_MODE,
    HELLO_REQ,
    HELLO_RSP,
    MEMORY_DEBUG_64,
    MEMORY_DEBUG_MODE,
    MEMORY_READ_64,
    RESET_REQ,
    RESET_RSP,
    SaharaClient,
)


class FakeTransport:
    def __init__(self, reads: list[bytes]):
        self.reads = list(reads)
        self.writes: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.writes.append(bytes(data))

    def read(self, size: int = 1024 * 1024, timeout_ms: int | None = None) -> bytes:
        del timeout_ms
        if not self.reads:
            return b""
        chunk = self.reads.pop(0)
        if len(chunk) <= size:
            return chunk
        self.reads.insert(0, chunk[size:])
        return chunk[:size]


class SaharaTests(unittest.TestCase):
    def test_execute_command_enters_command_mode_and_reads_payload(self) -> None:
        transport = FakeTransport(
            [
                struct.pack("<II", HELLO_REQ, 48) + b"\x00" * 40,
                struct.pack("<II", CMD_READY, 8),
                struct.pack("<IIII", CMD_EXEC_RSP, 16, 1, 4),
                b"SN01",
            ]
        )
        client = SaharaClient(transport)

        payload = client.execute_command(1)

        self.assertEqual(payload, b"SN01")
        hello_cmd, _, _, _, _, hello_mode = struct.unpack_from(
            "<IIIIII", transport.writes[0], 0
        )
        self.assertEqual(hello_cmd, HELLO_RSP)
        self.assertEqual(hello_mode, COMMAND_MODE)
        self.assertEqual(transport.writes[1], struct.pack("<III", CMD_EXEC, 12, 1))
        self.assertEqual(transport.writes[2], struct.pack("<III", CMD_EXEC_DATA, 12, 1))

    def test_reset_state_machine_waits_for_reset_response(self) -> None:
        transport = FakeTransport([struct.pack("<II", RESET_RSP, 8)])
        client = SaharaClient(transport)

        client.reset_state_machine()

        self.assertEqual(transport.writes, [struct.pack("<II", RESET_REQ, 8)])

    def test_dump_memory_reads_table_and_regions(self) -> None:
        table = (
            struct.pack("<QQQ", 1, 0x2000, 4)
            + b"DDR\x00".ljust(20, b"\x00")
            + b"ddr.bin\x00".ljust(20, b"\x00")
        )
        transport = FakeTransport(
            [
                struct.pack("<II", HELLO_REQ, 48) + b"\x00" * 40,
                struct.pack("<IIQQ", MEMORY_DEBUG_64, 24, 0x1000, len(table)),
                table,
                b"DATA",
            ]
        )
        client = SaharaClient(transport)

        with tempfile.TemporaryDirectory() as tmp:
            paths = client.dump_memory(Path(tmp))
            self.assertEqual((Path(tmp) / "memory_table.bin").read_bytes(), table)
            self.assertEqual((Path(tmp) / "ddr.bin").read_bytes(), b"DATA")

        self.assertEqual([path.name for path in paths], ["memory_table.bin", "ddr.bin"])
        hello_cmd, _, _, _, _, hello_mode = struct.unpack_from(
            "<IIIIII", transport.writes[0], 0
        )
        self.assertEqual(hello_cmd, HELLO_RSP)
        self.assertEqual(hello_mode, MEMORY_DEBUG_MODE)
        self.assertEqual(
            transport.writes[1], struct.pack("<IIQQ", MEMORY_READ_64, 24, 0x1000, 64)
        )
        self.assertEqual(
            transport.writes[2], struct.pack("<IIQQ", MEMORY_READ_64, 24, 0x2000, 4)
        )


if __name__ == "__main__":
    unittest.main()
