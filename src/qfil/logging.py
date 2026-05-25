"""Shared logging setup for qfil command-line tools."""

from __future__ import annotations

import logging
import sys

DEFAULT_FORMAT = "%(levelname)s:%(name)s:%(message)s"


def configure_logging(level: int | str = logging.INFO) -> None:
    root = logging.getLogger()
    if root.handlers:
        root.setLevel(level)
        return
    logging.basicConfig(level=level, format=DEFAULT_FORMAT, stream=sys.stdout)


def get_logger(name: str) -> logging.Logger:
    configure_logging()
    return logging.getLogger(name)
