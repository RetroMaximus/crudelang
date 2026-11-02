"""PNG icon loading for the tool palette.

Icons live in ``assets/icons/<name>.png`` and go through the renderer's
existing PNG texture cache, so a name is loaded at most once. A missing
file is remembered and reported as ``None`` — callers fall back to a text
label instead of raising.
"""
from __future__ import annotations

from pathlib import Path

ICON_DIR = Path(__file__).resolve().parent.parent / "assets" / "icons"


class IconStore:
    def __init__(self, renderer, icon_dir: Path | str | None = None) -> None:
        self.renderer = renderer
        self.icon_dir = Path(icon_dir) if icon_dir is not None else ICON_DIR
        self._cache: dict[str, tuple | None] = {}

    def path_for(self, name: str) -> Path:
        return self.icon_dir / f"{name}.png"

    def get(self, name: str) -> tuple[int, int, int] | None:
        """(gl_texture, width, height) or None when the PNG is absent."""
        if name in self._cache:
            return self._cache[name]
        path = self.path_for(name)
        result = None
        if path.is_file() and self.renderer is not None:
            result = self.renderer.load_texture(str(path))
        self._cache[name] = result
        return result

    def size(self, name: str) -> tuple[int, int]:
        result = self.get(name)
        return (result[1], result[2]) if result else (0, 0)

    def available(self, name: str) -> bool:
        return self.get(name) is not None

    def names(self) -> list[str]:
        if not self.icon_dir.is_dir():
            return []
        return sorted(p.stem for p in self.icon_dir.glob("*.png"))

    def clear(self) -> None:
        """Forget which files were missing (e.g. after generating icons)."""
        self._cache.clear()
