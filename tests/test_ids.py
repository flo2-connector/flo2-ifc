"""IDS checks and IDS writing (src/flo2_ifc/checking.py), on synthetic models IfcOpenShell writes.

Every facet type flo2-ifc writes (entity, attribute, property, classification,
material, part_of) is written into an IDS with ``write_ids`` (ifctester's
API), checked against the IDS 1.0 XSD, read back, and run against a model
that passes it and one that fails it with ``check_ids`` (ifctester's checker).
An invalid IDS is refused. A large model's check keeps to the time it is given.
No real model is a fixture here.
"""

from __future__ import annotations

import json
import time

import ifcopenshell
import ifcopenshell.api
import pytest

from flo2_ifc import checking as C
from tests import models

run = ifcopenshell.api.run


def _kept(m, tmp_path, name="model.ifc") -> tuple[ifcopenshell.file, str]:
    path = str(tmp_path / name)
    m.write(path)
    return ifcopenshell.open(path), path


def _ids(tmp_path, specs, name="req") -> str:
    out = C.write_ids({"title": "Synthetic must-haves", "specifications": specs}, str(tmp_path / name))
    assert out["valid"] is True and out["written"].endswith(".ids")
    return out["written"]


def _one(tmp_path, m, spec) -> dict:
    model, path = _kept(m, tmp_path)
    ids = _ids(tmp_path, [spec])
    out = C.check_ids(model, path, ids, report_path=str(tmp_path / "report"))
    assert out["complete"] is True
    return out["specifications"][0]


def _house():
    return models.building(walls=3)


def _pset(m, element, pset, props):
    p = run("pset.add_pset", m, product=element, name=pset)
    run("pset.edit_pset", m, pset=p, properties=props)


# ---------------------------------------------------------------- each facet, passing and failing

WALLS = [{"facet": "entity", "name": "IfcWall"}]


def test_entity_facet_passes_and_fails(tmp_path):
    spec = {"name": "Walls are standard", "applicability": WALLS, "requirements": [{"facet": "entity", "name": "IfcWall", "predefined_type": "STANDARD"}]}
    m = _house()
    for w in m.by_type("IfcWall"):
        w.PredefinedType = "STANDARD"
    assert _one(tmp_path / "a", m, spec)["status"] == "passed"
    m = _house()
    for w in m.by_type("IfcWall"):
        w.PredefinedType = "STANDARD"
    m.by_type("IfcWall")[0].PredefinedType = "PARTITIONING"
    got = _one(tmp_path / "b", m, spec)
    assert got["status"] == "failed" and got["failed_elements"] == 1 and got["applicable"] == 3


def test_attribute_facet_passes_and_fails(tmp_path):
    spec = {"name": "Walls are named W-nnn", "applicability": WALLS, "requirements": [{"facet": "attribute", "name": "Name", "value": {"pattern": "Wall [0-9]{3}"}}]}
    assert _one(tmp_path / "a", _house(), spec)["status"] == "passed"
    m = _house()
    bad = m.by_type("IfcWall")[1]
    bad.Name = "north wall"
    got = _one(tmp_path / "b", m, spec)
    assert got["status"] == "failed"
    (failing,) = got["failing"]
    assert failing["global_id"] == bad.GlobalId and failing["class"] == "IfcWall" and failing["reasons"]


def test_property_facet_passes_and_fails(tmp_path):
    spec = {
        "name": "Every wall has a fire rating",
        "applicability": WALLS,
        "requirements": [{"facet": "property", "property_set": "Pset_WallCommon", "property": "FireRating", "value": {"one_of": ["EI60", "EI90"]}, "data_type": "IfcLabel"}],
    }
    m = _house()
    for w in m.by_type("IfcWall"):
        _pset(m, w, "Pset_WallCommon", {"FireRating": "EI60"})
    assert _one(tmp_path / "a", m, spec)["status"] == "passed"
    m = _house()
    walls = m.by_type("IfcWall")
    _pset(m, walls[0], "Pset_WallCommon", {"FireRating": "EI60"})
    _pset(m, walls[1], "Pset_WallCommon", {"FireRating": "EI30"})  # a value not allowed
    got = _one(tmp_path / "b", m, spec)  # and walls[2] has none
    assert got["status"] == "failed" and got["failed_elements"] == 2
    assert {f["global_id"] for f in got["failing"]} == {walls[1].GlobalId, walls[2].GlobalId}


def test_a_numeric_bound_on_a_quantity(tmp_path):
    spec = {
        "name": "Every space is at least 10 m2",
        "applicability": [{"facet": "entity", "name": "IfcSpace"}],
        "requirements": [{"facet": "property", "property_set": "Qto_SpaceBaseQuantities", "property": "NetFloorArea", "value": {"at_least": 10}, "data_type": "IfcAreaMeasure"}],
    }
    assert _one(tmp_path / "a", _house(), spec)["status"] == "passed"  # both spaces store 12.5
    m = _house()
    space = m.by_type("IfcSpace")[0]
    qto = ifcopenshell.util.element.get_psets(space)["Qto_SpaceBaseQuantities"]["id"]
    run("pset.edit_qto", m, qto=m.by_id(qto), properties={"NetFloorArea": 8.0})
    got = _one(tmp_path / "b", m, spec)
    assert got["status"] == "failed" and [f["global_id"] for f in got["failing"]] == [space.GlobalId]


def test_classification_facet_passes_and_fails(tmp_path):
    spec = {"name": "Walls are classified", "applicability": WALLS, "requirements": [{"facet": "classification", "system": "Uniclass"}]}
    m = _house()
    system = run("classification.add_classification", m, classification="Uniclass")
    for w in m.by_type("IfcWall"):
        run("classification.add_reference", m, products=[w], identification="EF_25_10", name="Walls", classification=system)
    assert _one(tmp_path / "a", m, spec)["status"] == "passed"
    got = _one(tmp_path / "b", _house(), spec)
    assert got["status"] == "failed" and got["failed_elements"] == 3


def test_material_facet_passes_and_fails(tmp_path):
    spec = {"name": "Walls say their material", "applicability": WALLS, "requirements": [{"facet": "material", "value": "Concrete"}]}
    m = _house()
    concrete = run("material.add_material", m, name="Concrete")
    run("material.assign_material", m, products=m.by_type("IfcWall"), material=concrete)
    assert _one(tmp_path / "a", m, spec)["status"] == "passed"
    m = _house()
    steel = run("material.add_material", m, name="Steel")
    run("material.assign_material", m, products=m.by_type("IfcWall"), material=steel)
    got = _one(tmp_path / "b", m, spec)
    assert got["status"] == "failed" and got["failed_elements"] == 3


def test_part_of_facet_passes_and_fails(tmp_path):
    spec = {
        "name": "Every wall is on a storey",
        "applicability": WALLS,
        "requirements": [{"facet": "part_of", "entity": "IfcBuildingStorey", "relation": "IfcRelContainedInSpatialStructure"}],
    }
    assert _one(tmp_path / "a", _house(), spec)["status"] == "passed"
    m = _house()
    loose = m.by_type("IfcWall")[2]
    run("spatial.unassign_container", m, products=[loose])
    got = _one(tmp_path / "b", m, spec)
    assert got["status"] == "failed" and [f["global_id"] for f in got["failing"]] == [loose.GlobalId]


def test_a_prohibited_specification_and_a_required_one_with_nothing_to_apply_to(tmp_path):
    model, path = _kept(_house(), tmp_path)
    ids = _ids(
        tmp_path,
        [
            {"name": "No proxies", "cardinality": "prohibited", "applicability": [{"facet": "entity", "name": "IfcBuildingElementProxy"}]},
            {"name": "No doors", "cardinality": "prohibited", "applicability": [{"facet": "entity", "name": "IfcDoor"}]},
            {"name": "There are windows", "applicability": [{"facet": "entity", "name": "IfcWindow"}], "requirements": [{"facet": "attribute", "name": "Name"}]},
            {"name": "Columns, if any, are named", "cardinality": "optional", "applicability": [{"facet": "entity", "name": "IfcColumn"}], "requirements": [{"facet": "attribute", "name": "Name"}]},
        ],
    )
    out = C.check_ids(model, path, ids)
    by = {s["name"]: s for s in out["specifications"]}
    assert by["No proxies"]["status"] == "passed"
    assert by["No doors"]["status"] == "failed" and by["No doors"]["failed_elements"] == 1
    assert by["There are windows"]["status"] == "failed" and "no element" in by["There are windows"]["why"]
    assert by["Columns, if any, are named"]["status"] == "passed"
    assert out["summary"] == {"passed": 2, "failed": 2, "not_this_ifc_version": 0}


def test_a_quantity_is_compared_in_si_units_and_an_attribute_in_the_models_unit(tmp_path):
    # The house is in millimetres. Its rooms store Height 2700 mm: IDS 1.0
    # compares a quantity in SI units (2.7 m), so at_least 2.5 passes and
    # at_least 2500 fails. A door's OverallWidth is an attribute, compared as
    # stored: 900 (mm) passes at_least 850 and fails at_least 950.
    m = _house()
    m.by_type("IfcDoor")[0].OverallWidth = 900.0
    height = lambda b: {"name": f"h {b}", "applicability": [{"facet": "entity", "name": "IfcSpace"}], "requirements": [{"facet": "property", "property_set": "Qto_SpaceBaseQuantities", "property": "Height", "value": {"at_least": b}, "data_type": "IfcLengthMeasure"}]}
    width = lambda b: {"name": f"w {b}", "applicability": [{"facet": "entity", "name": "IfcDoor"}], "requirements": [{"facet": "attribute", "name": "OverallWidth", "value": {"at_least": b}}]}
    model, path = _kept(m, tmp_path)
    ids = _ids(tmp_path, [height(2.5), height(2500), width(850), width(950)])
    got = [s["status"] for s in C.check_ids(model, path, ids)["specifications"]]
    assert got == ["passed", "failed", "passed", "failed"]


# ---------------------------------------------------------------- the reply and the reports


def test_the_reply_cites_both_files_and_the_reports_are_written(tmp_path):
    model, path = _kept(_house(), tmp_path)
    ids = _ids(tmp_path, [{"name": "Every wall has a fire rating", "applicability": WALLS, "requirements": [{"facet": "property", "property_set": "Pset_WallCommon", "property": "FireRating"}]}])
    out = C.check_ids(model, path, ids, report_path=str(tmp_path / "report"), limit=2)
    from flo2_ifc import geometry

    assert out["model"] == {"file": "model.ifc", "sha256": geometry.file_sha256(path)}
    assert out["ids"]["file"] == "req.ids" and out["ids"]["sha256"] == geometry.file_sha256(ids) and out["ids"]["specifications"] == 1
    assert out["passed"] is False
    spec = out["specifications"][0]
    assert len(spec["failing"]) == 2 and spec["failing_page"] == {"offset": 0, "listed": 2, "of": 3, "next_offset": 2}
    assert "ifctester 0.9.0" in out["checked_by"]
    html = (tmp_path / "report.html").read_text()
    assert "Every wall has a fire rating" in html and "<html" in html.lower()
    data = json.loads((tmp_path / "report.json").read_text())
    assert data["specifications"][0]["name"] == "Every wall has a fire rating" and data["status"] is False
    assert out["reports"] == {"json": str(tmp_path / "report.json"), "html": str(tmp_path / "report.html")}


def test_a_specification_for_another_ifc_version_is_said_so(tmp_path):
    model, path = _kept(_house(), tmp_path)
    ids = _ids(tmp_path, [{"name": "2x3 walls", "ifc_version": "IFC2X3", "applicability": WALLS, "requirements": [{"facet": "attribute", "name": "Name"}]}])
    out = C.check_ids(model, path, ids)
    assert out["specifications"][0]["status"] == "not_this_ifc_version" and out["summary"]["not_this_ifc_version"] == 1


# ---------------------------------------------------------------- writing: refusals


@pytest.mark.parametrize(
    "description, words",
    [
        ({"specifications": []}, "`title` is needed"),
        ({"title": "t", "specifications": []}, "at least one specification"),
        ({"title": "t", "specifications": [{"name": "x", "applicability": WALLS, "requirements": [{"facet": "colour"}]}]}, "`facet` is one of"),
        ({"title": "t", "specifications": [{"name": "x", "applicability": [], "requirements": []}]}, "`applicability` is a list"),
        ({"title": "t", "specifications": [{"name": "x", "applicability": WALLS, "requirements": [{"facet": "attribute", "name": "Name", "cardinality": "sometimes"}]}]}, "cardinality is required, optional or prohibited"),
        ({"title": "t", "specifications": [{"name": "x", "applicability": WALLS, "requirements": [{"facet": "property", "property_set": "P", "property": "Q", "value": {"at_least": "ten"}}]}]}, "at_least is a number"),
        ({"title": "t", "ifc_version": "IFC5", "specifications": [{"name": "x", "applicability": WALLS, "requirements": [{"facet": "attribute", "name": "Name"}]}]}, "not IFC5"),
        ({"title": "t", "specifications": [{"name": "x", "applicability": WALLS, "requirements": [{"facet": "attribute", "name": "Name", "colour": "red"}]}]}, "not colour"),
    ],
)
def test_a_description_that_makes_no_valid_ids_is_refused_and_nothing_is_written(tmp_path, description, words):
    with pytest.raises(C.IdsRefused, match=words.replace("(", r"\(").replace(")", r"\)").replace("`", "`")):
        C.write_ids(description, str(tmp_path / "bad"))
    assert not (tmp_path / "bad.ids").exists()


def test_a_value_the_xsd_refuses_is_refused_with_the_schemas_reason(tmp_path):
    # A negative length, which the IDS 1.0 XSD's xs:minLength refuses.
    description = {"title": "t", "specifications": [{"name": "x", "applicability": WALLS, "requirements": [{"facet": "attribute", "name": "Name", "value": {"min_length": -1}}]}]}
    with pytest.raises(C.IdsRefused, match="valid IDS 1.0 file: value must be non negative"):
        C.write_ids(description, str(tmp_path / "bad"))
    assert not (tmp_path / "bad.ids").exists()


# ---------------------------------------------------------------- checking: an invalid IDS


def test_an_invalid_ids_file_is_refused_with_the_schemas_reason(tmp_path):
    model, path = _kept(_house(), tmp_path)
    good = _ids(tmp_path, [{"name": "x", "applicability": WALLS, "requirements": [{"facet": "attribute", "name": "Name"}]}])
    text = open(good, encoding="utf-8").read()
    bad = tmp_path / "bad.ids"
    bad.write_text(text.replace("<specifications>", "<specifications><nonsense/>"), encoding="utf-8")
    with pytest.raises(C.IdsRefused, match="not a valid IDS 1.0 file"):
        C.check_ids(model, path, str(bad))
    notxml = tmp_path / "notes.ids"
    notxml.write_text("every wall has a fire rating", encoding="utf-8")
    with pytest.raises(C.IdsRefused, match="could not be read as an IDS file|not a valid IDS"):
        C.check_ids(model, path, str(notxml))


# ---------------------------------------------------------------- time


def test_a_large_models_check_keeps_to_its_time_and_goes_on_from_where_it_stopped(tmp_path):
    m = models.building(walls=1200)
    model, path = _kept(m, tmp_path)
    specs = [
        {"name": f"Check {i}", "applicability": WALLS, "requirements": [{"facet": "property", "property_set": "Pset_WallCommon", "property": "FireRating"}, {"facet": "attribute", "name": "Name", "value": {"pattern": "Wall [0-9]+"}}]}
        for i in range(40)
    ]
    ids = _ids(tmp_path, specs)
    budget = 3.0
    t = time.monotonic()
    first = C.check_ids(model, path, ids, report_path=str(tmp_path / "r1"), seconds=budget)
    took = time.monotonic() - t
    assert took < budget + 1.5, took
    assert first["complete"] is False and 0 < first["checked"]["to"] < 40 and first["next_start"] == first["checked"]["to"]
    assert "Call again with start" in first["note"]
    assert first["reports"]  # the specifications it finished are reported
    rest = C.check_ids(model, path, ids, start=first["next_start"], seconds=60)
    assert rest["complete"] is True and rest["checked"]["from"] == first["next_start"] and rest["checked"]["to"] == 40
    assert all(s["status"] == "failed" and s["failed_elements"] == 1200 for s in first["specifications"] + rest["specifications"])


# THE REPORTS OF WHAT A CHECK FINISHED ARE KEPT WHEN WRITING THEM RUNS PAST THE CALL'S TIME
# (fact:root-cause-an-ids-checks-finished-reports-are-thrown-away-when-their-writing-outruns-the-kill-2026-11-10).
# The reports are about 80% of a large check's time, bounded only by a fixed guess (REPORT_FACTOR)
# with a 10% margin, and the parent killed the child at exactly the budget, so a check slowed by
# about half a second in its last 3 s lost the reports of every specification it had finished
# (the timing test above failed on CI 2026-11-10). Now the child says when it has finished
# checking, the parent gives its reports a grace (REPORT_GRACE of the budget), and a JSON report
# once written counts whatever happens to the HTML. Each test slows the child's report writing by
# a known amount (the fork inherits the patch). Observed failing on main 11583bd (see the PR).


def _large(tmp_path):
    m = models.building(walls=1200)
    model, path = _kept(m, tmp_path)
    specs = [
        {"name": f"Check {i}", "applicability": WALLS, "requirements": [{"facet": "property", "property_set": "Pset_WallCommon", "property": "FireRating"}, {"facet": "attribute", "name": "Name", "value": {"pattern": "Wall [0-9]+"}}]}
        for i in range(40)
    ]
    return model, path, _ids(tmp_path, specs)


def _slow_html(monkeypatch, seconds: float) -> None:
    from ifctester import reporter

    plain = reporter.Html.to_file

    def slow(self, *a, **k):
        time.sleep(seconds)
        return plain(self, *a, **k)

    monkeypatch.setattr(reporter.Html, "to_file", slow)


def test_the_reports_of_what_was_checked_are_kept_when_writing_them_runs_past_the_budget(tmp_path, monkeypatch):
    model, path, ids = _large(tmp_path)
    budget = 4.0
    # 0.3 of the budget more in the report: past the budget on its own, inside the grace.
    _slow_html(monkeypatch, 0.3 * budget)
    t = time.monotonic()
    r = C.check_ids(model, path, ids, report_path=str(tmp_path / "r"), seconds=budget)
    took = time.monotonic() - t
    assert r["complete"] is False and r["checked"]["to"] > 0
    assert set(r["reports"]) == {"json", "html"}, r.get("reports_note")
    assert (tmp_path / "r.json").is_file() and (tmp_path / "r.html").is_file()
    assert took < budget * (1 + C.REPORT_GRACE) + 0.5, took


def test_a_json_report_written_in_time_is_kept_when_the_html_is_not(tmp_path, monkeypatch):
    model, path, ids = _large(tmp_path)
    budget = 4.0
    _slow_html(monkeypatch, 30.0)  # the HTML never finishes in the call
    t = time.monotonic()
    r = C.check_ids(model, path, ids, report_path=str(tmp_path / "r"), seconds=budget)
    took = time.monotonic() - t
    assert r["reports"] == {"json": str(tmp_path / "r.json")}, r
    assert "HTML report" in r["reports_note"] and "JSON report" in r["reports_note"]
    assert took < budget * (1 + C.REPORT_GRACE) + 0.5, took


def test_a_check_of_every_specification_is_complete_when_only_its_html_report_is_cut_off(tmp_path, monkeypatch):
    # A small check finishes every specification and writes its JSON well inside the call; only the HTML is cut
    # off. It is complete, with nothing left to call again for.
    m = models.building(walls=3)
    model, path = _kept(m, tmp_path)
    ids = _ids(tmp_path, [{"name": "Fire rating", "applicability": WALLS, "requirements": [{"facet": "property", "property_set": "Pset_WallCommon", "property": "FireRating"}]}])
    budget = 2.0
    _slow_html(monkeypatch, 30.0)
    r = C.check_ids(model, path, ids, report_path=str(tmp_path / "r"), seconds=budget)
    assert r["complete"] is True and r["next_start"] is None and r["checked"] == {"from": 0, "to": 1, "of": 1}
    assert r["reports"] == {"json": str(tmp_path / "r.json")} and "note" not in r, r
