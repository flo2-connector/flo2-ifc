---
name: read-a-building-model
description: Check a design against its building model (an IFC file) with someone who is not a modelling specialist, measure it, draw it, and change it when they ask. Use when a person asks what their model says (its storeys, rooms, a floor area, a wall's length, its materials), wants a footprint, a volume, a height or a distance measured, wants a plan, section or elevation drawn to scale or a 3D picture of it, wants a limit the design records checked against the model, wants a list of what the model must contain written down in a checkable form (an IDS) or the model checked against one, wants the model checked against buildingSMART's validation rules before it is handed over, or asks to build or change something in it (a wall, slab, roof or room; a room's name, a property, equipment placed, an opening cut), or has no model yet. It covers starting one, where to start, which numbers the model stores and in what units, measuring from the shapes and citing the number, drawing to scale and in 3D, following a measurement the design already records, how to report a comparison, writing a must-contain list as an IDS, checking a model against it and against buildingSMART's rules and reading the results, how an edit is made and kept as a new file, and recipes for shapes, positions and openings.
compatibility: Needs IfcOpenShell's ifcmcp with flo2-ifc 0.9.0's layer, as github.com/flo2-connector/flo2-ifc builds it (in flo2, the helper "building-model"), which provides ifc_summary, ifc_units, ifc_tree, ifc_select, ifc_info, ifc_relations, ifc_extent, ifc_quantify, ifc_materials, ifc_validate, ifc_schema, the measures ifc_footprint, ifc_volume and ifc_distance, the drawing ifc_draw (in flo2, the step draw_model), the 3D picture ifc_render (in flo2, the step render_model), the checks ifc_write_ids and ifc_check_ids (in flo2, the steps write_ids and check_ids) and ifc_check_rules, for editing ifc_list, ifc_docs and ifc_edit, for shapes ifc_contexts, ifc_shape_list, ifc_shape_docs, ifc_shape and ifc_clash, the building steps ifc_add_wall, ifc_add_slab, ifc_add_roof and ifc_add_space, and ifc_add_unit.
---

# Read a building model with someone

Work in the person's terms: the storeys, the rooms, the floor, the walls, the roof, square metres. Say "the model",
not "the IFC file", and name a thing by what it is ("the usable floor slab"), with its id only when it helps them
find it again. The tools read the model; your job is to say what it means for their design.

This page holds the rules. The recipes are in the pages beside it: read the one a task needs with get_skill
(`name` "read-a-building-model", `file` the page), before you start that task.

| When | Read |
|---|---|
| The design keeps no model, and you start one | `references/starting-a-model.md` |
| The model is large (thousands of elements, an answer that pages) | `references/large-models.md` |
| A footprint, volume, height or distance, or a number a decision rests on | `references/measuring.md` |
| A plan, section or elevation to scale, or a 3D picture | `references/drawing.md` |
| A list of what the model must contain (an IDS), or a check against one | `references/ids.md` |
| buildingSMART's validation rules, before a hand-over | `references/validation-rules.md` |
| A change they ask for, or a wall, slab, roof or room built | `references/editing.md` |
| Equipment placed, an opening cut and filled, a type placed many times, any other shape | `references/shapes.md` |

## When the design keeps no model

Start one: follow `references/starting-a-model.md`, copy its text changing only what it names, keep it with
`save_a_design_file` (a name ending `.ifc`), then open and check it here. Never improvise IFC text.

## Before you open it

- If they want the design checked against the model, look in the design first for a measurement it already
  records: search the design for the quantity (for example "floor area" or "ceiling height"). A recorded check often
  names the element to read and the traps its author found. Follow it rather than improvising.
- Note the limit the design records and its unit, so the comparison is like for like.

## The loop

1. **Orient.** `ifc_summary` gives the schema, the project, how many of each kind of thing the model holds, and its
   units. `ifc_tree` gives the site, the building, its storeys (each with its elevation) and the rooms in each, with
   their ids, and how many elements of each kind each place holds.
2. **Find.** `ifc_select` takes a selector such as `"IfcSlab"`, `"IfcSpace"` or `"IfcWall, IfcDoor"`. Each result
   has an id, a type and a name. **By storey:** `IfcDoor, location = "Level 1"` is the doors on the storey named
   Level 1 (use the name `ifc_tree` gives).
3. **Read.** `ifc_info` on one id gives its attributes, its property sets and its quantity sets (for example
   `Qto_SlabBaseQuantities.GrossArea`), where it is placed, and its `storey`. A room is PART of its storey rather
   than contained in it, and `storey.via` says which.
4. **Measure.** `ifc_extent` gives how far elements reach, from their 3D shapes: the box around them, its `bottom`
   and `top`, and which one reaches `highest`. "How high is the roof?" is `ifc_extent` with `"selector": "IfcRoof"`,
   then `overall.top`. It answers in the model's unit, with metres and feet and inches beside. A box is aligned with
   the model's axes, so say "reaches", not "is", for a sloped or turned element.
5. **Relate.** `ifc_relations` says what contains an element, what it is part of and what it contains;
   `"traverse": "up"` walks up to the project.
6. **The rest.** `ifc_materials` lists the materials and what uses them. `ifc_schema` says what an IFC type is.
   `ifc_validate` checks the model against the schema, and `"express_rules": true` also checks its rules (slower).

On a large model, read `references/large-models.md` first: nothing is read whole.

## The numbers

- A stored quantity is what the model's author exported. It is in the model's own units, which `ifc_units` (and
  `units` in `ifc_info`) names. Do not assume metres: a Revit export is often in MILLIMETRES for lengths, with areas
  in square metres and volumes in cubic metres, so a storey elevation of 2844.8 is 2.84 m. Say the unit every time.
- A property's value with a measure (a U-factor, a temperature, a price) is in the unit `property_units` in `ifc_info`
  gives it. `"not declared"` means the model gives it NO unit: say so, never assume SI, and never compare it with a
  limit as if it had one. To write one, `references/editing.md`.
- A stored quantity can be wrong. Check a surprising one against the shape: a volume larger than the element's
  `ifc_extent` box cannot be right. Report what is stored and what the shape says; never change a stored value
  unasked.
- Say which quantity you read. A gross area includes what a net area leaves out (openings, for example), and the two
  can differ by more than a margin the design cares about.
- If a quantity is missing, `ifc_quantify` recomputes quantities from the geometry with a named rule (for example
  `"IFC4QtoBaseQuantities"`) for the elements a selector matches; read the results with `ifc_info`. Say the number
  was computed, not stored. It changes only the copy being read, never the file.
- Names are the author's. When two elements match a name, read both and say so rather than picking one.

## Measures, drawings and checks: the rules

- **A measure** reads the shapes, not the stored quantities. Never quote a volume for an OPEN shape (`not_closed`),
  nor its stored volume unchecked against `ifc_extent`'s box. Say which measure you quote, in the model's unit and
  in metres or feet. Every measure carries `cite`; when `source` carries a `warning`, the number is of the session's
  edited copy, not of the file.
- **A drawing or a picture** is kept in the design. Send the person its link: most chat apps do not show them the
  picture you are shown, so never say it is above or below. A 3D picture is NOT to scale: draw to scale for sizes.
- **An IDS** is written with the helper's step, never as XML by hand. It checks what the model STORES, not its
  shapes: say which items went into it and which stay measures.
- **buildingSMART's rules** check the FILE: save an edited copy first and check the saved file. A rule named in
  `rules_not_finished` was not checked: say so.

## Comparing with the design

Say three things, plainly:

- what the design says: the limit, its unit and where it is recorded;
- what the model says: the value, which element it came from (its name and id), and whether it was stored or
  computed;
- the verdict, with the margin. For example: "The design caps the usable floor at 30 m². The model's slab 'Usable
  floor' (#131) stores a gross area of 26.84 m², which is 3.16 m² under."

If they disagree, change neither. Offer to record the finding in the design, and ask what they want to do.

## When a decision rests on the number

Work the comparison out with the calc helper (`"helper": "calc"`, the skill support-a-decision-with-math), never in
your head, and keep it in the decision's calc record. The model's value goes in as an input whose `source` is the
measure's `source.cite_as` (`design file "<file>" sha256:<its sha256>`). `references/measuring.md` gives the input
in full, and what to do when the model gets a new version.

## Changing the model, when they ask

Edit only what the person asked for, and say what you will change before you change it. Edits change only the
session's copy until it is kept as a NEW file; the file opened is never changed. A wall, slab, roof or room is one
building step each (`references/editing.md`); anything else is `ifc_edit` or the shape builder
(`references/shapes.md`). The geometry functions take METRES whatever the model's unit; the building steps and the
shape builder take the model's OWN unit. Check with `ifc_validate` before you keep it.

## In flo2

- The model is opened for you from the design's own file. Name it with `file` when the design keeps more than one;
  never send a path. A drawing is made and kept with `draw_model`, and a 3D picture with `render_model`, the helper's own steps:
  neither writes over a file the design keeps (a name it already keeps gets -2), and a viewer of the design cannot
  draw. An IDS is written with `write_ids` and kept as a new `.ids` file (a design need not
  keep a model for it), and `check_ids` reads an IDS the design keeps by its file name and keeps the two reports as
  new files.
- Edits change only your session's copy. Keep them with `save_model_as` and a NEW file name: it checks the model
  with `ifc_validate` first and refuses an invalid one, and it never writes over a file the person uploaded. A file a
  chat made takes a new version with `base_sha256` and `why`. On a large model the check takes more than one call:
  `save_model_as` then says how far it got, and you call it again with the same arguments until it saves (a check
  you already ran with `ifc_validate` counts).
- Every answer's `note` says the units the model declares. Quote numbers in them.
- A session is kept for ten minutes unused. Its unsaved edits are lost when it ends, or when another file of the
  design is opened, and the file opened is never changed.
- If the design keeps no model yet, start one (above), or the person adds theirs on flo2.io/account, in the design's Files.
