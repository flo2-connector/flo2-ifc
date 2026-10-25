# Measuring from the shapes, and the number a decision rests on

Part of the skill read-a-building-model; SKILL.md holds the rules this page follows.

The stored quantities are the exporter's. These read the elements' 3D shapes instead, and answer in the model's unit,
in metres (square, cubic) and in feet and inches (square, cubic feet). Name the elements with a `selector` (by storey:
`location = "Level 1"`), with `ids` (ids or GlobalIds, comma separated), or with one `element_id`.

- **Footprint:** `ifc_footprint`. Each element's footprint is its shape seen from above. The set's `footprint`
  counts what is stacked ONCE (a slab over its footings, a wall over a wall); `sum_of_elements` adds each one's own,
  and `stacked_overlap` is the difference. Quote `footprint` for "how much ground does it cover", and say when the
  overlap is large: two rooms that overlap in plan are worth telling the person about.
- **Volume:** `ifc_volume`. Only a CLOSED shape holds a volume. An OPEN one (a roof modelled as a surface, a wall
  with a missing face) is named in `not_closed` and left out of `total`. Never quote a volume for it, and never its
  stored quantity either without checking it against `ifc_extent`'s box (no solid is larger than its box). Say how
  many were left out.
- **Size and height above a datum:** `ifc_extent` with `"datum": "Level 1"` (a storey's level) or a height in the
  model's unit. `above_datum.top` is how high it reaches above that floor.
- **Distance:** `ifc_distance` with `a` and `b` (each an id or GlobalId). `clear_distance` is the shortest gap
  between their surfaces (0 when they touch), with the two closest points; `box_gap` and `centre_to_centre` are
  beside it. Say which one you quote: "the clear gap between the two walls is 1.8 m (5' 10 7/8")".

**Every measure is citable.** Its `cite` names the model file and its sha256, the elements' GlobalIds, the unit and
the method, and in flo2 its `source.cite_as` is the input `source` the calc helper takes (below). If `source`
carries a `warning`, the session's copy was edited: the number is of the edited copy, not of the file.

## When a decision rests on the number

When a decision rests on the number (the floor area against its cap, a wall's length against a clearance), work the
comparison out with the calc helper (`"helper": "calc"`, the skill support-a-decision-with-math) rather than in your
head, and keep it in the decision's calc record. A measure's answer gives the `source` to use (`source.cite_as`). The
model's value goes in as an input whose:

- `source` is the model file and the version read, exactly as read_a_design_file names it (`cite_as`:
  `design file "<file>" sha256:<its sha256>`), the one form every source from a design file takes;
- `note` names the building-model helper, the element by its name and id, the quantity read, and whether it was
  stored or computed.

Then keep the record and link it to the decision, as that skill says. For a usable floor whose slab stores 26.84 m², the input is:

```json
{"id": "floor", "value": "26.84 m^2",
 "source": "design file \"pod.ifc\" sha256:<the sha256 cite_as gives>",
 "note": "building-model helper: slab 'Usable floor' (#131), Qto_SlabBaseQuantities.GrossArea, stored"}
```

Nothing re-runs the model read when the file gets a new version, and `rerun_record` only re-checks the old number. So
after a new version, read the model again and re-run the record with what it now says and the new `cite_as`
(`record_computation`, the same name, so the design keeps it as the record's next version).
