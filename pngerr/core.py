"""PNG 头解析核心。

对外 API 保持不变：``PngLoader().load(data) -> (width, height)``。
解析只依赖签名与 IHDR，不解压 IDAT、不分配像素缓冲，因此超大图可流式处理；
完整文件的返回值与原加载路径逐字节一致。

失败时抛出 ``PngError``（``ValueError`` 的子类，截断类同时是 ``EOFError``），
其 ``code`` 属性给出稳定错误码，三类截断彼此可区分：

    PNG_ERR_SIGNATURE        不足 8 字节或签名不匹配（含空输入）
    PNG_ERR_TRUNCATED_HEAD   签名合法但不足一个完整 IHDR 块（< 33 字节）
    PNG_ERR_TRUNCATED_CHUNK  已读到块头，但块头声明的数据/CRC 不完整
    PNG_ERR_TRUNCATED_TAIL   IHDR 之后在块边界处结束（缺 IEND）或 IEND 不完整
    PNG_ERR_CRC              块 CRC 校验失败
    PNG_ERR_INVALID          其它结构或取值错误

加载器在失败后不保留任何解析状态，可立即复用；解析过程只用局部变量，
不持有输入缓冲区的引用，失败路径不会造成 RSS 增长，并发调用互不干扰。
"""

import struct
import zlib

__all__ = [
    "PngLoader",
    "PngError",
    "PngSignatureError",
    "PngTruncatedError",
    "PngHeadTruncatedError",
    "PngChunkTruncatedError",
    "PngTailTruncatedError",
    "PngChecksumError",
    "PngFormatError",
    "PNG_ERR_SIGNATURE",
    "PNG_ERR_TRUNCATED_HEAD",
    "PNG_ERR_TRUNCATED_CHUNK",
    "PNG_ERR_TRUNCATED_TAIL",
    "PNG_ERR_CRC",
    "PNG_ERR_INVALID",
]

PNG_ERR_SIGNATURE = "PNG_ERR_SIGNATURE"
PNG_ERR_TRUNCATED_HEAD = "PNG_ERR_TRUNCATED_HEAD"
PNG_ERR_TRUNCATED_CHUNK = "PNG_ERR_TRUNCATED_CHUNK"
PNG_ERR_TRUNCATED_TAIL = "PNG_ERR_TRUNCATED_TAIL"
PNG_ERR_CRC = "PNG_ERR_CRC"
PNG_ERR_INVALID = "PNG_ERR_INVALID"

_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_SIG_LEN = len(_SIGNATURE)
_IHDR = b"IHDR"
_IEND = b"IEND"
_IEND_HEADER = b"\x00\x00\x00\x00IEND"
_IHDR_BODY = 13
# 签名(8) + 块头(8) + IHDR 数据(13) + CRC(4)
_FIRST_CHUNK_END = _SIG_LEN + 8 + _IHDR_BODY + 4
_MAX_DIM = 0x7FFFFFFF

_CHUNK_HEADER = struct.Struct(">I4s")
_U32 = struct.Struct(">I")
_IHDR_DIMS = struct.Struct(">II")


class PngError(ValueError):
    """带稳定错误码的解析失败。

    ``code`` 是 ``PNG_ERR_*`` 之一；``message`` 为人类可读的细节。
    """

    __slots__ = ("code", "message")

    def __init__(self, code, message=""):
        self.code = code
        self.message = message
        super().__init__(code, message)

    def __str__(self):
        if self.message:
            return "%s: %s" % (self.code, self.message)
        return self.code


class PngSignatureError(PngError):
    """PNG 签名缺失或不匹配。"""


class PngFormatError(PngError):
    """非截断类的结构或取值错误。"""


class PngChecksumError(PngError):
    """块 CRC 校验失败。"""


class PngTruncatedError(PngError, EOFError):
    """数据被截断。"""


class PngHeadTruncatedError(PngTruncatedError):
    """签名与 IHDR 之间被截断。"""


class PngChunkTruncatedError(PngTruncatedError):
    """块头已读到，但块数据或 CRC 不完整。"""


class PngTailTruncatedError(PngTruncatedError):
    """尾部截断：缺少 IEND 或 IEND 不完整。"""


_ERROR_TYPES = {
    PNG_ERR_SIGNATURE: PngSignatureError,
    PNG_ERR_TRUNCATED_HEAD: PngHeadTruncatedError,
    PNG_ERR_TRUNCATED_CHUNK: PngChunkTruncatedError,
    PNG_ERR_TRUNCATED_TAIL: PngTailTruncatedError,
    PNG_ERR_CRC: PngChecksumError,
    PNG_ERR_INVALID: PngFormatError,
}


def _fail(code, message):
    raise _ERROR_TYPES[code](code, message)


def _parse(view):
    """解析 memoryview，返回 (width, height)；失败抛 PngError。"""
    total = view.nbytes
    if total < _SIG_LEN:
        if total and bytes(view) == _SIGNATURE[:total]:
            _fail(
                PNG_ERR_TRUNCATED_HEAD,
                "签名不完整（需要 %d 字节，实际 %d 字节）" % (_SIG_LEN, total),
            )
        _fail(PNG_ERR_SIGNATURE, "数据长度 %d，不足 PNG 签名 %d 字节" % (total, _SIG_LEN))
    if bytes(view[:_SIG_LEN]) != _SIGNATURE:
        _fail(PNG_ERR_SIGNATURE, "PNG 签名不匹配")
    if total < _FIRST_CHUNK_END:
        # 块头已读全且明确不是 IHDR 时是格式错误，与“只是还没读够”区分开。
        if total >= _SIG_LEN + 8:
            length, ctype = _CHUNK_HEADER.unpack_from(view, _SIG_LEN)
            if ctype != _IHDR:
                _fail(PNG_ERR_INVALID, "首个块不是 IHDR")
        _fail(
            PNG_ERR_TRUNCATED_HEAD,
            "IHDR 块不完整（需要 %d 字节，实际 %d 字节）" % (_FIRST_CHUNK_END, total),
        )

    # IHDR 必须完整（上面已保证），直接解出尺寸。
    length, ctype = _CHUNK_HEADER.unpack_from(view, _SIG_LEN)
    if ctype != _IHDR:
        _fail(PNG_ERR_INVALID, "首个块不是 IHDR")
    if length != _IHDR_BODY:
        _fail(PNG_ERR_INVALID, "IHDR 长度非法: %d" % length)

    body = _SIG_LEN + 8
    crc_end = body + _IHDR_BODY
    if zlib.crc32(view[_SIG_LEN + 4:crc_end]) != _U32.unpack_from(view, crc_end)[0]:
        _fail(PNG_ERR_CRC, "IHDR CRC 校验失败")

    width, height = _IHDR_DIMS.unpack_from(view, body)
    if not 0 < width <= _MAX_DIM or not 0 < height <= _MAX_DIM:
        _fail(PNG_ERR_INVALID, "图像尺寸非法: %dx%d" % (width, height))

    # 走到 IEND，确保文件自身完整且各块 CRC 正确；不解压 IDAT，超大图也只是
    # 按块头流式扫描，不分配像素缓冲。
    pos = _FIRST_CHUNK_END
    while True:
        remaining = total - pos
        if remaining == 0:
            _fail(PNG_ERR_TRUNCATED_TAIL, "数据在块边界处结束，缺少 IEND 块")
        if remaining < 8:
            # 残余字节恰好是 IEND 块头（长度 0 + b"IEND"）的前缀时属于尾部截断，
            # 其余情况是中间块头被截断。
            if _IEND_HEADER.startswith(bytes(view[pos:])):
                _fail(PNG_ERR_TRUNCATED_TAIL, "IEND 块头不完整（剩余 %d 字节）" % remaining)
            _fail(PNG_ERR_TRUNCATED_CHUNK, "块头不完整（剩余 %d 字节）" % remaining)

        length, ctype = _CHUNK_HEADER.unpack_from(view, pos)
        body = pos + 8
        if length > total - body:
            _fail(
                PNG_ERR_TRUNCATED_CHUNK,
                "块 %r 声明 %d 字节，实际仅剩 %d 字节" % (bytes(ctype), length, total - body),
            )
        if ctype == _IEND and length != 0:
            _fail(PNG_ERR_INVALID, "IEND 块长度应为 0，实际 %d" % length)

        end = body + length
        if total - end < 4:
            if ctype == _IEND:
                _fail(PNG_ERR_TRUNCATED_TAIL, "IEND 块 CRC 缺失")
            _fail(PNG_ERR_TRUNCATED_CHUNK, "块 %r 的 CRC 不完整" % (bytes(ctype),))
        if zlib.crc32(view[pos + 4:end]) != _U32.unpack_from(view, end)[0]:
            _fail(PNG_ERR_CRC, "块 %r CRC 校验失败" % (bytes(ctype),))

        if ctype == _IEND:
            return width, height
        pos = end + 4


class PngLoader:
    """PNG 尺寸加载器。

    只解析签名与各块头（含 CRC），不缓存输入数据；状态仅在成功时写入，
    失败后对象可立即复用。
    """

    __slots__ = ("_width", "_height")

    def __init__(self):
        self._width = 0
        self._height = 0

    def load(self, data):
        """加载 PNG，返回 (width, height)。"""
        try:
            view = memoryview(data)
        except TypeError:
            raise PngFormatError(
                PNG_ERR_INVALID, "不支持的数据类型: %s" % type(data).__name__
            ) from None
        if view.format != "B" or view.ndim != 1:
            try:
                view = view.cast("B")
            except (TypeError, ValueError):
                raise PngFormatError(PNG_ERR_INVALID, "不支持的数据布局") from None
        if not view.contiguous:
            # 步长视图无法直接做 CRC；只在调用方传入非连续缓冲时复制一次。
            view = memoryview(view.tobytes())

        failure = None
        try:
            try:
                size = _parse(view)
            except PngError as exc:
                failure = (exc.code, exc.message)
                del exc
        finally:
            # 在 view 之外重建异常：新异常的 traceback 只包含本帧，且此时 view
            # 已 release，调用方即使保留异常对象也不会钉住输入数据。
            view.release()
            del view

        if failure is not None:
            code, message = failure
            # 异常对象会带走本帧的 traceback；清掉参数绑定，避免调用方保留
            # 异常时连带把整个输入缓冲钉在内存里。
            data = None
            raise _ERROR_TYPES[code](code, message) from None

        self._width, self._height = size
        return size

