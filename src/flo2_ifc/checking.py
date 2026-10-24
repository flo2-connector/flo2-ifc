"""Checking a model against an IDS, and writing an IDS, with IfcOpenShell's own IDS checker (ifctester).

An IDS (buildingSMART's Information Delivery Specification, version 1.0) is a
list of what a model must contain, in a form a program checks: "every space
has a name and a NetFloorArea", "every wall says its fire rating". This module
does the two things a chat needs with one, both through ifctester, never a
checker of our own:

- ``check_ids``: run an IDS file against the model open in the session. Each
  specification is reported passed or failed, with how many elements it
  applies to and the failing elements by GlobalId with ifctester's own reason.
  ifctester's own HTML report (for the person) and JSON report (for a chat or
  a calculation) are written where the caller says. The reply cites both
  files by name and sha256.
- ``write_ids``: build an IDS from a structured description (specifications,
  each with its applicability and its requirements) with ifctester's API,
  check it against the IDS 1.0 XSD that ifctester ships, and write it. An
  invalid one is refused with the schema's reason, and nothing is written.
  A chat never writes IDS XML by hand.

Both keep to one call's time (``reading.SECONDS``): the check runs in a forked
child that is stopped at the limit, and a check that does not finish says how
far it got and where to go on from (``start``), as ifc_validate does.
"""

from __future__ import annotations

import json
import os
import select as _io_select
import time
from typing import Any

import ifcopenshell

from flo2_ifc import geometry, reading

#: The IFC versions an IDS 1.0 specification may name.
IFC_VERSIONS = ("IFC2X3", "IFC4", "IFC4X3_ADD2")
#: How many failing elements one specification lists in a reply, at most, before
#: it says the rest by count (the reports hold them all, up to ifctester's own
#: limits in its HTML).
MAX_FAILING_KEPT = 1000
#: The share of the call's time the check may use, its reports included.
USE_OF_BUDGET = 0.9
#: ifctester's two reports take about this many times as long to write as
#: checking the specifications they report (measured on 1,200 walls: checking
#: 0.28 s, JSON 0.82 s, HTML 0.68 s). The check starts no new specification
#: once the reports of those it finished might not be written in time.
REPORT_FACTOR = 6.0

FACETS = ("entity", "attribute", "property", "classification", "material", "part_of")
CARDINALITIES = ("required", "optional", "prohibited")


class IdsRefused(ValueError):
    """An IDS that cannot be used, with the reason a chat can correct."""


# ---------------------------------------------------------------- reading an IDS


def _schema_reason(err: Exception) -> str:
    """The XSD's own words for why an IDS is invalid, short."""
    inner = getattr(err, "xml_error", err)
    reason = getattr(inner, "reason", None) or str(inner)
    path = getattr(inner, "path", None)
    text = f"{reason}" + (f" (at {path})" if path else "")
    return " ".join(text.split())[:600]


def open_ids(path: str):
    """The IDS at `path`, checked against the IDS 1.0 XSD first; IdsRefused if
    it is not a valid IDS, with the schema's reason."""
    from ifctester import ids as ids_mod

    try:
        return ids_mod.open(path, validate=True)
    except ids_mod.IdsXmlValidationError as e:
        raise IdsRefused(f"it is not a valid IDS 1.0 file: {_schema_reason(e)}") from e
    except Exception as e:  # not XML at all, or not an IDS
        raise IdsRefused(f"it could not be read as an IDS file: {' '.join(str(e).split())[:600] or type(e).__name__}") from e


def _file_entry(path: str | None) -> dict[str, Any]:
    return {"file": os.path.basename(path) if path else None, "sha256": geometry.file_sha256(path)}


def _element(e: ifcopenshell.entity_instance) -> dict[str, Any]:
    return {"global_id": getattr(e, "GlobalId", None), "id": e.id(), "class": e.is_a(), "name": getattr(e, "Name", None)}


def _status(spec) -> str:
    if spec.is_ifc_version is False:
        return "not_this_ifc_version"
    if spec.status is True:
        return "passed"
    if spec.status is False:
        return "failed"
    return "not_checked"


def _cardinality(spec) -> str:
    from ifctester.reporter import get_cardinality

    return get_cardinality(spec)


def _spec_summary(index: int, spec) -> dict[str, Any]:
    """One specification's result, as plain data: what it asks, how many
    elements it applies to, and every failing element with ifctester's reason."""
    failing: dict[int, dict[str, Any]] = {}
    requirements = []
    for facet in spec.requirements:
        says = facet.to_string("requirement", spec, facet)
        requirements.append({"facet": type(facet).__name__, "says": says, "passed": facet.status is True, "failed": len(facet.failures)})
        for f in facet.failures:
            e = f["element"]
            entry = failing.setdefault(e.id(), {**_element(e), "reasons": []})
            entry["reasons"].append(f"{says}: {f['reason']}")
    applicable = len(spec.applicable_entities)
    cardinality = _cardinality(spec)
    out: dict[str, Any] = {
        "index": index,
        "name": spec.name,
        "status": _status(spec),
        "cardinality": cardinality,
        "applies_to": [a.to_string("applicability", spec) for a in spec.applicability],
        "applicable": applicable,
        "failed_elements": len(failing),
        "passed_elements": applicable - len(failing) if spec.maxOccurs != 0 else 0,
        "requirements": requirements,
        "failing": list(failing.values())[:MAX_FAILING_KEPT],
    }
    if spec.description:
        out["description"] = spec.description
    if cardinality == "required" and applicable == 0:
        out["why"] = "the specification is required, and no element in the model is one it applies to"
    elif cardinality == "prohibited" and applicable:
        out["why"] = f"the specification prohibits what it applies to, and {applicable} element(s) are"
        out["failing"] = [{**_element(e), "reasons": ["prohibited by this specification"]} for e in spec.applicable_entities[:MAX_FAILING_KEPT]]
        out["failed_elements"] = applicable
    elif cardinality == "optional" and applicable == 0:
        out["status"] = "passed"
        out["note"] = "optional, and no element in the model is one it applies to"
    if spec.is_ifc_version is False:
        out["why"] = f"it is for {', '.join(spec.ifcVersion)}, and the model is {spec.ifc_file_schema if hasattr(spec, 'ifc_file_schema') else 'another version'}"
    return out


# ---------------------------------------------------------------- checking a model


def _run_child(model, ids_path: str, model_path: str | None, start: int, report_path: str | None, budget: float) -> tuple[list[dict[str, Any]], dict[str, Any] | None, bool]:
    """Check specifications from `start` in a forked child, which streams one
    line per specification and a last line when its reports are written.
    Returns the specifications' summaries, the last line (None if it never
    came), and whether the child was stopped at the time limit."""
    read_end, write_end = os.pipe()
    pid = os.fork()
    if pid == 0:  # the child
        code = 0
        try:
            os.close(read_end)
            w = os.fdopen(write_end, "wb", buffering=0)
            started = time.monotonic()
            ids = open_ids(ids_path)
            ids.filepath = model_path
            ids.filename = os.path.basename(model_path) if model_path else None
            from ifctester import facet as facet_mod  # noqa: F401 - imported before the clock matters

            done = []
            stopped_early = False
            checking_took = 0.0
            longest = 0.0
            for i, spec in enumerate(ids.specifications):
                if i < start:
                    continue
                elapsed = time.monotonic() - started
                if done and elapsed + longest + (REPORT_FACTOR if report_path else 0.0) * (checking_took + longest) > budget * USE_OF_BUDGET:
                    stopped_early = True
                    break
                t = time.monotonic()
                spec.reset_status()
                spec.check_ifc_version(model)
                spec.ifc_file_schema = model.schema_identifier
                spec.validate(model, should_filter_version=True)
                took = time.monotonic() - t
                checking_took += took
                longest = max(longest, took)
                done.append(spec)
                w.write((json.dumps({"spec": _spec_summary(i, spec)}, default=str) + "\n").encode())
            wrote: dict[str, Any] = {}
            if report_path and done:
                from ifctester import reporter

                ids.specifications = done
                j = reporter.Json(ids)
                j.report()
                j.to_file(f"{report_path}.json")
                h = reporter.Html(ids)
                h.report()
                h.to_file(f"{report_path}.html")
                wrote = {"json": f"{report_path}.json", "html": f"{report_path}.html"}
            w.write((json.dumps({"end": {"stopped_early": stopped_early, "total": len(ids.specifications), "wrote": wrote}}) + "\n").encode())
        except BaseException as e:  # noqa: BLE001 - the child reports any failure
            code = 1
            try:
                w.write((json.dumps({"failed": f"{type(e).__name__}: {' '.join(str(e).split())[:600]}", "refused": isinstance(e, IdsRefused)}) + "\n").encode())
            except BaseException:  # noqa: BLE001
                pass
        finally:
            os._exit(code)
    os.close(write_end)
    buf = b""
    deadline = time.monotonic() + max(0.0, budget)
    timed_out = False
    with os.fdopen(read_end, "rb", buffering=0) as r:
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                timed_out = True
                break
            ready, _, _ = _io_select.select([r], [], [], left)
            if not ready:
                continue
            chunk = r.read(65536)
            if not chunk:
                break
            buf += chunk
    if timed_out:
        try:
            os.kill(pid, 9)
        except ProcessLookupError:
            pass
    os.waitpid(pid, 0)
    specs: list[dict[str, Any]] = []
    end: dict[str, Any] | None = None
    for line in buf.split(b"\n"):
        if not line.strip():
            continue
        try:
            said = json.loads(line)
        except json.JSONDecodeError:
            continue  # a line cut off when the child was stopped
        if "spec" in said:
            specs.append(said["spec"])
        elif "end" in said:
            end = said["end"]
        elif "failed" in said:
            if said.get("refused"):
                raise IdsRefused(said["failed"].split(": ", 1)[-1])
            raise RuntimeError(f"the IDS check could not run: {said['failed']}")
    return specs, end, timed_out


def check_ids(
    model: ifcopenshell.file,
    model_path: str | None,
    ids_path: str,
    report_path: str = "",
    start: int = 0,
    offset: int = 0,
    limit: int = 20,
    edits: int = 0,
    seconds: float | None = None,
) -> dict[str, Any]:
    """Check the model open in the session against the IDS at `ids_path`."""
    if start < 0:
        raise ValueError("start is a specification's position, 0 or more")
    if offset < 0 or limit < 0:
        raise ValueError("offset and limit are 0 or more")
    limit = min(limit, reading.MAX_LIMIT)
    if not os.path.isfile(ids_path):
        raise IdsRefused(f"there is no IDS file at {ids_path}")
    ids = open_ids(ids_path)  # refuse an invalid one here, before forking
    total = len(ids.specifications)
    if total == 0:
        raise IdsRefused("the IDS holds no specification, so there is nothing to check")
    if start >= total:
        raise ValueError(f"start is {start}, and the IDS holds {total} specification(s), numbered from 0")
    started = time.monotonic()
    budget = reading.SECONDS if seconds is None else seconds
    if hasattr(os, "fork"):
        specs, end, timed_out = _run_child(model, ids_path, model_path, start, report_path or None, budget)
    else:  # pragma: no cover - flo2-ifc runs on Linux
        raise RuntimeError("the IDS check needs os.fork")
    checked_to = start + len(specs)
    complete = checked_to >= total and end is not None
    failed = [s for s in specs if s["status"] == "failed"]
    out: dict[str, Any] = {
        "ids": {**_file_entry(ids_path), "title": ids.info.get("title"), "specifications": total},
        "model": _file_entry(model_path),
        "passed": not failed if specs else None,
        "complete": complete,
        "checked": {"from": start, "to": checked_to, "of": total},
        "next_start": None if checked_to >= total else checked_to,
        "summary": {
            "passed": sum(1 for s in specs if s["status"] == "passed"),
            "failed": len(failed),
            "not_this_ifc_version": sum(1 for s in specs if s["status"] == "not_this_ifc_version"),
        },
        "specifications": [],
        "checked_by": f"ifctester {_ifctester_version()} (IfcOpenShell's IDS checker), ifcopenshell {ifcopenshell.version}, flo2-ifc {geometry._version()}",
    }
    # Paging: each specification lists its failing elements from `offset`, at most `limit`.
    for s in specs:
        failing = s["failing"]
        page = failing[offset : offset + limit]
        entry = {k: v for k, v in s.items() if k != "failing"}
        entry["failing"] = page
        if len(failing) > offset + limit or s["failed_elements"] > len(failing):
            entry["failing_page"] = {"offset": offset, "listed": len(page), "of": s["failed_elements"], "next_offset": offset + limit if s["failed_elements"] > offset + limit else None}
        out["specifications"].append(entry)
    if not model_path:
        out["model"]["note"] = "This model was made in the session (ifc_new) and has no file."
    elif edits:
        out["model"]["edited"] = (
            f"The session's copy was changed {edits} time(s) after it was opened, so this check is of the EDITED copy, not of the file the sha256 names."
        )
    if end and end.get("wrote"):
        out["reports"] = end["wrote"]
    elif report_path:
        out["reports"] = {}
        out["reports_note"] = "No report was written: the check did not finish a specification in this call's time." if not specs else "No report was written: the check was stopped before it could write them."
    if not complete:
        why = "the time limit stopped it" if timed_out else f"it starts no new specification once the rest of the call's {budget:.0f} s may not hold it and its reports"
        out["note"] = (
            f"Partial: specifications {start} to {checked_to - 1 if specs else start - 1} of {total} (numbered from 0) were checked ({why}). "
            f"Call again with start {checked_to} to check on; `passed` covers only what was checked."
            if specs
            else f"No specification finished in {budget:.0f} s: specification {start} is too large for one call on this model."
        )
    out["seconds"] = round(time.monotonic() - started, 1)
    return out


def _ifctester_version() -> str:
    try:
        from importlib.metadata import version

        return version("ifctester")
    except Exception:  # noqa: BLE001
        return "unknown"


# ---------------------------------------------------------------- writing an IDS


def _value(raw: Any, where: str, numeric_base: bool = False):
    """A facet's value: plain text, or a restriction (pattern, one_of, bounds, lengths)."""
    from ifctester import facet as facet_mod

    if raw is None:
        return None
    if isinstance(raw, bool):
        return "TRUE" if raw else "FALSE"
    if isinstance(raw, (int, float)):
        return str(raw)
    if isinstance(raw, str):
        if not raw.strip():
            raise IdsRefused(f"{where}: a value is not empty text; leave it out to ask only that it is present")
        return raw
    if isinstance(raw, list):
        raw = {"one_of": raw}
    if not isinstance(raw, dict):
        raise IdsRefused(f"{where}: a value is text, a number, or an object such as {{\"pattern\": \"[A-Z]{{2}}[0-9]+\"}}, {{\"one_of\": [\"EI60\", \"EI90\"]}} or {{\"at_least\": 5}}")
    names = {
        "pattern": "pattern",
        "one_of": "enumeration",
        "at_least": "minInclusive",
        "at_most": "maxInclusive",
        "more_than": "minExclusive",
        "less_than": "maxExclusive",
        "length": "length",
        "min_length": "minLength",
        "max_length": "maxLength",
    }
    unknown = sorted(set(raw) - set(names) - {"base"})
    if unknown:
        raise IdsRefused(f"{where}: a value restriction takes {', '.join(names)}, not {', '.join(unknown)}")
    options: dict[str, Any] = {}
    for k, v in raw.items():
        if k == "base":
            continue
        if k == "one_of":
            if not isinstance(v, list) or not v:
                raise IdsRefused(f"{where}: one_of is a list of the values allowed")
            options["enumeration"] = [str(x) for x in v]
        elif k == "pattern":
            if not isinstance(v, str) or not v:
                raise IdsRefused(f"{where}: pattern is an XML Schema regular expression, as text")
            options["pattern"] = v
        else:
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                raise IdsRefused(f"{where}: {k} is a number")
            options[names[k]] = v
    if not options:
        raise IdsRefused(f"{where}: an empty restriction asks for nothing")
    bounds = {"minInclusive", "maxInclusive", "minExclusive", "maxExclusive"}
    base = raw.get("base") or ("double" if (bounds & set(options)) or numeric_base else "string")
    if base not in ("string", "double", "integer", "boolean"):
        raise IdsRefused(f"{where}: base is string, double, integer or boolean")
    return facet_mod.Restriction(options=options, base=base)


def _cardinality_of(raw: dict[str, Any], where: str, default: str = "required") -> str:
    c = raw.get("cardinality", default)
    if c not in CARDINALITIES:
        raise IdsRefused(f"{where}: cardinality is required, optional or prohibited, not {c!r}")
    return c


def _text(raw: dict[str, Any], key: str, where: str, required: bool = False) -> str | None:
    v = raw.get(key)
    if v is None or v == "":
        if required:
            raise IdsRefused(f"{where}: `{key}` is needed")
        return None
    if not isinstance(v, str):
        raise IdsRefused(f"{where}: `{key}` is text")
    return v


def _facet(raw: Any, where: str, clause: str):
    """One facet of a specification, from its description."""
    from ifctester import facet as facet_mod

    if not isinstance(raw, dict):
        raise IdsRefused(f"{where}: a facet is an object such as {{\"facet\": \"entity\", \"name\": \"IfcWall\"}}")
    kind = raw.get("facet")
    if kind not in FACETS:
        raise IdsRefused(f"{where}: `facet` is one of {', '.join(FACETS)}, not {kind!r}")
    allowed = {
        "entity": {"name", "predefined_type"},
        "attribute": {"name", "value", "cardinality", "instructions"},
        "property": {"property_set", "property", "value", "data_type", "cardinality", "instructions"},
        "classification": {"system", "value", "uri", "cardinality", "instructions"},
        "material": {"value", "uri", "cardinality", "instructions"},
        "part_of": {"entity", "predefined_type", "relation", "cardinality", "instructions"},
    }[kind] | {"facet"}
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise IdsRefused(f"{where}: a {kind} facet takes {', '.join(sorted(allowed - {'facet'}))}, not {', '.join(unknown)}")
    if clause == "applicability" and "cardinality" in raw and raw["cardinality"] != "required":
        raise IdsRefused(f"{where}: an applicability facet takes no cardinality; say it on the specification instead")
    instructions = _text(raw, "instructions", where)
    if kind == "entity":
        name = _text(raw, "name", where, required=True)
        return facet_mod.Entity(name=name.upper(), predefinedType=_value(raw.get("predefined_type"), where))
    cardinality = _cardinality_of(raw, where) if clause == "requirements" else "required"
    if kind == "attribute":
        return facet_mod.Attribute(name=_text(raw, "name", where, required=True), value=_value(raw.get("value"), where), cardinality=cardinality, instructions=instructions)
    if kind == "property":
        data_type = _text(raw, "data_type", where)
        numeric = bool(data_type) and data_type.upper() in ("IFCREAL", "IFCAREAMEASURE", "IFCLENGTHMEASURE", "IFCVOLUMEMEASURE", "IFCPOSITIVELENGTHMEASURE", "IFCINTEGER", "IFCCOUNTMEASURE", "IFCMASSMEASURE")
        return facet_mod.Property(
            propertySet=_value(_text(raw, "property_set", where, required=True), where),
            baseName=_value(_text(raw, "property", where, required=True), where),
            value=_value(raw.get("value"), where, numeric_base=numeric),
            dataType=data_type.upper() if data_type else None,
            cardinality=cardinality,
            instructions=instructions,
        )
    if kind == "classification":
        system, value = raw.get("system"), raw.get("value")
        return facet_mod.Classification(value=_value(value, where), system=_value(system, where), uri=_text(raw, "uri", where), cardinality=cardinality, instructions=instructions)
    if kind == "material":
        return facet_mod.Material(value=_value(raw.get("value"), where), uri=_text(raw, "uri", where), cardinality=cardinality, instructions=instructions)
    # part_of
    relation = _text(raw, "relation", where)
    return facet_mod.PartOf(
        name=_text(raw, "entity", where, required=True).upper(),
        predefinedType=_value(raw.get("predefined_type"), where),
        relation=relation.upper() if relation else None,
        cardinality=cardinality,
        instructions=instructions,
    )


def build_ids(description: dict[str, Any]):
    """An ifctester Ids from its description; IdsRefused, naming the place, if any part is wrong."""
    from ifctester import ids as ids_mod

    if not isinstance(description, dict):
        raise IdsRefused("the description is an object with `title` and `specifications`")
    top = {"title", "description", "author", "version", "date", "purpose", "milestone", "copyright", "ifc_version", "specifications"}
    unknown = sorted(set(description) - top)
    if unknown:
        raise IdsRefused(f"the description takes {', '.join(sorted(top))}, not {', '.join(unknown)}")
    title = _text(description, "title", "the IDS", required=True)
    author = _text(description, "author", "the IDS")
    if author and "@" not in author:
        raise IdsRefused("the IDS: `author` is an email address in IDS 1.0; leave it out otherwise")
    out = ids_mod.Ids(
        title=title,
        description=_text(description, "description", "the IDS"),
        author=author,
        version=_text(description, "version", "the IDS"),
        date=_text(description, "date", "the IDS"),
        purpose=_text(description, "purpose", "the IDS"),
        milestone=_text(description, "milestone", "the IDS"),
        copyright=_text(description, "copyright", "the IDS"),
    )
    default_versions = _versions(description.get("ifc_version"), "the IDS") or list(IFC_VERSIONS)
    specs = description.get("specifications")
    if not isinstance(specs, list) or not specs:
        raise IdsRefused("the IDS: `specifications` is a list of at least one specification")
    for n, raw in enumerate(specs):
        where = f"specification {n}"
        if not isinstance(raw, dict):
            raise IdsRefused(f"{where}: a specification is an object with name, applicability and requirements")
        allowed = {"name", "description", "instructions", "identifier", "ifc_version", "cardinality", "applicability", "requirements"}
        unknown = sorted(set(raw) - allowed)
        if unknown:
            raise IdsRefused(f"{where}: a specification takes {', '.join(sorted(allowed))}, not {', '.join(unknown)}")
        name = _text(raw, "name", where, required=True)
        where = f'specification {n} ("{name}")'
        cardinality = _cardinality_of(raw, where, default="required")
        min_occurs, max_occurs = {"required": (1, "unbounded"), "optional": (0, "unbounded"), "prohibited": (0, 0)}[cardinality]
        spec = ids_mod.Specification(
            name=name,
            minOccurs=min_occurs,
            maxOccurs=max_occurs,
            ifcVersion=_versions(raw.get("ifc_version"), where) or default_versions,
            identifier=_text(raw, "identifier", where),
            description=_text(raw, "description", where),
            instructions=_text(raw, "instructions", where),
        )
        applicability = raw.get("applicability")
        if not isinstance(applicability, list) or not applicability:
            raise IdsRefused(f"{where}: `applicability` is a list of facets saying which elements it is about, usually one entity facet such as {{\"facet\": \"entity\", \"name\": \"IfcSpace\"}}")
        for k, f in enumerate(applicability):
            spec.applicability.append(_facet(f, f"{where}, applicability {k}", "applicability"))
        requirements = raw.get("requirements", [])
        if not isinstance(requirements, list):
            raise IdsRefused(f"{where}: `requirements` is a list of facets")
        if not requirements and cardinality != "prohibited":
            raise IdsRefused(f"{where}: `requirements` is empty; say what the elements must have, or make the specification prohibited")
        for k, f in enumerate(requirements):
            spec.requirements.append(_facet(f, f"{where}, requirement {k}", "requirements"))
        out.specifications.append(spec)
    return out


def _versions(raw: Any, where: str) -> list[str] | None:
    if raw is None:
        return None
    values = [raw] if isinstance(raw, str) else raw
    if not isinstance(values, list) or not values or not all(isinstance(v, str) for v in values):
        raise IdsRefused(f"{where}: ifc_version is one or more of {', '.join(IFC_VERSIONS)}")
    up = [v.upper() for v in values]
    bad = [v for v in up if v not in IFC_VERSIONS]
    if bad:
        raise IdsRefused(f"{where}: ifc_version is one or more of {', '.join(IFC_VERSIONS)}, not {', '.join(bad)}")
    return up


def _remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def write_ids(description: dict[str, Any] | str, output_path: str) -> dict[str, Any]:
    """Build an IDS from `description`, check it against the IDS 1.0 XSD, and
    write it to `output_path`.ids. Refused, with nothing written, if it is not
    a valid IDS."""
    from ifctester import ids as ids_mod

    if isinstance(description, str):
        try:
            description = json.loads(description)
        except json.JSONDecodeError as e:
            raise IdsRefused(f"the description is not JSON: {e}") from e
    built = build_ids(description)
    path = f"{output_path}.ids"
    # Written by ifctester's own writer, then checked as the file it wrote: the
    # XSD's QName values (base="xs:string") need the namespace declarations
    # the file carries, which an in-memory ElementTree drops.
    try:
        built.to_xml(path)
    except Exception as e:  # the schema refused to encode it
        _remove(path)
        raise IdsRefused(f"it does not make a valid IDS 1.0 file: {_schema_reason(e)}") from e
    errors = [_schema_reason(e) for e in ids_mod.get_schema().iter_errors(path)]
    if errors:
        _remove(path)
        raise IdsRefused(f"it does not make a valid IDS 1.0 file: {errors[0]}" + (f" (and {len(errors) - 1} more)" if len(errors) > 1 else ""))
    # It reads back as the same IDS, through the checker that will use it.
    try:
        back = ids_mod.open(path, validate=True)
    except Exception as e:
        _remove(path)
        raise IdsRefused(f"it did not read back as a valid IDS: {_schema_reason(e)}") from e
    if len(back.specifications) != len(built.specifications):
        _remove(path)
        raise IdsRefused("it did not read back with the same specifications")
    import hashlib

    with open(path, "rb") as f:
        data = f.read()
    return {
        "written": path,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "valid": True,
        "checked_against": "the IDS 1.0 XSD ifctester ships (http://standards.buildingsmart.org/IDS/1.0/ids.xsd)",
        "title": built.info.get("title"),
        "specifications": [
            {
                "name": s.name,
                "cardinality": _cardinality(s),
                "ifc_version": s.ifcVersion,
                "applies_to": [a.to_string("applicability", s) for a in s.applicability],
                "requires": [r.to_string("requirement", s, r) for r in s.requirements],
            }
            for s in built.specifications
        ],
        "written_by": f"ifctester {_ifctester_version()} (IfcOpenShell's IDS API), flo2-ifc {geometry._version()}",
    }
