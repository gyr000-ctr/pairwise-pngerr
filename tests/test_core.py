import math
import struct

import pytest

import pngerr

def _raises(fn, exc=ValueError):
    try:
        fn()
    except exc:
        return True
    return False

import struct
import zlib

SIG = b"\x89PNG\r\n\x1a\n"


def _chunk(t, p):
    return struct.pack(">I", len(p)) + t + p + struct.pack(">I", zlib.crc32(t + p) & 0xFFFFFFFF)


def _png(w=1, h=1):
    return SIG + _chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0)) + _chunk(b"IDAT", zlib.compress(b"\x00\x00")) + _chunk(b"IEND", b"")

CASES = [
        ("valid", lambda: pngerr.PngLoader().load(_png(3, 4)) == (3, 4)),
        ("one", lambda: pngerr.PngLoader().load(_png()) == (1, 1)),
        ("bad_sig", lambda: _raises(lambda: pngerr.PngLoader().load(b"notpng"))),
        ("truncated_head", lambda: _raises(lambda: pngerr.PngLoader().load(_png()[:10]))),
        ("truncated_tail", lambda: _raises(lambda: pngerr.PngLoader().load(_png()[:-4]))),
        ("empty", lambda: _raises(lambda: pngerr.PngLoader().load(b""))),
        ("width", lambda: pngerr.PngLoader().load(_png(7, 2))[0] == 7),
        ("height", lambda: pngerr.PngLoader().load(_png(7, 2))[1] == 2),
        ("type", lambda: isinstance(pngerr.PngLoader().load(_png()), tuple)),
        ("deterministic", lambda: pngerr.PngLoader().load(_png()) == pngerr.PngLoader().load(_png())),
        ("bad_crc", lambda: _raises(lambda: pngerr.PngLoader().load(_png()[:-1] + b"\x00"))),
        ("no_mutation", lambda: (lambda b: (pngerr.PngLoader().load(b), b[:8] == SIG)[1])(_png())),
        ("big", lambda: pngerr.PngLoader().load(_png(100, 50)) == (100, 50)),
        ("zero_size", lambda: _raises(lambda: pngerr.PngLoader().load(_png(0, 1)))),
        ("reuse", lambda: (lambda l: (l.load(_png()), l.load(_png(2, 2)) == (2, 2))[1])(pngerr.PngLoader())),
    ]


@pytest.mark.parametrize("name,check", CASES, ids=[c[0] for c in CASES])
def test_spec(name, check):
    assert check() is True
