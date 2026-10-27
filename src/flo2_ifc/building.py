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
  level), reading the plan points in the frame the call names (``frame``, below);
- links it to the design part it realizes (``classification.add_reference`` in the classification "flo2 design",
  the reference's identification being the part's id).

THE FRAME (since 0.8.0; dec:building-steps-take-a-frame-and-measures-cut-openings, from
fact:building-steps-take-plan-points-in-world-coordinates-not-the-storeys-frame-2026-10-25). Every step's plan points
are read in one of two frames, and its reply says which:

- ``"world"`` (the default, and the only frame before 0.8.0): [x, y] as ifc_extent and ifc_info report them, the
  model's world plan. In a building turned on its site, a wall along the building's own side has world points that
  are not square to the axes.
- ``"storey"``: [x, y] in the storey's own frame, so the storey's placement (its turn about the vertical and its
  offset, through the site and the building it stands in) carries them into the world: [0, 0] is the storey's origin
  and x runs along its own x axis. A plan's dimensions, measured along the building's walls, are in this frame.

Heights are always above the storey's level, in either frame.

Nothing is hand-built where IfcOpenShell has a function for it. Every input is checked before the model is touched,
and the steps run in one IfcOpenShell transaction, so a refused or failed step leaves the model as it was.

The steps are additive: ifcmcp's ``ifc_edit`` and ``ifc_shape`` stay for anything unusual.
"""

from __future__ import annotations

import json
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
#: The frames a step's plan points can be given in (module docstring, "THE FRAME").
FRAMES = ("world", "storey")
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


def _frame(value: Any) -> str:
    """The frame a step's plan points are in: "world" (the default, also for an empty value) or "storey"."""
    if value is None or (isinstance(value, str) and value.strip() == ""):
        return "world"
    said = value.strip().lower() if isinstance(value, str) else None
    if said not in FRAMES:
        raise ValueError(
            f"frame is storey or world, not {value!r}: \"storey\" reads [x, y] in the storey's own axes (its placement turns "
            "and shifts them into the world), \"world\" reads them as ifc_extent and ifc_info report them."
        )
    return said


class Frame:
    """Where a step's plan points are read: the world's plan, or the storey's own (turned and shifted) plan.

    ``to_world`` carries a placement matrix built from plan points (and a world height) into the world; for "world"
    it is the matrix itself."""

    def __init__(self, storey: ifcopenshell.entity_instance, name: str):
        self.name = name
        self.storey = storey
        placed = getattr(storey, "ObjectPlacement", None)
        self.matrix = ifcopenshell.util.placement.get_local_placement(placed) if placed else np.eye(4)
        self.level = _level(storey)

    def to_world(self, m: np.ndarray) -> np.ndarray:
        if self.name == "world":
            return m
        # The plan point's height is a world height (the storey's level plus an offset); in the storey's frame it is
        # the offset alone, and the storey's matrix adds the level back (and any turn).
        local = m.copy()
        local[2, 3] -= self.level
        return self.matrix @ local

    def say(self) -> dict[str, Any]:
        out: dict[str, Any] = {"used": self.name}
        origin = self.matrix[:3, 3]
        turned = math.degrees(math.atan2(self.matrix[1][0], self.matrix[0][0]))
        if self.name == "storey":
            out["meaning"] = "[x, y] were read in the storey's own axes and carried into the world by its placement."
        else:
            out["meaning"] = "[x, y] were read in the model's world plan, as ifc_extent and ifc_info report it."
        out["storey_placement"] = {"origin": [round(float(v), 6) for v in origin], "turned_degrees": round(turned, 6)}
        if self.name == "world" and (abs(turned) > 1e-6 or abs(origin[0]) > 1e-9 or abs(origin[1]) > 1e-9):
            out["note"] = 'This storey is turned or shifted in the world: frame "storey" reads [x, y] along its own axes.'
        return out


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


def _place(model, product, frame: Frame, origin: tuple[float, float, float], x_axis: tuple[float, float], tilt: float = 0.0) -> None:
    """Place `product` at `origin` (model units: [x, y] in `frame`'s plan, z a world height), its x along `x_axis` in
    that plan, turned up by `tilt` radians about that x. edit_object_placement takes the world matrix and makes the
    placement relative to the storey (or roof) it is in."""
    ux, uy = x_axis
    c, s = math.cos(tilt), math.sin(tilt)
    m = np.eye(4)
    # Columns: x along the line; y to its left, turned up by tilt; z square to both.
    m[:3, 0] = (ux, uy, 0.0)
    m[:3, 1] = (-uy * c, ux * c, s)
    m[:3, 2] = (uy * s, -ux * s, c)
    m[:3, 3] = origin
    ifcopenshell.api.run("geometry.edit_object_placement", model, product=product, matrix=frame.to_world(m), is_si=False)


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


def _reply(model, element, storey, linked, shape: str, frame: Frame) -> dict[str, Any]:
    unit = reading.units(model)["length"]
    return {
        "made": _made(element),
        "storey": {"id": storey.id(), "name": storey.Name, "level": round(_level(storey), 6)},
        "frame": frame.say(),
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
    frame: Any = "world",
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
    in_frame = _frame(frame)
    level = _storey(model, storey)
    plan = Frame(level, in_frame)
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
        _place(model, wall, plan, (s[0], s[1], _level(level) + lift), (dx / length, dy / length))
        return wall, _link(model, wall, realizes)

    wall, linked = _in_one_transaction(model, build)
    if h2 is not None and h2 != h1:
        tops = f"{_say(h1)} high at its start rising to {_say(h2)} at its end"
    elif hp is not None:
        tops = f"{_say(h1)} high at its ends rising to {_say(hp)} in the middle"
    else:
        tops = f"{_say(h1)} high"
    return _reply(model, wall, level, linked, f"a wall {_say(round(length, 6))} long, {_say(t)} thick, {tops}, {side} on its baseline", plan)


def add_slab(
    model: ifcopenshell.file,
    storey: Any,
    footprint: Any,
    thickness: Any,
    top_offset: Any = 0.0,
    kind: str = "floor",
    name: str = "",
    realizes: str = "",
    frame: Any = "world",
) -> dict[str, Any]:
    """A slab over a footprint, its top at the storey's level (plus `top_offset`), `thickness` deep below it."""
    corners = _footprint(footprint)
    t = _number(thickness, "thickness", positive=True)
    raise_by = _number(top_offset, "top_offset")
    what = (_text(kind, "kind") or "floor").lower()
    if what not in SLAB_KINDS:
        raise ValueError(f"kind is {' or '.join(SLAB_KINDS)}, not {kind!r} (a roof is ifc_add_roof's).")
    in_frame = _frame(frame)
    level = _storey(model, storey)
    plan = Frame(level, in_frame)
    name, realizes = _text(name, "name"), _text(realizes, "realizes")
    scale = ifcopenshell.util.unit.calculate_unit_scale(model)
    x0, y0 = corners[0]
    local = [((x - x0) * scale, (y - y0) * scale) for x, y in corners]

    def build():
        slab = ifcopenshell.api.run("root.create_entity", model, ifc_class="IfcSlab", name=name or None, predefined_type=SLAB_KINDS[what])
        rep = ifcopenshell.api.run("geometry.add_slab_representation", model, context=_body(model), depth=t * scale, direction_sense="NEGATIVE", polyline=local + [local[0]])
        ifcopenshell.api.run("geometry.assign_representation", model, product=slab, representation=rep)
        ifcopenshell.api.run("spatial.assign_container", model, products=[slab], relating_structure=level)
        _place(model, slab, plan, (x0, y0, _level(level) + raise_by), (1.0, 0.0))
        return slab, _link(model, slab, realizes)

    slab, linked = _in_one_transaction(model, build)
    area = Polygon(corners).area
    return _reply(model, slab, level, linked, f"a {what} slab of {_say(round(area, 6))} square units in plan, {_say(t)} thick, its top {_say(raise_by)} above the storey's level", plan)


def add_space(
    model: ifcopenshell.file,
    storey: Any,
    footprint: Any,
    height: Any,
    floor_offset: Any = 0.0,
    name: str = "",
    long_name: str = "",
    realizes: str = "",
    frame: Any = "world",
) -> dict[str, Any]:
    """A room (IfcSpace) over a footprint, from the storey's level (plus `floor_offset`) up `height`, part of its
    storey as rooms are."""
    corners = _footprint(footprint)
    h = _number(height, "height", positive=True)
    lift = _number(floor_offset, "floor_offset")
    in_frame = _frame(frame)
    level = _storey(model, storey)
    plan = Frame(level, in_frame)
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
        _place(model, space, plan, (x0, y0, _level(level) + lift), (1.0, 0.0))
        return space, _link(model, space, realizes)

    space, linked = _in_one_transaction(model, build)
    area = Polygon(corners).area
    return _reply(model, space, level, linked, f"a room of {_say(round(area, 6))} square units in plan, {_say(h)} high", plan)


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
    frame: Any = "world",
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
    in_frame = _frame(frame)
    level = _storey(model, storey)
    plan_frame = Frame(level, in_frame)
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
        _place(model, roof, plan_frame, (ax, ay, z0), (ux, uy))
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
            _place(model, plane, plan_frame, (origin_x, origin_y, z), (ux * way, uy * way), tilt=angle)
            made.append(_made(plane))
        return roof, made, _link(model, roof, realizes)

    roof, made, linked = _in_one_transaction(model, build)
    out = _reply(
        model,
        roof,
        level,
        linked,
        f"a {what} roof of {len(made)} plane(s), rising {_say(rise)} a unit across from its eave{'s' if what == 'gable' else ''} at {_say(eave)} above the storey's level, {_say(t)} thick, overhanging {_say(out_by)}",
        plan_frame,
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


# ------------------------------------------------------------------ units

# A UNIT IS DECLARED BEFORE A VALUE IN IT IS WRITTEN (since 0.9.0; 1510fc3728665dbf,
# fact:root-cause-a-u-factor-in-a-model-carries-no-unit-and-the-reader-reports-only-length-area-volume-2026-10-26).
# A property's value with no unit of its own is in the project's unit for its measure (reading.py, "WHAT A VALUE WITH NO
# UNIT OF ITS OWN IS IN"), and a model that assigns none for that measure stores a bare number: a U-factor of 1.70348
# that the reader cannot tell from 1.70348 Btu/(hr.ft2.°F). ifcmcp's ifc_edit cannot declare a derived unit
# (unit.add_derived_unit takes its elements as a mapping of entities to exponents, which ifcedit cannot pass: it fails
# with "Attribute not set"), so `add_unit` declares any project unit in one call, and `unit_missing_after_edit` holds
# an ifc_edit that writes a measured value to a declared unit.

#: Exact factors for the conversion-based units `add_unit` declares, in SI units (the kilogram for a mass), by the
#: name IfcOpenShell gives each (ifcopenshell.util.unit.imperial_types, which also says its unit type). IfcOpenShell's
#: own table (si_conversions) rounds several (a yard 0.914 m, a mile 1609 m, a pound 0.454 kg) and gives a degree
#: Fahrenheit as 1.8 K where it is 5/9 K, which would make a Btu/(hr.ft2.°F) U-factor 3.24 times too large; these are
#: the exact definitions (NIST SP 811, appendix B: the international foot and pound of 1959, the International Table
#: Btu). A Fahrenheit temperature also takes IfcOpenShell's offset; inside a derived unit it is a difference, as IFC
#: reads a derived unit's elements.
CONVERSIONS = {
    "thou": 2.54e-5, "inch": 0.0254, "foot": 0.3048, "yard": 0.9144, "mile": 1609.344,
    "square thou": 6.4516e-10, "square inch": 6.4516e-4, "square foot": 0.09290304, "square yard": 0.83612736,
    "acre": 4046.8564224, "square mile": 2589988.110336,
    "cubic thou": 1.6387064e-14, "cubic inch": 1.6387064e-5, "cubic foot": 0.028316846592, "cubic yard": 0.764554857984,
    "cubic mile": 4168181825.440579584, "litre": 0.001,
    "fluid ounce UK": 2.84130625e-5, "fluid ounce US": 2.95735295625e-5, "pint UK": 5.6826125e-4, "pint US": 4.73176473e-4,
    "gallon UK": 4.54609e-3, "gallon US": 3.785411784e-3,
    "degree": math.pi / 180,
    "ounce": 0.028349523125, "pound": 0.45359237, "ton UK": 1016.0469088, "ton US": 907.18474, "tonne": 1000.0,
    "lbf": 4.4482216152605, "pound-force": 4.4482216152605, "kip": 4448.2216152605,
    "psi": 6894.757293168361, "pound-force per square inch": 6894.757293168361, "ksi": 6894757.293168361,
    "minute": 60.0, "hour": 3600.0, "day": 86400.0,
    "btu": 1055.05585262,
    "fahrenheit": 5 / 9,
}  # fmt: skip

#: The SI dimensions (length, mass, time, current, temperature, amount, luminous intensity) of each derived unit type
#: IFC4 names (IfcDerivedUnitEnum, from the SI unit its documentation gives each), so a derived unit is checked to be
#: what it says before it is declared. A type not listed here is declared unchecked, and the reply says so.
DERIVED_DIMENSIONS = {
    "ACCELERATIONUNIT": (1, 0, -2, 0, 0, 0, 0),  # m/s2
    "ANGULARVELOCITYUNIT": (0, 0, -1, 0, 0, 0, 0),  # rad/s
    "AREADENSITYUNIT": (-2, 1, 0, 0, 0, 0, 0),  # kg/m2
    "COMPOUNDPLANEANGLEUNIT": (0, 0, 0, 0, 0, 0, 0),
    "CURVATUREUNIT": (-1, 0, 0, 0, 0, 0, 0),  # rad/m
    "DYNAMICVISCOSITYUNIT": (-1, 1, -1, 0, 0, 0, 0),  # Pa.s
    "HEATFLUXDENSITYUNIT": (0, 1, -3, 0, 0, 0, 0),  # W/m2
    "HEATINGVALUEUNIT": (2, 0, -2, 0, 0, 0, 0),  # J/kg
    "INTEGERCOUNTRATEUNIT": (0, 0, -1, 0, 0, 0, 0),  # 1/s
    "IONCONCENTRATIONUNIT": (-3, 1, 0, 0, 0, 0, 0),  # kg/m3
    "ISOTHERMALMOISTURECAPACITYUNIT": (3, -1, 0, 0, 0, 0, 0),  # m3/kg
    "KINEMATICVISCOSITYUNIT": (2, 0, -1, 0, 0, 0, 0),  # m2/s
    "LINEARFORCEUNIT": (0, 1, -2, 0, 0, 0, 0),  # N/m
    "LINEARMOMENTUNIT": (1, 1, -2, 0, 0, 0, 0),  # N.m/m
    "LINEARSTIFFNESSUNIT": (0, 1, -2, 0, 0, 0, 0),  # N/m
    "LINEARVELOCITYUNIT": (1, 0, -1, 0, 0, 0, 0),  # m/s
    "MASSDENSITYUNIT": (-3, 1, 0, 0, 0, 0, 0),  # kg/m3
    "MASSFLOWRATEUNIT": (0, 1, -1, 0, 0, 0, 0),  # kg/s
    "MASSPERLENGTHUNIT": (-1, 1, 0, 0, 0, 0, 0),  # kg/m
    "MODULUSOFELASTICITYUNIT": (-1, 1, -2, 0, 0, 0, 0),  # N/m2
    "MODULUSOFLINEARSUBGRADEREACTIONUNIT": (-1, 1, -2, 0, 0, 0, 0),  # N/m2
    "MODULUSOFROTATIONALSUBGRADEREACTIONUNIT": (1, 1, -2, 0, 0, 0, 0),  # N.m/(m.rad)
    "MODULUSOFSUBGRADEREACTIONUNIT": (-2, 1, -2, 0, 0, 0, 0),  # N/m3
    "MOISTUREDIFFUSIVITYUNIT": (3, 0, -1, 0, 0, 0, 0),  # m3/s
    "MOLECULARWEIGHTUNIT": (0, 1, 0, 0, 0, -1, 0),  # kg/mol
    "MOMENTOFINERTIAUNIT": (4, 0, 0, 0, 0, 0, 0),  # m4
    "PLANARFORCEUNIT": (-1, 1, -2, 0, 0, 0, 0),  # N/m2
    "ROTATIONALFREQUENCYUNIT": (0, 0, -1, 0, 0, 0, 0),  # 1/s
    "ROTATIONALMASSUNIT": (2, 1, 0, 0, 0, 0, 0),  # kg.m2
    "ROTATIONALSTIFFNESSUNIT": (2, 1, -2, 0, 0, 0, 0),  # N.m/rad
    "SECTIONAREAINTEGRALUNIT": (5, 0, 0, 0, 0, 0, 0),  # m5
    "SECTIONMODULUSUNIT": (3, 0, 0, 0, 0, 0, 0),  # m3
    "SHEARMODULUSUNIT": (-1, 1, -2, 0, 0, 0, 0),  # N/m2
    "SOUNDPOWERUNIT": (2, 1, -3, 0, 0, 0, 0),  # W
    "SOUNDPRESSUREUNIT": (-1, 1, -2, 0, 0, 0, 0),  # Pa
    "SPECIFICHEATCAPACITYUNIT": (2, 0, -2, 0, -1, 0, 0),  # J/(kg.K)
    "TEMPERATUREGRADIENTUNIT": (-1, 0, 0, 0, 1, 0, 0),  # K/m
    "TEMPERATURERATEOFCHANGEUNIT": (0, 0, -1, 0, 1, 0, 0),  # K/s
    "THERMALADMITTANCEUNIT": (0, 1, -3, 0, -1, 0, 0),  # W/(m2.K)
    "THERMALCONDUCTANCEUNIT": (1, 1, -3, 0, -1, 0, 0),  # W/(m.K): IfcThermalConductivityMeasure's unit
    "THERMALEXPANSIONCOEFFICIENTUNIT": (0, 0, 0, 0, -1, 0, 0),  # 1/K
    "THERMALRESISTANCEUNIT": (0, -1, 3, 0, 1, 0, 0),  # m2.K/W
    "THERMALTRANSMITTANCEUNIT": (0, 1, -3, 0, -1, 0, 0),  # W/(m2.K)
    "TORQUEUNIT": (2, 1, -2, 0, 0, 0, 0),  # N.m
    "VAPORPERMEABILITYUNIT": (0, 0, 1, 0, 0, 0, 0),  # kg/(s.m.Pa)
    "VOLUMETRICFLOWRATEUNIT": (3, 0, -1, 0, 0, 0, 0),  # m3/s
    "WARPINGCONSTANTUNIT": (6, 0, 0, 0, 0, 0, 0),  # m6
    "WARPINGMOMENTUNIT": (3, 1, -2, 0, 0, 0, 0),  # N.m2
}

#: The ifcopenshell.api functions through ifc_edit that write property or quantity values.
WRITES_VALUES = ("pset.edit_pset", "pset.edit_qto")


def _si_unit_type(name: str) -> str | None:
    """The unit type an SI unit name belongs to (WATT: POWERUNIT), or None if IFC names no such SI unit."""
    if name == "DEGREE_CELSIUS":
        return "THERMODYNAMICTEMPERATUREUNIT"
    return next((t for t, n in ifcopenshell.util.unit.si_type_names.items() if n == name and t != "USERDEFINED"), None)


def _conversion_name(name: str) -> str | None:
    wanted = name.strip().lower()
    return next((n for n in CONVERSIONS if n.lower() == wanted), None)


def _named_unit_plan(name: Any, prefix: Any, what: str) -> tuple[str, str, str, tuple[int, ...]]:
    """Check one named unit before anything is made: ("si" or "conversion", its IFC name, its unit type, its dimensions)."""
    text = _text(name, what)
    if not text:
        raise ValueError(f"{what} is empty: name an SI unit (such as WATT, METRE, KELVIN) or a conversion-based unit (such as foot, btu, hour, fahrenheit).")
    prefix_text = _text(prefix, f"{what}'s prefix").upper() if prefix else ""
    si_name = text.upper().replace(" ", "_").replace("METER", "METRE")
    if si_name in reading.SI_SYMBOLS:
        unit_type = _si_unit_type(si_name)
        if unit_type is None:
            raise ValueError(f"{text!r} is not an SI unit IFC can declare on its own.")
        if prefix_text and prefix_text not in ifcopenshell.util.unit.prefixes:
            raise ValueError(f"{prefix_text!r} is not an SI prefix. IFC's: {', '.join(ifcopenshell.util.unit.prefixes)}.")
        return "si", si_name, unit_type, tuple(ifcopenshell.util.unit.si_dimensions[si_name])
    conversion = _conversion_name(text)
    if conversion is None:
        raise ValueError(
            f"{text!r} is not a unit flo2-ifc can declare. An SI unit: {', '.join(reading.SI_SYMBOLS)} (with a prefix such as MILLI or KILO);"
            f" or one of these: {', '.join(CONVERSIONS)}."
        )
    if prefix_text:
        raise ValueError(f"{conversion!r} is not an SI unit, so it takes no prefix.")
    unit_type = ifcopenshell.util.unit.imperial_types[conversion]
    return "conversion", conversion, unit_type, tuple(ifcopenshell.util.unit.named_dimensions[unit_type])


def _make_named_unit(model: ifcopenshell.file, kind: str, name: str, unit_type: str, prefix: str) -> ifcopenshell.entity_instance:
    """Make one named unit with IfcOpenShell's functions (it is not assigned to the project here)."""
    if kind == "si":
        unit = ifcopenshell.api.run("unit.add_si_unit", model, unit_type=unit_type, prefix=prefix or None)
        if unit.Name != name:  # DEGREE_CELSIUS: add_si_unit names a temperature unit KELVIN
            ifcopenshell.api.run("unit.edit_named_unit", model, unit=unit, attributes={"Name": name})
        return unit
    unit = ifcopenshell.api.run("unit.add_conversion_based_unit", model, name=name)
    ifcopenshell.api.run(
        "attribute.edit_attributes",
        model,
        product=unit.ConversionFactor,
        attributes={"ValueComponent": model.create_entity("IfcReal", CONVERSIONS[name])},
    )
    return unit


def add_unit(model: ifcopenshell.file, unit_type: str, unit: str = "", prefix: str = "", parts: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Declare one project unit (IfcUnitAssignment), in one call: an SI unit, a conversion-based unit, a derived unit
    or the currency. Refuses a unit type the project already declares: every value stored in that measure is read in
    the unit declared, so changing it is a deliberate ifc_edit (unit.unassign_unit), never a side effect."""
    kind_of_type = _text(unit_type, "unit_type").upper().replace(" ", "")
    if not kind_of_type.endswith("UNIT"):
        kind_of_type += "UNIT"
    known, _ = reading._schema_unit_types(model)
    if kind_of_type not in known:
        raise ValueError(f"{unit_type!r} is not a unit type of {model.schema_identifier}. For example: LENGTHUNIT, THERMODYNAMICTEMPERATUREUNIT, THERMALTRANSMITTANCEUNIT, MONETARYUNIT.")
    if not model.by_type("IfcProject"):
        raise ValueError("The model has no IfcProject, which is where its units are declared.")
    existing = reading.project_unit(model, kind_of_type)
    if existing is not None:
        said = reading.describe_unit(model, existing)
        raise ValueError(
            f"The model already declares {said['of']} in {said['symbol'] or said['name']}. Every value it stores in that measure is read in it,"
            " so it is not replaced here: to change it, remove it with ifc_edit unit.unassign_unit first, and convert the stored values."
        )
    derived_types = set(DERIVED_DIMENSIONS) | {t for t in known if t not in ifcopenshell.util.unit.named_dimensions and t != "MONETARYUNIT"}
    parts = parts or []
    checked = True
    if kind_of_type == "MONETARYUNIT":
        currency = _text(unit, "unit").upper()
        if not (len(currency) == 3 and currency.isalpha()) or prefix or parts:
            raise ValueError("A currency is declared by its ISO 4217 code in `unit` (such as USD or EUR), with no prefix and no parts.")

        def build():
            return ifcopenshell.api.run("unit.add_monetary_unit", model, currency=currency)

    elif kind_of_type in derived_types:
        if unit or prefix:
            raise ValueError(f"{kind_of_type} is a derived unit: give its `parts`, each {{\"unit\": ..., \"exponent\": ...}}, not `unit` or `prefix`.")
        if not isinstance(parts, list) or not parts:
            raise ValueError(
                f"{kind_of_type} is a derived unit: give its `parts`, e.g. for W/(m2.K) "
                '[{"unit": "WATT"}, {"unit": "METRE", "exponent": -2}, {"unit": "KELVIN", "exponent": -1}], '
                'or for Btu/(hr.ft2.°F) [{"unit": "btu"}, {"unit": "hour", "exponent": -1}, {"unit": "square foot", "exponent": -1}, {"unit": "fahrenheit", "exponent": -1}].'
            )
        plans = []
        dims = [0] * 7
        for i, item in enumerate(parts, 1):
            if not isinstance(item, dict) or set(item) - {"unit", "exponent", "prefix"} or "unit" not in item:
                raise ValueError(f"parts[{i}] must be {{\"unit\": ..., \"exponent\": ..., \"prefix\": ...}} (exponent and prefix optional), not {item!r}.")
            exponent = item.get("exponent", 1)
            if isinstance(exponent, bool) or not isinstance(exponent, int) or exponent == 0:
                raise ValueError(f"parts[{i}]'s exponent must be a whole number other than 0, not {exponent!r}.")
            kind, name, its_type, its_dims = _named_unit_plan(item["unit"], item.get("prefix", ""), f"parts[{i}]'s unit")
            plans.append((kind, name, its_type, (item.get("prefix") or "").upper(), exponent))
            dims = [d + exponent * e for d, e in zip(dims, its_dims, strict=True)]
        wanted = DERIVED_DIMENSIONS.get(kind_of_type)
        checked = wanted is not None
        if checked and tuple(dims) != wanted:
            raise ValueError(
                f"Those parts do not make a {reading._label(model, kind_of_type)} unit: their dimensions (length, mass, time, current,"
                f" temperature, amount, luminous intensity) are {tuple(dims)}, and {kind_of_type} is {wanted}."
            )

        def build():
            made = {_make_named_unit(model, kind, name, its_type, p): exponent for kind, name, its_type, p, exponent in plans}
            return ifcopenshell.api.run("unit.add_derived_unit", model, unit_type=kind_of_type, userdefinedtype=None, attributes=made)

    else:
        if parts:
            raise ValueError(f"{kind_of_type} is a named unit: give it in `unit` (and `prefix`), not `parts`.")
        kind, name, its_type, _ = _named_unit_plan(unit, prefix, "unit")
        if its_type != kind_of_type:
            raise ValueError(f"{name!r} is a {reading._label(model, its_type)} unit ({its_type}), not {kind_of_type}.")
        prefix_text = (prefix or "").upper()

        def build():
            return _make_named_unit(model, kind, name, its_type, prefix_text)

    def declare():
        made = build()
        ifcopenshell.api.run("unit.assign_unit", model, units=[made])
        return made

    made = _in_one_transaction(model, declare)
    out: dict[str, Any] = {"declared": reading.describe_unit(model, made), "units": reading.units(model)["say"]}
    if not checked:
        out["dimensions_checked"] = False
    out["check"] = "ifc_units lists it; ifc_info's property_units now gives this unit for every value of its measure that names none of its own."
    return out


def unit_missing_after_edit(model: ifcopenshell.file, function_path: str, params: Any) -> list[str]:
    """After ifc_edit ran pset.edit_pset or pset.edit_qto: the values it wrote whose measure has no unit (neither the
    property's own nor the project's), as ``Pset_WindowCommon.ThermalTransmittance (IfcThermalTransmittanceMeasure)``."""
    if function_path not in WRITES_VALUES:
        return []
    try:
        args = json.loads(params) if isinstance(params, str) else dict(params or {})
        definition = model.by_id(int(str(args.get("pset") or args.get("qto")).lstrip("#")))
        written = args.get("properties") or {}
        written = json.loads(written) if isinstance(written, str) else written
    except (ValueError, TypeError, RuntimeError, AttributeError):
        return []
    names = set(written) if isinstance(written, dict) else set()
    missing = []
    for prop in list(getattr(definition, "HasProperties", None) or []) + list(getattr(definition, "Quantities", None) or []):
        if prop.Name in names:
            found = reading.property_unit(model, prop)
            if found is not None and found["from"] == "not declared":
                missing.append(f"{definition.Name}.{prop.Name} ({found['measure']})")
    return missing
