#!/usr/bin/env python3
"""Measure the image's peak memory while it renders, for the broker's cap.

    python3 tools/measure.py --model path/to/model.ifc [--model ...] [--image flo2-ifc:local] [--cap 1g]

For each model, starts ONE container of the image under flo2-tool-sandbox's
flags (no network, a read-only root, a 64 MB /tmp, user 65534, no
capabilities, one CPU, 64 processes, the memory cap with no swap, the model's
folder read-only at /design), drives a session over stdio that opens the model
and renders it as flo2's door does (ifc_render into a file, as the door's step
has it write into its outbox; here /tmp): the whole model from each side, then
the built elements only, then one element picked out. Then it reads the
container's cgroup memory.peak and memory.events before closing it, and the
highest ANONYMOUS memory (memory.stat's anon, sampled every 0.2 s: the peak
also counts the page cache, the image's libraries read in, which the kernel
reclaims under the cap). Prints one
JSON line per model. Needs docker, the image, the mcp client (pip install
'.[test]'), and a cgroup v2 host that exposes memory.peak.

ifcquery's renderer draws one size of picture, 1280 x 960, so the size that
moves the peak is the model's: its shapes, and the triangles they make.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import threading
import time
import uuid
from pathlib import Path

import anyio
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

#: The renders one session makes: (arguments, what it is).
RENDERS = [
    ({"view": "iso"}, "the whole model, iso"),
    ({"view": "top"}, "the whole model, top"),
    ({"view": "south"}, "the whole model, south"),
    ({"view": "iso", "selector": "IfcElement"}, "the built elements only"),
]


async def drive(command: list[str], name: str, model: str) -> dict:
    params = StdioServerParameters(command=command[0], args=command[1:])
    out: dict = {"renders": []}
    async with stdio_client(params) as (read, write), ClientSession(read, write) as s:
        await s.initialize()
        cid = subprocess.run(["docker", "ps", "-q", "--no-trunc", "--filter", f"name={name}"], capture_output=True, text=True).stdout.strip()
        scope = Path(f"/sys/fs/cgroup/system.slice/docker-{cid}.scope")
        anon = {"max": 0}
        done = threading.Event()

        def sample() -> None:
            while not done.is_set():
                try:
                    for line in (scope / "memory.stat").read_text().splitlines():
                        if line.startswith("anon "):
                            anon["max"] = max(anon["max"], int(line.split()[1]))
                except OSError:
                    pass
                done.wait(0.2)

        threading.Thread(target=sample, daemon=True).start()
        t = time.perf_counter()
        r = await s.call_tool("ifc_load", {"path": f"/design/{model}"})
        assert not r.is_error, r.content
        out["load_s"] = round(time.perf_counter() - t, 2)
        wall = await s.call_tool("ifc_select", {"query": "IfcWall", "limit": 1})
        first = json.loads(wall.content[0].text)["elements"][:1]
        renders = RENDERS + ([({"view": "iso", "element_ids": [first[0]["id"]]}, "one wall picked out")] if first else [])
        for i, (args, what) in enumerate(renders):
            t = time.perf_counter()
            r = await s.call_tool("ifc_render", {**args, "output_path": f"/tmp/render-{i}.png"})
            png = [b for b in r.content if b.type == "image"]
            out["renders"].append({"what": what, "s": round(time.perf_counter() - t, 2), "ok": not r.is_error and len(png) == 1,
                                   **({} if not r.is_error else {"error": "".join(getattr(b, "text", "") for b in r.content)[:300]})})
        done.set()
        out["anon_peak_mib"] = round(anon["max"] / 1048576, 1)
        out["peak_mib"] = round(int((scope / "memory.peak").read_text()) / 1048576, 1)
        out["events"] = dict(line.split() for line in (scope / "memory.events").read_text().splitlines())
        try:
            out["pids_peak"] = int((scope / "pids.peak").read_text())
        except OSError:
            pass
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--image", default="flo2-ifc:local")
    ap.add_argument("--cap", default="1g")
    ap.add_argument("--model", action="append", required=True)
    a = ap.parse_args()
    for model in a.model:
        path = Path(model).resolve()
        name = f"flo2-ifc-measure-{uuid.uuid4().hex[:8]}"
        command = [
            "docker", "run", "--rm", "-i", "--name", name, "--network", "none", "--read-only",
            "--tmpfs", "/tmp:rw,nosuid,nodev,size=64m", "--memory", a.cap, "--memory-swap", a.cap,
            "--cpus", "1", "--pids-limit", "64", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--user", "65534:65534", "--mount", f"type=bind,source={path.parent},target=/design,readonly", a.image,
        ]
        started = time.perf_counter()
        result = anyio.run(drive, command, name, path.name)
        print(json.dumps({"image": a.image, "cap": a.cap, "model": path.name, "bytes": path.stat().st_size,
                          "seconds": round(time.perf_counter() - started, 1), **result}), flush=True)


if __name__ == "__main__":
    main()
