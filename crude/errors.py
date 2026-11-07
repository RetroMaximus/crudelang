"""Package-level error types and process exit codes.

Every subsystem raises a subclass of :class:`CrudeError`; the CLI maps the
concrete type onto one of the exit codes below so scripts and CI can branch on
the failure class without parsing text.

:class:`ExitRequest` is the exception to that sentence, on purpose: a script
asking to stop is not a failure, so it is not a ``CrudeError`` either.
"""

from __future__ import annotations

EXIT_OK = 0
EXIT_MANIFEST = 2
EXIT_LOAD = 3
EXIT_SCRIPT = 4
EXIT_RUNTIME = 5

EXIT_CODES = {
    "manifest": EXIT_MANIFEST,
    "load": EXIT_LOAD,
    "script": EXIT_SCRIPT,
    "runtime": EXIT_RUNTIME,
}


class CrudeError(Exception):
    """Base class for every error the runtime raises on purpose."""

    kind = "runtime"
    exit_code = EXIT_RUNTIME


class ManifestError(CrudeError):
    """A manifest is missing, malformed or references something illegal."""

    kind = "manifest"
    exit_code = EXIT_MANIFEST

    def __init__(self, message: str, pointer: str = "") -> None:
        self.pointer = pointer
        #: the message without the pointer prefix, so a caller can add context
        #: (which file?) without duplicating the location in the text
        self.message = message
        super().__init__(f"{pointer}: {message}" if pointer else message)


class LoadError(CrudeError):
    """A load-pipeline phase failed."""

    kind = "load"
    exit_code = EXIT_LOAD

    def __init__(self, step: str, cause: BaseException) -> None:
        self.step = step
        self.cause = cause
        super().__init__(f"load step '{step}' failed: {cause}")


class ResourceError(CrudeError):
    """A project file is missing, or is not the data the manifest claimed.

    Raised by the resource table itself, before a phase has a step name to
    attach, so the loader can still wrap it into a :class:`LoadError`.
    """

    kind = "load"
    exit_code = EXIT_LOAD


class CancelError(CrudeError):
    """A load was stopped on purpose (``--progress`` Ctrl-C, a window close).

    It is a load failure for the caller - the project never came up - but the
    pipeline needs to tell a stop the user asked for apart from a phase that
    broke, because only the second one is worth a stack trace in the log.
    """

    kind = "load"
    exit_code = EXIT_LOAD

    def __init__(self, reason: str = "the load was cancelled") -> None:
        self.reason = reason
        super().__init__(reason)


class ScriptError(CrudeError):
    """A `.crude` script failed to compile or raised while running."""

    kind = "script"
    exit_code = EXIT_SCRIPT


class HostError(CrudeError):
    """A host binding rejected its arguments or hit an unrecoverable state."""

    kind = "runtime"
    exit_code = EXIT_RUNTIME


class ExitRequest(Exception):
    """A script asked for the process to end: ``EXIT``.

    This is control flow, not a failure, which is why it is *not* a
    :class:`CrudeError`: the call machinery turns a ``CrudeError`` raised inside a
    binding into a script error, and the interpreter would label a clean shutdown
    as a crash. It travels out of the binding, past the interpreter, to whoever
    started the script - and carries the code that run should end with.
    """

    #: the process exit code the script chose
    code = EXIT_OK

    def __init__(self, code: int = EXIT_OK, reason: str = "") -> None:
        self.code = int(code)
        #: what the script said to say, which is worth a line in the log
        self.reason = str(reason or "")
        super().__init__(f"the script asked to exit with code {self.code}")
