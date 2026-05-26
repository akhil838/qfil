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
    skipstorageinit: int = 0
    skipwrite: int = 0
    maxpayloadsizetotargetinbytes: int = 1024 * 1024
    maxpayloadsizefromtargetinbytes: int = 8192
    maxxmlsizeinbytes: int = 4096
    verbose: int = 0
    alwaysvalidate: int = 0
    maxdigesttablesizeinbytes: int = 2048
    getstorageinfo: int | None = None
    fixgpt: int | None = None
    verify_programming: bool = False
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
                    skip_storage_init=self.options.skipstorageinit,
                    skip_write=self.options.skipwrite,
                    max_payload_to_target=(self.options.maxpayloadsizetotargetinbytes),
                    max_payload_from_target=(
                        self.options.maxpayloadsizefromtargetinbytes
                    ),
                    max_xml_size=self.options.maxxmlsizeinbytes,
                    verbose=self.options.verbose,
                    always_validate=self.options.alwaysvalidate,
                    max_digest_table_size=(self.options.maxdigesttablesizeinbytes),
                ),
            )
            get_logger(__name__).info(
                "Configuring Firehose: memory=%s", self.options.memoryname
            )
            client.configure()
            if self.options.getstorageinfo is not None:
                get_logger(__name__).info(
                    "Reading Firehose storage info: lun=%s",
                    self.options.getstorageinfo,
                )
                client.get_storage_info(self.options.getstorageinfo)
            if self.options.fixgpt is not None:
                get_logger(__name__).info(
                    "Sending Firehose fixgpt: lun=%s", self.options.fixgpt
                )
                client.fix_gpt(self.options.fixgpt)
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
                        xml_path,
                        self.options.search_path,
                        progress=progress,
                        verify_programming=self.options.verify_programming,
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
    parser.add_argument("--skipstorageinit", type=int, default=0)
    parser.add_argument("--skipwrite", type=int, default=0)
    parser.add_argument(
        "--maxpayloadsizetotargetinbytes",
        type=lambda value: int(value, 0),
        default=1024 * 1024,
    )
    parser.add_argument(
        "--maxpayloadsizefromtargetinbytes",
        type=lambda value: int(value, 0),
        default=8192,
    )
    parser.add_argument(
        "--maxxmlsizeinbytes",
        type=lambda value: int(value, 0),
        default=4096,
    )
    parser.add_argument("--verbose", type=int, default=0)
    parser.add_argument("--alwaysvalidate", type=int, default=0)
    parser.add_argument(
        "--maxdigesttablesizeinbytes",
        type=lambda value: int(value, 0),
        default=2048,
    )
    parser.add_argument("--getstorageinfo", type=lambda value: int(value, 0))
    parser.add_argument("--fixgpt", type=lambda value: int(value, 0))
    parser.add_argument("--verify_programming", action="store_true")
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
                skipstorageinit=args.skipstorageinit,
                skipwrite=args.skipwrite,
                maxpayloadsizetotargetinbytes=(args.maxpayloadsizetotargetinbytes),
                maxpayloadsizefromtargetinbytes=(args.maxpayloadsizefromtargetinbytes),
                maxxmlsizeinbytes=args.maxxmlsizeinbytes,
                verbose=args.verbose,
                alwaysvalidate=args.alwaysvalidate,
                maxdigesttablesizeinbytes=args.maxdigesttablesizeinbytes,
                getstorageinfo=args.getstorageinfo,
                fixgpt=args.fixgpt,
                verify_programming=args.verify_programming,
                vid=args.vid,
                pid=args.pid,
            )
        ).run()
    except (RuntimeError, OSError) as exc:
        get_logger(__name__).error("fh_loader failed: %s", exc)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
