"""Regression tests for header-size validation.

A crafted Hermes bundle whose header claims sizes far larger than the
file should be rejected up-front with ``ValueError`` rather than e.g.
allocating gigabytes before failing or hanging.
"""
import json
import pathlib
import struct
from io import BytesIO

import pytest

from hbctool import hbc

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
FIXTURE = REPO_ROOT / "Testfiles" / "index.android.bundle"
HBC96_STRUCTURE = REPO_ROOT / "hbctool" / "hbc" / "hbc96" / "data" / "structure.json"


@pytest.fixture(scope="module")
def fixture_bytes() -> bytes:
    if not FIXTURE.exists():
        pytest.skip(f"Fixture missing: {FIXTURE}")
    return FIXTURE.read_bytes()


@pytest.fixture(scope="module")
def header_layout() -> dict:
    layout = json.loads(HBC96_STRUCTURE.read_text())["header"]
    offsets: dict[str, tuple[int, int]] = {}
    off = 0
    for name, fmt in layout.items():
        kind, bits, count = fmt
        size = count if kind == "bytes" else (bits // 8) * count
        offsets[name] = (off, size)
        off += size
    return offsets


def _patch_uint(data: bytearray, header_layout: dict, name: str, value: int) -> bytearray:
    off, size = header_layout[name]
    if size == 4:
        struct.pack_into("<I", data, off, value & 0xFFFFFFFF)
    elif size == 8:
        struct.pack_into("<Q", data, off, value & 0xFFFFFFFFFFFFFFFF)
    else:
        raise ValueError(f"Unsupported field size: {size}")
    return data


def test_legit_bundle_still_loads(fixture_bytes):
    h = hbc.load(BytesIO(fixture_bytes))
    assert h.getVersion() == 96


def test_oversized_string_storage_size_rejected(fixture_bytes, header_layout):
    data = bytearray(fixture_bytes)
    _patch_uint(data, header_layout, "stringStorageSize", 0xFFFFFFFF)
    with pytest.raises(ValueError, match=r"stringStorageSize"):
        hbc.load(BytesIO(bytes(data)))


def test_oversized_array_buffer_size_rejected(fixture_bytes, header_layout):
    data = bytearray(fixture_bytes)
    _patch_uint(data, header_layout, "arrayBufferSize", len(fixture_bytes) + 1)
    with pytest.raises(ValueError, match=r"arrayBufferSize"):
        hbc.load(BytesIO(bytes(data)))


def test_huge_function_count_rejected(fixture_bytes, header_layout):
    data = bytearray(fixture_bytes)
    _patch_uint(data, header_layout, "functionCount", 0x40000000)
    with pytest.raises(ValueError, match=r"functionCount"):
        hbc.load(BytesIO(bytes(data)))


def test_huge_string_count_rejected(fixture_bytes, header_layout):
    data = bytearray(fixture_bytes)
    _patch_uint(data, header_layout, "stringCount", 0x40000000)
    with pytest.raises(ValueError, match=r"stringCount"):
        hbc.load(BytesIO(bytes(data)))


def test_table_entry_size_handles_bit_packed_rows():
    """``_table_entry_size`` must compute byte sizes for bit-packed
    rows by summing bits across the whole row and rounding once -- not
    by truncating each bit field independently. Otherwise a crafted
    bundle with an inflated table count can sneak past the validation.
    """
    from hbctool.hbc._base import _table_entry_size

    layout = json.loads(HBC96_STRUCTURE.read_text())

    # SmallFuncHeader = 9 bit fields summing to 25+7+15+17+25+7+8+8+8 = 120 bits
    # plus a trailing uint8 = 16 bytes per row.
    assert _table_entry_size(layout["SmallFuncHeader"]) == 16

    # SmallStringTableEntry = 1+23+8 bits = 32 bits = 4 bytes per row.
    assert _table_entry_size(layout["SmallStringTableEntry"]) == 4


def test_huge_function_count_with_correct_smallfuncheader_size(fixture_bytes, header_layout):
    """Regression: the old ``_struct_size`` underestimated SmallFuncHeader
    at 13 bytes (instead of the real 16). With the corrected size, a
    smaller crafted ``functionCount`` is rejected -- a count that fit
    "within file size" using the buggy 13-byte denominator must still
    be rejected by the corrected 16-byte denominator.
    """
    file_size = len(fixture_bytes)
    # Pick N such that N * 13 <= file_size but N * 16 > file_size.
    n = (file_size // 13) - 1
    assert n * 13 <= file_size, "test setup invalid"
    assert n * 16 > file_size, "test setup invalid"

    data = bytearray(fixture_bytes)
    _patch_uint(data, header_layout, "functionCount", n)
    with pytest.raises(ValueError, match=r"functionCount"):
        hbc.load(BytesIO(bytes(data)))
