# Shapes, positions and openings

Part of the skill read-a-building-model; SKILL.md holds the rules this page follows.

For anything the building steps (`references/editing.md`) do not make. Each recipe below was run on a copy of a real model before it was written
here. The ids in the steps
are whatever the previous step answered.

**Units, before any length.** Read `ifc_units` first. The geometry functions (`geometry.add_wall_representation`,
`add_slab_representation`, `add_profile_representation`, `add_mesh_representation`, and
`edit_object_placement`'s matrix) take METRES whatever the model's unit, and convert. A value set directly (a
profile's `XDim` or `YDim` through `attribute.edit_attributes`, a property or a quantity) and every value given to
the shape builder (`ifc_shape`) are in the model's OWN unit. On a millimetre model the tank below is
`"XDim": 1200, "YDim": 750` and `"depth": 0.9`. `ifc_clash` takes its distances in metres.

**Place a sized piece of equipment** (a tank 1.2 m by 0.75 m, 0.9 m high):

1. `ifc_contexts`: note the id of the `"Body"` context. `ifc_tree` gives the storey's id.
2. `ifc_edit` `root.create_entity` with `{"ifc_class": "IfcTank", "name": "Fish tank"}`. Pick the class
   that says what it is (IfcTank, IfcFurniture, IfcSanitaryTerminal, IfcElectricFlowStorageDevice …);
   `ifc_schema` tells you about one.
3. Its outline: `profile.add_parameterized_profile` with `{"ifc_class": "IfcRectangleProfileDef"}`, then
   `attribute.edit_attributes` with `{"product": <profile>, "attributes": {"ProfileType": "AREA",
   "XDim": 1.2, "YDim": 0.75}}`.
4. Its shape: `geometry.add_profile_representation` with `{"context": <Body>, "profile": <profile>,
   "depth": 0.9}`, then `geometry.assign_representation` with `{"product": <tank>, "representation":
   <shape>}`.
5. Its place: `geometry.edit_object_placement` with `{"product": <tank>, "matrix": [[1, 0, 0, x],
   [0, 1, 0, y], [0, 0, 1, z], [0, 0, 0, 1]]}`. The outline is CENTRED on (x, y), and z is the
   bottom of the shape. `[[0, -1, 0, x], [1, 0, 0, y], [0, 0, 1, z], [0, 0, 0, 1]]` turns it a
   quarter turn.
6. Its storey: `spatial.assign_container` with `{"products": [<tank>], "relating_structure": <storey>}`.
7. `ifc_clash` with `{"element_id": <tank>}`. A "pierce" means it sits inside something, often the
   floor because z is too low: move it and check again.

**Cut an opening and fill it** (a window 0.9 m wide and 1.2 m high in a wall):

1. `root.create_entity` with `"IfcOpeningElement"`. Give it a shape with
   `geometry.add_wall_representation` (`"length": 0.9`, `"height": 1.2`, and a `"thickness"` deeper than
   the wall), then assign it and place it so it passes right through the wall.
2. `feature.add_feature` with `{"feature": <opening>, "element": <wall>}` cuts it.
3. The window: `root.create_entity` with `"IfcWindow"`, a thin shape the size of the opening, placed in
   it, then `feature.add_filling` with `{"opening": <opening>, "element": <window>}`.

`ifc_volume`, `ifc_footprint`, `ifc_distance` and the drawings cut the opening from the wall (the door or window then
shows in an elevation and a section); `ifc_extent`'s box does not change, as an opening cannot widen it.
`edit_object_placement`'s matrix is in the WORLD frame, as `ifc_extent` reports it.

Find where the wall is before you cut it. Its placement is in `ifc_info`, but some walls keep their
position inside their shape: a wall built as a block is one. If the opening misses the wall, nothing
is cut, and `ifc_relations` on the wall still lists the opening. So say where you put it, and check
the result with the person.

**One kind of thing, placed many times** (two battery cabinets): `root.create_entity` with a TYPE class
(`"IfcElectricFlowStorageDeviceType"`, `"predefined_type": "BATTERY"`). Give the type a shape as in
steps 3–4, then make each cabinet and place it. Then `type.assign_type` with `{"related_objects":
[<one>, <two>], "relating_type": <type>}`: each cabinet takes the type's shape.

**Anything else, with the shape builder:** `ifc_shape_list` names its methods (a polyline outline, a
circle, a swept pipe, a sphere, a mesh …) and `ifc_shape_docs` gives one in full. For example:

1. `ifc_shape` `rectangle` with `{"size": [2.03, 1.37]}`.
2. `ifc_shape` `extrude` with `{"profile_or_curve": <curve>, "magnitude": 0.45}`.
3. `ifc_shape` `get_representation` with `{"context": <Body>, "items": [<solid>]}`.
4. `geometry.assign_representation` on the element.

Its values go as themselves: lists stay lists. A shape-builder rectangle STARTS at its position (a
corner), unlike a profile, which is centred.

Then `ifc_validate`, and keep the model with `save_model_as`. Tell the person what you placed and where,
in their words: "the tank sits against the rear corner post, 1.2 by 0.75 metres."

What is not offered yet:

- **Changing a wall's length or thickness by a number on a meshed model.** A Revit model exported for reference
  (IFC4 Reference View) keeps every shape as a triangle mesh. A whole element can be moved, copied, removed,
  renamed or given properties; resizing one means giving it a new shape.
- **Names in `ifc_clash`.** In this build, each clash names the element you checked rather than what it
  hit. Use its two points to find the other one.
