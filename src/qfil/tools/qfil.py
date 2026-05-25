"""QFIL-compatible orchestration for Software Fix Rescue.cmd."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from qfil.protocol import FirehoseClient, FirehoseConfig, SaharaClient
from qfil.software_fix import (
    QfilPlan,
    parse_program_entries,
    parse_rescue_cmd,
    summarize_plan,
)
from qfil.usb import QualcommUsbTransport


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
    with QualcommUsbTransport.auto() as transport:
        SaharaClient(transport).upload_programmer(
            plan.programmer.loader, plan.programmer.image_id
        )
        client = FirehoseClient(
            transport,
            FirehoseConfig(
                memory=plan.firehose.memory or "ufs",
                zlpawarehost=1 if plan.firehose.zlpawarehost else 0,
            ),
        )
        client.configure()
        if plan.firehose.set_active_partition is not None:
            client.set_bootable_storage_drive(plan.firehose.set_active_partition)
        for xml_path in [*plan.firehose.rawprograms, *plan.firehose.patches]:
            client.process_xml_file(xml_path, plan.image_dir, progress=_print_progress)
        if plan.firehose.reset:
            client.reset()


def _print_progress(entry, written: int, total: int) -> None:
    percent = 100.0 if total == 0 else (written / total) * 100
    print(f"{entry.label}: {percent:5.1f}% {written}/{total}", flush=True)


class QfilTool:
    def __init__(self, startup_file: Path, image_dir: Path | None = None):
        self.plan = parse_rescue_cmd(startup_file, image_dir)

    def inspect(self, show_programs: bool = False) -> None:
        for line in summarize_plan(self.plan):
            print(line)
        print("\nNative module command:")
        print(" ".join(build_qfil_module_command(self.plan)))
        if show_programs:
            print("\nProgram entries:")
            for entry in parse_program_entries(self.plan.firehose.rawprograms):
                sectors = entry.sectors if entry.sectors is not None else "dynamic"
                print(
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
    tool.inspect(show_programs=args.show_programs)
    if args.flash:
        tool.flash()


if __name__ == "__main__":
    main()
