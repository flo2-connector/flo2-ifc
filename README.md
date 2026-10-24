# flo2-ifc: the ifcmcp that flo2.io runs

This is a small repository. It holds the exact set of packages that flo2.io installs to run **ifcmcp**, IfcOpenShell's
MCP server for IFC building models. That server has 25 tools: load, query, edit, validate, quantify and plot a
building model. It is published on PyPI as `ifcopenshell-mcp` and licensed LGPL-3.0-or-later.

The repository holds seven things:

- the pinned set, in `pyproject.toml`;
- the readers for a whole building, the measures and drawings, and the checks (IDS and buildingSMART's rules), in
  `src/flo2_ifc/` (below);
- the starting model a chat copies to start a building model from nothing, in
  `skills/read-a-building-model/references/starting-a-model.md` (since 0.5.1; below, "Starting a model from nothing");
- the container recipe flo2.io builds, in `Dockerfile`;
- the plugin, in two formats;
- a small CI;
- a monthly dependency check.

Until 0.2.0 it held no server code, and `flo2-ifc` handed straight to ifcmcp's own command line. Since 0.2.0 it holds
one thin layer, **the readers for a whole building**, because a real building did not fit through ifcmcp's tools, and
since 0.3.0 **measures and drawings to scale** on the same layer. Since 0.4.0 **the server is flo2-ifc's own**, on the
MCP Python SDK 2.x, serving ifcmcp's tools from upstream's published `ifcopenshell-mcp` rather than from a fork
(below, "Where ifcmcp comes from"). Since 0.5.0 it **checks a model against an IDS and against buildingSMART's
validation rules, and writes an IDS** (below, "Checks").

## The readers for a whole building (since 0.2.0)

A large building model read through flo2 (2026-10-23) showed five problems. A chat holds about 29,000 characters in one reply, and stops waiting at about 60 s:

| ifcmcp's tool | On a large model | flo2-ifc's reader |
|---|---|---|
| `ifc_tree` | over 200,000 characters | places in full, each with its elements counted by class; elements a page at a time, filtered by `storey` and `element_type` |
| `ifc_relations` on a storey | over 150,000 characters | each long list paged (`offset`, `limit`) and filtered by `element_type`; `lists` says what was withheld |
| `ifc_select` "IfcWall" | over 100,000 characters | sorted, a page at a time, with `counts`; the tool text says `location = "<storey>"` selects by storey |
| `ifc_info` on a room | no storey (a room is part of its storey, not contained in it) | adds `storey`, found through containment or aggregation, with the path |
| `ifc_validate` | over 100 s, one call | checks as much as one call allows (30 s), says `complete` and `next_start`; the schema's rules run in a forked child that is stopped at the call's time |
| (none) | no way to say how high the roof is | `ifc_extent`: bounding boxes from the geometry, in the model's unit, metres, and feet and inches |
| (none) | replies said lengths were "usually metres" | `ifc_units`, and `units` in every reply that carries a length, area or volume |

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

Every measure answers in the model's unit, in metres (square, cubic) and in feet and inches (square, cubic feet), and
carries `cite`: the model file's name and sha256, the elements' GlobalIds, the unit and the method, which flo2-calc
keeps with the number. When the session's copy was edited after it was opened, `cite.edited` says the number is of
the edited copy, not of the file the sha256 names.

**Why flo2-ifc draws for itself.** ifcmcp's `ifc_plot` goes through `ifcopenshell.draw`, which in 0.9.0 raises
`'SwigPyObject' object has no attribute 'is_a'` at draw.py:376 in its cells pass (`ray_intersection_result.instance`
is an `express::entity`, which IfcOpenShell's SWIG wrapper does not wrap), and with cells off it draws no scale and
nothing on a storey with no elevation. flo2-ifc installs ifcopenshell 0.9.0 from PyPI, so a fix in the fork would not
reach it; the fix is offered upstream on its own. `ifc_plot` and `ifc_render` stay in the server unchanged.

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

**The meshes are kept** for the session (`FLO2_IFC_MESH_CACHE_MB`, 256 MB; a large building model's all fit
in it), so a second drawing or measure of the same elements does not make them again, and they are forgotten whenever
the session's model changes (an edit, a shape, a reload: `server._watch_changes`). A drawing that runs out of
`FLO2_IFC_SECONDS` stops with `partial` and keeps what it made: the same call again goes on.

Measured on a large building model in the image under flo2's flags (1 CPU, 1 GiB, 2026-10-24): one storey's plan in
8.0 s; a section, which makes the shapes of the whole building first, in two calls
(30.0 s, partial, then 21.9 s), and 21.5 s once its shapes are kept; every measure 0.1 s or less once the shapes are
made; memory at most about 481 MiB (sampled).

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
  │                     the readers for a whole building, the measures and the drawings, the checks
  │                     (7 tools replaced, 9 added)
  ├─ ifctester          IfcOpenShell's IDS checker and IDS API, from PyPI
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
| `cairosvg==2.9.1` | `ifc_draw` renders its SVG to PNG with it. It loads the cairo library at run time: the image installs Debian's `libcairo2`, and `fonts-dejavu-core` for the words. Its own dependencies (cairocffi, cssselect2, tinycss2, defusedxml, pillow) are transitive. |
| `ifctester==0.9.0` | `ifc_check_ids` and `ifc_write_ids`: IfcOpenShell's own IDS checker, reporters and IDS API, with the IDS 1.0 XSD. The image removes its browser web app (`ifctester/webapp/www`, 32 MB), which nothing here serves. |
| `xmlschema==4.3.2`, `elementpath==5.1.4` | ifctester leaves them unpinned, and they decide what an IDS says and matches: xmlschema reads an IDS and checks it against the XSD (offline: it carries the W3C schemas the XSD imports), and elementpath turns an IDS pattern into the regular expression a value is matched with. |
| `behave==1.2.6`, `python-dotenv==1.1.1`, `rtree==1.4.0`, `pyproj==3.7.1`, `mpmath==1.3.0`, `pydot==4.0.1`, `pyspellchecker==0.8.3` | What buildingSMART's rules import when they run, at the versions their `requirements.txt` pins (v1.0.5). |
| `django==5.2.18` | The rules' data model is a Django app, imported for its classes; no database is opened. The newest patch of the 5.2 LTS series the rules pin (5.2.4), for its security fixes. Held at 5.2 with the rules (`dependency-holds.toml`). |
| `pyparsing==3.3.3`, `pydantic==2.14.1` | The rules import them; pinned at the versions already installed here (the rules pin 3.2.3 and 2.11.7). numpy, shapely and networkx are flo2-ifc's own pins above. Upstream's tests give the same result with either set (above). The rules' test and CI tools (pytest-xdist, pandas, sqlalchemy, tabulate, lark-parser) are not installed. |
| `jsonschema==4.26.0` (the `test` extra) | The manifest tests. |
| `setuptools==84.2.1` (build) | The build backend, so this package builds the same way every time. |

Some things are deliberately left out:

- **pyvista.** It is 900 MB, and only `ifc_render` needs it. flo2's door does not offer that tool. Without pyvista, the
  tool is still listed, but calling it fails.
- **ifctester's other helpers** (flask, bcf-client, odfpy, pystache, python-dateutil). ifctester requires them, so
  they are installed, unpinned: they serve its web app and its BCF, ODS and HTML reports, and decide nothing a check
  says. **bcf-client is GPL-3.0**; flo2-ifc's own code never imports it.
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
- the build checks `flo2-ifc --version` and `ifcmcp --version`;
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

`python -m pytest` runs ten test files:

- `tests/test_smoke.py` starts the installed `flo2-ifc` over stdio with the mcp 2.3.0 client. It checks that:
  - the server initializes as `ifc-mcp`, with instructions;
  - it lists ifcmcp's 25 tools and flo2-ifc's nine, and the readers, measures and checks take their arguments;
  - `ifc_new` and then `ifc_summary` both answer;
  - a synthetic millimetre house (`tests/models.py`), opened with `ifc_load`, reads whole: its units, a page of the
    tree, doors by storey, the roof's top, a room's storey, and a complete validation; and it is measured and drawn
    (a footprint, a volume, and a plan rendered to PNG, which checks the image's cairo library and fonts); an IDS is
    written and the house checked against it (one specification passes, one fails, both reports are written), an
    empty IDS is refused, and buildingSMART's critical and informal-proposition rules run on it.
- `tests/test_tool_list.py` starts it the same way and compares what it lists with flo2-ifc 0.3.1's list
  (`tests/tools-0.3.1.json`, read from 0.3.1 over stdio): the server's name and instructions, 0.3.1's 31 tools in
  order (and only the three checks added since),
  each one's input and output schemas, its description (by sha256, so ifcmcp's LGPL text is not copied here), and the
  reason each of ten failing ifcmcp calls gives. A difference means the tools changed, and flo2's real-ifcmcp door
  test must change with them.
- `tests/test_measuring.py` and `tests/test_drawing.py` test the measures and the drawings on a synthetic model whose
  sizes are known (`models.measured_site()`): a slab over a footing counted once, an open mesh refused a volume, the
  clear distance between two walls, a plan to a scale measured off the SVG, sections along a line, a grid line and
  through named elements, an elevation, and a region.
- `tests/test_reading.py` tests each reader on synthetic models, at sizes that pass a reply's budget (400 walls on
  one storey), and with a time limit of zero so a partial answer is tested too. No real person's model is ever a
  fixture.

  If `FLO2_IFC_SERVER` is set to a command line, the test starts that command instead. CI uses this to ask the built
  image the same questions, under flo2's flags.
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

This repository is designed on flo2.io, in the design **"IfcOpenShell fork"** (id `23d874b1643673ad`). Read it through
the `ifcopenshell-fork` MCP connection. Where this README and the design differ, the design wins.

## Licence

This repository's own files are licensed under the [Apache License 2.0](LICENSE).

ifcmcp and IfcOpenShell, which this repository installs, stay LGPL-3.0-or-later.
