# flo2-ifc: the ifcmcp that flo2.io runs

This is a small repository. It holds the exact set of packages that flo2.io installs to run **ifcmcp**, IfcOpenShell's
MCP server for IFC building models. That server has 25 tools: load, query, edit, validate, quantify and plot a
building model. It is published on PyPI as `ifcopenshell-mcp` and licensed LGPL-3.0-or-later.

The repository holds seven things:

- the pinned set, in `pyproject.toml`;
- the readers for a whole building, the measures and drawings, the checks (IDS and buildingSMART's rules), and the
  record of what changed between two models (IfcDiff and BCF 3.0), in `src/flo2_ifc/` (below);
- the skill a chat reads to work with a building model, `skills/read-a-building-model/` (since 0.7.0; until then
  flo2 wrote it): its rules in `SKILL.md`, and its recipes in pages beside it, `references/`, which SKILL.md names
  and flo2 serves unchanged. One of them, `references/starting-a-model.md` (since 0.5.1; below, "Starting a model
  from nothing"), is the starting model a chat copies to start a building model from nothing;
- the container recipe flo2.io builds, in `Dockerfile`;
- the plugin, in two formats;
- a small CI;
- a monthly dependency check.

Until 0.2.0 it held no server code, and `flo2-ifc` handed straight to ifcmcp's own command line. Since 0.2.0 it holds
one thin layer, **the readers for a whole building**, because a real building did not fit through ifcmcp's tools, and
since 0.3.0 **measures and drawings to scale** on the same layer. Since 0.4.0 **the server is flo2-ifc's own**, on the
MCP Python SDK 2.x, serving ifcmcp's tools from upstream's published `ifcopenshell-mcp` rather than from a fork
(below, "Where ifcmcp comes from"). Since 0.5.0 it **checks a model against an IDS and against buildingSMART's
validation rules, and writes an IDS** (below, "Checks"). Since 0.10.0 it **records what changed between two models**,
with IfcOpenShell's IfcDiff and a BCF 3.0 file (below, "What changed between two models"). Since 0.13.0 it **draws a
plan set and binds it into one PDF** (below, "A plan set as one PDF").

## The readers for a whole building (since 0.2.0)

A large building model read through flo2 (2026-10-23) showed five problems. A chat holds about 29,000 characters in one reply, and stops waiting at about 60 s:

| ifcmcp's tool | On a large model | flo2-ifc's reader |
|---|---|---|
| `ifc_tree` | over 200,000 characters | places in full, each with its elements counted by class; elements a page at a time, filtered by `storey` and `element_type`; since 0.8.1, also the elements referenced into a storey (a wall rising from the storey below), marked and counted apart |
| `ifc_relations` on a storey | over 150,000 characters | each long list paged (`offset`, `limit`) and filtered by `element_type`; `lists` says what was withheld |
| `ifc_select` "IfcWall" | over 100,000 characters | sorted, a page at a time, with `counts`; the tool text says `location = "<storey>"` selects by storey |
| `ifc_info` on a room | no storey (a room is part of its storey, not contained in it) | adds `storey`, found through containment or aggregation, with the path; since 0.8.1, `referenced_into`, the other storeys an element spanning storeys is referenced into |
| `ifc_validate` | over 100 s, one call | checks as much as one call allows (30 s), says `complete` and `next_start`; the schema's rules run in a forked child that is stopped at the call's time; since 0.12.0 each issue names its element and attribute, and `scope: "edits"` checks only what the session's edits touched (below, "Checking what the edits touched") |
| (none) | no way to say how high the roof is | `ifc_extent`: bounding boxes from the geometry, in the model's unit, metres, and feet and inches |
| (none) | replies said lengths were "usually metres"; until 0.9.0 a U-factor came back as a bare number | `ifc_units` (since 0.9.0 EVERY unit the model declares: SI, conversion-based, derived and its currency), `units` in every reply that carries a length, area or volume (since 0.9.0 also the other units declared, and that a measure with none has no unit), and in `ifc_info`, since 0.9.0, each measured property's measure type and unit (`property_units`) |

`src/flo2_ifc/server.py` reads every tool ifcmcp's own server defines, takes its one session, and serves them on its
own server, with seven of them replaced by the readers in `src/flo2_ifc/reading.py`. Each replacement keeps its name
and only ADDS optional arguments.

These readers are flo2-ifc's, not ifcmcp's. The ones ifcmcp itself would want (paging, the storey of a room, units, a
timed validation, extent) can be offered to IfcOpenShell as their own changes; that is a separate decision.

The limits are set by the environment: `FLO2_IFC_PAGE_CHARS` (20,000 characters of JSON in one listing),
`FLO2_IFC_SECONDS` (30 s of measuring or checking in one call), `FLO2_IFC_GEOMETRY_LIBRARY`
(`hybrid-cgal-simple-opencascade`: a large model's doors took 86 s with IfcOpenShell's default library and 1.8 s
with this one) and `FLO2_IFC_GEOMETRY_BATCH` (250 elements per geometry iterator: one iterator over a whole large
model peaked at 1.18 GB, over flo2's 1 GiB cap; batches of 250 peak at 390 MB).

## Measures and drawings (since 0.3.0)

A chat measures a building model and draws it to scale, so a person can explore a change to a building and end with
drawings of it to scale. Each tool reads the elements' triangle meshes, as IfcOpenShell's geometry iterator makes them (world coordinates), in
`src/flo2_ifc/geometry.py` and `src/flo2_ifc/drawing.py`:

| Tool | What it gives |
|---|---|
| `ifc_footprint` | each element's footprint (its shape projected onto plan), and the SET's, in which what is stacked counts once (`stacked_overlap` says how much) |
| `ifc_volume` | the volume of each CLOSED mesh, and their total; an OPEN mesh is named in `not_closed` and refused, because what it encloses depends on where it is measured from (a roof's stored volume can be exactly that fault) |
| `ifc_extent` `datum` | heights above a storey's level or a given height (`above_datum`) |
| `ifc_distance` | the shortest gap between two elements' surfaces with the two closest points (exact over every corner, edge and triangle), the box gaps and the centres |
| `ifc_draw` | a plan of a storey at a cut height, a section along a line, a grid line or through named elements, or an elevation of one side, as an SVG in millimetres of paper and a PNG, to a stated scale written on the sheet with the model's unit and a scale bar in metres and feet; `highlight` draws elements in orange |
| `ifc_plan_set` (since 0.13.0) | a plan set: numbered sheets, each `ifc_draw`'s drawing with its number and title, after a cover sheet that lists them, bound into ONE PDF (below, "A plan set as one PDF") |

Every measure answers in the model's unit, in metres (square, cubic) and in feet and inches (square, cubic feet), and
carries `cite`: the model file's name and sha256, the elements' GlobalIds, the unit and the method, which flo2-calc
keeps with the number. When the session's copy was edited after it was opened, `cite.edited` says the number is of
the edited copy, not of the file the sha256 names.

**Why flo2-ifc draws for itself.** ifcmcp's `ifc_plot` goes through `ifcopenshell.draw`, which in 0.9.0 raises
`'SwigPyObject' object has no attribute 'is_a'` at draw.py:376 in its cells pass (`ray_intersection_result.instance`
is an `express::entity`, which IfcOpenShell's SWIG wrapper does not wrap), and with cells off it draws no scale and
nothing on a storey with no elevation. flo2-ifc installs ifcopenshell 0.9.0 from PyPI, so a fix in the fork would not
reach it; the fix is offered upstream on its own. `ifc_plot` and `ifc_render` stay in the server unchanged.

**A 3D picture (since 0.7.0).** `ifc_render`, ifcmcp's own tool (ifcquery's render module), draws the model's shapes
with pyvista on VTK in an off-screen window and returns a 1280 x 960 PNG, and writes it where `output_path` says. Until
0.6.0 the image left pyvista out, so the tool was listed and failed. flo2's door now offers it through its own step,
which has it write into the session's outbox and keeps the picture in the design. The image draws with no display:
VTK's Linux wheel looks for its OpenGL at run time, and the image installs Mesa's EGL and llvmpipe (`libegl1`,
`libegl-mesa0`, `libgl1-mesa-dri`, `libgl1`) and names VTK's EGL window (`VTK_DEFAULT_OPENGL_WINDOW`), so it draws on
the CPU on Mesa's surfaceless platform. One thing in flo2-ifc's own server had to change for it: mcp 2.x's
`ImageContent` is left set in ifcmcp's server module after its tools are read, because the image tools look it up when
called (until 0.7.0 both failed with "'NoneType' object is not callable" after drawing).

**How it draws.** What a plane cuts is joined into rings and filled dark; what lies beyond the cut (below it, in a
plan) is each element's triangles projected, snapped to a tenth of a millimetre of paper and joined, drawn white with
a thin line, farthest first so nearer ones cover them. Snapping loses nothing the sheet can show and joins a detailed
fixture's thousands of triangles into few. Rooms are outlined and named, with their area in a plan; a section or
elevation marks each storey's level. The scale, when none is given, is the largest of the architect's and the metric
scales that fits the sheet.

**A room that holds other rooms** (since 0.3.1), such as a whole floor's room with zones traced inside it, is named in
its part clear of them, where its name fits whole, and otherwise not named on the sheet: the reply lists it in
`unlabelled_rooms` and says so in `notes`, so the chat can tell the person. One room holds another when at least 90%
of the other's drawn area lies inside it and the other is the smaller (`HOLDS_SHARE` in `drawing.py`): a zone traced a
hair past its room's edge still counts, and rooms that merely overlap do not. The names of rooms that hold none stay
where they always were. A section names the rooms it cuts the same way.

**A room's area on a plan is the one the model stores** (since 0.5.0), when it stores one: the authoring tool's own
number, `Qto_SpaceBaseQuantities.NetFloorArea` first, then an `Area` in another set (a Revit export keeps it in
"Dimensions", which is looked at first). It is read in the model's area unit. Only a room that stores no area is
labelled with the area measured from its outline. The reply's `room_areas` says, per room, which was used (`source`:
`stored` or `measured`), and where a stored area and the measured outline differ by more than 5% of the stored one
(`AREA_DIFFERS`) it names the room and both numbers in `notes`: an authoring tool measures a room to its own rules
(to a wall's finish face or its centre, with or without a column), and the difference is a fact about the model.

**A storey holds what is referenced into it (since 0.8.1).** IFC places an element that spans storeys, such as a wall
rising through two, in one storey (contained) and references it into the others (`IfcRelReferencedInSpatialStructure`),
so that it is part of each. A plan or a section limited to a storey draws both what the storey contains and what is
referenced into it, with the parts of each that belong to no other storey; so a plan of an upper storey cuts the wall
rising through it and leaves out that wall's door below. Until 0.8.1 the drawings, and `ifc_tree`, walked only what
a storey contains, and a plan of a loft drew its windows floating where the wall should be (flo2-ifc's design
1510fc3728665dbf, `fact:a-storeys-readers-walk-only-what-it-contains-not-what-is-referenced-into-it-2026-10-26`).
A selector's `location = "<storey>"` is IfcOpenShell's and still picks only what the storey contains.

**Openings are cut (since 0.8.0).** The meshes the measures and drawings read are made with IfcOpenShell's opening
subtraction ON (`geometry.mesh_settings`), so a wall is measured and drawn as built: a 5 x 3 x 0.2 m wall with a
0.9 x 2.1 m opening holds 2.622 m3, a section through its door cuts the wall only above the opening, and an elevation
shows the door. Until 0.8.0 they shared `ifc_extent`'s setting, which skips the subtraction because an opening cannot
widen a box, on the premise that a Revit export's meshes come already cut; that premise was false for an opening made
in the model (the skill's recipe, `feature.add_feature`) and, measured on 2026-10-25, for both public samples too:
their walls' volume fell from 64.78 to 54.50 m3 (AC20-FZK-Haus, 13 walls, 17 openings) and from 240.61 to 207.92 m3
(the Duplex, 57 walls, 50 openings) once their openings were cut (flo2-ifc's design 1510fc3728665dbf,
`fact:measures-and-drawings-skip-opening-subtraction-so-a-cut-wall-reads-whole-2026-10-25`,
`dec:building-steps-take-a-frame-and-measures-cut-openings`). `ifc_extent`'s boxes keep the fast setting. A mesh is
kept under the setting it was made with. What it costs, cold (no kept meshes), one CPU (an i5-10300H core), in
seconds before and after:

| Model | `ifc_volume` of every wall | the ground storey's plan | a section through a door |
|---|---|---|---|
| AC20-FZK-Haus (2.6 MB) | 0.13 to 1.92 | 0.49 to 0.77 | 0.57 to 2.35 |
| Duplex (2.4 MB) | 0.15 to 0.44 | 0.31 to 0.31 | 0.90 to 1.43 |

Cutting is slower (15 times for FZK-Haus's walls' volume, 4 times for its section), and still a few seconds a call; once made, a mesh is kept, so the next measure or drawing of the same elements pays
nothing. The geometry library stays `hybrid-cgal-simple-opencascade`: with openings cut, `opencascade`, `cgal` and
`hybrid-opencascade-cgal` took as long for the volume and 1.6 to 1.8 times as long for the drawings.

**The meshes are kept** for the session (`FLO2_IFC_MESH_CACHE_MB`, 256 MB; a large building model's all fit
in it), so a second drawing or measure of the same elements does not make them again, and they are forgotten whenever
the session's model changes (an edit, a shape, a reload: `server._watch_changes`). A drawing that runs out of
`FLO2_IFC_SECONDS` stops with `partial` and keeps what it made: the same call again goes on.

Measured on a large building model in the image under flo2's flags (1 CPU, 1 GiB, 2026-10-24): one storey's plan in
8.0 s; a section, which makes the shapes of the whole building first, in two calls
(30.0 s, partial, then 21.9 s), and 21.5 s once its shapes are kept; every measure 0.1 s or less once the shapes are
made; memory at most about 481 MiB (sampled).

## A plan set as one PDF (since 0.13.0)

A person who had edited a building model through chats asked to "pull a PDF planset" (flo2's design
1a97fc1f1de4d5d2, `req:a-chat-pulls-a-pdf-plan-set-of-a-designs-building-model`, 2026-11-09): numbered sheets of
plans, elevations and sections drawn to scale from the model as it stands, a cover sheet listing them, kept as one
PDF. `ifc_plan_set` (`src/flo2_ifc/plan_set.py`) draws one and writes `<output_path>.pdf` and each sheet's SVG,
`<output_path>-<number>.svg`.

**The sheets.** Each is the drawing `ifc_draw` makes (`drawing.sheet`, the same cut, scale bars and title block), with
its sheet number, large, and the set's name in a box at the right of its title block, and its title over what it
shows. With no `sheets` given the set is the conventional one:

| Number | Sheet |
|---|---|
| A-001 | the cover: the set's name (by default the project's, as the model states it), the project and buildings as the model names them, the model file and its sha256, the date drawn, and every sheet by number, title and scale, with any sheet not drawn and why |
| A-101 on | a plan of every storey, lowest first, at its 1.2 m cut; a storey that holds nothing with a shape gets none, and `notes` says so |
| A-201 to A-204 | the north, east, south and west elevations |
| A-301, A-302 | a north-south and an east-west section through the middle of the building (the middle of the box of every element of every storey, the line running a metre past it each way) |

The plans, the elevations and the sections are each at ONE scale, the largest that fits every sheet of the kind: the
smallest of the scales each would be drawn at alone. Or the caller gives the sheets, each `ifc_draw`'s own arguments
with a `number` (default: by kind, A-101.. plans, A-201.. elevations, A-301.. sections) and a `title` (default: what
it shows), drawn in the order given. `paper` is one for the whole set; `scale` is one for every sheet that names
none. A sheet that cannot be drawn (no such storey, a scale too large for the paper) is named in `not_drawn` with its
reason and listed on the cover, and the rest are bound; a sheet that gives `paper`, `output_path` or an argument
`ifc_draw` does not take, a number given twice or the cover's, is refused before anything is drawn.

**The PDF** is bound with cairo's own PDF surface (`cairocffi`, which cairosvg draws with; cairosvg alone writes a
one-page PDF), so no new package is installed (one already installed is now pinned: "What is pinned and why"). Each
sheet's SVG is drawn by cairosvg onto a cairo recording surface as soon as the sheet is drawn, and binding replays
them, a page each: vector, its words as text in DejaVu Sans, each page the size and way round of its sheet, and the
PDF's title the set's name. Drawing the SVGs is most of the cost (on the Clinic below, 9.0 s for 4.4 MB of SVG, and
1.9 s to replay them), so it is spread over the calls that draw the sheets.

**Time.** A set keeps to one call's time (`FLO2_IFC_SECONDS`), as `ifc_draw` does. What it has drawn is kept for the
session, each sheet in memory, by the model, its generation (any edit, shape or reload forgets it), the session's
edits and the set's own arguments, so the same call again goes on from the next sheet (`partial`, with `done` and
`to_do`); an edit starts the set again. A sheet is not started when less time is left than the slowest sheet of the
set took, and the last call keeps a second a MB of SVG for binding. Nothing is written until every sheet is drawn or
refused. Measured on public samples, the default set on Tabloid, on one core of an Intel N95 laptop (2026-11-09):

| Model | Sheets | Calls | Time | PDF |
|---|---|---|---|---|
| AC20-FZK-Haus (2.6 MB) | 9 | 1 | 8.8 s | 0.11 MB |
| Duplex (2.4 MB) | 11 | 1 | 8.1 s | 0.24 MB |
| Clinic_Architectural (13 MB) | 11 | 3 | 32.3, 28.5 and 10.9 s | 1.88 MB |
| Schependomlaan (49 MB) | 13 | 3 | 27.8, 25.6 and 16.9 s | 0.57 MB |

The first call of each makes every shape of the building; the rest are kept for the session as for `ifc_draw`. The
process peaked at 267 MB (the Clinic) and 421 MB (Schependomlaan). flo2.io runs a session about 2.2 times slower, so
a large model's set takes more calls there.

## Checks (since 0.5.0)

A design package is checked before it is handed over (flo2's
`req:a-chat-checks-a-building-model-against-an-ids-and-buildingsmarts-rules`). Three tools do it, with buildingSMART's
and IfcOpenShell's own code, never a checker of flo2-ifc's:

| Tool | What it does |
|---|---|
| `ifc_write_ids` | builds an **IDS** (buildingSMART's Information Delivery Specification, version 1.0: what a model must contain) from a structured description, with **ifctester**'s API (IfcOpenShell's IDS library). Facets: entity, attribute, property, classification, material and part_of, each value plain or a restriction (pattern, one_of, bounds, lengths), each requirement required, optional or prohibited. Units are IDS 1.0's: a property's or quantity's value is in SI units whatever the model's unit (ifctester converts), and an attribute's value is compared as stored, in the model's unit. It is written by ifctester's writer, checked against the IDS 1.0 XSD ifctester ships, and read back; an invalid one is refused with the schema's reason and nothing is written. A chat never writes IDS XML. |
| `ifc_check_ids` | runs an IDS file against the model open in the session with **ifctester**'s checker. Each specification says passed or failed, how many elements it applies to, and the failing elements by GlobalId with ifctester's reason, a page at a time; with `report_path`, ifctester's own HTML report (for a person) and JSON report (for a program) are written. The reply names the IDS and the model file, each with its sha256. |
| `ifc_check_rules` | runs **buildingSMART's validation rules** on the model file: the normative rules (implementer agreements and informal propositions), the critical header rule and the industry-practice rules, as the validation service runs them. The reply says which rule sets ran, which did not (the schema and EXPRESS checks are `ifc_validate`'s; the bSDD check needs the network), and names each issue's element by id and GlobalId. |

**Both checks keep to one call's time.** The IDS check runs in a forked child that is stopped at the limit, and starts
no new specification once the rest of the call may not hold it and its reports; the rules run in a `behave` child
that is stopped at the limit. A check that does not finish says `complete: false` and `next_start`, as
`ifc_validate` does. A rule that cannot finish within one call on a model is passed over, named in
`rules_not_finished`, and the check goes on.

**buildingSMART's rules** are published as code, not on PyPI: github.com/buildingSMART/ifc-gherkin-rules (MIT), with
the data model it imports, github.com/buildingSMART/ifc-validation-data-model (MIT). `tools/ifc_rules.py` pins one
release (v1.0.5) by tag, commit and each archive's sha256, and the image's build stage fetches it there and nowhere
else; the image holds it at `/opt/ifc-gherkin-rules` (`FLO2_IFC_RULES_DIR`) and needs no network. They run unchanged,
from the console as their own CI runs them: no database, no web service. `flo2_ifc.rules_formatter.Lines`, a behave
formatter, writes each rule's outcomes as the rule ends, so a run stopped at the time limit keeps every rule that
finished. Measured on 2026-10-24, under flo2's flags (1 CPU, 1 GiB):

- a small model (a 14 KB shell of a building, IFC4, 7 rooms): all 97 rules in one call, 10.7 s, the child at most 148 MB;
- a synthetic 85,000-entity model (5,000 walls): 11 calls, 325 s in all, the child at most 170 MB; two rules
  (IFC102, IFC106) take longer than one call on it (45 s and 29 s on a laptop) and were passed over.

**What runs of upstream's own tests.** Upstream's test suite (849 test models) was run with the pinned release: 804
pass and 45 fail, the same 45 with the rules' own `requirements.txt` pins and with flo2-ifc's. All 45 are models meant
to pass: for 43 the rule reported nothing where its test expects it to run, and for 2 (PSE001) it reported an error.
The cause is not established; both sets share ifcopenshell 0.9.0 from PyPI. The 21 rules concerned are listed in
`rules.py` (`UPSTREAM_TESTS_NOT_ALL_PASSING`), and a reply flags any of them it ran (`rules_with_a_caveat`).

## Starting a model from nothing (since 0.5.1)

A person designing a building from an idea has no model to upload, so a chat starts one: it writes a small IFC4 file as
text and keeps it in the design with flo2's file tool, and the building-model helper opens it like any other model
(flo2's design 1a97fc1f1de4d5d2, `req:a-chat-starts-a-designs-building-model-from-nothing`). Measured on flo2.io on
2026-10-24, a chat that improvised that text got a model that opened and validated, but nothing said the text was
right, and nothing would say so tomorrow.

`skills/read-a-building-model/references/starting-a-model.md` is the text a chat copies instead: a project in feet (or,
with the lines it gives, metres), its 3D context with `Body` and `Axis`, a site on a latitude and longitude, a
building, its storeys, the relations that join them, and a map projection (`IfcProjectedCRS` and `IfcMapConversion`)
so buildingSMART's GRF003 passes. It says which values to change and how: a new GlobalId for every rooted entity and
how a chat makes one, decimal degrees as degrees, minutes, seconds and millionths (south and west negative in every
part), a storey's height in both its places, the UTM zone and EPSG code, and, plainly, that the easting and northing
need a map projection a chat must be given or must compute, and what to leave out and say when it cannot.

`tests/test_starting_model.py` takes the IFC text out of the page itself and runs on it what `ifc_validate` (with
express rules) and `ifc_check_rules` run: as written, with new GlobalIds, in metres, with a third storey and with one,
there is no error and no warning; with no map projection, the one warning is GRF003. It also checks the page's worked
numbers (the degrees, minutes and seconds; the easting and northing, against pyproj).

The page is flo2-ifc's half of the skill `read-a-building-model`, which flo2 writes and serves. flo2 serves this page
beside it as an unchanged copy at the commit it pins, as it serves flo2-calc's references, and its CI compares the two
byte for byte.

## Building steps (since 0.6.0)

A chat that built a 24-element shell (walls, a slab, a roof, rooms) through ifcmcp's `ifc_shape` and `ifc_edit`
spent 250 calls, about 6 to 8 an element, because each IfcOpenShell function is one call: a polyline, an extrusion
and a representation from the shape builder, then `root.create_entity`, `geometry.assign_representation`,
`geometry.edit_object_placement`, `classification.add_reference` and `spatial.assign_container` (flo2's design
1a97fc1f1de4d5d2, `fact:building-a-models-shell-takes-about-6-helper-calls-per-element-2026-10-24`). It also needed
to know IFC to make an ordinary wall. Anthony's decision (`dec:a-chat-builds-a-models-ordinary-parts-with-one-call-each`)
is a few steps shaped like what a person says, each ONE call:

| Step | What a person gives | What it makes |
|---|---|---|
| `ifc_add_wall` | a baseline (`start`, `end`), `height`, `thickness`; optionally `height_at_end` (a sloped top) or `peak_height` (a gable end), `base_offset`, `align` (centre, left or right of the line) | an `IfcWall`, contained in its storey |
| `ifc_add_slab` | a `footprint` (corners in order), `thickness`; optionally `top_offset`, `kind` (floor or baseslab) | an `IfcSlab`, its top at the storey's level, contained in it |
| `ifc_add_roof` | a `footprint`, `kind` (shed or gable), `eave_height`, `slope` (rise over run), `thickness`; optionally `overhang` | an `IfcRoof` contained in its storey, made of one (shed) or two (gable) sloped `IfcSlab` roof planes |
| `ifc_add_space` | a `footprint`, `height`; optionally `floor_offset`, `long_name` | an `IfcSpace`, part of its storey as rooms are |

Points are `[x, y]` in plan and every length is in the model's own length unit (`ifc_units`), the numbers `ifc_extent`
gives; heights are above the storey's level. Each step takes an optional `name`, and `realizes`, the id of the design
part the element realizes (for example `cmp:north-wall`), kept as an `IfcClassificationReference` with that
identification in the classification "flo2 design". A shed roof's low eave runs along the footprint's first side (its
first two corners); a gable's ridge runs parallel to it, halfway across.

**The frame (since 0.8.0).** Each step takes `frame`. `"world"` (the default, and before 0.8.0 the only frame) reads
`[x, y]` in the model's world plan, the numbers `ifc_extent` and `ifc_info` report. `"storey"` reads them in the
storey's own axes: its placement (its turn about the vertical and its offset, through its site and building) carries
them into the world, so a building turned on its plot is built from the plan's own dimensions. Heights are above the
storey's level in both. The reply's `frame` says which was used and the storey's origin and turn, and notes when a
world-frame call was made on a turned or shifted storey. Until 0.8.0 the steps' texts said only "[x, y] in plan", and
a chat thinking in a turned building's axes had every part turned and shifted (flo2-ifc's design 1510fc3728665dbf,
`fact:building-steps-take-plan-points-in-world-coordinates-not-the-storeys-frame-2026-10-25`). `"storey"` becomes
the default only after a release that says so first. `tests/test_building.py` builds every step on a storey turned
26.03 degrees and shifted to (10, 5), the turn in the site's placement and, separately, in the building's.

Inside, each step uses IfcOpenShell's own api: `root.create_entity`; `geometry.add_wall_representation` (with
`Clipping` planes for a sloped or peaked top) or `geometry.add_slab_representation` (with its `x_angle` for a roof
plane); `geometry.assign_representation`; `spatial.assign_container` or `aggregate.assign_object`;
`geometry.edit_object_placement` (relative to the storey, or to the roof for its planes); and
`classification.add_classification` and `classification.add_reference`. Every input is checked before the model is
touched, and the step runs in one IfcOpenShell transaction, so a refused or failed step leaves the model as it was. A
step is counted as an edit, as `ifc_edit` is, so a measure says the copy was edited.

The steps are additive: `ifc_edit` and `ifc_shape` stay for anything unusual. `tests/test_building.py` builds a small
shell through the server (4 walls, two with sloped tops, a slab, a shed roof with an overhang, and a room) in 7 calls,
and the same shell the old way in 57; the new one validates with express rules and passes buildingSMART's normative
rules.

## Units (since 0.9.0)

A designer read a window's U-factor, `Pset_WindowCommon` `ThermalTransmittance` 1.70348, with no unit: the model
declared none for thermal transmittance, flo2-ifc's readers named only lengths, areas and volumes, and every reply
said only those had units (flo2-ifc's design 1510fc3728665dbf,
`fact:root-cause-a-u-factor-in-a-model-carries-no-unit-and-the-reader-reports-only-length-area-volume-2026-10-26`).
IFC4 says a property with no unit of its own is in "the global unit assignment at IfcProject", and states no default
beyond it, so a measure the project assigns no unit to has none. Now:

- `ifc_units` lists every unit the assignment holds (`declared`), each with its unit type, what it measures, its IFC
  class, name, symbol (W/(m2.K), btu/(hr.ft2.°F), USD: never `?`) and how many SI units one of it is; `say`, in
  every reply, names the other units declared and says that a value of any other measure has no unit;
- `ifc_info` gives each measured property's and quantity's measure type and unit (`property_units`): from the
  property's own `Unit`, the project's unit for its measure, or `"not declared"`, which `units` then names. A label,
  a boolean, a count or a ratio has no unit type and is left out. `property_sets` is unchanged;
- `ifc_add_unit` declares one project unit in one call, with IfcOpenShell's `unit` functions: an SI unit (WATT,
  KELVIN, METRE with MILLI), a conversion-based one with its exact factor (foot, square foot, btu, hour, fahrenheit,
  pound: NIST SP 811's values, where IfcOpenShell's own table rounds several and gives a degree Fahrenheit as 1.8 K),
  a derived one from its `parts`, checked against its unit type's dimensions, or a currency. A unit type already
  declared is refused: every value stored in it is read in it. ifcmcp's `ifc_edit` cannot declare a derived unit
  (`unit.add_derived_unit` takes a mapping of entities, which ifcedit cannot pass);
- `ifc_edit`'s `pset.edit_pset` and `pset.edit_qto` write a measured value only in a declared unit: one the model
  would store with no unit is not written, and the reply says to declare it with `ifc_add_unit` first. Every other
  edit runs as ifcmcp runs it.

## One name for one thing (since 0.9.0)

A designer found one element named four ways across the tools (flo2's design 1a97fc1f1de4d5d2,
`fact:root-cause-a-helpers-describe-is-refused-because-the-client-holds-a-tool-list-without-it-2026-10-26`). The
rule is the name most tools already used (`ARGUMENT_NAMES` in `src/flo2_ifc/server.py`): one element is
`element_id`, several are `element_ids`, a selector is `selector`, and an IFC class the listed elements are kept to is
`element_type`. Wherever flo2-ifc reads one element it takes its step id or its GlobalId.

| Tool | Until 0.9.0 | Since 0.9.0 |
|---|---|---|
| `ifc_select` | `query` | `selector` |
| `ifc_footprint`, `ifc_volume` | `ids` (text) | `element_ids` (text or a list) |
| `ifc_distance` | `a`, `b` | `element_id`, `other_element_id` |
| `ifc_info`, `ifc_relations`, `ifc_extent`, `ifc_footprint`, `ifc_volume` | `element_id`, an integer | `element_id`, a step id or a GlobalId |

Every old name is still taken (`ALIASES`), so a caller holding an old tool list (flo2's door and its chats keep the
list they connected with) calls the same way and is answered the same; the served schemas show only the new names.
An old and a new name with different values is refused. Left as they are: ifcmcp's `ifc_schema` takes `entity_type`,
the IFC class whose schema it reads, most often not an element's; ifcmcp's `ifc_clash`, `ifc_plot` and `ifc_render`
already use these names; `ifc_draw`'s `through`, `around` and `highlight` each name a role, not which element a call
is about.

## A refusal names the form to copy (since 0.13.0)

A chat calls a tool with what it guesses the tool takes, and when the guess is wrong the refusal is the one text it is
sure to read then. Until 0.13.0 every refusal for a malformed call was the library's own: lark's `No terminal matches
'~' ... Expected one of: * DOT` for a selector, pydantic's validator for an argument left out or of the wrong kind,
Python's `No module named 'ifcopenshell.api.space'`, ifcedit's `Unknown parameter 'product' for
spatial.assign_container`; and an argument a tool does not take was dropped, not refused. On flo2.io one chat was
refused six times in three days for a selector (`name ~ "102"`, `name contains "footing"`, `IfcFooting OR IfcPile`),
and guessed `~` again an hour after its refusal (flo2-ifc's design 1510fc3728665dbf,
`fact:root-cause-a-refusal-passes-on-the-library-text-and-never-names-the-form-to-copy-2026-11-06`).

`src/flo2_ifc/refusals.py` says what was wrong in the tool's terms and gives the corrected call, built from the
chat's own input, first (flo2's door passes on a refusal's first 600 characters), then how the thing is written:

| Sent | Refused with |
|---|---|
| `ifc_select` `IfcSpace, name ~ "102"` | `` `~` at character 16 is not part of one. Did you mean `IfcSpace, Name *= "102"`? `` and the selector's forms |
| `ifc_select` `IfcFooting OR IfcPile` | `` Did you mean `IfcFooting + IfcPile`? `` |
| `ifc_select` `IfcRoom`, or a value in single quotes | the class meant (`IfcSpace`), the text in double quotes: both read and select nothing |
| `ifc_extent` `{"location": "Level 1"}` | `` Call it as: `{"selector": "IfcElement, location = \"Level 1\""}` `` |
| `ifc_edit` with `ifc_class`, `name` beside `params` | the call with them inside `params` (it used to run without them) |
| `ifc_edit` `spatial.assign_container` with `product` | `` Did you mean `products`? `` and every parameter with its type |
| `ifc_list` `space`, `ifc_docs` `spatial` | the module meant (`spatial`), the functions of the one named |

Every tool's `selector` is checked before the tool runs (`Server.call_tool`), ifcmcp's included, and every selector
flo2-ifc's own readers resolve goes through `reading.selected` (ifc_draw's `highlight`, `around` and `through` too).
What is checked is IfcOpenShell's own: the selector's grammar, the model's schema, the editing functions'
signatures. A call that reads is answered as before. ifc_docs keeps ifcmcp's reason for a function that does not
exist, as 0.3.1 gave it.

## What changed between two models, and a BCF file of it (since 0.10.0)

A person changes a model and wants the change written down in openBIM's own form (flo2's design 1a97fc1f1de4d5d2,
`req:a-chat-records-what-changed-between-two-building-models-as-an-ifcdiff-and-a-bcf-file`). Two tools do it, with
IfcOpenShell's own code, never a differ or a BCF writer of flo2-ifc's:

| Tool | What it does |
|---|---|
| `ifc_diff` | compares the model open in the session with another model file (`other_path`; the one after, or before with `other_is`) with **IfcDiff** (IfcOpenShell's `ifcdiff`): every element added, deleted and changed, by GlobalId, IFC class and name; what changed on each; and the counts by class. The whole comparison is written as `<output_path>.json`, with IfcDiff's own report in it (`ifcdiff`); the reply lists 50 of each list (`offset`, `limit`) and says what it withheld. |
| `ifc_write_bcf` | writes a **BCF 3.0** file (buildingSMART's BIM Collaboration Format) with **bcf-client** (IfcOpenShell's BCF library): the project, and topics, each with its title, description, type, status, labels, author, comments, the elements it is about (selected, and optionally coloured, in its viewpoint, by GlobalId), a camera (given in metres, or aimed by bcf-client's own camera at the elements in the open model), a snapshot picture, and the model files its header names (with the open model's IfcProject). Written as `<output_path>.bcf`. Needs no model. |

**What IfcDiff is asked, and why** (`src/flo2_ifc/diffing.py`). IfcDiff compares by GlobalId every `IfcElement` and
spatial element (openings left out). flo2-ifc runs it with:

- `relationships`: everything it compares: attributes, properties, type, container, aggregate, classification, and
  geometry. Its own default is attributes and geometry only, which says nothing of a changed fire rating or a wall
  moved to another storey, the changes a person most often makes. Plus `placement`, IfcDiff's quick geometry check
  (`diff_element_basic_geometry`: the placement moved or turned, the openings changed), which IfcDiff has and does
  not run: its full geometry check takes only elements with a "Body" shape in a model view, and on the public
  Clinic_Architectural and Duplex samples it missed three of the ten elements changed (measured 2026-11-02).
- `is_shallow=False`, so an element renamed AND moved says both; and each relationship asked separately, because
  IfcDiff stops at the first that differs.
- **The shapes only where they fit.** IfcDiff's full geometry check makes every shape of both models. Two models over
  8 MiB together (`FLO2_IFC_DIFF_GEOMETRY_MAX_BYTES`) compare placements instead, and say so (`note_geometry`); a call
  that asks for `geometry` there is refused unless `selector` narrows it. Measured: the public AC20-FZK-Haus pair (5.0
  MB together) took 10.2 s and 208 MiB with shapes, in the container; a 49.8 MB pair (the public rac_basic_sample_project) ran for minutes and past
  850 MB on a laptop.
- What a chat needs to say a change, added to IfcDiff's report: which attributes changed, with their values before
  and after; where a changed placement moved to, in the model's length unit.
- **Two fixes to IfcDiff 0.9.0's comparisons, each IfcDiff's own intent.** A property set's STEP id is left out: its
  `exclude_regex_paths=[r".*id$"]` matches no path DeepDiff 9 writes (`root['Pset_X']['id']` ends in `']`), so
  without it every property set of another export of the same model reads as changed
  (`tests/test_diffing.py`, a model renumbered). And an element whose plain attributes or property sets are equal in
  both models is not handed to DeepDiff, which finds nothing there and took 25 of 30 s on a 13 MB model.

**Memory and time: measured, then refused up front** (`tools/measure.py --diff`, one container under flo2's broker
flags: no network, a read-only root, a 64 MB /tmp, user 65534, one CPU, 64 processes, `--memory 1152m` with no swap;
Intel N95; one session opening the model before and comparing it with an edited copy, made with IfcOpenShell's API:
five walls renamed, three elements moved, two deleted, one added, two given a property set). The comparison runs in a
forked child, stopped at the call's time (30 s), so a child the cap stops leaves the session and its model alive.

| Public sample (each model) | Entities | Compared | Peak (memory.peak) | Anonymous | Time |
|---|---|---|---|---|---|
| AC20-FZK-Haus, 2.6 MB (KIT) | 44,249 | with shapes | 208 MiB | 190 MiB | 10.2 s |
| Duplex_A_20110907, 2.4 MB (buildingSMART) | 38,898 | with shapes | 155 MiB | 145 MiB | 1.2 s |
| Clinic_Architectural, 13.0 MB (buildingSMART) | 209,259 | placements | 296 MiB | 264 MiB | 12.6 s |
| ifc4rac_basic_sample_project, 25.4 MB (opensourceBIM) | 496,141 | placements | 441 MiB | 423 MiB | 2.8 s |
| Clinic_HVAC, 26.9 MB (buildingSMART) | 455,957 | placements | 489 MiB | 431 MiB | 17.6 s |
| Duplex_Plumbing_20121113, 31.6 MB (buildingSMART) | 566,909 | placements | 541 MiB | 470 MiB | 4.5 s |
| Schependomlaan, 49.3 MB (buildingSMART) | 784,962 | placements | 766 MiB | 659 MiB | 18.2 s |

No memory event and no kill in any; at most 6 of the 64 processes; each within the call's 30 s. The peak grows by
about 6.5 MiB for each MB of the two files together. So two models over **100 MB together**
(`FLO2_IFC_DIFF_MAX_BYTES`, just over the largest pair measured) are refused before either is opened, in plain words
(measured: 0.03 s, nothing opened); two ~31 MB models (62.5 MB together) fit at 541 MiB. A session that has already
made meshes (a measure, a drawing) holds them too, on top of these. The broker's cap stays 1152 MiB. Placements catch
what shapes alone miss: on the Duplex pair 10 elements read changed with both, 7 with shapes alone (the 3 moved
elements have no "Body" shape in a model view), and on Clinic_Architectural, compared with shapes in 8.0 s at 306
MiB, the same 7 against 10.

**The BCF file is checked before it is kept** (`src/flo2_ifc/bcf_writing.py`). bcf-client writes it; then every part
(bcf.version, project.bcfp, extensions.xml, each topic's markup.bcf and viewpoint) is checked against
**buildingSMART's BCF 3.0 XML schemas**, with xmlschema and no network, and the file is opened again with bcf-client
and must hold the topics, components and comments it was given. bcf-client ships no schemas, so the seven are in this
package (`src/flo2_ifc/schemas/bcf-3.0/`), copied unchanged from github.com/buildingSMART/BCF-XML at tag `v3.0`,
each held to its sha256 by `tests/test_bcf.py`; they are licensed CC BY-ND 4.0 (shared verbatim, with attribution:
the folder's README.md). `extensions.xml` lists every topic type, status, label, priority, stage and user the topics
use, as BCF 3.0 asks. Limits (each an environment setting): 100 topics a file, 2,000 elements a topic and 20,000 in
all, 50 comments and 20 labels a topic, an 8 MiB snapshot.

## Checking what the edits touched (since 0.12.0)

A chat edits a model and flo2's `save_model_as` checks it before keeping it. Until 0.12.0 that check was of the whole
model, however small the edit: on a ~500,000-instance model each save cost a chat three calls of 30 s on flo2.io
(flo2's design 1a97fc1f1de4d5d2, `fact:root-cause-a-large-models-save-costs-a-chat-three-calls-every-time-because-the-
check-is-of-the-whole-model-not-the-edit-2026-11-05`), and a longer call is no fix, because claude.ai stops waiting
near 60 s. The decision (`dec:a-save-checks-only-what-the-edits-touched-and-a-file-versions-own-check-is-kept`)
is to check what the edits touched. flo2-ifc gives flo2 three things for it:

**Each issue names its element and attribute** (`reading.issue`). IfcOpenShell's validator says which instance and
attribute each statement is about; until 0.12.0 `ifc_validate` kept only the level and the message, so "Attribute not
optional" did not say where (1510fc3728665dbf, `fact:ifc-validate-names-an-issue-by-its-message-and-drops-the-element-
and-attribute-ifcopenshell-gives-it-2026-11-05`). Now:

```json
{"level": "error", "message": "Attribute not optional",
 "element": {"id": 78, "type": "IfcRelContainedInSpatialStructure", "global_id": "3upzUlFvb0txIqLN1tw5nO"},
 "attribute": "IfcRelContainedInSpatialStructure.RelatingStructure"}
```

(from a synthetic model, `tests/models.py`; until 0.12.0 the same issue was `{"level": "error", "message":
"Attribute not optional"}`).

`level` and `message` are as before, byte for byte; `element` is null for what is about no instance (the header, a
global rule); a GlobalId used twice also names the `other_element`.

**A record of what the edits touched** (`src/flo2_ifc/edits.py`), since the model was opened (`ifc_load`, `ifc_new`;
`ifc_reset` leaves none). It is IfcOpenShell's own undo record: every change through ifcopenshell.api, and so through
ifcmcp's `ifc_edit`, `ifc_shape`, `ifc_quantify` and flo2-ifc's building steps, passes through the open transaction
(`store_create`, `store_edit` with the old and new value, `store_delete` with the removed instance and every instance
that referenced it). Each call of one of those tools runs with a transaction of the record's own open, read into the
record when the call ends. Every served tool is classed as changing, replacing or reading the model (`edits.CHANGES`,
`REPLACES`, `READS`), and the server will not start with one unclassed. What the record cannot see, it says, and
refuses: a transaction opened inside a call (IfcOpenShell's alignment functions open one), instances added or removed
that it did not see (each changing call is counted), a reading tool that added instances, a model it did not see
opened. A reading tool's attribute edit that adds nothing is not caught; none of ifcmcp's or flo2-ifc's readers writes
the model. `ifc_edited` lists the record a page at a time (`how`: `created`, `changed`, `removed`, `neighbour`, or
`checked`; `element_type`), with `counts` and `since`.

**`ifc_validate` with `scope: "edits"`** checks the instances the edits created and changed that still exist, and
their NEIGHBOURS: what each references and what references it now, and what the changed and removed ones referenced
and were referenced by before (held from the transaction when it happened). An instance's schema check reads its own
attributes, the class of what they name, and how many instances name it through each inverse, so those are the only
instances an edit can make fail without touching them: a removed association leaves its usage with none (`With
inverse: AssociatedTo : SET [1:?] OF IfcRelAssociatesMaterial ... Not valid`), a deleted storey empties its
containment's `RelatingStructure`. The whole model's GlobalIds, header and applications are checked every call. The
reply says `scope: "edits"`, `checked.of` (how many it checked) and `edits` (how many created, changed, removed and
neighbours). When the record cannot say, the call is refused, `scope "edits" is refused: <why> ...`, and the caller
checks the whole model (`scope: "model"`, the default). The schema's rules (`express_rules`) run on the whole model
only.

**The class test** (`tests/test_edits_class.py`) holds the risk this carries, that the narrow check misses an error the
whole one finds. On real edits it compares the two checks element by element (an issue's element, attribute, level and
message): the narrow check finds nothing the whole check does not, and every error the whole check of the edited model
has that the model as opened did not. Measured 2026-11-05 on this machine (Intel N95), the whole check in one call:

| Model | Instances | Edits | Checked by `scope: "edits"` | Errors the edits added | Missed | `scope: "edits"` | `scope: "model"` |
|---|---|---|---|---|---|---|---|
| AC20-FZK-Haus, IFC4 (KIT) | 44,249 | each of the 7 alone | 3 to 81 | 0 to 3 | 0 | 0.01 to 0.02 s | 3.5 to 4.2 s |
| | | all at once | 180 | 7 | 0 | 0.04 s | 3.8 s |
| Duplex_A_20110907, IFC2X3 (buildingSMART) | 38,898 | each of the 7 alone | 3 to 328 | 0 to 6 | 0 | 0.02 to 0.08 s | 2.9 to 3.1 s |
| | | all at once | 444 | 13 | 0 | 0.10 s | 3.0 s |
| Clinic_HVAC, IFC2X3 (buildingSMART) | 455,957 | each of 5 alone (it holds no walls, so two flow segments are removed, and no materials) | 7 to 1,664 | 0 to 5 | 0 | 0.44 to 0.87 s | 34.9 to 35.9 s, and 44.0 s once while another check ran |
| | | all at once | 1,946 | 8 | 0 | 0.97 s | 35.4 s |
| `models.with_materials(6)`, IFC4 (synthetic, in CI) | 209 | each of the 7 alone, and all at once | 2 to 40 | 0 to 4 | 0 | 0.001 to 0.005 s | 0.015 to 0.025 s |

The edits: a room added (`ifc_add_space`'s own function); an element moved (`geometry.edit_object_placement`) and
given another class (`root.reassign_class`); two walls removed (`root.remove_product`); a material association removed
and nothing else; a containment's required `RelatingStructure` emptied (`attribute.edit_attributes`); a storey, and a
material, deleted while others still name them. The narrow check's time is mostly the whole model's GlobalId check
(0.44 s on Clinic_HVAC); the whole check took two calls of 30 s here, and about three on flo2.io, which checks about
2.2 times slower (flo2's root cause above). Its reply was 835 to 2,937 characters on Clinic_HVAC.

On every case the narrow check found exactly the errors the edits added. Some were on instances no edit touched:
removing two walls of the Duplex left three `IfcLocalPlacement`s placing nothing (`PlacesObject`, an inverse), found
because the walls had referenced them. Two of the added errors are flo2-ifc's and IfcOpenShell's own, met by the test
on the IFC2X3 samples: a room added with `ifc_add_space` has no `InteriorOrExteriorSpace`, which IFC2X3 requires, and
the type `root.reassign_class` makes has no `PredefinedType`. CI runs the synthetic cases; the public ones run where
`FLO2_IFC_SAMPLES` names a folder holding AC20-FZK-Haus.ifc and Duplex_A_20110907.ifc.

## Where ifcmcp comes from (since 0.4.0)

ifcmcp is two parts. `core.py` (`IfcSession`) is the tool code: glue over IfcOpenShell's own `ifcquery` and `ifcedit`,
and it never imports `mcp`. `server.py` registers each tool on the MCP Python SDK's FastMCP. Every
`ifcopenshell-mcp` release so far builds that server on the SDK 1.x, and its `[mcp]` extra requires `mcp>=1,<2`.
Every MCP server Anthony runs must be on its language's latest official SDK, which is 2.x, where FastMCP is renamed
`MCPServer`.

Until 0.3.1 flo2-ifc installed ifcmcp from Anthony's fork of IfcOpenShell (sligara7/IfcOpenShell, branch
`ifcmcp/mcp-python-sdk-2`, commit `0ea3edc7a`), where the server was ported to the SDK 2.x. Since 0.4.0
(decision `dec:idea-flo2-ifc-drops-the-ifcmcp-layer-or-the-fork`, settled 2026-10-24):

- `pyproject.toml` pins upstream's own `ifcopenshell-mcp==0.9.0` from PyPI, **without** its `[mcp]` extra. Its
  `core.py` is the fork's, word for word, but for two docstring lines.
- `flo2_ifc.server.build_server` runs ifcmcp's own `build_server` once, against a recorder in place of FastMCP. That
  gives every tool ifcmcp defines, with its own name, arguments, description and function. flo2-ifc then registers
  each of them on its own `MCPServer` (mcp 2.3.0), except the seven its readers replace.
- Every tool, ifcmcp's and flo2-ifc's, is registered through one catch that raises a failure as the SDK's
  `ToolError` with its own reason. MCPServer passes a ToolError's text on, and replaces any other exception with a
  bare "Error executing tool <name>". This is what the fork's commit `0ea3edc7a` did inside ifcmcp's server.
- The tools a chat sees did not change. `tests/test_tool_list.py` compares the running server with what 0.3.1 listed
  (`tests/tools-0.3.1.json`): the same 31 tools in the same order, the same input and output schemas, the same
  descriptions, and the same reason from each failing ifcmcp tool.

The `ifcmcp` command that the release also installs is upstream's own server. It needs mcp<2, so beside mcp 2.3.0 it
answers `--version` (`ifcmcp 0.9.0`) and does not serve. Use `flo2-ifc`.

The fork's port is offered to IfcOpenShell as a pull request of its own
(`req:the-ifcmcp-sdk-2-port-is-offered-to-ifcopenshell`). If upstream takes it, flo2-ifc can compare its server with
upstream's again; the dependency check reports when a new `ifcopenshell-mcp`'s `[mcp]` extra admits the pinned mcp.

flo2-ifc ties itself to two things in ifcmcp that upstream does not promise to keep: `server.py` naming `FastMCP` and
`ImageContent` and defining its tools inside `build_server`, and the tools closing over one `session`. Both are
checked when the server starts, which then fails loudly, and the exact pin and the tests cover a change of release.

## The layers

```
flo2-ifc                this repository: pins, image, plugin, CI, dependency check, the server on mcp 2.x,
  │                     the readers for a whole building, the measures and the drawings, the checks,
  │                     what changed between two models, what the edits touched (7 tools replaced, 17 added)
  ├─ ifctester          IfcOpenShell's IDS checker and IDS API, from PyPI
  ├─ ifcdiff, bcf       IfcOpenShell's IfcDiff and BCF library (bcf-client), from PyPI, with buildingSMART's BCF 3.0 schemas
  ├─ ifc-gherkin-rules  buildingSMART's validation rules, at the release tools/ifc_rules.py pins, run with behave
  └─ ifcmcp             ifcmcp's tool code (25 tools), from ifcopenshell-mcp 0.9.0 on PyPI
       └─ ifcquery, ifcedit (and ifc5d)   the helpers ifcmcp calls into
            └─ ifcopenshell             the Python API
                 └─ the C++ core        compiled into the ifcopenshell wheel
```

flo2.io never builds IfcOpenShell's source tree. It installs released wheels from PyPI.

## What is pinned and why

Every dependency is pinned exactly in `pyproject.toml`. That list is the set flo2.io installs.

| Pin | Why |
|---|---|
| `ifcopenshell-mcp==0.9.0` | ifcmcp's tool code, from IfcOpenShell's own release. Without the `[mcp]` extra, which requires `mcp<2`; flo2-ifc's server registers its tools on the mcp below. |
| `mcp==2.3.0` | The latest official MCP Python SDK, which flo2-ifc's own server is built on. |
| `ifcopenshell==0.9.0`, `ifcquery==0.9.0`, `ifcedit==0.9.0`, `ifc5d==0.9.0` | ifcmcp leaves these unpinned, and they decide what a model can measure. For example, with ifc5d 0.9.0 the base-quantities rule measures a room modelled as a plain box; with 0.8.5 it skips the room without saying so. |
| `networkx==3.7` | Without it, `ifcedit list` prints a "Note: API not available" line to stdout ahead of its JSON. |
| `pytest==9.1.1` | `ifc_validate`'s `express_rules` runs IfcOpenShell's rule executor, which imports pytest's assertion rewriting (`ifcopenshell/express/rule_executor.py`). Without it that check fails with "No module named '_pytest'". The tests use it too. |
| `shapely==2.2.0` | flo2-ifc's footprints and drawings join projected triangles with it. ifcopenshell 0.9.0 requires it, unpinned; flo2-ifc imports it itself, so it is pinned at the version that resolves. |
| `pyvista==0.49.1`, `vtk==9.7.1` | `ifc_render` (since 0.7.0). ifcquery imports pyvista if it can and fails the tool otherwise; pyvista draws with VTK, which decides how a picture is drawn with no display. Their own dependencies (matplotlib, pillow, pooch, scooby, cyclopts) are transitive. They take the image from 660 MB to 1.55 GB (VTK's libraries are 565 MB, Mesa's LLVM 124 MB). |
| `cairosvg==2.9.1` | `ifc_draw` renders its SVG to PNG with it. It loads the cairo library at run time: the image installs Debian's `libcairo2`, and `fonts-dejavu-core` for the words. Its own dependencies (cssselect2, tinycss2, defusedxml, pillow) are transitive. |
| `cairocffi==1.7.1` | Since 0.13.0: `ifc_plan_set` binds a set's sheets into one PDF with cairo's own PDF surface, which cairosvg draws with (cairosvg writes a one-page PDF only). cairosvg 2.9.1 requires it, unpinned; flo2-ifc imports it itself, so it is pinned at the version that resolves (its latest, 2026-11-09). Nothing new is installed. |
| `ifcdiff==0.9.0` | `ifc_diff` (since 0.10.0): IfcOpenShell's own IfcDiff. LGPL-3.0-or-later. |
| `deepdiff==9.1.0`, `orderly-set==5.5.0`, `cachebox==5.2.3` | ifcdiff leaves them unpinned, and they decide what it reports: deepdiff compares attributes and property sets and records a property change, orderly-set is the set ifcdiff imports from it, cachebox is deepdiff's cache. All MIT. |
| `bcf-client==0.9.0`, `xsdata==26.10` | `ifc_write_bcf` (since 0.10.0): IfcOpenShell's own BCF library, and the XML binding its BCF 3.0 model is generated with, which decides what the file says. bcf-client's source files and upstream's `src/bcf/pyproject.toml` say LGPL-3.0-or-later; the 0.9.0 wheel's PyPI classifier still says GPLv3. xsdata is MIT. |
| `ifctester==0.9.0` | `ifc_check_ids` and `ifc_write_ids`: IfcOpenShell's own IDS checker, reporters and IDS API, with the IDS 1.0 XSD. The image removes its browser web app (`ifctester/webapp/www`, 32 MB), which nothing here serves. |
| `xmlschema==4.3.2`, `elementpath==5.1.4` | ifctester leaves them unpinned, and they decide what an IDS says and matches: xmlschema reads an IDS and checks it against the XSD (offline: it carries the W3C schemas the XSD imports), and elementpath turns an IDS pattern into the regular expression a value is matched with. |
| `behave==1.2.6`, `python-dotenv==1.1.1`, `rtree==1.4.0`, `pyproj==3.7.1`, `mpmath==1.3.0`, `pydot==4.0.1`, `pyspellchecker==0.8.3` | What buildingSMART's rules import when they run, at the versions their `requirements.txt` pins (v1.0.5). |
| `django==5.2.18` | The rules' data model is a Django app, imported for its classes; no database is opened. The newest patch of the 5.2 LTS series the rules pin (5.2.4), for its security fixes. Held at 5.2 with the rules (`dependency-holds.toml`). |
| `pyparsing==3.3.3`, `pydantic==2.14.1` | The rules import them; pinned at the versions already installed here (the rules pin 3.2.3 and 2.11.7). numpy, shapely and networkx are flo2-ifc's own pins above. Upstream's tests give the same result with either set (above). The rules' test and CI tools (pytest-xdist, pandas, sqlalchemy, tabulate, lark-parser) are not installed. |
| `jsonschema==4.26.0` (the `test` extra) | The manifest tests. |
| `setuptools==84.2.1` (build) | The build backend, so this package builds the same way every time. |

Some things are deliberately left out:

- **ifctester's other helpers** (flask, odfpy, pystache, python-dateutil). ifctester requires them, so they are
  installed, unpinned: they serve its web app and its ODS and HTML reports, and decide nothing a check says.
  bcf-client, once among them, is pinned above since 0.10.0, when `ifc_write_bcf` began to import it.
- **Transitive dependencies** (pillow and the rest). They are not pinned: each comes in at whatever
  version the pinned packages accept. `pip freeze --all` in the image build and in CI shows what was installed.

## How flo2.io uses it

flo2's `flo2-tool-sandbox` runs one container per person's session on a design. It runs that container with:

- no network (`--network none`);
- a read-only root (`--read-only`);
- the unprivileged user 65534;
- a memory cap;
- only the design's folder mounted, read-only, at `/design`.

So the image needs no network and no writable root at run time:

- `HOME` is `/tmp`;
- no bytecode is written;
- the build checks `flo2-ifc --version` and `ifcmcp --version`, and that pyvista draws with no display;
- VTK draws with Mesa's EGL on the CPU (`VTK_DEFAULT_OPENGL_WINDOW=vtkEGLRenderWindow`, `EGL_PLATFORM=surfaceless`),
  Mesa's shader cache is off, and matplotlib's font cache goes to `/tmp` (`MPLCONFIGDIR`);
- the entry point is `flo2-ifc` on stdio (it was `ifcmcp` until 0.2.0).

flo2 builds its image from this repository's `Dockerfile` at one pinned commit of this repository
(`ops/images/flo2-ifc/COMMIT` in flo2), the same way it builds flo2-cad's, and its broker names the same tag.

```sh
docker build -t flo2-ifc .
docker run --rm --network none --read-only --cap-drop ALL --user 65534:65534 flo2-ifc --version
docker run --rm -i --network none --read-only --tmpfs /tmp --user 65534:65534 \
  --mount type=bind,source="$PWD",target=/design,readonly flo2-ifc     # MCP on stdin/stdout; ifc_draw writes under /tmp
```

The image is a two-stage build: the build stage installs the package from its source, and the final image gets only
the finished environment. Since 0.4.0 every dependency is a PyPI release, so neither stage needs git.

## Use it locally

You need [uv](https://docs.astral.sh/uv/). Every dependency is a release on PyPI, and uv's cache makes a second run
quick.

**As a plugin.** This repository is a plugin in two formats, and both start the same server, named `ifc`:

| Format | Files |
|---|---|
| [Agent Plugins](https://agent-plugins.org) 1.0.0 | `plugin.json`, `mcp.json` |
| Claude Code | `.claude-plugin/plugin.json`, `.mcp.json` |

The server command in both is:

```sh
uvx --from git+https://github.com/flo2-connector/flo2-ifc flo2-ifc
```

**By hand**, in any MCP client's config:

```json
{"mcpServers": {"ifc": {"command": "uvx", "args": ["--from", "git+https://github.com/flo2-connector/flo2-ifc", "flo2-ifc"]}}}
```

**From a checkout:**

```sh
uv venv -p 3.12 && uv pip install '.[test]'
.venv/bin/flo2-ifc            # the server, on stdio
.venv/bin/python -m pytest    # the tests
```

## The tests and the checks

`python -m pytest` runs twenty-four test files:

- `tests/test_refusals.py` (since 0.13.0) holds every refusal for a malformed call to the class: for each kind of
  guess the field made, through every tool that takes the thing guessed, the call is refused with nothing changed,
  the refusal carries none of the library's own words, its first 600 characters give the corrected form built from
  the chat's input, and that form answers; and the selector's forms are in ifc_select's description and SKILL.md.


- `tests/test_skill.py` holds the skill read-a-building-model to its shape: each file at most 16,000 bytes (flo2
  serves each in one reply of at most 30,000 characters), SKILL.md naming every page beside it and no other, and
  every tool a page names one the server serves.

- `tests/test_smoke.py` starts the installed `flo2-ifc` over stdio with the mcp 2.3.0 client. It checks that:
  - the server initializes as `ifc-mcp`, with instructions;
  - it lists ifcmcp's 25 tools and flo2-ifc's eighteen, and the readers, measures, checks, building steps and the
    record of what changed take their arguments;
  - `ifc_new` and then `ifc_summary` both answer;
  - a synthetic millimetre house (`tests/models.py`), opened with `ifc_load`, reads whole: its units, a page of the
    tree, doors by storey, the roof's top, a room's storey, and a complete validation; and it is measured and drawn
    (a footprint, a volume, and a plan rendered to PNG, which checks the image's cairo library and fonts); an IDS is
    written and the house checked against it (one specification passes, one fails, both reports are written), an
    empty IDS is refused, and buildingSMART's critical and informal-proposition rules run on it; and a wall is built
    on it in one call (`ifc_add_wall`), in each frame; and it is rendered in 3D (`ifc_render`), which checks the image's off-screen
    OpenGL; and it is compared with an edited copy (`ifc_diff`, a forked child whose prints must not reach the MCP
    stream) and a BCF 3.0 file written of the change (`ifc_write_bcf`); and the two walls built are what
    `ifc_edited` lists, and what `ifc_validate` with `scope: "edits"` checks (since 0.12.0); and its whole plan set
    is drawn and bound into one PDF of nine pages (`ifc_plan_set`, since 0.13.0), which checks the image's cairo writes
    a PDF.
- `tests/test_tool_list.py` starts it the same way and compares what it lists with flo2-ifc 0.3.1's list
  (`tests/tools-0.3.1.json`, read from 0.3.1 over stdio): the server's name and instructions, 0.3.1's 31 tools in
  order (and only the three checks, the four building steps, `ifc_add_unit`, `ifc_diff`, `ifc_write_bcf`,
  `ifc_edited` and `ifc_plan_set` added since),
  each one's input and output schemas, its description (by sha256, so ifcmcp's LGPL text is not copied here), and the
  reason each of ten failing ifcmcp calls gives. The seven tools renamed in 0.9.0 are held instead to taking every
  argument 0.3.1 took, under its name or as an alias, with its type and whether it is required, as is
  `ifc_validate`, which takes `scope` since 0.12.0.
- `tests/test_units.py` (since 0.9.0) builds a window in feet with a U-factor and no thermal transmittance unit, with
  ifcopenshell.api: every declared unit listed with its symbol, the U-factor said to have no unit, then a project
  unit or the property's own, `ifc_add_unit` declaring W/(m2.K), Btu/(hr.ft2.°F) (5.678263 W/(m2.K)), °F, °C, a
  pound, a kilowatt and a currency and refusing what is not a unit, and `ifc_edit` refusing to write the U-factor
  until its unit is declared.
- `tests/test_argument_names.py` (since 0.9.0) holds every served tool to one name for each thing, every old name to
  being taken and answered as its new one, conflicting names to a refusal, and one element to its step id or its
  GlobalId. A difference means the tools changed, and flo2's real-ifcmcp door
  test must change with them.
- `tests/test_rendering.py` renders the synthetic house in 3D with `ifc_render`, in a child with no display: a
  1280 x 960 PNG on a white ground with shaded faces, a different picture from each side, a selector, and an element
  picked out.
- `tests/test_measuring.py` and `tests/test_drawing.py` test the measures and the drawings on a synthetic model whose
  sizes are known (`models.measured_site()`): a slab over a footing counted once, an open mesh refused a volume, the
  clear distance between two walls, a plan to a scale measured off the SVG, sections along a line, a grid line and
  through named elements, an elevation, and a region.
- `tests/test_plan_set.py` (since 0.13.0) draws plan sets of the synthetic house: the default set (the cover, a plan
  of every storey in elevation order, the four elevations, two sections through the middle, numbered A-001 to A-302,
  each kind at the largest scale that fits all of it), and sheets the caller gives; one paper for the set; each
  sheet's number and title on it, and each sheet `ifc_draw`'s drawing with its number added; the cover naming the
  project, building, file, sha256, date and every sheet; the PDF one vector page per sheet (no picture, its words as
  text, read back from the PDF's own objects); a sheet not drawn named with why; every bad sheet refused with nothing
  written; a set out of time going on from where it stopped, and an edit starting it again; the tool served and
  reading the model only. On the public AC20-FZK-Haus when `FLO2_IFC_SAMPLES` names its folder.
- `tests/test_openings.py` (since 0.8.0) measures and draws a wall with an opening and a door in it
  (`models.wall_with_a_door()`): its volume less the opening (2.622 m3), a section through the door that cuts the
  wall only above the opening, an elevation that shows the door, a plan cut that shows the gap, and a mesh kept per
  setting.
- `tests/test_referenced_elements.py` (since 0.8.1) draws and reads two storeys with a wall that rises from the lower
  through the upper, contained in the lower and referenced into the upper (`models.through_two_storeys()`): the
  upper storey's plan and section cut the wall, its plan leaves out the wall's door on the lower storey, `ifc_tree`
  lists the wall under both storeys, and `ifc_info` names the storey it is referenced into.
- `tests/test_clash_touching.py` (since 0.8.1) holds a known false result of IfcOpenShell 0.9.0's clash check as a
  strict expected failure: a rail flush between the two jambs of an opening reported piercing the wall, on 15 of 36
  turns of a model in feet. The fix is IfcOpenShell's; the release that carries it turns this test red.
- `tests/test_reading.py` tests each reader on synthetic models, at sizes that pass a reply's budget (400 walls on
  one storey), and with a time limit of zero so a partial answer is tested too. No real person's model is ever a
  fixture.

  If `FLO2_IFC_SERVER` is set to a command line, the test starts that command instead. CI uses this to ask the built
  image the same questions, under flo2's flags.
- `tests/test_edits.py` (since 0.12.0) holds each `ifc_validate` issue to naming its element and attribute (a
  required attribute emptied, an inverse failed, a value of the wrong kind, a GlobalId used twice or not valid, a
  rule), the record of what the edits touched to every tool that changes the model (each building step, `ifc_edit`,
  `ifc_shape`, `ifc_quantify`, `ifc_add_unit`), every served tool to being classed (`edits.CHANGES`, `REPLACES`,
  `READS`), `scope: "edits"` to its neighbours, its time and its paging, and each way the record is refused by name
  (a transaction opened inside a call, a reader that added to the model, a model it did not see opened, no model).
- `tests/test_edits_class.py` (since 0.12.0) is the class test (below, "Checking what the edits touched"): on real
  edits, the narrow check finds every error the whole check says the edits added, and nothing it does not, element
  by element, on a synthetic model always and on the public AC20-FZK-Haus and Duplex samples when `FLO2_IFC_SAMPLES`
  names a folder that holds them.
- `tests/test_diffing.py` (since 0.10.0) compares a synthetic house with an edited copy (a wall renamed, one given a
  fire rating, one moved, the door deleted, a column added): the exact GlobalIds added, deleted and changed and what
  changed on each, the counts by class, the kept JSON with IfcDiff's own report, the other model before, `compare`
  and `selector`, a renumbered copy reading unchanged, two large models comparing placements, paging, nothing
  printed to stdout, and every refusal with nothing written (no second model, not IFC, an unknown comparison, too
  large for memory, out of time, the same file).
- `tests/test_bcf.py` (since 0.10.0) writes a BCF 3.0 file and opens it again with bcf-client: the project, every
  topic's title, description, type, status, labels, author and comments, its components selected and coloured, its
  camera (given, perspective and orthogonal, or aimed at its elements), its snapshot and its header's model files;
  every part valid against buildingSMART's schemas (held to their sha256), the extensions listing what the topics
  use, and every refusal with nothing written.
- `tests/test_ids.py` writes an IDS for each facet type (entity, attribute, property, classification, material,
  part_of) and checks a synthetic model that passes it and one that fails it; prohibited, optional and
  required-with-nothing specifications; the reply's citations and both reports; descriptions and IDS files that are
  refused with their reason; and a 1,200-wall model checked over two calls inside the time it is given.
- `tests/test_rules.py` runs buildingSMART's rules on synthetic models: every rule set, a room with no shape failing
  GEM002 by GlobalId and one with a shape passing it, rule sets narrowed, a check out of time going on from `start`,
  and a rule passed over. It needs the rules installed (`tools/ifc_rules.py`, `FLO2_IFC_RULES_DIR`): run locally
  without them it skips, and in CI it fails.
- `tests/test_starting_model.py` checks the starting model a chat copies (above, "Starting a model from nothing"):
  each variant the page describes passes `ifc_validate` with express rules and buildingSMART's rules with no error.
  The rules' half needs them installed, as `tests/test_rules.py` does.
- `tests/test_manifests.py` validates `plugin.json` and `mcp.json` against the Agent Plugins 1.0.0 schemas copied into
  `tests/schemas/` (`SOURCE.txt` there names the spec commit). It also checks that both formats name the same server,
  command and arguments.
- `tests/test_dependency_currency.py` tests the dependency check against fake registry data. It needs no network.

`.github/workflows/ci.yml` has two jobs:

- the tests, on Python 3.12;
- the image: build it, run `--version` with no network, a read-only root and user 65534:65534, then run the smoke and
  tool-list tests against the image.

## The monthly dependency check

Every MCP server Anthony runs has its dependencies reviewed and updated monthly. For this repository, that review is
`tools/dependency_currency.py`. `.github/workflows/dependencies.yml` runs it at 06:00 UTC on the 1st of each month. It
also runs on demand, and on any pull request that touches `pyproject.toml`, `dependency-holds.toml`,
`.github/workflows/**` or `tools/**`.

It compares four things with their latest releases:

- every pin, with PyPI;
- every Action, with its latest GitHub release;
- the Agent Plugins schema version, with the spec repository's latest tag;
- for `ifcopenshell-mcp`, also whether its latest release's `[mcp]` extra still requires `mcp<2`, that is, whether
  IfcOpenShell's own server has caught up with the SDK 2.x. That is reported, never failed.

What the check does with each result:

| Result | What happens |
|---|---|
| A newer patch or minor release | Reported: take it in the next monthly update. |
| A newer major, or a new 0.x minor (0.9 to 0.10), with no hold in `dependency-holds.toml` | Fails. |
| A hold past its `look_again` date, or a hold with no reason or no date | Fails. |
| A pin that is a range instead of an exact version | Fails. |
| A lookup the check could not make | Fails. |
| A dependency installed from a URL (`name @ git+https://...`) instead of a release | Fails (`url_pin`). |

The report is dated. It goes to stdout and to the run's summary, and a JSON copy is kept for 90 days. Run it by hand
with `python3 tools/dependency_currency.py`; set `GITHUB_TOKEN` to raise GitHub's rate limit.

## Moving the ifcopenshell-mcp pin

To a newer `ifcopenshell-mcp` release:

1. Change its line in `pyproject.toml`, still without the `[mcp]` extra (unless that extra now admits the pinned mcp).
2. Install and test: `uv pip install '.[test]' && python -m pytest`. `tests/test_tool_list.py` says whether the tools
   changed. If they did on purpose, regenerate `tests/tools-0.3.1.json` under a new name for the new list, and change
   flo2's door (its real-ifcmcp test) in the same step.
3. Build the image and run its `--version` and the smoke test against it.
4. Open a pull request, then move flo2's pin to the new commit of this repository.

## The design

This repository is designed on flo2.io, in the design **"flo2-ifc"** (id `1510fc3728665dbf`), served at
`https://api.flo2.io/g/1510fc3728665dbf/mcp`. Where this README and the design differ, the design wins. IfcOpenShell,
which this repository installs, has its own design, **"IfcOpenShell fork"** (id `23d874b1643673ad`); flo2-ifc's
records lived there until 2026-10-24.

## Licence

This repository's own files are licensed under the [Apache License 2.0](LICENSE).

ifcmcp and IfcOpenShell, which this repository installs, stay LGPL-3.0-or-later.
