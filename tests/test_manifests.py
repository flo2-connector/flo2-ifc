"""The plugin, in both formats, is valid and names one server the same way.

- Agent Plugins: plugin.json and mcp.json at the root, validated against the
  1.0.0 schemas vendored in tests/schemas/ (see tests/schemas/SOURCE.txt).
- Claude Code: .claude-plugin/plugin.json and .mcp.json, which carry no
  `type` and no `$schema`, the way flo2-cad's do.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import jsonschema
import pytest

REPO = Path(__file__).resolve().parent.parent
SCHEMAS = Path(__file__).resolve().parent / "schemas"
SERVER = "ifc"
COMMAND = "uvx"
ARGS = ["--from", "git+https://github.com/sligara7/flo2-ifc", "flo2-ifc"]


def load(rel: str) -> dict:
    return json.loads((REPO / rel).read_text())


@pytest.mark.parametrize("manifest, schema", [("plugin.json", "plugin.schema.json"), ("mcp.json", "mcp.schema.json")])
def test_agent_plugins_manifest_is_valid(manifest, schema):
    schema_doc = json.loads((SCHEMAS / schema).read_text())
    doc = load(manifest)
    assert doc["$schema"] == schema_doc["$id"], "the manifest declares the schema version vendored here"
    jsonschema.Draft202012Validator.check_schema(schema_doc)
    jsonschema.Draft202012Validator(schema_doc).validate(doc)


def test_both_formats_name_the_same_server_command_and_args():
    agent = load("mcp.json")["mcpServers"]
    claude = load(".mcp.json")["mcpServers"]
    assert list(agent) == [SERVER]
    assert list(claude) == [SERVER]
    assert agent[SERVER]["type"] == "stdio"
    for servers in (agent, claude):
        assert servers[SERVER]["command"] == COMMAND
        assert servers[SERVER]["args"] == ARGS


def test_claude_format_carries_no_type_or_schema():
    claude = load(".mcp.json")
    assert "$schema" not in claude
    assert "type" not in claude["mcpServers"][SERVER]
    assert "$schema" not in load(".claude-plugin/plugin.json")


def test_both_plugin_manifests_say_the_same_thing():
    agent = load("plugin.json")
    claude = load(".claude-plugin/plugin.json")
    agent.pop("$schema")
    assert agent == claude
    project = tomllib.loads((REPO / "pyproject.toml").read_text())["project"]
    assert agent["name"] == project["name"]
    assert agent["version"] == project["version"]
    assert agent["author"] == {"name": "Anthony Sligar"}
    assert agent["homepage"] == agent["repository"] == "https://github.com/sligara7/flo2-ifc"
    assert agent["license"] == project["license"] == "Apache-2.0"
    assert (REPO / "LICENSE").read_text().lstrip().startswith("Apache License\n")


def test_the_uvx_entry_point_is_the_console_script():
    scripts = tomllib.loads((REPO / "pyproject.toml").read_text())["project"]["scripts"]
    assert scripts[ARGS[-1]] == "flo2_ifc:main"
