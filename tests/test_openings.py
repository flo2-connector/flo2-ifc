"""Openings are cut in what a chat measures and draws.

fact:measures-and-drawings-skip-opening-subtraction-so-a-cut-wall-reads-whole-2026-10-25: the meshes behind
ifc_volume, ifc_footprint, ifc_distance and ifc_draw were made with IfcOpenShell's opening subtraction switched off,
a setting chosen for ifc_extent's boxes, so a wall with an opening made in the model was measured and drawn whole
(3.0 m3 for a wall that holds 2.622 m3, a section through a door drawn solid, an elevation with no door). The
accepted fix (dec:building-steps-take-a-frame-and-measures-cut-openings, part 2) gives the meshes their own setting,
openings cut, kept per setting; the boxes keep the fast one. tests/models.py, wall_with_a_door, is the model.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

import pytest
from shapely.geometry import Polygon

from flo2_ifc import drawing as D
from flo2_ifc import geometry as G
from flo2_ifc import reading as R
from tests import models

SVG = "{http://www.w3.org/2000/svg}"


@pytest.fixture()
def door_model():
    G.changed()
    return models.wall_with_a_door()


def one(model, cls):
    (e,) = model.by_type(cls)
    return e


def path_of(root, cls, ifc):
    found = [p for p in root.iter(f"{SVG}path") if cls in (p.get("class") or "").split() and p.get("data-ifc") == ifc]
    assert len(found) == 1, (cls, ifc, len(found))
    return found[0]


def rings(d: str) -> list[list[tuple[float, float]]]:
    """The rings of an SVG path written as `M x y x y ... Z`, in paper millimetres."""
    out = []
    for part in [p for p in d.split("M") if p.strip()]:
        nums = [float(n) for n in re.findall(r"-?\d+(?:\.\d+)?", part)]
        out.append(list(zip(nums[0::2], nums[1::2])))
    return out


def area_of(d: str):
    shape = Polygon()
    for ring in rings(d):
        shape = shape.symmetric_difference(Polygon(ring).buffer(0))
    return shape


def test_a_walls_volume_leaves_out_its_opening(door_model):
    wall = one(door_model, "IfcWall")
    (length, height, thick), (width, high) = models.DOOR["wall"], models.DOOR["opening"]
    expected = length * height * thick - width * high * thick
    assert expected == pytest.approx(2.622)
    out = G.volume(door_model, element_id=wall.id())
    assert out["elements"][0]["mesh"] == "closed"
    assert out["total"]["model"] == pytest.approx(expected, abs=1e-6)


def test_a_walls_box_is_unchanged_by_its_opening(door_model):
    # ifc_extent keeps the fast setting: an opening only removes material, so it cannot widen a box.
    wall = one(door_model, "IfcWall")
    box = R.extent(door_model, element_id=wall.id())["overall"]
    assert box["min"] == pytest.approx([0, -0.1, 0], abs=1e-6)
    assert box["max"] == pytest.approx([5, 0.1, 3], abs=1e-6)


def test_a_section_through_the_door_cuts_the_wall_only_above_its_opening(door_model, tmp_path):
    door = one(door_model, "IfcDoor")
    out = D.draw(door_model, str(tmp_path / "section"), view="section", through=str(door.id()), direction="north-south")
    root = ET.parse(out["drawn"]["svg"]).getroot()
    ratio = out["sheet"]["ratio"]
    cut_wall = area_of(path_of(root, "cut", "IfcWall").get("d"))
    tall_m = (cut_wall.bounds[3] - cut_wall.bounds[1]) * ratio / 1000
    # The wall is cut only over the opening's head: 3 - 2.1 = 0.9 m, never its whole 3 m.
    assert tall_m == pytest.approx(models.DOOR["wall"][1] - models.DOOR["opening"][1], abs=0.01)
    # And the door leaf is cut, in the opening.
    cut_door = area_of(path_of(root, "cut", "IfcDoor").get("d"))
    assert (cut_door.bounds[3] - cut_door.bounds[1]) * ratio / 1000 == pytest.approx(models.DOOR["opening"][1], abs=0.01)


def test_an_elevation_shows_the_door_in_its_opening(door_model, tmp_path):
    out = D.draw(door_model, str(tmp_path / "south"), view="elevation", side="south")
    root = ET.parse(out["drawn"]["svg"]).getroot()
    wall = area_of(path_of(root, "seen", "IfcWall").get("d"))
    door = area_of(path_of(root, "seen", "IfcDoor").get("d"))
    # The wall seen from the south no longer covers the door: the opening is a notch in its outline...
    assert wall.intersection(door).area < 0.01 * door.area
    assert not wall.contains(door.representative_point())
    # ...as wide and as high as the opening.
    ratio = out["sheet"]["ratio"]
    w, h = (door.bounds[2] - door.bounds[0]) * ratio / 1000, (door.bounds[3] - door.bounds[1]) * ratio / 1000
    assert (w, h) == pytest.approx(models.DOOR["opening"], abs=0.01)


def test_a_plan_cut_through_the_door_shows_a_gap_in_the_wall(door_model, tmp_path):
    out = D.draw(door_model, str(tmp_path / "plan"), view="plan", storey="L1", cut_height=1.0)
    root = ET.parse(out["drawn"]["svg"]).getroot()
    cut_wall = area_of(path_of(root, "cut", "IfcWall").get("d"))
    assert cut_wall.geom_type == "MultiPolygon" and len(cut_wall.geoms) == 2
    ratio = out["sheet"]["ratio"]
    lengths = sorted((g.bounds[2] - g.bounds[0]) * ratio / 1000 for g in cut_wall.geoms)
    assert lengths == pytest.approx([2.0, 5.0 - 2.0 - 0.9], abs=0.01)


def test_meshes_are_kept_per_setting(door_model):
    wall = one(door_model, "IfcWall")
    import time

    far = time.monotonic() + 60
    cut, _ = G.meshes(door_model, [wall], far)
    whole, _ = G.meshes(door_model, [wall], far, cut_openings=False)
    assert G.closure(cut[wall.id()])["status"] == G.closure(whole[wall.id()])["status"] == "closed"
    assert G.signed_volume(cut[wall.id()]) == pytest.approx(2.622, abs=1e-6)
    assert G.signed_volume(whole[wall.id()]) == pytest.approx(3.0, abs=1e-6)
    # Each is kept under its own setting: asked again, each comes back as it was made.
    assert G.meshes(door_model, [wall], far)[0][wall.id()] is cut[wall.id()]
    assert G.meshes(door_model, [wall], far, cut_openings=False)[0][wall.id()] is whole[wall.id()]
