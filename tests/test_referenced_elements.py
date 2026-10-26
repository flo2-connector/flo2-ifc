"""A storey's elements include those REFERENCED into it, not only those it contains.

fact:loft-plan-still-omits-walls-referenced-into-the-loft-2026-10-25 (Shop sample 671a764fa790c030): a plan of the
Loft left out the wing's outer walls, which rise from the Ground floor and are referenced into the Loft
(IfcRelReferencedInSpatialStructure), though it drew the loft windows set in them. IFC places an element that spans
storeys in one of them (contained) and references it into the others, so that it is part of each. The cause
(1510fc3728665dbf, fact:a-storeys-readers-walk-only-what-it-contains-not-what-is-referenced-into-it-2026-10-26): the
readers that answer "what is on this storey" (drawing.storey_elements, behind a plan's and a section's storey, and
reading.tree, behind ifc_tree) walked only containment and decomposition. tests/models.py, through_two_storeys, is
the model.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from flo2_ifc import drawing as D
from flo2_ifc import geometry as G
from flo2_ifc import reading as R
from tests import models

SVG = "{http://www.w3.org/2000/svg}"


@pytest.fixture()
def model():
    G.changed()
    return models.through_two_storeys()


def named(model, name):
    (e,) = [e for e in model.by_type("IfcElement") if e.Name == name]
    return e


def drawn(out, cls=None):
    """The ids of the elements a drawing drew (as `cls`, "cut" or "seen", when given)."""
    root = ET.parse(out["drawn"]["svg"]).getroot()
    return {
        int(p.get("data-id"))
        for p in root.iter(f"{SVG}path")
        if p.get("data-id") and (cls is None or cls in (p.get("class") or "").split())
    }


def test_a_plan_of_the_upper_storey_cuts_the_wall_that_rises_through_it(model, tmp_path):
    out = D.draw(model, str(tmp_path / "loft"), view="plan", storey="LOFT", cut_height=1.2)
    wall, window, partition = named(model, "Outer wall"), named(model, "Loft window"), named(model, "Loft partition")
    cut = drawn(out, "cut")
    assert {wall.id(), window.id(), partition.id()} <= cut, "the wall the loft window sits in is cut too"


def test_a_plan_of_the_upper_storey_leaves_out_the_lower_storeys_fillings(model, tmp_path):
    # The wall is part of LOFT; the door in it on GROUND is not.
    out = D.draw(model, str(tmp_path / "loft"), view="plan", storey="LOFT", cut_height=1.2)
    assert named(model, "Ground door").id() not in drawn(out)


def test_a_plan_of_the_lower_storey_is_unchanged(model, tmp_path):
    out = D.draw(model, str(tmp_path / "ground"), view="plan", storey="GROUND", cut_height=1.2)
    wall, door = named(model, "Outer wall"), named(model, "Ground door")
    assert {wall.id(), door.id()} <= drawn(out, "cut")
    assert named(model, "Loft partition").id() not in drawn(out)


def test_a_section_of_the_upper_storey_includes_the_wall(model, tmp_path):
    window = named(model, "Loft window")
    out = D.draw(model, str(tmp_path / "s"), view="section", storey="LOFT", through=str(window.id()), direction="north-south")
    assert named(model, "Outer wall").id() in drawn(out, "cut")


def test_the_storeys_elements_are_its_own_and_those_referenced_into_it(model):
    elements, _ = D.storey_elements(R.find_storey(model, "LOFT"))
    names = sorted(e.Name for e in elements)
    assert names == ["Loft partition", "Loft window", "Outer wall"]


def test_the_tree_lists_an_element_referenced_into_a_storey_and_says_so(model):
    t = R.tree(model, storey="LOFT")
    loft = t["tree"]
    assert loft["element_counts"] == {"IfcWall": 1, "IfcWindow": 1}
    assert loft["referenced_counts"] == {"IfcWall": 1}
    listed = {e["name"]: e for e in loft["elements"]}
    assert set(listed) == {"Loft partition", "Loft window", "Outer wall"}
    assert listed["Outer wall"].get("referenced") is True
    assert "referenced" not in listed["Loft window"]
    ground = R.tree(model, storey="GROUND")["tree"]
    assert "referenced_counts" not in ground
    assert {e["name"] for e in ground["elements"]} == {"Outer wall", "Ground door"}


def test_info_names_the_storeys_an_element_is_referenced_into(model):
    wall = R.info_extra(model, named(model, "Outer wall"))
    assert wall["storey"]["name"] == "GROUND"
    assert [s["name"] for s in wall["referenced_into"]] == ["LOFT"]
    assert "referenced_into" not in R.info_extra(model, named(model, "Loft window"))
