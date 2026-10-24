"""Desktop adapter. Import only when running on a graphical desktop."""
import threading


class Desktop:
    def __init__(self):
        try:
            from pynput import keyboard, mouse
            import pyperclip
        except ImportError as exc:
            raise RuntimeError("Install desktop dependencies with: pip install -e .") from exc
        self.keyboard = keyboard
        self.mouse = mouse
        self.pyperclip = pyperclip
        self.pointer = mouse.Controller()
        self.keys = keyboard.Controller()
        self._listeners = []
        self._remote = False
        self._emit = None
        self._lock = threading.Lock()

    def size(self) -> tuple[int, int]:
        import Quartz
        bounds = Quartz.CGDisplayBounds(Quartz.CGMainDisplayID())
        return int(bounds.size.width), int(bounds.size.height)

    def position(self) -> tuple[int, int]:
        return tuple(map(int, self.pointer.position))

    def check_permissions(self, capture: bool) -> None:
        import ApplicationServices
        import Quartz
        missing = []
        if not ApplicationServices.AXIsProcessTrusted():
            missing.append("Accessibility")
        if capture and not Quartz.CGPreflightListenEventAccess():
            missing.append("Input Monitoring")
        if missing:
            raise RuntimeError(
                "macOS permission required: " + ", ".join(missing)
                + ". Grant the terminal app access in System Settings → Privacy & Security, then restart it."
            )

    def move(self, x: int, y: int) -> None:
        self.pointer.position = (x, y)

    def clipboard(self) -> str:
        return self.pyperclip.paste()

    def set_clipboard(self, value: str) -> None:
        self.pyperclip.copy(value)

    def _stop(self) -> None:
        for listener in self._listeners:
            listener.stop()
        self._listeners = []

    def capture(self, emit, remote: bool = False) -> None:
        with self._lock:
            self._stop()
            self._emit = emit
            self._remote = remote
            mouse = self.mouse.Listener(
                on_move=lambda x, y: emit("move", int(x), int(y)),
                on_click=lambda x, y, button, pressed: emit("button", button.name, pressed),
                on_scroll=lambda x, y, dx, dy: emit("wheel", int(dx), int(dy)),
                suppress=remote,
            )
            keyboard = self.keyboard.Listener(
                on_press=lambda key: emit("key", key, True),
                on_release=lambda key: emit("key", key, False),
                suppress=remote,
            )
            self._listeners = [mouse, keyboard]
            for listener in self._listeners:
                listener.start()

    def stop(self) -> None:
        with self._lock:
            self._stop()

    def inject_key(self, key, pressed: bool) -> None:
        (self.keys.press if pressed else self.keys.release)(key)

    def inject_button(self, button: int, pressed: bool) -> None:
        choice = {1: self.mouse.Button.left, 2: self.mouse.Button.middle, 3: self.mouse.Button.right}.get(button)
        if choice:
            (self.pointer.press if pressed else self.pointer.release)(choice)

    def inject_wheel(self, x: int, y: int) -> None:
        self.pointer.scroll(round(x / 120), round(y / 120))


SPECIAL_KEYS = {
    "enter": 0xEF0D, "tab": 0xEF09, "backspace": 0xEF08,
    "esc": 0xEF1B, "space": 0x20, "delete": 0xEFFF,
    "shift": 0xEFE1, "shift_r": 0xEFE2, "ctrl": 0xEFE3,
    "ctrl_r": 0xEFE4, "alt": 0xEFE9, "alt_r": 0xEFEA,
    "cmd": 0xEFEB, "cmd_r": 0xEFEC,
    "up": 0xEF52, "down": 0xEF54, "left": 0xEF51, "right": 0xEF53,
    "home": 0xEF50, "end": 0xEF57, "page_up": 0xEF55, "page_down": 0xEF56,
}
SPECIAL_KEYS.update({f"f{i}": 0xEFBD + i for i in range(1, 13)})
REVERSE_KEYS = {value: key for key, value in SPECIAL_KEYS.items()}

MODIFIER_KEYS = {
    0xEFE1: 1, 0xEFE2: 1,
    0xEFE3: 2, 0xEFE4: 2,
    0xEFE9: 4, 0xEFEA: 4,
    0xEFEB: 16, 0xEFEC: 16,
}


def key_id(key) -> int | None:
    char = getattr(key, "char", None)
    if char and len(char) == 1:
        return ord(char)
    return SPECIAL_KEYS.get(getattr(key, "name", ""))


def native_key(code: int, keyboard):
    if code in REVERSE_KEYS:
        return getattr(keyboard.Key, REVERSE_KEYS[code], None)
    if 32 <= code <= 0x10FFFF:
        try:
            return keyboard.KeyCode.from_char(chr(code))
        except (ValueError, OverflowError):
            return None
    return None
