"""ifc_render draws a building model as a 3D picture, with no display (since 0.7.0).

ifcmcp's ifc_render (ifcquery's render module) draws the model's shapes with
pyvista, VTK's Python layer, and saves what its off-screen window shows as a
PNG. Until 0.6.0 the image left pyvista out, so the tool was listed and failed.
flo2's door offers it now, through its own step (flo2's design 1a97fc1f1de4d5d2,
``dec:a-chat-renders-a-building-model-in-3d``), so flo2-ifc carries the renderer
and these tests hold that it draws:

- with no display at all (no X server, no DISPLAY), as in flo2's container;
- the synthetic house (tests/models.py), never a person's model, at the size
  ifcquery draws (1280 x 960), with its shapes in colour on a white ground;
- from each named side, and with the elements it is given picked out.

Each render runs in a child Python with DISPLAY and WAYLAND_DISPLAY removed,
so a laptop's own display cannot make it pass.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests import models

#: What ifcquery 0.9.0's renderer draws: a 1280 x 960 window.
SIZE = (1280, 960)

CHILD = r"""
import json, sys
import ifcopenshell
from ifcquery import render
model = ifcopenshell.open(sys.argv[1])
ids = json.loads(sys.argv[4]) or None
png = render.render(model, selector=sys.argv[3] or None, element_ids=ids, view=sys.argv[2])
open(sys.argv[5], "wb").write(png)
"""


def without_a_display() -> dict:
    env = {k: v for k, v in os.environ.items() if k not in ("DISPLAY", "WAYLAND_DISPLAY")}
    return env


def render(model_path: Path, out: Path, view: str = "iso", selector: str = "", element_ids=None) -> bytes:
    done = subprocess.run(
        [sys.executable, "-c", CHILD, str(model_path), view, selector, json.dumps(element_ids or []), str(out)],
        env=without_a_display(), capture_output=True, text=True, timeout=300,
    )
    assert done.returncode == 0, done.stderr[-3000:]
    return out.read_bytes()


def picture(png: bytes):
    from PIL import Image

    assert png[:8] == b"\x89PNG\r\n\x1a\n", png[:16]
    return Image.open(io.BytesIO(png)).convert("RGB")


def drawn_pixels(img) -> int:
    """Pixels that are not the white ground."""
    return sum(1 for p in img.getdata() if p != (255, 255, 255))


@pytest.fixture(scope="module")
def house(tmp_path_factory) -> Path:
    at = tmp_path_factory.mktemp("render") / "house.ifc"
    models.building(walls=3).write(str(at))
    return at


def test_the_renderer_is_installed():
    from ifcquery import render as render_mod

    assert render_mod._HAS_PYVISTA, "pyvista is not installed, so ifc_render fails"


def test_the_house_is_drawn_in_3d_with_no_display(house, tmp_path):
    img = picture(render(house, tmp_path / "iso.png"))
    assert img.size == SIZE
    assert img.getpixel((0, 0)) == (255, 255, 255), "a white ground"
    drawn = drawn_pixels(img)
    assert drawn > 0.02 * SIZE[0] * SIZE[1], f"the house covers only {drawn} pixels"
    assert len(set(img.getdata())) > 8, "shaded faces, not a flat silhouette"


def test_each_side_is_a_different_picture(house, tmp_path):
    seen = {view: render(house, tmp_path / f"{view}.png", view=view) for view in ("iso", "top", "south", "east")}
    assert len(set(seen.values())) == 4
    for view, png in seen.items():
        assert drawn_pixels(picture(png)) > 1000, view


def test_a_selector_draws_only_what_it_names_and_named_elements_are_picked_out(house, tmp_path):
    import ifcopenshell

    # The camera fits what is drawn, so a part fills the picture as the whole does: compare the pictures.
    whole = render(house, tmp_path / "whole.png")
    walls = render(house, tmp_path / "walls.png", selector="IfcWall")
    assert drawn_pixels(picture(walls)) > 1000 and walls != whole
    wall = ifcopenshell.open(str(house)).by_type("IfcWall")[0].id()
    picked = picture(render(house, tmp_path / "picked.png", element_ids=[wall]))
    plain = picture(render(house, tmp_path / "plain.png"))
    assert picked.tobytes() != plain.tobytes()
