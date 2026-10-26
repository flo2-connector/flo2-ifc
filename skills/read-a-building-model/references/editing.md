# Changing the model, and building its ordinary parts

Part of the skill read-a-building-model; SKILL.md holds the rules this page follows.

Edit only what the person asked for, and say what you will change before you change it.

1. **Find the edit.** `ifc_list` names the editing functions by module (`attribute`, `pset`, `root`,
   `spatial` …); `ifc_docs` on one, such as `"attribute.edit_attributes"`, gives its parameters.
2. **Make it.** `ifc_edit` with that `function_path` and its `params` by name, an element as its id:
   `{"product": 190, "attributes": {"Name": "Zone 01 — bathroom"}}`. Read the element back with
   `ifc_info` to check it took.
3. **Check it.** `ifc_validate`. An error means the model is no longer valid IFC: put it right before
   going on.
   A measured value (a U-factor through `pset.edit_pset`) is written only in a unit the model declares. If it is
   refused for that, ask the person which unit the number is in, declare it with `ifc_add_unit` (a U-factor in
   W/(m2.K) or Btu/(hr.ft2.°F): its `parts`), then write the number in that unit, unconverted.
4. **Keep it as a new file.** Saving writes a NEW file beside the original, which stays as it was.
   Tell the person the new file's name.

When a decision rests on a number in the edited model, it goes to the calc helper as `references/measuring.md` says, its
`source` the saved file's `cite_as`.

## Building the ordinary parts: one step each

A wall, slab, roof or room is ONE call: made, shaped, placed, on its storey and linked to the design part it
realizes (`realizes`: its id, `"cmp:north-wall"`). Lengths are in the model's OWN unit, heights above
the storey's level, points `[x, y]` in plan, in the step's `"frame"`:

- `"frame": "world"` (the default): the model's world plan, the numbers `ifc_extent` and `ifc_info` report.
- `"frame": "storey"`: the storey's own axes. Its placement turns and shifts the points into the world, so `[0, 0]`
  is the storey's origin and x runs along its own x axis. Use it when the building is turned on its site (`ifc_info`
  on the storey: a placement that is not square) and the points come from the plan's own dimensions.

The reply's `frame` says which was used, and the storey's origin and turn. Say it to the person when they differ.

- `ifc_add_wall`: `"start": [0, 0], "end": [30, 0], "height": 10, "thickness": 0.5`, centred on that line; a sloped
  top takes `"height_at_end"`, a gable end `"peak_height"`.
- `ifc_add_slab` and `ifc_add_space` (a room): a `"footprint"`, `[[0, 0], [30, 0], [30, 20], [0, 20]]`, and a
  `"thickness"` or `"height"`.
- `ifc_add_roof`: a `"footprint"`, `"kind"` `"shed"` (rising from its FIRST side) or `"gable"`, `"eave_height"`,
  `"slope"` (rise over run), `"thickness"`, `"overhang"`.

Each takes `"storey"` and `"name"`. Check with `ifc_extent` and `ifc_validate`. A refused step changes nothing.
