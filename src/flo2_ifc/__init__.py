"""flo2-ifc: ifcmcp as flo2.io runs it.

The tools are IfcOpenShell's ifcmcp, from the ifcopenshell-mcp release
pyproject.toml pins, served by flo2-ifc's own server on the MCP Python SDK 2.x,
with flo2-ifc's readers for a whole building in front of seven of them and nine
tools added (server.py, reading.py, geometry.py, drawing.py; since 0.5.0 the
IDS check and writer, checking.py, and buildingSMART's rules, rules.py). ``flo2-ifc`` runs
it over stdio, and ``uvx --from git+https://github.com/flo2-connector/flo2-ifc
flo2-ifc`` runs it with every dependency at the version pinned in
pyproject.toml. The ``ifcmcp`` command the release also installs is upstream's
own server, which needs mcp<2, so it does not start here.
"""

from __future__ import annotations

import argparse
import importlib.util
import sys

__version__ = "0.8.0"


def _installed(name: str) -> str:
    """`name` and the version installed beside flo2-ifc, as --version says it:
    what actually runs, kept short so the line is one line."""
    from importlib.metadata import PackageNotFoundError, version

    try:
        return f"{name} {version(name)}"
    except PackageNotFoundError:
        return f"{name} not installed"


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="flo2-ifc",
        description="ifcmcp, IfcOpenShell's MCP server for IFC building models, as flo2.io runs it.",
        # Text as written: --version stays one line however narrow the terminal,
        # because flo2's broker reads its first line.
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"flo2-ifc {__version__} ({_installed('ifcopenshell-mcp')}, {_installed('mcp')})")
    parser.add_argument("--transport", choices=["stdio", "sse", "streamable-http"], default="stdio", help="MCP transport (default: stdio)")
    args = parser.parse_args()
    if importlib.util.find_spec("mcp.server.mcpserver") is None:
        print("error: the 'mcp' package is required to run the server.", file=sys.stderr)
        sys.exit(1)
    from flo2_ifc.server import build_server

    build_server().run(transport=args.transport)
