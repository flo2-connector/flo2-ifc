"""A plan set (src/flo2_ifc/plan_set.py, since 0.13.0): numbered sheets drawn to scale from the model, bound into ONE PDF.

flo2's design 1a97fc1f1de4d5d2, req:a-chat-pulls-a-pdf-plan-set-of-a-designs-building-model: a person asks for a plan
set and a chat makes one from the model as it stands, a plan of every storey, the elevations and sections through the
building, each to scale on a sheet with a sheet number and title, and a cover sheet listing them, kept as one PDF.

What must hold:
- with no sheets given, the set is a cover sheet (the project and building as the model names them, the model file,
  the date drawn, and an index of every sheet), a plan of every storey in elevation order, the four elevations, and
  two sections through the middle of the building, north-south and east-west, numbered A-001, A-101.., A-201..,
  A-301..; plans, elevations and sections each share one scale, the largest that fits all of them;
- or the caller gives the sheets, each ifc_draw's own arguments with its number and title;
- one paper for the whole set; each sheet's number and title are written on it;
- <output_path>.pdf (vector, one page per sheet) and each sheet's SVG are written, and the reply says what was
  written: pages, the sheets, and any sheet not drawn and why;
- a set that runs out of time says `partial` with which sheets are done, writes nothing, and the same call goes on;
  an edit in between starts it again;
- a bad sheet is refused before anything is drawn.

Only synthetic models (tests/models.py), and the public samples when FLO2_IFC_SAMPLES names their folder.
"""

from __future__ import annotations

import datetime
import json
import os
import re
import xml.etree.ElementTree as ET
import zlib
from pathlib import Path

import ifcopenshell.api
import numpy as np
import pytest

from flo2_ifc import drawing as D
from flo2_ifc import geometry
from flo2_ifc import plan_set as P
from tests import models

SVG = "{http://www.w3.org/2000/svg}"
DEFAULT_NUMBERS = ["A-001", "A-101", "A-102", "A-201", "A-202", "A-203", "A-204", "A-301", "A-302"]
DEFAULT_TITLES = ["Cover sheet", "GROUND plan", "FIRST plan", "North elevation", "East elevation", "South elevation", "West elevation", "North-south section", "East-west section"]
MM_PER_PT = 25.4 / 72


def texts(svg_path) -> str:
    root = ET.parse(svg_path).getroot()
    return " ".join("".join(t.itertext()) for t in root.iter(f"{SVG}text"))


def pdf_objects(path) -> bytes:
    """The PDF's bytes with every Flate stream inflated beside them, so its object streams can be read."""
    raw = Path(path).read_bytes()
    parts = [raw]
    for m in re.finditer(rb"stream\r?\n", raw):
        end = raw.find(b"endstream", m.end())
        try:
            parts.append(zlib.decompress(raw[m.end() : end]))
        except zlib.error:
            pass
    return b"\n".join(parts)


def pages_of(path) -> list[tuple[float, float]]:
    """Each page's size in millimetres, from its MediaBox, in page order."""
    body = pdf_objects(path)
    boxes = [tuple(float(x) for x in m.group(1).split()) for m in re.finditer(rb"/Type\s*/Page\b(?!s)[^>]*?/MediaBox\s*\[\s*([^\]]+)\]", body, re.S)]
    if not boxes:  # the MediaBox before /Type in the dictionary
        boxes = [tuple(float(x) for x in m.group(1).split()) for m in re.finditer(rb"/MediaBox\s*\[\s*([^\]]+)\][^>]*?/Type\s*/Page\b(?!s)", body, re.S)]
    return [((b[2] - b[0]) * MM_PER_PT, (b[3] - b[1]) * MM_PER_PT) for b in boxes]


def page_count(path) -> int:
    body = pdf_objects(path)
    return len(re.findall(rb"/Type\s*/Page\b(?!s)", body))


@pytest.fixture()
def house(tmp_path):
    """The synthetic house (tests/models.py): GROUND with three walls, FIRST with a door and a roof, millimetres,
    written to a file so the set names it and its sha256."""
    m = models.building(walls=3)
    path = tmp_path / "house.ifc"
    m.write(str(path))
    return m, str(path)


@pytest.fixture(scope="module")
def default_set(tmp_path_factory):
    m = models.building(walls=3)
    folder = tmp_path_factory.mktemp("set")
    path = folder / "house.ifc"
    m.write(str(path))
    out = P.plan_set(m, str(folder / "house set"), model_path=str(path))
    return m, str(path), folder, out


# ---------------------------------------------------------------- the default set


def test_the_default_set_is_a_cover_a_plan_of_every_storey_four_elevations_and_two_sections(default_set):
    _, _, folder, out = default_set
    assert out["complete"] is True, out
    assert [s["number"] for s in out["sheets"]] == DEFAULT_NUMBERS
    assert [s["title"] for s in out["sheets"]] == DEFAULT_TITLES
    assert [s["page"] for s in out["sheets"]] == list(range(1, 10))
    assert [s.get("view") for s in out["sheets"][1:]] == ["plan"] * 2 + ["elevation"] * 4 + ["section"] * 2
    assert "not_drawn" not in out
    assert out["set"]["pages"] == 9


def test_the_pdf_is_one_vector_page_per_sheet_and_each_sheets_svg_is_written(default_set):
    _, _, folder, out = default_set
    pdf = out["written"]["pdf"]
    assert pdf == str(folder / "house set.pdf")
    assert Path(pdf).read_bytes()[:5] == b"%PDF-"
    assert out["written"]["pdf_bytes"] == Path(pdf).stat().st_size and len(out["written"]["sha256"]) == 64
    assert page_count(pdf) == 9
    body = pdf_objects(pdf)
    assert b"/Font" in body, "the words are text, not pictures"
    assert b"/Subtype /Image" not in body and b"/Subtype/Image" not in body, "nothing is a picture: vector only"
    assert out["written"]["svgs"] == [str(folder / f"house set-{n}.svg") for n in DEFAULT_NUMBERS]
    for sheet, svg in zip(out["sheets"], out["written"]["svgs"]):
        assert sheet["svg"] == svg and Path(svg).is_file()


def test_each_sheet_carries_its_number_and_title(default_set):
    _, _, _, out = default_set
    for sheet in out["sheets"]:
        words = texts(sheet["svg"])
        assert sheet["number"] in words and sheet["title"] in words, (sheet["number"], words[:300])
    plan = texts(out["sheets"][1]["svg"])
    assert "Plan of GROUND" in plan, "what a sheet shows is still written under its title"


def test_one_paper_for_the_whole_set(house, tmp_path):
    m, path = house
    out = P.plan_set(m, str(tmp_path / "a3"), model_path=path, paper="A3")
    assert out["set"]["paper"] == "A3"
    for w, h in pages_of(out["written"]["pdf"]):
        assert sorted((round(w), round(h))) == [297, 420], (w, h)
    for sheet in out["sheets"]:
        root = ET.parse(sheet["svg"]).getroot()
        assert sorted((root.get("width"), root.get("height"))) == ["297mm", "420mm"]
    tabloid = P.plan_set(m, str(tmp_path / "tabloid"), model_path=path)
    assert tabloid["set"]["paper"].startswith("Tabloid")
    for w, h in pages_of(tabloid["written"]["pdf"]):
        assert sorted((round(w, 1), round(h, 1))) == [279.4, 431.8], (w, h)


def test_the_cover_names_the_project_and_building_as_the_model_states_them_the_file_the_date_and_every_sheet(default_set):
    m, path, _, out = default_set
    cover = texts(out["sheets"][0]["svg"])
    assert "Test house" in cover and "House" in cover  # IfcProject.Name and IfcBuilding.Name, as the model has them
    assert out["project"]["name"] == "Test house" and out["buildings"][0]["name"] == "House"
    assert "house.ifc" in cover and geometry.file_sha256(path)[:12] in cover
    today = datetime.date.today().isoformat()
    assert today in cover and out["set"]["drawn_on"] == today
    for number, title in zip(DEFAULT_NUMBERS, DEFAULT_TITLES):
        assert number in cover and title in cover, (number, title)
    assert out["source"] == {"model_file": "house.ifc", "sha256": geometry.file_sha256(path)}


def test_plans_elevations_and_sections_each_share_the_largest_scale_that_fits_all_of_them(default_set, tmp_path):
    m, path, _, out = default_set
    by_view: dict[str, set[int]] = {}
    for sheet in out["sheets"][1:]:
        by_view.setdefault(sheet["view"], set()).add(sheet["ratio"])
    assert all(len(r) == 1 for r in by_view.values()), by_view
    # Each kind's scale is the smallest of the scales each of its sheets would be drawn at alone.
    for kind in ("plan", "elevation", "section"):
        alone = [D.draw(m, str(tmp_path / f"alone-{s['number']}"), model_path=path, **s["arguments"])["sheet"]["ratio"] for s in out["sheets"] if s.get("view") == kind]
        assert by_view[kind] == {max(alone)}, (kind, alone)


def test_storeys_are_planned_in_elevation_order_and_a_storey_with_nothing_drawn_has_no_plan(tmp_path):
    m = models.building(walls=3)
    run = ifcopenshell.api.run
    body = next(c for c in m.by_type("IfcGeometricRepresentationSubContext") if c.ContextIdentifier == "Body")
    bldg = m.by_type("IfcBuilding")[0]
    # Made last, lowest: a basement with a wall, and an empty roof level above everything.
    basement = run("root.create_entity", m, ifc_class="IfcBuildingStorey", name="BASEMENT")
    attic = run("root.create_entity", m, ifc_class="IfcBuildingStorey", name="ROOF LEVEL")
    basement.Elevation, attic.Elevation = -3000.0, 9000.0
    run("aggregate.assign_object", m, relating_object=bldg, products=[basement, attic])
    models._place(m, basement, z=-3.0)
    models._place(m, attic, z=9.0)
    wall = run("root.create_entity", m, ifc_class="IfcWall", name="Basement wall")
    run("geometry.assign_representation", m, product=wall, representation=run("geometry.add_wall_representation", m, context=body, length=4.0, height=2.8, thickness=0.3))
    models._place(m, wall, z=-3.0)
    run("spatial.assign_container", m, relating_structure=basement, products=[wall])
    out = P.plan_set(m, str(tmp_path / "set"))
    plans = [s for s in out["sheets"] if s.get("view") == "plan"]
    assert [(s["number"], s["title"]) for s in plans] == [("A-101", "BASEMENT plan"), ("A-102", "GROUND plan"), ("A-103", "FIRST plan")]
    assert any("ROOF LEVEL" in n and "no plan" in n for n in out["notes"]), out.get("notes")


def test_the_sections_cut_through_the_middle_of_the_building(default_set):
    _, _, _, out = default_set
    ns, ew = (s for s in out["sheets"] if s.get("view") == "section")
    # The house's walls run along x from 0 to 4 m, side by side at y 0, 1 and 2 m (0.2 m thick): its middle is
    # x = 2 m; the roof slab and the door reach no farther. Lines are in the model's millimetres.
    x1, y1, x2, y2 = (float(v) for v in ns["arguments"]["line"].split(","))
    assert x1 == x2 == pytest.approx(2000, abs=1) and y1 < 0 < 2200 < y2
    x1, y1, x2, y2 = (float(v) for v in ew["arguments"]["line"].split(","))
    assert y1 == y2 and x1 < 0 < 4000 < x2
    assert "looking west" in ns["what"] and "looking north" in ew["what"]


# ---------------------------------------------------------------- sheets the caller gives


def test_the_caller_gives_the_sheets(house, tmp_path):
    m, path = house
    sheets = [
        {"number": "A-101", "title": "Ground floor", "view": "plan", "storey": "GROUND", "scale": "1:50"},
        {"number": "A-301", "title": "Through the walls", "view": "section", "through": "IfcWall", "direction": "east-west"},
    ]
    out = P.plan_set(m, str(tmp_path / "mine"), model_path=path, sheets=sheets)
    assert out["complete"] is True
    assert [(s["number"], s["title"]) for s in out["sheets"]] == [("A-001", "Cover sheet"), ("A-101", "Ground floor"), ("A-301", "Through the walls")]
    assert out["sheets"][1]["scale"].startswith("1:50") and page_count(out["written"]["pdf"]) == 3
    cover = texts(out["sheets"][0]["svg"])
    assert "Ground floor" in cover and "Through the walls" in cover and "North elevation" not in cover
    assert "Ground floor" in texts(out["sheets"][1]["svg"])


def test_a_sheet_without_a_number_or_title_is_numbered_by_its_kind_and_titled_by_what_it_shows(house, tmp_path):
    m, path = house
    out = P.plan_set(m, str(tmp_path / "mine"), model_path=path, sheets=[{"view": "elevation", "side": "south"}, {"view": "plan", "storey": "FIRST"}])
    assert [s["number"] for s in out["sheets"]] == ["A-001", "A-201", "A-101"], "in the order given, numbered by kind"
    assert out["sheets"][1]["title"] == "South elevation" and out["sheets"][2]["title"].startswith("Plan of FIRST")


def test_one_scale_for_the_set_is_taken_by_every_sheet_that_names_none(house, tmp_path):
    m, path = house
    out = P.plan_set(m, str(tmp_path / "s"), model_path=path, scale="1:100", sheets=[{"view": "plan", "storey": "GROUND"}, {"view": "plan", "storey": "FIRST", "scale": "1:50"}])
    assert [s.get("ratio") for s in out["sheets"]] == [None, 100, 50]


def test_a_sheet_that_cannot_be_drawn_is_named_with_why_and_the_rest_are_bound(house, tmp_path):
    m, path = house
    sheets = [
        {"number": "A-101", "title": "Ground floor", "view": "plan", "storey": "GROUND"},
        {"number": "A-102", "title": "Attic", "view": "plan", "storey": "ATTIC"},
        {"number": "A-103", "title": "Too big", "view": "plan", "storey": "GROUND", "scale": "1:1"},
    ]
    out = P.plan_set(m, str(tmp_path / "some"), model_path=path, sheets=sheets)
    assert out["complete"] is True
    assert [s["number"] for s in out["sheets"]] == ["A-001", "A-101"]
    why = {s["number"]: s["why"] for s in out["not_drawn"]}
    assert set(why) == {"A-102", "A-103"}
    assert "ATTIC" in why["A-102"] and "does not fit" in why["A-103"]
    assert page_count(out["written"]["pdf"]) == 2
    cover = texts(out["sheets"][0]["svg"])
    assert "Not drawn" in cover and "A-102" in cover and "Attic" in cover
    assert not (tmp_path / "some-A-102.svg").exists()


def test_a_set_with_no_sheet_that_can_be_drawn_is_refused_and_writes_nothing(house, tmp_path):
    m, path = house
    with pytest.raises(ValueError, match="No sheet .* could be drawn.*ATTIC"):
        P.plan_set(m, str(tmp_path / "none"), model_path=path, sheets=[{"view": "plan", "storey": "ATTIC"}])
    assert list(tmp_path.iterdir()) == [Path(path)]


@pytest.mark.parametrize(
    ("sheets", "why"),
    [
        ([{"view": "plan", "storey": "GROUND", "paper": "A3"}], "one paper"),
        ([{"view": "plan", "storey": "GROUND", "output_path": "/tmp/x"}], "output_path"),
        ([{"view": "plan", "storey": "GROUND", "colour": "red"}], "colour"),
        ([{"view": "isometric"}], "not a view"),
        ([{"number": "A-101", "view": "plan", "storey": "GROUND"}, {"number": "A-101", "view": "plan", "storey": "FIRST"}], "A-101.*twice"),
        ([{"number": "A-001", "view": "plan", "storey": "GROUND"}], "cover"),
        ([{"number": "A/1", "view": "plan", "storey": "GROUND"}], "sheet number"),
        (["plan"], "object"),
        ('{"view": "plan"}', "list"),
    ],
)
def test_a_bad_sheet_is_refused_before_anything_is_drawn(house, tmp_path, sheets, why):
    m, path = house
    with pytest.raises(ValueError, match=why):
        P.plan_set(m, str(tmp_path / "bad"), model_path=path, sheets=sheets)
    assert list(tmp_path.iterdir()) == [Path(path)]


def test_a_bad_paper_scale_or_path_is_refused(house, tmp_path):
    m, path = house
    with pytest.raises(ValueError, match="not a sheet"):
        P.plan_set(m, str(tmp_path / "x"), paper="B5")
    with pytest.raises(ValueError, match="not a scale"):
        P.plan_set(m, str(tmp_path / "x"), scale="big")
    with pytest.raises(ValueError, match="output_path"):
        P.plan_set(m, "  ")


def test_the_title_names_the_set(house, tmp_path):
    m, path = house
    out = P.plan_set(m, str(tmp_path / "t"), model_path=path, title="The house as it stands", sheets=[{"view": "plan", "storey": "GROUND"}])
    assert out["set"]["title"] == "The house as it stands"
    assert "The house as it stands" in texts(out["sheets"][0]["svg"]) and "The house as it stands" in texts(out["sheets"][1]["svg"])
    assert b"The house as it stands" in pdf_objects(out["written"]["pdf"]), "the PDF's own title"


def test_an_edited_copy_says_so_on_every_sheet(house, tmp_path):
    m, path = house
    out = P.plan_set(m, str(tmp_path / "e"), model_path=path, edits=2, sheets=[{"view": "plan", "storey": "GROUND"}])
    for sheet in out["sheets"]:
        assert "as edited in the session (2 change(s), not saved to that file)" in texts(sheet["svg"])


# ---------------------------------------------------------------- time


def test_a_set_out_of_time_is_partial_writes_nothing_and_the_same_call_goes_on(house, tmp_path, monkeypatch):
    m, path = house
    real = D.sheet
    drawn: list[str] = []
    stopped: list[str] = []

    def stops_once_at_the_fourth(*args, **kwargs):
        if kwargs.get("fit_only"):
            return real(*args, **kwargs)
        drawn.append(kwargs["number"])
        if len(drawn) == 4 and not stopped:
            stopped.append(kwargs["number"])
            return {"view": kwargs["view"], "partial": True, "stopped": "Time ran out (30 s) while drawing (a test)."}, None
        return real(*args, **kwargs)

    monkeypatch.setattr(D, "sheet", stops_once_at_the_fourth)
    first = P.plan_set(m, str(tmp_path / "slow"), model_path=path)
    assert first["complete"] is False and first["partial"] is True
    assert first["done"] == ["A-101", "A-102", "A-201"] and first["to_do"] == ["A-202", "A-203", "A-204", "A-301", "A-302"]
    assert "same arguments" in first["stopped"]
    assert list(tmp_path.iterdir()) == [Path(path)], "nothing is written until the set is whole"

    drawn.clear()
    second = P.plan_set(m, str(tmp_path / "slow"), model_path=path)
    assert second["complete"] is True and [s["number"] for s in second["sheets"]] == DEFAULT_NUMBERS
    assert drawn == ["A-202", "A-203", "A-204", "A-301", "A-302"], "the sheets done before are not drawn again"
    assert page_count(second["written"]["pdf"]) == 9


def test_a_set_with_no_time_to_make_its_shapes_says_partial_and_goes_on(tmp_path):
    m = models.building(walls=3)
    first = P.plan_set(m, str(tmp_path / "set"), seconds=0)
    assert first["partial"] is True and first["done"] == [] and "shapes" in first["stopped"]
    assert not list(tmp_path.iterdir())
    second = P.plan_set(m, str(tmp_path / "set"))
    assert second["complete"] is True and second["set"]["pages"] == 9


def test_an_edit_between_calls_starts_the_set_again(house, tmp_path, monkeypatch):
    m, path = house
    real = D.sheet
    drawn: list[str] = []

    def counting(*args, **kwargs):
        if not kwargs.get("fit_only"):
            drawn.append(kwargs["number"])
            if len(drawn) == 2:
                return {"view": kwargs["view"], "partial": True, "stopped": "Time ran out (a test)."}, None
        return real(*args, **kwargs)

    monkeypatch.setattr(D, "sheet", counting)
    assert P.plan_set(m, str(tmp_path / "e"), model_path=path)["partial"] is True
    geometry.changed()  # the server calls this on every edit (server._watch_changes)
    drawn.clear()
    monkeypatch.setattr(D, "sheet", lambda *a, **k: (drawn.append(k["number"]) if not k.get("fit_only") else None) or real(*a, **k))
    out = P.plan_set(m, str(tmp_path / "e"), model_path=path)
    assert out["complete"] is True and drawn == DEFAULT_NUMBERS[1:], "every sheet drawn again, from the edited model"


# ---------------------------------------------------------------- through the server


def test_the_plan_set_is_served_and_reads_the_model_only(tmp_path):
    import anyio
    from mcp.server.mcpserver.exceptions import ToolError

    from flo2_ifc import edits
    from flo2_ifc.server import build_server

    path = tmp_path / "house.ifc"
    models.building(walls=3).write(str(path))
    server = build_server()

    async def run():
        tools = {t.name: t for t in await server.list_tools()}
        assert "ifc_plan_set" in tools and "ifc_plan_set" in edits.READS
        schema = tools["ifc_plan_set"].input_schema
        assert set(schema["properties"]) == {"output_path", "sheets", "paper", "scale", "title"} and schema["required"] == ["output_path"]
        assert schema["properties"]["sheets"]["type"] == "array"
        await server.call_tool("ifc_load", {"path": str(path)})
        result = await server.call_tool("ifc_plan_set", {"output_path": str(tmp_path / "served"), "sheets": [{"number": "A-101", "title": "Ground floor", "view": "plan", "storey": "GROUND"}]})
        out = json.loads("".join(b.text for b in result.content))
        assert out["complete"] is True and out["set"]["pages"] == 2 and out["source"]["model_file"] == "house.ifc"
        edited = json.loads("".join(b.text for b in (await server.call_tool("ifc_edited", {})).content))
        assert edited["elements"] == [], "a plan set changes nothing"
        try:
            await server.call_tool("ifc_plan_set", {"output_path": str(tmp_path / "bad"), "sheets": [{"view": "plan", "paper": "A3"}]})
        except ToolError as e:
            assert "one paper" in str(e)
        else:
            raise AssertionError("a sheet's own paper is refused")

    anyio.run(run)


# ---------------------------------------------------------------- a public sample

SAMPLES_DIR = os.environ.get("FLO2_IFC_SAMPLES", "")


@pytest.mark.skipif(not (SAMPLES_DIR and (Path(SAMPLES_DIR) / "AC20-FZK-Haus.ifc").is_file()), reason="FLO2_IFC_SAMPLES does not name a folder holding AC20-FZK-Haus.ifc")
def test_a_public_sample_gets_its_whole_default_set(tmp_path):
    path = str(Path(SAMPLES_DIR) / "AC20-FZK-Haus.ifc")
    m = ifcopenshell.open(path)
    out = None
    for _ in range(10):
        out = P.plan_set(m, str(tmp_path / "fzk"), model_path=path)
        if out["complete"]:
            break
    assert out and out["complete"], out
    storeys = [s for s in out["sheets"] if s.get("view") == "plan"]
    assert len(storeys) >= 2 and out["set"]["pages"] == 1 + len(storeys) + 4 + 2 - len(out.get("not_drawn", []))
    assert page_count(out["written"]["pdf"]) == out["set"]["pages"]
    assert np.all(np.diff([s["ratio"] for s in storeys]) == 0)


def test_a_sheet_is_the_drawing_ifc_draw_makes_with_its_number_and_title_added(default_set, tmp_path):
    m, path, _, out = default_set
    for sheet in (out["sheets"][1], out["sheets"][3], out["sheets"][7]):  # a plan, an elevation, a section
        alone = D.draw(m, str(tmp_path / f"alone-{sheet['number']}"), model_path=path, scale=f"1:{sheet['ratio']}", **{k: v for k, v in sheet["arguments"].items() if k != "title"})
        one, other = (ET.parse(p).getroot() for p in (alone["drawn"]["svg"], sheet["svg"]))

        def drawn(root):
            return sorted((p.get("class"), p.get("data-id"), p.get("d")) for p in root.iter(f"{SVG}path") if p.get("data-id"))

        assert drawn(one) == drawn(other) and drawn(one), sheet["number"]
        assert not [g for g in one.iter(f"{SVG}g") if g.get("id") == "sheet"], "a drawing alone has no sheet number"
        assert [g for g in other.iter(f"{SVG}g") if g.get("id") == "sheet"], "a set's sheet has its number"
