"""Named scenes in one window, switched by name or by number.

A workspace is not a second window and not a second interpreter. It is a *subtree*
of the one :class:`~crude.ui.context.UIContext` tree, plus the answer to one
question: whose widgets is the window showing right now. The entry script has always
been the answer - it is the workspace named :data:`ROOT_WORKSPACE`, and its panel is
the window's own root - which is why nothing about an existing example, test or
binding changes while that is the only workspace there is.

Three rules carry the module:

* **Ownership is one name on one widget.** :meth:`~crude.ui.context.UIContext.add`
  stamps the widget it is given with the current scene's name the first time it is
  added, and a switch hides exactly the top-level widgets whose stamp is not the
  target's. :data:`~crude.ui.widget.SHARED_WORKSPACE` is the stamp that means "this
  belongs to the window, not to a scene": the menu bar, every menu popup and every
  dialog popup carry it, so changing scenes cannot take the menu down with it.
* **Hiding is not forgetting.** A scene whose :attr:`Workspace.keep` is true is
  *carried*: its subtree leaves the tree but keeps its props and its handler
  registrations, so a sampler's settings survive a jump to the mixer and come back
  unchanged. A detached widget cannot be hit, which is also what makes a handler
  registered in a hidden scene unable to fire - the refusal needs no second table.
  A scene whose ``keep`` is false is *rebuilt*: its subtree is removed through the
  context, which releases every registration with it, and its ``on_enter`` runs on
  the way in.
* **The geometry is the layout engine's.** A scene's panel is glued to its parent on
  all four edges - ``top`` to the menu bar's bottom edge when the window has one -
  so :func:`~crude.ui.layout.resolve` answers a window resize for a scene in the same
  pass it answers it for a dialog, and nothing here has a ``_slide()`` loop of its
  own.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from .base import UIError
from .layout import anchor, pad, unanchor
from .widget import SHARED_WORKSPACE, Widget

__all__ = ["Workspaces", "Workspace", "WorkspaceError", "ROOT_WORKSPACE"]

#: the entry script's scene, always index 0 and never re-declarable: the plan's
#: "'entry': 'main.crude' the entry will always be rootspace" said as a name
ROOT_WORKSPACE = "root"


class WorkspaceError(UIError):
    """A scene was asked for by a name the window does not have, or twice."""


def _word(value: Any) -> str:
    """A workspace name, trimmed; anything that is not text is refused."""
    if isinstance(value, Widget):
        raise WorkspaceError(f"'{value.path()}' is a widget, not a workspace; "
                             "name the scene by its name or by its number")
    said = str(value if value is not None else "").strip()
    if not said:
        raise WorkspaceError("a workspace needs a name: WORKSPACE(\"mixer\"), or "
                             "a number from WORKSPACE_INDEX()")
    return said


class Workspace:
    """One scene: its name, its panel, and how it is carried between shows."""

    __slots__ = ("name", "panel", "on_enter", "keep", "stash", "attached",
                 "seeded")

    def __init__(self, name: str, *, panel: Optional[Widget] = None,
                 on_enter: Optional[Callable[..., Any]] = None,
                 keep: bool = True, seeded: bool = False) -> None:
        #: what a script called it, and what a widget's ``workspace`` stamp says
        self.name = str(name)
        #: the panel this scene's widgets live in. ``None`` for
        #: :data:`ROOT_WORKSPACE`, whose scene *is* the window's root panel, and
        #: ``None`` for a rebuilt scene between its exit and its next entry - the
        #: panel is made again so a scene that asked to be rebuilt gets a clean one
        self.panel = panel
        #: the function run on the way in, for a scene that rebuilds itself
        self.on_enter = on_enter
        #: carry the subtree (props and handlers survive) or throw it away and
        #  rebuild it. Declaring an ``on_enter`` is a script saying "rebuild me", so
        #  a scene with one starts out ``keep=False``; ``WORKSPACE_KEEP`` restates it
        self.keep = bool(keep)
        #: the top-level widgets taken out of the tree while this scene is hidden,
        #  in tree order, so coming back puts them back the same way they went
        self.stash: List[Widget] = []
        #: whether this scene's widgets are in the tree right now
        self.attached = False
        #: named by the manifest and not yet filled in by a script. Such a scene is
        #  what ``WORKSPACE("mixer")`` adopts instead of refusing, because the
        #  project said the scene exists before the code that draws it ran
        self.seeded = bool(seeded)

    @property
    def is_root(self) -> bool:
        """Whether this is the entry script's scene, whose panel is the window's root.

        It can still be carried off when another scene is shown - the window below
        the menu is the new scene's, and that is the whole gesture - but it has no
        panel of its own to be lifted out, and it never loses its name.
        """
        return self.name == ROOT_WORKSPACE

    def __str__(self) -> str:
        said = f"Workspace({self.name}"
        said += ", root" if self.is_root else ", panel"
        said += ", keep" if self.keep else ", rebuild"
        if self.on_enter is not None:
            said += ", on_enter"
        return said + (", shown" if self.attached else ", hidden") + ")"

    def __repr__(self) -> str:
        return f"<Workspace {self.name!r} {'shown' if self.attached else 'hidden'}>"


class Workspaces:
    """The scenes one window knows, and the one it is showing.

    Built by :class:`~crude.ui.context.UIContext` with the context, and holding
    :data:`ROOT_WORKSPACE` alone until a script declares another. Every public
    answer is a name or a number, never an index into a private list, because a
    script's ``WORKSPACE_SHOW(2)`` and its ``WORKSPACE_SHOW("mixer")`` have to be
    the same gesture.
    """

    def __init__(self, ctx: Any) -> None:
        #: the window these scenes are subtrees of
        self.ctx = ctx
        #: the scenes, in declaration order - which is the order a ``View`` menu
        #  lists them in and the order ``WORKSPACE_INDEX`` counts
        self.rows: List[Workspace] = []
        self._by_name: Dict[str, Workspace] = {}
        #: the menu bar's panel, when the window has a menu bar: what every scene's
        #  ``top`` edge is glued to, and why a scene starts below the bar
        self.bar: Optional[Widget] = None
        root = Workspace(ROOT_WORKSPACE)
        root.attached = True
        self.rows.append(root)
        self._by_name[root.name] = root
        self.current: Workspace = root

    # -- reading ----------------------------------------------------------
    def names(self) -> List[str]:
        """Every scene's name, in order: what ``WORKSPACE_NAMES()`` answers."""
        return [held.name for held in self.rows]

    def index_of(self, wanted: Any) -> int:
        """Which number this scene is: by name, or its own number, unchanged."""
        return self.rows.index(self.get(wanted))

    def get(self, wanted: Any) -> Workspace:
        """One scene by name or by number, and a named refusal for neither.

        An unknown name lists the known ones, because the point of a number is that
        a script can be a loop and the point of a name is that a person can be wrong.
        A name is tried first: a window may legitimately have a scene called ``"2"``,
        and it should win over the second scene for the same reason a variable
        declaration wins over a positional argument.
        """
        if isinstance(wanted, Workspace):
            return wanted
        if isinstance(wanted, Widget):
            raise WorkspaceError(f"'{wanted.path()}' is a widget, not a workspace; "
                                 "name the scene by its name or by its number")
        if isinstance(wanted, bool):
            raise WorkspaceError(f"'{wanted}' is not a workspace: name it, or give "
                                 "the number WORKSPACE_INDEX() answered")
        said = str(wanted if wanted is not None else "").strip()
        if not said:
            raise WorkspaceError("a workspace needs a name: WORKSPACE(\"mixer\"), "
                                 "or a number from WORKSPACE_INDEX()")
        found = self._by_name.get(said)
        if found is not None:
            return found
        if isinstance(wanted, int) or said.lstrip("-").isdigit():
            number = int(said)
            if not 0 <= number < len(self.rows):
                raise WorkspaceError(
                    f"there is no workspace number {said}; this window has "
                    f"{len(self.rows)}: {', '.join(self.names())}")
            return self.rows[number]
        raise WorkspaceError(f"there is no workspace named '{said}'; this "
                             f"window has: {', '.join(self.names())}")

    @property
    def current_name(self) -> str:
        """The name of the scene on screen: what ``WORKSPACE()`` answers."""
        return self.current.name

    def container(self, held: Optional[Workspace] = None) -> Widget:
        """Where a widget goes in this scene: its panel, or the window's root.

        :data:`ROOT_WORKSPACE` has no panel of its own - the entry script's widgets
        are the window's children, which is what every example has always had - and
        every other scene owns a panel that fills the window below the menu bar, so
        a scene's widgets are inside something that can be lifted out in one move.
        """
        found = held if held is not None else self.current
        if found.panel is not None:
            return found.panel
        if found.is_root:
            return self.ctx.root
        # a rebuilt scene between its exit and its re-entry: make the panel it
        # will hold its next frame of widgets in, at the point it is asked for
        # one rather than at the point somebody remembers to
        found.panel = self._make_panel(found.name)
        return found.panel

    # -- writing ----------------------------------------------------------
    def claim(self, held: Widget) -> Widget:
        """Stamp a widget with the scene it was added to, once.

        Only a widget that says nothing yet is stamped: a scene's own panel already
        carries its name (so a switch can find it again), and anything the menu
        layer marked :data:`~crude.ui.widget.SHARED_WORKSPACE` before adding it is
        the window's business rather than a scene's.
        """
        if not held.workspace:
            held.workspace = self.current_name
        return held

    def declare(self, name: Any, on_enter: Optional[Callable[..., Any]] = None,
                keep: Optional[bool] = None) -> Workspace:
        """Add a scene; the :class:`Workspace`, not switched to.

        The name is validated here rather than in the bindings so a manifest, a
        runtime and a script all get the same refusal: no duplicates, no
        :data:`~crude.ui.widget.SHARED_WORKSPACE` (the window's own stamp), and no
        second :data:`ROOT_WORKSPACE`, which the plan makes index 0 by definition.

        A name the manifest seeded and no script has claimed yet is not a duplicate:
        the project named the scene, and this call is the script filling it in, so
        the entry function and the carry rule are installed on the scene that is
        already in the list at the position the manifest put it.
        """
        said = _word(name)
        found = self._by_name.get(said)
        if found is not None:
            if not found.seeded:
                raise WorkspaceError(
                    f"'{said}' is already a workspace in this window"
                    + (", and the entry script's scene is always that one at "
                       "index 0" if said == ROOT_WORKSPACE else ""))
            found.seeded = False
            found.on_enter = on_enter
            found.keep = (on_enter is None) if keep is None else bool(keep)
            return found
        if said == SHARED_WORKSPACE:
            raise WorkspaceError(f"'{said}' is the window's own, not a scene's: "
                                 "the menu bar and every popup are stamped with it")
        held = Workspace(said, on_enter=on_enter,
                         keep=(on_enter is None) if keep is None else bool(keep))
        self.rows.append(held)
        self._by_name[said] = held
        return held

    def seed(self, names: Any) -> List[Workspace]:
        """Name the scenes a manifest declared, in order, without showing any.

        The runtime's job, before the script runs: the list is names and order only
        (the manifest validates them the same way), so a project whose ``ui`` section
        says ``["sampler", "sequencer", "mixer"]`` has those numbers reserved at 1, 2
        and 3 whether or not the script has reached them yet. A name already in the
        list - ``root``, or a scene a test built first - is left exactly as it is.
        """
        made: List[Workspace] = []
        for name in (names or ()):
            said = _word(name)
            if said in self._by_name or said == SHARED_WORKSPACE:
                continue
            held = Workspace(said, seeded=True)
            self.rows.append(held)
            self._by_name[said] = held
            made.append(held)
        return made

    def show(self, wanted: Any) -> str:
        """Put one scene on screen and take the others off; the name now current.

        The order is the plan's: everyone else leaves first, the target arrives, and
        only then does the target's ``on_enter`` run - so a scene that rebuilds
        itself adds its widgets into a tree that is already saying which scene it is,
        and :meth:`~crude.ui.context.UIContext.add` stamps them correctly.
        """
        held = self.get(wanted)
        if held is self.current:
            return held.name
        for other in self.rows:
            if other is held or not other.attached:
                continue
            self._detach(other)
        self.current = held
        self._attach(held)
        if not held.keep and held.on_enter is not None:
            self._enter(held)
        return held.name

    def attach_bar(self, panel: Any) -> Optional[Widget]:
        """Tell the scenes where the menu bar is, and re-glue their top edges.

        A bar is usually built before the first scene switch, but "usually" is not a
        reason to make a scene guess: whichever order the two arrive in, every scene
        on screen ends up starting at the bar's bottom edge rather than under it.
        """
        self.bar = panel if isinstance(panel, Widget) else None
        for held in self.rows:
            if held.attached and held.panel is not None:
                self._glue(held.panel)
        return self.bar

    # -- the tree, moved --------------------------------------------------
    def _detach(self, held: Workspace) -> None:
        """Take one scene's top-level widgets out of the window's tree."""
        gone = [kid for kid in self.ctx.root.children
                if kid.workspace == held.name]
        if held.keep:
            for kid in gone:
                # out of the tree and out of the pointer's reach, but every prop and
                # every ON_CLICK it was given stays on it: this is the carry, and a
                # widget with no parent is a widget no hit test can find
                kid.detach()
                self.ctx.router.forget(kid)
            held.stash = gone
        else:
            for kid in gone:
                # the full release, handlers and cached faces included, which is what
                # "rebuild me" costs and what makes the next on_enter honest
                self.ctx.remove(kid)
            held.stash = []
            if held.panel is not None:
                # the panel went with the subtree it held; a fresh one is made by
                # container() the next time this scene is asked to hold something
                held.panel = None
        held.attached = False

    def _attach(self, held: Workspace) -> None:
        """Put one scene's widgets back, in the order they left."""
        if held.is_root:
            for kid in held.stash:
                self.ctx.root.add(kid)
            held.stash = []
            held.attached = True
            return
        panel = self.container(held)
        for kid in held.stash:
            if kid is not panel:
                self.ctx.root.add(kid)
        held.stash = []
        if panel.parent is None:
            self.ctx.root.add(panel)
        self._glue(panel)
        held.attached = True

    def _make_panel(self, name: str) -> Widget:
        """A scene's own panel: invisible, unpadded, and stamped with its name."""
        panel = Widget("panel", {"label": ""}, name=name)
        # stamped before it is added so the context's stamp leaves it alone: this is
        # the widget a switch lifts out and puts back
        panel.workspace = name
        # a scene is the window below the menu rather than a card inside it, so it
        # takes the window's role - a plate the colour of the window, which is to say
        # no plate at all over what the script drew - and no padding of its own, so
        # ``PLACE(x, y)`` inside a scene measures from the same edge it measures from
        # in the entry script's scene
        panel.set("role", "window")
        pad(panel, 0.0)
        return panel

    def _glue(self, panel: Widget) -> None:
        """Fill the window below the menu bar: four glues, no arithmetic."""
        if panel.parent is None:
            # a detached scene has no parent to be glued to, and its anchors are
            # restated on the way back in rather than left pointing at nothing
            return
        unanchor(panel)
        # both edges on one axis are a stretch rather than a fight: the panel is
        # exactly as wide and as tall as the room the bar and the window leave it,
        # which is the whole answer to "a resize should not need a second pass"
        anchor(panel, "left")
        anchor(panel, "right")
        anchor(panel, "bottom")
        if self.bar is not None and self.bar.parent is not None:
            anchor(panel, "top", self.bar, "bottom")
        else:
            anchor(panel, "top")

    def _enter(self, held: Workspace) -> None:
        """Run a rebuilt scene's entry function, and log it if it could not run.

        Through :meth:`~crude.lang.program.Program.invoke`, which is the one door
        host code has back into the language and the same one the Pd, MIDI and
        serial runtimes use: the tick stays on the interpreter thread, and a failure
        is a line in the window's error list rather than an exception that stops the
        frame - a scene with a bug in it must not take the menu bar down with it.
        """
        func = held.on_enter
        if func is None:
            return
        bridge = getattr(self.ctx, "handlers", None)
        program = getattr(bridge, "program", None)
        invoke = getattr(program, "invoke", None)
        if callable(invoke):
            _value, error = invoke(func, [])
            if error is None:
                return
            self._said(bridge, f"workspace '{held.name}' on_enter failed: "
                               f"{getattr(error, 'details', None) or error}")
            return
        if not callable(func):
            self._said(bridge, f"workspace '{held.name}' was given an on_enter "
                               f"that is not callable: {func!r}")
            return
        try:
            func()
        except Exception as exc:  # a host callback's own bug, said and carried on
            self._said(bridge, f"workspace '{held.name}' on_enter raised {exc}")

    def _said(self, bridge: Any, message: str) -> None:
        """One log line, in whichever of the two shapes this window keeps."""
        if bridge is None:
            return
        errors = getattr(bridge, "errors", None)
        if isinstance(errors, list):
            errors.append(message)
        speak = getattr(bridge, "log", None)
        if callable(speak):
            speak(message)

    # -- reports ----------------------------------------------------------
    def describe(self) -> List[str]:
        """One line per scene: the name, the number, and which one is on screen."""
        out = []
        for index, held in enumerate(self.rows):
            said = f"{index} {held.name}"
            said += " (current)" if held is self.current else ""
            said += "" if held.keep else " (rebuilt)"
            if held.seeded:
                # the project named this one and the script has not drawn it yet
                said += " (declared)"
            out.append(said)
        return out

    def __len__(self) -> int:
        return len(self.rows)

    def __iter__(self):
        return iter(self.rows)

    def __contains__(self, wanted: Any) -> bool:
        try:
            self.get(wanted)
        except (WorkspaceError, ValueError):
            return False
        return True

    def __str__(self) -> str:
        return (f"Workspaces({self.current_name}: "
                f"{', '.join(self.names())})")

    def __repr__(self) -> str:
        return (f"<Workspaces {self.current_name!r} "
                f"{len(self.rows)} scene(s)>")
