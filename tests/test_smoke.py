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
    params = StdioServerParameters(command=command, args=args)
    house = write_house()
    out: dict = {}
    with anyio.fail_after(120):
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
    return out


@pytest.fixture(scope="module")
def served() -> dict:
    return anyio.run(round_trip)


def test_initialize_names_the_server_and_gives_instructions(served):
    assert served["name"] == "ifc-mcp"
    assert served["instructions"] and "ifc_load" in served["instructions"]


def test_list_tools_gives_ifcmcps_25_tools_and_flo2_ifcs_six(served):
    names = served["tools"]
    assert len(names) == 31, names
    assert len(set(names)) == 31, names
    assert set(names) == EXPECTED_TOOLS
    for name in ("ifc_load", "ifc_summary", "ifc_edit", "ifc_plot", "ifc_render"):
        assert name in names


def test_the_readers_take_their_paging_and_filter_arguments(served):
    for tool, wanted in ADDED_ARGUMENTS.items():
        props = set(served["schemas"][tool].get("properties", {}))
        assert props == wanted, (tool, props)
        required = set(served["schemas"][tool].get("required", []))
        assert required <= {"query", "element_id"} - ({"element_id"} if tool in ("ifc_extent", "ifc_footprint", "ifc_volume") else set()), (tool, required)


def test_a_millimetre_house_reads_whole_through_the_server(served):
    assert "165" not in served["load"] and "IFC4" in served["load"]
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
    # mcp 2.x hides the text of any exception but ToolError; the fork's port raises ToolError.
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
