"""ifcmcp, as flo2.io runs it: its tools on the MCP Python SDK 2.x, with readers for a whole building.

The tool code is IfcOpenShell's ifcmcp, from upstream's published package
(ifcopenshell-mcp, pinned exactly in pyproject.toml, without its ``[mcp]``
extra, which asks for mcp<2). The server is flo2-ifc's own ``MCPServer`` from
the MCP Python SDK 2.x. ifcmcp's ``build_server`` is run against a recorder in
place of FastMCP (``_ifcmcp_tools``), so every tool it defines is registered
here with its own name, arguments, description and function, which calls
ifcmcp's ``IfcSession`` (core.py), with its one session and its 25 tools'
behaviour unchanged, except where a real building did not fit in a reply or a
call (reading.py says why):

- ``ifc_tree``, ``ifc_select`` and ``ifc_relations`` list a page at a time and
  filter (by IFC class, by storey), and say what they withheld;
- ``ifc_info`` also says which storey an element is on, however the model
  places it there, and the model's units;
- ``ifc_summary`` and ``ifc_quantify`` also say the model's units;
- ``ifc_validate`` checks as much as one call's time allows and says where to
  go on from;

and tools are added: ``ifc_extent`` (how far elements reach, from their
geometry, and how high above a datum) and ``ifc_units`` (the units the model
declares); since 0.3.0 the measures ``ifc_footprint``, ``ifc_volume`` and
``ifc_distance`` (geometry.py), and ``ifc_draw``, a plan, section or elevation
to scale as SVG and PNG (drawing.py), which ifcmcp's ``ifc_plot`` cannot make
in ifcopenshell 0.9.0; and since 0.5.0 the checks ``ifc_check_ids`` (the model
against an IDS, with IfcOpenShell's ifctester) and ``ifc_check_rules``
(buildingSMART's normative validation rules, run offline), and
``ifc_write_ids``, which builds an IDS with ifctester's API (checking.py,
rules.py); and since 0.6.0 the building steps ``ifc_add_wall``, ``ifc_add_slab``,
``ifc_add_roof`` and ``ifc_add_space``, each making one ordinary part of a
building in ONE call: the element, its shape, its place, its storey and its
link to the design part it realizes, with IfcOpenShell's own api functions
(building.py); and since 0.9.0 ``ifc_add_unit``, which declares a project unit
(a U-factor's, before a U-factor is written).

Since 0.9.0 every unit a model declares is read, and each measured property's
unit (reading.py, "WHAT A VALUE WITH NO UNIT OF ITS OWN IS IN"); ifc_edit's
property writes are held to a declared unit (``_hold_units``); and every tool
names what it is about one way (``ARGUMENT_NAMES``), taking each old name as an
alias (``ALIASES``, applied before a call's arguments are checked).

Every tool, ifcmcp's and flo2-ifc's, is registered through one catch
(``_reason_kept``): a failure reaches the agent as a ToolError with its own
reason, because MCPServer replaces the text of any other exception with a bare
"Error executing tool <name>".

Every measure says what flo2-calc needs to cite its number (``cite``: the
model file and its sha256, the elements' GlobalIds, the unit and the method),
and whether the session's copy was edited since it was opened: the session's
editing methods are watched (``_watch_changes``) for that, and so that a
drawing after an edit draws the edited model.

Each replacement keeps its tool's name and adds only optional arguments, so a
caller of ifcmcp's tool still calls it the same way. These are flo2-ifc's, not
ifcmcp's. Any of them that ifcmcp itself would want is offered to IfcOpenShell
as its own change.
"""

from __future__ import annotations

import functools
import inspect
from typing import Any

from flo2_ifc import building, checking, drawing, geometry, reading, rules

#: The tools this module replaces or adds, by name.
REPLACED = ("ifc_summary", "ifc_tree", "ifc_select", "ifc_info", "ifc_relations", "ifc_validate", "ifc_quantify")
ADDED = (
    "ifc_extent",
    "ifc_units",
    "ifc_footprint",
    "ifc_volume",
    "ifc_distance",
    "ifc_draw",
    "ifc_check_ids",
    "ifc_write_ids",
    "ifc_check_rules",
    "ifc_add_wall",
    "ifc_add_slab",
    "ifc_add_roof",
    "ifc_add_space",
    "ifc_add_unit",
)
#: The added tools that change the session's model (building.py), counted as edits like ifc_edit.
BUILDS = ("ifc_add_wall", "ifc_add_slab", "ifc_add_roof", "ifc_add_space", "ifc_add_unit")

#: ONE NAME FOR ONE THING (since 0.9.0; flo2's design 1a97fc1f1de4d5d2,
#: fact:root-cause-a-helpers-describe-is-refused-because-the-client-holds-a-tool-list-without-it-2026-10-26). Every
#: tool names what it is about the way most tools already did: one element is ``element_id`` (six tools named it so,
#: one named it ``a``/``b``), several are ``element_ids`` (ifcmcp's ifc_plot and ifc_render; flo2-ifc's footprint and
#: volume said ``ids``), and a selector is ``selector`` (six tools; ifc_select said ``query``). An element is named by
#: its step id or its GlobalId wherever flo2-ifc reads it. Left as they are, with reasons: ifcmcp's ``ifc_schema``
#: takes ``entity_type``, the IFC class whose schema it reads, which is most often not an element (an
#: IfcPropertySingleValue, an IfcRelAggregates), so ``element_type`` would misname it; ifcmcp's own ``ifc_clash``,
#: ``ifc_plot`` and ``ifc_render`` already use these names and keep ifcmcp's integer ids; ``ifc_draw``'s ``through``,
#: ``around`` and ``highlight`` each name a role (cut through these, draw around those), not which element a call is
#: about.
ARGUMENT_NAMES = {
    "element_id": "one element: its step id or its GlobalId",
    "element_ids": "several elements: step ids or GlobalIds",
    "selector": "an IfcOpenShell selector",
    "element_type": "an IFC class the listed elements are kept to",
}
#: Each argument's old name, still taken: a caller holding an old tool list (flo2's door and chats keep the list they
#: connected with) calls the old way and is answered the same. Served schemas show only the new names.
ALIASES = {
    "ifc_select": {"query": "selector"},
    "ifc_footprint": {"ids": "element_ids"},
    "ifc_volume": {"ids": "element_ids"},
    "ifc_distance": {"a": "element_id", "b": "other_element_id"},
}


def canonical_arguments(tool: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
    """`arguments` with every old name (ALIASES) given its new one. Both names with different values is refused."""
    arguments = dict(arguments or {})
    for old, new in ALIASES.get(tool, {}).items():
        if old in arguments:
            value = arguments.pop(old)
            if new in arguments and arguments[new] != value:
                raise ValueError(f"{tool} was given `{new}` and its old name `{old}`, with different values; give `{new}` only.")
            arguments[new] = value
    return arguments

#: The session's methods that change its model, and those that replace it.
CHANGES = ("ifc_edit", "ifc_shape", "ifc_quantify")
REPLACES = ("ifc_load", "ifc_new", "ifc_reset")


def _watch_changes(session: Any) -> dict[str, int]:
    """Count the changes made to the session's model since it was opened, and
    forget its kept meshes on each one. ifcmcp's tools call these methods on
    the session object at call time, so wrapping them on the instance watches
    every tool that changes the model, with no tool's arguments changed."""
    state = {"edits": 0}

    def wrap(name: str, replaces: bool) -> None:
        inner = getattr(session, name, None)
        if inner is None:
            return

        @functools.wraps(inner)
        def watched(*args, **kwargs):
            try:
                out = inner(*args, **kwargs)
            finally:
                geometry.changed()
            if replaces:
                state["edits"] = 0
            elif not (isinstance(out, dict) and out.get("ok") is False):
                state["edits"] += 1
            return out

        setattr(session, name, watched)

    for n in CHANGES:
        wrap(n, False)
    for n in REPLACES:
        wrap(n, True)
    return state


class _Recorder:
    """Stands in for FastMCP while ifcmcp's ``build_server`` runs: it keeps the
    server's name and instructions, and each tool's function and the keyword
    arguments it was registered with, in order, and registers nothing."""

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.tools: list[tuple[Any, dict[str, Any]]] = []

    def tool(self, **kwargs: Any):
        def record(fn):
            self.tools.append((fn, kwargs))
            return fn

        return record


def _ifcmcp_tools() -> _Recorder:
    """Every tool ifcmcp's own server defines, as it defines it.

    ifcmcp's server.py imports FastMCP from mcp 1.x, which mcp 2.x does not
    have, so on mcp 2.x both its FastMCP and its ImageContent are None.
    FastMCP is set, for this one call, to the recorder, and put back after.
    ImageContent is set to mcp 2.x's (which still accepts ifcmcp's
    ``mimeType=``, and is the name its image tools' return annotations resolve
    to) and LEFT SET: ifc_render and ifc_plot look it up in that module when
    they are CALLED, to wrap their PNG. Until 0.7.0 it was put back to None
    after the recording, so both failed with "'NoneType' object is not
    callable" once they had drawn; nothing offered either then. If a later
    ifcmcp builds its server some other way, this fails loudly at start, and
    the smoke and tool-list tests with it."""
    import ifcmcp.server as ifcmcp_server
    from mcp.types import ImageContent

    if not hasattr(ifcmcp_server, "FastMCP") or not hasattr(ifcmcp_server, "ImageContent"):
        raise RuntimeError("flo2-ifc cannot read this build of ifcmcp's server: it no longer names FastMCP and ImageContent")
    saved = ifcmcp_server.FastMCP
    ifcmcp_server.FastMCP, ifcmcp_server.ImageContent = _Recorder, ImageContent
    try:
        recorded = ifcmcp_server.build_server()
    finally:
        ifcmcp_server.FastMCP = saved
    if not isinstance(recorded, _Recorder) or not recorded.tools:
        raise RuntimeError("flo2-ifc read no tools from this build of ifcmcp's server")
    return recorded


def _session_of(recorded: _Recorder) -> Any:
    """ifcmcp's one IfcSession, from the closure of the tool that reads it.

    ifcmcp keeps its session inside build_server; its tools close over it. If a
    later ifcmcp keeps it some other way, this fails loudly at start, and the
    smoke test with it."""
    fn = next((f for f, _ in recorded.tools if f.__name__ == "ifc_summary"), None)
    session = inspect.getclosurevars(fn).nonlocals.get("session") if fn else None
    if session is None or not hasattr(session, "ifc_load"):
        raise RuntimeError("flo2-ifc cannot find ifcmcp's session in this build of ifcmcp; the readers cannot be added")
    return session


def _reason_kept(fn):
    """`fn`, raising any failure as a ToolError with its own reason. MCPServer
    passes on a ToolError's text but replaces any other exception with a bare
    "Error executing tool <name>", which leaves the agent nothing to correct.
    This is the one place flo2-ifc's server does it, for every tool."""
    from mcp.server.mcpserver.exceptions import ToolError

    @functools.wraps(fn)
    def call(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ToolError:
            raise
        except Exception as e:
            raise ToolError(str(e) or type(e).__name__) from e

    return call


def _hold_units(session: Any) -> None:
    """Hold ifc_edit's pset.edit_pset and pset.edit_qto to a declared unit (building.py, "A UNIT IS DECLARED BEFORE A
    VALUE IN IT IS WRITTEN"): a measured value they would store with no unit is not written, and the reply says how to
    declare the unit. Every other edit runs as ifcmcp runs it. Wrapped before ``_watch_changes``, so a refused edit is
    not counted as one."""
    inner = getattr(session, "ifc_edit", None)
    if inner is None:
        return

    @functools.wraps(inner)
    def edit(function_path: str, params: Any = "{}"):
        if function_path not in building.WRITES_VALUES:
            return inner(function_path=function_path, params=params)
        model = session._require_model()
        model.begin_transaction()
        try:
            out = inner(function_path=function_path, params=params)
        finally:
            model.end_transaction()
        missing = building.unit_missing_after_edit(model, function_path, params) if isinstance(out, dict) and out.get("ok") else []
        if not missing:
            return out
        model.undo()
        return {
            "ok": False,
            "error": (
                f"Not written: {', '.join(missing)} would be stored with no unit, because the model declares none for that measure"
                " and the property names none, so the number would mean nothing in particular. Declare the unit first with"
                " ifc_add_unit (the unit the value is in, e.g. unit_type THERMALTRANSMITTANCEUNIT in W/(m2.K) or Btu/(hr.ft2.°F)),"
                " then write the value in it. Nothing was changed."
            ),
        }

    session.ifc_edit = edit


def build_server() -> Any:
    from mcp.server.mcpserver import MCPServer
    from mcp.server.mcpserver.exceptions import ToolError

    class Server(MCPServer):
        """MCPServer, taking each tool's old argument names (ALIASES) as its new ones."""

        async def call_tool(self, name, arguments, context=None):
            try:
                arguments = canonical_arguments(name, arguments)
            except ValueError as e:
                raise ToolError(str(e)) from e
            return await super().call_tool(name, arguments, context)

    recorded = _ifcmcp_tools()
    session = _session_of(recorded)
    _hold_units(session)
    watched = _watch_changes(session)
    server = Server(**recorded.kwargs)
    for fn, kwargs in recorded.tools:
        if fn.__name__ not in REPLACED:
            server.tool(**kwargs)(_reason_kept(fn))

    def model():
        return session._require_model()

    def path() -> str | None:
        return getattr(session, "model_path", None)

    def tool(fn):
        """Register `fn` under its own name and docstring, through the one catch."""
        return server.tool(description=inspect.getdoc(fn))(_reason_kept(fn))

    from ifcquery import info as info_mod
    from ifcquery import relations as relations_mod
    from ifcquery import summary as summary_mod

    @tool
    def ifc_summary() -> dict[str, Any]:
        """What the model holds: its IFC schema, its project, how many of each
        IFC class, and every unit it declares (``units``, as ifc_units gives
        them)."""
        m = model()
        out = summary_mod.summary(m)
        out["units"] = reading.units(m)
        return out

    @tool
    def ifc_units() -> dict[str, Any]:
        """Every unit the model declares in its IfcUnitAssignment (``declared``):
        SI, conversion-based (a foot), derived (a U-factor's W/(m2.K)) and its
        currency, each with its unit type, what it measures, its name, symbol and
        how many SI units one of it is; and its length, area and volume units by
        name. Every value the model STORES with no unit of its own is in these:
        a Revit export is often in millimetres, with areas in square metres. A
        measure with no unit here (often a U-factor) has NO unit in the model:
        ``say`` says so, and ifc_info's ``property_units`` says which properties.
        Read it before quoting a stored number."""
        return reading.units(model())

    @tool
    def ifc_tree(storey: str = "", element_type: str = "", offset: int = 0, limit: int = reading.DEFAULT_LIMIT) -> dict[str, Any]:
        """The model's spatial structure: project, site, building, storeys and
        the rooms (spaces) in each, with their ids, in full; each place's
        elements counted by IFC class (``element_counts``) and listed a page at
        a time in tree order. A storey says its stored ``elevation`` and its
        placed ``z``, in the model's length unit.

        :param storey: One storey only, by its id or its name (e.g. "Level 1").
        :param element_type: List only elements of this IFC class (e.g. "IfcDoor").
        :param offset: Where the page of elements starts (the reply's
            ``elements_page.next_offset`` gets the next one).
        :param limit: How many elements to list, at most (default 100); a long
            page is also cut to fit one reply, and the reply says so.
        """
        return reading.tree(model(), storey=storey, element_type=element_type, offset=offset, limit=limit)

    @tool
    def ifc_select(selector: str, offset: int = 0, limit: int = reading.DEFAULT_LIMIT) -> dict[str, Any]:
        """Find elements with an IfcOpenShell selector; each one's id, IFC
        class and name, sorted by id, a page at a time, with how many of each
        class matched (``counts``).

        Selectors: ``IfcWall``; ``IfcWall, IfcDoor``; ``IfcWall, Name = "W1"``;
        ``type = "Concrete Wall"``; ``material = "Concrete"``; and BY PLACE,
        ``IfcDoor, location = "Level 1"`` (the doors on the storey named
        Level 1; a space or building name works too).

        :param selector: The selector (until 0.9.0 ``query``, still taken).
        :param offset: Where the page starts (the reply's ``page.next_offset``).
        :param limit: How many to list, at most (default 100).
        """
        return reading.select(model(), selector, offset=offset, limit=limit)

    @tool
    def ifc_info(element_id: int | str) -> dict[str, Any]:
        """Everything about one element: its attributes, property sets and
        quantity sets (stored lengths, areas and volumes, in the model's units,
        which ``units`` names), its type, material, placement and shape, and
        the STOREY it is on (``storey``, with the path the model uses: a room
        is usually part of its storey, a wall contained in it). Each measured
        property's measure type and unit (``property_units``: a U-factor's
        IfcThermalTransmittanceMeasure and W/(m2.K), from the property itself or
        the project, or "not declared", which ``units`` then names).

        :param element_id: The element: its id (its STEP number) or its GlobalId.
        """
        m = model()
        element = reading.element(m, element_id)
        out = info_mod.info(m, element)
        out.update(reading.info_extra(m, element))
        return out

    @tool
    def ifc_relations(element_id: int | str, traverse: str = "", element_type: str = "", offset: int = 0, limit: int = reading.DEFAULT_LIMIT) -> dict[str, Any] | list[dict[str, Any]]:
        """How one element relates to the rest of the model: what contains it
        and what it is part of (``hierarchy``), what it contains (``children``),
        its type and the type's occurrences, groups, systems, zones, material
        and connections. Each long list is paged and can be filtered; ``lists``
        gives each one's total and what was withheld. ``traverse`` "up" gives
        the chain of places from the element to the project instead.

        :param element_id: The element: its id or its GlobalId.
        :param traverse: "up" for the chain to the project.
        :param element_type: Keep only list items of this IFC class (e.g. "IfcDoor").
        :param offset: Where each list's page starts.
        :param limit: How many items each list gives, at most (default 100).
        """
        m = model()
        element = reading.element(m, element_id)
        full = relations_mod.relations(m, element, traverse=traverse or None)
        return reading.relations(full, m, element_type=element_type, offset=offset, limit=limit)

    @tool
    def ifc_extent(selector: str = "", element_id: int | str = 0, offset: int = 0, limit: int = 50, datum: str = "") -> dict[str, Any]:
        """How far elements reach, measured from their geometry: the box
        around them (min and max x, y, z, the overall size, the bottom and the
        top), the element that reaches highest and the one lowest, and each
        element's own box a page at a time. In the model's length unit, with
        the overall box also in metres and in feet and inches. For example,
        the highest point of the roofs: ``selector`` "IfcRoof", then
        ``overall.top`` and ``highest``. ``z`` is in the model's world
        coordinates, as each storey's placed ``z`` in ifc_tree. Boxes are
        aligned with the model's axes. A call that runs out of time says
        ``partial`` and what it measured. Reads only.

        :param selector: Which elements, as ifc_select takes it (e.g. "IfcRoof"
            or 'IfcDoor, location = "Level 1"').
        :param element_id: Or one element, by its id or GlobalId.
        :param offset: Where the page of elements starts.
        :param limit: How many elements' boxes to list (default 50).
        :param datum: Optional: heights are also given above this: a
            storey's name or id (its level), or a height (z) in the model's
            length unit. ``above_datum`` then says the bottom and top above it.

        The reply's ``cite`` names the model file and its sha256, the
        elements' GlobalIds, the unit and the method: what a calculation
        keeps with a number taken from here.
        """
        return reading.extent(model(), selector=selector, element_id=element_id, offset=offset, limit=limit, datum=datum, model_path=path(), edits=watched["edits"])

    @tool
    def ifc_footprint(selector: str = "", element_ids: str | list[int | str] = "", element_id: int | str = 0, offset: int = 0, limit: int = 50) -> dict[str, Any]:
        """How much plan area elements cover, from their geometry: each
        element's footprint (its shape projected onto plan), and the SET's
        footprint (``footprint``), in which what is stacked (a slab on a
        footing, a wall over a wall) counts once. ``sum_of_elements`` adds each
        one's own; ``stacked_overlap`` is the difference. In the model's area
        unit, in square metres and in square feet. Name the elements with a
        selector, element_ids, or one element_id (any together). The reply's ``cite``
        names the model file and its sha256, the elements' GlobalIds, the unit
        and the method. A call that runs out of time says ``partial``. Reads
        only.

        :param selector: Which elements, as ifc_select takes it (e.g.
            'IfcSlab, location = "Level 1"').
        :param element_ids: Or elements by step id or GlobalId, comma
            separated or a list (e.g. "1203, 1247"); until 0.9.0 ``ids``, still taken.
        :param element_id: Or one element, by its id or GlobalId.
        :param offset: Where the page of elements starts.
        :param limit: How many elements' own footprints to list (default 50).
        """
        return geometry.footprint(model(), path(), selector=selector, element_ids=element_ids, element_id=element_id, offset=offset, limit=limit, edits=watched["edits"])

    @tool
    def ifc_volume(selector: str = "", element_ids: str | list[int | str] = "", element_id: int | str = 0, offset: int = 0, limit: int = 50) -> dict[str, Any]:
        """How much solid elements hold, from their geometry, and which of
        their shapes are NOT CLOSED. A volume is given only for a closed mesh
        (every edge shared by two triangles, or closed at its seams); an OPEN
        mesh holds no solid volume, because what it encloses depends on where
        it is measured from, so it is named in ``not_closed`` and left out of
        ``total``. In the model's volume unit, in cubic metres and in cubic
        feet. The reply's ``cite`` names the model file and its sha256, the
        GlobalIds of the elements in the total, the unit and the method. Reads
        only.

        :param selector: Which elements, as ifc_select takes it.
        :param element_ids: Or elements by step id or GlobalId, comma
            separated or a list; until 0.9.0 ``ids``, still taken.
        :param element_id: Or one element, by its id or GlobalId.
        :param offset: Where the page of elements starts.
        :param limit: How many elements to list (default 50).
        """
        return geometry.volume(model(), path(), selector=selector, element_ids=element_ids, element_id=element_id, offset=offset, limit=limit, edits=watched["edits"])

    @tool
    def ifc_distance(element_id: int | str, other_element_id: int | str) -> dict[str, Any]:
        """The distance between two elements, from their geometry: the
        shortest gap between their surfaces (``clear_distance``, 0 when they
        touch or overlap) with the two closest points, the gap between their
        boxes along x, y and z, and the distance between their centres, in
        plan and in height. In the model's length unit, in metres and in feet
        and inches. The reply's ``cite`` names the model file and its sha256,
        both GlobalIds, the unit and the method. The reply calls the two ``a``
        and ``b``. Reads only.

        :param element_id: One element: its step id or its GlobalId (until
            0.9.0 ``a``, still taken).
        :param other_element_id: The other (until 0.9.0 ``b``, still taken).
        """
        return geometry.distance(model(), path(), element_id=element_id, other_element_id=other_element_id, edits=watched["edits"])

    @tool
    def ifc_draw(
        output_path: str,
        view: str = "plan",
        storey: str = "",
        cut_height: float | None = None,
        line: str = "",
        through: str = "",
        direction: str = "",
        gridline: str = "",
        looking: str = "",
        side: str = "",
        around: str = "",
        margin: float | None = None,
        depth: float | None = None,
        highlight: str = "",
        scale: str = "",
        paper: str = "",
        title: str = "",
        png_px: int = 2000,
    ) -> dict[str, Any]:
        """Draw the model to scale: a PLAN of a storey, a SECTION along a
        line, or an ELEVATION of one side; written as ``output_path``.svg (in
        millimetres of paper, so it prints at its scale) and ``output_path``.png.
        What a plane cuts is drawn dark, what lies beyond (or below, in a
        plan) as seen, nearer over farther; rooms are outlined and named (a
        room that holds other rooms is named clear of them, or, where its name
        does not fit, left unnamed and listed in ``unlabelled_rooms`` and
        ``notes``); a section or elevation marks each storey's level. The sheet states the
        scale, the model's unit, a scale bar in metres and in feet, and the
        model file it was drawn from. ``highlight`` draws elements in orange
        (for what a change touches). Lengths and positions you give are in the
        model's length unit, the numbers ifc_extent gives. A first drawing of
        a large model makes its shapes first and may stop with ``partial``:
        call again with the same arguments, the shapes are kept. Reads the
        model only.

        :param output_path: Where to write: <output_path>.svg and .png.
        :param view: "plan", "section" or "elevation".
        :param storey: A plan's storey (name or id). For a section or an
            elevation, optional: only these storeys, comma separated.
        :param cut_height: A plan's cut, above the storey's level, in the
            model's length unit (default 1.2 m).
        :param line: A section along x1, y1, x2, y2 (model length unit).
        :param through: Or a section through the middle of these elements
            (ids, GlobalIds, or a selector), running ``direction``.
        :param direction: "north-south" (default) or "east-west".
        :param gridline: Or a section along a grid line, by its tag.
        :param looking: Which way a section looks: north, south, east or west
            (default: to the left of the line's run, so west for a
            north-south one and north for an east-west one).
        :param side: An elevation's side: north, south, east or west (the
            side seen; north is the model's +y).
        :param around: Draw only around these elements (ids, GlobalIds or a
            selector): their box in plan, plus ``margin``.
        :param margin: Around ``around``'s box, in the model's length unit
            (default 1 m).
        :param depth: A section's depth: draw only what lies within this far
            beyond the cut (model length unit). Default: all of it.
        :param highlight: Elements to draw in orange: ids, GlobalIds or a
            selector.
        :param scale: "1:100", or an architect's scale such as 1/8" = 1'-0"
            (1:96). Default: the largest that fits the sheet.
        :param paper: The sheet: Tabloid (11 x 17", the default), Letter, A4,
            A3, A2, A1, A0, ARCH C, ARCH D, ANSI D.
        :param title: The title written on the sheet (default: what it shows).
        :param png_px: The picture's longer side in pixels (default 2000).
        """
        return drawing.draw(
            model(),
            output_path,
            view=view,
            model_path=path(),
            storey=storey,
            cut_height=cut_height,
            line=line,
            through=through,
            direction=direction,
            gridline=gridline,
            looking=looking,
            side=side,
            around=around,
            margin=margin,
            depth=depth,
            highlight=highlight,
            scale=scale,
            paper=paper,
            title=title,
            png_px=png_px,
            edits=watched["edits"],
        )

    @tool
    def ifc_check_ids(ids_path: str, report_path: str = "", start: int = 0, offset: int = 0, limit: int = 20) -> dict[str, Any]:
        """Check the model open in this session against an IDS file
        (buildingSMART's Information Delivery Specification 1.0: what a model
        must contain), with IfcOpenShell's own IDS checker, ifctester. Each
        specification says ``passed`` or ``failed``, how many elements it
        applies to (``applicable``), and the failing elements by GlobalId with
        ifctester's reason, a page at a time. With ``report_path``, ifctester's
        own reports are written as <report_path>.html (for a person) and
        <report_path>.json (for a program). The reply names the IDS and the
        model file, each with its sha256. An IDS that is not valid against the
        IDS 1.0 schema is refused with the schema's reason. A large check is
        made over several calls: ``complete: false`` and ``next_start``; call
        again with ``start`` set to it. Reads the model only.

        :param ids_path: The IDS file (.ids) to check against.
        :param report_path: Optional: where to write the reports, without
            .html or .json.
        :param start: Which specification to start from, 0 first (the last
            reply's ``next_start``).
        :param offset: Where each specification's page of failing elements starts.
        :param limit: How many failing elements each specification lists (default 20).
        """
        return checking.check_ids(model(), path(), ids_path, report_path=report_path, start=start, offset=offset, limit=limit, edits=watched["edits"])

    @tool
    def ifc_write_ids(description: str, output_path: str) -> dict[str, Any]:
        """Write an IDS file (IDS 1.0) from a description of what a model must
        contain, with ifctester's API: never hand-written XML. It is checked
        against the IDS 1.0 schema and read back before it is kept; an invalid
        one is refused with the reason, and nothing is written. Written as
        <output_path>.ids. Needs no model.

        ``description`` is JSON: {"title": ..., "ifc_version": ["IFC4"]
        (optional), "specifications": [{"name": ..., "cardinality":
        "required" | "optional" | "prohibited", "applicability": [facets],
        "requirements": [facets]}]}. A facet is one of
        {"facet": "entity", "name": "IfcWall", "predefined_type": ...};
        {"facet": "attribute", "name": "Name", "value": ...};
        {"facet": "property", "property_set": "Pset_WallCommon", "property":
        "FireRating", "value": ..., "data_type": "IFCLABEL"};
        {"facet": "classification", "system": ..., "value": ...};
        {"facet": "material", "value": ...};
        {"facet": "part_of", "entity": "IfcBuildingStorey", "relation":
        "IFCRELCONTAINEDINSPATIALSTRUCTURE"}. A requirement facet may say
        "cardinality" (required, optional, prohibited). A value is text or a
        number, or a restriction: {"pattern": ...}, {"one_of": [...]},
        {"at_least": n}, {"at_most": n}, {"more_than": n}, {"less_than": n},
        {"min_length": n}, {"max_length": n}. UNITS, as IDS 1.0 has them: a
        property's or quantity's value is in SI units (metres, square
        metres), whatever the model's unit, and ifctester converts; an
        attribute's value (a door's OverallWidth) is compared as stored, in
        the model's own unit.

        :param description: The IDS, described as above, as JSON text.
        :param output_path: Where to write it, without .ids.
        """
        return checking.write_ids(description, output_path)

    @tool
    def ifc_check_rules(rule_sets: str = "", start: int = 0, offset: int = 0, limit: int = 50) -> dict[str, Any]:
        """Check the model FILE open in this session against buildingSMART's
        validation rules beyond the IFC schema: the normative rules
        (implementer agreements and informal propositions) and the
        industry-practice rules that buildingSMART's validation service runs,
        from their published code (github.com/buildingSMART/ifc-gherkin-rules,
        MIT), run here unchanged, with no network. Each rule says
        ``passed``, ``error``, ``warning`` or ``not_applicable``; issues name
        the element by id and GlobalId with what was expected and observed, a
        page at a time. The reply says which rule sets ran and which did not
        (the schema and EXPRESS checks are ifc_validate's; the bSDD check
        needs the network). Checks the file, not unsaved edits. A large model
        is checked over several calls: ``complete: false`` and
        ``next_start``; call again with ``start`` set to it.

        :param rule_sets: Optional: comma separated, of critical,
            implementer-agreement, informal-proposition, industry-practice; or
            "normative" for the first three. Default: all.
        :param start: Which rule to start from, 0 first, in code order (the
            last reply's ``next_start``).
        :param offset: Where the page of issues starts.
        :param limit: How many issues to list (default 50).
        """
        return rules.check_rules(model(), path(), rule_sets=rule_sets, start=start, offset=offset, limit=limit, edits=watched["edits"])

    def built(out: dict[str, Any]) -> dict[str, Any]:
        """A building step changed the model: count it as an edit, as ifc_edit is, and forget kept meshes."""
        geometry.changed()
        watched["edits"] += 1
        out["units"] = reading.units(model())["say"]
        return out

    @tool
    def ifc_add_wall(
        storey: str,
        start: list[float],
        end: list[float],
        height: float,
        thickness: float,
        height_at_end: float = 0.0,
        peak_height: float = 0.0,
        base_offset: float = 0.0,
        align: str = "centre",
        name: str = "",
        realizes: str = "",
        frame: str = "world",
    ) -> dict[str, Any]:
        """Add a WALL in one call: made, shaped, placed, put on its storey and
        linked to the design part it realizes. It runs along a baseline from
        ``start`` to ``end`` (points [x, y] in plan, in ``frame``), stands on the storey's
        level, and is ``height`` high and ``thickness`` thick. A sloped top
        rises from ``height`` at the start to ``height_at_end`` at the end; a
        gable end rises from ``height`` at both ends to ``peak_height`` in the
        middle. Every length is in the model's length unit (ifc_units), as
        ifc_extent gives them. Changes only the session's copy.

        :param storey: The storey it stands on, by name or id (ifc_tree names them).
        :param start: Where its baseline starts, [x, y].
        :param end: Where its baseline ends, [x, y].
        :param height: Its height (at its start, when its top slopes).
        :param thickness: Its thickness.
        :param height_at_end: Optional: its height at its end, for a sloped top
            (0 or left out: a level top).
        :param peak_height: Optional: its height in the middle, for a gable end
            (0 or left out: none).
        :param base_offset: Optional: how far above the storey's level its bottom is (default 0).
        :param align: Optional: "centre" (default) on the baseline, or "left" or
            "right" of it, looking from start to end.
        :param name: Optional: its name, e.g. "North wall".
        :param realizes: Optional: the id of the design part it realizes, e.g.
            "cmp:north-wall"; kept as a reference in the classification "flo2 design".
        :param frame: Optional: the frame [x, y] are in. "world" (default):
            the model's world plan, as ifc_extent and ifc_info report it.
            "storey": the storey's own axes, which its placement turns and
            shifts into the world (a plan's dimensions along a turned
            building's walls). The reply's ``frame`` says which was used.
        """
        # 0 is "not given": a plain number keeps the tool's schema plain (no anyOf with null).
        return built(building.add_wall(model(), storey, start, end, height, thickness, height_at_end or None, peak_height or None, base_offset, align, name, realizes, frame))

    @tool
    def ifc_add_slab(
        storey: str,
        footprint: list[list[float]],
        thickness: float,
        top_offset: float = 0.0,
        kind: str = "floor",
        name: str = "",
        realizes: str = "",
        frame: str = "world",
    ) -> dict[str, Any]:
        """Add a SLAB in one call: made, shaped, placed, put on its storey and
        linked to the design part it realizes. It covers ``footprint`` (its
        corners in order, each [x, y] in plan, in ``frame``), its top at the storey's level
        (plus ``top_offset``), ``thickness`` deep below it. Every length is in
        the model's length unit (ifc_units). Changes only the session's copy.

        :param storey: The storey it is the floor of, by name or id.
        :param footprint: Its corners in order around it, e.g. [[0, 0], [30, 0], [30, 20], [0, 20]].
        :param thickness: How deep it is.
        :param top_offset: Optional: how far above the storey's level its top is (default 0).
        :param kind: Optional: "floor" (default) or "baseslab".
        :param name: Optional: its name.
        :param realizes: Optional: the id of the design part it realizes.
        :param frame: Optional: the frame [x, y] are in. "world" (default):
            the model's world plan, as ifc_extent and ifc_info report it.
            "storey": the storey's own axes, which its placement turns and
            shifts into the world (a plan's dimensions along a turned
            building's walls). The reply's ``frame`` says which was used.
        """
        return built(building.add_slab(model(), storey, footprint, thickness, top_offset, kind, name, realizes, frame))

    @tool
    def ifc_add_roof(
        storey: str,
        footprint: list[list[float]],
        kind: str,
        eave_height: float,
        slope: float,
        thickness: float,
        overhang: float = 0.0,
        name: str = "",
        realizes: str = "",
        frame: str = "world",
    ) -> dict[str, Any]:
        """Add a ROOF in one call: an IfcRoof on the storey, made of sloped
        roof planes (IfcSlab), shaped, placed and linked to the design part it
        realizes. "shed": one plane, its low eave along the footprint's FIRST
        side (its first two corners), rising across it. "gable": two planes,
        their eaves along the first side and the side across from it, meeting
        at a ridge halfway across. ``eave_height`` is where the underside meets
        the footprint's eave side (the top of the wall), above the storey's
        level; ``slope`` is rise over run (0.25 for 3 in 12). Every length is
        in the model's length unit (ifc_units). Changes only the session's copy.

        :param storey: The storey it sits on, by name or id.
        :param footprint: The outline it covers, its corners [x, y] in order, in ``frame``, the eave side first.
        :param kind: "shed" or "gable".
        :param eave_height: The underside's height at the eave side, above the storey's level.
        :param slope: Rise over run, e.g. 0.25.
        :param thickness: Its thickness, square to the plane.
        :param overhang: Optional: how far it reaches beyond the footprint all round (default 0).
        :param name: Optional: its name.
        :param realizes: Optional: the id of the design part it realizes.
        :param frame: Optional: the frame [x, y] are in. "world" (default):
            the model's world plan, as ifc_extent and ifc_info report it.
            "storey": the storey's own axes, which its placement turns and
            shifts into the world (a plan's dimensions along a turned
            building's walls). The reply's ``frame`` says which was used.
        """
        return built(building.add_roof(model(), storey, footprint, kind, eave_height, slope, thickness, overhang, name, realizes, frame))

    @tool
    def ifc_add_space(
        storey: str,
        footprint: list[list[float]],
        height: float,
        floor_offset: float = 0.0,
        name: str = "",
        long_name: str = "",
        realizes: str = "",
        frame: str = "world",
    ) -> dict[str, Any]:
        """Add a ROOM (IfcSpace) in one call: made, shaped, placed, part of its
        storey as a room is, and linked to the design part it realizes. It
        covers ``footprint`` (its corners in order, each [x, y] in plan, in ``frame``) from
        the storey's level up ``height``. Every length is in the model's length
        unit (ifc_units). Changes only the session's copy.

        :param storey: The storey it is on, by name or id.
        :param footprint: Its corners in order around it.
        :param height: Its height.
        :param floor_offset: Optional: how far above the storey's level its floor is (default 0).
        :param name: Optional: its name (often its number), e.g. "101".
        :param long_name: Optional: its long name, e.g. "Shop floor".
        :param realizes: Optional: the id of the design part it realizes.
        :param frame: Optional: the frame [x, y] are in. "world" (default):
            the model's world plan, as ifc_extent and ifc_info report it.
            "storey": the storey's own axes, which its placement turns and
            shifts into the world (a plan's dimensions along a turned
            building's walls). The reply's ``frame`` says which was used.
        """
        return built(building.add_space(model(), storey, footprint, height, floor_offset, name, long_name, realizes, frame))

    @tool
    def ifc_add_unit(unit_type: str, unit: str = "", prefix: str = "", parts: list[dict[str, Any]] = []) -> dict[str, Any]:  # noqa: B006 - read only; a plain list keeps the schema plain (no anyOf with null)
        """Declare one of the model's units in one call (its IfcUnitAssignment),
        so the values stored in that measure carry it: a U-factor's unit before
        a U-factor is written, a temperature's, a currency. An SI unit by name
        (WATT, KELVIN, METRE with prefix MILLI); a conversion-based one by name,
        with its exact factor (foot, inch, square foot, btu, hour, fahrenheit,
        pound, degree); a derived one from its ``parts``; a currency by its
        code. A unit type the model already declares is refused (its stored
        values are read in that unit). Write each value in the unit declared:
        ifc_edit's pset.edit_pset refuses a measured value whose unit the model
        does not declare. Changes only the session's copy.

        :param unit_type: What it measures, as IFC names it: LENGTHUNIT,
            THERMODYNAMICTEMPERATUREUNIT, THERMALTRANSMITTANCEUNIT (a U-factor),
            THERMALRESISTANCEUNIT (an R-value), MONETARYUNIT, ...
        :param unit: A named unit: an SI name or a conversion-based name; for
            MONETARYUNIT, the currency's ISO code (USD).
        :param prefix: Optional: an SI unit's prefix (MILLI, KILO).
        :param parts: A derived unit's parts, each {"unit": name, "exponent":
            n, "prefix": optional}: W/(m2.K) is [{"unit": "WATT"}, {"unit":
            "METRE", "exponent": -2}, {"unit": "KELVIN", "exponent": -1}];
            Btu/(hr.ft2.°F) is [{"unit": "btu"}, {"unit": "hour", "exponent":
            -1}, {"unit": "square foot", "exponent": -1}, {"unit":
            "fahrenheit", "exponent": -1}].
        """
        out = building.add_unit(model(), unit_type, unit, prefix, parts)
        geometry.changed()
        watched["edits"] += 1
        return out

    @tool
    def ifc_validate(express_rules: bool = False, start: int = 0) -> dict[str, Any]:
        """Check the model against the IFC schema: every instance's attributes
        and inverses, and that no GlobalId is used twice. A large model is
        checked over several calls: when time runs out the reply says
        ``complete: false`` and ``next_start``; call again with ``start`` set
        to it. Only ``complete: true`` with ``valid: true`` means the whole
        model passed.

        :param express_rules: Also run the schema's rules (slower), once the
            instances are done, in the call that finishes them.
        :param start: Where to check on from: the last reply's ``next_start``.
        """
        return reading.validate(model(), express_rules=express_rules, start=start)

    @tool
    def ifc_quantify(rule: str, selector: str = "") -> dict[str, Any]:
        """Recompute quantities from the geometry with a named rule (e.g.
        "IFC4QtoBaseQuantities") for the elements a selector matches, into the
        session's model; read them with ifc_info. They are written in the
        model's units (``units``).

        :param rule: The quantity rule.
        :param selector: Which elements; leave out for all.
        """
        out = session.ifc_quantify(rule=rule, selector=selector)
        if isinstance(out, dict):
            out["units"] = reading.units(model())["say"]
        return out

    return server
