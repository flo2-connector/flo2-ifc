# Changelog

flo2-ifc's versions, newest first. Versions before 0.3.1 are described in README.md, in the sections marked "since".

## 0.9.1 (unreleased)

A fix, no tool added or renamed, every served schema unchanged (flo2's re-pin needs no tool-list change):

- **A name a reply gives an element is a name the tools take**
  (1510fc3728665dbf, `fact:root-cause-a-reply-names-an-element-id-while-the-tools-take-element-id-2026-10-31`).
  0.9.0 gave each thing one name in what the tools TAKE, but their replies still name an element `id` (its step id)
  and `global_id`, and neither was taken: a chat on flo2.io read `{"id": 157952}` off `ifc_select` and was refused
  `ifc_info {"id": 157952}`. Now every tool whose served schema takes `element_id` also takes `id` as it, and
  `global_id` where that `element_id` takes a GlobalId (ifcmcp's `ifc_clash` takes the step id alone): aliases,
  never served, read from each tool's own schema (`server.REPLY_NAMES`, `reply_names_taken`), so a tool added later
  is covered without a list to keep. Guard: `tests/test_argument_names.py`. Every one-element tool takes both names
  as its schema says, and every key in a real reply that holds an element's step id or GlobalId is a name the tools
  take. Checked failing before the fix: `ifc_clash` ran with `id` ignored.

## 0.9.0 (unreleased)

One tool is added (`ifc_add_unit`, 39 tools), seven tools' arguments are renamed with every old name still taken, and
`ifc_units`, `ifc_summary` and `ifc_info` say more: a minor release. flo2 re-pins to take it (its pinned tool list and
the skill pages it serves change). Two root causes found on 2026-10-26:

- **Every unit, and the unit of every measured value**
  (1510fc3728665dbf, `fact:root-cause-a-u-factor-in-a-model-carries-no-unit-and-the-reader-reports-only-length-area-volume-2026-10-26`).
  A window's U-factor (`Pset_WindowCommon` `ThermalTransmittance` 1.70348) came back bare: the model declared no
  thermal transmittance unit, `reading.units()` read only lengths, areas and volumes, property values came back with
  no measure type or unit, and every reply's note spoke only of lengths, areas and volumes. Now:
  - `ifc_units` lists EVERY unit the IfcUnitAssignment holds (`declared`): SI, conversion-based, derived and the
    currency, each with its unit type, what it measures, IFC class, name, symbol and SI scale. Symbols are composed
    for derived units (W/(m2.K), btu/(hr.ft2.°F)) from a full SI table, where IfcOpenShell answered `?/m2.?`. The
    `length`, `area` and `volume` entries are as before;
  - `units`, in every reply, also names the other units declared and says that a value of any other measure has no
    unit unless its property names one: never assume SI. IFC4 states no default beyond the project's assignment
    (`IfcPropertySingleValue.Unit`, `IfcPhysicalSimpleQuantity.Unit`; cited in `reading.py`);
  - `ifc_info` adds `property_units`: each measured property's and quantity's measure type and unit, from the
    property itself, the project, or `"not declared"`, which its `units` then names. `property_sets` is unchanged;
  - **new tool `ifc_add_unit`** declares one project unit in one call with IfcOpenShell's `unit` functions: an SI
    unit, a conversion-based one with its exact factor (NIST SP 811; IfcOpenShell's table rounds several and gives
    a degree Fahrenheit as 1.8 K, which would make a Btu U-factor 3.24 times too large), a derived one from its
    `parts` checked against its unit type's dimensions, or a currency; a unit type already declared is refused.
    ifcmcp's `ifc_edit` could not declare a derived unit (`unit.add_derived_unit` fails with "Attribute not set");
  - `ifc_edit`'s `pset.edit_pset` and `pset.edit_qto` write a measured value only in a declared unit: a value that
    would be stored with no unit is not written (`{"ok": false, "error": ...}`, the edit undone), and the reply says
    to declare the unit first. Every other edit is ifcmcp's, unchanged.

  `tests/test_units.py` builds the window in feet with ifcopenshell.api.
- **One name for one thing across the tools, every old name still taken**
  (1a97fc1f1de4d5d2, `fact:root-cause-a-helpers-describe-is-refused-because-the-client-holds-a-tool-list-without-it-2026-10-26`,
  its flo2-ifc part). The rule is the name most tools already used (`server.ARGUMENT_NAMES`): one element
  `element_id`, several `element_ids`, a selector `selector`. `ifc_select`'s `query` is now `selector`;
  `ifc_footprint`'s and `ifc_volume`'s `ids` is `element_ids` (text or a list); `ifc_distance`'s `a` and `b` are
  `element_id` and `other_element_id` (its reply still calls them `a` and `b`); and `element_id` in `ifc_info`,
  `ifc_relations`, `ifc_extent`, `ifc_footprint` and `ifc_volume` takes a GlobalId as well as a step id. Every old
  name is taken as an alias (`server.ALIASES`, applied before the arguments are checked: until now an unknown name
  was silently dropped), and an old and a new name with different values is refused. Served schemas show the new
  names only. Kept, with reasons in `server.py`: ifcmcp's `ifc_schema` `entity_type` (an IFC class, most often not an
  element's), ifcmcp's `ifc_clash`/`ifc_plot`/`ifc_render` (already these names), `ifc_draw`'s role names.
  `tests/test_argument_names.py` holds the rule and every alias; `tests/test_tool_list.py` now holds the seven
  renamed tools to taking every argument 0.3.1 took.

## 0.8.1 (unreleased)

A fix: no tool is added or removed, and every tool's name, arguments and description are 0.8.0's. A plan of a storey,
and `ifc_tree`, can now hold more than before.

- **A storey holds what is referenced into it** (`fact:a-storeys-readers-walk-only-what-it-contains-not-what-is-referenced-into-it-2026-10-26`;
  reported on the Shop sample, `fact:loft-plan-still-omits-walls-referenced-into-the-loft-2026-10-25`). A plan of a
  loft left out the outer walls that rise through it from the storey below, though it drew the loft windows set in
  them. IFC places such a wall in its lowest storey (contained) and references it into the others; the readers that
  answer "what is on this storey" walked only containment and decomposition. Now, in one place
  (`reading.referenced_into`):
  - a plan, and a section or elevation limited to a storey, draw what is referenced into the storey too, with the
    parts of each that belong to no other storey (the wall, not its door below) (`drawing.storey_elements`);
  - `ifc_tree` lists a storey's referenced elements after those it contains, each marked `"referenced": true`, and
    counts them in `referenced_counts`;
  - `ifc_info` names the other storeys an element is referenced into (`referenced_into`).

  `tests/test_referenced_elements.py` (`models.through_two_storeys`) failed on 0.8.0 on all four (the plan, the
  section, the storey's elements and the tree) before the fix. A selector's `location = "<storey>"` is IfcOpenShell's
  and still picks only what the storey contains; the skill's `references/large-models.md` says so.
- **Known false result, pinned: `ifc_clash` on an element flush between two jambs**
  (`fact:ifc-clash-reports-an-element-touching-both-jambs-as-piercing-the-wall-2026-10-26` in IfcOpenShell's design
  23d874b1643673ad; reported on the Shop sample as `fact:ifc-clash-still-ignores-openings-in-flo2-ifc-0-8-0-2026-10-25`).
  The clash does cut openings. A rail exactly as long as the opening it stands in was reported piercing the wall as
  deep as the wall is thick: IfcOpenShell's clash tree counts the places where an edge's own ends touch the other
  shape as a piercing, when rounding puts them inside the edge. The fix is IfcOpenShell's, prepared in the fork and
  not offered yet; nothing is changed here. `tests/test_clash_touching.py` holds it as a strict expected failure, so
  the ifcopenshell release that fixes it turns the test red and the mark comes off.

## 0.8.0 (unreleased)

The four building steps each take a new optional argument, `frame`, and the measures and drawings now cut openings,
so a number a chat reads can change: this is a minor release. No tool is added or removed; every other tool's name,
arguments and description are 0.7.0's. Both fix root causes found on 2026-10-25 (flo2-ifc's design
1510fc3728665dbf, `dec:building-steps-take-a-frame-and-measures-cut-openings`, accepted by Anthony on 2026-10-25).

- **The building steps take `frame`** (`fact:building-steps-take-plan-points-in-world-coordinates-not-the-storeys-frame-2026-10-25`).
  `ifc_add_wall`, `ifc_add_slab`, `ifc_add_roof` and `ifc_add_space` read their `[x, y]` points in the model's WORLD
  plan and only their heights relative to the storey, and their texts said only "[x, y] in plan", so a chat thinking
  in a turned building's own axes had every part turned and shifted (reproduced: a wall `[0, 0]` to `[5, 0]` on a
  storey turned 26.03 degrees at (10, 5) landed at world (0, 0), -26.03 degrees and 11.18 m off in the storey's
  frame). Now `frame: "storey"` reads them in the storey's own axes, which its placement turns and shifts into the
  world; `frame: "world"` reads them as `ifc_extent` and `ifc_info` report them, and is the default, so no chat
  breaks. Every reply says which frame it used (`frame.used`), with the storey's origin and turn, and a world-frame
  reply on a turned or shifted storey says so. The tools' texts, the skill's `references/editing.md` and the README
  name the frame. Making `"storey"` the default needs a release that says so first. `tests/test_building.py` builds
  every step on a storey turned and shifted through its site and, separately, through its building
  (`models.turned_storey`).
- **Measures and drawings cut openings** (`fact:measures-and-drawings-skip-opening-subtraction-so-a-cut-wall-reads-whole-2026-10-25`).
  The meshes behind `ifc_volume`, `ifc_footprint`, `ifc_distance` and `ifc_draw` (in flo2, `draw_model`) were made
  with `ifc_extent`'s geometry setting, opening subtraction OFF, on the premise that a Revit export's meshes come
  already cut. A wall with an opening made in the model was measured whole (3.0 m3 for 2.622) and drawn solid, its
  door hidden. They now have their own setting, openings cut (`geometry.mesh_settings`), and a mesh is kept per
  setting; `ifc_extent`'s boxes keep the fast setting. The premise was false for the public samples too: their walls
  held 54.50 m3, not 64.78 (AC20-FZK-Haus), and 207.92 m3, not 240.61 (the Duplex, a Revit export).
  `tests/test_openings.py` measures and draws a wall with an opening and a door (`models.wall_with_a_door`).
- **What cutting costs**, cold, one CPU (an i5-10300H core), before and after, in seconds:

  | Model | `ifc_volume` of every wall | the ground storey's plan | a section through a door |
  |---|---|---|---|
  | AC20-FZK-Haus (2.6 MB; 13 walls, 17 openings) | 0.13 to 1.92 | 0.49 to 0.77 | 0.57 to 2.35 |
  | Duplex (2.4 MB; 57 walls, 50 openings) | 0.15 to 0.44 | 0.31 to 0.31 | 0.90 to 1.43 |

  Up to 15 times slower for a volume, and still a few seconds; a kept mesh costs nothing the second time. The other
  geometry libraries were no faster with openings cut, so `hybrid-cgal-simple-opencascade` stays.

## 0.7.0 (unreleased)

No tool is added or removed, and every tool's name, arguments and description are 0.6.0's; `ifc_render`, listed and
failing until now, draws. It adds two pins and 890 MB to the image, so this is a minor release.

- **`ifc_render` draws a 3D picture, with no display** (flo2's design 1a97fc1f1de4d5d2,
  `dec:a-chat-renders-a-building-model-in-3d`; flo2's door offers it through its own step, which keeps the PNG in the
  design). New pins: `pyvista==0.49.1` and `vtk==9.7.1`. The image installs Mesa's EGL and llvmpipe (`libegl1`,
  `libegl-mesa0`, `libgl1-mesa-dri`, `libgl1`) and names VTK's EGL window, so VTK draws on the CPU on Mesa's
  surfaceless platform under flo2's flags (no network, a read-only root, user 65534). The build checks that pyvista
  draws; the smoke test renders the synthetic house through the confined image; `tests/test_rendering.py` renders it
  with no display from each side, with a selector and with an element picked out.
- **Fixed: ifcmcp's image tools failed after drawing.** flo2-ifc read ifcmcp's tools with mcp 2.x's `ImageContent`
  set in ifcmcp's server module, then put it back to None; `ifc_render` and `ifc_plot` look it up when called, so
  both failed with "'NoneType' object is not callable" once they had drawn. It is now left set.
- **The skill read-a-building-model lives here, split** (Anthony's ruling of 2026-10-25 in flo2's design
  1a97fc1f1de4d5d2: "Yes, go with your recommendations"). Until now flo2 wrote its SKILL.md, one page every feature
  added a section to (4,205 bytes on 2026-10-03, 26,766 on 2026-10-24, its reply 67 characters under flo2's
  30,000-character limit), and a render section would have put it over. Now `skills/read-a-building-model/SKILL.md`
  holds the rules (11,120 bytes) and names a page for each kind of task: `large-models.md`, `measuring.md`,
  `drawing.md` (with the new 3D picture), `ids.md`, `validation-rules.md`, `editing.md` and `shapes.md`, beside
  `starting-a-model.md`; flo2 serves the whole folder unchanged. `tests/test_skill.py` holds each file to 16,000
  bytes, SKILL.md to naming every page, and every tool a page names to one the server serves.
- **The image is 1.55 GB** (0.6.0: 660 MB): VTK's libraries 565 MB, Mesa's LLVM 124 MB, matplotlib 34 MB.
- **`tools/measure.py`**: a session's peak memory while it renders (memory.peak, and the anonymous peak sampled from
  memory.stat), in flo2's container shape, for the broker's cap. Measured on 2026-10-25 under a 1 GiB cap, one CPU,
  a session opening a model and rendering it five times (iso, top, south, the built elements only, one wall picked
  out): bhome's model (14 KB) 323.9 to 425.2 MiB, the public AC20-FZK-Haus sample (2.6 MB) 545.9 to 589.8 MiB, the
  public Duplex sample (2.4 MB) 470.2 to 507.7 MiB; no memory event and no kill. A render of either sample took 23 to
  29 s at one CPU (Intel N95).

## 0.6.0

Four tools are added, so this is a minor release. Every tool 0.5.1 served is unchanged.

- **Building steps: a model's ordinary parts, one call each** (flo2's design 1a97fc1f1de4d5d2,
  `dec:a-chat-builds-a-models-ordinary-parts-with-one-call-each`). Measured on 2026-10-24, a chat building a
  24-element shell through `ifc_shape` and `ifc_edit` spent 250 calls, about 6 to 8 an element
  (`fact:building-a-models-shell-takes-about-6-helper-calls-per-element-2026-10-24`). Each step creates the element,
  gives it its shape, places it, puts it on its storey and links it to the design part it realizes, from what a
  person would say, in the model's own length unit:
  - `ifc_add_wall`: a wall along a baseline, with its height and thickness, optionally a sloped top
    (`height_at_end`) or a gable end (`peak_height`), centred on the line or on either side of it;
  - `ifc_add_slab`: a slab over a footprint, its top at the storey's level;
  - `ifc_add_roof`: a shed or gable roof over a footprint (an `IfcRoof` of one or two sloped roof planes), from its
    eave height, slope (rise over run), thickness and overhang;
  - `ifc_add_space`: a room over a footprint, part of its storey, with its height.

  `realizes` keeps the design part's id as a reference in the classification "flo2 design". Each step uses
  IfcOpenShell's own api functions (`geometry.add_wall_representation` with clippings, `add_slab_representation`
  with `x_angle`, `edit_object_placement`, `spatial.assign_container`, `aggregate.assign_object`,
  `classification.add_reference`), checks every input before it touches the model, and runs in one transaction, so a
  refused step changes nothing. `ifc_edit` and `ifc_shape` stay for anything unusual. `tests/test_building.py` builds a
  small shell through the server in 7 calls where the old way took 57, and holds it to express rules and
  buildingSMART's normative rules.

## 0.5.1

The tools a chat sees are unchanged (names, arguments and descriptions), so this is a patch release.

- **A conversion-based unit is named with its symbol, never "(?)".** A model in feet as Revit writes it (`FOOT`,
  `SQUARE FOOT`, `CUBIC FOOT`) read "Lengths are in feet (?)" in `ifc_units`' sentence, which flo2 repeats in every
  reply about the model: IfcOpenShell's `get_unit_symbol` looks a conversion-based unit up by its name exactly as its
  table spells it (`foot`), and answers `?` otherwise. flo2-ifc now looks it up in any case, as IFC (and buildingSMART's
  PJS001) compares these names, so it reads "Lengths are in feet (ft)"; a name no table knows is named with no symbol.
  The measures' `cite` and `ifc_extent`'s `length_unit` say `ft` too, where they said `?`. (flo2's design
  1a97fc1f1de4d5d2, `req:a-chat-starts-a-designs-building-model-from-nothing`, its fix 3.)
- **The starting model a chat copies**, `skills/read-a-building-model/references/starting-a-model.md`: an IFC4
  project in feet or metres, a site on given coordinates, a building, its storeys, the relations, and a map projection
  so buildingSMART's GRF003 passes; with how to make GlobalIds, write a latitude and longitude, add a storey, and what
  to do without projected coordinates. `tests/test_starting_model.py` holds every variant it describes to no error under
  `ifc_validate` with express rules and buildingSMART's rules. flo2 serves it beside its skill `read-a-building-model`.
  (The same requirement, its fix 2.)

## 0.5.0 (2026-10-24)

Three tools are added, so this is a minor release. Every tool 0.4.0 served is unchanged.

- **`ifc_check_ids`: a model checked against an IDS**, with IfcOpenShell's own IDS checker (ifctester 0.9.0). Each
  specification passed or failed, how many elements it applies to, and the failing elements by GlobalId with
  ifctester's reason, paged; ifctester's own HTML and JSON reports written where the caller says; the IDS and the
  model file cited by name and sha256. An IDS invalid against the IDS 1.0 schema is refused with the schema's reason.
  It runs in a forked child stopped at the call's time, and a check that does not finish says where to go on from.
- **`ifc_write_ids`: an IDS written from a structured description** with ifctester's API (entity, attribute,
  property, classification, material and part_of facets; plain values or restrictions; required, optional or
  prohibited), checked against the IDS 1.0 XSD and read back before it is written, refused with the reason otherwise.
- **`ifc_check_rules`: buildingSMART's validation rules**, the normative rules its validation service runs
  (github.com/buildingSMART/ifc-gherkin-rules v1.0.5, MIT), run unchanged and offline with behave on the model file,
  stopped at the call's time and gone on from `start`. The reply says which rule sets ran and which did not (the
  schema and EXPRESS checks are `ifc_validate`'s; the bSDD check needs the network), and flags the 21 rules whose own
  upstream tests do not all pass in this build. `tools/ifc_rules.py` pins the release by tag, commit and sha256; the
  image's build stage installs it at `/opt/ifc-gherkin-rules`.
- **New pins**: ifctester, xmlschema and elementpath; behave, django (5.2.18), python-dotenv, rtree, pyproj, mpmath,
  pydot, pyspellchecker, pyparsing and pydantic for the rules. Holds for django and pyspellchecker, which move with the
  rules' release. The dependency check also checks the rules' release against upstream's latest (kind `rules`).
- **The image** is 660 MB (0.4.0: 560 MB): ifctester without its 32 MB browser web app, the rules (3.2 MB) and their
  dependencies.

- **A drawn room's label shows the area the model stores for it.** `ifc_draw` labelled every room on a plan with the
  area measured from its outline, which can differ from the area the authoring tool stores for the same room. Now a
  room is labelled with its stored area, `Qto_SpaceBaseQuantities.NetFloorArea` first, then an `Area` in another set
  (Revit's "Dimensions" first), read in the model's area unit; only a room that stores none is labelled as measured.
  The reply's `room_areas` says, per room, which was used, and `notes` names each room whose stored and measured
  areas differ by more than 5%, with both numbers. A model that stores no room areas draws exactly as before.
  (23d874b1643673ad's `req:a-drawn-rooms-label-shows-the-models-stored-room-area`.)

## 0.4.0 (2026-10-24)

The tools a chat sees are unchanged. Where their code comes from changes, so this is a minor release.

- **ifcmcp comes from IfcOpenShell's own release, not the fork.** `pyproject.toml` pins `ifcopenshell-mcp==0.9.0` from
  PyPI, without its `[mcp]` extra (which requires mcp<2), instead of
  `ifcopenshell-mcp[mcp] @ git+https://github.com/sligara7/IfcOpenShell@0ea3edc…`. Its `core.py` is the fork's but for
  two docstring lines, and it never imports mcp. The fork is no longer a dependency, and nothing is installed from git.
- **The server is flo2-ifc's own, on the MCP Python SDK 2.x.** `flo2_ifc.server.build_server` runs ifcmcp's
  `build_server` once against a recorder in place of FastMCP, and registers every tool it defines on its own
  `MCPServer` (mcp 2.3.0) with the same name, arguments and description, except the seven its readers replace.
- **A failing tool still says why**, through one catch in flo2-ifc's server that raises the failure as a `ToolError`
  with its reason, for every tool. The fork did this inside ifcmcp's server (its commit 0ea3edc7a).
- **`tests/test_tool_list.py`** compares the running server with 0.3.1's list, read over stdio
  (`tests/tools-0.3.1.json`): the server's name and instructions, the 31 tools in order, every input and output
  schema, every description (by sha256), and the reason each of ten failing ifcmcp calls gives. All are the same. CI's
  image job runs it against the image too.
- **The dependency check** has no fork pin to read. A dependency installed from a URL now fails it (`url_pin`), and
  `ifcopenshell-mcp`'s row says whether upstream's own server has caught up with mcp 2.x.
- The image's build stage no longer installs git. `ifcmcp`, still on PATH, is upstream's own server: beside mcp 2.3.0
  it answers `--version` (`ifcmcp 0.9.0`, where the fork said `0.0.0`) and does not serve.
- `flo2-ifc --version` is one line naming what runs: `flo2-ifc 0.4.0 (ifcopenshell-mcp 0.9.0, mcp 2.3.0)`, read from
  the installed packages. flo2's broker reads its first line.

## 0.3.1 (2026-10-24)

- **A room that holds other rooms is named clear of them, or not at all.** `ifc_draw` drew such a room's name at its
  middle, on top of the names of the rooms inside it, so neither could be read. Now it is named in its part clear of
  the rooms it holds when its name fits there whole, and otherwise left unnamed on the sheet, with the reply listing it
  in `unlabelled_rooms` and saying so in `notes`. "Holds" is most of the other room, not all of it: at least 90% of
  its drawn area inside, and the other the smaller. The names of rooms that hold none do not move. Sections name rooms
  the same way.
- **A freed model's meshes go with it.** The meshes a session keeps were keyed by `id(model)`, which Python gives to
  the next object made once a model is freed, so a model made after another was freed could be drawn with the first
  one's shapes (seen in this release's tests: 8 of 30 pairs in a loop). The server already forgot every mesh when its
  model changed; now a model's meshes are also dropped when the model itself is freed.
- Example names and figures in the README, the tool descriptions, the code's comments and the tests are made generic
  (storeys such as "Level 1", "a large building model").
- The build backend's pin moves from setuptools 84.0.0 to 84.2.1, its latest release in the same major series. The
  package builds and its tests pass as before.
