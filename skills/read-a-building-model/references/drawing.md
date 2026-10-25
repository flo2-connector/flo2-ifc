# Drawing to scale, and 3D pictures

Part of the skill read-a-building-model; SKILL.md holds the rules this page follows.

`draw_model` (in flo2; `ifc_draw` on the server itself) draws a PLAN, a SECTION or an ELEVATION to a scale written on
the sheet, with the model's unit and scale bars in metres and feet. It is kept in the design as two NEW files: an SVG
that prints at its scale, and a PNG picture. Give the person the PNG's link to see it and the SVG's to print it; most
chat apps do not show them the picture you are shown.

- **A storey's plan:** `{"view": "plan", "storey": "Level 1"}`. Find the storey's exact name with `ifc_tree` (the
  lowest storey is first). The plan cuts at 1.2 m above the storey's level; `cut_height` (in the model's unit: 1500
  on a millimetre model) moves it. Rooms are outlined and named with their areas. A room that holds other rooms (a
  floor's room with zones traced inside it) is named in its part clear of them; where its name does not fit there,
  it is left unnamed, and the answer lists it in `unlabelled_rooms` and `notes`: tell the person which room is not
  named on the sheet and why.
- **A section through named elements:** find them first (`ifc_select` with `IfcSpace, location = "Level 1"` for the
  rooms, and `ifc_extent` for where they are: east is +x, north is +y). Then
  `{"view": "section", "through": "<id>, <id>", "direction": "north-south"}` cuts through the middle of their box,
  running north-south, looking west (`"looking": "east"` turns it round). Two rooms side by side east-west want
  `"direction": "east-west"`. A section along any line is `"line": "x1, y1, x2, y2"` in the model's unit; along a
  grid line, `"gridline": "A"`.
- **An elevation:** `{"view": "elevation", "side": "south"}` is the building seen from the south.
- **What changed:** `"highlight": "<ids or a selector>"` draws those in orange: the rooms a change removes, the walls
  it adds. Draw the same view before and after an edit to show it.
- **Scale and sheet:** by default the largest scale that fits an 11 x 17" sheet; `"scale": "1/8\" = 1'-0\""` or
  `"1:100"`, and `"paper": "ARCH D"` choose. Too large for the sheet at that scale is refused with the scale that
  fits; `"around": "<ids>"` draws only the part around those elements, and `"storey"` limits a section to some
  storeys.
- **A large model:** the first drawing of a whole building makes every element's shape first (about 20 to 30 s on
  a large model) and may answer that it is not finished. Call again with the same arguments: the shapes are
  kept for the session, and the next drawing takes about 20 s. A plan of one storey takes about 10 s.

Say what the drawing shows in the person's words ("the ground floor, with the two rooms the change removes in
orange, at 1/8 inch to the foot"), and that it was drawn from the model, not by the architect.

## A 3D picture

`render_model` (in flo2; `ifc_render` on the server itself) draws the model in 3D, shaded, as a 1280 x 960 PNG, and
keeps it in the design as a NEW file. It is a picture to look at, NOT to scale: for sizes, draw to scale
(above) or measure.

- **The view:** `{"view": "iso"}` (the default, from above a corner), `"top"`, or one side: `"north"`, `"south"`,
  `"east"`, `"west"` (the side seen; north is the model's +y).
- **Part of the model:** `"selector"` draws only what it names, as `ifc_select` takes it: `"IfcWall, IfcSlab, IfcRoof"`
  for the shell, `'IfcElement, location = "Level 1"'` for one storey's built parts. Rooms (IfcSpace) are drawn too
  when nothing leaves them out, and a room's box can hide the walls around it: leave rooms out to show the
  construction.
- **What changed:** `"highlight": "<ids>"` (element ids, as `ifc_select` gives them) draws those in their colours and
  everything else faint grey.
- **Time:** a small model renders in a few seconds; a model of a few megabytes takes 20 to 30 s. On a large model,
  render one storey or a selector first.

Look at the picture you are shown before you describe it, and send the person its link: most chat apps do not show
them the picture you are shown. Say it is a picture drawn from the model, not by the architect, and what it leaves
out.
