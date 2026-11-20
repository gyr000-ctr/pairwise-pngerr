"""截断错误码、失败可复用、失败路径不持有输入、并发安全的回归测试。"""

import concurrent.futures
import gc
import struct
import zlib

import pytest

import pngerr

SIG = b"\x89PNG\r\n\x1a\n"


def _chunk(t, p):
    return struct.pack(">I", len(p)) + t + p + struct.pack(">I", zlib.crc32(t + p) & 0xFFFFFFFF)


def _png(w=1, h=1):
    return (
        SIG
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(b"\x00\x00"))
        + _chunk(b"IEND", b"")
    )


DATA = _png(7, 2)
IHDR_END = len(SIG) + 8 + 13 + 4


def _code(data):
    with pytest.raises(pngerr.PngError) as info:
        pngerr.PngLoader().load(data)
    return info.value.code


def test_png_three_truncation_codes_are_distinct():
    head = _code(DATA[:20])
    chunk = _code(DATA[:40])
    tail = _code(DATA[:-4])
    assert len({head, chunk, tail}) == 3
    assert head == pngerr.PNG_ERR_TRUNCATED_HEAD
    assert chunk == pngerr.PNG_ERR_TRUNCATED_CHUNK
    assert tail == pngerr.PNG_ERR_TRUNCATED_TAIL


def test_png_truncation_errors_are_value_and_eof_errors():
    for cut in (4, 20, 40, len(DATA) - 4):
        with pytest.raises(ValueError):
            pngerr.PngLoader().load(DATA[:cut])
        with pytest.raises(EOFError):
            pngerr.PngLoader().load(DATA[:cut])


def test_png_full_file_result_unchanged():
    loader = pngerr.PngLoader()
    assert loader.load(DATA) == (7, 2)
    assert loader.load(_png(1, 1)) == (1, 1)
    assert loader.load(_png(100, 50)) == (100, 50)


def test_png_loader_reusable_after_failure():
    loader = pngerr.PngLoader()
    assert loader.load(DATA) == (7, 2)
    for bad in (b"", b"notpng", DATA[:20], DATA[:40], DATA[:-4]):
        with pytest.raises(pngerr.PngError):
            loader.load(bad)
        assert loader.load(DATA) == (7, 2)
    assert loader.load(_png(2, 2)) == (2, 2)


def test_png_failure_does_not_retain_input():
    data = bytearray(DATA[:-4])
    try:
        pngerr.PngLoader().load(data)
    except pngerr.PngError as exc:
        assert not any(ref is data for ref in gc.get_referents(exc))
        exc.__traceback__ = None


def test_png_concurrent_loads_are_safe():
    def work(_):
        loader = pngerr.PngLoader()
        for _ in range(50):
            assert loader.load(DATA) == (7, 2)
            with pytest.raises(pngerr.PngError):
                loader.load(DATA[:40])
        return True

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        assert all(pool.map(work, range(16)))
