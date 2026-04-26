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
