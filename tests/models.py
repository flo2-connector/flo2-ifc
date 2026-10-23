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
