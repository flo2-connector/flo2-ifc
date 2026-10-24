"""buildingSMART's validation rules (src/flo2_ifc/rules.py), run on synthetic models IfcOpenShell writes.

The rules are buildingSMART's own (github.com/buildingSMART/ifc-gherkin-rules,
at the release tools/ifc_rules.py pins), run unchanged. They are not on PyPI:
CI installs them with tools/ifc_rules.py and sets FLO2_IFC_RULES_DIR, and the
image holds them at /opt/ifc-gherkin-rules. Run locally without them, these
tests skip; in CI (CI set) they fail instead, so a missing install is never a
pass. No real model is a fixture here.
"""

from __future__ import annotations

import os
import time

import pytest

from flo2_ifc import rules as R
from tests import models

INSTALLED = os.path.isdir(os.path.join(R.RULES_DIR, "features", "rules"))
if not INSTALLED:
    if os.environ.get("CI"):
        raise RuntimeError(f"buildingSMART's rules are not installed at {R.RULES_DIR}: run tools/ifc_rules.py and set FLO2_IFC_RULES_DIR")
    pytest.skip(f"buildingSMART's rules are not installed at {R.RULES_DIR} (tools/ifc_rules.py installs them)", allow_module_level=True)


@pytest.fixture(scope="module")
def files(tmp_path_factory):
    d = tmp_path_factory.mktemp("rules")
    out = {}
    # Rooms with no shape at all: GEM002 (every space has a Body representation) fails.
    out["house"] = str(d / "house.ifc")
    models.building(walls=3).write(out["house"])
    # Rooms with a Body shape: GEM002 passes.
    out["rooms"] = str(d / "rooms.ifc")
    models.rooms_on_a_floor([("Room A", (0, 0, 4, 6)), ("Room B", (4, 0, 8, 6))]).write(out["rooms"])
    return out


@pytest.fixture(scope="module")
def house(files):
    return R.check_rules(None, files["house"], seconds=120)


def test_the_pinned_release_is_installed_and_named():
    said = R.release()
    assert said["rules"]["tag"] == "v1.0.5" and said["rules"]["licence"] == "MIT"
    assert said["data_model"]["licence"] == "MIT"


def test_every_rule_set_runs_and_the_reply_says_which(house):
    assert house["complete"] is True and house["checked"]["to"] == house["checked"]["of"] == len(R.rules_for(None))
    assert set(house["rule_sets"]["ran"]) == set(R.RULE_SETS)
    assert set(house["rule_sets"]["not_run_here"]) == {"header, schema and EXPRESS rules", "bSDD"}
    assert house["rule_sets"]["disabled_upstream"], "upstream disables some rules, and the reply names them"
    assert "IFC101" in house["rules_passed"]  # the header names an official IFC version
    assert house["summary"]["rules_run"] == len(house["rules_with_issues"]) + len(house["rules_passed"]) + len(house["rules_not_applicable"])


def test_a_space_with_no_body_shape_fails_gem002_by_globalid(files, house):
    import ifcopenshell

    m = ifcopenshell.open(files["house"])
    again = R.check_rules(m, files["house"], rule_sets="informal-proposition", seconds=120)
    gem002 = [i for i in again["issues"] if i["code"] == "GEM002"]
    assert {i["global_id"] for i in gem002} == {s.GlobalId for s in m.by_type("IfcSpace")}
    assert all(i["severity"] == "error" for i in gem002)
    assert again["passed"] is False
    assert any(r["code"] == "GEM002" and r["result"] == "error" for r in house["rules_with_issues"])


def test_a_space_with_a_body_shape_passes_gem002(files):
    out = R.check_rules(None, files["rooms"], rule_sets="informal-proposition", seconds=120)
    assert "GEM002" in out["rules_passed"]
    assert not [i for i in out["issues"] if i["severity"] == "error"]


def test_rule_sets_narrow_what_runs(files):
    out = R.check_rules(None, files["rooms"], rule_sets="critical", seconds=120)
    assert out["checked"]["of"] == 1 and out["rules_passed"] == ["IFC101"]
    normative = R.check_rules(None, files["rooms"], rule_sets="normative", seconds=120)
    assert "industry-practice" not in normative["rule_sets"]["ran"]
    with pytest.raises(ValueError, match="rule_sets names"):
        R.check_rules(None, files["rooms"], rule_sets="colour")


def test_a_check_out_of_time_says_where_to_go_on_from(files):
    # The time a whole run takes on this machine, then a budget well short of
    # it: the check stops part way and says where to go on from (or, if not
    # even the first rule fits, passes it over and names it).
    whole = R.check_rules(None, files["rooms"], seconds=300)
    assert whole["complete"] is True
    budget = max(0.5, whole["seconds"] * 0.5)
    t = time.monotonic()
    first = R.check_rules(None, files["rooms"], seconds=budget)
    assert time.monotonic() - t < budget + 3
    assert first["complete"] is False and first["next_start"] == first["checked"]["to"] > 0
    assert "Call again with start" in first["note"]
    rest = R.check_rules(None, files["rooms"], start=first["next_start"], seconds=300)
    assert rest["complete"] is True and rest["checked"]["from"] == first["next_start"]


def test_a_rule_that_cannot_finish_in_one_call_is_passed_over_and_named(files):
    out = R.check_rules(None, files["rooms"], seconds=0.5)  # not even the model opens in time
    first = R.rules_for(None)[0]["code"]
    assert out["rules_not_finished"] == [first] and out["passed"] is None
    assert out["next_start"] == 1 and "NOT checked" in out["note"]


def test_the_rules_need_a_file(files):
    with pytest.raises(ValueError, match="save it first"):
        R.check_rules(None, None)
