"""``crude.manifest.json``: everything a project declares before it runs.

The manifest is the only place a project says what it needs - which script is
the entry, what the window looks like, which audio stream to open, which patches
to load, which resources to find - so it is validated *before* anything is
loaded, and the validation is strict on purpose:

* **unknown keys are errors**. A silently ignored typo ("target_flps") is a
  project that does not do what it says, and there is no way to tell it apart
  from an unsupported version;
* **every error carries a JSON pointer** (``pd.patches[1].authored.width``), so
  the message says where to look in a file that is often a hundred lines long;
* **paths are relative and contained**. A manifest that can reach outside its own
  directory is not a project description any more, so absolute paths, ``..`` and
  ``~`` are rejected where they are parsed rather than where they are opened;
* **the result is frozen dataclasses**. Nothing downstream has to re-check that
  ``runtime.target_fps`` is an integer, and a loader bug cannot corrupt a project
  description on disk by mutating the object read from it.

Two kinds of problem stay deliberately apart: this module judges the *shape* of
the declaration and raises :class:`~crude.errors.ManifestError` (exit 2), while
missing files, absent devices and unreadable patches are the loading pipeline's
business (:class:`~crude.errors.LoadError`, exit 3).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..errors import ManifestError

#: The file name that makes a directory a crude project.
MANIFEST_NAME = "crude.manifest.json"

#: ``$schema`` is "<scheme>@<version>"; a newer version is refused, not guessed at.
SCHEMA_PREFIX = "crude/manifest@"
SCHEMA_VERSION = 1
DEFAULT_SCHEMA = f"{SCHEMA_PREFIX}{SCHEMA_VERSION}"

DEFAULT_ENTRY = "main.crude"
DEFAULT_ID = "project"

#: The three runtime modes, exactly as ``crude run --mode`` spells them.
MODES = ("terminal", "headless", "window")

#: Who reports loading progress; ``auto`` lets the mode decide.
PROGRESS_MODES = ("auto", "console", "json", "splash", "none")

#: Where a manifest may say an audio backend comes from (see :func:`known_backends`).
AUDIO_BACKENDS = ("sounddevice", "pyaudio", "null")

#: A project id also names its build cache, so it has to be filesystem-safe.
_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")

#: Keys a whole manifest may contain; anything else is a validation error.
TOP_LEVEL_KEYS = (
    "$schema", "id", "name", "entry",
    "runtime", "ui", "audio", "pd", "serial", "resources", "loading",
)


# --------------------------------------------------------------------------
# the sections
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class RuntimeConfig:
    """``runtime``: the window and the frame clock."""

    mode: str = "window"
    width: int = 1024
    height: int = 640
    target_fps: int = 60
    vsync: bool = True

    def as_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "width": self.width,
            "height": self.height,
            "target_fps": self.target_fps,
            "vsync": self.vsync,
        }

    @property
    def needs_window(self) -> bool:
        return self.mode == "window"


@dataclass(frozen=True)
class UiConfig:
    """``ui``: how the widgets look, and which art they are allowed to need."""

    theme: str = "dark"
    icons: str = "generated"
    font: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return {"theme": self.theme, "icons": self.icons, "font": self.font}


@dataclass(frozen=True)
class AudioSection:
    """``audio``: the stream to open, and how much it matters if it fails."""

    backend: Optional[str] = None
    sample_rate: int = 44100
    block_size: int = 64
    in_channels: int = 2
    out_channels: int = 2
    input_device: Any = None
    output_device: Any = None
    #: ``optional: true`` means "nice to have": falling back to silence is a
    #: note, not a failure. Absent it, a machine with no device cannot load.
    optional: bool = False
    #: ``false`` still opens no device: it only asks for the metering taps and
    #: the headless tick, which is what a terminal-mode project wants.
    enabled: bool = True

    @property
    def strict(self) -> bool:
        """Does a failure to open the named backend end the load?"""
        return self.enabled and not self.optional

    def config(self) -> Any:
        """The :class:`~crude.audio.base.AudioConfig` this section describes."""
        from ..audio.base import AudioConfig

        return AudioConfig(
            sample_rate=self.sample_rate,
            block_size=self.block_size,
            in_channels=self.in_channels,
            out_channels=self.out_channels,
            input_device=self.input_device,
            output_device=self.output_device,
        )

    def as_dict(self) -> Dict[str, Any]:
        return {
            "backend": self.backend,
            "sample_rate": self.sample_rate,
            "block_size": self.block_size,
            "in_channels": self.in_channels,
            "out_channels": self.out_channels,
            "input_device": self.input_device,
            "output_device": self.output_device,
            "optional": self.optional,
            "enabled": self.enabled,
        }


@dataclass(frozen=True)
class AuthoredPatch:
    """The ``authored`` half of a patch declaration.

    Nothing is on disk yet: the loader builds the document, and phase 6
    (``pd.build``) writes it into the cache so progress reporting covers it.
    """

    width: int = 900
    height: int = 600
    font: Optional[int] = None
    #: open it during loading instead of waiting for the script to say ``PD_LOAD``
    preload: bool = True

    def as_dict(self) -> Dict[str, Any]:
        return {"width": self.width, "height": self.height,
                "font": self.font, "preload": self.preload}


@dataclass(frozen=True)
class PatchDecl:
    """One entry of ``pd.patches``: either a ``file`` or an ``authored`` block."""

    id: str
    file: Optional[str] = None
    authored: Optional[AuthoredPatch] = None
    #: a hand-written patch may preload too; for an authored one the flag lives
    #: inside ``authored`` (that is where the size lives as well)
    preload: bool = False
    optional: bool = False

    @property
    def is_authored(self) -> bool:
        return self.authored is not None

    @property
    def should_preload(self) -> bool:
        if self.authored is not None:
            return self.authored.preload or self.preload
        return self.preload

    def describe(self) -> str:
        kind = f"authored {self.authored.width}x{self.authored.height}" if self.is_authored \
            else self.file
        flags = " preload" if self.should_preload else ""
        flags += " optional" if self.optional else ""
        return f"{self.id} ({kind}{flags})"

    def as_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {"id": self.id}
        if self.file is not None:
            data["file"] = self.file
        if self.authored is not None:
            data["authored"] = self.authored.as_dict()
        if self.preload:
            data["preload"] = self.preload
        if self.optional:
            data["optional"] = self.optional
        return data


@dataclass(frozen=True)
class PdSection:
    """``pd``: where patches live, and where generated ones go."""

    search_paths: Tuple[str, ...] = ("pd",)
    build_dir: str = ".crude_build/pd"
    patches: Tuple[PatchDecl, ...] = ()

    def as_dict(self) -> Dict[str, Any]:
        return {
            "search_paths": list(self.search_paths),
            "build_dir": self.build_dir,
            "patches": [patch.as_dict() for patch in self.patches],
        }


@dataclass(frozen=True)
class SerialDecl:
    """``serial[*]``: one link the project expects to find."""

    id: str
    port: str
    baud: int = 115200
    #: the codec registry is the serial stage's business; the manifest only
    #: insists that a name is given, so adding a codec needs no schema change
    codec: str = "line"
    optional: bool = True

    def describe(self) -> str:
        return f"{self.id} {self.port} {self.baud} {self.codec}"

    def as_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "port": self.port, "baud": self.baud,
                "codec": self.codec, "optional": self.optional}


@dataclass(frozen=True)
class ArrayDecl:
    """One ``resources.arrays`` entry: a file of numbers to seed a Pd array.

    libpd cannot create an array - only a patch can - so ``patch`` names the
    declared patch whose table these numbers fill. With none given the file is
    still parsed and kept for the script, which writes it with
    ``PD_ARRAY_WRITE`` once it knows which patch owns the table.
    """

    id: str
    file: str
    #: ``text`` is a column of numbers; ``pd`` is a Pd ``array save`` file
    format: str = "text"
    patch: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {"id": self.id, "file": self.file,
                                "format": self.format}
        if self.patch:
            data["patch"] = self.patch
        return data


@dataclass(frozen=True)
class Resources:
    """``resources``: the files a project ships, in the four kinds that load."""

    images: Tuple[str, ...] = ()
    sounds: Tuple[str, ...] = ()
    arrays: Tuple[ArrayDecl, ...] = ()
    #: presets are patterns the project carries, and a glob is allowed here
    presets: Tuple[str, ...] = ()

    def as_dict(self) -> Dict[str, Any]:
        return {
            "images": list(self.images),
            "sounds": list(self.sounds),
            "arrays": [array.as_dict() for array in self.arrays],
            "presets": list(self.presets),
        }


@dataclass(frozen=True)
class LoadingConfig:
    """``loading``: who talks to the user while the project comes up."""

    progress: str = "auto"

    def as_dict(self) -> Dict[str, Any]:
        return {"progress": self.progress}


@dataclass(frozen=True)
class Manifest:
    """A whole, validated project description plus the directory it came from."""

    root: str = ""
    id: str = DEFAULT_ID
    name: str = ""
    entry: str = DEFAULT_ENTRY
    schema: str = DEFAULT_SCHEMA
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)
    ui: UiConfig = field(default_factory=UiConfig)
    audio: AudioSection = field(default_factory=AudioSection)
    pd: PdSection = field(default_factory=PdSection)
    serial: Tuple[SerialDecl, ...] = ()
    resources: Resources = field(default_factory=Resources)
    loading: LoadingConfig = field(default_factory=LoadingConfig)

    # -- paths ----------------------------------------------------------
    @property
    def project_dir(self) -> str:
        return os.path.abspath(self.root or ".")

    @property
    def path(self) -> str:
        """The manifest file itself, for messages and for ``crude check``."""
        return os.path.join(self.project_dir, MANIFEST_NAME)

    @property
    def entry_path(self) -> str:
        return self.resolve(self.entry)

    def resolve(self, relative: str) -> str:
        """A manifest path as an absolute one, under this project's root."""
        return os.path.normpath(os.path.join(self.project_dir, relative))

    def search_dirs(self) -> List[str]:
        """Pd's search path: the project root first, then the declared folders."""
        return [self.project_dir] + [self.resolve(part) for part in self.pd.search_paths]

    def build_path(self) -> str:
        return self.resolve(self.pd.build_dir)

    # -- convenience ----------------------------------------------------
    @property
    def display_name(self) -> str:
        return self.name or self.id

    @property
    def mode(self) -> str:
        return self.runtime.mode

    def as_dict(self) -> Dict[str, Any]:
        """The manifest back to JSON-ready data, with the defaults written out."""
        return {
            "$schema": self.schema,
            "id": self.id,
            "name": self.display_name,
            "entry": self.entry,
            "runtime": self.runtime.as_dict(),
            "ui": self.ui.as_dict(),
            "audio": self.audio.as_dict(),
            "pd": self.pd.as_dict(),
            "serial": [link.as_dict() for link in self.serial],
            "resources": self.resources.as_dict(),
            "loading": self.loading.as_dict(),
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.as_dict(), indent=indent, sort_keys=False)

    def __str__(self) -> str:
        return f"Manifest {self.id} ({self.mode}, {len(self.pd.patches)} patch(es))"


# --------------------------------------------------------------------------
# reading one
# --------------------------------------------------------------------------

def manifest_path(path: str) -> str:
    """A project directory *or* a manifest file -> the manifest file."""
    if os.path.isdir(path):
        return os.path.join(path, MANIFEST_NAME)
    return path


def find_project(path: str) -> Tuple[Optional[str], Optional[str]]:
    """``(project directory, manifest file)`` for a path, or ``(directory, None)``.

    Accepts the root itself, a directory holding it, or the manifest file, so the
    CLI can take any of the three without a special case per form. Anything else
    reports the directory it belongs to, which is what an error should name.
    """
    candidate = os.path.abspath(path)
    if os.path.isdir(candidate):
        found = os.path.join(candidate, MANIFEST_NAME)
        return candidate, found if os.path.isfile(found) else None

    parent = os.path.dirname(candidate)
    if os.path.basename(candidate) == MANIFEST_NAME and os.path.isfile(candidate):
        return parent, candidate
    return parent, None


def load_manifest(path: str) -> Manifest:
    """Read and validate the manifest of ``path`` (a directory or the file).

    Raises :class:`ManifestError` - the file missing, unreadable or not JSON is a
    manifest problem, so the CLI exits 2 with the file name instead of 3.
    """
    target = manifest_path(path)
    if os.path.isdir(target) or not os.path.isfile(target):
        raise ManifestError(
            f"there is no {MANIFEST_NAME} in '{os.path.abspath(path)}'; "
            f"is this a crude project?",
            pointer=target,
        )

    try:
        with open(target, "r", encoding="utf-8") as handle:
            text = handle.read()
    except OSError as exc:
        raise ManifestError(f"'{target}' could not be read: {exc}", pointer=target)

    try:
        data = json.loads(text)
    except ValueError as exc:
        line = getattr(exc, "lineno", 0)
        raise ManifestError(
            f"'{target}' is not valid JSON: {exc}" + (f" (line {line})" if line else ""),
            pointer=target,
        )

    root = os.path.dirname(os.path.abspath(target))
    try:
        return build_manifest(data, root=root)
    except ManifestError as exc:
        # The pointer says *where* in the document; the message says *which*
        # document, because a project can hold more than one manifest on the way
        # to the one the user meant.
        raise ManifestError(f"{exc.message} (in '{target}')", exc.pointer) from exc


def build_manifest(data: Any, root: str = "", source: str = "") -> Manifest:
    """Validate ``data`` (the parsed JSON) into a :class:`Manifest`.

    Pointers are always structural (``pd.patches[0].id``), because that is what a
    reader looks for in the file; ``source`` - the manifest path - is only used
    for the failures that have no location inside a document.
    """
    if not isinstance(data, dict):
        raise ManifestError(
            f"a manifest is a JSON object, not {_shape(data)}"
            + (f" in '{source}'" if source else ""), _at(source)
        )

    _no_extra(data, TOP_LEVEL_KEYS, "")

    schema = _schema(data.get("$schema"), "$schema")
    project_id = _text(data.get("id"), DEFAULT_ID, "id", "id")
    if not _ID_PATTERN.fullmatch(project_id):
        raise ManifestError(
            f"id '{project_id}' cannot be used as a directory name; use letters, "
            f"digits, '-', '_' and '.'",
            "id",
        )

    name = _text(data.get("name"), project_id, "name", "name")
    entry = _path(data.get("entry"), DEFAULT_ENTRY, "entry", "entry")
    if not entry.endswith(".crude"):
        raise ManifestError(
            f"entry '{entry}' should be a .crude script", "entry"
        )

    # ``pd`` is read first: an array may only name a patch that is declared.
    pd_section = _pd(data.get("pd"), "pd")

    return Manifest(
        root=os.path.abspath(root or "."),
        id=project_id,
        name=name,
        entry=entry,
        schema=schema,
        runtime=_runtime(data.get("runtime"), "runtime"),
        ui=_ui(data.get("ui"), "ui"),
        audio=_audio(data.get("audio"), "audio"),
        pd=pd_section,
        serial=_serial(data.get("serial"), "serial"),
        resources=_resources(data.get("resources"), "resources",
                             tuple(patch.id for patch in pd_section.patches)),
        loading=_loading(data.get("loading"), "loading"),
    )


# --------------------------------------------------------------------------
# sections
# --------------------------------------------------------------------------

def _runtime(data: Any, pointer: str) -> RuntimeConfig:
    if data is None:
        return RuntimeConfig()
    data = _mapping(data, pointer, "runtime")
    _no_extra(data, ("mode", "width", "height", "target_fps", "vsync"), pointer)

    mode = _text(data.get("mode"), "window", f"{pointer}.mode", "mode", MODES)
    return RuntimeConfig(
        mode=mode,
        width=_whole(data.get("width"), 1024, f"{pointer}.width", "width", minimum=1),
        height=_whole(data.get("height"), 640, f"{pointer}.height", "height", minimum=1),
        target_fps=_whole(data.get("target_fps"), 60, f"{pointer}.target_fps",
                          "target_fps", minimum=1),
        vsync=_flag(data.get("vsync"), True, f"{pointer}.vsync", "vsync"),
    )


def _ui(data: Any, pointer: str) -> UiConfig:
    if data is None:
        return UiConfig()
    data = _mapping(data, pointer, "ui")
    _no_extra(data, ("theme", "icons", "font"), pointer)

    font = data.get("font")
    return UiConfig(
        theme=_text(data.get("theme"), "dark", f"{pointer}.theme", "theme"),
        icons=_text(data.get("icons"), "generated", f"{pointer}.icons", "icons"),
        font=_path(font, None, f"{pointer}.font", "font") if font is not None else None,
    )


def _audio(data: Any, pointer: str) -> AudioSection:
    if data is None:
        return AudioSection()
    data = _mapping(data, pointer, "audio")
    _no_extra(data, ("backend", "sample_rate", "block_size", "in_channels",
                     "out_channels", "input_device", "output_device",
                     "optional", "enabled"), pointer)

    backend = data.get("backend")
    if backend is not None:
        backend = _text(backend, None, f"{pointer}.backend", "backend")
        known = known_backends()
        if known and backend not in known:
            raise ManifestError(
                f"there is no audio backend named '{backend}' "
                f"(known: {', '.join(known)})",
                f"{pointer}.backend",
            )

    return AudioSection(
        backend=backend,
        sample_rate=_whole(data.get("sample_rate"), 44100, f"{pointer}.sample_rate",
                           "sample_rate", minimum=1),
        block_size=_whole(data.get("block_size"), 64, f"{pointer}.block_size",
                          "block_size", minimum=1),
        in_channels=_whole(data.get("in_channels"), 2, f"{pointer}.in_channels",
                           "in_channels", minimum=0),
        out_channels=_whole(data.get("out_channels"), 2, f"{pointer}.out_channels",
                            "out_channels", minimum=0),
        input_device=_device(data.get("input_device"), f"{pointer}.input_device"),
        output_device=_device(data.get("output_device"), f"{pointer}.output_device"),
        optional=_flag(data.get("optional"), False, f"{pointer}.optional", "optional"),
        enabled=_flag(data.get("enabled"), True, f"{pointer}.enabled", "enabled"),
    )


def _pd(data: Any, pointer: str) -> PdSection:
    if data is None:
        return PdSection()
    data = _mapping(data, pointer, "pd")
    _no_extra(data, ("search_paths", "build_dir", "patches"), pointer)

    patches = tuple(
        _patch_decl(item, f"{pointer}.patches[{index}]")
        for index, item in enumerate(_list(data.get("patches"), [],
                                          f"{pointer}.patches", "patches"))
    )
    seen: Dict[str, str] = {}
    for index, patch in enumerate(patches):
        where = f"{pointer}.patches[{index}].id"
        if patch.id in seen:
            raise ManifestError(
                f"patch id '{patch.id}' is declared twice "
                f"(first at pd.patches[{seen[patch.id]}])",
                where,
            )
        seen[patch.id] = str(index)

    return PdSection(
        search_paths=tuple(
            _path(item, None, f"{pointer}.search_paths[{index}]", "search path")
            for index, item in enumerate(
                _list(data.get("search_paths"), ["pd"], f"{pointer}.search_paths",
                      "search_paths")
            )
        ),
        build_dir=_path(data.get("build_dir"), ".crude_build/pd",
                        f"{pointer}.build_dir", "build_dir"),
        patches=patches,
    )


def _patch_decl(data: Any, pointer: str) -> PatchDecl:
    data = _mapping(data, pointer, "a patch")
    _no_extra(data, ("id", "file", "authored", "preload", "optional"), pointer)

    patch_id = _required_text(data.get("id"), f"{pointer}.id",
                              "a patch needs an id the script can name")

    file_value = data.get("file")
    authored_value = data.get("authored")
    if file_value is None and authored_value is None:
        raise ManifestError(
            f"patch '{patch_id}' needs either a 'file' to open or an 'authored' "
            f"block to build", pointer
        )
    if file_value is not None and authored_value is not None:
        raise ManifestError(
            f"patch '{patch_id}' cannot be both a 'file' and 'authored'", pointer
        )

    return PatchDecl(
        id=patch_id,
        file=_path(file_value, None, f"{pointer}.file", "file") if file_value else None,
        authored=_authored(authored_value, f"{pointer}.authored")
        if authored_value is not None else None,
        preload=_flag(data.get("preload"), False, f"{pointer}.preload", "preload"),
        optional=_flag(data.get("optional"), False, f"{pointer}.optional", "optional"),
    )


def _authored(data: Any, pointer: str) -> AuthoredPatch:
    data = _mapping(data, pointer, "authored")
    _no_extra(data, ("width", "height", "font", "preload"), pointer)

    font = data.get("font")
    return AuthoredPatch(
        width=_whole(data.get("width"), 900, f"{pointer}.width", "width", minimum=1),
        height=_whole(data.get("height"), 600, f"{pointer}.height", "height", minimum=1),
        font=_whole(font, None, f"{pointer}.font", "font", minimum=1)
        if font is not None else None,
        preload=_flag(data.get("preload"), True, f"{pointer}.preload", "preload"),
    )


def _serial(data: Any, pointer: str) -> Tuple[SerialDecl, ...]:
    if data is None:
        return ()
    entries = _list(data, [], pointer, "serial")
    return tuple(
        _serial_decl(item, f"{pointer}[{index}]") for index, item in enumerate(entries)
    )


def _serial_decl(data: Any, pointer: str) -> SerialDecl:
    data = _mapping(data, pointer, "a serial link")
    _no_extra(data, ("id", "port", "baud", "codec", "optional"), pointer)

    link_id = _required_text(data.get("id"), f"{pointer}.id",
                             "a serial link needs an id the script can name")
    port = _required_text(
        data.get("port"), f"{pointer}.port",
        f"link '{link_id}' needs a port ('COM3', '/dev/ttyUSB0', 'loop://')")

    return SerialDecl(
        id=link_id,
        port=port,
        baud=_whole(data.get("baud"), 115200, f"{pointer}.baud", "baud", minimum=1),
        codec=_text(data.get("codec"), "line", f"{pointer}.codec", "codec"),
        optional=_flag(data.get("optional"), True, f"{pointer}.optional", "optional"),
    )


def _resources(data: Any, pointer: str,
               patch_ids: Sequence[str] = ()) -> Resources:
    if data is None:
        return Resources()
    data = _mapping(data, pointer, "resources")
    _no_extra(data, ("images", "sounds", "arrays", "presets"), pointer)

    arrays = tuple(
        _array_decl(item, f"{pointer}.arrays[{index}]", patch_ids)
        for index, item in enumerate(_list(data.get("arrays"), [],
                                          f"{pointer}.arrays", "arrays"))
    )
    return Resources(
        images=_paths(data.get("images"), f"{pointer}.images", "images"),
        sounds=_paths(data.get("sounds"), f"{pointer}.sounds", "sounds"),
        arrays=arrays,
        presets=_paths(data.get("presets"), f"{pointer}.presets", "presets",
                       glob_last=True),
    )


def _array_decl(data: Any, pointer: str, patch_ids: Sequence[str] = ()) -> ArrayDecl:
    data = _mapping(data, pointer, "an array")
    _no_extra(data, ("id", "file", "format", "patch"), pointer)

    array_id = _required_text(data.get("id"), f"{pointer}.id",
                              "an array needs a name the script can read")
    file_value = _required_text(
        data.get("file"), f"{pointer}.file",
        f"array '{array_id}' needs a file of numbers to read")

    patch = data.get("patch")
    if patch is not None:
        patch = _required_text(patch, f"{pointer}.patch",
                               f"array '{array_id}' needs a patch id to fill")
        if patch_ids and patch not in patch_ids:
            raise ManifestError(
                f"array '{array_id}' wants patch '{patch}', which pd.patches does "
                f"not declare (declared: {', '.join(patch_ids)})",
                f"{pointer}.patch",
            )

    return ArrayDecl(
        id=array_id,
        file=_path(file_value, None, f"{pointer}.file", "file"),
        format=_text(data.get("format"), "text", f"{pointer}.format", "format",
                     ("text", "pd", "csv")),
        patch=patch,
    )


def _loading(data: Any, pointer: str) -> LoadingConfig:
    if data is None:
        return LoadingConfig()
    data = _mapping(data, pointer, "loading")
    _no_extra(data, ("progress",), pointer)

    return LoadingConfig(
        progress=_text(data.get("progress"), "auto", f"{pointer}.progress",
                       "progress", PROGRESS_MODES),
    )


def known_backends() -> Tuple[str, ...]:
    """The audio names a manifest may use, when the audio package can say.

    Checked here so ``crude check`` can flag a bad backend without opening a
    device. A build where the audio package cannot be imported falls back to the
    statically known list instead of refusing every manifest.
    """
    try:
        from ..audio import backend_names
    except Exception:  # pragma: no cover - audio is a hard dependency
        return AUDIO_BACKENDS
    try:
        return tuple(backend_names())
    except Exception:  # pragma: no cover - a broken registry is not the manifest's fault
        return AUDIO_BACKENDS


# --------------------------------------------------------------------------
# the scalar helpers: one place where "is this legal here?" is decided
# --------------------------------------------------------------------------

def _at(source: str) -> str:
    """The pointer of the document itself, used when there is no key to blame."""
    return source or ""


def _shape(value: Any) -> str:
    """How JSON would describe this value, for a message a scripter understands."""
    return {
        dict: "an object", list: "an array", str: "a string",
        bool: "a boolean", int: "a number", float: "a number",
        type(None): "null",
    }.get(type(value), type(value).__name__)


def _mapping(value: Any, pointer: str, label: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise ManifestError(f"{label} must be a JSON object, not {_shape(value)}",
                            pointer)
    return value


def _list(value: Any, default: Sequence[Any], pointer: str, label: str) -> List[Any]:
    if value is None:
        return list(default)
    if isinstance(value, (list, tuple)):
        return list(value)
    raise ManifestError(f"{label} must be a JSON array, not {_shape(value)}", pointer)


def _no_extra(data: Dict[str, Any], allowed: Sequence[str], pointer: str,
              label: str = None) -> None:
    """Reject a key nobody defined: the point of a schema is to notice typos."""
    allowed_set = set(allowed)
    extra = sorted(key for key in data if key not in allowed_set)
    if not extra:
        return

    where = label or pointer or "the manifest"
    raise ManifestError(
        f"unexpected {'key' if len(extra) == 1 else 'keys'} "
        f"{', '.join(repr(key) for key in extra)} in {where}; "
        f"known here: {', '.join(sorted(allowed_set)) if allowed_set else '(none)'}",
        extra[0] if not pointer else f"{pointer}.{extra[0]}",
    )


def _required_text(value: Any, pointer: str, message: str) -> str:
    """A string that has to be present, with a message that says what it is for.

    "port cannot be empty" is true but unhelpful; "link 'arduino' needs a port
    ('COM3', '/dev/ttyUSB0', 'loop://')" tells the reader what to type.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ManifestError(message, pointer)
    if isinstance(value, bool) or not isinstance(value, str):
        raise ManifestError(
            f"{message.split(' needs ')[0]} needs a string, not {_shape(value)}",
            pointer,
        )
    return value.strip()


def _text(value: Any, default: Optional[str], pointer: str, label: str,
          choices: Sequence[str] = None) -> str:
    if value is None:
        value = default if default is not None else ""
    if isinstance(value, bool) or not isinstance(value, str):
        raise ManifestError(f"{label} must be a string, not {_shape(value)}", pointer)

    text = value.strip()
    if not text:
        if default is None:
            raise ManifestError(f"{label} cannot be empty", pointer)
        return default
    if choices is not None and text not in choices:
        raise ManifestError(
            f"{label} '{text}' must be one of {', '.join(choices)}", pointer
        )
    return text


def _whole(value: Any, default: Optional[int], pointer: str, label: str,
           minimum: int = None) -> Optional[int]:
    if value is None:
        return default
    if isinstance(value, bool):
        raise ManifestError(f"{label} must be a number, not a boolean", pointer)
    if isinstance(value, float) and not value.is_integer():
        raise ManifestError(f"{label} must be a whole number, got {value}", pointer)
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ManifestError(f"{label} must be a number, not {_shape(value)}", pointer)

    if minimum is not None and number < minimum:
        raise ManifestError(f"{label} must be at least {minimum}, got {number}",
                            pointer)
    return number


def _flag(value: Any, default: bool, pointer: str, label: str) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("true", "false"):
        # JSON writers differ: a hand-edited manifest saying "true" means it.
        return value.strip().lower() == "true"
    raise ManifestError(f"{label} must be true or false, not {_shape(value)}",
                        pointer)


def _device(value: Any, pointer: str) -> Any:
    """An audio device selection: an index, or a name that has to be unique."""
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, bool):
        raise ManifestError("a device cannot be true/false; give an index or a name",
                            pointer)
    number = _whole(value, None, pointer, "device")
    return number


def _path(value: Any, default: Optional[str], pointer: str, label: str,
          glob_last: bool = False) -> str:
    """A project-relative path, checked for the three ways out of the project."""
    if value is None:
        return default if default is not None else ""
    if not isinstance(value, str) or isinstance(value, bool):
        raise ManifestError(f"{label} must be a path string, not {_shape(value)}",
                            pointer)

    text = value.strip().replace("\\", "/")
    if not text:
        if default is not None:
            return default
        raise ManifestError(f"{label} cannot be empty", pointer)
    if text.startswith("/") or re.match(r"^[A-Za-z]:", text):
        raise ManifestError(
            f"{label} '{value}' must be relative to the project, not absolute",
            pointer,
        )
    if text.startswith("~"):
        raise ManifestError(f"{label} '{value}' cannot use a home directory",
                            pointer)

    parts = [part for part in text.split("/") if part not in ("", ".")]
    if any(part == ".." for part in parts):
        raise ManifestError(
            f"{label} '{value}' reaches outside the project; keep it inside",
            pointer,
        )
    if glob_last and any("*" in part for part in parts[:-1]):
        raise ManifestError(
            f"{label} '{value}' may only use '*' in the file name", pointer
        )
    elif not glob_last and any("*" in part or "?" in part for part in parts):
        raise ManifestError(f"{label} '{value}' cannot contain wildcards", pointer)

    return "/".join(parts)


def _paths(value: Any, pointer: str, label: str, glob_last: bool = False) -> Tuple[str, ...]:
    entries = _list(value, [], pointer, label)
    return tuple(
        _path(item, None, f"{pointer}[{index}]", f"{label} path", glob_last=glob_last)
        for index, item in enumerate(entries)
    )


def _schema(value: Any, pointer: str) -> str:
    """Check the version tag, and accept its absence as the current version."""
    if value is None:
        return DEFAULT_SCHEMA
    if not isinstance(value, str) or isinstance(value, bool):
        raise ManifestError(f"$schema must be a string like '{DEFAULT_SCHEMA}'",
                            pointer)

    text = value.strip()
    if not text.startswith(SCHEMA_PREFIX):
        raise ManifestError(
            f"$schema '{value}' is not a crude manifest; expected "
            f"'{SCHEMA_PREFIX}<version>'", pointer,
        )

    tail = text[len(SCHEMA_PREFIX):]
    if not tail.strip():
        raise ManifestError(f"$schema '{value}' does not say which version it is",
                            pointer)

    version = _whole(tail, None, pointer, "$schema version")
    if version is None:  # pragma: no cover - _whole refuses a missing value first
        raise ManifestError(f"$schema '{value}' does not say which version it is",
                            pointer)
    if version > SCHEMA_VERSION:
        raise ManifestError(
            f"$schema '{value}' is newer than this runtime understands "
            f"('{DEFAULT_SCHEMA}'); update crude", pointer,
        )
    if version < 1:
        raise ManifestError(f"$schema '{value}' names no manifest version", pointer)
    return text
