from hbctool.util import memcpy

def test_memcpy_list_happy_path():
    dest = [0, 0, 0, 0, 0]
    src = [1, 2, 3]
    memcpy(dest, src, 1, 3)
    assert dest == [0, 1, 2, 3, 0]

def test_memcpy_list_full_copy():
    dest = [0, 0, 0]
    src = [1, 2, 3]
    memcpy(dest, src, 0, 3)
    assert dest == [1, 2, 3]

def test_memcpy_bytes_src():
    dest = [0, 0, 0, 0, 0]
    src = b"\x01\x02\x03"
    memcpy(dest, src, 1, 3)
    assert dest == [0, 1, 2, 3, 0]

def test_memcpy_bytearray_src():
    dest = [0, 0, 0, 0, 0]
    src = bytearray([1, 2, 3])
    memcpy(dest, src, 1, 3)
    assert dest == [0, 1, 2, 3, 0]

def test_memcpy_zero_length():
    dest = [0, 0, 0]
    src = [1, 2, 3]
    memcpy(dest, src, 1, 0)
    assert dest == [0, 0, 0]


# Regression: ``obj["inst"]`` is a ``bytearray`` after the §5.3 refactor,
# and ``setFunction`` calls ``memcpy(obj["inst"], bc, ...)``. The
# ``_fastutil`` C extension used to ``PyList_Check(dest)`` and crash with
# ``TypeError: dest must be a list``. Make sure both branches now accept
# ``bytearray`` destinations.

def test_memcpy_bytearray_dest_with_list_src():
    dest = bytearray(5)
    src = [1, 2, 3]
    memcpy(dest, src, 1, 3)
    assert dest == bytearray([0, 1, 2, 3, 0])


def test_memcpy_bytearray_dest_with_bytes_src():
    dest = bytearray(5)
    src = b"\x01\x02\x03"
    memcpy(dest, src, 1, 3)
    assert dest == bytearray([0, 1, 2, 3, 0])


def test_memcpy_bytearray_dest_with_bytearray_src():
    dest = bytearray(5)
    src = bytearray([1, 2, 3])
    memcpy(dest, src, 1, 3)
    assert dest == bytearray([0, 1, 2, 3, 0])


def test_memcpy_rejects_signed_overflow_args():
    """``start + length`` could wrap on ``Py_ssize_t`` overflow and bypass
    the dest-size bounds check, allowing a heap buffer overflow. The C
    extension must handle this safely (the pure-Python fallback gets the
    same protection from CPython's slice handling)."""
    import sys
    import hbctool.util as u

    if u._fastutil is None:
        return  # pure-Python path is fine; nothing to regress.

    dest = bytearray(16)
    src = b"\x00" * 16
    huge = sys.maxsize  # 2**63 - 1 on a 64-bit box; ``huge + huge`` wraps negative.

    import pytest

    with pytest.raises((IndexError, OverflowError, ValueError)):
        u.memcpy(dest, src, huge, huge)
    with pytest.raises((IndexError, OverflowError, ValueError)):
        u.memcpy(dest, src, 0, huge)
    with pytest.raises((IndexError, OverflowError, ValueError)):
        u.memcpy(dest, src, huge, 0)


def test_memcpy_setfunction_roundtrip():
    """End-to-end: load a real HBC bundle, get a function, set it back
    unchanged. This is the path that crashed with the C extension."""
    import pathlib
    import hbctool

    fixture = pathlib.Path(__file__).parent.parent / "Testfiles" / "index.android.bundle"
    if not fixture.exists():
        return  # bundle isn't shipped in every checkout; skip silently.

    with open(fixture, "rb") as f:
        hbo = hbctool.hbc.load(f)

    insts = hbo.getFunction(0)
    assert isinstance(hbo.getObj()["inst"], bytearray)
    # Should not raise. Was: TypeError: dest must be a list.
    hbo.setFunction(0, insts)
