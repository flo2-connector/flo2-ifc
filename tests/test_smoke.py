"""The installed `flo2-ifc` command is ifcmcp with flo2-ifc's readers, and it answers over stdio.

This starts the server exactly as both plugin manifests do (the `flo2-ifc`
console script, minus uvx's fetch) and talks to it with the client of the
pinned MCP Python SDK, mcp 2.3.0. It needs no network: an IFC model is made
in memory with ifc_new, and a synthetic house (tests/models.py, millimetres)
is written to a folder and opened with ifc_load.

FLO2_IFC_SERVER, when set, is the command line to start instead. CI's image
job sets it to a `docker run` of the built image, confined as flo2 confines
it, so the image answers the same questions as the package. The house is then
written to FLO2_IFC_DESIGN_DIR, which that command mounts read-only where the
server sees it as FLO2_IFC_DESIGN_SEEN_AS (/design, as flo2 mounts a design).
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import sys
from pathlib import Path

import tempfile

import anyio
import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

EXPECTED_TOOLS = {
    # lifecycle
    "ifc_new", "ifc_load", "ifc_save", "ifc_reset",
    # query
    "ifc_summary", "ifc_tree", "ifc_info", "ifc_select", "ifc_relations", "ifc_clash",
    "ifc_contexts", "ifc_materials",
    # edit
    "ifc_list", "ifc_docs", "ifc_edit",
    # extended query and edit
    "ifc_validate", "ifc_schedule", "ifc_cost", "ifc_schema", "ifc_quantify",
    "ifc_shape_list", "ifc_shape_docs", "ifc_shape",
    # drawing
    "ifc_plot", "ifc_render",
    # flo2-ifc's own (src/flo2_ifc/server.py)
    "ifc_extent", "ifc_units", "ifc_footprint", "ifc_volume", "ifc_distance", "ifc_draw",
    # since 0.5.0: IDS and buildingSMART's rules (src/flo2_ifc/checking.py, rules.py)
    "ifc_check_ids", "ifc_write_ids", "ifc_check_rules",
    # since 0.6.0: the building steps, one call an element (src/flo2_ifc/building.py)
    "ifc_add_wall", "ifc_add_slab", "ifc_add_roof", "ifc_add_space",
}

#: The optional arguments flo2-ifc's readers add to ifcmcp's tools.
ADDED_ARGUMENTS = {
    "ifc_tree": {"storey", "element_type", "offset", "limit"},
    "ifc_select": {"query", "offset", "limit"},
    "ifc_relations": {"element_id", "traverse", "element_type", "offset", "limit"},
    "ifc_validate": {"express_rules", "start"},
    "ifc_extent": {"selector", "element_id", "offset", "limit", "datum"},
    "ifc_footprint": {"selector", "ids", "element_id", "offset", "limit"},
    "ifc_volume": {"selector", "ids", "element_id", "offset", "limit"},
    "ifc_check_ids": {"ids_path", "report_path", "start", "offset", "limit"},
    "ifc_write_ids": {"description", "output_path"},
    "ifc_check_rules": {"rule_sets", "start", "offset", "limit"},
    "ifc_add_wall": {"storey", "start", "end", "height", "thickness", "height_at_end", "peak_height", "base_offset", "align", "name", "realizes", "frame"},
    "ifc_add_slab": {"storey", "footprint", "thickness", "top_offset", "kind", "name", "realizes", "frame"},
    "ifc_add_roof": {"storey", "footprint", "kind", "eave_height", "slope", "thickness", "overhang", "name", "realizes", "frame"},
    "ifc_add_space": {"storey", "footprint", "height", "floor_offset", "name", "long_name", "realizes", "frame"},
}

#: The arguments each building step requires.
BUILD_REQUIRED = {
    "ifc_add_wall": {"storey", "start", "end", "height", "thickness"},
    "ifc_add_slab": {"storey", "footprint", "thickness"},
    "ifc_add_roof": {"storey", "footprint", "kind", "eave_height", "slope", "thickness"},
    "ifc_add_space": {"storey", "footprint", "height"},
}

#: An IDS the smoke test writes and checks the house against: its spaces store
#: a NetFloorArea (they pass), its walls say no fire rating (they fail).
SMOKE_IDS = {
    "title": "Smoke test",
    "specifications": [
        {"name": "Every space has a net floor area", "applicability": [{"facet": "entity", "name": "IfcSpace"}],
         "requirements": [{"facet": "property", "property_set": "Qto_SpaceBaseQuantities", "property": "NetFloorArea"}]},
        {"name": "Every wall has a fire rating", "applicability": [{"facet": "entity", "name": "IfcWall"}],
         "requirements": [{"facet": "property", "property_set": "Pset_WallCommon", "property": "FireRating"}]},
    ],
}


def flo2_ifc_command() -> str:
    """The console script installed beside this interpreter, so the test runs
    the package under test even when its venv is not on PATH."""
    beside = Path(sys.executable).parent / "flo2-ifc"
    if beside.exists():
        return str(beside)
    found = shutil.which("flo2-ifc")
    if found is None:
        pytest.fail("the flo2-ifc command is not installed: pip install '.[test]' first")
    return found


def text_of(result) -> str:
    assert not result.is_error, result
    return "".join(block.text for block in result.content if getattr(block, "type", None) == "text")


def write_house() -> str:
    """The synthetic house, written where the server can open it; its path as the server sees it."""
    from tests import models

    folder = os.environ.get("FLO2_IFC_DESIGN_DIR") or tempfile.mkdtemp(prefix="flo2-ifc-smoke-")
    models.building(walls=3).write(str(Path(folder) / "house.ifc"))
    seen_as = os.environ.get("FLO2_IFC_DESIGN_SEEN_AS") or folder
    return f"{seen_as}/house.ifc"


async def round_trip() -> dict:
    override = shlex.split(os.environ.get("FLO2_IFC_SERVER", ""))
    command, args = (override[0], override[1:]) if override else (flo2_ifc_command(), [])
    # The MCP client passes a child only a few variables; the rules' folder is
    # passed on when it is set (CI's package job installs them there).
    from mcp.client.stdio import get_default_environment

    env = get_default_environment() | ({"FLO2_IFC_RULES_DIR": os.environ["FLO2_IFC_RULES_DIR"]} if os.environ.get("FLO2_IFC_RULES_DIR") and not override else {})
    params = StdioServerParameters(command=command, args=args, env=env)
    house = write_house()
    out: dict = {}
    with anyio.fail_after(300):
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            init = await session.initialize()
            out["name"] = init.server_info.name
            out["instructions"] = init.instructions
            tools = await session.list_tools()
            out["tools"] = [t.name for t in tools.tools]
            out["schemas"] = {t.name: t.input_schema for t in tools.tools}
            out["new"] = text_of(await session.call_tool("ifc_new", {"schema": "IFC4"}))
            out["summary"] = text_of(await session.call_tool("ifc_summary", {}))
            validated = await session.call_tool("ifc_validate", {"express_rules": True})
            out["validate"] = (validated.is_error, "".join(getattr(b, "text", "") for b in validated.content))
            missing = await session.call_tool("ifc_info", {"element_id": 999999})
            out["missing"] = (missing.is_error, "".join(getattr(b, "text", "") for b in missing.content))
            out["load"] = text_of(await session.call_tool("ifc_load", {"path": house}))
            out["units"] = json.loads(text_of(await session.call_tool("ifc_units", {})))
            out["tree"] = json.loads(text_of(await session.call_tool("ifc_tree", {"limit": 2})))
            out["doors"] = json.loads(text_of(await session.call_tool("ifc_select", {"query": 'IfcDoor, location = "FIRST"'})))
            out["roof"] = json.loads(text_of(await session.call_tool("ifc_extent", {"selector": "IfcRoof"})))
            spaces = json.loads(text_of(await session.call_tool("ifc_select", {"query": "IfcSpace"})))
            out["room"] = json.loads(text_of(await session.call_tool("ifc_info", {"element_id": spaces["elements"][0]["id"]})))
            out["checked"] = json.loads(text_of(await session.call_tool("ifc_validate", {})))
            out["footprint"] = json.loads(text_of(await session.call_tool("ifc_footprint", {"selector": "IfcWall"})))
            out["volume"] = json.loads(text_of(await session.call_tool("ifc_volume", {"selector": "IfcWall"})))
            # Written where the confined image can write: its /tmp, as flo2's slot gives it an outbox.
            out["draw"] = json.loads(text_of(await session.call_tool("ifc_draw", {"output_path": f"{tempfile.gettempdir()}/flo2-ifc-smoke-plan", "storey": "GROUND"})))
            # An IDS written, then the house checked against it, then buildingSMART's rules.
            ids_at = f"{tempfile.gettempdir()}/flo2-ifc-smoke-req"
            out["write_ids"] = json.loads(text_of(await session.call_tool("ifc_write_ids", {"description": json.dumps(SMOKE_IDS), "output_path": ids_at})))
            out["check_ids"] = json.loads(text_of(await session.call_tool("ifc_check_ids", {"ids_path": f"{ids_at}.ids", "report_path": f"{tempfile.gettempdir()}/flo2-ifc-smoke-report"})))
            bad = await session.call_tool("ifc_write_ids", {"description": json.dumps({"title": "t", "specifications": []}), "output_path": f"{ids_at}-bad"})
            out["bad_ids"] = (bad.is_error, "".join(getattr(b, "text", "") for b in bad.content))
            rules = await session.call_tool("ifc_check_rules", {"rule_sets": "critical,informal-proposition", "limit": 5})
            out["rules"] = (rules.is_error, "".join(getattr(b, "text", "") for b in rules.content))
            # A building step: one call makes a wall on the house's ground floor, in its millimetres.
            out["wall"] = json.loads(text_of(await session.call_tool("ifc_add_wall", {"storey": "GROUND", "start": [0, 5000], "end": [3000, 5000], "height": 2700, "thickness": 100, "realizes": "cmp:smoke-wall"})))
            out["wall_extent"] = json.loads(text_of(await session.call_tool("ifc_extent", {"element_id": out["wall"]["made"]["id"]})))
            # The same in the storey's own frame (since 0.8.0): the house's storeys are not turned, so it lands the same.
            out["wall_storey"] = json.loads(text_of(await session.call_tool("ifc_add_wall", {"storey": "GROUND", "start": [0, 6000], "end": [3000, 6000], "height": 2700, "thickness": 100, "frame": "storey"})))
            out["wall_storey_extent"] = json.loads(text_of(await session.call_tool("ifc_extent", {"element_id": out["wall_storey"]["made"]["id"]})))
            # ifcmcp's 3D picture (since 0.7.0), written where the confined image can write.
            rendered = await session.call_tool("ifc_render", {"view": "iso", "output_path": f"{tempfile.gettempdir()}/flo2-ifc-smoke-render.png"})
            out["render"] = (rendered.is_error, [(b.type, getattr(b, "mime_type", None), getattr(b, "data", None) or getattr(b, "text", "")) for b in rendered.content])
    return out


@pytest.fixture(scope="module")
def served() -> dict:
    return anyio.run(round_trip)


def test_initialize_names_the_server_and_gives_instructions(served):
    assert served["name"] == "ifc-mcp"
    assert served["instructions"] and "ifc_load" in served["instructions"]


def test_list_tools_gives_ifcmcps_25_tools_and_flo2_ifcs_thirteen(served):
    names = served["tools"]
    assert len(names) == 38, names
    assert len(set(names)) == 38, names
    assert set(names) == EXPECTED_TOOLS
    for name in ("ifc_load", "ifc_summary", "ifc_edit", "ifc_plot", "ifc_render"):
        assert name in names


def test_the_readers_take_their_paging_and_filter_arguments(served):
    for tool, wanted in ADDED_ARGUMENTS.items():
        props = set(served["schemas"][tool].get("properties", {}))
        assert props == wanted, (tool, props)
        required = set(served["schemas"][tool].get("required", []))
        own = {"ifc_check_ids": {"ids_path"}, "ifc_write_ids": {"description", "output_path"}, **BUILD_REQUIRED}.get(tool, set())
        assert required <= ({"query", "element_id"} | own) - ({"element_id"} if tool in ("ifc_extent", "ifc_footprint", "ifc_volume") else set()), (tool, required)


def test_a_millimetre_house_reads_whole_through_the_server(served):
    assert "IFC4" in served["load"]
    assert served["units"]["length"]["symbol"] == "mm"
    assert served["tree"]["elements_page"]["listed"] == 2 and "more" in served["tree"]
    assert [e["name"] for e in served["doors"]["elements"]] == ["Front door"]
    assert served["roof"]["overall"]["top"] == 6500.0
    assert served["roof"]["overall_metres"]["top"] == 6.5
    assert served["room"]["storey"]["name"] in ("GROUND", "FIRST")
    assert "millimetres" in served["room"]["units"]
    assert served["checked"]["complete"] is True and served["checked"]["valid"] is True


def test_ifc_new_then_ifc_summary_answer(served):
    new = json.loads(served["new"])
    assert new.get("schema") == "IFC4", new
    summary = json.loads(served["summary"])
    assert summary.get("schema") == "IFC4", summary


def test_a_failing_tool_says_why(served):
    # mcp 2.x hides the text of any exception but ToolError; flo2-ifc's server raises ToolError for every tool.
    is_error, text = served["missing"]
    assert is_error
    assert "#999999" in text, text


def test_express_rule_validation_runs(served):
    # IfcOpenShell's rule executor imports pytest; without it this fails with
    # "No module named '_pytest'".
    is_error, text = served["validate"]
    assert not is_error, text
    assert "_pytest" not in text, text


def test_the_house_is_measured_and_drawn_through_the_server(served):
    # Three walls, 4 x 0.2 m each, side by side 1 m apart: 2.4 m2 of footprint, 7.2 m3 of solid.
    assert served["footprint"]["footprint"]["square_metres"] == 2.4
    assert served["footprint"]["cite"]["model_file"] == "house.ifc"
    assert len(served["footprint"]["cite"]["sha256"]) == 64
    assert len(served["footprint"]["cite"]["global_ids"]) == 3
    assert served["volume"]["total"]["cubic_metres"] == 7.2 and "not_closed" not in served["volume"]
    drawn = served["draw"]
    assert drawn["view"] == "plan" and drawn["drawn"]["png_bytes"] > 1000, drawn
    assert drawn["sheet"]["scale"].startswith("1:") and drawn["elements"]["cut"] == 3, drawn


def test_an_ids_is_written_and_the_house_checked_against_it_through_the_server(served):
    written = served["write_ids"]
    assert written["valid"] is True and written["written"].endswith("flo2-ifc-smoke-req.ids") and len(written["sha256"]) == 64
    checked = served["check_ids"]
    assert checked["complete"] is True and checked["passed"] is False
    assert [s["status"] for s in checked["specifications"]] == ["passed", "failed"]
    assert checked["specifications"][1]["failed_elements"] == 3 and len(checked["specifications"][1]["failing"][0]["global_id"]) == 22
    assert checked["model"]["file"] == "house.ifc" and checked["ids"]["sha256"] == written["sha256"]
    assert checked["reports"]["html"].endswith(".html") and checked["reports"]["json"].endswith(".json")
    is_error, text = served["bad_ids"]
    assert is_error and "at least one specification" in text, text


def test_buildingsmarts_rules_run_through_the_server(served):
    is_error, text = served["rules"]
    if is_error and "not installed" in text and not os.environ.get("CI"):
        pytest.skip("buildingSMART's rules are not installed here (tools/ifc_rules.py installs them)")
    assert not is_error, text
    said = json.loads(text)
    assert said["checked"]["to"] > 0 and said["model"]["file"] == "house.ifc"
    assert "critical" in said["rule_sets"]["ran"] and "IFC101" in said["rules_passed"]
    if said["complete"]:
        assert any(r["code"] == "GEM002" for r in said["rules_with_issues"]), said  # the house's rooms have no shape


def test_a_wall_is_built_in_one_call_through_the_server(served):
    wall = served["wall"]
    assert wall["made"]["class"] == "IfcWall" and wall["storey"]["name"] == "GROUND"
    assert wall["realizes"] == {"identification": "cmp:smoke-wall", "classification": "flo2 design"}
    box = served["wall_extent"]["overall"]
    assert box["min"] == pytest.approx([0, 4950, 0], abs=0.01) and box["max"] == pytest.approx([3000, 5050, 2700], abs=0.01)
    # The reply names the frame its points were read in: "world" unless the call asks for "storey".
    assert wall["frame"]["used"] == "world" and served["wall_storey"]["frame"]["used"] == "storey"
    box = served["wall_storey_extent"]["overall"]
    assert box["min"] == pytest.approx([0, 5950, 0], abs=0.01) and box["max"] == pytest.approx([3000, 6050, 2700], abs=0.01)


def test_the_house_is_rendered_in_3d_through_the_server(served):
    # ifc_render needs pyvista and a renderer that draws with no display.
    import base64
    import io

    from PIL import Image

    is_error, blocks = served["render"]
    assert not is_error, blocks
    images = [data for kind, mime, data in blocks if kind == "image" and mime == "image/png"]
    assert len(images) == 1, [(k, m, str(d)[:200]) for k, m, d in blocks]
    img = Image.open(io.BytesIO(base64.b64decode(images[0]))).convert("RGB")
    assert img.size == (1280, 960)
    assert sum(1 for p in img.getdata() if p != (255, 255, 255)) > 20_000


def test_version_is_one_line_naming_what_runs():
    # flo2's broker reads the first line of the image's --version.
    import subprocess

    out = subprocess.run([flo2_ifc_command(), "--version"], capture_output=True, text=True, check=True,
                         env={**os.environ, "COLUMNS": "20"}).stdout
    assert out.count("\n") == 1, out
    from flo2_ifc import __version__

    assert out.startswith(f"flo2-ifc {__version__} (ifcopenshell-mcp "), out
    assert ", mcp 2." in out, out
