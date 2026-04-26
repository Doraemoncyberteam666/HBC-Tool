import io
from hbctool.util import BitReader


def test_readall_empty_stream():
    reader = BitReader(io.BytesIO(b""))
    assert reader.readall() == bytearray()


def test_readall_full_stream():
    reader = BitReader(io.BytesIO(b"\x01\x02\x03\xff\x00"))
    assert reader.readall() == bytearray(b"\x01\x02\x03\xff\x00")


def test_readall_partial_stream():
    reader = BitReader(io.BytesIO(b"\x01\x02\x03\xff\x00"))
    assert reader.readbytes(2) == 258
    assert reader.readall() == bytearray(b"\x03\xff\x00")


def test_readall_multiple_times():
    reader = BitReader(io.BytesIO(b"\x01\x02\x03"))
    assert reader.readall() == bytearray(b"\x01\x02\x03")
    assert reader.readall() == bytearray()


def test_readall_returns_bytearray():
    """``readall`` should return a ``bytearray`` (~1 byte per element)
    rather than a ``list[int]`` (~8 bytes per element)."""
    reader = BitReader(io.BytesIO(b"\x01\x02\x03"))
    result = reader.readall()
    assert isinstance(result, bytearray)
