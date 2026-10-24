"""Validate named screens and directional links."""
import json
from dataclasses import dataclass
from pathlib import Path

DIRECTIONS = {"left", "right", "top", "bottom"}
OPPOSITE = {"left": "right", "right": "left", "top": "bottom", "bottom": "top"}


@dataclass(frozen=True)
class Layout:
    primary: str
    screens: dict[str, dict[str, str]]

    @classmethod
    def load(cls, path: str | Path) -> "Layout":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or not isinstance(raw.get("screens"), dict):
            raise ValueError("configuration requires a screens object")
        primary = raw.get("primary")
        screens = raw["screens"]
        if not isinstance(primary, str) or primary not in screens:
            raise ValueError("primary must name a configured screen")
        for name, links in screens.items():
            if not isinstance(name, str) or not name or not isinstance(links, dict):
                raise ValueError("each screen needs a name and link object")
            for direction, neighbor in links.items():
                if direction not in DIRECTIONS:
                    raise ValueError(f"{name}: unknown direction {direction}")
                if neighbor not in screens or neighbor == name:
                    raise ValueError(f"{name}.{direction}: unknown or self-referencing screen {neighbor}")
        return cls(primary, screens)

    def neighbor(self, screen: str, direction: str) -> str | None:
        return self.screens[screen].get(direction)


def entry_point(direction: str, x: int, y: int, old: tuple[int, int], new: tuple[int, int]) -> tuple[int, int]:
    width, height = new
    if direction in ("left", "right"):
        mapped = min(height - 1, max(0, round(y * (height - 1) / max(1, old[1] - 1))))
        return (width - 2 if direction == "left" else 1, mapped)
    mapped = min(width - 1, max(0, round(x * (width - 1) / max(1, old[0] - 1))))
    return (mapped, height - 2 if direction == "top" else 1)
