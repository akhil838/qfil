"""QSaharaServer.exe compatible native entrypoint."""

from __future__ import annotations

from dataclasses import dataclass
import argparse
from pathlib import Path

from qfil.protocol import SaharaClient
from qfil.usb import QualcommUsbTransport


@dataclass(frozen=True)
class QSaharaServerOptions:
    programmer_id: int
    programmer: Path
    vid: int = 0x05C6
    pid: int = 0x9008


class QSaharaServerTool:
    """Small native equivalent for `QSaharaServer.exe -s id:path`."""

    def __init__(self, options: QSaharaServerOptions):
        self.options = options

    def run(self) -> None:
        with QualcommUsbTransport(self.options.vid, self.options.pid) as transport:
            SaharaClient(transport).upload_programmer(
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
        required=True,
        help="Programmer mapping, e.g. 13:xbl_s_devprg_ns.melf",
    )
    parser.add_argument("--vid", type=lambda value: int(value, 0), default=0x05C6)
    parser.add_argument("--pid", type=lambda value: int(value, 0), default=0x9008)
    args = parser.parse_args(argv)
    image_id, programmer = parse_sahara_spec(args.sahara_spec)
    QSaharaServerTool(
        QSaharaServerOptions(image_id, programmer, args.vid, args.pid)
    ).run()


if __name__ == "__main__":
    main()
