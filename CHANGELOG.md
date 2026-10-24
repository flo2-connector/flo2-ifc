# Changelog

flo2-ifc's versions, newest first. Versions before 0.3.1 are described in README.md, in the sections marked "since".

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
