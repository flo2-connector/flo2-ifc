"""Every unit a model declares, and the unit of every measured value (since 0.9.0).

A designer read a window's U-factor (Pset_WindowCommon ThermalTransmittance 1.70348) with no unit: the model declared
none for thermal transmittance, the readers named only lengths, areas and volumes, and ifc_edit could not declare a
derived unit (1510fc3728665dbf,
fact:root-cause-a-u-factor-in-a-model-carries-no-unit-and-the-reader-reports-only-length-area-volume-2026-10-26).
These tests build that window with ifcopenshell.api, in feet as a Revit export is, and hold:

- ifc_units to every unit the assignment holds (SI, conversion-based, derived, the currency), with symbols;
- ifc_info to each measured property's measure type and unit, from the property, the project, or "not declared";
- ifc_add_unit to declaring a unit with IfcOpenShell's functions and exact factors, refusing what is not one;
- ifc_edit's pset.edit_pset to writing a measured value only in a declared unit.
"""

from __future__ import annotations

import json

import anyio
import ifcopenshell
import ifcopenshell.api.pset
import ifcopenshell.api.root
import ifcopenshell.api.unit
import pytest

from flo2_ifc import building, reading
from flo2_ifc.server import build_server

#: 1 Btu/(hr.ft2.°F) in W/(m2.K): NIST SP 811, B.9 (thermal conductance), 5.678 263.
BTU_U_FACTOR = 5.678263


def shop(declare_thermal: bool = False) -> tuple[ifcopenshell.file, ifcopenshell.entity_instance, ifcopenshell.entity_instance]:
    """A project in feet (and degrees) with one window whose U-factor is stored with no unit of its own."""
    m = ifcopenshell.file(schema="IFC4")
    ifcopenshell.api.root.create_entity(m, ifc_class="IfcProject")
    units = [ifcopenshell.api.unit.add_conversion_based_unit(m, name=n) for n in ("foot", "square foot", "cubic foot", "degree")]
    if declare_thermal:
        w = ifcopenshell.api.unit.add_si_unit(m, unit_type="POWERUNIT")
        metre = ifcopenshell.api.unit.add_si_unit(m, unit_type="LENGTHUNIT")
        kelvin = ifcopenshell.api.unit.add_si_unit(m, unit_type="THERMODYNAMICTEMPERATUREUNIT")
        units.append(ifcopenshell.api.unit.add_derived_unit(m, unit_type="THERMALTRANSMITTANCEUNIT", userdefinedtype=None, attributes={w: 1, metre: -2, kelvin: -1}))
    ifcopenshell.api.unit.assign_unit(m, units=units)
    window = ifcopenshell.api.root.create_entity(m, ifc_class="IfcWindow", name="W1")
    pset = ifcopenshell.api.pset.add_pset(m, product=window, name="Pset_WindowCommon")
    ifcopenshell.api.pset.edit_pset(m, pset=pset, properties={"ThermalTransmittance": 1.70348, "Reference": "W1", "IsExternal": True})
    qto = ifcopenshell.api.pset.add_qto(m, product=window, name="Qto_WindowBaseQuantities")
    ifcopenshell.api.pset.edit_qto(m, qto=qto, properties={"Width": 3.0})
    return m, window, pset


# ------------------------------------------------------------ reading


def test_every_declared_unit_is_listed_with_its_symbol():
    m, _, _ = shop(declare_thermal=True)
    u = reading.units(m)
    by_type = {d["unit_type"]: d for d in u["declared"]}
    assert set(by_type) == {"LENGTHUNIT", "AREAUNIT", "VOLUMEUNIT", "PLANEANGLEUNIT", "THERMALTRANSMITTANCEUNIT"}
    assert by_type["LENGTHUNIT"] == {"unit_type": "LENGTHUNIT", "of": "length", "kind": "IfcConversionBasedUnit", "name": "foot", "symbol": "ft", "si_per_unit": 0.3048}
    thermal = by_type["THERMALTRANSMITTANCEUNIT"]
    assert (thermal["kind"], thermal["of"], thermal["symbol"], thermal["si_per_unit"]) == ("IfcDerivedUnit", "thermal transmittance", "W/(m2.K)", 1.0)
    assert u["length"]["symbol"] == "ft"  # the three by name are as before
    assert "thermal transmittance in W/(m2.K)" in u["say"] and "plane angle in degrees (°)" in u["say"]


def test_a_measure_the_model_does_not_declare_is_said_to_have_no_unit():
    m, _, _ = shop()
    say = reading.units(m)["say"]
    assert "No other unit is declared" in say and "a U-factor" in say and "never assume SI" in say


def test_a_u_factor_with_no_unit_declared_says_so():
    m, window, _ = shop()
    extra = reading.info_extra(m, window)
    assert extra["property_units"]["Pset_WindowCommon"]["ThermalTransmittance"] == {"measure": "IfcThermalTransmittanceMeasure", "unit": None, "from": "not declared"}
    # A label and a boolean have no unit type, so they are left out; a quantity takes the project's length unit.
    assert set(extra["property_units"]["Pset_WindowCommon"]) == {"ThermalTransmittance"}
    assert extra["property_units"]["Qto_WindowBaseQuantities"]["Width"] == {"measure": "IfcLengthMeasure", "unit": "ft", "from": "project"}
    assert "NO UNIT DECLARED for Pset_WindowCommon.ThermalTransmittance (IfcThermalTransmittanceMeasure)" in extra["units"]


def test_a_u_factor_takes_the_projects_unit_or_its_own():
    m, window, pset = shop(declare_thermal=True)
    assert reading.info_extra(m, window)["property_units"]["Pset_WindowCommon"]["ThermalTransmittance"] == {"measure": "IfcThermalTransmittanceMeasure", "unit": "W/(m2.K)", "from": "project"}
    # A property that names its own unit is read in it, whatever the project says.
    prop = next(p for p in pset.HasProperties if p.Name == "ThermalTransmittance")
    own = building._make_named_unit(m, "si", "WATT", "POWERUNIT", "")
    prop.Unit = ifcopenshell.api.unit.add_derived_unit(m, unit_type="THERMALTRANSMITTANCEUNIT", userdefinedtype=None, attributes={own: 1})
    assert reading.property_unit(m, prop) == {"measure": "IfcThermalTransmittanceMeasure", "unit": "W", "from": "property"}


def test_every_measure_ifc4_names_maps_to_a_unit_type_it_has():
    m, _, _ = shop()
    _, measures = reading._schema_unit_types(m)
    assert measures["THERMALTRANSMITTANCEUNIT"] == "IfcThermalTransmittanceMeasure"
    assert measures["THERMALCONDUCTANCEUNIT"] == "IfcThermalConductivityMeasure"
    assert measures["LENGTHUNIT"] == "IfcLengthMeasure"
    for unitless in ("IfcLabel", "IfcBoolean", "IfcCountMeasure", "IfcRatioMeasure", "IfcPositiveRatioMeasure", "IfcReal"):
        assert reading.measure_unit_type(m, unitless) is None, unitless


# ------------------------------------------------------------ declaring


def test_a_btu_u_factor_unit_is_declared_with_exact_factors():
    m, window, _ = shop()
    out = building.add_unit(
        m,
        "THERMALTRANSMITTANCEUNIT",
        parts=[{"unit": "btu"}, {"unit": "hour", "exponent": -1}, {"unit": "square foot", "exponent": -1}, {"unit": "fahrenheit", "exponent": -1}],
    )
    assert out["declared"]["symbol"] == "btu/(hr.ft2.°F)"
    assert out["declared"]["si_per_unit"] == pytest.approx(BTU_U_FACTOR, rel=1e-6)
    # The stored 1.70348 is now read in Btu/(hr.ft2.°F): what the model says, in the unit it declares.
    assert reading.info_extra(m, window)["property_units"]["Pset_WindowCommon"]["ThermalTransmittance"]["unit"] == "btu/(hr.ft2.°F)"


@pytest.mark.parametrize(
    ("unit_type", "kwargs", "symbol", "si"),
    [
        ("THERMALTRANSMITTANCEUNIT", {"parts": [{"unit": "WATT"}, {"unit": "METRE", "exponent": -2}, {"unit": "KELVIN", "exponent": -1}]}, "W/(m2.K)", 1.0),
        ("THERMODYNAMICTEMPERATUREUNIT", {"unit": "fahrenheit"}, "°F", 5 / 9),
        ("THERMODYNAMICTEMPERATUREUNIT", {"unit": "DEGREE_CELSIUS"}, "°C", 1.0),
        ("MASSUNIT", {"unit": "pound"}, "lb", 453.59237),  # IfcOpenShell scales a mass to the gram, the SI unit IFC names
        ("POWERUNIT", {"unit": "WATT", "prefix": "KILO"}, "kW", 1000.0),
        ("MONETARYUNIT", {"unit": "usd"}, "USD", None),
    ],
)
def test_a_unit_is_declared(unit_type, kwargs, symbol, si):
    m, _, _ = shop()
    out = building.add_unit(m, unit_type, **kwargs)
    assert out["declared"]["symbol"] == symbol
    assert out["declared"]["si_per_unit"] == (pytest.approx(si) if si is not None else None)
    assert out["declared"]["unit_type"] == unit_type
    assert unit_type in {d["unit_type"] for d in reading.units(m)["declared"]}


@pytest.mark.parametrize(
    ("unit_type", "kwargs", "reason"),
    [
        ("LENGTHUNIT", {"unit": "METRE"}, "already declares length in ft"),
        ("THERMALRESISTANCEUNIT", {"parts": [{"unit": "WATT"}, {"unit": "METRE", "exponent": -2}]}, "do not make a thermal resistance unit"),
        ("THERMALTRANSMITTANCEUNIT", {"unit": "WATT"}, "is a derived unit"),
        ("POWERUNIT", {"unit": "foot"}, "not POWERUNIT"),
        ("POWERUNIT", {"unit": "horsepower"}, "not a unit flo2-ifc can declare"),
        ("POWERUNIT", {"unit": "WATT", "prefix": "MEGAA"}, "not an SI prefix"),
        ("NOSUCHUNIT", {"unit": "WATT"}, "not a unit type"),
        ("MONETARYUNIT", {"unit": "dollars"}, "ISO 4217"),
        ("THERMALTRANSMITTANCEUNIT", {"parts": [{"unit": "WATT", "exponent": 0}]}, "other than 0"),
    ],
)
def test_what_is_not_a_unit_is_refused_and_nothing_changes(unit_type, kwargs, reason):
    m, _, _ = shop()
    before = len(list(m))
    with pytest.raises(ValueError, match=reason):
        building.add_unit(m, unit_type, **kwargs)
    assert len(list(m)) == before


# ------------------------------------------------------------ through the server


def _answer(result):
    text = "".join(b.text for b in result.content)
    return json.loads(text) if not result.is_error else text


def test_a_measured_value_is_written_only_in_a_declared_unit(tmp_path):
    m, window, pset = shop()
    path = tmp_path / "shop.ifc"
    m.write(str(path))
    server = build_server()

    async def run():
        await server.call_tool("ifc_load", {"path": str(path)})
        edit = {"function_path": "pset.edit_pset", "params": json.dumps({"pset": pset.id(), "properties": json.dumps({"ThermalTransmittance": 0.30})})}
        refused = _answer(await server.call_tool("ifc_edit", edit))
        info = _answer(await server.call_tool("ifc_info", {"element_id": window.id()}))
        declared = _answer(
            await server.call_tool(
                "ifc_add_unit",
                {"unit_type": "THERMALTRANSMITTANCEUNIT", "parts": [{"unit": "btu"}, {"unit": "hour", "exponent": -1}, {"unit": "square foot", "exponent": -1}, {"unit": "fahrenheit", "exponent": -1}]},
            )
        )
        written = _answer(await server.call_tool("ifc_edit", edit))
        label = _answer(await server.call_tool("ifc_edit", {"function_path": "pset.edit_pset", "params": json.dumps({"pset": pset.id(), "properties": json.dumps({"Reference": "W2"})})}))
        after = _answer(await server.call_tool("ifc_info", {"element_id": window.GlobalId}))
        units = _answer(await server.call_tool("ifc_units", {}))
        return refused, info, declared, written, label, after, units

    refused, info, declared, written, label, after, units = anyio.run(run)
    assert refused["ok"] is False and "Not written: Pset_WindowCommon.ThermalTransmittance (IfcThermalTransmittanceMeasure)" in refused["error"]
    assert "Nothing was changed" in refused["error"] and "ifc_add_unit" in refused["error"]
    assert info["property_sets"]["Pset_WindowCommon"]["ThermalTransmittance"] == 1.70348  # the refused edit wrote nothing
    assert info["property_units"]["Pset_WindowCommon"]["ThermalTransmittance"]["from"] == "not declared"
    assert declared["declared"]["symbol"] == "btu/(hr.ft2.°F)"
    assert written == {"ok": True, "result": None}
    assert label == {"ok": True, "result": None}  # a value with no unit type is written as before
    assert after["property_sets"]["Pset_WindowCommon"]["ThermalTransmittance"] == 0.30
    assert after["property_units"]["Pset_WindowCommon"]["ThermalTransmittance"] == {"measure": "IfcThermalTransmittanceMeasure", "unit": "btu/(hr.ft2.°F)", "from": "project"}
    assert "NO UNIT DECLARED" not in after["units"]
    assert "THERMALTRANSMITTANCEUNIT" in {d["unit_type"] for d in units["declared"]}
