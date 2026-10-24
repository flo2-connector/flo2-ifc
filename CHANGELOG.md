# Changelog

flo2-ifc's versions, newest first. Versions before 0.3.1 are described in README.md, in the sections marked "since".

## 0.5.1 (unreleased)

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
