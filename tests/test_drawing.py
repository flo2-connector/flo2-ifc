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


# ---------------------------------------------------------------- a room that holds other rooms

SVG = "{http://www.w3.org/2000/svg}"


def room_boxes(root):
    """Each room's outline on the paper, by its name: (x0, y0, x1, y1) in millimetres."""
    names = {}
    for p in paths(root, "room"):
        xy = [float(v) for v in re.findall(r"-?\d+\.\d+", p.get("d"))]
        names[p.get("data-id")] = (min(xy[0::2]), min(xy[1::2]), max(xy[0::2]), max(xy[1::2]))
    return names


def labels(root):
    """Each label's name and anchor on the paper, halo left out: {name: (x, y)}."""
    group = next(g for g in root.iter(f"{SVG}g") if g.get("id") == "labels")
    out = {}
    for t in group.iter(f"{SVG}text"):
        if "stroke" in (t.get("style") or "") or t.get("font-weight") != "bold":
            continue
        out["".join(t.itertext())] = (float(t.get("x")), float(t.get("y")))
    return out


def ids(model):
    return {s.Name: str(s.id()) for s in model.by_type("IfcSpace")}


def test_a_room_that_holds_others_is_named_in_its_part_clear_of_them(tmp_path):
    # The hall holds rooms A and B along its west end; its east end, 4 x 6 m, is clear.
    m = models.rooms_on_a_floor([("Hall", (0, 0, 10, 6)), ("Room A", (0, 0, 3, 6)), ("Room B", (3, 0, 6, 6))])
    out = D.draw(m, str(tmp_path / "plan"), view="plan", storey="Level 1", scale="1:50")
    root = svg_of(out)
    box, at, n = room_boxes(root), labels(root), ids(m)
    hall = box[n["Hall"]]
    east_of_b = box[n["Room B"]][2]
    x, y = at["Hall"]
    assert east_of_b < x < hall[2] and hall[1] < y < hall[3]  # in the clear part, not over A's or B's name
    assert x == pytest.approx((east_of_b + hall[2]) / 2, abs=0.5) and y == pytest.approx((hall[1] + hall[3]) / 2, abs=2.0)  # in its middle
    for room in ("Room A", "Room B"):  # theirs stay in the middle of each
        b = box[n[room]]
        assert at[room] == pytest.approx(((b[0] + b[2]) / 2, (b[1] + b[3]) / 2), abs=0.05)
    assert "unlabelled_rooms" not in out
    assert out["elements"]["rooms"] == 3


def test_a_room_with_no_part_clear_for_its_name_is_left_unnamed_and_the_reply_says_so(tmp_path):
    # The hall is tiled by A and B: there is nowhere to name it but on top of them.
    m = models.rooms_on_a_floor([("Hall", (0, 0, 6, 6)), ("Room A", (0, 0, 3, 6)), ("Room B", (3, 0, 6, 6))])
    out = D.draw(m, str(tmp_path / "plan"), view="plan", storey="Level 1")
    root = svg_of(out)
    assert set(labels(root)) == {"Room A", "Room B"}
    assert [p.get("data-id") for p in paths(root, "room")].count(ids(m)["Hall"]) == 1  # still outlined
    assert out["unlabelled_rooms"] == [{"id": int(ids(m)["Hall"]), "name": "Hall", "global_id": m.by_type("IfcSpace")[0].GlobalId, "contains": ["Room A", "Room B"]}]
    assert any(note.startswith("Not named on the sheet: Hall") and "Room A, Room B" in note for note in out["notes"])


def test_holding_is_most_of_a_room_not_all_of_it(tmp_path):
    # Room B runs 0.2 m past the hall's south edge: 96% of it is inside, so the hall holds it.
    m = models.rooms_on_a_floor([("Hall", (0, 0.2, 6, 6)), ("Room A", (0, 0.2, 3, 6)), ("Room B", (3, 0, 6, 6))])
    out = D.draw(m, str(tmp_path / "plan"), view="plan", storey="Level 1")
    assert [r["name"] for r in out.get("unlabelled_rooms", [])] == ["Hall"]
    # Rooms that only overlap (half of B in A) hold nothing: both are named where they always were.
    m = models.rooms_on_a_floor([("Room A", (0, 0, 6, 6)), ("Room B", (3, 0, 9, 6))])
    out = D.draw(m, str(tmp_path / "plan2"), view="plan", storey="Level 1")
    root = svg_of(out)
    box, at, n = room_boxes(root), labels(root), ids(m)
    for room in ("Room A", "Room B"):
        b = box[n[room]]
        assert at[room] == pytest.approx(((b[0] + b[2]) / 2, (b[1] + b[3]) / 2), abs=0.05)
    assert "unlabelled_rooms" not in out


def test_rooms_that_hold_none_are_named_where_they_always_were(tmp_path):
    # No room inside another: each name sits at its room's middle, as before rooms could be nested.
    m = models.rooms_on_a_floor([("Room A", (0, 0, 4, 6)), ("Room B", (4, 0, 7, 3)), ("Room C", (4, 3, 7, 6)), ("Room D", (7, 0, 12, 6))])
    out = D.draw(m, str(tmp_path / "plan"), view="plan", storey="Level 1")
    root = svg_of(out)
    box, at, n = room_boxes(root), labels(root), ids(m)
    assert set(at) == {"Room A", "Room B", "Room C", "Room D"}
    for room in at:
        b = box[n[room]]
        assert at[room] == pytest.approx(((b[0] + b[2]) / 2, (b[1] + b[3]) / 2), abs=0.05)
    assert "unlabelled_rooms" not in out and "notes" not in out


def test_a_section_names_a_room_that_holds_others_the_same_way(tmp_path):
    m = models.rooms_on_a_floor([("Hall", (0, 0, 6, 6)), ("Room A", (0, 0, 3, 6)), ("Room B", (3, 0, 6, 6))])
    out = D.draw(m, str(tmp_path / "sec"), view="section", line="-1000, 3000, 13000, 3000", looking="north")
    assert set(labels(svg_of(out))) >= {"Room A", "Room B"} and "Hall" not in labels(svg_of(out))
    assert [r["name"] for r in out["unlabelled_rooms"]] == ["Hall"]


def test_a_freed_models_meshes_are_forgotten_with_it(tmp_path):
    # The meshes are kept by (id(model), element id). Python gives a freed
    # object's id to the next one made, so a model built after another was freed
    # could be drawn with the first one's meshes: with these tests in one run,
    # a hall tiled by two rooms was drawn with an earlier hall's clear east end
    # (seen 2026-10-24: 8 of 30 such pairs in a loop). The server forgets every
    # mesh when its model changes (server._watch_changes); this pins that a
    # model's meshes also go when the model itself does, whoever made it.
    import gc

    from flo2_ifc import geometry as G

    m = models.rooms_on_a_floor([("Hall", (0, 0, 6, 6))])
    D.draw(m, str(tmp_path / "plan"), view="plan", storey="Level 1")
    key = id(m)
    assert any(k[0] == key for k in G._cache)
    del m
    gc.collect()
    assert not any(k[0] == key for k in G._cache)
    assert not any(k[0] == key for k in G._closures)


def _store_area(m, room: str, pset: str, name: str, value: float) -> None:
    import ifcopenshell.api

    space = next(s for s in m.by_type("IfcSpace") if s.Name == room)
    if pset.startswith("Qto_"):
        q = ifcopenshell.api.run("pset.add_qto", m, product=space, name=pset)
        ifcopenshell.api.run("pset.edit_qto", m, qto=q, properties={name: value})
    else:
        p = ifcopenshell.api.run("pset.add_pset", m, product=space, name=pset)
        ifcopenshell.api.run("pset.edit_pset", m, pset=p, properties={name: value})


def test_a_room_is_labelled_with_the_area_the_model_stores_and_a_large_difference_is_named(tmp_path):
    # Room A is 4 x 6 m (24.0 m²) as drawn; the model stores 30.0 m² (the outline is 20% under it).
    # Room B is 3 x 6 m (18.0 m²) and stores 20.5 m², 12% apart, in a Revit-style
    # "Dimensions" set. Room C, 5 x 6 m, stores none and is labelled as measured (30.0 m²).
    m = models.rooms_on_a_floor([("Room A", (0, 0, 4, 6)), ("Room B", (4, 0, 7, 6)), ("Room C", (7, 0, 12, 6))])
    _store_area(m, "Room A", "Qto_SpaceBaseQuantities", "NetFloorArea", 30.0)
    _store_area(m, "Room B", "Dimensions", "Area", 20.5)
    out = D.draw(m, str(tmp_path / "plan"), view="plan", storey="Level 1")
    words = texts(svg_of(out))
    assert "30.0 m² (323 ft²)" in words and "24.0 m²" not in words
    assert "20.5 m² (221 ft²)" in words and "18.0 m²" not in words
    by = {r["name"]: r for r in out["room_areas"]}
    assert by["Room A"]["source"] == "stored" and by["Room A"]["stored_in"] == "Qto_SpaceBaseQuantities.NetFloorArea"
    assert by["Room A"]["area"]["square_metres"] == pytest.approx(30.0) and by["Room A"]["measured"]["square_metres"] == pytest.approx(24.0, abs=0.01)
    assert by["Room A"]["differs"] is True
    assert by["Room B"]["source"] == "stored" and by["Room B"]["stored_in"] == "Dimensions.Area" and by["Room B"]["differs"] is True
    assert by["Room C"]["source"] == "measured" and by["Room C"]["area"]["square_metres"] == pytest.approx(30.0, abs=0.01)
    assert "measured" not in by["Room C"] and "differs" not in by["Room C"]
    named = [n for n in out["notes"] if "the model stores" in n]
    assert len(named) == 2
    assert named[0].startswith("Room A (#") and "30.0 m²" in named[0] and "24.0 m²" in named[0] and "20% apart" in named[0]  # of the stored area


def test_a_stored_area_within_five_percent_is_labelled_and_not_named(tmp_path):
    m = models.rooms_on_a_floor([("Room A", (0, 0, 4, 6))])
    _store_area(m, "Room A", "Qto_SpaceBaseQuantities", "NetFloorArea", 24.6)  # 2.5% over the outline's 24.0
    out = D.draw(m, str(tmp_path / "plan"), view="plan", storey="Level 1")
    assert "24.6 m²" in texts(svg_of(out))
    (room,) = out["room_areas"]
    assert room["source"] == "stored" and "differs" not in room
    assert "notes" not in out


def test_net_floor_area_comes_before_a_dimensions_area(tmp_path):
    m = models.rooms_on_a_floor([("Room A", (0, 0, 4, 6))])
    _store_area(m, "Room A", "Dimensions", "Area", 26.0)
    _store_area(m, "Room A", "Qto_SpaceBaseQuantities", "NetFloorArea", 24.2)
    out = D.draw(m, str(tmp_path / "plan"), view="plan", storey="Level 1")
    assert out["room_areas"][0]["stored_in"] == "Qto_SpaceBaseQuantities.NetFloorArea"
    assert "24.2 m²" in texts(svg_of(out))


def test_a_stored_area_is_read_in_the_models_area_unit(tmp_path):
    # The model's areas in square millimetres: 24,000,000 mm² is 24.0 m².
    import ifcopenshell.api

    m = models.rooms_on_a_floor([("Room A", (0, 0, 4, 6))])
    mm2 = ifcopenshell.api.run("unit.add_si_unit", m, unit_type="AREAUNIT", prefix="MILLI")
    assignment = m.by_type("IfcUnitAssignment")[0]
    assignment.Units = [u for u in assignment.Units if getattr(u, "UnitType", None) != "AREAUNIT"] + [mm2]
    _store_area(m, "Room A", "Qto_SpaceBaseQuantities", "NetFloorArea", 30_000_000.0)
    out = D.draw(m, str(tmp_path / "plan"), view="plan", storey="Level 1")
    room = out["room_areas"][0]
    assert room["area"]["square_metres"] == pytest.approx(30.0) and room["area"]["model"] == pytest.approx(30_000_000.0)
    assert "30.0 m²" in texts(svg_of(out))
