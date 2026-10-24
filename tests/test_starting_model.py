"""The starting model a chat copies (skills/read-a-building-model/references/starting-a-model.md), checked.

A chat that starts a building model from nothing writes it as text from this page and keeps it with flo2's file tool
(flo2's design 1a97fc1f1de4d5d2: req:a-chat-starts-a-designs-building-model-from-nothing, accepted 2026-10-24). So
the page must be right, and stay right: each test here takes the IFC text out of the page itself, makes it the way the
page tells a chat to (as written; in metres; with a third storey; with no map projection; with new GlobalIds), and
runs on it what ifc_validate and ifc_check_rules run, the same functions. With express rules there must be no issue,
and buildingSMART's rules must report no error. The page's worked numbers are checked too: its latitude and longitude
in degrees, minutes, seconds and millionths, and its easting and northing, against pyproj.

flo2 serves this page beside its skill read-a-building-model, an unchanged copy at the commit it pins; flo2's own
end-to-end check saves the starting model through its file tool and has the helper open and check it.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import ifcopenshell
import ifcopenshell.guid
import ifcopenshell.util.element
import ifcopenshell.util.placement
import pytest

from flo2_ifc import reading
from flo2_ifc import rules as R

PAGE = Path(__file__).resolve().parent.parent / "skills" / "read-a-building-model" / "references" / "starting-a-model.md"
RULES = os.path.isdir(os.path.join(R.RULES_DIR, "features", "rules"))
if not RULES and os.environ.get("CI"):
    raise RuntimeError(f"buildingSMART's rules are not installed at {R.RULES_DIR}: run tools/ifc_rules.py and set FLO2_IFC_RULES_DIR")
needs_rules = pytest.mark.skipif(not RULES, reason=f"buildingSMART's rules are not installed at {R.RULES_DIR} (tools/ifc_rules.py installs them)")

GLOBALID = re.compile(r"'([0-3][0-9A-Za-z_$]{21})'")
ALPHABET = set("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_$")


def page() -> str:
    return PAGE.read_text(encoding="utf-8")


def blocks() -> list[str]:
    """The page's IFC text blocks, in order: the starting model, the metre units, a third storey."""
    found = re.findall(r"```ifc\n(.*?)\n```", page(), flags=re.S)
    assert len(found) == 3, f"the page shows the starting model, the metre units and a third storey: {len(found)} blocks"
    return found


def starting() -> str:
    return blocks()[0] + "\n"


def line(text: str, ref: str) -> str:
    return next(x for x in text.split("\n") if x.startswith(f"{ref}="))


def replace_line(text: str, ref: str, new: str) -> str:
    return "\n".join(new if x.startswith(f"{ref}=") else x for x in text.split("\n"))


def without(text: str, *refs: str) -> str:
    return "\n".join(x for x in text.split("\n") if not x.startswith(tuple(f"{r}=" for r in refs)))


def in_metres(text: str) -> str:
    """As the page says: the metre lines in place of #10 to #27, the scale 1., and a storey 3 m up."""
    lines = text.split("\n")
    first = next(i for i, x in enumerate(lines) if x.startswith("#10="))
    last = next(i for i, x in enumerate(lines) if x.startswith("#27="))
    out = "\n".join(lines[:first] + blocks()[1].split("\n") + lines[last + 1 :])
    out = replace_line(out, "#41", line(out, "#41").replace(",0.3048);", ",1.);"))
    out = replace_line(out, "#73", line(out, "#73").replace(",10.);", ",3.);"))
    return replace_line(out, "#76", "#76=IFCCARTESIANPOINT((0.,0.,3.));")


def with_a_third_storey(text: str) -> str:
    """As the page says: the storey's lines added, and #82 lists it."""
    out = text.replace("ENDSEC;\nEND-ISO", blocks()[2] + "\nENDSEC;\nEND-ISO")
    return replace_line(out, "#82", line(out, "#82").replace("(#70,#73));", "(#70,#73,#90));"))


def with_new_globalids(text: str) -> str:
    """As a chat must: every GlobalId replaced by a new one."""
    return GLOBALID.sub(lambda m: f"'{ifcopenshell.guid.new()}'", text)


def checked(text: str, tmp_path: Path, name: str, rules: bool = True) -> dict:
    """What ifc_validate (express rules on) and ifc_check_rules say about `text`, kept as a file as flo2 keeps it."""
    path = tmp_path / f"{name}.ifc"
    path.write_text(text, encoding="utf-8")
    model = ifcopenshell.open(str(path))
    out = {"model": model, "validate": reading.validate(model, express_rules=True, seconds=120), "units": reading.units(model)}
    if rules:
        out["rules"] = R.check_rules(model, str(path), seconds=300)
    return out


def assert_no_errors(said: dict, warnings: set[str]) -> None:
    v = said["validate"]
    assert v["complete"] is True and v["valid"] is True and v["issues"] == [], v["issues"]
    assert v["rules"] == {"checked": True}, f"the schema's rules ran: {v.get('rules')}"
    r = said["rules"]
    assert r["complete"] is True and r["passed"] is True, r["issues"]
    assert r["summary"]["errors"] == 0, r["issues"]
    assert {i["code"] for i in r["issues"] if i["severity"] == "warning"} == warnings, r["issues"]


# ------------------------------------------------------------ the page's text


def test_the_starting_model_is_one_entity_per_line_with_no_comments():
    text = starting()
    assert text.startswith("ISO-10303-21;\nHEADER;\n") and text.endswith("ENDSEC;\nEND-ISO-10303-21;\n")
    assert "/*" not in text and "FILE_SCHEMA(('IFC4'));" in text
    data = text.split("DATA;\n", 1)[1].split("ENDSEC;", 1)[0].strip().split("\n")
    assert all(re.fullmatch(r"#\d+=IFC[A-Z0-9]+\(.*\);", x) for x in data), "one entity per line"


def test_every_globalid_shown_is_valid_and_different():
    shown = GLOBALID.findall(page())
    every = re.findall(r"'([^']{22})'", "\n".join(blocks()))
    assert sorted(shown) == sorted(every), "every 22-character value in the blocks is a GlobalId of the right form"
    assert len(shown) == len(set(shown)) == 9  # eight in the starting model, one for the third storey
    for g in shown:
        assert len(g) == 22 and set(g) <= ALPHABET and g[0] in "0123", g
        assert ifcopenshell.guid.compress(ifcopenshell.guid.expand(g)) == g, g


def compound(decimal: float) -> tuple[int, int, int, int]:
    """Decimal degrees as IFC writes them, the way the page says, in whole numbers only."""
    n = round(abs(decimal) * 3_600_000_000)
    d, n = divmod(n, 3_600_000_000)
    m, n = divmod(n, 60_000_000)
    s, u = divmod(n, 1_000_000)
    sign = -1 if decimal < 0 else 1
    return (sign * d, sign * m, sign * s, sign * u)


def test_the_worked_latitudes_and_longitudes_are_right():
    text = page()
    for decimal in (40.748440, -73.985664, -33.856784, 151.215297):
        written = "(" + ",".join(str(x) for x in compound(decimal)) + ")"
        assert written in text, f"{decimal} is {written}"
    site = line(starting(), "#50")
    assert "(40,44,54,384000),(-73,-59,-8,-390400)" in site
    # IfcOpenShell reads them back as the decimal degrees they came from.
    import ifcopenshell.util.geolocation as geo

    assert geo.dms2dd(40, 44, 54, 384000) == pytest.approx(40.748440, abs=1e-9)
    assert geo.dms2dd(-73, -59, -8, -390400) == pytest.approx(-73.985664, abs=1e-9)


def test_the_worked_easting_and_northing_are_the_sites_on_its_utm_zone():
    from pyproj import Transformer

    e, n = Transformer.from_crs("EPSG:4326", "EPSG:32618", always_xy=True).transform(-73.985664, 40.748440)
    assert f"({e:.3f}, {n:.3f})" in page()
    assert line(starting(), "#41") == f"#41=IFCMAPCONVERSION(#30,#40,{e:.3f},{n:.3f},0.,1.,0.,0.3048);"
    assert int((-73.985664 + 180) // 6) + 1 == 18 and "'EPSG:32618'" in line(starting(), "#40")
    assert int((151.215297 + 180) // 6) + 1 == 56 and "EPSG:32756" in page()


# ------------------------------------------------------------ the starting model, checked


def test_the_starting_model_opens_as_project_site_building_and_storeys_in_feet(tmp_path):
    said = checked(starting(), tmp_path, "as-written", rules=False)
    m = said["model"]
    (project,) = m.by_type("IfcProject")
    (site,) = m.by_type("IfcSite")
    (building,) = m.by_type("IfcBuilding")
    parts = lambda whole: [o.Name for rel in whole.IsDecomposedBy for o in rel.RelatedObjects]  # noqa: E731
    assert (parts(project), parts(site), parts(building)) == (["Site"], ["Example studio"], ["Level 1", "Level 2"])
    assert [(s.Name, s.Elevation) for s in m.by_type("IfcBuildingStorey")] == [("Level 1", 0.0), ("Level 2", 10.0)]
    storey_z = [ifcopenshell.util.placement.get_local_placement(s.ObjectPlacement)[2][3] for s in m.by_type("IfcBuildingStorey")]
    assert storey_z == [0.0, 10.0], "each storey is placed at its height"
    assert said["units"]["say"] == "Lengths are in feet (ft), areas in square feet and volumes in cubic feet, as the model declares them."
    assert site.RefLatitude == (40, 44, 54, 384000) and site.RefLongitude == (-73, -59, -8, -390400)
    (context,) = [c for c in m.by_type("IfcGeometricRepresentationContext") if not c.is_a("IfcGeometricRepresentationSubContext")]
    assert {s.ContextIdentifier for s in context.HasSubContexts} == {"Body", "Axis"}
    assert context.HasCoordinateOperation[0].TargetCRS.Name == "EPSG:32618"
    assert building.Decomposes[0].RelatingObject == site


@needs_rules
def test_as_written_it_passes_with_no_error_and_no_warning(tmp_path):
    assert_no_errors(checked(starting(), tmp_path, "as-written"), warnings=set())


@needs_rules
def test_with_new_globalids_as_a_chat_writes_it_it_passes(tmp_path):
    text = with_new_globalids(starting())
    assert not set(GLOBALID.findall(text)) & set(GLOBALID.findall(page())), "every GlobalId is new"
    assert_no_errors(checked(text, tmp_path, "new-ids"), warnings=set())


@needs_rules
def test_in_metres_it_passes(tmp_path):
    said = checked(in_metres(starting()), tmp_path, "metres")
    assert said["units"]["say"] == "Lengths are in metres (m), areas in square metres and volumes in cubic metres, as the model declares them."
    assert [s.Elevation for s in said["model"].by_type("IfcBuildingStorey")] == [0.0, 3.0]
    assert_no_errors(said, warnings=set())


@needs_rules
def test_with_a_third_storey_it_passes(tmp_path):
    said = checked(with_a_third_storey(starting()), tmp_path, "three-storeys")
    assert [s.Name for s in said["model"].by_type("IfcBuildingStorey")] == ["Level 1", "Level 2", "Level 3"]
    assert_no_errors(said, warnings=set())


@needs_rules
def test_with_one_storey_it_passes(tmp_path):
    text = without(starting(), "#73", "#74", "#75", "#76")
    text = replace_line(text, "#82", line(text, "#82").replace("(#70,#73));", "(#70));"))
    assert_no_errors(checked(text, tmp_path, "one-storey"), warnings=set())


@needs_rules
def test_with_no_map_projection_it_passes_with_only_grf003s_warning(tmp_path):
    """What the page tells a chat that cannot get the easting and northing: leave out #40 and #41."""
    said = checked(without(starting(), "#40", "#41"), tmp_path, "no-projection")
    assert_no_errors(said, warnings={"GRF003"})
    (grf003,) = [i for i in said["rules"]["issues"] if i["code"] == "GRF003"]
    assert "IfcProjectedCRS" in grf003["expected"]
    assert "GRF003" in page() and "There must be at least 1 instance(s) of .IfcProjectedCRS" in page()


@needs_rules
def test_a_wrong_scale_is_what_grf005_catches(tmp_path):
    """Why the page names the scale: a feet model on a metre grid at scale 1 warns GRF005."""
    text = replace_line(starting(), "#41", line(starting(), "#41").replace(",0.3048);", ",1.);"))
    r = checked(text, tmp_path, "wrong-scale")["rules"]
    assert {i["code"] for i in r["issues"]} == {"GRF005"}
