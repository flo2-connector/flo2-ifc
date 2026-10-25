"""The building steps: a wall, a slab, a roof and a room, each made with ONE call.

flo2's design 1a97fc1f1de4d5d2, dec:a-chat-builds-a-models-ordinary-parts-with-one-call-each (accepted 2026-10-24):
a chat that built a 24-element shell through ifcmcp's ifc_shape and ifc_edit spent 250 calls, about 6 to 8 per
element (polyline, extrude, get_representation; root.create_entity, geometry.assign_representation,
geometry.edit_object_placement; classification.add_reference and spatial.assign_container), measured in
fact:building-a-models-shell-takes-about-6-helper-calls-per-element-2026-10-24. Each step here creates the element,
gives it its shape, places it, puts it on its storey and links it to the design part it realizes, in one call.

These tests build on the starting model a chat copies (in feet, and in metres) and on the synthetic millimetre house
(tests/models.py), never on a person's model. Every model they make must validate with the schema's rules; the shell
built through the server must also pass buildingSMART's rules when they are installed.
"""

from __future__ import annotations

import json
import math
import os
import re
import shlex
import tempfile
import time
from pathlib import Path

import anyio
import ifcopenshell
import ifcopenshell.util.classification
import ifcopenshell.util.element
import ifcopenshell.util.placement
import numpy as np
import pytest

from flo2_ifc import building, geometry, reading
from tests import models
from tests.test_starting_model import RULES, in_metres, needs_rules, starting, without

FT = 0.3048


def feet_model() -> ifcopenshell.file:
    return ifcopenshell.file.from_string(starting())


def metre_model() -> ifcopenshell.file:
    return ifcopenshell.file.from_string(in_metres(starting()))


def box(model, element) -> dict:
    return reading.extent(model, element_id=element)["overall"]


def references(element) -> list[tuple[str, str]]:
    return [(r.Identification, r.ReferencedSource.Name) for r in ifcopenshell.util.classification.get_references(element)]


def assert_valid(model):
    out = reading.validate(model, express_rules=True)
    assert out["complete"] and out["valid"], out.get("issues")


# ------------------------------------------------------------------- walls


def test_a_wall_from_a_baseline_is_made_shaped_placed_contained_and_linked_in_one_call():
    m = feet_model()
    out = building.add_wall(m, storey="Level 1", start=[0, 0], end=[30, 0], height=10, thickness=0.5, name="South wall", realizes="cmp:south-wall")
    wall = m.by_id(out["made"]["id"])
    assert wall.is_a("IfcWall") and wall.Name == "South wall"
    assert ifcopenshell.util.element.get_container(wall).Name == "Level 1"
    assert references(wall) == [("cmp:south-wall", "flo2 design")]
    b = box(m, wall.id())
    # On its baseline, centred on it, in the model's feet.
    assert b["min"] == pytest.approx([0, -0.25, 0], abs=1e-4)
    assert b["max"] == pytest.approx([30, 0.25, 10], abs=1e-4)
    assert geometry.volume(m, element_id=wall.id())["total"]["model"] == pytest.approx(30 * 0.5 * 10, rel=1e-3)
    assert out["storey"]["name"] == "Level 1"
    assert out["realizes"] == {"identification": "cmp:south-wall", "classification": "flo2 design"}
    assert_valid(m)


def test_a_wall_with_a_sloped_top_rises_from_its_start_height_to_its_end_height():
    m = feet_model()
    out = building.add_wall(m, storey="Level 1", start=[30, 0], end=[30, 20], height=10, height_at_end=14, thickness=0.5)
    wall = out["made"]["id"]
    b = box(m, wall)
    assert b["max"][2] == pytest.approx(14, abs=1e-4)
    assert b["min"][1] == pytest.approx(0, abs=1e-4) and b["max"][1] == pytest.approx(20, abs=1e-4)
    # A trapezoid 20 ft long, 10 ft high at one end and 14 at the other, 0.5 ft thick.
    assert geometry.volume(m, element_id=wall)["total"]["model"] == pytest.approx(20 * 0.5 * 12, rel=1e-3)
    assert_valid(m)


def test_a_gable_end_wall_peaks_in_the_middle():
    m = metre_model()
    out = building.add_wall(m, storey="Level 1", start=[0, 0], end=[6, 0], height=3, peak_height=5, thickness=0.2)
    assert box(m, out["made"]["id"])["max"][2] == pytest.approx(5, abs=1e-4)
    assert geometry.volume(m, element_id=out["made"]["id"])["total"]["model"] == pytest.approx(6 * 0.2 * 3 + 0.5 * 6 * 2 * 0.2, rel=1e-3)
    assert_valid(m)


def test_a_wall_stands_on_its_storeys_level_with_a_base_offset_and_on_either_side_of_its_line():
    m = feet_model()
    up = building.add_wall(m, storey="Level 2", start=[0, 0], end=[0, 10], height=8, thickness=1, base_offset=0.5, align="left")
    b = box(m, up["made"]["id"])
    # Level 2 is 10 ft up. Running north, its left is west (-x).
    assert b["min"][2] == pytest.approx(10.5, abs=1e-4) and b["max"][2] == pytest.approx(18.5, abs=1e-4)
    assert b["min"][0] == pytest.approx(-1, abs=1e-4) and b["max"][0] == pytest.approx(0, abs=1e-4)
    right = building.add_wall(m, storey="Level 1", start=[0, 0], end=[0, 10], height=8, thickness=1, align="right")
    assert box(m, right["made"]["id"])["min"][0] == pytest.approx(0, abs=1e-4)
    assert_valid(m)


def test_a_wall_in_a_millimetre_model_takes_millimetres():
    m = models.building(walls=0)
    out = building.add_wall(m, storey="GROUND", start=[1000, 2000], end=[5000, 2000], height=2700, thickness=100)
    b = box(m, out["made"]["id"])
    assert b["min"] == pytest.approx([1000, 1950, 0], abs=0.01)
    assert b["max"] == pytest.approx([5000, 2050, 2700], abs=0.01)
    assert_valid(m)


# ------------------------------------------------------------- slab, room


def test_a_slab_from_a_footprint_has_its_top_at_the_storeys_level():
    m = feet_model()
    footprint = [[0, 0], [30, 0], [30, 20], [10, 20], [10, 12], [0, 12]]
    out = building.add_slab(m, storey="Level 1", footprint=footprint, thickness=0.5, name="Floor slab", realizes="cmp:floor")
    slab = m.by_id(out["made"]["id"])
    assert slab.is_a("IfcSlab") and slab.PredefinedType == "FLOOR"
    assert ifcopenshell.util.element.get_container(slab).Name == "Level 1"
    assert references(slab) == [("cmp:floor", "flo2 design")]
    b = box(m, slab.id())
    assert b["top"] == pytest.approx(0, abs=1e-4) and b["bottom"] == pytest.approx(-0.5, abs=1e-4)
    area = 30 * 12 + 20 * 8
    assert geometry.footprint(m, element_id=slab.id())["footprint"]["model"] == pytest.approx(area, rel=1e-3)
    assert_valid(m)


def test_a_room_from_a_footprint_is_part_of_its_storey_with_its_height():
    m = feet_model()
    out = building.add_space(m, storey="Level 2", footprint=[[0, 0], [12, 0], [12, 10], [0, 10]], height=9, name="Office", realizes="cmp:office")
    space = m.by_id(out["made"]["id"])
    assert space.is_a("IfcSpace") and space.Name == "Office"
    assert ifcopenshell.util.element.get_aggregate(space).Name == "Level 2"
    assert reading.storey_of(space)["name"] == "Level 2"
    assert references(space) == [("cmp:office", "flo2 design")]
    b = box(m, space.id())
    assert b["bottom"] == pytest.approx(10, abs=1e-4) and b["top"] == pytest.approx(19, abs=1e-4)
    assert geometry.volume(m, element_id=space.id())["total"]["model"] == pytest.approx(12 * 10 * 9, rel=1e-3)
    assert_valid(m)


# ------------------------------------------------------------------- roofs


def test_a_shed_roof_rises_across_its_footprint_from_the_first_side():
    m = feet_model()
    out = building.add_roof(m, storey="Level 1", footprint=[[0, 0], [30, 0], [30, 20], [0, 20]], kind="shed", eave_height=10, slope=0.2, thickness=0.5, realizes="cmp:roof")
    roof = m.by_id(out["made"]["id"])
    assert roof.is_a("IfcRoof") and roof.PredefinedType == "SHED_ROOF"
    assert ifcopenshell.util.element.get_container(roof).Name == "Level 1"
    assert references(roof) == [("cmp:roof", "flo2 design")]
    planes = out["made"]["planes"]
    assert len(planes) == 1
    plane = m.by_id(planes[0]["id"])
    assert plane.is_a("IfcSlab") and plane.PredefinedType == "ROOF"
    assert ifcopenshell.util.element.get_aggregate(plane) == roof
    b = box(m, plane.id())
    # Underside at 10 ft along the south side, rising 0.2 ft a foot to 14 ft at the north;
    # 0.5 ft thick across the slope, so its top is 0.5 / cos(slope) above that.
    rise = 0.5 / math.cos(math.atan(0.2))
    assert b["bottom"] == pytest.approx(10, abs=1e-4)
    assert b["top"] == pytest.approx(14 + rise, abs=1e-4)
    assert b["min"][:2] == pytest.approx([0, 0], abs=1e-4) and b["max"][:2] == pytest.approx([30, 20], abs=1e-4)
    assert geometry.footprint(m, element_id=plane.id())["footprint"]["model"] == pytest.approx(600, rel=1e-3)
    assert_valid(m)


def test_a_gable_roof_has_two_planes_meeting_at_a_ridge_and_an_overhang():
    m = metre_model()
    out = building.add_roof(m, storey="Level 1", footprint=[[0, 0], [8, 0], [8, 6], [0, 6]], kind="gable", eave_height=3, slope=0.5, thickness=0.2, overhang=0.5)
    assert m.by_id(out["made"]["id"]).PredefinedType == "GABLE_ROOF"
    planes = [p["id"] for p in out["made"]["planes"]]
    assert len(planes) == 2
    boxes = [box(m, p) for p in planes]
    # The ridge runs east-west halfway across (y = 3), 3 m from each eave: 3 + 0.5 * 3 = 4.5 m to the underside.
    ridge_top = 4.5 + 0.2 / math.cos(math.atan(0.5))
    for b in boxes:
        assert b["top"] == pytest.approx(ridge_top, abs=1e-4)
        # The overhang: 0.5 m beyond the walls, so the eave's underside drops 0.25 m.
        assert b["bottom"] == pytest.approx(2.75, abs=1e-4)
    assert min(b["min"][0] for b in boxes) == pytest.approx(-0.5, abs=1e-4)
    assert max(b["max"][1] for b in boxes) == pytest.approx(6.5, abs=1e-4)
    covered = geometry.footprint(m, ids=",".join(str(p) for p in planes))
    assert covered["footprint"]["model"] == pytest.approx(9 * 7, rel=1e-3)
    assert covered["stacked_overlap"]["model"] == pytest.approx(0, abs=1e-3)
    assert_valid(m)


# ------------------------------------------------------------------ frames
#
# fact:building-steps-take-plan-points-in-world-coordinates-not-the-storeys-frame-2026-10-25: until 0.8.0 every step
# read [x, y] in the model's WORLD frame, so a chat thinking in a turned building's own axes got every part turned
# and shifted. dec:building-steps-take-a-frame-and-measures-cut-openings, part 1: each step takes `frame`, "storey"
# (the plan points are in the storey's own axes: its placement turns and shifts them) or "world" (as ifc_extent and
# ifc_info report them; the default, as before). models.turned_storey turns the site, or the building, by 26.03
# degrees and shifts it to (10, 5), the storey 3 m up.


def storey_matrix(model) -> np.ndarray:
    storey = reading.find_storey(model, "L1")
    return ifcopenshell.util.placement.get_local_placement(storey.ObjectPlacement)


def bearing(matrix) -> float:
    return math.degrees(math.atan2(matrix[1][0], matrix[0][0]))


def reach_in_storey(model, element_id) -> tuple[np.ndarray, np.ndarray]:
    """How far the element's shape reaches in the storey's own frame (metres): its lowest and highest corners."""
    got, _ = geometry.meshes(model, [model.by_id(element_id)], time.monotonic() + 60)
    corners = got[element_id].verts
    local = (np.linalg.inv(storey_matrix(model)) @ np.c_[corners, np.ones(len(corners))].T).T[:, :3]
    return local.min(axis=0), local.max(axis=0)


@pytest.mark.parametrize("turn_at", ["site", "building"])
def test_in_the_storeys_frame_every_step_builds_on_the_storeys_own_axes(turn_at):
    m = models.turned_storey(turn_at)
    frame = storey_matrix(m)
    assert bearing(frame) == pytest.approx(models.TURN_DEGREES, abs=1e-6)
    assert frame[:3, 3] == pytest.approx(models.TURN_ORIGIN, abs=1e-6)

    wall = building.add_wall(m, storey="L1", start=[0, 0], end=[5, 0], height=3, thickness=0.2, frame="storey")
    assert wall["frame"]["used"] == "storey"
    lo, hi = reach_in_storey(m, wall["made"]["id"])
    assert lo == pytest.approx([0, -0.1, 0], abs=1e-6) and hi == pytest.approx([5, 0.1, 3], abs=1e-6)
    placed = ifcopenshell.util.placement.get_local_placement(m.by_id(wall["made"]["id"]).ObjectPlacement)
    assert bearing(placed) == pytest.approx(models.TURN_DEGREES, abs=1e-6)

    footprint = [[0, 0], [5, 0], [5, 4], [0, 4]]
    slab = building.add_slab(m, storey="L1", footprint=footprint, thickness=0.2, frame="storey")
    lo, hi = reach_in_storey(m, slab["made"]["id"])
    assert lo == pytest.approx([0, 0, -0.2], abs=1e-6) and hi == pytest.approx([5, 4, 0], abs=1e-6)

    room = building.add_space(m, storey="L1", footprint=footprint, height=2.7, frame="storey")
    lo, hi = reach_in_storey(m, room["made"]["id"])
    assert lo == pytest.approx([0, 0, 0], abs=1e-6) and hi == pytest.approx([5, 4, 2.7], abs=1e-6)

    roof = building.add_roof(m, storey="L1", footprint=footprint, kind="shed", eave_height=3, slope=0.25, thickness=0.2, frame="storey")
    assert roof["frame"]["used"] == "storey"
    lo, hi = reach_in_storey(m, roof["made"]["planes"][0]["id"])
    assert lo[:2] == pytest.approx([0, 0], abs=1e-4) and hi[:2] == pytest.approx([5, 4], abs=1e-4)
    # The eave's underside is 3 m above the storey along the first side; it rises 0.25 a metre across 4 m.
    assert lo[2] == pytest.approx(3, abs=1e-4)
    assert hi[2] == pytest.approx(3 + 0.25 * 4 + 0.2 / math.cos(math.atan(0.25)), abs=1e-4)
    for step in (wall, slab, room):
        assert step["frame"]["used"] == "storey"
    assert_valid(m)


def test_in_the_world_frame_the_default_points_are_where_ifc_extent_reports_them():
    m = models.turned_storey("building")
    frame = storey_matrix(m)
    # The storey's own [0, 0] and [5, 0], given in world: the wall lands on the storey's axes, as before 0.8.0.
    a, b = frame @ [0, 0, 0, 1], frame @ [5, 0, 0, 1]
    given = building.add_wall(m, storey="L1", start=a[:2].tolist(), end=b[:2].tolist(), height=3, thickness=0.2)
    assert given["frame"]["used"] == "world"
    lo, hi = reach_in_storey(m, given["made"]["id"])
    assert lo == pytest.approx([0, -0.1, 0], abs=1e-6) and hi == pytest.approx([5, 0.1, 3], abs=1e-6)
    # [0, 0] -> [5, 0] in world runs along world x, whatever way the storey is turned; heights stay the storey's.
    plain = building.add_wall(m, storey="L1", start=[0, 0], end=[5, 0], height=3, thickness=0.2, frame="world")
    assert box(m, plain["made"]["id"])["min"] == pytest.approx([0, -0.1, 3], abs=1e-6)
    assert box(m, plain["made"]["id"])["max"] == pytest.approx([5, 0.1, 6], abs=1e-6)
    assert_valid(m)


def test_on_an_unturned_storey_both_frames_build_the_same():
    m = feet_model()
    one = building.add_wall(m, storey="Level 2", start=[0, 0], end=[30, 0], height=10, thickness=0.5, frame="storey")
    two = building.add_wall(m, storey="Level 2", start=[0, 0], end=[30, 0], height=10, thickness=0.5, frame="world")
    assert box(m, one["made"]["id"])["min"] == pytest.approx(box(m, two["made"]["id"])["min"], abs=1e-6)
    assert box(m, one["made"]["id"])["max"] == pytest.approx(box(m, two["made"]["id"])["max"], abs=1e-6)


# ---------------------------------------------------------------- refusals


@pytest.mark.parametrize(
    ("step", "kwargs", "says"),
    [
        ("add_wall", {"storey": "Level 9", "start": [0, 0], "end": [1, 0], "height": 3, "thickness": 0.2}, "No storey"),
        ("add_wall", {"storey": "Level 1", "start": [0, 0], "end": [0, 0], "height": 3, "thickness": 0.2}, "start and end are the same point"),
        ("add_wall", {"storey": "Level 1", "start": [0, 0, 0], "end": [1, 0], "height": 3, "thickness": 0.2}, "start is a point in plan"),
        ("add_wall", {"storey": "Level 1", "start": [0, 0], "end": [1, 0], "height": 0, "thickness": 0.2}, "height"),
        ("add_wall", {"storey": "Level 1", "start": [0, 0], "end": [1, 0], "height": 3, "thickness": -1}, "thickness"),
        ("add_wall", {"storey": "Level 1", "start": [0, 0], "end": [1, 0], "height": 3, "thickness": 0.2, "height_at_end": 4, "peak_height": 5}, "not both"),
        ("add_wall", {"storey": "Level 1", "start": [0, 0], "end": [1, 0], "height": 3, "thickness": 0.2, "peak_height": 2}, "peak_height"),
        ("add_wall", {"storey": "Level 1", "start": [0, 0], "end": [1, 0], "height": 3, "thickness": 0.2, "align": "middle"}, "align"),
        ("add_slab", {"storey": "Level 1", "footprint": [[0, 0], [1, 0]], "thickness": 0.5}, "at least 3"),
        ("add_slab", {"storey": "Level 1", "footprint": [[0, 0], [2, 2], [2, 0], [0, 2]], "thickness": 0.5}, "crosses itself"),
        ("add_slab", {"storey": "Level 1", "footprint": [[0, 0], [1, 0], [2, 0]], "thickness": 0.5}, "encloses no area"),
        ("add_slab", {"storey": "Level 1", "footprint": [[0, 0], [1, 0], [1, 1]], "thickness": 0.5, "kind": "ceiling"}, "kind"),
        ("add_space", {"storey": "Level 1", "footprint": [[0, 0], [1, 0], [1, 1]], "height": -2}, "height"),
        ("add_roof", {"storey": "Level 1", "footprint": [[0, 0], [1, 0], [1, 1], [0, 1]], "kind": "hip", "eave_height": 3, "slope": 0.3, "thickness": 0.2}, "shed or gable"),
        ("add_roof", {"storey": "Level 1", "footprint": [[0, 0], [1, 0], [1, 1], [0, 1]], "kind": "shed", "eave_height": 3, "slope": 0, "thickness": 0.2}, "slope"),
        ("add_roof", {"storey": "Level 1", "footprint": [[0, 0], [1, 0], [1, 1], [0, 1]], "kind": "shed", "eave_height": 3, "slope": 0.3, "thickness": 0.2, "overhang": -1}, "overhang"),
        ("add_wall", {"storey": "Level 1", "start": [0, 0], "end": [1, 0], "height": 3, "thickness": 0.2, "frame": "plan"}, "frame is"),
        ("add_slab", {"storey": "Level 1", "footprint": [[0, 0], [1, 0], [1, 1]], "thickness": 0.5, "frame": "local"}, "frame is"),
        ("add_space", {"storey": "Level 1", "footprint": [[0, 0], [1, 0], [1, 1]], "height": 2, "frame": 1}, "frame"),
        ("add_roof", {"storey": "Level 1", "footprint": [[0, 0], [1, 0], [1, 1], [0, 1]], "kind": "shed", "eave_height": 3, "slope": 0.3, "thickness": 0.2, "frame": "site"}, "frame is"),
    ],
)
def test_wrong_input_is_refused_with_its_reason_and_changes_nothing(step, kwargs, says):
    m = feet_model()
    before = len(list(m))
    with pytest.raises(ValueError) as refused:
        getattr(building, step)(m, **kwargs)
    assert says in str(refused.value)
    assert len(list(m)) == before


def test_a_model_with_no_body_context_gets_one():
    m = ifcopenshell.file.from_string(without(starting(), "#31", "#32"))
    assert not m.by_type("IfcGeometricRepresentationSubContext")
    out = building.add_wall(m, storey="Level 1", start=[0, 0], end=[10, 0], height=8, thickness=0.5)
    rep = m.by_id(out["made"]["id"]).Representation.Representations[0]
    assert rep.ContextOfItems.ContextIdentifier == "Body"
    assert_valid(m)


# --------------------------------------------- a small shell, through the server

#: A 30 x 20 ft shop on the starting model's Level 1: four walls (the east and west ones with sloped tops under a
#: shed roof rising from 10 ft at the south to 14 ft at the north), a floor slab, the roof, and the shop floor room.
SHELL = [
    ("ifc_add_wall", {"storey": "Level 1", "start": [0, 0], "end": [30, 0], "height": 10, "thickness": 0.5, "name": "South wall", "realizes": "cmp:south-wall"}),
    ("ifc_add_wall", {"storey": "Level 1", "start": [30, 0], "end": [30, 20], "height": 10, "height_at_end": 14, "thickness": 0.5, "name": "East wall", "realizes": "cmp:east-wall"}),
    ("ifc_add_wall", {"storey": "Level 1", "start": [30, 20], "end": [0, 20], "height": 14, "thickness": 0.5, "name": "North wall", "realizes": "cmp:north-wall"}),
    ("ifc_add_wall", {"storey": "Level 1", "start": [0, 20], "end": [0, 0], "height": 14, "height_at_end": 10, "thickness": 0.5, "name": "West wall", "realizes": "cmp:west-wall"}),
    ("ifc_add_slab", {"storey": "Level 1", "footprint": [[0, 0], [30, 0], [30, 20], [0, 20]], "thickness": 0.5, "name": "Floor slab", "realizes": "cmp:floor-slab"}),
    ("ifc_add_roof", {"storey": "Level 1", "footprint": [[0, 0], [30, 0], [30, 20], [0, 20]], "kind": "shed", "eave_height": 10, "slope": 0.2, "thickness": 0.5, "overhang": 1, "name": "Roof", "realizes": "cmp:roof"}),
    ("ifc_add_space", {"storey": "Level 1", "footprint": [[0.25, 0.25], [29.75, 0.25], [29.75, 19.75], [0.25, 19.75]], "height": 10, "name": "Shop floor", "realizes": "cmp:shop-floor"}),
]


def server_params():
    from mcp.client.stdio import StdioServerParameters, get_default_environment

    from tests.test_smoke import flo2_ifc_command

    override = shlex.split(os.environ.get("FLO2_IFC_SERVER", ""))
    command, args = (override[0], override[1:]) if override else (flo2_ifc_command(), [])
    env = get_default_environment() | ({"FLO2_IFC_RULES_DIR": os.environ["FLO2_IFC_RULES_DIR"]} if os.environ.get("FLO2_IFC_RULES_DIR") and not override else {})
    return StdioServerParameters(command=command, args=args, env=env)


def answer(result):
    """A tool's answer as data: its structured result when it sent one, else each text block parsed as JSON."""
    texts = [getattr(b, "text", "") for b in result.content]
    assert not result.is_error, texts
    if isinstance(result.structured_content, dict):
        sc = result.structured_content
        return sc["result"] if list(sc) == ["result"] else sc
    parsed = []
    for text in texts:
        try:
            parsed.append(json.loads(text))
        except json.JSONDecodeError:
            parsed.append(text)
    return parsed[0] if len(parsed) == 1 else parsed


def old_way_calls(start_from: str) -> list[tuple[str, dict]]:
    """The same shell as SHELL, made the way the measured chat made it (ifcmcp's ifc_shape and ifc_edit, one
    IfcOpenShell function a call), as call templates: "$name" is the id an earlier call answered."""
    calls: list[tuple[str, dict]] = [("ifc_edit", {"function_path": "classification.add_classification", "params": json.dumps({"classification": "flo2 design"}), "as": "$cls"})]

    def element(n, ifc_class, name, points, magnitude, position, x_axis, z_axis, matrix, part, place="contained"):
        calls.extend(
            [
                ("ifc_shape", {"method": "polyline", "params": json.dumps({"points": points, "closed": True}), "as": f"$c{n}"}),
                ("ifc_shape", {"method": "extrude", "params": {"profile_or_curve": f"$c{n}", "magnitude": magnitude, "position": position, "position_x_axis": x_axis, "position_z_axis": z_axis}, "as": f"$s{n}"}),
                ("ifc_shape", {"method": "get_representation", "params": {"context": "$body", "items": [f"$s{n}"]}, "as": f"$r{n}"}),
                ("ifc_edit", {"function_path": "root.create_entity", "params": {"ifc_class": ifc_class, "name": name}, "as": f"$e{n}"}),
                ("ifc_edit", {"function_path": "geometry.assign_representation", "params": {"product": f"$e{n}", "representation": f"$r{n}"}}),
                ("ifc_edit", {"function_path": "geometry.edit_object_placement", "params": {"product": f"$e{n}", "matrix": matrix}}),
                ("ifc_edit", {"function_path": "classification.add_reference", "params": {"products": [f"$e{n}"], "identification": part, "classification": "$cls"}}),
                (
                    ("ifc_edit", {"function_path": "spatial.assign_container", "params": {"products": [f"$e{n}"], "relating_structure": "$storey"}})
                    if place == "contained"
                    else ("ifc_edit", {"function_path": "aggregate.assign_object", "params": {"products": [f"$e{n}"], "relating_object": "$storey"}})
                ),
            ]
        )

    def at(x, y, angle):  # an SI matrix, as geometry.edit_object_placement takes it
        c, s = math.cos(angle), math.sin(angle)
        return [[c, -s, 0, x * FT], [s, c, 0, y * FT], [0, 0, 1, 0], [0, 0, 0, 1]]

    # Each wall: its elevation outline (x along it, z up), extruded through its thickness.
    for n, (sx, sy, length, angle, h1, h2, part) in enumerate(
        [(0, 0, 30, 0, 10, 10, "cmp:south-wall"), (30, 0, 20, math.pi / 2, 10, 14, "cmp:east-wall"), (30, 20, 30, math.pi, 14, 14, "cmp:north-wall"), (0, 20, 20, -math.pi / 2, 14, 10, "cmp:west-wall")]
    ):
        element(n, "IfcWall", part, [[0, 0], [length, 0], [length, h2], [0, h1]], 0.5, [0, 0.25, 0], [1, 0, 0], [0, -1, 0], at(sx, sy, angle), part)
    element(4, "IfcSlab", "Floor slab", [[0, 0], [30, 0], [30, 20], [0, 20]], 0.5, [0, 0, -0.5], [1, 0, 0], [0, 0, 1], at(0, 0, 0), "cmp:floor-slab")
    # The roof: its section across the slope (y along the slope, z up), extruded along x.
    element(5, "IfcRoof", "Roof", [[-1, 9.8], [21, 14.2], [21, 14.7], [-1, 10.3]], 32, [-1, 0, 0], [0, 1, 0], [1, 0, 0], at(0, 0, 0), "cmp:roof")
    element(6, "IfcSpace", "Shop floor", [[0.25, 0.25], [29.75, 0.25], [29.75, 19.75], [0.25, 19.75]], 10, [0, 0, 0], [1, 0, 0], [0, 0, 1], at(0, 0, 0), "cmp:shop-floor", place="aggregated")
    return calls


def model_on_disk(text: str) -> tuple[str, str]:
    """The model written where the server can open it: (its path here, its path as the server sees it)."""
    folder = os.environ.get("FLO2_IFC_DESIGN_DIR") or tempfile.mkdtemp(prefix="flo2-ifc-shell-")
    Path(folder, "studio.ifc").write_text(text)
    seen_as = os.environ.get("FLO2_IFC_DESIGN_SEEN_AS") or folder
    return str(Path(folder, "studio.ifc")), f"{seen_as}/studio.ifc"


async def build_shell() -> dict:
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client

    _, seen = model_on_disk(starting())
    out: dict = {"steps": []}
    with anyio.fail_after(300):
        async with stdio_client(server_params()) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            # The new way: one call an element.
            answer(await session.call_tool("ifc_load", {"path": seen}))
            calls = 0
            for tool, arguments in SHELL:
                out["steps"].append(answer(await session.call_tool(tool, arguments)))
                calls += 1
            out["calls"] = calls
            out["validate"] = answer(await session.call_tool("ifc_validate", {"express_rules": True}))
            out["boxes"] = [answer(await session.call_tool("ifc_extent", {"element_id": s["made"]["id"]}))["overall"] for i, s in enumerate(out["steps"]) if i != 5]
            out["roof_planes"] = [answer(await session.call_tool("ifc_extent", {"element_id": p["id"]}))["overall"] for p in out["steps"][5]["made"]["planes"]]
            out["infos"] = [answer(await session.call_tool("ifc_info", {"element_id": s["made"]["id"]})) for s in out["steps"]]
            out["refused"] = await session.call_tool("ifc_add_wall", {"storey": "Level 1", "start": [0, 0], "end": [0, 0], "height": 10, "thickness": 0.5})
            if RULES:
                saved = f"{tempfile.gettempdir()}/flo2-ifc-shell-built.ifc"
                answer(await session.call_tool("ifc_save", {"path": saved}))
                answer(await session.call_tool("ifc_load", {"path": saved}))
                out["rules"], start = [], 0
                while start is not None:
                    said = answer(await session.call_tool("ifc_check_rules", {"rule_sets": "normative", "start": start, "limit": 50}))
                    out["rules"].append(said)
                    start = said["next_start"]

            # The old way, on a fresh copy of the same starting model: one IfcOpenShell function a call.
            answer(await session.call_tool("ifc_load", {"path": seen}))
            ids: dict[str, int] = {}
            contexts = answer(await session.call_tool("ifc_contexts", {}))
            ids["$body"] = next(c["id"] for c in contexts if c.get("context_identifier") == "Body")
            ids["$storey"] = answer(await session.call_tool("ifc_select", {"query": 'IfcBuildingStorey, Name = "Level 1"'}))["elements"][0]["id"]
            old = 0
            for tool, spec in old_way_calls(seen):
                spec = dict(spec)
                name = spec.pop("as", None)
                params = spec["params"]
                params = json.loads(params) if isinstance(params, str) else params
                params = json.loads(re.sub(r'"(\$[a-z0-9]+)"', lambda m: str(ids[m.group(1)]), json.dumps(params)))
                if tool == "ifc_edit":
                    # ifcedit takes each value as TEXT, as flo2's door sends it (editParamsAsText).
                    params = {k: v if isinstance(v, str) else json.dumps(v) for k, v in params.items()}
                key = "method" if tool == "ifc_shape" else "function_path"
                got = answer(await session.call_tool(tool, {key: spec[key], "params": json.dumps(params)}))
                assert got.get("ok"), (tool, spec, got)
                old += 1
                if name:
                    result = got["result"]
                    ids[name] = result["id"] if isinstance(result, dict) else result
            out["old_calls"] = old
            out["old_validate"] = answer(await session.call_tool("ifc_validate", {"express_rules": True}))
    return out


@pytest.fixture(scope="module")
def shell() -> dict:
    return anyio.run(build_shell)


def test_the_shell_takes_one_call_an_element_where_the_old_way_took_eight(shell):
    assert shell["calls"] == 7
    # The same shell, the way the measured chat built it: 8 calls an element and one for the classification.
    assert shell["old_calls"] == 7 * 8 + 1
    assert shell["old_validate"]["valid"]


def test_every_element_of_the_shell_is_on_its_storey(shell):
    for info in shell["infos"]:
        assert info["storey"]["name"] == "Level 1", info["storey"]
    # The room is part of its storey; the walls, slab and roof are contained in it.
    assert [i["storey"]["via"][0].split(" #")[0] for i in shell["infos"]] == ["contained in (IfcRelContainedInSpatialStructure)"] * 6 + ["part of (IfcRelAggregates)"]


def test_every_element_of_the_shell_is_linked_to_its_design_part(shell):
    parts = [arguments["realizes"] for _, arguments in SHELL]
    assert [s["realizes"]["identification"] for s in shell["steps"]] == parts
    assert all(s["realizes"]["classification"] == "flo2 design" for s in shell["steps"])


def test_every_element_of_the_shell_has_its_geometry(shell):
    south, east, north, west, slab, room = shell["boxes"]
    assert south["min"] == pytest.approx([0, -0.25, 0], abs=1e-3) and south["max"] == pytest.approx([30, 0.25, 10], abs=1e-3)
    assert east["top"] == pytest.approx(14, abs=1e-3) and west["top"] == pytest.approx(14, abs=1e-3) and north["top"] == pytest.approx(14, abs=1e-3)
    assert slab["top"] == pytest.approx(0, abs=1e-3) and slab["bottom"] == pytest.approx(-0.5, abs=1e-3)
    assert room["top"] == pytest.approx(10, abs=1e-3)
    (plane,) = shell["roof_planes"]
    assert plane["bottom"] == pytest.approx(10 - 0.2, abs=1e-3)  # the 1 ft overhang drops the eave 0.2 ft
    assert plane["min"][:2] == pytest.approx([-1, -1], abs=1e-3) and plane["max"][:2] == pytest.approx([31, 21], abs=1e-3)
    # The IfcRoof holds its plane (IfcRelAggregates), which carries the shape.
    assert shell["steps"][5]["made"]["class"] == "IfcRoof"


def test_the_shell_validates(shell):
    assert shell["validate"]["complete"] and shell["validate"]["valid"], shell["validate"].get("issues")


@needs_rules
def test_the_shell_passes_buildingsmarts_normative_rules(shell):
    calls = shell["rules"]
    assert calls[-1]["complete"]
    assert not any(c.get("rules_not_finished") for c in calls)
    errors = [i for c in calls for i in c["issues"] if i["severity"] == "error"]
    assert sum(c["summary"]["errors"] for c in calls) == 0, errors
    assert sum(c["summary"]["rules_run"] for c in calls) > 0


def test_a_refused_step_says_why_through_the_server(shell):
    refused = shell["refused"]
    assert refused.is_error
    assert "start and end are the same point" in "".join(getattr(b, "text", "") for b in refused.content)
