"""Android sparse image reader used by native QFIL programming."""

from __future__ import annotations

import struct
from pathlib import Path


SPARSE_MAGIC = 0xED26FF3A
CHUNK_RAW = 0xCAC1
CHUNK_FILL = 0xCAC2
CHUNK_DONT_CARE = 0xCAC3
CHUNK_CRC32 = 0xCAC4


class SparseImageError(RuntimeError):
    pass


def is_sparse_image(path: Path) -> bool:
    with Path(path).open("rb") as handle:
        return handle.read(4) == struct.pack("<I", SPARSE_MAGIC)


class SparseImageReader:
    def __init__(self, path: Path):
        self.handle = Path(path).open("rb")
        header = self.handle.read(28)
        if len(header) != 28:
            raise SparseImageError("Sparse image header is truncated.")
        (
            magic,
            self.major_version,
            self.minor_version,
            self.file_header_size,
            self.chunk_header_size,
            self.block_size,
            self.total_blocks,
            self.total_chunks,
            self.image_checksum,
        ) = struct.unpack("<IHHHHIIII", header)
        if magic != SPARSE_MAGIC:
            raise SparseImageError("Not an Android sparse image.")
        if self.file_header_size < 28 or self.chunk_header_size < 12:
            raise SparseImageError("Unsupported sparse image header sizes.")
        if self.file_header_size > 28:
            self.handle.read(self.file_header_size - 28)
        self.total_size = self.total_blocks * self.block_size
        self._chunk_index = 0
        self._pending = b""
        self._raw_remaining = 0
        self._fill_pattern = b""
        self._fill_remaining = 0
        self._zero_remaining = 0

    def close(self) -> None:
        self.handle.close()

    def __enter__(self) -> "SparseImageReader":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def read(self, size: int) -> bytes:
        output = bytearray()
        while len(output) < size:
            if self._pending:
                take = min(size - len(output), len(self._pending))
                output += self._pending[:take]
                self._pending = self._pending[take:]
                continue
            if self._raw_remaining:
                take = min(size - len(output), self._raw_remaining)
                data = self.handle.read(take)
                if len(data) != take:
                    raise SparseImageError("Sparse raw chunk ended early.")
                self._raw_remaining -= take
                output += data
                continue
            if self._fill_remaining:
                take = min(size - len(output), self._fill_remaining)
                repeats = (take + len(self._fill_pattern) - 1) // len(
                    self._fill_pattern
                )
                data = (self._fill_pattern * repeats)[:take]
                self._fill_remaining -= take
                output += data
                continue
            if self._zero_remaining:
                take = min(size - len(output), self._zero_remaining)
                self._zero_remaining -= take
                output += b"\x00" * take
                continue
            if self._chunk_index >= self.total_chunks:
                break
            self._load_next_chunk()
        return bytes(output)

    def _load_next_chunk(self) -> None:
        header = self.handle.read(12)
        if len(header) != 12:
            raise SparseImageError("Sparse chunk header is truncated.")
        chunk_type, _, chunk_blocks, total_size = struct.unpack("<HHII", header)
        if self.chunk_header_size > 12:
            self.handle.read(self.chunk_header_size - 12)
        data_size = total_size - self.chunk_header_size
        expanded_size = chunk_blocks * self.block_size
        if chunk_type == CHUNK_RAW:
            if data_size != expanded_size:
                raise SparseImageError("Sparse raw chunk size mismatch.")
            self._raw_remaining = data_size
        elif chunk_type == CHUNK_FILL:
            if data_size != 4:
                raise SparseImageError("Sparse fill chunk size mismatch.")
            self._fill_pattern = self.handle.read(4)
            self._fill_remaining = expanded_size
        elif chunk_type == CHUNK_DONT_CARE:
            if data_size:
                self.handle.read(data_size)
            self._zero_remaining = expanded_size
        elif chunk_type == CHUNK_CRC32:
            if data_size:
                self.handle.read(data_size)
            self._pending = b""
        else:
            raise SparseImageError(f"Unsupported sparse chunk type 0x{chunk_type:x}.")
        self._chunk_index += 1
