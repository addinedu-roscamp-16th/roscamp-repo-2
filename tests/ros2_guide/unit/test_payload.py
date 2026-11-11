import pytest

from r2g.payload import decode, encode, encode_sized


def test_round_trip():
    d = decode(encode(trial=3, seq=7, stamp_ns=123, pad_bytes=10))
    assert d == {"trial": 3, "seq": 7, "stamp_ns": 123}


def test_pad_size():
    assert len(encode(0, 0, 0, pad_bytes=1000)) >= 1000


@pytest.mark.parametrize("bad", ["hello", "{}", '{"trial": 1}'])
def test_decode_rejects(bad):
    with pytest.raises(ValueError):
        decode(bad)


def test_encode_sized_exact_length():
    for size in (1024, 65536, 512000):
        s = encode_sized(7, 123, size)
        assert len(s.encode("utf-8")) == size and decode(s)["seq"] == 7
    with pytest.raises(ValueError):
        encode_sized(0, 0, 10)
