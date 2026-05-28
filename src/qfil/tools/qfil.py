"""QFIL-compatible orchestration for Software Fix Rescue.cmd."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from qfil.logging import get_logger
from qfil.progress import EntryProgress
from qfil.protocol import FirehoseClient, FirehoseConfig, SaharaClient
from qfil.protocol.sahara import SaharaError
from qfil.software_fix import (
    QfilPlan,
    parse_program_entries,
    parse_rescue_cmd,
    summarize_plan,
)
from qfil.usb import QualcommUsbTransport, UsbTransportError


def build_qfil_module_command(plan: QfilPlan) -> list[str]:
    return [
        sys.executable,
        "-m",
        "qfil",
        str(plan.startup_file),
        "--flash",
    ]


def run_qfil_plan(plan: QfilPlan, dry_run: bool = True) -> None:
    if dry_run:
        return
    if not plan.programmer:
        raise RuntimeError("QFIL plan does not include a QSahara programmer loader.")
    get_logger(__name__).info("Opening Qualcomm 9008 USB transport for Sahara/Firehose")
    progress = EntryProgress("firehose")
    with QualcommUsbTransport.auto() as transport:
        sahara_done = False
        if transport.pid == 0x9008:
            get_logger(__name__).info(
                "Uploading programmer: %s image_id=%s",
                plan.programmer.loader,
                plan.programmer.image_id,
            )
            try:
                SaharaClient(transport).upload_programmer(
                    plan.programmer.loader, plan.programmer.image_id
                )
                sahara_done = True
            except SaharaError as exc:
                get_logger(__name__).warning(
                    "Sahara upload failed (%s), device may already be in Firehose mode",
                    exc,
                )
        else:
            get_logger(__name__).info(
                "Device already in Firehose mode (PID %04x), skipping Sahara",
                transport.pid,
            )
        if sahara_done:
            _reopen_transport_for_firehose(transport)
        else:
            transport.reopen()
        client = FirehoseClient(
            transport,
            FirehoseConfig(
                memory=plan.firehose.memory or "ufs",
                zlpawarehost=1,
            ),
        )
        get_logger(__name__).info(
            "Configuring Firehose: memory=%s", plan.firehose.memory or "ufs"
        )
        client.configure()
        get_logger(__name__).info(
            "Firehose ready: memory=%s max_payload=%s sector_size=%s",
            client.config.memory,
            client.config.max_payload_to_target,
            client.config.sector_size,
        )
        if plan.firehose.set_active_partition is not None:
            get_logger(__name__).info(
                "Setting active storage drive: %s",
                plan.firehose.set_active_partition,
            )
            client.set_bootable_storage_drive(plan.firehose.set_active_partition)
        try:
            for xml_path in [*plan.firehose.rawprograms, *plan.firehose.patches]:
                get_logger(__name__).info("Processing Firehose XML: %s", xml_path)
                client.process_xml_file(
                    xml_path,
                    plan.image_dir,
                    progress=progress,
                    verify_programming=plan.firehose.verify_programming,
                )
        finally:
            progress.close()
        if plan.firehose.reset:
            get_logger(__name__).info("Resetting target after flash")
            client.reset()


def _reopen_transport_for_firehose(transport: QualcommUsbTransport) -> None:
    get_logger(__name__).info("Reopening USB transport for Firehose")
    transport.close()
    # Wait for programmer to boot and USB to re-enumerate
    time.sleep(2)
    deadline = time.monotonic() + 10
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            transport.open()
            transport.drain()
            return
        except (UsbTransportError, OSError) as exc:
            last_error = exc
            time.sleep(0.5)
    raise UsbTransportError(
        "Programmer upload completed, but Qualcomm USB transport did not reopen for Firehose."
    ) from last_error


class QfilTool:
    def __init__(self, startup_file: Path, image_dir: Path | None = None):
        self.plan = parse_rescue_cmd(startup_file, image_dir)

    def inspect(self, show_programs: bool = False) -> None:
        for line in summarize_plan(self.plan):
            get_logger(__name__).info(line)
        get_logger(__name__).info("\nNative module command:")
        get_logger(__name__).info(" ".join(build_qfil_module_command(self.plan)))
        if show_programs:
            get_logger(__name__).info("\nProgram entries:")
            for entry in parse_program_entries(self.plan.firehose.rawprograms):
                sectors = entry.sectors if entry.sectors is not None else "dynamic"
                get_logger(__name__).info(
                    f"{entry.xml.name}: {entry.filename} -> {entry.label} "
                    f"lun={entry.lun} start={entry.start_sector} sectors={sectors}"
                )

    def flash(self) -> None:
        run_qfil_plan(self.plan, dry_run=False)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="qfil", description="Native QFIL-compatible Software Fix runner."
    )
    parser.add_argument(
        "startup_file", type=Path, help="Path to Rescue.cmd or Flash.cmd"
    )
    parser.add_argument(
        "--image-dir", type=Path, help="Override image/search_path directory"
    )
    parser.add_argument("--show-programs", action="store_true")
    parser.add_argument("--flash", action="store_true")
    args = parser.parse_args(argv)
    tool = QfilTool(args.startup_file, args.image_dir)
    try:
        tool.inspect(show_programs=args.show_programs)
        if args.flash:
            tool.flash()
    except (RuntimeError, OSError) as exc:
        get_logger(__name__).error("QFIL failed: %s", exc)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
