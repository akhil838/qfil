from __future__ import annotations

import tempfile
import hashlib
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET

from qfil.protocol.firehose import (
    FirehoseClient,
    FirehoseConfig,
    FirehoseError,
    FirehoseResponse,
)


ACK = b'<?xml version="1.0" ?><data><response value="ACK" /></data>'


class FakeTransport:
    def __init__(
        self,
        responses: list[bytes] | None = None,
        read_chunks: list[bytes] | None = None,
    ):
        self.responses = list(responses or [])
        self.read_chunks = list(read_chunks or [])
        self.writes: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.writes.append(bytes(data))

    def read_until(self, marker: bytes, timeout_s: float = 10.0) -> bytes:
        del marker, timeout_s
        if self.responses:
            return self.responses.pop(0)
        return ACK

    def read(self, size: int = 1024 * 1024, timeout_ms: int | None = None) -> bytes:
        del timeout_ms
        if not self.read_chunks:
            return b""
        chunk = self.read_chunks.pop(0)
        if len(chunk) <= size:
            return chunk
        self.read_chunks.insert(0, chunk[size:])
        return chunk[:size]


class FirehoseTests(unittest.TestCase):
    def test_response_parses_ack_attributes_and_logs(self) -> None:
        response = FirehoseResponse.from_bytes(
            b'<?xml version="1.0" ?><data>'
            b'<log value="storage initialized" />'
            b'<response value="ACK" MemoryName="UFS" '
            b'MaxPayloadSizeToTargetInBytesSupported="4096" />'
            b"</data>"
        )

        self.assertTrue(response.ack)
        self.assertEqual(response.attributes["MemoryName"], "UFS")
        self.assertEqual(response.logs, ("storage initialized",))

    def test_configure_sends_fh_loader_compatible_options(self) -> None:
        transport = FakeTransport(
            responses=[
                b'<?xml version="1.0" ?><data>'
                b'<response value="ACK" MemoryName="UFS" '
                b'MaxPayloadSizeToTargetInBytesSupported="4096" '
                b'MaxPayloadSizeFromTargetInBytes="16384" '
                b'MaxXMLSizeInBytes="8192" SECTOR_SIZE_IN_BYTES="4096" />'
                b"</data>"
            ]
        )
        client = FirehoseClient(
            transport,
            FirehoseConfig(
                memory="ufs",
                max_payload_to_target=1024,
                zlpawarehost=0,
                skip_storage_init=1,
                skip_write=1,
                verbose=1,
                always_validate=1,
                max_digest_table_size=4096,
            ),
        )

        client.configure()

        xml = transport.writes[0].decode()
        self.assertIn('Verbose="1"', xml)
        self.assertIn('AlwaysValidate="1"', xml)
        self.assertIn('MaxDigestTableSizeInBytes="4096"', xml)
        self.assertIn('SkipStorageInit="1"', xml)
        self.assertIn('SkipWrite="1"', xml)
        self.assertEqual(client.config.memory, "ufs")
        self.assertEqual(client.config.max_payload_to_target, 4096)
        self.assertEqual(client.config.max_payload_from_target, 16384)
        self.assertEqual(client.config.max_xml_size, 8192)

    def test_process_xml_file_uses_fh_loader_tag_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "payload.bin").write_bytes(b"ABCDEFGH")
            xml_path = root / "rawprogram.xml"
            xml_path.write_text(
                """<?xml version="1.0" ?>
<data>
  <power value="reset" />
  <patch filename="DISK" physical_partition_number="0" />
  <program filename="payload.bin" SECTOR_SIZE_IN_BYTES="4"
    num_partition_sectors="2" physical_partition_number="0"
    start_sector="0" />
  <erase physical_partition_number="0" start_sector="0"
    num_partition_sectors="1" />
  <configure MemoryName="ufs" />
</data>
""",
                encoding="utf-8",
            )
            transport = FakeTransport()
            client = FirehoseClient(
                transport,
                FirehoseConfig(
                    sector_size=4,
                    max_payload_to_target=4,
                    max_xml_size=2048,
                ),
            )

            client.process_xml_file(xml_path, root)

        seen = [_classify_write(write) for write in transport.writes if write]
        self.assertEqual(
            seen,
            ["configure", "erase", "program", "raw:ABCD", "raw:EFGH", "patch", "power"],
        )

    def test_process_xml_file_skips_empty_programs_and_patch_container(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            xml_path = root / "patch.xml"
            xml_path.write_text(
                """<?xml version="1.0" ?>
<patches>
  <program filename="" SECTOR_SIZE_IN_BYTES="4"
    num_partition_sectors="2" physical_partition_number="0"
    start_sector="10" />
  <patch filename="DISK" physical_partition_number="0"
    start_sector="1" byte_offset="0" size_in_bytes="4" value="0" />
</patches>
""",
                encoding="utf-8",
            )
            transport = FakeTransport()
            client = FirehoseClient(transport, FirehoseConfig(max_xml_size=2048))

            client.process_xml_file(xml_path, root)

        seen = [_classify_write(write) for write in transport.writes if write]
        self.assertEqual(seen, ["patch"])

    def test_write_file_element_streams_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "firmware.bin").write_bytes(b"123456")
            elem = ET.fromstring(
                '<firmwarewrite filename="firmware.bin" '
                'physical_partition_number="0" />'
            )
            transport = FakeTransport()
            client = FirehoseClient(transport, FirehoseConfig(max_payload_to_target=4))

            client.write_file_element(elem, root)

        seen = [_classify_write(write) for write in transport.writes if write]
        self.assertEqual(seen, ["firmwarewrite", "raw:1234", "raw:56"])

    def test_read_to_file_receives_expected_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            elem = ET.fromstring(
                '<read filename="dump.bin" SECTOR_SIZE_IN_BYTES="4" '
                'num_partition_sectors="2" physical_partition_number="0" '
                'start_sector="0" />'
            )
            transport = FakeTransport(read_chunks=[b"ABCD", b"EFGH"])
            client = FirehoseClient(
                transport,
                FirehoseConfig(
                    sector_size=4,
                    max_payload_from_target=4,
                    max_xml_size=2048,
                ),
            )

            output = client.read_to_file(elem, root)

            self.assertEqual(output.read_bytes(), b"ABCDEFGH")
            seen = [_classify_write(write) for write in transport.writes if write]
            self.assertEqual(seen, ["read"])

    def test_fix_gpt_sends_firehose_command(self) -> None:
        transport = FakeTransport()
        client = FirehoseClient(transport)

        client.fix_gpt(4)

        xml = transport.writes[0].decode()
        self.assertIn("<fixgpt", xml)
        self.assertIn('physical_partition_number="4"', xml)

    def test_verify_programming_uses_target_sha256_digest(self) -> None:
        payload = b"ABCD"
        expected = hashlib.sha256(payload).hexdigest()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "payload.bin").write_bytes(payload)
            xml_path = root / "rawprogram.xml"
            xml_path.write_text(
                """<?xml version="1.0" ?>
<data>
  <program filename="payload.bin" SECTOR_SIZE_IN_BYTES="4"
    num_partition_sectors="1" physical_partition_number="0"
    start_sector="0" />
</data>
""",
                encoding="utf-8",
            )
            transport = FakeTransport(
                responses=[
                    ACK,
                    ACK,
                    (
                        b'<?xml version="1.0" ?><data>'
                        b'<response value="ACK" digest="' + expected.encode() + b'" />'
                        b"</data>"
                    ),
                ]
            )
            client = FirehoseClient(
                transport,
                FirehoseConfig(
                    sector_size=4,
                    max_payload_to_target=4,
                    max_xml_size=2048,
                ),
            )

            client.process_xml_file(xml_path, root, verify_programming=True)

        seen = [_classify_write(write) for write in transport.writes if write]
        self.assertEqual(seen, ["program", "raw:ABCD", "getsha256digest"])


class FailThenSucceedTransport:
    """Transport that raises on the first N read_until calls, then succeeds."""

    def __init__(self, fail_count: int, exc: Exception, success_response: bytes):
        self.fail_count = fail_count
        self.exc = exc
        self.success_response = success_response
        self.call_count = 0
        self.writes: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.writes.append(bytes(data))

    def read_until(self, marker: bytes, timeout_s: float = 10.0) -> bytes:
        self.call_count += 1
        if self.call_count <= self.fail_count:
            raise self.exc
        return self.success_response

    def read(self, size: int = 1024 * 1024, timeout_ms: int | None = None) -> bytes:
        return b""


class MultiBlockResponseTests(unittest.TestCase):
    def test_response_parses_concatenated_log_and_response_blocks(self) -> None:
        raw = (
            b'<?xml version="1.0" encoding="UTF-8" ?>\n'
            b"<data>\n"
            b'<log value="INFO: Calling handler for configure" /></data>'
            b'<?xml version="1.0" encoding="UTF-8" ?>\n'
            b"<data>\n"
            b'<log value="INFO: Storage type set to value UFS" /></data>'
            b'<?xml version="1.0" encoding="UTF-8" ?>\n'
            b"<data>\n"
            b'<response value="ACK" MemoryName="UFS" '
            b'MaxPayloadSizeToTargetInBytesSupported="1048576" /></data>'
        )
        response = FirehoseResponse.from_bytes(raw)

        self.assertTrue(response.ack)
        self.assertEqual(response.attributes["MemoryName"], "UFS")
        self.assertEqual(
            response.attributes["MaxPayloadSizeToTargetInBytesSupported"], "1048576"
        )
        self.assertIn("INFO: Calling handler for configure", response.logs)
        self.assertIn("INFO: Storage type set to value UFS", response.logs)

    def test_configure_succeeds_with_multi_block_response(self) -> None:
        transport = FakeTransport(
            responses=[
                b'<?xml version="1.0" ?><data>'
                b'<log value="INFO: handler" /></data>'
                b'<?xml version="1.0" ?><data>'
                b'<response value="ACK" MemoryName="UFS" '
                b'MaxPayloadSizeToTargetInBytesSupported="1048576" '
                b'MaxPayloadSizeFromTargetInBytes="8192" '
                b'MaxXMLSizeInBytes="4096" SECTOR_SIZE_IN_BYTES="4096" /></data>'
            ]
        )
        client = FirehoseClient(transport, FirehoseConfig(memory="ufs"))

        client.configure()

        self.assertEqual(client.config.memory, "ufs")
        self.assertEqual(client.config.max_payload_to_target, 1048576)


class ConfigureRetryTests(unittest.TestCase):
    def test_configure_retries_on_os_timeout_and_succeeds(self) -> None:
        transport = FailThenSucceedTransport(
            fail_count=2,
            exc=OSError(60, "Operation timed out"),
            success_response=(
                b'<?xml version="1.0" ?><data>'
                b'<response value="ACK" MemoryName="UFS" '
                b'MaxPayloadSizeToTargetInBytesSupported="4096" />'
                b"</data>"
            ),
        )
        client = FirehoseClient(transport, FirehoseConfig(memory="ufs"))

        client.configure(retries=3, retry_delay=0.0)

        self.assertEqual(transport.call_count, 3)
        self.assertEqual(client.config.max_payload_to_target, 4096)

    def test_configure_retries_on_firehose_error_and_succeeds(self) -> None:
        transport = FailThenSucceedTransport(
            fail_count=1,
            exc=FirehoseError("NAK"),
            success_response=(
                b'<?xml version="1.0" ?><data>'
                b'<response value="ACK" MemoryName="UFS" />'
                b"</data>"
            ),
        )
        client = FirehoseClient(transport, FirehoseConfig())

        client.configure(retries=3, retry_delay=0.0)

        self.assertEqual(transport.call_count, 2)

    def test_configure_raises_after_all_retries_exhausted(self) -> None:
        transport = FailThenSucceedTransport(
            fail_count=5,
            exc=OSError(60, "Operation timed out"),
            success_response=ACK,
        )
        client = FirehoseClient(transport, FirehoseConfig())

        with self.assertRaises(FirehoseError) as ctx:
            client.configure(retries=3, retry_delay=0.0)

        self.assertIn("3 attempts", str(ctx.exception))
        self.assertEqual(transport.call_count, 3)


def _classify_write(write: bytes) -> str:
    if write.startswith(b"<?xml"):
        text = write.decode()
        for tag in (
            "configure",
            "erase",
            "program",
            "patch",
            "power",
            "firmwarewrite",
            "read",
            "fixgpt",
            "getsha256digest",
        ):
            if f"<{tag}" in text:
                return tag
        return "xml"
    return f"raw:{write.decode(errors='replace')}"


if __name__ == "__main__":
    unittest.main()
