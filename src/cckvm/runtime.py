"""Async TCP server and client for the Barrier 1.6 command subset."""
import asyncio
import logging
import struct
import time
from dataclasses import dataclass, field

from .layout import Layout, OPPOSITE, entry_point
from .platform import MODIFIER_KEYS, key_id, native_key
from .protocol import (
    ClipboardReceiver, ProtocolError, VERSION, clipboard_frames, command,
    hello, info, packet, parse_hello, parse_info, read_packet,
)

LOG = logging.getLogger("cckvm")


@dataclass
class Peer:
    name: str
    writer: asyncio.StreamWriter
    size: tuple[int, int] = (0, 0)
    origin: tuple[int, int] = (0, 0)
    position: tuple[int, int] = (0, 0)
    sequence: int = 0
    last_reply: float = field(default_factory=time.monotonic)
    clipboard: ClipboardReceiver = field(default_factory=ClipboardReceiver)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def send(self, data: bytes) -> None:
        async with self.lock:
            self.writer.write(packet(data))
            await self.writer.drain()


class KVMServer:
    def __init__(self, layout: Layout, desktop, host: str = "0.0.0.0", port: int = 24800):
        self.layout, self.desktop, self.host, self.port = layout, desktop, host, port
        self.peers: dict[str, Peer] = {}
        self.active = layout.primary
        self.position = desktop.position()
        self.primary_size = desktop.size()
        self.sequence = 0
        self.server = None
        self.tasks: list[asyncio.Task] = []
        self.events: asyncio.Queue = asyncio.Queue()
        self._clipboard = desktop.clipboard()
        self._last_local = self.position
        self._handoff_guard_until = 0.0
        self._keys_down: set[int] = set()
        self._modifiers = 0

    async def start(self, capture: bool = True) -> None:
        self.server = await asyncio.start_server(self._accept, self.host, self.port)
        LOG.info("listening on %s", self.server.sockets[0].getsockname())
        if capture:
            loop = asyncio.get_running_loop()
            self.desktop.capture(lambda *event: loop.call_soon_threadsafe(self.events.put_nowait, event))
            self.tasks.append(asyncio.create_task(self._events()))
        self.tasks.extend([asyncio.create_task(self._keepalive()), asyncio.create_task(self._clipboards())])

    async def close(self) -> None:
        if self.server:
            self.server.close()
        for peer in list(self.peers.values()):
            try:
                await peer.send(b"CBYE")
            except (ConnectionError, OSError):
                pass
            peer.writer.close()
        if self.server:
            await self.server.wait_closed()
        self.desktop.stop()
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)

    async def _accept(self, reader, writer) -> None:
        peer = None
        try:
            writer.write(packet(hello()))
            await writer.drain()
            reply = await asyncio.wait_for(read_packet(reader), 5)
            major, minor, name = parse_hello(reply, True)
            if major != VERSION[0] or minor != VERSION[1]:
                writer.write(packet(command("EICV", "HH", *VERSION)))
                await writer.drain()
                return
            if name not in self.layout.screens or name == self.layout.primary:
                writer.write(packet(b"EUNK"))
                await writer.drain()
                return
            if name in self.peers:
                writer.write(packet(b"EBSY"))
                await writer.drain()
                return
            peer = Peer(name, writer)
            self.peers[name] = peer
            LOG.info("%s connected, protocol %d.%d", name, major, minor)
            await peer.send(b"QINF")
            await peer.send(b"CROP")
            while True:
                data = await read_packet(reader)
                await self._from_peer(peer, data)
        except (asyncio.IncompleteReadError, ConnectionError, OSError, ProtocolError, asyncio.TimeoutError) as exc:
            LOG.info("client disconnected: %s", exc)
        finally:
            if peer and self.peers.get(peer.name) is peer:
                del self.peers[peer.name]
                if self.active == peer.name:
                    self.active = self.layout.primary
                    self.desktop.capture(self.desktop._emit, remote=False) if self.desktop._emit else None
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass

    async def _from_peer(self, peer: Peer, data: bytes) -> None:
        code = data[:4]
        if code == b"DINF":
            x, y, w, h, mx, my = parse_info(data)
            peer.origin, peer.size, peer.position = (x, y), (w, h), (mx, my)
            LOG.info("%s screen %dx%d at %d,%d", peer.name, w, h, x, y)
            await peer.send(b"CIAK")
        elif code == b"CALV" and len(data) == 4:
            peer.last_reply = time.monotonic()
        elif code == b"CNOP" and len(data) == 4:
            pass
        elif code == b"CCLP" and len(data) == 9:
            await self._broadcast(data, exclude=peer.name)
        elif code == b"DCLP":
            value = peer.clipboard.feed(data)
            if value is not None:
                self.desktop.set_clipboard(value)
                self._clipboard = value
                await self._broadcast(command("CCLP", "BI", 0, 0), exclude=peer.name)
                for frame in clipboard_frames(value, 0):
                    await self._broadcast(frame, exclude=peer.name)
        else:
            raise ProtocolError(f"unexpected client command {code!r}")

    async def _broadcast(self, data: bytes, exclude: str | None = None) -> None:
        for peer in list(self.peers.values()):
            if peer.name != exclude:
                await peer.send(data)

    async def _keepalive(self) -> None:
        while True:
            await asyncio.sleep(3)
            for peer in list(self.peers.values()):
                if time.monotonic() - peer.last_reply > 9:
                    LOG.warning("%s timed out", peer.name)
                    peer.writer.close()
                else:
                    try:
                        await peer.send(b"CALV")
                    except (ConnectionError, OSError):
                        peer.writer.close()

    async def _clipboards(self) -> None:
        while True:
            await asyncio.sleep(0.5)
            if self.active != self.layout.primary:
                continue
            value = self.desktop.clipboard()
            if value != self._clipboard:
                self._clipboard = value
                await self._broadcast(command("CCLP", "BI", 0, 0))
                for frame in clipboard_frames(value, 0):
                    await self._broadcast(frame)

    async def _events(self) -> None:
        while True:
            kind, *args = await self.events.get()
            if kind == "move":
                await self.mouse_move(*args)
            elif kind == "move_relative":
                await self.mouse_delta(*args)
            elif kind == "key":
                key, pressed = args
                code = key_id(key)
                if code is None or code > 0xFFFF:
                    continue
                bit = MODIFIER_KEYS.get(code, 0)
                if bit:
                    self._modifiers = (self._modifiers | bit) if pressed else (self._modifiers & ~bit)
                if pressed and code == 0xEF1B and self._modifiers & 0x6 == 0x6 and self.active != self.layout.primary:
                    await self._return_to_primary()
                    continue
                if self.active in self.peers:
                    peer = self.peers[self.active]
                    button = getattr(key, "vk", None) or code
                    if pressed and button in self._keys_down:
                        await peer.send(command("DKRP", "HHHH", code, self._modifiers, 1, button & 0xFFFF))
                    else:
                        await peer.send(command("DKDN" if pressed else "DKUP", "HHH", code, self._modifiers, button & 0xFFFF))
                    (self._keys_down.add if pressed else self._keys_down.discard)(button)
            elif self.active in self.peers:
                peer = self.peers[self.active]
                if kind == "button":
                    button, pressed = args
                    button_id = {"left": 1, "middle": 2, "right": 3}.get(button)
                    if button_id:
                        await peer.send(command("DMDN" if pressed else "DMUP", "B", button_id))
                elif kind == "wheel":
                    dx, dy = args
                    await peer.send(command("DMWM", "hh", 120 * dx, 120 * dy))

    def _size(self, name: str) -> tuple[int, int]:
        return self.primary_size if name == self.layout.primary else self.peers[name].size

    def _discard_pending_motion(self) -> None:
        retained = []
        while True:
            try:
                event = self.events.get_nowait()
            except asyncio.QueueEmpty:
                break
            if event[0] not in ("move", "move_relative"):
                retained.append(event)
        for event in retained:
            self.events.put_nowait(event)

    async def _return_to_primary(self) -> None:
        peer = self.peers.get(self.active)
        if peer:
            await peer.send(b"COUT")
        self.active = self.layout.primary
        self.position = (self.primary_size[0] // 2, self.primary_size[1] // 2)
        self._keys_down.clear()
        self._modifiers = 0
        self._handoff_guard_until = time.monotonic() + 0.25
        self._discard_pending_motion()
        self.desktop.move(*self.position)
        if self.desktop._emit:
            self.desktop.capture(self.desktop._emit, remote=False)
        LOG.info("returned control to primary via emergency shortcut")

    async def mouse_move(self, x: int, y: int) -> None:
        if self.active == self.layout.primary:
            self.position = (max(0, min(self.primary_size[0] - 1, x)), max(0, min(self.primary_size[1] - 1, y)))
            direction = ("left" if x <= 0 else "right" if x >= self.primary_size[0] - 1 else
                         "top" if y <= 0 else "bottom" if y >= self.primary_size[1] - 1 else None)
        else:
            cx, cy = self.primary_size[0] // 2, self.primary_size[1] // 2
            await self.mouse_delta(x - cx, y - cy)
            return
        if direction and time.monotonic() >= self._handoff_guard_until:
            await self._handoff(direction)

    async def mouse_delta(self, dx: int, dy: int) -> None:
        if self.active == self.layout.primary or (dx == 0 and dy == 0):
            return
        px, py = self.position
        size = self._size(self.active)
        self.position = (max(0, min(size[0] - 1, px + dx)), max(0, min(size[1] - 1, py + dy)))
        await self.peers[self.active].send(command("DMRM", "hh", max(-32768, min(32767, dx)), max(-32768, min(32767, dy))))
        direction = ("left" if px + dx <= 0 else "right" if px + dx >= size[0] - 1 else
                     "top" if py + dy <= 0 else "bottom" if py + dy >= size[1] - 1 else None)
        if direction and time.monotonic() >= self._handoff_guard_until:
            await self._handoff(direction)

    async def _handoff(self, direction: str) -> None:
        target = self.layout.neighbor(self.active, direction)
        if not target or (target != self.layout.primary and (target not in self.peers or self.peers[target].size == (0, 0))):
            return
        old = self.active
        position = entry_point(direction, *self.position, self._size(old), self._size(target))
        if old in self.peers:
            await self.peers[old].send(b"COUT")
        self.active, self.position = target, position
        self._handoff_guard_until = time.monotonic() + 0.25
        self._discard_pending_motion()
        LOG.info("handoff %s -> %s at %s", old, target, position)
        if target in self.peers:
            peer = self.peers[target]
            self.sequence += 1
            peer.sequence = self.sequence
            await peer.send(command("CINN", "hhIH", position[0] + peer.origin[0], position[1] + peer.origin[1], self.sequence, self._modifiers))
            center = (self.primary_size[0] // 2, self.primary_size[1] // 2)
            self.desktop.move(*center)
        else:
            self.desktop.move(*position)
        if self.desktop._emit:
            self.desktop.capture(self.desktop._emit, remote=target != self.layout.primary)


class KVMClient:
    def __init__(self, name: str, desktop, host: str, port: int = 24800):
        self.name, self.desktop, self.host, self.port = name, desktop, host, port
        self.sequence = 0
        self.clipboard = ClipboardReceiver()
        self._clipboard = desktop.clipboard()
        self._keys: dict[int, object] = {}
        self._active = False

    async def run(self) -> None:
        reader, writer = await asyncio.open_connection(self.host, self.port)
        tasks = []
        try:
            major, minor, _ = parse_hello(await read_packet(reader), False)
            if major != VERSION[0] or minor < 1:
                raise ProtocolError("incompatible server protocol")
            writer.write(packet(hello(self.name)))
            await writer.drain()
            tasks.append(asyncio.create_task(self._clipboards(writer)))
            while True:
                data = await read_packet(reader)
                if data == b"CBYE":
                    return
                await self._receive(data, writer)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            writer.close()
            await writer.wait_closed()

    async def _receive(self, data: bytes, writer) -> None:
        code = data[:4]
        if code == b"QINF":
            w, h = self.desktop.size()
            x, y = self.desktop.position()
            writer.write(packet(info(0, 0, w, h, x, y)))
        elif code in (b"CIAK", b"CROP", b"CNOP"):
            pass
        elif code == b"DSOP":
            if len(data) < 8 or (len(data) - 8) % 4:
                raise ProtocolError("invalid options packet")
            count = struct.unpack_from(">I", data, 4)[0]
            if count != (len(data) - 8) // 4:
                raise ProtocolError("invalid option count")
        elif code == b"CALV":
            writer.write(packet(b"CALV"))
        elif code == b"CINN" and len(data) == 14:
            x, y, self.sequence, _mask = struct.unpack(">hhIH", data[4:])
            self._active = True
            self.desktop.move(x, y)
        elif code == b"COUT":
            self._active = False
            for key in self._keys.values():
                self.desktop.inject_key(key, False)
            self._keys.clear()
        elif code in (b"DMMV", b"DMRM") and len(data) == 8:
            x, y = struct.unpack(">hh", data[4:])
            if code == b"DMRM":
                px, py = self.desktop.position()
                x, y = px + x, py + y
            self.desktop.move(x, y)
        elif code in (b"DMDN", b"DMUP") and len(data) == 5:
            self.desktop.inject_button(data[4], code == b"DMDN")
        elif code == b"DMWM" and len(data) == 8:
            self.desktop.inject_wheel(*struct.unpack(">hh", data[4:]))
        elif code in (b"DKDN", b"DKUP", b"DKRP"):
            expected = 12 if code == b"DKRP" else 10
            if len(data) != expected:
                raise ProtocolError("invalid key packet")
            values = struct.unpack(">" + ("HHHH" if code == b"DKRP" else "HHH"), data[4:])
            key = native_key(values[0], self.desktop.keyboard)
            button = values[-1]
            if key is not None:
                if code == b"DKDN":
                    self._keys[button] = key
                    self.desktop.inject_key(key, True)
                elif code == b"DKUP":
                    self.desktop.inject_key(self._keys.pop(button, key), False)
                else:
                    for _ in range(min(values[2], 100)):
                        self.desktop.inject_key(key, True)
                        self.desktop.inject_key(key, False)
        elif code == b"CCLP":
            pass
        elif code == b"DCLP":
            value = self.clipboard.feed(data)
            if value is not None:
                self.desktop.set_clipboard(value)
                self._clipboard = value
        elif code == b"CSEC" and len(data) == 5:
            LOG.info("primary screensaver %s", "active" if data[4] else "inactive")
        elif code in (b"EICV", b"EUNK", b"EBSY", b"EBAD"):
            raise ProtocolError(f"server rejected connection: {code.decode()}")
        else:
            raise ProtocolError(f"unsupported server command: {code!r}")
        await writer.drain()

    async def _clipboards(self, writer) -> None:
        while True:
            await asyncio.sleep(0.5)
            value = self.desktop.clipboard()
            if value != self._clipboard:
                self._clipboard = value
                writer.write(packet(command("CCLP", "BI", 0, self.sequence)))
                for frame in clipboard_frames(value, self.sequence):
                    writer.write(packet(frame))
                await writer.drain()
