"""fh_loader.exe compatible native entrypoint."""

from __future__ import annotations

from dataclasses import dataclass
import argparse
from pathlib import Path

from qfil.logging import get_logger
from qfil.progress import EntryProgress
from qfil.protocol import FirehoseClient, FirehoseConfig
from qfil.usb import QualcommUsbTransport


@dataclass(frozen=True)
class FhLoaderOptions:
    sendxml: tuple[Path, ...]
    search_path: Path
    memoryname: str = "ufs"
    setactivepartition: int | None = None
    reset: bool = False
    zlpawarehost: bool = True
    vid: int = 0x05C6
    pid: int = 0x9008


class FhLoaderTool:
    """Native equivalent for the fh_loader subset used by Lenovo Rescue.cmd."""

    def __init__(self, options: FhLoaderOptions):
        self.options = options

    def run(self) -> None:
        get_logger(__name__).info(
            "Opening Qualcomm USB transport: vid=0x%04x pid=0x%04x",
            self.options.vid,
            self.options.pid,
        )
        progress = EntryProgress("fh_loader")
        with QualcommUsbTransport(self.options.vid, self.options.pid) as transport:
            client = FirehoseClient(
                transport,
                FirehoseConfig(
                    memory=self.options.memoryname,
                    zlpawarehost=1 if self.options.zlpawarehost else 0,
                ),
            )
            get_logger(__name__).info(
                "Configuring Firehose: memory=%s", self.options.memoryname
            )
            client.configure()
            if self.options.setactivepartition is not None:
                get_logger(__name__).info(
                    "Setting active storage drive: %s",
                    self.options.setactivepartition,
                )
                client.set_bootable_storage_drive(self.options.setactivepartition)
            try:
                for xml_path in self.options.sendxml:
                    get_logger(__name__).info("Processing Firehose XML: %s", xml_path)
                    client.process_xml_file(
                        xml_path, self.options.search_path, progress=progress
                    )
            finally:
                progress.close()
            if self.options.reset:
                get_logger(__name__).info("Resetting target after flash")
                client.reset()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="fh_loader", description="Native fh_loader-compatible Firehose runner."
    )
    parser.add_argument("--sendxml", action="append", default=[])
    parser.add_argument("--search_path", type=Path, default=Path("."))
    parser.add_argument("--memoryname", default="ufs")
    parser.add_argument("--setactivepartition", type=lambda value: int(value, 0))
    parser.add_argument("--reset", action="store_true")
    parser.add_argument("--zlpawarehost", type=int, default=1)
    parser.add_argument("--vid", type=lambda value: int(value, 0), default=0x05C6)
    parser.add_argument("--pid", type=lambda value: int(value, 0), default=0x9008)
    parser.add_argument(
        "--port", help="Accepted for compatibility; USB auto-detection is used."
    )
    parser.add_argument("--noprompt", action="store_true")
    parser.add_argument("--showpercentagecomplete", action="store_true")
    args = parser.parse_args(argv)
    search_path = args.search_path.resolve()
    sendxml = tuple(
        (search_path / value.strip()).resolve()
        for raw_value in args.sendxml
        for value in raw_value.split(",")
        if value.strip()
    )
    try:
        FhLoaderTool(
            FhLoaderOptions(
                sendxml=sendxml,
                search_path=search_path,
                memoryname=args.memoryname.lower(),
                setactivepartition=args.setactivepartition,
                reset=args.reset,
                zlpawarehost=bool(args.zlpawarehost),
                vid=args.vid,
                pid=args.pid,
            )
        ).run()
    except (RuntimeError, OSError) as exc:
        get_logger(__name__).error("fh_loader failed: %s", exc)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
