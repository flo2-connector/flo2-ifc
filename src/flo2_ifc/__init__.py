"""flo2-ifc: ifcmcp as flo2.io runs it.

The server is IfcOpenShell's ifcmcp, from the fork at the commit
pyproject.toml pins, with flo2-ifc's readers for a whole building in front of
seven of its tools and two tools added (server.py, reading.py). ``flo2-ifc``
runs it over stdio, and ``uvx --from git+https://github.com/flo2-connector/flo2-ifc
flo2-ifc`` runs it with every dependency at the version pinned in
pyproject.toml. ``ifcmcp``, installed beside it, is the fork's server alone.
"""

from __future__ import annotations

import argparse
import importlib.util
import sys

__version__ = "0.3.1"


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="flo2-ifc",
        description="ifcmcp, IfcOpenShell's MCP server for IFC building models, as flo2.io runs it.",
    )
    parser.add_argument("--version", action="version", version=f"flo2-ifc {__version__} (ifcmcp from the fork, pinned in pyproject.toml)")
    parser.add_argument("--transport", choices=["stdio", "sse", "streamable-http"], default="stdio", help="MCP transport (default: stdio)")
    args = parser.parse_args()
    if importlib.util.find_spec("mcp.server.mcpserver") is None:
        print("error: the 'mcp' package is required to run the server.", file=sys.stderr)
        sys.exit(1)
    from flo2_ifc.server import build_server

    build_server().run(transport=args.transport)
