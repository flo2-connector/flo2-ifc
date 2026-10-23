# flo2-ifc: the ifcmcp that flo2.io runs

This is a small repository. It holds the exact set of packages that flo2.io installs to run **ifcmcp**, IfcOpenShell's
MCP server for IFC building models. That server has 25 tools: load, query, edit, validate, quantify and plot a
building model. It is published on PyPI as `ifcopenshell-mcp` and licensed LGPL-3.0-or-later.

The repository holds six things:

- the pinned set, in `pyproject.toml`;
- the readers for a whole building, in `src/flo2_ifc/` (below);
- the container recipe flo2.io builds, in `Dockerfile`;
- the plugin, in two formats;
- a small CI;
- a monthly dependency check.

Until 0.2.0 it held no server code, and `flo2-ifc` handed straight to ifcmcp's own command line. Since 0.2.0 it holds
one thin layer, **the readers for a whole building**, because a real building did not fit through ifcmcp's tools.

## The readers for a whole building (since 0.2.0)

The first real Revit export read through flo2 (IFC4, 165,111 entities, lengths in millimetres, 2026-10-23) showed five
problems. A chat holds about 29,000 characters in one reply, and stops waiting at about 60 s:

| ifcmcp's tool | On that model | flo2-ifc's reader |
|---|---|---|
| `ifc_tree` | 230,618 characters | places in full, each with its elements counted by class; elements a page at a time, filtered by `storey` and `element_type` |
| `ifc_relations` on a storey | up to 167,000 characters | each long list paged (`offset`, `limit`) and filtered by `element_type`; `lists` says what was withheld |
| `ifc_select` "IfcWall" | 110,811 characters | sorted, a page at a time, with `counts`; the tool text says `location = "<storey>"` selects by storey |
| `ifc_info` on a room | no storey (a room is part of its storey, not contained in it) | adds `storey`, found through containment or aggregation, with the path |
| `ifc_validate` | 105 s, one call | checks as much as one call allows (30 s), says `complete` and `next_start`; the schema's rules run in a forked child that is stopped at the call's time |
| (none) | no way to say how high the roof is | `ifc_extent`: bounding boxes from the geometry, in the model's unit, metres, and feet and inches |
| (none) | replies said lengths were "usually metres" | `ifc_units`, and `units` in every reply that carries a length, area or volume |

`src/flo2_ifc/server.py` builds ifcmcp's own server, takes its one session, and replaces seven tools with the readers
in `src/flo2_ifc/reading.py`. Each replacement keeps its name and only ADDS optional arguments. `ifcmcp`, also on PATH
in the image, is still the fork's server alone.

These readers are flo2-ifc's, not the fork's: the fork stays IfcOpenShell plus the SDK port. The ones ifcmcp itself
would want (paging, the storey of a room, units, a timed validation, extent) can be offered to the fork as their own
upstreamable changes; that is a separate decision.

The limits are set by the environment: `FLO2_IFC_PAGE_CHARS` (20,000 characters of JSON in one listing),
`FLO2_IFC_SECONDS` (30 s of measuring or checking in one call), `FLO2_IFC_GEOMETRY_LIBRARY`
(`hybrid-cgal-simple-opencascade`: the Wetmore model's 84 doors took 86 s with IfcOpenShell's default library and
1.8 s with this one) and `FLO2_IFC_GEOMETRY_BATCH` (250 elements per geometry iterator: one iterator over the whole
model peaked at 1.18 GB, over flo2's 1 GiB cap; batches of 250 peak at 390 MB).

## Why this is separate from the fork

ifcmcp on PyPI is built on the MCP Python SDK 1.x: every `ifcopenshell-mcp` release so far requires `mcp<2`. Every MCP
server Anthony runs must be on its language's latest official SDK, so ifcmcp is ported to the SDK 2.x (`MCPServer`) on
Anthony's fork of IfcOpenShell:

- repository: [sligara7/IfcOpenShell](https://github.com/sligara7/IfcOpenShell)
- branch: `ifcmcp/mcp-python-sdk-2`
- commit: `0ea3edc7a38d08c4c40918201357bd637fdaa39d`

There, 74 of 74 ifcmcp tests pass on mcp 2.3.0. A failing tool still tells the agent why: the port raises the SDK's
`ToolError`, whose text MCPServer passes on (any other exception reaches the agent as a bare "Error executing tool").

**The fork stays whole: a copy of IfcOpenShell plus that port, and nothing else.** Everything flo2 needs lives here
instead: the pins, the image, the plugin, the CI and the dependency check. That keeps two things clean:

- the monthly merge of upstream IfcOpenShell into the fork;
- the pull request that offers the port upstream.

Nothing flo2-specific ever goes into the fork.

## The layers

```
flo2-ifc                this repository: pins, image, plugin, CI, dependency check,
  │                     and the readers for a whole building (7 tools replaced, 2 added)
  └─ ifcmcp             the MCP server (25 tools), from the fork at one commit
       └─ ifcquery, ifcedit (and ifc5d)   the helpers ifcmcp calls into
            └─ ifcopenshell             the Python API
                 └─ the C++ core        compiled into the ifcopenshell wheel
```

flo2.io never builds IfcOpenShell's source tree. It installs released wheels from PyPI.

ifcmcp builds from the fork's subdirectory `src/ifcmcp` alone. That build is pure Python, with no C++. The fork's
source says `0.0.0` as its version, because upstream stamps the real number when it makes a release. So
`ifcmcp --version` prints `ifcmcp 0.0.0`. The commit pinned in `pyproject.toml` says what actually runs.

## What is pinned and why

Every dependency is pinned exactly in `pyproject.toml`. That list is the set flo2.io installs.

| Pin | Why |
|---|---|
| `ifcopenshell-mcp[mcp] @ git+https://github.com/sligara7/IfcOpenShell@0ea3edc…#subdirectory=src/ifcmcp` | ifcmcp on the MCP Python SDK 2.x, from the fork. The `[mcp]` extra makes the resolver hold mcp to the range the fork declares (`>=2,<3`). |
| `mcp==2.3.0` | The latest official MCP Python SDK. |
| `ifcopenshell==0.9.0`, `ifcquery==0.9.0`, `ifcedit==0.9.0`, `ifc5d==0.9.0` | ifcmcp leaves these unpinned, and they decide what a model can measure. For example, with ifc5d 0.9.0 the base-quantities rule measures a room modelled as a plain box; with 0.8.5 it skips the room without saying so. |
| `networkx==3.7` | Without it, `ifcedit list` prints a "Note: API not available" line to stdout ahead of its JSON. |
| `pytest==9.1.1` | `ifc_validate`'s `express_rules` runs IfcOpenShell's rule executor, which imports pytest's assertion rewriting (`ifcopenshell/express/rule_executor.py`). Without it that check fails with "No module named '_pytest'". The tests use it too. |
| `jsonschema==4.26.0` (the `test` extra) | The manifest tests. |
| `setuptools==84.0.0` (build) | The build backend, so this package builds the same way every time. |

Some things are deliberately left out:

- **pyvista.** It is 900 MB, and only `ifc_render` needs it. flo2's door does not offer that tool. Without pyvista, the
  tool is still listed, but calling it fails.
- **Transitive dependencies** (numpy, shapely, pydantic and the rest). They are not pinned: each comes in at whatever
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

flo2 is meant to build its ifcmcp image from this repository's `Dockerfile` at a pinned commit, the same way it already
builds flo2-cad's image.

**That change in flo2 is not made yet.** Today flo2 still builds `ops/images/ifcmcp/Dockerfile`, which has
`ifcopenshell-mcp[mcp]==0.8.5` on mcp 1.30.0.

```sh
docker build -t flo2-ifc .
docker run --rm --network none --read-only --cap-drop ALL --user 65534:65534 flo2-ifc --version
docker run --rm -i --network none --read-only --user 65534:65534 \
  --mount type=bind,source="$PWD",target=/design,readonly flo2-ifc     # MCP on stdin/stdout
```

The image is a two-stage build. The fork pin is a git URL, so installing it needs git. The build stage has git; the
final image does not.

## Use it locally

You need [uv](https://docs.astral.sh/uv/). The first run fetches the fork's commit, which takes about a minute. After
that, uv's cache makes it quick.

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

`python -m pytest` runs four test files:

- `tests/test_smoke.py` starts the installed `flo2-ifc` over stdio with the mcp 2.3.0 client. It checks that:
  - the server initializes as `ifc-mcp`, with instructions;
  - it lists ifcmcp's 25 tools and flo2-ifc's two, and the readers take their paging and filter arguments;
  - `ifc_new` and then `ifc_summary` both answer;
  - a synthetic millimetre house (`tests/models.py`), opened with `ifc_load`, reads whole: its units, a page of the
    tree, doors by storey, the roof's top, a room's storey, and a complete validation.
- `tests/test_reading.py` tests each reader on synthetic models, at sizes that pass a reply's budget (400 walls on
  one storey), and with a time limit of zero so a partial answer is tested too. No real person's model is ever a
  fixture.

  If `FLO2_IFC_SERVER` is set to a command line, the test starts that command instead. CI uses this to ask the built
  image the same questions, under flo2's flags.
- `tests/test_manifests.py` validates `plugin.json` and `mcp.json` against the Agent Plugins 1.0.0 schemas copied into
  `tests/schemas/` (`SOURCE.txt` there names the spec commit). It also checks that both formats name the same server,
  command and arguments.
- `tests/test_dependency_currency.py` tests the dependency check against fake registry data. It needs no network.

`.github/workflows/ci.yml` has two jobs:

- the tests, on Python 3.12;
- the image: build it, run `--version` with no network, a read-only root and user 65534:65534, then run the smoke test
  against the image.

## The monthly dependency check

Every MCP server Anthony runs has its dependencies reviewed and updated monthly. For this repository, that review is
`tools/dependency_currency.py`. `.github/workflows/dependencies.yml` runs it at 06:00 UTC on the 1st of each month. It
also runs on demand, and on any pull request that touches `pyproject.toml`, `dependency-holds.toml`,
`.github/workflows/**` or `tools/**`.

It compares four things with their latest releases:

- every pin, with PyPI;
- every Action, with its latest GitHub release;
- the Agent Plugins schema version, with the spec repository's latest tag;
- the fork pin. For this one it reports the fork commit, PyPI's latest `ifcopenshell-mcp`, and whether that release
  still requires `mcp<2`.

What the check does with each result:

| Result | What happens |
|---|---|
| A newer patch or minor release | Reported: take it in the next monthly update. |
| A newer major, or a new 0.x minor (0.9 to 0.10), with no hold in `dependency-holds.toml` | Fails. |
| A hold past its `look_again` date, or a hold with no reason or no date | Fails. |
| A pin that is a range instead of an exact version | Fails. |
| A lookup the check could not make | Fails. |
| PyPI's latest `ifcopenshell-mcp` admits the pinned mcp | Reported as `return_to_pypi`: the fork pin can go back to a release. |

The report is dated. It goes to stdout and to the run's summary, and a JSON copy is kept for 90 days. Run it by hand
with `python3 tools/dependency_currency.py`; set `GITHUB_TOKEN` to raise GitHub's rate limit.

## Moving the fork pin

**To a newer commit on the fork**, for example after the monthly upstream merge or a fix to the port:

1. On the fork, run ifcmcp's own tests on branch `ifcmcp/mcp-python-sdk-2` (`cd src/ifcmcp && pytest tests`), and push.
2. Here, change the commit in the `ifcopenshell-mcp` line of `pyproject.toml`. Use the full 40-character id: the check
   fails on a branch name.
3. Install and test: `uv pip install '.[test]' && python -m pytest`. Build the image and run its `--version`.
4. Open a pull request, then move flo2's pin to the new commit of this repository.

**Back to a PyPI release.** Do this once the dependency check reports `return_to_pypi`:

1. Replace the git line with `"ifcopenshell-mcp[mcp]==X.Y.Z"`.
2. Run the same tests. The smoke test's tool names say whether upstream's server is the same one.
3. Retire the fork branch only after the change has landed and flo2 runs it.

## The design

This repository is designed on flo2.io, in the design **"IfcOpenShell fork"** (id `23d874b1643673ad`). Read it through
the `ifcopenshell-fork` MCP connection. Where this README and the design differ, the design wins.

## Licence

This repository's own files are licensed under the [Apache License 2.0](LICENSE).

ifcmcp and IfcOpenShell, which this repository installs, stay LGPL-3.0-or-later.
