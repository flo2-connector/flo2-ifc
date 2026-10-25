"""Synthetic building models for the tests, made with IfcOpenShell's API.

No real person's model is ever a fixture here. These are built from nothing,
in memory, with sizes the tests know, in millimetres like a Revit export:

- a project, a site, a building and two storeys (0 and 3000 mm), each storey
  with a room (IfcSpace) that is PART of it, as Revit places rooms;
- `walls` walls on the ground floor, each 4000 x 200 x 3000 mm, contained in it;
- a door on the first floor, and a roof slab whose top is at 6500 mm.
"""

from __future__ import annotations

import ifcopenshell
import ifcopenshell.api
import numpy as np


def _place(model, product, x=0.0, y=0.0, z=0.0):
    """Place `product` at (x, y, z) given in METRES: ifcopenshell.api converts
    an SI matrix to the model's own unit (is_si defaults to True)."""
    m = np.eye(4)
    m[:3, 3] = (x, y, z)
    ifcopenshell.api.run("geometry.edit_object_placement", model, product=product, matrix=m)


def building(walls: int = 3, schema: str = "IFC4") -> ifcopenshell.file:
    run = ifcopenshell.api.run
    f = run("project.create_file", version=schema)
    project = run("root.create_entity", f, ifc_class="IfcProject", name="Test house")
    mm = run("unit.add_si_unit", f, unit_type="LENGTHUNIT", prefix="MILLI")
    m2 = run("unit.add_si_unit", f, unit_type="AREAUNIT")
    m3 = run("unit.add_si_unit", f, unit_type="VOLUMEUNIT")
    run("unit.assign_unit", f, units=[mm, m2, m3])
    model3d = run("context.add_context", f, context_type="Model")
    body = run("context.add_context", f, context_type="Model", context_identifier="Body", target_view="MODEL_VIEW", parent=model3d)

    site = run("root.create_entity", f, ifc_class="IfcSite", name="Site")
    bldg = run("root.create_entity", f, ifc_class="IfcBuilding", name="House")
    ground = run("root.create_entity", f, ifc_class="IfcBuildingStorey", name="GROUND")
    first = run("root.create_entity", f, ifc_class="IfcBuildingStorey", name="FIRST")
    ground.Elevation = 0.0
    first.Elevation = 3000.0
    run("aggregate.assign_object", f, relating_object=project, products=[site])
    run("aggregate.assign_object", f, relating_object=site, products=[bldg])
    run("aggregate.assign_object", f, relating_object=bldg, products=[ground, first])
    for p in (site, bldg, ground):
        _place(f, p)
    _place(f, first, z=3.0)

    for storey, name in ((ground, "Kitchen"), (first, "Bedroom")):
        space = run("root.create_entity", f, ifc_class="IfcSpace", name=name)
        run("aggregate.assign_object", f, relating_object=storey, products=[space])
        _place(f, space, z=storey.Elevation / 1000)
        qto = run("pset.add_qto", f, product=space, name="Qto_SpaceBaseQuantities")
        run("pset.edit_qto", f, qto=qto, properties={"Height": 2700.0, "NetFloorArea": 12.5})

    for i in range(walls):
        wall = run("root.create_entity", f, ifc_class="IfcWall", name=f"Wall {i:03d}")
        rep = run("geometry.add_wall_representation", f, context=body, length=4.0, height=3.0, thickness=0.2)
        run("geometry.assign_representation", f, product=wall, representation=rep)
        _place(f, wall, x=0.0, y=i * 1.0, z=0.0)
        run("spatial.assign_container", f, relating_structure=ground, products=[wall])

    door = run("root.create_entity", f, ifc_class="IfcDoor", name="Front door")
    rep = run("geometry.add_wall_representation", f, context=body, length=0.9, height=2.1, thickness=0.05)
    run("geometry.assign_representation", f, product=door, representation=rep)
    _place(f, door, x=1.0, y=0.0, z=3.0)
    run("spatial.assign_container", f, relating_structure=first, products=[door])

    roof = run("root.create_entity", f, ifc_class="IfcRoof", name="Flat roof")
    rep = run("geometry.add_slab_representation", f, context=body, depth=0.5)
    run("geometry.assign_representation", f, product=roof, representation=rep)
    _place(f, roof, x=0.0, y=0.0, z=6.0)
    run("spatial.assign_container", f, relating_structure=first, products=[roof])
    return f


#: The known numbers of measured_site(), in metres.
SITE = {
    "footing": (0.0, 0.0, -0.5, 4.0, 4.0, 0.5),  # x, y, z, size x, size y, height
    "slab": (0.0, 0.0, 0.0, 6.0, 4.0, 0.2),
    "wall_a_y": 0.0,  # wall A runs along x from y 0 to 0.2
    "wall_b_y": 2.0,  # wall B from y 2.0 to 2.2: a clear gap of 1.8 m
    "closed_box": (1.0, 2.0, 3.0),  # a closed 1 x 2 x 3 m box: 6 m3
}


def _box(x0, y0, z0, sx, sy, sz, top=True):
    """A box's corners and its faces as triangles, wound outward; without its top when `top` is false."""
    v = [(x0 + dx * sx, y0 + dy * sy, z0 + dz * sz) for dz in (0, 1) for dy in (0, 1) for dx in (0, 1)]
    quads = [(0, 2, 3, 1), (4, 5, 7, 6), (0, 1, 5, 4), (2, 6, 7, 3), (0, 4, 6, 2), (1, 3, 7, 5)]
    if not top:
        quads = [q for q in quads if q != (4, 5, 7, 6)]
    tris = [t for a, b, c, d in quads for t in ((a, b, c), (a, c, d))]
    return v, tris


def measured_site(schema: str = "IFC4") -> ifcopenshell.file:
    """A model with sizes the measures and drawings are checked against (SITE), in millimetres:

    - GROUND (0) and FIRST (3000 mm) storeys, and a grid with lines "A" (x = 1 m) and "1" (y = 1 m);
    - a footing (4 x 4 x 0.5 m, top at 0) under a slab (6 x 4 x 0.2 m): stacked over 16 m2;
    - two parallel walls 4 x 0.2 x 3 m, 1.8 m apart (A at y 0, B at y 2.0);
    - a closed meshed box 1 x 2 x 3 m (6 m3) and the same box OPEN (no top), at x 8;
    - a room (IfcSpace) 4 x 4 m with a shape, named "Room 1";
    all on GROUND."""
    run = ifcopenshell.api.run
    f = run("project.create_file", version=schema)
    project = run("root.create_entity", f, ifc_class="IfcProject", name="Measured site")
    mm = run("unit.add_si_unit", f, unit_type="LENGTHUNIT", prefix="MILLI")
    m2 = run("unit.add_si_unit", f, unit_type="AREAUNIT")
    m3 = run("unit.add_si_unit", f, unit_type="VOLUMEUNIT")
    run("unit.assign_unit", f, units=[mm, m2, m3])
    model3d = run("context.add_context", f, context_type="Model")
    body = run("context.add_context", f, context_type="Model", context_identifier="Body", target_view="MODEL_VIEW", parent=model3d)
    site = run("root.create_entity", f, ifc_class="IfcSite", name="Site")
    bldg = run("root.create_entity", f, ifc_class="IfcBuilding", name="Shop")
    ground = run("root.create_entity", f, ifc_class="IfcBuildingStorey", name="GROUND")
    first = run("root.create_entity", f, ifc_class="IfcBuildingStorey", name="FIRST")
    ground.Elevation, first.Elevation = 0.0, 3000.0
    run("aggregate.assign_object", f, relating_object=project, products=[site])
    run("aggregate.assign_object", f, relating_object=site, products=[bldg])
    run("aggregate.assign_object", f, relating_object=bldg, products=[ground, first])
    for p in (site, bldg, ground):
        _place(f, p)
    _place(f, first, z=3.0)

    def element(cls, name, rep, x=0.0, y=0.0, z=0.0, storey=ground):
        e = run("root.create_entity", f, ifc_class=cls, name=name)
        run("geometry.assign_representation", f, product=e, representation=rep)
        _place(f, e, x=x, y=y, z=z)
        run("spatial.assign_container", f, relating_structure=storey, products=[e])
        return e

    fx, fy, fz, fsx, fsy, fh = SITE["footing"]
    element("IfcFooting", "Footing", run("geometry.add_slab_representation", f, context=body, depth=fh, polyline=[(0, 0), (fsx, 0), (fsx, fsy), (0, fsy)]), fx, fy, fz)
    sx, sy, sz, ssx, ssy, sh = SITE["slab"]
    element("IfcSlab", "Floor slab", run("geometry.add_slab_representation", f, context=body, depth=sh, polyline=[(0, 0), (ssx, 0), (ssx, ssy), (0, ssy)]), sx, sy, sz)
    for name, y in (("Wall A", SITE["wall_a_y"]), ("Wall B", SITE["wall_b_y"])):
        element("IfcWall", name, run("geometry.add_wall_representation", f, context=body, length=4.0, height=3.0, thickness=0.2), 0.0, y, 0.2)
    bx, by, bz = SITE["closed_box"]
    for name, top, x in (("Closed box", True, 8.0), ("Open box", False, 10.0)):
        v, t = _box(0, 0, 0, bx, by, bz, top=top)
        element("IfcBuildingElementProxy", name, run("geometry.add_mesh_representation", f, context=body, vertices=[v], faces=[t]), x, 0.0, 0.0)

    space = run("root.create_entity", f, ifc_class="IfcSpace", name="Room 1")
    run("aggregate.assign_object", f, relating_object=ground, products=[space])
    rep = run("geometry.add_slab_representation", f, context=body, depth=2.7, polyline=[(0, 0), (4, 0), (4, 4), (0, 4)])
    run("geometry.assign_representation", f, product=space, representation=rep)
    _place(f, space, x=0.0, y=0.0, z=0.2)

    grid = run("root.create_entity", f, ifc_class="IfcGrid", name="Grid")
    _place(f, grid)
    run("spatial.assign_container", f, relating_structure=ground, products=[grid])
    a = run("grid.create_grid_axis", f, grid=grid, axis_tag="A", uvw_axes="UAxes")
    a.AxisCurve = f.createIfcPolyline([f.createIfcCartesianPoint((1000.0, -2000.0)), f.createIfcCartesianPoint((1000.0, 6000.0))])
    one = run("grid.create_grid_axis", f, grid=grid, axis_tag="1", uvw_axes="VAxes")
    one.AxisCurve = f.createIfcPolyline([f.createIfcCartesianPoint((-2000.0, 1000.0)), f.createIfcCartesianPoint((14000.0, 1000.0))])
    return f


def rooms_on_a_floor(rooms: list[tuple[str, tuple[float, float, float, float]]], schema: str = "IFC4") -> ifcopenshell.file:
    """One storey, "Level 1", with a floor slab 12 x 6 m and the rooms given,
    each as (name, (x0, y0, x1, y1)) in metres, 2.7 m tall: a room may lie
    inside another, as a zone is traced inside the room that holds it."""
    run = ifcopenshell.api.run
    f = run("project.create_file", version=schema)
    project = run("root.create_entity", f, ifc_class="IfcProject", name="Rooms")
    mm = run("unit.add_si_unit", f, unit_type="LENGTHUNIT", prefix="MILLI")
    m2 = run("unit.add_si_unit", f, unit_type="AREAUNIT")
    run("unit.assign_unit", f, units=[mm, m2])
    model3d = run("context.add_context", f, context_type="Model")
    body = run("context.add_context", f, context_type="Model", context_identifier="Body", target_view="MODEL_VIEW", parent=model3d)
    site = run("root.create_entity", f, ifc_class="IfcSite", name="Site")
    bldg = run("root.create_entity", f, ifc_class="IfcBuilding", name="Building")
    level = run("root.create_entity", f, ifc_class="IfcBuildingStorey", name="Level 1")
    level.Elevation = 0.0
    run("aggregate.assign_object", f, relating_object=project, products=[site])
    run("aggregate.assign_object", f, relating_object=site, products=[bldg])
    run("aggregate.assign_object", f, relating_object=bldg, products=[level])
    for p in (site, bldg, level):
        _place(f, p)
    slab = run("root.create_entity", f, ifc_class="IfcSlab", name="Floor slab")
    run("geometry.assign_representation", f, product=slab, representation=run("geometry.add_slab_representation", f, context=body, depth=0.2, polyline=[(0, 0), (12, 0), (12, 6), (0, 6)]))
    _place(f, slab, z=-0.2)
    run("spatial.assign_container", f, relating_structure=level, products=[slab])
    for name, (x0, y0, x1, y1) in rooms:
        space = run("root.create_entity", f, ifc_class="IfcSpace", name=name)
        run("aggregate.assign_object", f, relating_object=level, products=[space])
        rep = run("geometry.add_slab_representation", f, context=body, depth=2.7, polyline=[(0, 0), (x1 - x0, 0), (x1 - x0, y1 - y0), (0, y1 - y0)])
        run("geometry.assign_representation", f, product=space, representation=rep)
        _place(f, space, x=x0, y=y0, z=0.0)
    return f


#: A turned and shifted building (turned_storey): its plan is turned this many degrees about the vertical, its
#: origin is at TURN_ORIGIN (metres, world), and its storey "L1" stands TURN_ORIGIN's z above it.
TURN_DEGREES = 26.03
TURN_ORIGIN = (10.0, 5.0, 3.0)


def _turned(degrees: float, x: float, y: float, z: float) -> np.ndarray:
    m = np.eye(4)
    c, s = np.cos(np.radians(degrees)), np.sin(np.radians(degrees))
    m[:2, :2] = [[c, -s], [s, c]]
    m[:3, 3] = (x, y, z)
    return m


def turned_storey(turn_at: str = "site", schema: str = "IFC4") -> ifcopenshell.file:
    """A model in metres whose storey "L1" is turned TURN_DEGREES and shifted to TURN_ORIGIN in world: the turn is in
    the site's placement (`turn_at="site"`) or in the building's (`"building"`), as a model drawn square to its own
    walls and set on its plot is (fact:building-steps-take-plan-points-in-world-coordinates-not-the-storeys-frame-
    2026-10-25). Each placement is given as its WORLD matrix: edit_object_placement makes it relative to its parent."""
    if turn_at not in ("site", "building"):
        raise ValueError(turn_at)
    run = ifcopenshell.api.run
    f = run("project.create_file", version=schema)
    project = run("root.create_entity", f, ifc_class="IfcProject", name="Turned")
    run("unit.assign_unit", f, units=[run("unit.add_si_unit", f, unit_type="LENGTHUNIT")])
    model3d = run("context.add_context", f, context_type="Model")
    run("context.add_context", f, context_type="Model", context_identifier="Body", target_view="MODEL_VIEW", parent=model3d)
    site = run("root.create_entity", f, ifc_class="IfcSite", name="Site")
    bldg = run("root.create_entity", f, ifc_class="IfcBuilding", name="Building")
    storey = run("root.create_entity", f, ifc_class="IfcBuildingStorey", name="L1")
    storey.Elevation = TURN_ORIGIN[2]
    run("aggregate.assign_object", f, relating_object=project, products=[site])
    run("aggregate.assign_object", f, relating_object=site, products=[bldg])
    run("aggregate.assign_object", f, relating_object=bldg, products=[storey])
    x, y, z = TURN_ORIGIN
    at_site = _turned(TURN_DEGREES if turn_at == "site" else 0.0, x, y, 0.0)
    at_building = _turned(TURN_DEGREES, x, y, 0.0)
    run("geometry.edit_object_placement", f, product=site, matrix=at_site)
    run("geometry.edit_object_placement", f, product=bldg, matrix=at_building.copy())
    run("geometry.edit_object_placement", f, product=storey, matrix=_turned(TURN_DEGREES, x, y, z))
    return f


#: wall_with_a_door's sizes, in metres: a wall 5 long, 3 high, 0.2 thick along x from the origin, centred on y = 0;
#: an opening 0.9 wide and 2.1 high through it, 2 m from its start; a door 0.05 thick filling it.
DOOR = {"wall": (5.0, 3.0, 0.2), "opening_at": 2.0, "opening": (0.9, 2.1), "door_thickness": 0.05}


def wall_with_a_door(schema: str = "IFC4") -> ifcopenshell.file:
    """One storey "L1" (metres) holding one wall with an opening cut in it (feature.add_feature) and a door filling
    the opening (feature.add_filling), made the way the skill's recipe makes them
    (fact:measures-and-drawings-skip-opening-subtraction-so-a-cut-wall-reads-whole-2026-10-25). The wall's volume
    with its opening cut is 5 x 3 x 0.2 - 0.9 x 2.1 x 0.2 = 2.622 m3."""
    run = ifcopenshell.api.run
    f = run("project.create_file", version=schema)
    project = run("root.create_entity", f, ifc_class="IfcProject", name="Door")
    run("unit.assign_unit", f, units=[run("unit.add_si_unit", f, unit_type="LENGTHUNIT")])
    model3d = run("context.add_context", f, context_type="Model")
    body = run("context.add_context", f, context_type="Model", context_identifier="Body", target_view="MODEL_VIEW", parent=model3d)
    site = run("root.create_entity", f, ifc_class="IfcSite", name="Site")
    bldg = run("root.create_entity", f, ifc_class="IfcBuilding", name="Building")
    storey = run("root.create_entity", f, ifc_class="IfcBuildingStorey", name="L1")
    storey.Elevation = 0.0
    run("aggregate.assign_object", f, relating_object=project, products=[site])
    run("aggregate.assign_object", f, relating_object=site, products=[bldg])
    run("aggregate.assign_object", f, relating_object=bldg, products=[storey])
    for p in (site, bldg, storey):
        _place(f, p)
    length, height, thick = DOOR["wall"]
    wall = run("root.create_entity", f, ifc_class="IfcWall", name="Wall")
    run("geometry.assign_representation", f, product=wall, representation=run("geometry.add_wall_representation", f, context=body, length=length, height=height, thickness=thick))
    _place(f, wall, y=-thick / 2)
    run("spatial.assign_container", f, relating_structure=storey, products=[wall])
    width, high = DOOR["opening"]
    opening = run("root.create_entity", f, ifc_class="IfcOpeningElement", name="Opening")
    run("geometry.assign_representation", f, product=opening, representation=run("geometry.add_wall_representation", f, context=body, length=width, height=high, thickness=2 * thick))
    _place(f, opening, x=DOOR["opening_at"], y=-thick)
    run("feature.add_feature", f, feature=opening, element=wall)
    door = run("root.create_entity", f, ifc_class="IfcDoor", name="Door")
    run("geometry.assign_representation", f, product=door, representation=run("geometry.add_wall_representation", f, context=body, length=width, height=high, thickness=DOOR["door_thickness"]))
    _place(f, door, x=DOOR["opening_at"], y=-DOOR["door_thickness"] / 2)
    run("feature.add_filling", f, opening=opening, element=door)
    run("spatial.assign_container", f, relating_structure=storey, products=[door])
    return f
