"""Standalone native QFIL/QSahara/Firehose module."""

from .protocol import FirehoseClient, FirehoseError, SaharaClient, SaharaError
from .software_fix import (
    FirehoseOptions,
    PatchEntry,
    ProgramEntry,
    QfilPlan,
    SaharaProgrammer,
    discover_firehose_loader,
    discover_qfil_files,
    has_qfil_files,
    parse_patch_entries,
    parse_program_entries,
    parse_rescue_cmd,
    resolve_qfil_image_dir,
    select_qfil_set,
    summarize_plan,
)
from .tools.qfil import build_qfil_module_command, run_qfil_plan
from .images import SparseImageReader, SparseImageError, is_sparse_image
from .usb import QualcommUsbTransport, UsbTransportError

__all__ = [
    "FirehoseClient",
    "FirehoseError",
    "FirehoseOptions",
    "PatchEntry",
    "ProgramEntry",
    "QfilPlan",
    "QualcommUsbTransport",
    "SaharaClient",
    "SaharaError",
    "SaharaProgrammer",
    "SparseImageError",
    "SparseImageReader",
    "UsbTransportError",
    "build_qfil_module_command",
    "discover_firehose_loader",
    "discover_qfil_files",
    "has_qfil_files",
    "parse_patch_entries",
    "parse_program_entries",
    "parse_rescue_cmd",
    "resolve_qfil_image_dir",
    "run_qfil_plan",
    "select_qfil_set",
    "summarize_plan",
    "is_sparse_image",
]
