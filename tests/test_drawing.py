"""Drawing to scale (src/flo2_ifc/drawing.py), on a synthetic model whose sizes are known.

A plan at a storey and cut height, a section along a line, a grid line or
through named elements, and an elevation from a side, each as an SVG in
millimetres of paper and a PNG, at a stated scale written on the sheet, with
highlighted elements. ifcopenshell.draw is not in the path, so its 0.9.0 cells
bug (draw.py:376) cannot reach these. No real model is a fixture here.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

import pytest

from flo2_ifc import drawing as D
from tests import models


@pytest.fixture(scope="module")
def site():
    return models.measured_site()


def svg_of(out) -> ET.Element:
    return ET.parse(out["drawn"]["svg"]).getroot()


def paths(root, cls):
    return [p for p in root.iter("{http://www.w3.org/2000/svg}path") if cls in (p.get("class") or "").split()]


def texts(root):
    return " ".join("".join(t.itertext()) for t in root.iter("{http://www.w3.org/2000/svg}text"))


def test_a_plan_cuts_the_walls_and_shows_what_lies_below(site, tmp_path):
    out = D.draw(site, str(tmp_path / "plan"), view="plan", storey="GROUND")
    root = svg_of(out)
    cut = {p.get("data-ifc") for p in paths(root, "cut")}
    assert cut == {"IfcWall", "IfcBuildingElementProxy"}  # the walls and both boxes reach 1.2 m; the slab does not
    seen = {p.get("data-ifc") for p in paths(root, "seen")}
    assert {"IfcSlab", "IfcFooting"} <= seen
    assert [p.get("data-ifc") for p in paths(root, "room")] == ["IfcSpace"]
    assert "Room 1" in texts(root) and "16.0 m²" in texts(root)
    assert out["elements"]["rooms"] == 1


def test_the_sheet_is_in_paper_millimetres_and_states_its_scale_and_unit(site, tmp_path):
    out = D.draw(site, str(tmp_path / "plan"), view="plan", storey="GROUND", scale='1/4" = 1\'-0"', paper="tabloid")
    root = svg_of(out)
    assert root.get("width") == "431.8mm" and root.get("height") == "279.4mm"
    assert root.get("viewBox") == "0 0 431.8 279.4"
    words = texts(root)
    assert "Scale 1:48 (1/4\" = 1'-0\")" in words and "millimetres" in words
    assert out["sheet"]["ratio"] == 48


def test_a_drawing_is_to_its_scale(site, tmp_path):
    # Wall A is 4 m long: at 1:50 it is 80 mm of paper.
    out = D.draw(site, str(tmp_path / "plan"), view="plan", storey="GROUND", scale="1:50", highlight="IfcWall, Name = \"Wall A\"")
    root = svg_of(out)
    wall = [p for p in paths(root, "cut") if "hl" in p.get("class").split()][0]
    xs = [float(x) for x in re.findall(r"(-?\d+\.\d+) (-?\d+\.\d+)", wall.get("d")) for x in x[:1]]
    assert max(xs) - min(xs) == pytest.approx(80.0, abs=0.05)
    assert out["elements"]["highlighted"] == 1 and out["highlight"][0]["name"] == "Wall A"


def test_the_largest_scale_that_fits_is_chosen_and_a_scale_that_does_not_fit_is_refused(site, tmp_path):
    out = D.draw(site, str(tmp_path / "plan"), view="plan", storey="GROUND", paper="A4")
    assert out["sheet"]["ratio"] in D.CANDIDATES
    smaller = [r for r in D.CANDIDATES if r < out["sheet"]["ratio"]]
    with pytest.raises(ValueError, match="does not fit"):
        D.draw(site, str(tmp_path / "plan"), view="plan", storey="GROUND", paper="A4", scale=f"1:{smaller[-1]}")


def test_a_section_along_a_grid_line_cuts_the_slab_and_the_walls(site, tmp_path):
    out = D.draw(site, str(tmp_path / "sec"), view="section", gridline="A")
    root = svg_of(out)
    cut = {p.get("data-ifc") for p in paths(root, "cut")}
    assert {"IfcWall", "IfcSlab", "IfcFooting"} <= cut
    assert "looking west" in out["what"]
    assert "GROUND" in texts(root) and "FIRST" in texts(root)  # the levels


def test_a_section_through_named_elements_and_its_looking_side(site, tmp_path):
    out = D.draw(site, str(tmp_path / "sec"), view="section", through="IfcSpace", direction="east-west", looking="south")
    assert "looking south" in out["what"] and "Room 1" in out["what"]
    root = svg_of(out)
    assert "Room 1" in texts(root)
    with pytest.raises(ValueError, match="looks along it"):
        D.draw(site, str(tmp_path / "sec"), view="section", through="IfcSpace", direction="east-west", looking="east")


def test_a_section_along_a_line_in_the_models_unit(site, tmp_path):
    out = D.draw(site, str(tmp_path / "sec"), view="section", line="-1000, 1000, 13000, 1000", looking="north")
    assert out["elements"]["cut"] >= 4


def test_an_elevation_sees_everything_and_cuts_nothing(site, tmp_path):
    out = D.draw(site, str(tmp_path / "elev"), view="elevation", side="south")
    assert out["elements"]["cut"] == 0 and out["elements"]["drawn"] == 6
    with pytest.raises(ValueError, match="not a side"):
        D.draw(site, str(tmp_path / "elev"), view="elevation", side="up")


def test_around_draws_only_a_region(site, tmp_path):
    whole = D.draw(site, str(tmp_path / "plan"), view="plan", storey="GROUND", scale="1:50")
    part = D.draw(site, str(tmp_path / "plan2"), view="plan", storey="GROUND", around="IfcBuildingElementProxy, Name = \"Closed box\"", margin=200, scale="1:50")
    assert part["frame"]["max"][0] - part["frame"]["min"][0] < whole["frame"]["max"][0] - whole["frame"]["min"][0]
    assert part["elements"]["drawn"] < whole["elements"]["drawn"]


def test_the_png_is_rendered(site, tmp_path):
    out = D.draw(site, str(tmp_path / "plan"), view="plan", storey="GROUND", png_px=800)
    data = open(out["drawn"]["png"], "rb").read()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert out["drawn"]["png_px"] == [800, round(800 * 279.4 / 431.8)]


def test_names_from_the_model_are_escaped(tmp_path):
    m = models.measured_site()
    m.by_type("IfcSpace")[0].Name = 'R&D <lab> "1"'
    out = D.draw(m, str(tmp_path / "plan"), view="plan", storey="GROUND")
    assert 'R&D <lab> "1"' in texts(svg_of(out))  # parsed back intact: it was escaped


@pytest.mark.parametrize(
    ("text", "ratio"),
    [("1:100", 100), ("96", 96), ('1/8" = 1\'-0"', 96), ('3/16" = 1\'-0"', 64), ("1/4\"=1'", 48), ('1" = 20\'', 240), ('1 1/2" = 1\'-0"', 8)],
)
def test_scales_are_read_as_written(text, ratio):
    assert D.parse_scale(text) == ratio


def test_a_bad_view_or_paper_is_refused(site, tmp_path):
    with pytest.raises(ValueError, match="not a view"):
        D.draw(site, str(tmp_path / "x"), view="isometric")
    with pytest.raises(ValueError, match="not a sheet"):
        D.draw(site, str(tmp_path / "x"), view="plan", storey="GROUND", paper="B5")
    with pytest.raises(ValueError, match="one storey"):
        D.draw(site, str(tmp_path / "x"), view="plan")
