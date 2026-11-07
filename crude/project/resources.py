"""The files a project ships, as one table the loader and the scripts share.

A manifest says *which* files exist; this module says what they are worth:

* every declared resource gets an **id a script can ask for** - the declared path,
  its file name, and its name without the extension - so ``RES("tick")`` and
  ``RES("assets/metrotick.wav")`` reach the same thing, and a name that could mean
  two files says so instead of picking one;
* **array files are parsed here** and nowhere else, because the three formats a
  project carries (a column of numbers, a CSV, a Pd ``array save`` file) are all
  the same list of floats by the time libpd is asked to fill a table;
* **presets are read and written here**, so a pattern a script saves lands in the
  directory the manifest declared rather than in whatever the working directory
  happens to be.

Nothing in this module touches libpd, a device or a window: it is a file table
with arithmetic on it, which is why every test of it runs anywhere.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

from ..errors import ResourceError
from .manifest import ArrayDecl, Manifest

IMAGE = "image"
SOUND = "sound"
ARRAY = "array"
PRESET = "preset"

#: The four kinds a manifest may declare, in the order the pipeline loads them.
KINDS = (IMAGE, SOUND, ARRAY, PRESET)

#: ``crude`` reads a preset file it did not write, but only this version of it.
PRESET_KIND = "crude_preset"
PRESET_VERSION = 1

_COMMENT = re.compile(r"#.*$")
_SEPARATORS = re.compile(r"[,\s]+")


# --------------------------------------------------------------------------
# one declared file
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Resource:
    """One file the project declared, with every name it answers to."""

    id: str
    kind: str
    path: str
    declared: str
    #: the declaration behind an array, so a loader can see the format and the
    #: patch it fills; ``None`` for every other kind
    array: Optional[ArrayDecl] = None

    @property
    def name(self) -> str:
        """The file name without its extension: what a script would call it."""
        return os.path.splitext(os.path.basename(self.path))[0]

    @property
    def exists(self) -> bool:
        return os.path.isfile(self.path)

    def as_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "declared": self.declared,
                "path": self.path, "exists": self.exists}

    def describe(self) -> str:
        where = "" if self.exists else "  (missing)"
        return f"{self.kind:<6} {self.id:<16} {self.declared}{where}"

    def __str__(self) -> str:
        return f"Resource {self.kind}:{self.id} ({self.declared})"


# --------------------------------------------------------------------------
# array files
# --------------------------------------------------------------------------


def parse_numbers(text: str, source: str = "", first_line: int = 1,
                  count: int = 0) -> List[float]:
    """A run of numbers in text, with ``#`` comments and any mix of separators.

    ``first_line`` says which line of the file ``text`` starts on, so a bad token
    is quoted with the number the reader would see in an editor.
    """
    values: List[float] = []

    for offset, raw in enumerate(text.splitlines()):
        line = offset + first_line
        cleaned = _COMMENT.sub("", raw).strip()
        if not cleaned:
            continue

        for token in _SEPARATORS.split(cleaned):
            if not token:
                continue
            try:
                values.append(float(token))
            except ValueError:
                raise ResourceError(
                    f"{source or 'array data'}:{line}: '{token}' is not a number"
                    + (f" (expected {count + len(values)} numbers before it)"
                       if values else "")
                )
    return values


def parse_array_text(text: str, source: str = "") -> List[float]:
    """``format: "text"`` - numbers separated by whitespace or commas."""
    return parse_numbers(text, source)


def parse_array_csv(text: str, source: str = "") -> List[float]:
    """``format: "csv"`` - comma separated, and a header row is skipped.

    A CSV exported from anything else usually names its columns; that name is not
    a number, and refusing the file for it would make people delete the header by
    hand. A header is therefore only skipped when the rest of the file parses.
    """
    lines = text.splitlines()
    if not lines:
        return []

    try:
        return parse_numbers(text, source)
    except ResourceError as exc:
        if ":1:" not in str(exc):
            raise
        # only the first line may be a header, and only if everything else works
        body = "\n".join(lines[1:])
        values = parse_numbers(body, source, first_line=2) if body.strip() else []
        if not values:
            raise ResourceError(
                f"{source or 'array data'}: has numbers in neither the header "
                f"nor the body"
            )
        return values


def parse_pd_array(text: str, source: str = "") -> Tuple[Optional[str], List[float]]:
    """``format: "pd"`` - what Pd's ``array save`` writes, or the writer's seed.

    Two shapes arrive here: a patch fragment (``#X array wave 1024 float 0;`` plus
    ``#A`` data lines) and a plain list of ``#A`` lines. Both mean the same list of
    numbers, so the declaration is returned for information and the offsets are
    honoured, padding a gap with zeros the way Pd would show it.
    """
    name: Optional[str] = None
    values: List[float] = []

    for line, raw in enumerate(text.splitlines(), start=1):
        # ``#`` is structure here, not a comment: every line of a Pd file starts
        # with one, so the number parser's comment stripping cannot be reused.
        cleaned = raw.strip().rstrip(";").strip()
        if not cleaned:
            continue

        parts = cleaned.split()
        head = parts[0]

        if head == "#X" and len(parts) >= 3 and parts[1] == "array":
            name = parts[2]
            continue
        if head == "#A":
            if len(parts) < 2:
                raise ResourceError(
                    f"{source or 'array data'}:{line}: '#A' needs the index the "
                    f"values start at"
                )
            try:
                offset = int(parts[1])
            except ValueError:
                raise ResourceError(
                    f"{source or 'array data'}:{line}: '{parts[1]}' is not an array "
                    f"index"
                )
            if offset < 0:
                raise ResourceError(
                    f"{source or 'array data'}:{line}: an array index cannot be "
                    f"negative"
                )
            tail = " ".join(parts[2:])
            try:
                found = parse_numbers(tail, source, first_line=line)
            except ResourceError:
                raise ResourceError(
                    f"{source or 'array data'}:{line}: an '#A' line holds numbers "
                    f"only"
                )
            if offset > len(values):
                values.extend([0.0] * (offset - len(values)))
            values.extend(found)
            continue
        if head.startswith(("#X", "#N", "#F")):
            # anything else Pd writes around an array (coords, restore, obj) is not
            # the data, and a tolerant reader is the point of this format
            continue

        raise ResourceError(
            f"{source or 'array data'}:{line}: '{head}' is not an array line; a "
            f"'pd' file holds '#A' values and optionally a '#X array' header"
        )

    return name, values


def parse_array_file(text: str, format: str = "text",
                     source: str = "") -> List[float]:
    """Every array format, through one door (``crude.project``'s only parser)."""
    if format == "text":
        return parse_array_text(text, source)
    if format == "csv":
        return parse_array_csv(text, source)
    if format == "pd":
        return parse_pd_array(text, source)[1]
    raise ResourceError(f"there is no array format '{format}' (known: text, csv, pd)")


def read_array_file(path: str, format: str = "text") -> List[float]:
    """Parse one array file from disk, naming it in every failure."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read()
    except OSError as exc:
        raise ResourceError(f"'{path}' could not be read: {exc.strerror or exc}")
    except UnicodeDecodeError as exc:
        raise ResourceError(f"'{path}' is not a text file of numbers: {exc}")

    values = parse_array_file(text, format, source=path)
    if not values:
        raise ResourceError(f"'{path}' contains no numbers")
    return values


# --------------------------------------------------------------------------
# presets
# --------------------------------------------------------------------------


def preset_document(name: str, data: Any) -> Dict[str, Any]:
    """The JSON a preset file holds: the values, plus what they are."""
    return {PRESET_KIND: PRESET_VERSION, "id": name, "data": data}


def preset_values(payload: Any, source: str = "") -> Any:
    """Read a preset document back, tolerating a file a person wrote by hand."""
    if isinstance(payload, dict) and PRESET_KIND in payload:
        version = payload.get(PRESET_KIND)
        if version != PRESET_VERSION:
            raise ResourceError(
                f"{source or 'a preset'} is preset version {version}, this runtime "
                f"reads {PRESET_VERSION}"
            )
        return payload.get("data")
    # a bare list or object is what a hand-written pattern file looks like
    return payload


# --------------------------------------------------------------------------
# the table
# --------------------------------------------------------------------------


class ResourceTable:
    """Every file the manifest declared, addressable by the names a script uses."""

    #: where a preset goes when the manifest declared no presets directory
    DEFAULT_PRESET_DIR = "presets"

    def __init__(self, manifest: Manifest):
        self.manifest = manifest
        self.root = manifest.project_dir
        self.entries: List[Resource] = []
        #: every name a script may ask for -> the resource behind it
        self.by_name: Dict[str, Resource] = {}
        #: names that could mean more than one file: asked for, they report it
        self.ambiguous: Dict[str, List[str]] = {}
        #: the directories presets were declared in, first one wins when saving
        self.preset_dirs: List[str] = []
        self._values: Dict[str, List[float]] = {}

        self._build()

    # -- construction ---------------------------------------------------
    def _build(self) -> None:
        resources = self.manifest.resources

        for declared in resources.images:
            self._add(IMAGE, declared)
        for declared in resources.sounds:
            self._add(SOUND, declared)
        for array in resources.arrays:
            self._add(ARRAY, declared=array.file, array=array, key=array.id)

        for declared in self.manifest.resources.presets:
            pattern = os.path.basename(declared)
            if "*" in pattern or "?" in pattern:
                self._expand_preset_glob(declared)
            else:
                self._add(PRESET, declared)

    def _add(self, kind: str, declared: str, array: ArrayDecl = None,
             key: str = None) -> Resource:
        path = self.manifest.resolve(declared)
        resource = Resource(id=key or os.path.splitext(os.path.basename(declared))[0],
                            kind=kind, path=path, declared=declared, array=array)
        self.entries.append(resource)

        if kind == PRESET:
            directory = os.path.dirname(path)
            if directory and directory not in self.preset_dirs:
                self.preset_dirs.append(directory)

        base = os.path.basename(declared)
        stem = os.path.splitext(base)[0]
        for alias in (declared, base, stem, resource.id):
            self._alias(alias, resource)
        return resource

    def _alias(self, alias: str, resource: Resource) -> None:
        """Bind one spelling to one file, and remember when it could mean two."""
        if not alias:
            return
        current = self.by_name.get(alias)
        if current is None:
            self.by_name[alias] = resource
            return
        if os.path.normcase(current.path) != os.path.normcase(resource.path):
            # the same word for two files: refuse it later rather than guess now
            names = self.ambiguous.setdefault(alias, [current.declared])
            if resource.declared not in names:
                names.append(resource.declared)

    def _expand_preset_glob(self, declared: str) -> None:
        """``presets/*.json``: the directory is declared, the files are found."""
        path = self.manifest.resolve(declared)
        directory = os.path.dirname(path)
        pattern = os.path.basename(declared)
        if directory and directory not in self.preset_dirs:
            self.preset_dirs.append(directory)

        try:
            names = sorted(os.listdir(directory))
        except OSError:
            # a project may ship no presets yet; saving one will create the folder
            return

        folder = os.path.dirname(declared)
        for entry in names:
            if not fnmatch.fnmatch(entry, pattern):
                continue
            full = os.path.join(directory, entry)
            if not os.path.isfile(full):
                continue
            self._add(PRESET, f"{folder}/{entry}" if folder else entry)

    # -- asking ---------------------------------------------------------
    def get(self, name: str) -> Resource:
        """The resource a script means by ``name``, or a clear refusal."""
        if not name:
            raise ResourceError("a resource needs a name or a path")

        wanted = str(name).replace("\\", "/")
        for candidate in (wanted, os.path.basename(wanted),
                          os.path.splitext(os.path.basename(wanted))[0]):
            ambiguous = self.ambiguous.get(candidate)
            if ambiguous:
                raise ResourceError(
                    f"'{candidate}' is ambiguous: the manifest declares "
                    f"{', '.join(ambiguous)}; ask for one by its full path"
                )
            resource = self.by_name.get(candidate)
            if resource is not None:
                return resource

        raise ResourceError(
            f"there is no resource '{name}' in this project"
            + (f"; declared: {self._spellings()}" if self.by_name
               else "; this project declares no resources")
        )

    def _spellings(self) -> str:
        return ", ".join(sorted({entry.id for entry in self.entries}))

    def path(self, name: str) -> str:
        """The absolute path of a declared resource (``RES``)."""
        return self.get(name).path

    def find(self, name: str) -> Optional[Resource]:
        """``get`` without the exception, for callers that can live without it."""
        try:
            return self.get(name)
        except ResourceError:
            return None

    def of_kind(self, kind: str) -> Tuple[Resource, ...]:
        check_kind(kind)
        return tuple(entry for entry in self.entries if entry.kind == kind)

    # -- arrays ---------------------------------------------------------
    def values(self, name: str) -> List[float]:
        """The numbers in an array file, parsed once per resource."""
        resource = self.get(name)
        if resource.kind != ARRAY:
            raise ResourceError(
                f"'{name}' is a {resource.kind}, not an array of numbers"
            )
        cached = self._values.get(resource.id)
        if cached is not None:
            return list(cached)

        values = read_array_file(resource.path,
                                 resource.array.format if resource.array else "text")
        self._values[resource.id] = values
        return list(values)

    def arrays_for(self, patch_id: str) -> List[Resource]:
        """The declared arrays that fill tables inside ``patch_id``."""
        return [entry for entry in self.entries
                if entry.kind == ARRAY and entry.array is not None
                and entry.array.patch == patch_id]

    # -- presets --------------------------------------------------------
    def preset_path(self, name: str) -> str:
        """Where a preset called ``name`` is stored, existing or not."""
        resource = self.find(name)
        if resource is not None and resource.kind == PRESET:
            return resource.path
        directory = self.preset_dirs[0] if self.preset_dirs else os.path.join(
            self.root, self.DEFAULT_PRESET_DIR)
        return os.path.join(directory, f"{_safe_name(name)}.json")

    def read_preset(self, name: str) -> Any:
        """The data a preset holds, with the version checked on the way in."""
        path = self.preset_path(name)
        try:
            with open(path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except OSError as exc:
            raise ResourceError(
                f"preset '{name}' could not be read from '{path}': "
                f"{exc.strerror or exc}"
            )
        except ValueError as exc:
            raise ResourceError(f"preset '{name}' in '{path}' is not JSON: {exc}")
        return preset_values(payload, source=path)

    def write_preset(self, name: str, data: Any) -> str:
        """Save a preset; returns the file written."""
        path = self.preset_path(name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(preset_document(name, data), handle, indent=2, sort_keys=True)
            handle.write("\n")

        if self.find(name) is None:
            # a preset the script just made is as addressable as a shipped one
            self._add(PRESET, _relative(self.manifest, path))
        return path

    def presets(self) -> List[str]:
        return [entry.id for entry in self.entries if entry.kind == PRESET]

    # -- validation -----------------------------------------------------
    def missing(self) -> List[Resource]:
        """Declared files that are not there: everything but a preset glob.

        A glob is a request ("ship whatever patterns are in that folder"), so an
        empty match is a project with no presets, not a broken one.
        """
        return [entry for entry in self.entries if not entry.exists]

    def check(self) -> List[str]:
        """One line per problem, for ``crude check`` and for a load failure."""
        return [f"{entry.kind} '{entry.id}' is declared as '{entry.declared}', "
                f"which is not in '{self.root}'" for entry in self.missing()]

    def describe(self) -> List[str]:
        """The table as text, in declaration order."""
        return [entry.describe() for entry in self.entries]

    def counts(self) -> Dict[str, int]:
        counts = {kind: 0 for kind in KINDS}
        for entry in self.entries:
            counts[entry.kind] = counts.get(entry.kind, 0) + 1
        return counts

    def __len__(self) -> int:
        return len(self.entries)

    def __iter__(self) -> Iterable[Resource]:
        return iter(self.entries)

    def __repr__(self) -> str:
        return f"<ResourceTable {self.counts()}>"


def check_kind(kind: str) -> str:
    if kind not in KINDS:
        raise ResourceError(
            f"there is no resource kind '{kind}' (known: {', '.join(KINDS)})"
        )
    return kind


def _relative(manifest: Manifest, path: str) -> str:
    """An absolute file back to the project-relative spelling JSON uses."""
    try:
        return os.path.relpath(path, manifest.project_dir).replace(os.sep, "/")
    except ValueError:  # pragma: no cover - different drives on Windows
        return os.path.basename(path)


def _safe_name(text: str) -> str:
    """A file name from a preset name: what a scripter typed, minus the paths."""
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(text)).strip("-._")
    return cleaned or "preset"
