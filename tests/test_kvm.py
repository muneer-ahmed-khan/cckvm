import asyncio
import json
import struct
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from cckvm.layout import Layout, entry_point
from cckvm.platform import key_id
from cckvm.protocol import ClipboardReceiver, ProtocolError, clipboard_frames, hello, packet, parse_hello, read_packet
from cckvm.runtime import KVMClient, KVMServer


class FakeDesktop:
    def __init__(self):
        self.pos = (50, 50)
        self.clip = ""
        self.moves = []
        self.keys = []
        self.buttons = []
        self.wheels = []
        self.keyboard = SimpleNamespace(Key=SimpleNamespace(), KeyCode=SimpleNamespace(from_char=lambda x: x))
        self._emit = None

    def size(self): return (100, 100)
    def position(self): return self.pos
    def move(self, x, y): self.pos = (x, y); self.moves.append(self.pos)
    def clipboard(self): return self.clip
    def set_clipboard(self, value): self.clip = value
    def capture(self, emit, remote=False): self._emit = emit
    def stop(self): pass
    def inject_key(self, key, pressed): self.keys.append((key, pressed))
    def inject_button(self, button, pressed): self.buttons.append((button, pressed))
    def inject_wheel(self, x, y): self.wheels.append((x, y))


def layout():
    return Layout("main", {"main": {"right": "side"}, "side": {"left": "main", "bottom": "third"}, "third": {"top": "side"}})


class ProtocolTests(unittest.TestCase):
    def test_hello_and_clipboard(self):
        self.assertEqual(parse_hello(hello("screen"), True), (1, 6, "screen"))
        receiver = ClipboardReceiver()
        values = [receiver.feed(frame) for frame in clipboard_frames("hello 🌍", 7)]
        self.assertEqual(values, [None, None, "hello 🌍"])

    def test_clipboard_size_limit(self):
        with self.assertRaises(ProtocolError):
            clipboard_frames("x" * 32769, 0)

    def test_layout_validation(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.json"
            path.write_text(json.dumps({"primary": "a", "screens": {"a": {"right": "missing"}}}))
            with self.assertRaisesRegex(ValueError, "unknown"):
                Layout.load(path)
        self.assertEqual(layout().neighbor("side", "bottom"), "third")
        self.assertEqual(entry_point("right", 99, 49, (100, 100), (200, 80)), (1, 39))

    def test_special_key_uses_barrier_key_id(self):
        self.assertEqual(key_id(SimpleNamespace(name="shift", char=None)), 0xEFE1)
        self.assertEqual(key_id(SimpleNamespace(name="left", char=None)), 0xEF51)


class NetworkTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.desktop = FakeDesktop()
        self.server = KVMServer(layout(), self.desktop, "127.0.0.1", 0)
        await self.server.start(capture=False)
        self.port = self.server.server.sockets[0].getsockname()[1]

    async def asyncTearDown(self):
        await self.server.close()

    async def connect(self, name="side", version=(1, 6)):
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        self.assertEqual(parse_hello(await read_packet(reader), False), (1, 6, None))
        writer.write(packet(b"Barrier" + struct.pack(">HH", *version) + struct.pack(">I", len(name)) + name.encode()))
        await writer.drain()
        return reader, writer

    async def test_handshake_info_and_handoff(self):
        reader, writer = await self.connect()
        self.assertEqual(await read_packet(reader), b"QINF")
        self.assertEqual(await read_packet(reader), b"CROP")
        writer.write(packet(b"DINF" + struct.pack(">hhhhhhh", 0, 0, 200, 80, 0, 20, 20)))
        await writer.drain()
        self.assertEqual(await read_packet(reader), b"CIAK")
        await self.server.mouse_move(99, 40)
        enter = await read_packet(reader)
        self.assertEqual(enter[:4], b"CINN")
        self.assertEqual(struct.unpack(">hhIH", enter[4:])[:2], (1, 32))
        self.assertEqual(self.server.active, "side")
        await self.server.mouse_move(51, 50)
        self.assertEqual((await read_packet(reader))[:4], b"DMRM")
        event_task = asyncio.create_task(self.server._events())
        self.server.tasks.append(event_task)
        await self.server.events.put(("key", SimpleNamespace(name="shift", char=None, vk=56), True))
        self.assertEqual(await read_packet(reader), b"DKDN" + struct.pack(">HHH", 0xEFE1, 1, 56))
        await self.server.events.put(("key", SimpleNamespace(name=None, char="A", vk=0), True))
        self.assertEqual(await read_packet(reader), b"DKDN" + struct.pack(">HHH", 65, 1, 65))
        await self.server.events.put(("button", "left", True))
        self.assertEqual(await read_packet(reader), b"DMDN\x01")
        await self.server.events.put(("wheel", 0, 1))
        self.assertEqual(await read_packet(reader), b"DMWM" + struct.pack(">hh", 0, 120))
        writer.close()
        await writer.wait_closed()

    async def test_reject_bad_version_and_unknown(self):
        reader, writer = await self.connect(version=(9, 0))
        self.assertEqual(await read_packet(reader), b"EICV\x00\x01\x00\x06")
        self.assertEqual(await reader.read(), b"")
        writer.close()
        reader, writer = await self.connect("unknown")
        self.assertEqual(await read_packet(reader), b"EUNK")
        writer.close()

    async def test_own_client(self):
        client_desktop = FakeDesktop()
        task = asyncio.create_task(KVMClient("side", client_desktop, "127.0.0.1", self.port).run())
        for _ in range(50):
            if "side" in self.server.peers and self.server.peers["side"].size == (100, 100):
                break
            await asyncio.sleep(0.01)
        self.assertEqual(self.server.peers["side"].size, (100, 100))
        await self.server.mouse_move(99, 50)
        await asyncio.sleep(0.05)
        self.assertEqual(client_desktop.position(), (1, 50))
        await self.server.peers["side"].send(b"DMDN\x01")
        await self.server.peers["side"].send(b"DKDN" + struct.pack(">HHH", ord("a"), 0, 1))
        await self.server.peers["side"].send(b"DKUP" + struct.pack(">HHH", ord("a"), 0, 1))
        await asyncio.sleep(0.05)
        self.assertEqual(client_desktop.buttons, [(1, True)])
        self.assertEqual(client_desktop.keys, [("a", True), ("a", False)])
        for frame in clipboard_frames("shared", 0):
            await self.server.peers["side"].send(frame)
        await asyncio.sleep(0.05)
        self.assertEqual(client_desktop.clipboard(), "shared")
        await self.server.close()
        await asyncio.wait_for(task, 1)

    async def test_three_screen_handoff(self):
        first_reader, first_writer = await self.connect("side")
        second_reader, second_writer = await self.connect("third")
        for reader, writer in ((first_reader, first_writer), (second_reader, second_writer)):
            self.assertEqual(await read_packet(reader), b"QINF")
            self.assertEqual(await read_packet(reader), b"CROP")
            writer.write(packet(b"DINF" + struct.pack(">hhhhhhh", 0, 0, 100, 100, 0, 50, 50)))
            await writer.drain()
            self.assertEqual(await read_packet(reader), b"CIAK")
        await self.server.mouse_move(99, 50)
        self.assertEqual((await read_packet(first_reader))[:4], b"CINN")
        self.server.position = (50, 99)
        await self.server._handoff("bottom")
        self.assertEqual(await read_packet(first_reader), b"COUT")
        self.assertEqual((await read_packet(second_reader))[:4], b"CINN")
        self.assertEqual(self.server.active, "third")
        first_writer.close()
        second_writer.close()
        await first_writer.wait_closed()
        await second_writer.wait_closed()


if __name__ == "__main__":
    unittest.main()
