"""Barrier 1.x length-prefixed packets and command encodings."""
import asyncio
import struct

VERSION = (1, 6)
MAX_PACKET = 4 * 1024 * 1024
MAX_CLIPBOARD = 32 * 1024


class ProtocolError(ValueError):
    pass


def packet(payload: bytes) -> bytes:
    if len(payload) > MAX_PACKET:
        raise ProtocolError("packet too large")
    return struct.pack(">I", len(payload)) + payload


async def read_packet(reader: asyncio.StreamReader) -> bytes:
    size = struct.unpack(">I", await reader.readexactly(4))[0]
    if not 4 <= size <= MAX_PACKET:
        raise ProtocolError(f"invalid packet length: {size}")
    return await reader.readexactly(size)


def string(value: bytes) -> bytes:
    return struct.pack(">I", len(value)) + value


def take_string(data: bytes, offset: int = 0) -> tuple[bytes, int]:
    if len(data) < offset + 4:
        raise ProtocolError("short string length")
    size = struct.unpack_from(">I", data, offset)[0]
    if size > MAX_PACKET or len(data) < offset + 4 + size:
        raise ProtocolError("invalid string length")
    return data[offset + 4:offset + 4 + size], offset + 4 + size


def hello(name: str | None = None) -> bytes:
    body = b"Barrier" + struct.pack(">HH", *VERSION)
    return body if name is None else body + string(name.encode("utf-8"))


def parse_hello(data: bytes, with_name: bool) -> tuple[int, int, str | None]:
    if not data.startswith(b"Barrier") or len(data) < 11:
        raise ProtocolError("invalid hello")
    major, minor = struct.unpack_from(">HH", data, 7)
    if not with_name:
        if len(data) != 11:
            raise ProtocolError("invalid server hello length")
        return major, minor, None
    raw, end = take_string(data, 11)
    if end != len(data) or not raw or len(raw) > 1024:
        raise ProtocolError("invalid screen name")
    return major, minor, raw.decode("utf-8")


def command(code: str, fmt: str = "", *values: int) -> bytes:
    return code.encode("ascii") + (struct.pack(">" + fmt, *values) if fmt else b"")


def info(x: int, y: int, width: int, height: int, mx: int, my: int) -> bytes:
    return command("DINF", "hhhhhhh", x, y, width, height, 0, mx, my)


def parse_info(data: bytes) -> tuple[int, int, int, int, int, int]:
    if len(data) != 18 or data[:4] != b"DINF":
        raise ProtocolError("invalid DINF")
    x, y, w, h, _zone, mx, my = struct.unpack(">hhhhhhh", data[4:])
    if w <= 0 or h <= 0:
        raise ProtocolError("invalid screen size")
    return x, y, w, h, mx, my


def clipboard_frames(text: str, sequence: int) -> list[bytes]:
    # Barrier's clipboard archive: count, then format id, byte length and data.
    raw = text.encode("utf-8")
    if len(raw) > MAX_CLIPBOARD:
        raise ProtocolError("clipboard text too large")
    archive = struct.pack(">III", 1, 0, len(raw)) + raw
    frames = []
    for mark, part in ((1, str(len(archive)).encode()), (2, archive), (3, b"")):
        frames.append(b"DCLP" + struct.pack(">BIB", 0, sequence, mark) + string(part))
    return frames


class ClipboardReceiver:
    def __init__(self):
        self.expected: int | None = None
        self.parts = bytearray()

    def feed(self, data: bytes) -> str | None:
        if len(data) < 14 or data[:4] != b"DCLP":
            raise ProtocolError("invalid clipboard packet")
        clip_id, _seq, mark = struct.unpack(">BIB", data[4:10])
        part, end = take_string(data, 10)
        if clip_id != 0 or end != len(data):
            raise ProtocolError("invalid clipboard data")
        if mark == 1:
            try:
                self.expected = int(part)
            except ValueError as exc:
                raise ProtocolError("invalid clipboard size") from exc
            if self.expected > MAX_CLIPBOARD + 12:
                raise ProtocolError("clipboard too large")
            self.parts.clear()
        elif mark == 2 and self.expected is not None:
            if len(self.parts) + len(part) > MAX_CLIPBOARD + 12:
                raise ProtocolError("clipboard too large")
            self.parts.extend(part)
        elif mark == 3 and self.expected is not None:
            if len(self.parts) != self.expected or len(self.parts) < 12:
                raise ProtocolError("incomplete clipboard")
            count, kind, size = struct.unpack_from(">III", self.parts)
            if count != 1 or kind != 0 or size != len(self.parts) - 12:
                raise ProtocolError("unsupported clipboard format")
            text = self.parts[12:].decode("utf-8")
            self.expected = None
            self.parts.clear()
            return text
        else:
            raise ProtocolError("clipboard chunk out of order")
        return None
