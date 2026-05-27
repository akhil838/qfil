"""QSaharaServer.exe compatible native entrypoint."""

from __future__ import annotations

from dataclasses import dataclass
import argparse
from pathlib import Path
import sys

from qfil.protocol import SaharaClient
from qfil.usb import QualcommUsbTransport


@dataclass(frozen=True)
class QSaharaServerOptions:
    programmer_id: int | None = None
    programmer: Path | None = None
    command_id: int | None = None
    memdump: bool = False
    max_dump_bytes: int | None = None
    command_output_dir: Path = Path(".")
    vid: int = 0x05C6
    pid: int = 0x9008


class QSaharaServerTool:
    """Small native equivalent for `QSaharaServer.exe -s id:path`."""

    def __init__(self, options: QSaharaServerOptions):
        self.options = options

    def run(self) -> None:
        with QualcommUsbTransport(self.options.vid, self.options.pid) as transport:
            client = SaharaClient(transport)
            if self.options.memdump:
                paths = client.dump_memory(
                    self.options.command_output_dir,
                    max_region_bytes=self.options.max_dump_bytes,
                )
                for path in paths:
                    sys.stdout.write(f"{path}\n")
                return
            if self.options.command_id is not None:
                payload = client.execute_command(self.options.command_id)
                output = self.options.command_output_dir / (
                    f"commandop{self.options.command_id:02d}.bin"
                )
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(payload)
                sys.stdout.write(f"{output}\n")
                return
            if self.options.programmer is None or self.options.programmer_id is None:
                raise RuntimeError("QSaharaServer requires either -s or -c.")
            client.upload_programmer(
                self.options.programmer,
                expected_image_id=self.options.programmer_id,
            )


def parse_sahara_spec(spec: str) -> tuple[int, Path]:
    if ":" not in spec:
        raise argparse.ArgumentTypeError("-s must be in id:path form")
    image_id, path = spec.split(":", 1)
    return int(image_id, 0), Path(path)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="qsaharaserver",
        description="Native QSaharaServer-compatible loader upload.",
    )
    parser.add_argument(
        "-s",
        dest="sahara_spec",
        help="Programmer mapping, e.g. 13:xbl_s_devprg_ns.melf",
    )
    parser.add_argument(
        "-c",
        "--command",
        dest="command_id",
        type=lambda value: int(value, 0),
        help="Execute Sahara command id and save returned data as commandopXX.bin.",
    )
    parser.add_argument(
        "-m",
        "--memdump",
        action="store_true",
        help="Run Sahara memory-debug dump mode.",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("."),
        help="Directory for Sahara command output files.",
    )
    parser.add_argument(
        "--max-dump-bytes",
        type=lambda value: int(value, 0),
        help="Limit bytes read per memory region.",
    )
    parser.add_argument("--vid", type=lambda value: int(value, 0), default=0x05C6)
    parser.add_argument("--pid", type=lambda value: int(value, 0), default=0x9008)
    args = parser.parse_args(argv)
    modes = sum(
        bool(value)
        for value in (args.sahara_spec, args.command_id is not None, args.memdump)
    )
    if modes != 1:
        parser.error("exactly one of -s, -c, or -m is required.")
    image_id = None
    programmer = None
    if args.sahara_spec:
        image_id, programmer = parse_sahara_spec(args.sahara_spec)
    QSaharaServerTool(
        QSaharaServerOptions(
            programmer_id=image_id,
            programmer=programmer,
            command_id=args.command_id,
            memdump=args.memdump,
            max_dump_bytes=args.max_dump_bytes,
            command_output_dir=args.out_dir,
            vid=args.vid,
            pid=args.pid,
        )
    ).run()


if __name__ == "__main__":
    main()
