"""ifc_clash on an element set flush between the two jambs of an opening: a KNOWN FALSE RESULT in IfcOpenShell 0.9.0.

fact:ifc-clash-reports-an-element-touching-both-jambs-as-piercing-the-wall-2026-10-26 in IfcOpenShell's design
(23d874b1643673ad), reported on the Shop sample (671a764fa790c030,
fact:ifc-clash-still-ignores-openings-in-flo2-ifc-0-8-0-2026-10-25): a guard rail standing in an opening, exactly as
long as the opening, was reported piercing the wall as deep as the wall is thick. The clash DOES cut openings. The
false result is IfcOpenShell's clash tree (src/ifcgeom/kernels/opencascade/bvh_tree.h, pierce_shape): it takes the
first and last places an edge crosses the other shape as a piercing, and the rail's long edges cross the two jambs at
their own ends, where rounding leaves both crossings inside the edge. Whether it shows depends on rounding: on this
model in feet, on many of 36 turns.

ifc_clash is ifcmcp's (ifcquery.clash), and the fix belongs in IfcOpenShell, never here (AGENTS.md). So this test
is expected to FAIL until a pinned ifcopenshell carries the fix; strict, so the release that fixes it turns this
red and the mark comes off.
"""

from __future__ import annotations

import math

import ifcopenshell
import ifcopenshell.api
import numpy as np
import pytest
from ifcquery import clash

run = ifcopenshell.api.run
TURNS = list(range(0, 180, 5))


def wall_with_a_rail(turn: float, rail_z: tuple[float, float] = (10.0, 13.5)):
    """In feet: a wall 6 in thick, 30 ft long and 20 ft high with an opening 20 ft long from 2 ft along it, between
    10 and 18 ft high, and a rail 1.5 in thick from jamb to jamb, between rail_z; all turned `turn` degrees."""
    f = run("project.create_file", version="IFC4")
    project = run("root.create_entity", f, ifc_class="IfcProject", name="Rail")
    run("unit.assign_unit", f, units=[run("unit.add_conversion_based_unit", f, name="foot")])
    model3d = run("context.add_context", f, context_type="Model")
    body = run("context.add_context", f, context_type="Model", context_identifier="Body", target_view="MODEL_VIEW", parent=model3d)
    storey = run("root.create_entity", f, ifc_class="IfcBuildingStorey", name="L1")
    run("aggregate.assign_object", f, relating_object=project, products=[storey])
    a = math.radians(turn)
    m = np.eye(4)
    m[:2, :2] = [[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]]

    def box(cls, name, x, y, z, contained=True):
        (x0, x1), (y0, y1), (z0, z1) = x, y, z
        points = [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
        profile = f.createIfcArbitraryClosedProfileDef("AREA", None, f.createIfcPolyline([f.createIfcCartesianPoint(p) for p in points]))
        position = f.createIfcAxis2Placement3D(f.createIfcCartesianPoint((0.0, 0.0, z0)))
        solid = f.createIfcExtrudedAreaSolid(profile, position, f.createIfcDirection((0.0, 0.0, 1.0)), z1 - z0)
        e = run("root.create_entity", f, ifc_class=cls, name=name)
        run("geometry.assign_representation", f, product=e, representation=f.createIfcShapeRepresentation(body, "Body", "SweptSolid", [solid]))
        # A copy: edit_object_placement scales the matrix it is given in place.
        run("geometry.edit_object_placement", f, product=e, matrix=m.copy(), is_si=False)
        if contained:
            run("spatial.assign_container", f, relating_structure=storey, products=[e])
        return e

    wall = box("IfcWall", "Wall", (0.0, 0.5), (0.0, 30.0), (0.0, 20.0))
    opening = box("IfcOpeningElement", "Opening", (-0.01, 0.51), (2.0, 22.0), (10.0, 18.0), contained=False)
    run("feature.add_feature", f, feature=opening, element=wall)
    rail = box("IfcRailing", "Rail", (0.1875, 0.3125), (2.0, 22.0), rail_z)
    return f, wall, rail


@pytest.mark.xfail(
    strict=True,
    reason="IfcOpenShell 0.9.0's clash tree reports an element touching both jambs as piercing the wall "
    "(23d874b1643673ad, fact:ifc-clash-reports-an-element-touching-both-jambs-as-piercing-the-wall-2026-10-26)",
)
def test_a_rail_flush_between_the_jambs_of_an_opening_does_not_clash_with_the_wall():
    failed = {}
    for turn in TURNS:
        f, wall, rail = wall_with_a_rail(turn)
        out = clash.clash(f, rail, scope="all")
        if not out["pass"]:
            failed[turn] = [(c["type"], round(c["distance"], 4)) for c in out["checks"]["intersection"]["clashes"]]
    assert failed == {}


def test_a_rail_through_the_solid_head_of_the_wall_clashes_with_it():
    # The control: the same rail reaching above the opening's head runs inside the wall's solid part.
    for turn in (0, 25, 65):
        f, wall, rail = wall_with_a_rail(turn, rail_z=(10.0, 19.0))
        out = clash.clash(f, rail, scope="all")
        assert out["pass"] is False and len(out["checks"]["intersection"]["clashes"]) == 1
