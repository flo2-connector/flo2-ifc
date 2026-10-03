"""The installed `flo2-ifc` command is ifcmcp, and it answers over stdio.

This starts the server exactly as both plugin manifests do (the `flo2-ifc`
console script, minus uvx's fetch) and talks to it with the client of the
pinned MCP Python SDK, mcp 2.3.0. It needs no network: an IFC model is made
in memory with ifc_new.

FLO2_IFC_SERVER, when set, is the command line to start instead. CI's image
job sets it to a `docker run` of the built image, confined as flo2 confines
it, so the image answers the same questions as the package.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import sys
from pathlib import Path

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


async def round_trip() -> dict:
    override = shlex.split(os.environ.get("FLO2_IFC_SERVER", ""))
    command, args = (override[0], override[1:]) if override else (flo2_ifc_command(), [])
    params = StdioServerParameters(command=command, args=args)
    out: dict = {}
    with anyio.fail_after(120):
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            init = await session.initialize()
            out["name"] = init.server_info.name
            out["instructions"] = init.instructions
            tools = await session.list_tools()
            out["tools"] = [t.name for t in tools.tools]
            out["new"] = text_of(await session.call_tool("ifc_new", {"schema": "IFC4"}))
            out["summary"] = text_of(await session.call_tool("ifc_summary", {}))
            missing = await session.call_tool("ifc_info", {"element_id": 999999})
            out["missing"] = (missing.is_error, "".join(getattr(b, "text", "") for b in missing.content))
    return out


@pytest.fixture(scope="module")
def served() -> dict:
    return anyio.run(round_trip)


def test_initialize_names_the_server_and_gives_instructions(served):
    assert served["name"] == "ifc-mcp"
    assert served["instructions"] and "ifc_load" in served["instructions"]


def test_list_tools_gives_exactly_the_25_tools(served):
    names = served["tools"]
    assert len(names) == 25, names
    assert len(set(names)) == 25, names
    assert set(names) == EXPECTED_TOOLS
    for name in ("ifc_load", "ifc_summary", "ifc_edit", "ifc_plot", "ifc_render"):
        assert name in names


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
