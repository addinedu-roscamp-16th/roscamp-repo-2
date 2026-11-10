import pytest

from r2g.payload import decode, encode


def test_round_trip():
    d = decode(encode(trial=3, seq=7, stamp_ns=123, pad_bytes=10))
    assert d == {"trial": 3, "seq": 7, "stamp_ns": 123}


def test_pad_size():
    assert len(encode(0, 0, 0, pad_bytes=1000)) >= 1000


@pytest.mark.parametrize("bad", ["hello", "{}", '{"trial": 1}'])
def test_decode_rejects(bad):
    with pytest.raises(ValueError):
        decode(bad)
