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
rules.py).

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

from flo2_ifc import checking, drawing, geometry, reading, rules

#: The tools this module replaces or adds, by name.
REPLACED = ("ifc_summary", "ifc_tree", "ifc_select", "ifc_info", "ifc_relations", "ifc_validate", "ifc_quantify")
ADDED = ("ifc_extent", "ifc_units", "ifc_footprint", "ifc_volume", "ifc_distance", "ifc_draw", "ifc_check_ids", "ifc_write_ids", "ifc_check_rules")

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
    have, so on mcp 2.x both its FastMCP and its ImageContent are None. They
    are set, for this one call, to the recorder and to mcp 2.x's ImageContent
    (which still accepts ifcmcp's ``mimeType=``, and is the name its image
    tools' return annotations resolve to), and put back after. If a later
    ifcmcp builds its server some other way, this fails loudly at start, and
    the smoke and tool-list tests with it."""
    import ifcmcp.server as ifcmcp_server
    from mcp.types import ImageContent

    if not hasattr(ifcmcp_server, "FastMCP") or not hasattr(ifcmcp_server, "ImageContent"):
        raise RuntimeError("flo2-ifc cannot read this build of ifcmcp's server: it no longer names FastMCP and ImageContent")
    saved = (ifcmcp_server.FastMCP, ifcmcp_server.ImageContent)
    ifcmcp_server.FastMCP, ifcmcp_server.ImageContent = _Recorder, ImageContent
    try:
        recorded = ifcmcp_server.build_server()
    finally:
        ifcmcp_server.FastMCP, ifcmcp_server.ImageContent = saved
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


def build_server() -> Any:
    from mcp.server.mcpserver import MCPServer

    recorded = _ifcmcp_tools()
    session = _session_of(recorded)
    watched = _watch_changes(session)
    server = MCPServer(**recorded.kwargs)
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
        IFC class, and the units it declares for lengths, areas and volumes
        (``units``)."""
        m = model()
        out = summary_mod.summary(m)
        out["units"] = reading.units(m)
        return out

    @tool
    def ifc_units() -> dict[str, Any]:
        """The length, area and volume units the model declares in its
        IfcUnitAssignment: each one's name, symbol and how many metres (square
        metres, cubic metres) one of it is. Every length, area and volume the
        model STORES is in these units: a Revit export is often in millimetres,
        with areas in square metres. Read it before quoting a stored number."""
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
    def ifc_select(query: str, offset: int = 0, limit: int = reading.DEFAULT_LIMIT) -> dict[str, Any]:
        """Find elements with an IfcOpenShell selector; each one's id, IFC
        class and name, sorted by id, a page at a time, with how many of each
        class matched (``counts``).

        Selectors: ``IfcWall``; ``IfcWall, IfcDoor``; ``IfcWall, Name = "W1"``;
        ``type = "Concrete Wall"``; ``material = "Concrete"``; and BY PLACE,
        ``IfcDoor, location = "Level 1"`` (the doors on the storey named
        Level 1; a space or building name works too).

        :param query: The selector.
        :param offset: Where the page starts (the reply's ``page.next_offset``).
        :param limit: How many to list, at most (default 100).
        """
        return reading.select(model(), query, offset=offset, limit=limit)

    @tool
    def ifc_info(element_id: int) -> dict[str, Any]:
        """Everything about one element: its attributes, property sets and
        quantity sets (stored lengths, areas and volumes, in the model's units,
        which ``units`` names), its type, material, placement and shape, and
        the STOREY it is on (``storey``, with the path the model uses: a room
        is usually part of its storey, a wall contained in it).

        :param element_id: The element's id (its STEP number).
        """
        m = model()
        element = reading.element(m, element_id)
        out = info_mod.info(m, element)
        out.update(reading.info_extra(m, element))
        return out

    @tool
    def ifc_relations(element_id: int, traverse: str = "", element_type: str = "", offset: int = 0, limit: int = reading.DEFAULT_LIMIT) -> dict[str, Any] | list[dict[str, Any]]:
        """How one element relates to the rest of the model: what contains it
        and what it is part of (``hierarchy``), what it contains (``children``),
        its type and the type's occurrences, groups, systems, zones, material
        and connections. Each long list is paged and can be filtered; ``lists``
        gives each one's total and what was withheld. ``traverse`` "up" gives
        the chain of places from the element to the project instead.

        :param element_id: The element's id.
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
    def ifc_extent(selector: str = "", element_id: int = 0, offset: int = 0, limit: int = 50, datum: str = "") -> dict[str, Any]:
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
        :param element_id: Or one element, by its id.
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
    def ifc_footprint(selector: str = "", ids: str = "", element_id: int = 0, offset: int = 0, limit: int = 50) -> dict[str, Any]:
        """How much plan area elements cover, from their geometry: each
        element's footprint (its shape projected onto plan), and the SET's
        footprint (``footprint``), in which what is stacked (a slab on a
        footing, a wall over a wall) counts once. ``sum_of_elements`` adds each
        one's own; ``stacked_overlap`` is the difference. In the model's area
        unit, in square metres and in square feet. Name the elements with a
        selector, ids, or one element id (any together). The reply's ``cite``
        names the model file and its sha256, the elements' GlobalIds, the unit
        and the method. A call that runs out of time says ``partial``. Reads
        only.

        :param selector: Which elements, as ifc_select takes it (e.g.
            'IfcSlab, location = "Level 1"').
        :param ids: Or elements by step id or GlobalId, comma separated
            (e.g. "1203, 1247").
        :param element_id: Or one element, by its id.
        :param offset: Where the page of elements starts.
        :param limit: How many elements' own footprints to list (default 50).
        """
        return geometry.footprint(model(), path(), selector=selector, ids=ids, element_id=element_id, offset=offset, limit=limit, edits=watched["edits"])

    @tool
    def ifc_volume(selector: str = "", ids: str = "", element_id: int = 0, offset: int = 0, limit: int = 50) -> dict[str, Any]:
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
        :param ids: Or elements by step id or GlobalId, comma separated.
        :param element_id: Or one element, by its id.
        :param offset: Where the page of elements starts.
        :param limit: How many elements to list (default 50).
        """
        return geometry.volume(model(), path(), selector=selector, ids=ids, element_id=element_id, offset=offset, limit=limit, edits=watched["edits"])

    @tool
    def ifc_distance(a: str, b: str) -> dict[str, Any]:
        """The distance between two elements, from their geometry: the
        shortest gap between their surfaces (``clear_distance``, 0 when they
        touch or overlap) with the two closest points, the gap between their
        boxes along x, y and z, and the distance between their centres, in
        plan and in height. In the model's length unit, in metres and in feet
        and inches. The reply's ``cite`` names the model file and its sha256,
        both GlobalIds, the unit and the method. Reads only.

        :param a: One element: its step id or its GlobalId.
        :param b: The other.
        """
        return geometry.distance(model(), path(), a=a, b=b, edits=watched["edits"])

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
        {"min_length": n}, {"max_length": n}.

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
