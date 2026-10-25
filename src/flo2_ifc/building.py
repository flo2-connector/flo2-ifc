"""The building steps: a model's ordinary parts, each made with ONE call.

flo2's design 1a97fc1f1de4d5d2, dec:a-chat-builds-a-models-ordinary-parts-with-one-call-each (accepted 2026-10-24).
A chat that built a 24-element shell through ifcmcp's ``ifc_shape`` and ``ifc_edit`` spent 250 calls, about 6 to 8
an element, and needed to know IFC to make each one: a polyline, an extrusion and a representation from the shape
builder; then ``root.create_entity``, ``geometry.assign_representation``, ``geometry.edit_object_placement``,
``classification.add_reference`` and ``spatial.assign_container``
(fact:building-a-models-shell-takes-about-6-helper-calls-per-element-2026-10-24).

Each step here takes what a person would say (a baseline or a footprint as points in plan, a height, a thickness, a
slope, all in the model's own length unit) and, in one call:

- creates the element (``root.create_entity``);
- gives it its shape with IfcOpenShell's own function for it (``geometry.add_wall_representation`` for a wall, with
  ``Clipping`` planes for a sloped or peaked top; ``geometry.add_slab_representation`` for a slab, a room's volume
  and a sloped roof plane, with its ``x_angle``), assigned with ``geometry.assign_representation``;
- puts it on its storey (``spatial.assign_container``; a room is part of its storey, ``aggregate.assign_object``;
  a roof plane is part of its roof);
- places it relative to that storey (``geometry.edit_object_placement``, from plan coordinates and the storey's
  level);
- links it to the design part it realizes (``classification.add_reference`` in the classification "flo2 design",
  the reference's identification being the part's id).

Nothing is hand-built where IfcOpenShell has a function for it. Every input is checked before the model is touched,
and the steps run in one IfcOpenShell transaction, so a refused or failed step leaves the model as it was.

The steps are additive: ifcmcp's ``ifc_edit`` and ``ifc_shape`` stay for anything unusual.
"""

from __future__ import annotations

import math
from typing import Any

import ifcopenshell
import ifcopenshell.api
import ifcopenshell.util.placement
import ifcopenshell.util.representation
import ifcopenshell.util.unit
import numpy as np
from ifcopenshell.util.data import Clipping
from shapely.geometry import Polygon, box
from shapely.geometry.polygon import orient

from flo2_ifc import reading

#: The classification a step's design-part link is made in.
DESIGN_CLASSIFICATION = "flo2 design"
WALL_ALIGN = ("centre", "left", "right")
SLAB_KINDS = {"floor": "FLOOR", "baseslab": "BASESLAB"}
ROOF_KINDS = {"shed": "SHED_ROOF", "gable": "GABLE_ROOF"}
#: The steepest roof plane a step makes, as rise over run (about 80 degrees).
MAX_SLOPE = 6.0


# ------------------------------------------------------------------ inputs


def _number(value: Any, name: str, *, positive: bool = False, at_least_zero: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a number, not {value!r}.")
    if positive and value <= 0:
        raise ValueError(f"{name} must be more than zero, not {value!r}.")
    if at_least_zero and value < 0:
        raise ValueError(f"{name} must be zero or more, not {value!r}.")
    return float(value)


def _point(value: Any, name: str) -> tuple[float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(f"{name} is a point in plan, [x, y] in the model's length unit, not {value!r}.")
    return (_number(value[0], f"{name}'s x"), _number(value[1], f"{name}'s y"))


def _footprint(value: Any, name: str = "footprint") -> list[tuple[float, float]]:
    """A footprint's corners, in order, as a closed outline that does not cross itself, counter-clockwise."""
    if not isinstance(value, (list, tuple)) or len(value) < 3:
        raise ValueError(f"{name} needs at least 3 corners, each [x, y] in the model's length unit, in order around it.")
    points = [_point(p, f"{name}'s corner {i + 1}") for i, p in enumerate(value)]
    if len(points) > 3 and points[0] == points[-1]:
        points = points[:-1]
    for i, p in enumerate(points):
        if p == points[i - 1]:
            raise ValueError(f"{name}'s corners {i or len(points)} and {i + 1} are the same point.")
    (x0, y0), (x1, y1) = points[0], points[1]
    if all(abs((x1 - x0) * (y - y0) - (y1 - y0) * (x - x0)) < 1e-12 for x, y in points[2:]):
        raise ValueError(f"{name} encloses no area: its corners lie on one line.")
    shape = Polygon(points)
    if not shape.is_valid or shape.area <= 0:
        raise ValueError(f"{name} crosses itself: give its corners in order around it.")
    return list(orient(shape, 1.0).exterior.coords)[:-1]


def _storey(model: ifcopenshell.file, storey: Any) -> ifcopenshell.entity_instance:
    if storey is None or str(storey).strip() == "":
        raise ValueError("storey is the storey it stands on, by name or id (ifc_tree names them).")
    return reading.find_storey(model, storey)


def _text(value: Any, name: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{name} must be text, not {value!r}.")
    return value.strip()


# ------------------------------------------------------------------ the model


def _body(model: ifcopenshell.file) -> ifcopenshell.entity_instance:
    """The 3D Body context shapes are drawn in, made if the model has none (context.add_context)."""
    body = ifcopenshell.util.representation.get_context(model, "Model", "Body", "MODEL_VIEW")
    if body is not None:
        return body
    parent = ifcopenshell.util.representation.get_context(model, "Model")
    if parent is None:
        parent = ifcopenshell.api.run("context.add_context", model, context_type="Model")
    return ifcopenshell.api.run("context.add_context", model, context_type="Model", context_identifier="Body", target_view="MODEL_VIEW", parent=parent)


def _level(storey: ifcopenshell.entity_instance) -> float:
    """The storey's placed height, in the model's length unit (the z ifc_tree gives)."""
    if not getattr(storey, "ObjectPlacement", None):
        return float(storey.Elevation or 0.0)
    return float(ifcopenshell.util.placement.get_local_placement(storey.ObjectPlacement)[2][3])


def _place(model, product, origin: tuple[float, float, float], x_axis: tuple[float, float], tilt: float = 0.0) -> None:
    """Place `product` at `origin` (model units, world), its x along `x_axis` in plan, turned up by `tilt` radians
    about that x. edit_object_placement makes the placement relative to the storey (or roof) it is in."""
    ux, uy = x_axis
    c, s = math.cos(tilt), math.sin(tilt)
    m = np.eye(4)
    # Columns: x along the line; y to its left, turned up by tilt; z square to both.
    m[:3, 0] = (ux, uy, 0.0)
    m[:3, 1] = (-uy * c, ux * c, s)
    m[:3, 2] = (uy * s, -ux * s, c)
    m[:3, 3] = origin
    ifcopenshell.api.run("geometry.edit_object_placement", model, product=product, matrix=m, is_si=False)


def _link(model, product, realizes: str) -> dict[str, str] | None:
    """Link `product` to the design part it realizes: a reference in the "flo2 design" classification."""
    if not realizes:
        return None
    found = [c for c in model.by_type("IfcClassification") if c.Name == DESIGN_CLASSIFICATION]
    classification = found[0] if found else ifcopenshell.api.run("classification.add_classification", model, classification=DESIGN_CLASSIFICATION)
    ifcopenshell.api.run("classification.add_reference", model, products=[product], identification=realizes, classification=classification)
    return {"identification": realizes, "classification": DESIGN_CLASSIFICATION}


def _made(element: ifcopenshell.entity_instance) -> dict[str, Any]:
    return {"id": element.id(), "class": element.is_a(), "global_id": element.GlobalId, "name": element.Name}


def _in_one_transaction(model: ifcopenshell.file, build):
    """Run `build` so that, if it fails part way, the model is put back as it was."""
    model.begin_transaction()
    try:
        out = build()
    except BaseException:
        model.end_transaction()
        model.undo()
        raise
    model.end_transaction()
    return out


def _reply(model, element, storey, linked, shape: str) -> dict[str, Any]:
    unit = reading.units(model)["length"]
    return {
        "made": _made(element),
        "storey": {"id": storey.id(), "name": storey.Name, "level": round(_level(storey), 6)},
        "realizes": linked,
        "shape": shape,
        "length_unit": unit.get("symbol") or unit.get("name"),
        "check": f"ifc_extent with element_id {element.id()} says where it reaches; ifc_validate checks the model.",
    }


def _say(n: float) -> str:
    return f"{n:g}"


# ------------------------------------------------------------------ the steps


def add_wall(
    model: ifcopenshell.file,
    storey: Any,
    start: Any,
    end: Any,
    height: Any,
    thickness: Any,
    height_at_end: Any = None,
    peak_height: Any = None,
    base_offset: Any = 0.0,
    align: str = "centre",
    name: str = "",
    realizes: str = "",
) -> dict[str, Any]:
    """A wall along a baseline from `start` to `end`, standing on the storey's level (plus `base_offset`), `height`
    high (rising to `height_at_end` at the end, or to `peak_height` halfway), `thickness` thick, centred on the line
    or on its left or right (looking from start to end). Lengths in the model's unit."""
    s, e = _point(start, "start"), _point(end, "end")
    h1 = _number(height, "height", positive=True)
    t = _number(thickness, "thickness", positive=True)
    lift = _number(base_offset, "base_offset")
    h2 = None if height_at_end is None else _number(height_at_end, "height_at_end", positive=True)
    hp = None if peak_height is None else _number(peak_height, "peak_height", positive=True)
    if h2 is not None and hp is not None:
        raise ValueError("A wall's top slopes (height_at_end) or peaks in the middle (peak_height), not both.")
    if hp is not None and hp <= h1:
        raise ValueError(f"peak_height ({_say(hp)}) must be higher than height ({_say(h1)}): the top rises from both ends to it.")
    side = (_text(align, "align") or "centre").lower()
    if side == "center":
        side = "centre"
    if side not in WALL_ALIGN:
        raise ValueError(f"align is centre, left or right (the side of the line the wall stands on, looking from start to end), not {align!r}.")
    if s == e:
        raise ValueError("The wall's start and end are the same point: give two points in plan.")
    level = _storey(model, storey)
    name, realizes = _text(name, "name"), _text(realizes, "realizes")

    dx, dy = e[0] - s[0], e[1] - s[1]
    length = math.hypot(dx, dy)
    scale = ifcopenshell.util.unit.calculate_unit_scale(model)  # metres per model unit
    top = max(h1, h2 or h1, hp or h1)
    # The removed side of each clipping plane is above it (its normal points up), in the wall's own frame, in metres.
    clippings = []
    if h2 is not None and h2 != h1:
        clippings.append(Clipping(location=(0.0, 0.0, h1 * scale), normal=_unit((-(h2 - h1), 0.0, length))))
    if hp is not None:
        half = length / 2
        clippings.append(Clipping(location=(0.0, 0.0, h1 * scale), normal=_unit((-(hp - h1), 0.0, half))))
        clippings.append(Clipping(location=(length * scale, 0.0, h1 * scale), normal=_unit((hp - h1, 0.0, half))))
    offset = {"centre": -t / 2, "left": 0.0, "right": -t}[side]

    def build():
        wall = ifcopenshell.api.run("root.create_entity", model, ifc_class="IfcWall", name=name or None)
        rep = ifcopenshell.api.run(
            "geometry.add_wall_representation",
            model,
            context=_body(model),
            length=length * scale,
            height=top * scale,
            thickness=t * scale,
            offset=offset * scale,
            clippings=clippings,
        )
        ifcopenshell.api.run("geometry.assign_representation", model, product=wall, representation=rep)
        ifcopenshell.api.run("spatial.assign_container", model, products=[wall], relating_structure=level)
        _place(model, wall, (s[0], s[1], _level(level) + lift), (dx / length, dy / length))
        return wall, _link(model, wall, realizes)

    wall, linked = _in_one_transaction(model, build)
    if h2 is not None and h2 != h1:
        tops = f"{_say(h1)} high at its start rising to {_say(h2)} at its end"
    elif hp is not None:
        tops = f"{_say(h1)} high at its ends rising to {_say(hp)} in the middle"
    else:
        tops = f"{_say(h1)} high"
    return _reply(model, wall, level, linked, f"a wall {_say(round(length, 6))} long, {_say(t)} thick, {tops}, {side} on its baseline")


def add_slab(
    model: ifcopenshell.file,
    storey: Any,
    footprint: Any,
    thickness: Any,
    top_offset: Any = 0.0,
    kind: str = "floor",
    name: str = "",
    realizes: str = "",
) -> dict[str, Any]:
    """A slab over a footprint, its top at the storey's level (plus `top_offset`), `thickness` deep below it."""
    corners = _footprint(footprint)
    t = _number(thickness, "thickness", positive=True)
    raise_by = _number(top_offset, "top_offset")
    what = (_text(kind, "kind") or "floor").lower()
    if what not in SLAB_KINDS:
        raise ValueError(f"kind is {' or '.join(SLAB_KINDS)}, not {kind!r} (a roof is ifc_add_roof's).")
    level = _storey(model, storey)
    name, realizes = _text(name, "name"), _text(realizes, "realizes")
    scale = ifcopenshell.util.unit.calculate_unit_scale(model)
    x0, y0 = corners[0]
    local = [((x - x0) * scale, (y - y0) * scale) for x, y in corners]

    def build():
        slab = ifcopenshell.api.run("root.create_entity", model, ifc_class="IfcSlab", name=name or None, predefined_type=SLAB_KINDS[what])
        rep = ifcopenshell.api.run("geometry.add_slab_representation", model, context=_body(model), depth=t * scale, direction_sense="NEGATIVE", polyline=local + [local[0]])
        ifcopenshell.api.run("geometry.assign_representation", model, product=slab, representation=rep)
        ifcopenshell.api.run("spatial.assign_container", model, products=[slab], relating_structure=level)
        _place(model, slab, (x0, y0, _level(level) + raise_by), (1.0, 0.0))
        return slab, _link(model, slab, realizes)

    slab, linked = _in_one_transaction(model, build)
    area = Polygon(corners).area
    return _reply(model, slab, level, linked, f"a {what} slab of {_say(round(area, 6))} square units in plan, {_say(t)} thick, its top {_say(raise_by)} above the storey's level")


def add_space(
    model: ifcopenshell.file,
    storey: Any,
    footprint: Any,
    height: Any,
    floor_offset: Any = 0.0,
    name: str = "",
    long_name: str = "",
    realizes: str = "",
) -> dict[str, Any]:
    """A room (IfcSpace) over a footprint, from the storey's level (plus `floor_offset`) up `height`, part of its
    storey as rooms are."""
    corners = _footprint(footprint)
    h = _number(height, "height", positive=True)
    lift = _number(floor_offset, "floor_offset")
    level = _storey(model, storey)
    name, long_name, realizes = _text(name, "name"), _text(long_name, "long_name"), _text(realizes, "realizes")
    scale = ifcopenshell.util.unit.calculate_unit_scale(model)
    x0, y0 = corners[0]
    local = [((x - x0) * scale, (y - y0) * scale) for x, y in corners]

    def build():
        space = ifcopenshell.api.run("root.create_entity", model, ifc_class="IfcSpace", name=name or None, predefined_type="INTERNAL")
        if long_name:
            ifcopenshell.api.run("attribute.edit_attributes", model, product=space, attributes={"LongName": long_name})
        rep = ifcopenshell.api.run("geometry.add_slab_representation", model, context=_body(model), depth=h * scale, polyline=local + [local[0]])
        ifcopenshell.api.run("geometry.assign_representation", model, product=space, representation=rep)
        ifcopenshell.api.run("aggregate.assign_object", model, products=[space], relating_object=level)
        _place(model, space, (x0, y0, _level(level) + lift), (1.0, 0.0))
        return space, _link(model, space, realizes)

    space, linked = _in_one_transaction(model, build)
    area = Polygon(corners).area
    return _reply(model, space, level, linked, f"a room of {_say(round(area, 6))} square units in plan, {_say(h)} high")


def add_roof(
    model: ifcopenshell.file,
    storey: Any,
    footprint: Any,
    kind: str,
    eave_height: Any,
    slope: Any,
    thickness: Any,
    overhang: Any = 0.0,
    name: str = "",
    realizes: str = "",
) -> dict[str, Any]:
    """A roof over a footprint (IfcRoof, contained in its storey, made of one or two sloped IfcSlab planes):

    - "shed": one plane, its low eave along the footprint's FIRST side (its first two corners), rising across it;
    - "gable": two planes, eaves along the first side and the side across from it, meeting at a ridge parallel to
      the first side, halfway across.

    `eave_height` is the underside's height above the storey's level where it meets the footprint's eave side (the
    top of the wall); `slope` is rise over run (0.25 for 3 in 12); `thickness` is measured square to the plane; the
    planes reach `overhang` beyond the footprint all round."""
    corners = _footprint(footprint)
    what = (_text(kind, "kind") or "").lower()
    if what not in ROOF_KINDS:
        raise ValueError(f"kind is shed or gable, not {kind!r}.")
    eave = _number(eave_height, "eave_height")
    rise = _number(slope, "slope", positive=True)
    if rise > MAX_SLOPE:
        raise ValueError(f"slope is rise over run (0.25 for 3 in 12), at most {MAX_SLOPE:g}; {rise:g} is steeper than a roof plane.")
    t = _number(thickness, "thickness", positive=True)
    out_by = _number(overhang, "overhang", at_least_zero=True)
    level = _storey(model, storey)
    name, realizes = _text(name, "name"), _text(realizes, "realizes")
    scale = ifcopenshell.util.unit.calculate_unit_scale(model)
    angle = math.atan(rise)

    # The roof's frame: x along the footprint's first side, y across it, toward the inside.
    (ax, ay), (bx, by) = _first_side(footprint, corners)
    run = math.hypot(bx - ax, by - ay)
    ux, uy = (bx - ax) / run, (by - ay) / run
    vx, vy = -uy, ux
    in_frame = [((x - ax) * ux + (y - ay) * uy, (x - ax) * vx + (y - ay) * vy) for x, y in corners]
    plan = Polygon(in_frame)
    across_min, across_max = plan.bounds[1], plan.bounds[3]
    covered = plan.buffer(out_by, join_style=2) if out_by else plan
    # Each plane: the part of the plan it covers (in the roof's frame), where its eave is, and which way it rises.
    if what == "shed":
        planes = [(covered, across_min, +1)]
    else:
        ridge = (across_min + across_max) / 2
        lo, hi = covered.bounds[0] - 1, covered.bounds[2] + 1
        planes = [
            (covered.intersection(box(lo, covered.bounds[1] - 1, hi, ridge)), across_min, +1),
            (covered.intersection(box(lo, ridge, hi, covered.bounds[3] + 1)), across_max, -1),
        ]
    for part, _, _ in planes:
        if not isinstance(part, Polygon) or part.is_empty:
            raise ValueError("The footprint does not split into two roof planes at its ridge: give a simpler outline for a gable.")

    def build():
        roof = ifcopenshell.api.run("root.create_entity", model, ifc_class="IfcRoof", name=name or None, predefined_type=ROOF_KINDS[what])
        ifcopenshell.api.run("spatial.assign_container", model, products=[roof], relating_structure=level)
        z0 = _level(level)
        _place(model, roof, (ax, ay, z0), (ux, uy))
        made = []
        for i, (part, eave_at, way) in enumerate(planes):
            # The plane's own frame: its origin on its eave line (where the plan is lowest), x along the eave, y up
            # the slope in plan. add_slab_representation takes the plan outline in that frame and x_angle, and
            # slopes it; the placement turns it up by the same angle.
            low = part.bounds[1] if way > 0 else part.bounds[3]
            outline = [((x if way > 0 else -x), (y - low) * way) for x, y in orient(part, 1.0).exterior.coords[:-1]]
            origin_x, origin_y = ax + (low * vx), ay + (low * vy)
            z = z0 + eave + (low - eave_at) * way * rise
            plane = ifcopenshell.api.run("root.create_entity", model, ifc_class="IfcSlab", name=f"{name or 'Roof'} plane {i + 1}" if len(planes) > 1 else (name or "Roof plane"), predefined_type="ROOF")
            rep = ifcopenshell.api.run(
                "geometry.add_slab_representation",
                model,
                context=_body(model),
                depth=t * scale,
                x_angle=angle,
                polyline=[(x * scale, y * scale) for x, y in outline + [outline[0]]],
            )
            ifcopenshell.api.run("geometry.assign_representation", model, product=plane, representation=rep)
            ifcopenshell.api.run("aggregate.assign_object", model, products=[plane], relating_object=roof)
            _place(model, plane, (origin_x, origin_y, z), (ux * way, uy * way), tilt=angle)
            made.append(_made(plane))
        return roof, made, _link(model, roof, realizes)

    roof, made, linked = _in_one_transaction(model, build)
    out = _reply(
        model,
        roof,
        level,
        linked,
        f"a {what} roof of {len(made)} plane(s), rising {_say(rise)} a unit across from its eave{'s' if what == 'gable' else ''} at {_say(eave)} above the storey's level, {_say(t)} thick, overhanging {_say(out_by)}",
    )
    out["made"]["planes"] = made
    out["check"] = f"ifc_extent with element_id {made[0]['id']} (a plane) says where it reaches; ifc_validate checks the model."
    return out


def _first_side(raw: Any, corners: list[tuple[float, float]]) -> tuple[tuple[float, float], tuple[float, float]]:
    """The footprint's first side as the person gave it (its first two corners), whatever way round it was given."""
    a, b = _point(raw[0], "footprint's corner 1"), _point(raw[1], "footprint's corner 2")
    # The outline was turned counter-clockwise, so the inside is on the left of a side taken in that order.
    i, j = corners.index(a), corners.index(b)
    return (a, b) if (i + 1) % len(corners) == j else (b, a)


def _unit(v: tuple[float, float, float]) -> tuple[float, float, float]:
    n = math.sqrt(sum(c * c for c in v))
    return (v[0] / n, v[1] / n, v[2] / n)
