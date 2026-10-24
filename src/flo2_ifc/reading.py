"""Reading a whole building model within one reply and one call's time.

ifcmcp answers a question about a small model in full. A real building is not
small: a large building model flo2 read (2026-10-23) gave a storey's
relations as over 150,000 characters, its spatial tree as over 200,000, and
took over 100 s to validate. A chat can take about 29,000 characters in one
reply and stops waiting at about 60 s. So each reader here:

- pages: it lists at most ``limit`` items and at most ``PAGE_CHARS`` characters,
  and says how many it withheld and the ``offset`` that gets the rest;
- filters: by IFC class and by storey, so a chat asks for less in the first place;
- keeps time: it stops at ``SECONDS`` and says what it did not reach.

Every reply that carries a length, area or volume names the model's own units
(``units``), read from its IfcUnitAssignment: a Revit export is often in
millimetres, and a reader told "usually metres" misreads it by a thousand.

These functions take an open ``ifcopenshell.file`` and return plain data. They
never write the model.
"""

from __future__ import annotations

import json
import math
import os
import select as _io_select
import time
from collections import Counter
from collections.abc import Callable, Iterator
from typing import Any

import ifcopenshell
import ifcopenshell.util.element
import ifcopenshell.util.placement
import ifcopenshell.util.selector
import ifcopenshell.util.unit

#: Characters of JSON one listing may spend. flo2's door holds a whole reply to
#: about 29,000 characters, its own wrapping included.
PAGE_CHARS = int(os.environ.get("FLO2_IFC_PAGE_CHARS", "20000"))
#: Seconds one call may spend measuring or checking before it stops and says
#: what it reached. A chat stops waiting at about 60 s, and the first call of a
#: session also opens the model (about 5 s for a large model on a laptop).
SECONDS = float(os.environ.get("FLO2_IFC_SECONDS", "30"))
#: Items one listing gives when the caller names no limit.
DEFAULT_LIMIT = 100
MAX_LIMIT = 1000


# ---------------------------------------------------------------- units


def _unit_entry(model: ifcopenshell.file, unit_type: str) -> dict[str, Any]:
    try:
        unit = ifcopenshell.util.unit.get_project_unit(model, unit_type)
    except (IndexError, AttributeError):  # no project, or a project with no units
        unit = None
    if unit is None:
        return {"declared": False, "name": None, "symbol": None, "si_per_unit": None}
    name = ifcopenshell.util.unit.get_full_unit_name(unit).lower().replace("_", " ")
    scale = ifcopenshell.util.unit.get_unit_scale(unit) if hasattr(ifcopenshell.util.unit, "get_unit_scale") else None
    return {"declared": True, "name": name, "symbol": _symbol(unit), "si_per_unit": scale}


def _symbol(unit: ifcopenshell.entity_instance) -> str | None:
    """The unit's symbol (``mm``, ``ft``), or None when none is known: never ``?``.

    IfcOpenShell's ``get_unit_symbol`` looks a conversion-based unit up by its
    name exactly as its table spells it, and answers ``?`` otherwise. Revit
    writes ``FOOT``, ``SQUARE FOOT`` and ``CUBIC FOOT``, where the table has
    ``foot``; IFC compares these names in any case (buildingSMART's PJS001 does),
    so a conversion-based unit is looked up here in any case too. A name no table
    knows gives no symbol, and the reply names the unit without one.
    """
    try:
        symbol = ifcopenshell.util.unit.get_unit_symbol(unit)
    except Exception:  # an unusual conversion-based unit
        symbol = None
    if (not symbol or "?" in symbol) and unit.is_a("IfcConversionBasedUnit"):
        wanted = (unit.Name or "").strip().lower()
        symbol = next((s for n, s in ifcopenshell.util.unit.unit_symbols.items() if n.lower() == wanted), None)
    return symbol if symbol and "?" not in symbol else None


def _plural(name: str | None) -> str | None:
    if not name:
        return name
    return name if name.endswith(("s", "feet")) else ("feet" if name == "foot" else name.replace("foot", "feet") if name.endswith("foot") else name + "s")


def units(model: ifcopenshell.file) -> dict[str, Any]:
    """The length, area and volume units the model declares (its IfcUnitAssignment).

    Each has its ``name`` (``millimetre``), ``symbol`` (``mm``) and
    ``si_per_unit``: how many metres, square metres or cubic metres one of it
    is (0.001 for a millimetre). A unit the model does not declare says
    ``declared: false``. Lengths, areas and volumes the model STORES (its
    quantity sets, its placements, a storey's elevation) are in these units.
    """
    out = {k: _unit_entry(model, t) for k, t in (("length", "LENGTHUNIT"), ("area", "AREAUNIT"), ("volume", "VOLUMEUNIT"))}
    length = out["length"]
    if length["declared"]:
        symbol = f" ({length['symbol']})" if length["symbol"] else ""
        out["say"] = (
            f"Lengths are in {_plural(length['name'])}{symbol}, areas in {_plural(out['area']['name']) or 'undeclared units'}"
            f" and volumes in {_plural(out['volume']['name']) or 'undeclared units'}, as the model declares them."
        )
    else:
        out["say"] = "The model declares no length unit; read its numbers as unit-less and say so."
    return out


def unit_scale(model: ifcopenshell.file) -> float:
    """Metres per model length unit (0.001 for millimetres, 1.0 when undeclared)."""
    return ifcopenshell.util.unit.calculate_unit_scale(model)


def _decimals_for(scale: float) -> int:
    """Decimal places that keep a tenth of a millimetre in a unit of `scale` metres."""
    return max(0, 4 + round(math.log10(scale))) if scale > 0 else 4


def feet_and_inches(metres: float) -> str:
    """A length as feet and inches to the nearest sixteenth, like 53' 0 5/16"."""
    sixteenths = round(abs(metres) / 0.0254 * 16)
    feet, rest = divmod(sixteenths, 12 * 16)
    inches, frac = divmod(rest, 16)
    if frac:
        g = math.gcd(frac, 16)
        text = f"{feet}' {inches} {frac // g}/{16 // g}\""
    else:
        text = f"{feet}' {inches}\""
    return ("-" if metres < 0 and sixteenths else "") + text


# ---------------------------------------------------------------- storeys


def element(model: ifcopenshell.file, element_id: int) -> ifcopenshell.entity_instance:
    """The element with this id, or a refusal that says it is not there."""
    try:
        found = model.by_id(int(element_id))
    except RuntimeError:
        found = None
    if found is None:
        raise ValueError(f"Element #{element_id} not found.")
    return found


def storey_of(element: ifcopenshell.entity_instance) -> dict[str, Any] | None:
    """The storey an element is on, however the model says so.

    A wall is usually CONTAINED in its storey (IfcRelContainedInSpatialStructure);
    a room (IfcSpace) is usually PART of it (IfcRelAggregates), and a door may
    fill an opening in a wall that is contained. This walks up whichever the
    model uses until it reaches an IfcBuildingStorey, and says the path.
    """
    path: list[str] = []
    current = element
    for _ in range(32):
        if current.is_a("IfcBuildingStorey") and current is not element:
            return {
                "id": current.id(),
                "name": current.Name,
                "elevation": getattr(current, "Elevation", None),
                "via": path,
            }
        step = None
        container = ifcopenshell.util.element.get_container(current)
        if container is not None:
            step, current = "contained in (IfcRelContainedInSpatialStructure)", container
        else:
            aggregate = ifcopenshell.util.element.get_aggregate(current)
            if aggregate is not None:
                step, current = "part of (IfcRelAggregates)", aggregate
            else:
                parent = ifcopenshell.util.element.get_parent(current)
                if parent is None:
                    return None
                step, current = "inside", parent
        path.append(f"{step} #{current.id()} {current.is_a()}")
    return None


def _storey_z(storey: ifcopenshell.entity_instance) -> float | None:
    if not getattr(storey, "ObjectPlacement", None):
        return None
    return float(ifcopenshell.util.placement.get_local_placement(storey.ObjectPlacement)[2][3])


def find_storey(model: ifcopenshell.file, storey: str | int) -> ifcopenshell.entity_instance:
    """A storey by its id or its name (case and surrounding spaces ignored)."""
    text = str(storey).strip()
    storeys = model.by_type("IfcBuildingStorey")
    if text.lstrip("#").isdigit():
        wanted = int(text.lstrip("#"))
        for s in storeys:
            if s.id() == wanted:
                return s
    for s in storeys:
        if (s.Name or "").strip().lower() == text.lower():
            return s
    names = ", ".join(f'#{s.id()} "{s.Name}"' for s in storeys) or "none"
    raise ValueError(f"No storey {text!r}. The model's storeys: {names}.")


# ---------------------------------------------------------------- paging


def _check_page(offset: int, limit: int) -> tuple[int, int]:
    if offset < 0:
        raise ValueError("offset is where to start, 0 or more")
    if limit < 1:
        raise ValueError("limit is how many to list, 1 or more")
    return offset, min(limit, MAX_LIMIT)


def page_of(items: list[Any], offset: int, limit: int, chars: int = PAGE_CHARS) -> tuple[list[Any], dict[str, Any]]:
    """Items[offset:offset+limit], cut short where their JSON would pass `chars`.

    Returns the page and what was withheld: ``total``, ``offset``, ``listed``,
    ``withheld`` and ``next_offset`` (None when nothing is left). At least one
    item is listed when any is left, however long it is.
    """
    offset, limit = _check_page(offset, limit)
    page: list[Any] = []
    spent = 2
    for item in items[offset : offset + limit]:
        size = len(json.dumps(item, default=str)) + 1
        if page and spent + size > chars:
            break
        page.append(item)
        spent += size
    end = offset + len(page)
    info = {
        "total": len(items),
        "offset": offset,
        "listed": len(page),
        "withheld": len(items) - len(page),
        "next_offset": end if end < len(items) else None,
    }
    return page, info


def _how_more(tool: str, info: dict[str, Any], also: str = "") -> str | None:
    if info["withheld"] == 0:
        return None
    nxt = info["next_offset"]
    rest = f"Call {tool} again with offset {nxt} for the next ones" if nxt is not None else f"Call {tool} again with a smaller offset"
    return f"{info['listed']} of {info['total']} listed; {info['withheld']} withheld. {rest}{also}."


def _brief(element: ifcopenshell.entity_instance) -> dict[str, Any]:
    out: dict[str, Any] = {"id": element.id(), "type": element.is_a()}
    name = getattr(element, "Name", None)
    if name:
        out["name"] = name
    return out


def _class_check(model: ifcopenshell.file, element_type: str) -> str:
    element_type = element_type.strip()
    if not element_type:
        return ""
    try:
        return ifcopenshell.ifcopenshell_wrapper.schema_by_name(model.schema_identifier).declaration_by_name(element_type).name()
    except Exception:
        raise ValueError(f"{element_type!r} is not an IFC class in {model.schema}; ifc_summary lists the classes this model holds") from None


# ---------------------------------------------------------------- the tree


def tree(model: ifcopenshell.file, storey: str = "", element_type: str = "", offset: int = 0, limit: int = DEFAULT_LIMIT) -> dict[str, Any]:
    """The spatial structure, with the elements in each place listed a page at a time.

    Every place (site, building, storey, space) is given in full, with how many
    elements of each IFC class it holds (``element_counts``). Its elements are
    listed in tree order, ``limit`` at a time from ``offset``, filtered to
    ``element_type`` when given; ``storey`` (an id or a name) gives that storey
    alone. A storey also says its ``elevation`` as stored and its placed ``z``,
    both in the model's length unit.
    """
    offset, limit = _check_page(offset, limit)
    element_type = _class_check(model, element_type)
    projects = model.by_type("IfcProject")
    if not projects:
        raise ValueError("The model holds no IfcProject")
    roots = [find_storey(model, storey)] if str(storey).strip() else list(projects)

    flat: list[tuple[dict[str, Any], dict[str, Any]]] = []

    def node_for(place: ifcopenshell.entity_instance) -> dict[str, Any]:
        node = _brief(place)
        if place.is_a("IfcBuildingStorey"):
            node["elevation"] = getattr(place, "Elevation", None)
            node["z"] = _storey_z(place)
        children = [node_for(c) for rel in getattr(place, "IsDecomposedBy", []) or [] for c in rel.RelatedObjects]
        contained = [e for rel in getattr(place, "ContainsElements", []) or [] for e in rel.RelatedElements]
        if contained:
            node["element_counts"] = dict(Counter(e.is_a() for e in contained).most_common())
            for e in contained:
                if not element_type or e.is_a(element_type):
                    flat.append((node, _brief(e)))
        if children:
            node["children"] = children
        return node

    built = [node_for(r) for r in roots]
    page, info = page_of([b for _, b in flat], offset, limit)
    listed = {id(b) for b in page}
    for node, b in flat:
        if id(b) in listed:
            node.setdefault("elements", []).append(b)
    filt = f", element_type {element_type!r}" if element_type else ""
    out: dict[str, Any] = {
        "tree": built[0] if len(built) == 1 else built,
        "elements_page": info,
        "units": units(model)["say"],
    }
    how = _how_more("ifc_tree", info, f" (same storey{filt})" if str(storey).strip() else (f" (same{filt})" if filt else ""))
    if how:
        out["more"] = how + " Narrow it with `storey` (an id or a name) and `element_type` (an IFC class such as IfcDoor)."
    return out


# ---------------------------------------------------------------- select


def select(model: ifcopenshell.file, query: str, offset: int = 0, limit: int = DEFAULT_LIMIT) -> dict[str, Any]:
    """Elements an IfcOpenShell selector matches, sorted by id, a page at a time.

    The selector also filters by place: ``IfcDoor, location = "Level 1"`` is
    the doors on the storey named Level 1 (a storey, a space or a building, by
    its name), and ``IfcWall, IfcDoor, location = "Level 2"`` both kinds on it.
    """
    offset, limit = _check_page(offset, limit)
    found = sorted(ifcopenshell.util.selector.filter_elements(model, query), key=lambda e: e.id())
    page, info = page_of([_brief(e) for e in found], offset, limit)
    out: dict[str, Any] = {"query": query, "elements": page, "page": info}
    out["counts"] = dict(Counter(e.is_a() for e in found).most_common())
    how = _how_more("ifc_select", info, " (the same query)")
    if how:
        out["more"] = how + ' Narrow the query by place with location = "<storey name>".'
    return out


# ---------------------------------------------------------------- relations

#: The lists in ifcquery's relations answer that can run long, by their path.
_LISTS = (
    ("children", "contained"),
    ("children", "parts"),
    ("children", "components"),
    ("children", "openings"),
    ("type_relationship", "occurrences"),
    ("groups",),
    ("systems",),
    ("zones",),
    ("referenced_structures",),
    ("connections", "connected_to"),
    ("connections", "connected_from"),
    ("connections", "ports"),
)


def _get(d: dict[str, Any], path: tuple[str, ...]) -> Any:
    for k in path:
        if not isinstance(d, dict) or k not in d:
            return None
        d = d[k]
    return d


def _set(d: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    for k in path[:-1]:
        d = d[k]
    d[path[-1]] = value


def relations(full: dict[str, Any] | list[Any], model: ifcopenshell.file, element_type: str = "", offset: int = 0, limit: int = DEFAULT_LIMIT) -> dict[str, Any] | list[Any]:
    """ifcquery's relations answer, with each long list paged and filtered.

    `full` is that answer. Each list (what a storey contains, a type's
    occurrences ...) keeps the items of ``element_type`` when given, from
    ``offset``, ``limit`` at a time; the reply's ``lists`` gives each one's
    total and what was withheld. The flat ``elements`` list, which repeats
    everything above it, is dropped when any list was cut.
    """
    offset, limit = _check_page(offset, limit)
    element_type = _class_check(model, element_type)
    if not isinstance(full, dict):  # traverse "up": a short chain to the project
        return full
    out = json.loads(json.dumps(full, default=str))
    budget = max(2000, PAGE_CHARS // max(1, sum(1 for p in _LISTS if _get(out, p))))
    lists: dict[str, Any] = {}
    cut = False
    for path in _LISTS:
        items = _get(out, path)
        if not isinstance(items, list):
            continue
        if element_type:
            items = [i for i in items if isinstance(i, dict) and _is_class(model, i, element_type)]
        page, info = page_of(items, offset, limit, chars=budget)
        if element_type:
            info["of_type"] = element_type
        _set(out, path, page)
        lists[".".join(path)] = info
        cut = cut or info["withheld"] > 0
    if "elements" in out and (cut or element_type):
        out["elements_total"] = len(out.pop("elements"))
    out["lists"] = lists
    if cut:
        longest = max(lists.items(), key=lambda kv: kv[1]["withheld"])
        nxt = longest[1]["next_offset"]
        out["more"] = (
            f"Some lists were cut (`lists` says which). Call ifc_relations again with offset {nxt} for the next ones, "
            "or narrow it with element_type (an IFC class such as IfcDoor)."
        )
    return out


def _is_class(model: ifcopenshell.file, item: dict[str, Any], element_type: str) -> bool:
    try:
        return model.by_id(int(item["id"])).is_a(element_type)
    except Exception:
        return False


# ---------------------------------------------------------------- info


def info_extra(model: ifcopenshell.file, element: ifcopenshell.entity_instance) -> dict[str, Any]:
    """What ifc_info adds to ifcquery's answer: the storey, and the units."""
    extra: dict[str, Any] = {"units": units(model)["say"]}
    if element.is_a("IfcProduct"):
        found = storey_of(element)
        extra["storey"] = found if found else None
    return extra


# ---------------------------------------------------------------- extent


def _geometry_settings():
    import ifcopenshell.geom

    s = ifcopenshell.geom.settings()
    s.set("use-world-coords", True)
    # An opening only removes material, so subtracting it cannot widen a box;
    # skipping it saves the boolean work (a large model has hundreds of openings).
    s.set("disable-opening-subtractions", True)
    return s


#: IfcOpenShell's geometry library for meshes. Measured on a large model, one
#: thread: its doors took 86 s with the default library and 1.8 s with this
#: one; the whole model, 25 s.
GEOMETRY_LIBRARY = os.environ.get("FLO2_IFC_GEOMETRY_LIBRARY", "hybrid-cgal-simple-opencascade")


#: Elements measured by one geometry iterator. IfcOpenShell's iterator keeps
#: what it has made until it is dropped: one iterator over a whole large
#: model peaked at 1.18 GB, over flo2's 1 GiB cap, and batches
#: of 100 peaked at 390 MB for 25% more time (measured 2026-10-23).
GEOMETRY_BATCH = int(os.environ.get("FLO2_IFC_GEOMETRY_BATCH", "250"))


def _boxes(model: ifcopenshell.file, elements: list[ifcopenshell.entity_instance], deadline: float) -> tuple[dict[int, tuple[list[float], list[float]]], bool]:
    """World-space bounding boxes in METRES, by element id, and whether time ran out."""
    import ifcopenshell.geom
    import numpy as np

    boxes: dict[int, tuple[list[float], list[float]]] = {}
    settings = _geometry_settings()
    for first in range(0, len(elements), GEOMETRY_BATCH):
        if time.monotonic() > deadline:
            return boxes, True
        it = ifcopenshell.geom.iterator(settings, model, 1, include=elements[first : first + GEOMETRY_BATCH], geometry_library=GEOMETRY_LIBRARY)
        if not it.initialize():
            continue
        while True:
            shape = it.get()
            verts = np.asarray(shape.geometry.verts, dtype=float).reshape(-1, 3)
            if len(verts):
                boxes[shape.id] = (verts.min(axis=0).tolist(), verts.max(axis=0).tolist())
            more = it.next()
            if more and time.monotonic() > deadline:
                return boxes, True
            if not more:
                break
        del it
    return boxes, False


def _box_view(lo: list[float], hi: list[float], to_model: float, decimals: int) -> dict[str, Any]:
    r = lambda v: round(v * to_model, decimals)  # noqa: E731
    return {
        "min": [r(v) for v in lo],
        "max": [r(v) for v in hi],
        "size": [r(b - a) for a, b in zip(lo, hi)],
        "bottom": r(lo[2]),
        "top": r(hi[2]),
    }


def extent(
    model: ifcopenshell.file,
    selector: str = "",
    element_id: int = 0,
    offset: int = 0,
    limit: int = 50,
    seconds: float | None = None,
    datum: str = "",
    model_path: str | None = None,
    edits: int = 0,
) -> dict[str, Any]:
    """How far elements reach, measured from their geometry: their bounding boxes.

    Name one element by ``element_id``, or many with a ``selector`` (such as
    ``IfcRoof`` or ``IfcDoor, location = "Level 1"``). The reply gives:

    - ``overall``: the box around them all, with its ``bottom`` and ``top``;
    - ``highest`` and ``lowest``: the element that reaches highest, and lowest;
    - ``elements``: each one's box, a page at a time;

    in the model's length unit, with ``overall`` also in metres and in feet and
    inches. ``z`` is height in the model's world coordinates, the frame each
    storey's placed ``z`` (ifc_tree) is in. The boxes are aligned with the
    model's axes, so a rotated element's box is larger than the element. An
    element with no 3D shape is counted in ``no_shape``. With a ``datum`` (a
    storey's name or id, or a height in the model's unit), ``above_datum``
    gives the bottom and top above it, and each element's row its own. The
    reply's ``cite`` names the model file, its sha256 and the elements'
    GlobalIds, for a number kept in a calculation. Reads only.
    """
    offset, limit = _check_page(offset, limit)
    started = time.monotonic()
    budget = SECONDS if seconds is None else seconds
    if element_id:
        targets = [element(model, element_id)]
        asked = f"#{element_id}"
    elif selector.strip():
        targets = sorted(ifcopenshell.util.selector.filter_elements(model, selector), key=lambda e: e.id())
        asked = selector
    else:
        raise ValueError("Name what to measure: `selector` (such as \"IfcRoof\") or `element_id`.")
    shaped = [e for e in targets if e.is_a("IfcProduct") and getattr(e, "Representation", None)]
    boxes, stopped = _boxes(model, shaped, started + budget)
    scale = unit_scale(model)
    to_model = 1.0 / scale
    decimals = _decimals_for(scale)
    u = units(model)
    length_symbol = u["length"]["symbol"] or "model units"

    rows = []
    for e in shaped:
        b = boxes.get(e.id())
        if b:
            row = _brief(e)
            row.update(_box_view(b[0], b[1], to_model, decimals))
            rows.append(row)
    out: dict[str, Any] = {
        "asked": asked,
        "length_unit": length_symbol,
        "matched": len(targets),
        "measured": len(rows),
        "no_shape": len(targets) - len(shaped),
    }
    if rows:
        lo = [min(boxes[r["id"]][0][i] for r in rows) for i in range(3)]
        hi = [max(boxes[r["id"]][1][i] for r in rows) for i in range(3)]
        out["overall"] = _box_view(lo, hi, to_model, decimals)
        out["overall_metres"] = _box_view(lo, hi, 1.0, 4)
        out["overall_feet_inches"] = {
            "bottom": feet_and_inches(lo[2]),
            "top": feet_and_inches(hi[2]),
            "size": [feet_and_inches(b - a) for a, b in zip(lo, hi)],
        }
        top = max(rows, key=lambda r: r["top"])
        low = min(rows, key=lambda r: r["bottom"])
        out["highest"] = {k: top[k] for k in ("id", "type", "name", "top") if k in top}
        out["lowest"] = {k: low[k] for k in ("id", "type", "name", "bottom") if k in low}
    page, info = page_of(rows, offset, limit, chars=PAGE_CHARS // 2)
    out["elements"] = page
    out["page"] = info
    how = _how_more("ifc_extent", info, " (the same selector)")
    if how:
        out["more"] = how
    shaped_left = len(shaped) - len(boxes)
    if stopped:
        out["partial"] = True
        out["stopped"] = (
            f"Stopped after {budget:.0f} s with {len(boxes)} of {len(shaped)} shapes measured, so `overall`, `highest` and `lowest` "
            f"cover only those. Narrow the selector (by class, or location = \"<storey>\") to measure the rest."
        )
    elif shaped_left > 0:
        out["unmeasured"] = shaped_left
    if datum.strip() and rows:
        base_model, said = _datum(model, datum)
        base = base_model * scale
        for r in page:
            r["above_datum"] = {"bottom": round(r["bottom"] - base_model, decimals), "top": round(r["top"] - base_model, decimals)}
        out["above_datum"] = {
            "datum": said,
            "datum_z": round(base_model, decimals),
            "bottom": {"model": round((lo[2] - base) * to_model, decimals), "metres": round(lo[2] - base, 4), "feet_inches": feet_and_inches(lo[2] - base)},
            "top": {"model": round((hi[2] - base) * to_model, decimals), "metres": round(hi[2] - base, 4), "feet_inches": feet_and_inches(hi[2] - base)},
            "unit": length_symbol,
        }
    from flo2_ifc import geometry

    measured = [e for e in shaped if e.id() in boxes]
    out["cite"] = geometry.cite(
        model,
        model_path,
        measured,
        asked,
        length_symbol,
        "Extent: the axis-aligned box around each element's triangle mesh (IfcOpenShell's geometry, world coordinates)"
        + ("; heights above the datum are the box's bottom and top less the datum's height." if datum.strip() else "."),
        edits,
    )
    out["seconds"] = round(time.monotonic() - started, 1)
    out["units"] = u["say"]
    return out


def _datum(model: ifcopenshell.file, datum: str) -> tuple[float, str]:
    """A datum's height in the model's length unit, and what it is: a number, or a storey's level."""
    text = str(datum).strip()
    try:
        return float(text), f"z = {float(text):g} (the model's length unit)"
    except ValueError:
        pass
    st = find_storey(model, text)
    z = _storey_z(st)
    if z is None:
        z = float(getattr(st, "Elevation", None) or 0.0)
        return z, f"storey #{st.id()} \"{st.Name}\", its stored Elevation (it has no placement)"
    return z, f"storey #{st.id()} \"{st.Name}\", its level (placed z)"


# ---------------------------------------------------------------- validate


class _Window(ifcopenshell.file):
    """The model, seen by ifcopenshell.validate as only the instances from
    `start` until the deadline. Everything but iteration is the model's own."""

    def __init__(self, base: ifcopenshell.file, start: int, deadline: float):  # noqa: D107 - no super(): it shares base's state
        self.__dict__.update(base.__dict__)
        self._start = start
        self._deadline = deadline
        self.reached = start
        self.first_seen = False
        self.on_first: Callable[[], None] | None = None

    def __iter__(self) -> Iterator[ifcopenshell.entity_instance]:
        for step_id in self.entity_names()[self._start :]:
            inst = self[step_id]
            if not self.first_seen:
                self.first_seen = True
                if self.on_first:
                    self.on_first()
            # At least one instance a call, so a check always moves on.
            if self.reached > self._start and time.monotonic() > self._deadline:
                return
            yield inst
            self.reached += 1


_GUID_RULE = "IfcRoot.UR1"


def _duplicate_guids(model: ifcopenshell.file) -> list[dict[str, Any]]:
    seen: dict[str, ifcopenshell.entity_instance] = {}
    issues = []
    for inst in model.by_type("IfcRoot"):
        g = inst.GlobalId
        if g in seen:
            issues.append({"level": "Error", "message": f"Rule {_GUID_RULE}: GlobalId {g} is used by both #{seen[g].id()} and #{inst.id()}; it should be unique"})
        else:
            seen[g] = inst
    return issues


def _rules_in_child(model: ifcopenshell.file, seconds: float) -> dict[str, Any]:
    """The schema's EXPRESS rules, run in a forked child so they can be
    stopped at `seconds`; they cannot be interrupted in this process."""
    import ifcopenshell.express.rule_executor as rule_executor
    import ifcopenshell.validate

    if not hasattr(os, "fork"):
        logger = ifcopenshell.validate.json_logger()
        rule_executor.run(model, logger)
        return {"checked": True, "issues": [{"level": s["level"], "message": s["message"]} for s in logger.statements]}
    read_end, write_end = os.pipe()
    pid = os.fork()
    if pid == 0:  # the child: run the rules, write what they found, and leave
        code = 0
        try:
            os.close(read_end)
            logger = ifcopenshell.validate.json_logger()
            rule_executor.run(model, logger)
            data = json.dumps([{"level": s["level"], "message": str(s["message"])} for s in logger.statements]).encode()
        except BaseException as e:  # noqa: BLE001 - the child reports any failure
            data = json.dumps({"failed": f"{type(e).__name__}: {e}"}).encode()
            code = 1
        try:
            with os.fdopen(write_end, "wb") as w:
                w.write(data)
        finally:
            os._exit(code)
    os.close(write_end)
    chunks: list[bytes] = []
    deadline = time.monotonic() + max(0.0, seconds)
    timed_out = False
    with os.fdopen(read_end, "rb", buffering=0) as r:
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                timed_out = True
                break
            ready, _, _ = _io_select.select([r], [], [], left)
            if not ready:
                continue
            chunk = r.read(65536)
            if not chunk:
                break
            chunks.append(chunk)
    if timed_out:
        try:
            os.kill(pid, 9)
        except ProcessLookupError:
            pass
    os.waitpid(pid, 0)
    if timed_out:
        return {"checked": False, "why": f"the rules did not finish within {seconds:.0f} s, the time left in this call, and were stopped; on a model this size they take longer than one call allows"}
    said = json.loads(b"".join(chunks) or b"[]")
    if isinstance(said, dict):
        return {"checked": False, "why": f"the rules could not run: {said.get('failed')}"}
    return {"checked": True, "issues": said}


def validate(model: ifcopenshell.file, express_rules: bool = False, start: int = 0, seconds: float | None = None) -> dict[str, Any]:
    """Check the model against the IFC schema, as much as one call's time allows.

    Checks each instance's attributes and inverses, in file order, from
    ``start``. If time runs out first, the reply says ``complete: false`` and
    gives ``next_start``: call again with it to check on from there. ``valid``
    is true when nothing checked SO FAR (this call) found an issue; only
    ``complete: true`` with ``valid: true`` means the whole model passed.
    Every call also checks that no GlobalId is used twice across the whole
    model. ``express_rules`` also runs the schema's rules once the instances
    are done, if time is left, and says when it was not.
    """
    import ifcopenshell.validate

    if start < 0:
        raise ValueError("start is an instance position, 0 or more")
    started = time.monotonic()
    budget = SECONDS if seconds is None else seconds
    total = len(model.entity_names())
    logger = ifcopenshell.validate.json_logger()
    window = _Window(model, start, started + budget)
    header_count: list[int] = []
    window.on_first = lambda: header_count.append(len(logger.statements))
    derived = ifcopenshell.ifcopenshell_wrapper.get_feature("use_attribute_value_derived")
    try:
        ifcopenshell.validate.validate(window, logger, express_rules=False)
    finally:
        # validate() restores this itself only when it runs to the end.
        ifcopenshell.ifcopenshell_wrapper.set_feature("use_attribute_value_derived", derived)
    statements = logger.statements
    if start > 0:
        # The header and applications were checked by the call from 0.
        statements = statements[header_count[0] :] if header_count else []
    issues = [{"level": s["level"], "message": str(s["message"])} for s in statements if _GUID_RULE not in str(s["message"])]
    issues = _duplicate_guids(model) + issues
    reached = window.reached
    complete = reached >= total
    out: dict[str, Any] = {
        "valid": not issues,
        "complete": complete,
        "issues": issues[:200],
        "checked": {"from": start, "to": reached, "of": total},
        "next_start": None if complete else reached,
    }
    if len(issues) > 200:
        out["issues_withheld"] = len(issues) - 200
    if not complete:
        out["note"] = (
            f"Partial: instances {start} to {reached} of {total} were checked in {budget:.0f} s. "
            f"Call ifc_validate again with start {reached} to check on; `valid` covers only what was checked."
        )
    if express_rules:
        left = budget - (time.monotonic() - started)
        if not complete:
            out["rules"] = {"checked": False, "why": "the rules run once every instance is checked; ask for them on the call that finishes"}
        elif left < 1:
            out["rules"] = {"checked": False, "why": "no time was left in this call; call again with start equal to the total and express_rules true"}
        else:
            rules = _rules_in_child(model, left)
            out["rules"] = {k: v for k, v in rules.items() if k != "issues"}
            if rules.get("issues"):
                out["issues"] = (out["issues"] + rules["issues"])[:200]
                out["valid"] = False
    out["seconds"] = round(time.monotonic() - started, 1)
    return out
