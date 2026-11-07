"""Fixtures for the project stage: manifests as data and as files.

Everything here validates a *declaration*. No project is loaded, no device is
opened and no library is imported, so these tests run anywhere.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import pytest

from crude.errors import ManifestError
from crude.project import MANIFEST_NAME, ResourceTable, build_manifest, load_manifest


def example_manifest() -> Dict[str, Any]:
    """The manifest from the plan, in full - the shape every test starts from.

    A function, not a fixture, because tests mutate it and compare the result:
    sharing one dict between tests would make a failure depend on the order.
    """
    return {
        "$schema": "crude/manifest@1",
        "id": "fm-synth",
        "name": "FM Synth",
        "entry": "main.crude",
        "runtime": {"mode": "window", "width": 1024, "height": 640,
                    "target_fps": 60},
        "ui": {"theme": "dark", "icons": "generated", "font": "assets/font/ui.ttf"},
        "audio": {"backend": "sounddevice", "sample_rate": 44100, "block_size": 64,
                  "in_channels": 2, "out_channels": 2, "optional": False},
        "pd": {
            "search_paths": ["pd"],
            "build_dir": ".crude_build/pd",
            "patches": [
                {"id": "drums", "file": "pd/drums.pd"},
                {"id": "synth", "authored": {"width": 900, "height": 600,
                                             "preload": True}},
            ],
        },
        "serial": [{"id": "arduino", "port": "COM3", "baud": 115200,
                    "codec": "line", "optional": True}],
        "resources": {
            "images": ["assets/knob_atlas.png"],
            "sounds": ["assets/metrotick.wav"],
            "arrays": [{"id": "wave", "file": "assets/array.txt", "format": "text"}],
            "presets": ["presets/*.json"],
        },
        "loading": {"progress": "auto"},
    }


@pytest.fixture
def data() -> Dict[str, Any]:
    """A fresh copy of the full example manifest."""
    return example_manifest()


@pytest.fixture
def make(root: Path):
    """Build a manifest from a dict, with the pointers reported against nothing."""
    def build(overrides: Dict[str, Any] = None, source: str = ""):
        merged = example_manifest()
        for key, value in (overrides or {}).items():
            if value is None:
                merged.pop(key, None)
            else:
                merged[key] = value
        return build_manifest(merged, root=str(root), source=source)

    return build


@pytest.fixture
def make_table(root: Path):
    """A resource table from a minimal manifest, so tests say only what they use."""
    def build(resources: Dict[str, Any] = None, extra: Dict[str, Any] = None):
        payload: Dict[str, Any] = {"id": "x"}
        if resources is not None:
            payload["resources"] = resources
        payload.update(extra or {})
        return ResourceTable(build_manifest(payload, root=str(root)))

    return build


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """A project directory that exists, for path arithmetic."""
    project = tmp_path / "proj"
    project.mkdir()
    return project


@pytest.fixture
def project(root: Path):
    """``(directory, writer)``: write files under the project and read manifests."""
    class Writer:
        directory = root

        def manifest(self, payload: Dict[str, Any] = None, *, raw: str = None,
                     name: str = MANIFEST_NAME) -> Path:
            """Write a manifest into the project, JSON unless ``raw`` is given."""
            target = root / name
            text = raw if raw is not None else json.dumps(
                payload if payload is not None else example_manifest(), indent=2
            )
            target.write_text(text, encoding="utf-8")
            return target

        def file(self, relative: str, text: str = "") -> Path:
            """Write a resource file, making its parent directories."""
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
            return target

        def load(self, path=None):
            return load_manifest(str(path or root))

    return Writer()


@pytest.fixture
def error():
    """Assert a manifest is refused and hand back the error, for its pointer."""
    def check(payload: Dict[str, Any], contains: str = None, pointer: str = None,
              source: str = ""):
        with pytest.raises(ManifestError) as caught:
            build_manifest(payload, root="C:/project", source=source)
        message = str(caught.value)
        if contains:
            assert contains in message, message
        if pointer:
            assert caught.value.pointer == pointer, caught.value.pointer
        return caught.value

    return check


@pytest.fixture
def bad(error):
    """Refuse the plan's example manifest after one deliberate edit."""
    from copy import deepcopy

    def broken(path, value, contains: str = None, pointer: str = None):
        payload = deepcopy(example_manifest())
        parts = path.split(".")
        node = payload
        for part in parts[:-1]:
            node = node[int(part)] if part.isdigit() else node[part]
        if value is _REMOVE:
            node.pop(parts[-1])
        else:
            node[parts[-1]] = value
        return error(payload, contains, pointer)

    return broken


class _Remove:
    """Sentinel for "delete this key" in the :func:`broken` fixture."""


_REMOVE = _Remove()
