"""The readers for a whole building (src/flo2_ifc/reading.py), on synthetic models.

Each test pins one problem a large building model showed on 2026-10-23, at a
size the tests can build in a second:
a listing too large for one reply, no element extent, a room that did not say
its storey, a validation longer than one call, and a units note that said
"usually metres". No real model is a fixture here (tests/models.py).
"""

from __future__ import annotations

import json
import time

import pytest

from flo2_ifc import reading as R
from tests import models


@pytest.fixture(scope="module")
def house():
    return models.building(walls=3)


@pytest.fixture(scope="module")
def big():
    # 400 walls on one storey: like a real storey, more than one reply holds.
    return models.building(walls=400)


def size(x) -> int:
    return len(json.dumps(x, default=str))


# ------------------------------------------------------------ 5. units


def test_units_are_read_from_the_model_not_assumed(house):
    u = R.units(house)
    assert u["length"] == {"declared": True, "name": "millimetre", "symbol": "mm", "si_per_unit": 0.001}
    assert u["area"]["name"] == "square metre"
    assert u["volume"]["name"] == "cubic metre"
    assert "millimetres (mm)" in u["say"]
    assert "usually" not in u["say"]


def test_a_model_with_no_unit_assignment_says_so():
    import ifcopenshell

    f = ifcopenshell.file(schema="IFC4")
    f.createIfcProject(ifcopenshell.guid.new(), Name="bare")
    u = R.units(f)
    assert u["length"]["declared"] is False
    assert "declares no length unit" in u["say"]


@pytest.mark.parametrize(
    ("metres", "text"),
    [(0.0, "0' 0\""), (0.3048, "1' 0\""), (16.1525, "52' 11 15/16\""), (0.0127, "0' 0 1/2\""), (-0.3048, "-1' 0\"")],
)
def test_feet_and_inches_to_the_sixteenth(metres, text):
    assert R.feet_and_inches(metres) == text


# ------------------------------------------------------------ 3. storey of a room


def test_a_room_says_its_storey_though_it_is_aggregated_not_contained(house):
    kitchen = next(s for s in house.by_type("IfcSpace") if s.Name == "Kitchen")
    extra = R.info_extra(house, kitchen)
    assert extra["storey"]["name"] == "GROUND"
    assert extra["storey"]["via"][0].startswith("part of (IfcRelAggregates)")
    assert "millimetres" in extra["units"]


def test_a_wall_says_its_storey_by_containment(house):
    wall = house.by_type("IfcWall")[0]
    s = R.storey_of(wall)
    assert s["name"] == "GROUND"
    assert s["via"][0].startswith("contained in")


def test_a_storey_and_the_project_have_no_storey_above_them(house):
    assert R.storey_of(house.by_type("IfcProject")[0]) is None
    assert R.storey_of(house.by_type("IfcBuildingStorey")[0]) is None


# ------------------------------------------------------------ 1. paging and filtering


def test_page_of_says_what_it_withheld_and_where_to_go_on():
    items = [{"id": i} for i in range(10)]
    page, info = R.page_of(items, 0, 4)
    assert [i["id"] for i in page] == [0, 1, 2, 3]
    assert info == {"total": 10, "offset": 0, "listed": 4, "withheld": 6, "next_offset": 4}
    page, info = R.page_of(items, 8, 4)
    assert [i["id"] for i in page] == [8, 9]
    assert info["next_offset"] is None and info["withheld"] == 8


def test_page_of_cuts_at_the_character_budget_but_lists_at_least_one():
    items = [{"id": i, "pad": "x" * 100} for i in range(50)]
    page, info = R.page_of(items, 0, 50, chars=500)
    assert 1 <= len(page) < 50 and info["withheld"] == 50 - len(page)
    page, _ = R.page_of([{"pad": "x" * 1000}], 0, 5, chars=10)
    assert len(page) == 1


def test_bad_offsets_and_limits_are_refused_with_a_reason():
    with pytest.raises(ValueError, match="offset"):
        R.page_of([], -1, 5)
    with pytest.raises(ValueError, match="limit"):
        R.page_of([], 0, 0)


def test_the_tree_of_a_big_storey_fits_one_reply_and_says_how_to_get_the_rest(big):
    t = R.tree(big)
    assert size(t) < R.PAGE_CHARS + 5000
    # The storeys come in the file's order, which ifcopenshell.api's
    # aggregate.assign_object leaves to a Python set: find each by its name.
    storeys = {s["name"]: s for s in t["tree"]["children"][0]["children"][0]["children"]}
    ground = storeys["GROUND"]
    assert ground["name"] == "GROUND" and ground["element_counts"] == {"IfcWall": 400}
    assert ground["elevation"] == 0.0 and ground["z"] == 0.0
    assert t["elements_page"]["total"] == 402 and t["elements_page"]["withheld"] > 0
    assert "offset" in t["more"] and "storey" in t["more"]
    first = storeys["FIRST"]
    assert first["z"] == 3000.0, "a storey's placed z, in the model's unit"


def test_the_tree_filters_by_storey_and_by_class(big):
    t = R.tree(big, storey="first", element_type="IfcDoor")
    assert t["tree"]["name"] == "FIRST"
    assert [e["name"] for e in t["tree"]["elements"]] == ["Front door"]
    assert t["elements_page"]["total"] == 1 and "more" not in t
    by_id = R.tree(big, storey=str(big.by_type("IfcBuildingStorey")[0].id()), offset=390, limit=50)
    assert by_id["elements_page"]["listed"] == 10


def test_an_unknown_storey_or_class_is_refused_naming_what_there_is(big):
    with pytest.raises(ValueError, match='"GROUND"'):
        R.tree(big, storey="Mezzanine")
    with pytest.raises(ValueError, match="not an IFC class"):
        R.tree(big, element_type="IfcNotAThing")


def test_select_pages_and_counts(big):
    s = R.select(big, "IfcWall", limit=50)
    assert s["page"]["total"] == 400 and s["page"]["listed"] == 50 and s["counts"] == {"IfcWall": 400}
    assert "location" in s["more"]
    nxt = R.select(big, "IfcWall", offset=s["page"]["next_offset"], limit=50)
    assert nxt["elements"][0]["id"] > s["elements"][-1]["id"]


def test_select_by_storey_with_a_location_selector(big):
    s = R.select(big, 'IfcDoor, location = "FIRST"')
    assert [e["name"] for e in s["elements"]] == ["Front door"]
    assert R.select(big, 'IfcWall, location = "FIRST"')["page"]["total"] == 0


def test_relations_of_a_big_storey_fit_and_filter(big):
    from ifcquery import relations

    ground = big.by_type("IfcBuildingStorey")[0]
    full = relations.relations(big, ground)
    assert size(full) > 40_000, "unpaged, this storey's relations pass a reply's budget"
    r = R.relations(full, big)
    assert size(r) < R.PAGE_CHARS + 5000
    assert r["lists"]["children.contained"]["total"] == 400
    assert r["lists"]["children.contained"]["withheld"] > 0
    assert "elements" not in r and r["elements_total"] > 400
    assert "offset" in r["more"]
    walls = R.relations(full, big, element_type="IfcWall", offset=395, limit=10)
    assert walls["lists"]["children.contained"]["listed"] == 5


def test_relations_up_is_passed_through(house):
    from ifcquery import relations

    door = house.by_type("IfcDoor")[0]
    chain = relations.relations(house, door, traverse="up")
    assert R.relations(chain, house) == chain


# ------------------------------------------------------------ 2. extent


def test_extent_of_the_roof_gives_its_top_in_the_models_unit_metres_and_feet(house):
    e = R.extent(house, selector="IfcRoof")
    assert e["length_unit"] == "mm"
    assert e["overall"]["top"] == pytest.approx(6500.0)
    assert e["overall"]["bottom"] == pytest.approx(6000.0)
    assert e["overall_metres"]["top"] == pytest.approx(6.5)
    assert e["overall_feet_inches"]["top"] == "21' 3 7/8\""
    assert e["highest"]["name"] == "Flat roof"
    assert "millimetres" in e["units"]


def test_extent_of_one_element_and_of_many(house):
    door = house.by_type("IfcDoor")[0]
    one = R.extent(house, element_id=door.id())
    assert one["overall"]["size"] == pytest.approx([900.0, 50.0, 2100.0])
    walls = R.extent(house, selector="IfcWall", limit=2)
    assert walls["measured"] == 3 and walls["page"]["listed"] == 2 and "more" in walls
    assert walls["overall"]["max"][1] == pytest.approx(2200.0)


def test_extent_counts_what_has_no_shape_and_refuses_an_empty_question(house):
    e = R.extent(house, selector="IfcSpace")
    assert e["no_shape"] == 2 and e["measured"] == 0 and "overall" not in e
    with pytest.raises(ValueError, match="selector"):
        R.extent(house)
    with pytest.raises(ValueError, match="not found"):
        R.extent(house, element_id=999999)


def test_extent_that_runs_out_of_time_says_it_is_partial(big):
    e = R.extent(big, selector="IfcWall", seconds=0)
    assert e.get("partial") is True
    assert e["measured"] < 400
    assert "Stopped after" in e["stopped"]


# ------------------------------------------------------------ 4. validate


def test_validate_whole_in_one_call_when_time_allows(house):
    v = R.validate(house)
    assert v["complete"] is True and v["valid"] is True and v["next_start"] is None
    assert v["checked"]["to"] == v["checked"]["of"]


def test_validate_stops_at_its_time_and_goes_on_from_next_start(big):
    v = R.validate(big, seconds=0)
    assert v["complete"] is False and v["next_start"] is not None
    assert "Partial" in v["note"]
    total = v["checked"]["of"]
    calls = 1
    while v["next_start"] is not None:
        v = R.validate(big, start=v["next_start"], seconds=0.05)
        calls += 1
        assert calls < 10_000
    assert v["complete"] is True and v["checked"]["to"] == total
    assert calls > 1


def test_validate_finds_an_error_in_a_later_window_and_a_duplicate_globalid_anywhere():
    f = models.building(walls=3)
    walls = f.by_type("IfcWall")
    walls[2].GlobalId = walls[0].GlobalId
    v = R.validate(f)
    assert v["valid"] is False
    assert any("IfcRoot.UR1" in i["message"] for i in v["issues"])
    # The duplicate is seen by every call, from any start.
    late = R.validate(f, start=len(f.entity_names()) - 1)
    assert any("IfcRoot.UR1" in i["message"] for i in late["issues"])


def test_validate_moves_on_by_at_least_one_instance_a_call(house):
    v = R.validate(house, seconds=0)
    assert v["checked"]["to"] == 1 and v["next_start"] == 1
    v = R.validate(house, start=1, seconds=0)
    assert v["checked"] == {"from": 1, "to": 2, "of": v["checked"]["of"]}


def test_validate_leaves_ifcopenshells_feature_switch_as_it_found_it(big):
    import ifcopenshell

    before = ifcopenshell.ifcopenshell_wrapper.get_feature("use_attribute_value_derived")
    R.validate(big, seconds=0)
    assert ifcopenshell.ifcopenshell_wrapper.get_feature("use_attribute_value_derived") == before


def test_express_rules_run_when_the_instances_are_done(house):
    v = R.validate(house, express_rules=True)
    assert v["rules"]["checked"] is True


def test_express_rules_that_cannot_finish_are_stopped_and_said(house):
    t = time.monotonic()
    rules = R._rules_in_child(house, 0.0)
    assert time.monotonic() - t < 5
    assert rules["checked"] is False and "stopped" in rules["why"]


def test_express_rules_wait_for_the_call_that_finishes_the_instances(big):
    v = R.validate(big, express_rules=True, seconds=0)
    assert v["complete"] is False and v["rules"]["checked"] is False
