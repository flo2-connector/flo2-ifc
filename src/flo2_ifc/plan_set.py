"""A plan set: numbered sheets drawn to scale from the model, bound into ONE PDF (since 0.13.0).

flo2's design 1a97fc1f1de4d5d2, req:a-chat-pulls-a-pdf-plan-set-of-a-designs-building-model (2026-11-09): a person
who had edited a building model through chats asked to "pull a PDF planset". This draws one from the model as it
stands, the session's edits included.

Each sheet is the drawing ifc_draw makes (drawing.sheet: the same cut, the same scale bars and the same title block),
with its sheet number, large, and the set's name in a box at the right of its title block, and its title over what it
shows. With no sheets given, the set is the conventional one:

- A-001, the COVER SHEET: the set's name (by default the project's, as the model states it), the project and
  building as the model names them, the model file and its sha256, the date drawn, and an index of every sheet by
  number, title and scale, with any sheet not drawn and why;
- A-101 on, a PLAN of every storey, lowest first, at its 1.2 m cut; a storey that holds nothing with a shape gets no
  plan, and the reply says so;
- A-201 to A-204, the north, east, south and west ELEVATIONS;
- A-301 and A-302, a north-south and an east-west SECTION through the middle of the building: the middle of the box
  of every element of every storey, the line running a metre past it each way;
- the plans, the elevations and the sections each at ONE scale, the largest that fits every sheet of the kind (the
  scale each would be drawn at alone, the smallest of them), so a set's plans compare at a glance.

Or the caller gives the sheets: each one ifc_draw's own arguments, with its number and its title, drawn in the order
given, each at its own scale or the set's, or the largest that fits it. One paper for the whole set.

THE PDF is bound with cairo's own PDF surface (cairocffi, which cairosvg draws with). Each sheet's SVG is drawn by
cairosvg, as ifc_draw renders its pictures, onto a cairo recording surface as soon as the sheet is drawn (page_of),
and binding replays them, a page each (bind): vector, its words as text in DejaVu Sans, one page per sheet, each page
the size and way round of its sheet. The PDF's title is the set's name.

TIME. A set keeps to one call's time (reading.SECONDS), as ifc_draw does. What it has drawn is kept for the session,
each sheet's SVG in memory, by the model, its generation (geometry.changed(): any edit, shape or reload), the
session's edit count and the set's own arguments (not its output path), so the same call again goes on from the next
sheet, and an edit starts the set again. A sheet is not started when less time is left than the slowest sheet of
the set took. Nothing is written until every sheet is drawn or refused. Reads the model only; writes
<output_path>.pdf and <output_path>-<number>.svg.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import time
import weakref
from collections import OrderedDict
from typing import Any

import ifcopenshell

from flo2_ifc import drawing, geometry, reading

#: The cover sheet's number and title.
COVER = ("A-001", "Cover sheet")
#: Where each kind of sheet is numbered from: A-101.. plans, A-201.. elevations, A-301.. sections.
SERIES = {"plan": 100, "elevation": 200, "section": 300}
#: The default set's elevations, in the order they are numbered.
SIDES = ("north", "east", "south", "west")
#: How far past the building a default section's line runs, each way, in metres.
SECTION_PAST_M = 1.0
#: ifc_draw's own arguments, which a sheet may give.
SHEET_ARGUMENTS = ("view", "storey", "cut_height", "line", "through", "direction", "gridline", "looking", "side", "around", "margin", "depth", "highlight", "scale", "title")
#: ifc_draw's arguments a sheet may not give, and why.
SET_ONLY = {
    "paper": "a plan set has one paper for the whole set: give it once, as the set's `paper`",
    "output_path": "the set says where it is written, once, as its `output_path`",
    "png_px": "a plan set is a PDF and an SVG per sheet, with no picture",
}
#: A sheet number: letters, digits, dots, dashes and underscores, starting with a letter or digit, at most 16.
NUMBER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,15}")
#: The most sheets one set may have, the cover not counted.
MAX_SHEETS = 60
#: Sets kept per session (each its sheets' SVG text), the latest used last.
KEPT = 4
#: Seconds a call keeps for binding the set and writing it: at least this, and this per MB of the sheets' SVG. The
#: replay measured 0.43 s a MB on one laptop core (Clinic_Architectural, 4.4 MB, 1.9 s, 2026-11-09); flo2.io runs a
#: session about 2.2 times slower (README.md, "Checking what the edits touched"): a second a MB, and one to spare.
BIND_SECONDS = (1.0, 1.0)

_sets: OrderedDict[tuple, dict[str, Any]] = OrderedDict()
_owners: dict[int, weakref.finalize] = {}


def _forget(model_key: int) -> None:
    """A model was freed: drop its sets before its id is reused (as geometry._forget_model does its meshes)."""
    _owners.pop(model_key, None)
    for k in [k for k in _sets if k[0] == model_key]:
        _sets.pop(k, None)


def _state(model: ifcopenshell.file, key: tuple) -> dict[str, Any]:
    if key in _sets:
        _sets.move_to_end(key)
        return _sets[key]
    for k in [k for k in _sets if k[0] == key[0] and k[1:3] != key[1:3]]:
        _sets.pop(k, None)  # the same model before an edit: never continued
    state: dict[str, Any] = {"planned": None, "fitted": False, "fits": {}, "done": {}, "failed": {}, "notes": [], "slowest": 0.0}
    _sets[key] = state
    if key[0] not in _owners:
        _owners[key[0]] = weakref.finalize(model, _forget, key[0])
    while len(_sets) > KEPT:
        _sets.popitem(last=False)
    return state


# ---------------------------------------------------------------- what the model names


def names(model: ifcopenshell.file) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """The project and the buildings, as the model names them (Name and LongName, unchanged)."""

    def of(e: ifcopenshell.entity_instance) -> dict[str, Any]:
        return {"name": e.Name, "long_name": getattr(e, "LongName", None), "global_id": e.GlobalId}

    projects = model.by_type("IfcProject")
    return (of(projects[0]) if projects else None), [of(b) for b in model.by_type("IfcBuilding")]


def _named(e: dict[str, Any] | None) -> str:
    if not e:
        return ""
    parts = [t for t in (e["name"], e["long_name"]) if t]
    return " — ".join(dict.fromkeys(parts))


# ---------------------------------------------------------------- the sheets


def _given(sheets: Any) -> list[dict[str, Any]] | None:
    """The caller's sheets, checked and numbered, or None for the default set. Refuses before anything is drawn."""
    if sheets is None or sheets == "" or sheets == []:
        return None
    if isinstance(sheets, str):
        try:
            sheets = json.loads(sheets)
        except json.JSONDecodeError as e:
            raise ValueError(f"`sheets` is a list of sheets, each an object of ifc_draw's arguments with its number and title; it is not JSON ({e}).") from e
    if not isinstance(sheets, list):
        raise ValueError("`sheets` is a list of sheets, each an object of ifc_draw's arguments with its number and title, e.g. [{\"number\": \"A-101\", \"title\": \"Ground floor\", \"view\": \"plan\", \"storey\": \"Level 1\"}].")
    if len(sheets) > MAX_SHEETS:
        raise ValueError(f"`sheets` lists {len(sheets)} sheets; one set holds at most {MAX_SHEETS}. Make two sets.")
    out: list[dict[str, Any]] = []
    seen: dict[str, int] = {COVER[0].upper(): 0}
    counters = dict.fromkeys(SERIES, 0)
    for i, s in enumerate(sheets, 1):
        if not isinstance(s, dict):
            raise ValueError(f"Sheet {i} is not an object: each sheet is ifc_draw's arguments with its number and title, e.g. {{\"view\": \"elevation\", \"side\": \"south\"}}.")
        for k in s:
            if k in SET_ONLY:
                raise ValueError(f"Sheet {i} gives `{k}`: {SET_ONLY[k]}.")
            if k not in SHEET_ARGUMENTS and k != "number":
                raise ValueError(f"Sheet {i} gives `{k}`, which is not one of ifc_draw's arguments. A sheet takes number, title and {', '.join(a for a in SHEET_ARGUMENTS if a != 'title')}.")
        view = str(s.get("view") or "plan").strip().lower()
        if view not in drawing.VIEWS:
            raise ValueError(f'Sheet {i}: {view!r} is not a view: "plan", "section" or "elevation".')
        args = {k: v for k, v in s.items() if k in SHEET_ARGUMENTS and v is not None and v != ""}
        args["view"] = view
        if "scale" in args:
            try:
                drawing.parse_scale(str(args["scale"]))
            except ValueError as e:
                raise ValueError(f"Sheet {i}: {e}") from e
        number = str(s.get("number") or "").strip()
        if not number:
            counters[view] += 1
            number = f"A-{SERIES[view] + counters[view]}"
        elif not NUMBER.fullmatch(number):
            raise ValueError(f"Sheet {i}: {number!r} is not a sheet number: letters, digits, dots and dashes, at most 16, such as \"A-101\".")
        if number.upper() == COVER[0]:
            raise ValueError(f"Sheet {i} is numbered {number}, the cover sheet's number: the cover is always {COVER[0]}. Number it from A-101.")
        if number.upper() in seen:
            raise ValueError(f"Sheet number {number} is given twice (sheets {seen[number.upper()]} and {i}). Each sheet has its own number.")
        seen[number.upper()] = i
        out.append({"number": number, "title": str(args.get("title") or "").strip(), "arguments": args})
    return out


def _default(model: ifcopenshell.file, u: geometry.Units, deadline: float) -> tuple[list[dict[str, Any]] | None, list[str], str]:
    """The conventional set's sheets, or None when the shapes could not all be made in time (and how far it got)."""
    storeys = sorted(model.by_type("IfcBuildingStorey"), key=lambda s: (drawing.storey_z_m(model, s), s.id()))
    per = [(st, drawing.storey_elements(st)[0]) for st in storeys]
    every = sorted({e.id(): e for _, els in per for e in els}.values(), key=lambda e: e.id())
    got, stopped = geometry.meshes(model, every, deadline)
    if stopped:
        return None, [], f"after making {len(got)} of {len(every)} shapes, before the sheets were planned"
    notes: list[str] = []
    sheets: list[dict[str, Any]] = []
    for st, els in per:
        name = st.Name or getattr(st, "LongName", None) or f"Storey #{st.id()}"
        if not any(e.id() in got for e in els):
            notes.append(f"{name} (#{st.id()}) holds nothing with a shape, so it has no plan.")
            continue
        title = f"{name} plan"
        sheets.append({"number": f"A-{SERIES['plan'] + len(sheets) + 1}", "title": title, "arguments": {"view": "plan", "storey": f"#{st.id()}", "title": title}})
    shaped = [got[e.id()].verts for e in every if e.id() in got]
    if not shaped:
        notes.append("Nothing on any storey has a shape, so there is nothing to draw.")
        return [], notes, ""
    lo = min(v.min(axis=0)[0] for v in shaped), min(v.min(axis=0)[1] for v in shaped)
    hi = max(v.max(axis=0)[0] for v in shaped), max(v.max(axis=0)[1] for v in shaped)
    for i, side in enumerate(SIDES, 1):
        title = f"{side.capitalize()} elevation"
        sheets.append({"number": f"A-{SERIES['elevation'] + i}", "title": title, "arguments": {"view": "elevation", "side": side, "title": title}})

    def at(metres: float) -> str:
        return f"{metres / u.scale:.{u.decimals}f}"

    cx, cy = (lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2
    past = SECTION_PAST_M
    lines = (
        ("North-south section", f"{at(cx)}, {at(lo[1] - past)}, {at(cx)}, {at(hi[1] + past)}"),
        ("East-west section", f"{at(lo[0] - past)}, {at(cy)}, {at(hi[0] + past)}, {at(cy)}"),
    )
    for i, (title, line) in enumerate(lines, 1):
        sheets.append({"number": f"A-{SERIES['section'] + i}", "title": title, "arguments": {"view": "section", "line": line, "title": title}})
    return sheets, notes, ""


# ---------------------------------------------------------------- the cover


def cover(
    paper: str,
    set_name: str,
    project: dict[str, Any] | None,
    buildings: list[dict[str, Any]],
    source: str,
    drawn_on: str,
    rows: list[tuple[str, str, str]],
    not_drawn: list[tuple[str, str, str]],
    length_name: str,
) -> str:
    """The cover sheet's SVG: the set's name, what the model names, where it was drawn from and when, and the index."""
    from xml.sax.saxutils import escape

    long_side, short_side, paper_name = drawing.paper_of(paper)
    pw, ph = long_side, short_side  # landscape
    m = drawing.MARGIN_MM
    x0, width = m + 4, pw - 2 * m - 8
    t = drawing._text
    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{pw:g}mm" height="{ph:g}mm" viewBox="0 0 {pw:g} {ph:g}">',
        drawing.STYLE,
        f'<title>{escape(set_name)}: {COVER[1].lower()}</title>',
        f'<rect x="0" y="0" width="{pw:g}" height="{ph:g}" fill="#fff"/>',
        f'<rect x="{m / 2:g}" y="{m / 2:g}" width="{pw - m:g}" height="{ph - m:g}" fill="none" stroke="#000" stroke-width="0.35"/>',
    ]
    size = max(5.0, min(10.0, width / (max(len(set_name), 1) * 0.7)))
    y = m + 4 + size
    svg.append(t(x0, y, drawing.clip(set_name, width, size, 0.7), size, weight="bold"))
    y += 7
    for label, named in (("Project", _named(project)), *(("Building", _named(b)) for b in buildings)):
        if named:
            svg.append(t(x0, y, drawing.clip(f"{label}: {named}", width, 3.6), 3.6))
            y += 5
    y += 2
    svg.append(t(x0, y, "Plan set drawn from the building model", 4.2, weight="bold"))
    y += 6
    for line in (
        f"Drawn {drawn_on}. {source}.",
        f"{len(rows)} sheets on {paper_name}, each drawn to the scale written on it. The model's lengths are in {length_name}.",
        f"A conceptual set drawn from the model by flo2-ifc {geometry._version()}: not drawn by an architect, and not a permit or construction set.",
    ):
        svg.append(t(x0, y, drawing.clip(line, width, 2.8), 2.8))
        y += 4.5

    # The index: each sheet's number, title and scale; then any sheet not drawn, and why.
    y += 4
    svg.append(t(x0, y, "Sheets", 3.6, weight="bold"))
    y += 3
    bottom = ph - m - drawing.TITLE_MM - 4
    entries: list[tuple[str, str, str, bool]] = [(n, ti, sc, False) for n, ti, sc in rows]
    if not_drawn:
        entries.append(("", "", "", True))
        entries += [(n, ti, why, True) for n, ti, why in not_drawn]
    row = 5.5
    per_column = max(1, int((bottom - y) / row))
    if len(entries) > per_column:
        row = max(3.6, (bottom - y) / len(entries))
        per_column = max(1, int((bottom - y) / row))
    columns = -(-len(entries) // per_column)
    col_w = width / columns
    font = min(3.0, row * 0.55)
    for i, (number, title, last, missing) in enumerate(entries):
        cx = x0 + (i // per_column) * col_w
        cy = y + row * (i % per_column + 1)
        if missing and not number:
            svg.append(t(cx, cy, "Not drawn", font, weight="bold"))
            continue
        svg.append(t(cx, cy, number, font, weight="bold"))
        title_w = min(col_w * (0.45 if missing else 0.62) - 24, 110.0)
        svg.append(t(cx + 22, cy, drawing.clip(title, title_w, font), font))
        svg.append(t(cx + 22 + title_w + 2, cy, drawing.clip(last, col_w - title_w - 28, font), font))

    # The title block, as every sheet's.
    ty = ph - m - drawing.TITLE_MM + 4
    svg.append(f'<path d="M{m / 2:g} {ty - 4:.2f}H{pw - m / 2:g}" stroke="#000" stroke-width="0.25"/>')
    svg.append(t(m, ty + 2, COVER[1], 3.6, weight="bold"))
    svg.append(t(m, ty + 7.5, drawing.clip(f"{set_name}: the index of the set's {len(rows)} sheets.", width - drawing.SHEET_BOX_MM, 2.6), 2.6))
    svg.append(t(m, ty + 12, drawing.clip(f"{source}; flo2-ifc {geometry._version()}.", width - drawing.SHEET_BOX_MM, 2.1), 2.1))
    svg.extend(drawing.sheet_box(pw, ph, COVER[0], set_name))
    svg.append("</svg>")
    return "\n".join(svg)


# ---------------------------------------------------------------- binding


def page_of(svg: str) -> tuple[Any, float, float]:
    """A sheet's SVG drawn by cairosvg onto a cairo RECORDING surface, the size of its sheet in points: done as each
    sheet is drawn, so binding the set only replays them. Drawing an SVG with cairosvg is most of what binding costs
    (on the public Clinic_Architectural's ten sheets, 4.4 MB of SVG: 9.0 s to draw them, 1.9 s to replay them into the
    PDF, on one laptop core, 2026-11-09); a set's last call would otherwise draw them all at once."""
    import cairocffi
    from cairosvg.parser import Tree
    from cairosvg.surface import PDFSurface

    class Recorded(PDFSurface):
        """cairosvg's PDF surface (points, as the PDF's own), drawing on a recording surface of the sheet's size."""

        def _create_surface(self, width, height):
            self.recording = cairocffi.RecordingSurface(cairocffi.CONTENT_COLOR_ALPHA, (0, 0, width, height))
            return self.recording, width, height

    drawn = Recorded(Tree(bytestring=svg.encode("utf-8")), None, 96)
    return drawn.recording, drawn.width, drawn.height


def bind(pages: list[tuple[Any, float, float]], path: str, title: str) -> int:
    """The recorded sheets (page_of) as ONE PDF at `path`, a page each, each page its sheet's size, with cairo's own
    PDF surface (cairocffi): vector, its words as text in the fonts they were drawn in. Returns how many pages."""
    import cairocffi

    surface = cairocffi.PDFSurface(path, 1, 1)
    surface.set_metadata(cairocffi.PDF_METADATA_TITLE, title)
    surface.set_metadata(cairocffi.PDF_METADATA_CREATOR, f"flo2-ifc {geometry._version()}")
    try:
        for recording, width, height in pages:
            surface.set_size(width, height)
            context = cairocffi.Context(surface)
            context.set_source_surface(recording, 0, 0)
            context.paint()
            surface.show_page()
    finally:
        surface.finish()
    return len(pages)


# ---------------------------------------------------------------- the set


def plan_set(
    model: ifcopenshell.file,
    output_path: str,
    model_path: str | None = None,
    sheets: Any = None,
    paper: str = "",
    scale: str = "",
    title: str = "",
    edits: int = 0,
    seconds: float | None = None,
) -> dict[str, Any]:
    """Draw the set and bind it into one PDF, as <output_path>.pdf and <output_path>-<number>.svg. See server.py's
    ifc_plan_set."""
    started = time.monotonic()
    budget = reading.SECONDS if seconds is None else seconds
    deadline = started + budget
    stem = re.sub(r"\.pdf$", "", (output_path or "").strip(), flags=re.I)
    if not stem:
        raise ValueError("`output_path` says where the set is written: <output_path>.pdf, and <output_path>-<number>.svg for each sheet.")
    _, _, paper_name = drawing.paper_of(paper)
    if scale.strip():
        drawing.parse_scale(scale)
    given = _given(sheets)
    u = geometry.Units(model)
    project, buildings = names(model)
    # The set's name: the caller's, or the project's as the model states it (its long name first), or a building's.
    set_name = (
        title.strip()
        or next((e["long_name"] or e["name"] for e in [project, *buildings] if e and (e["long_name"] or e["name"])), None)
        or "A building model"
    )
    key = (id(model), geometry.generation(), edits, json.dumps({"sheets": given, "paper": paper_name, "scale": scale.strip(), "title": title.strip()}, sort_keys=True, default=str))
    state = _state(model, key)
    done: dict[str, dict[str, Any]] = state["done"]
    failed: dict[str, str] = state["failed"]

    def partial(why: str) -> dict[str, Any]:
        planned = state["planned"] or []
        left = [s["number"] for s in planned if s["number"] not in done and s["number"] not in failed]
        out: dict[str, Any] = {
            "complete": False,
            "partial": True,
            "done": [s["number"] for s in planned if s["number"] in done],
            "to_do": left,
            "stopped": (
                f"Time ran out ({budget:.0f} s) {why}. What is drawn is kept for this session: call again with the same arguments and it goes on"
                + (f" from {left[0]}" if left else "")
                + ". Nothing is written until the set is whole."
            ),
            "seconds": round(time.monotonic() - started, 1),
            "units": u.say,
        }
        if failed:
            out["not_drawn"] = [{"number": n, "why": w} for n, w in failed.items()]
        return out

    # 1. THE SHEETS: the caller's, or the conventional set's.
    if state["planned"] is None:
        if given is not None:
            state["planned"] = given
        else:
            planned, notes, how_far = _default(model, u, deadline)
            if planned is None:
                return partial(how_far)
            state["planned"], state["notes"] = planned, notes
    planned: list[dict[str, Any]] = state["planned"]

    # 2. ONE SCALE A KIND (the conventional set, when the set names no scale): the largest that fits every sheet of it.
    if given is None and not scale.strip() and not state["fitted"]:
        for s in planned:
            if s["number"] in state["fits"]:
                continue
            left = deadline - time.monotonic()
            if left <= 0:
                return partial("while choosing the sheets' scales")
            try:
                fit, _ = drawing.sheet(model, model_path=model_path, paper=paper, seconds=left, fit_only=True, **s["arguments"])
            except ValueError:
                state["fits"][s["number"]] = None  # it is refused with its reason when it is drawn
                continue
            if fit.get("partial"):
                return partial("while choosing the sheets' scales")
            state["fits"][s["number"]] = fit["fits"]["ratio"]
        for kind in SERIES:
            ratios = [state["fits"][s["number"]] for s in planned if s["arguments"]["view"] == kind and state["fits"].get(s["number"])]
            for s in planned:
                if s["arguments"]["view"] == kind and ratios:
                    s["scale"] = f"1:{max(ratios)}"
        state["fitted"] = True

    # 3. EACH SHEET, in order: what is done stays done.
    drawn_now = 0
    for s in planned:
        number = s["number"]
        if number in done or number in failed:
            continue
        left = deadline - time.monotonic()
        if left <= 0 or (drawn_now and left < state["slowest"]):
            return partial(f"with {len(done)} of {len(planned)} sheets drawn")
        args = dict(s["arguments"])
        if "scale" not in args and (s.get("scale") or scale.strip()):
            args["scale"] = s.get("scale") or scale.strip()
        began = time.monotonic()
        try:
            out, text = drawing.sheet(model, model_path=model_path, paper=paper, edits=edits, seconds=left, number=number, set_name=set_name, **args)
        except ValueError as e:
            failed[number] = str(e)
            continue
        if text is None:
            return partial(f"with {len(done)} of {len(planned)} sheets drawn, while drawing {number}")
        done[number] = {"out": out, "svg": text, "page": page_of(text)}
        drawn_now += 1
        state["slowest"] = max(state["slowest"], time.monotonic() - began)
    if not done:
        whys = "; ".join(f"{s['number']}{' (' + s['title'] + ')' if s['title'] else ''}: {failed.get(s['number'], '')}" for s in planned) or " ".join(state["notes"])
        raise ValueError(f"No sheet of the set could be drawn, so nothing was written: {whys}")
    svg_mb = sum(len(d["svg"]) for d in done.values()) / 1e6
    if drawn_now and deadline - time.monotonic() < BIND_SECONDS[0] + BIND_SECONDS[1] * svg_mb:
        return partial(f"with all {len(done)} sheets drawn, before binding them")

    # 4. THE COVER, then the PDF and each sheet's SVG.
    drawn_on = datetime.date.today().isoformat()
    source, edited, sha = drawing.source_line(model_path, edits)
    source += f", {edited}" if edited else ""
    titled = {n: (s["title"] or done[n]["out"]["what"]) if n in done else (s["title"] or _describe(s["arguments"])) for s in planned for n in [s["number"]]}
    rows = [(COVER[0], COVER[1], "")] + [(s["number"], titled[s["number"]], done[s["number"]]["out"]["sheet"]["scale"]) for s in planned if s["number"] in done]
    missing = [(s["number"], titled[s["number"]], failed[s["number"]]) for s in planned if s["number"] in failed]
    cover_svg = cover(paper, set_name, project, buildings, source, drawn_on, rows, missing, u.length_name)
    pages = [(COVER[0], cover_svg, page_of(cover_svg))] + [(s["number"], done[s["number"]]["svg"], done[s["number"]]["page"]) for s in planned if s["number"] in done]

    os.makedirs(os.path.dirname(os.path.abspath(stem)) or ".", exist_ok=True)
    pdf = stem + ".pdf"
    part = pdf + ".part"
    try:
        count = bind([page for _, _, page in pages], part, set_name)
        paths = []
        for number, text, _ in pages:
            p = f"{stem}-{number}.svg"
            with open(p, "w", encoding="utf-8") as f:
                f.write(text)
            paths.append(p)
        os.replace(part, pdf)
    finally:
        if os.path.exists(part):
            os.remove(part)
    with open(pdf, "rb") as f:
        pdf_sha = hashlib.sha256(f.read()).hexdigest()

    listed: list[dict[str, Any]] = [{"number": COVER[0], "title": COVER[1], "page": 1, "svg": paths[0]}]
    page = 1
    for s in planned:
        if s["number"] not in done:
            continue
        page += 1
        o = done[s["number"]]["out"]
        entry: dict[str, Any] = {
            "number": s["number"],
            "title": titled[s["number"]],
            "view": o["view"],
            "what": o["what"],
            "scale": o["sheet"]["scale"],
            "ratio": o["sheet"]["ratio"],
            "orientation": o["sheet"]["orientation"],
            "page": page,
            "svg": paths[page - 1],
            "arguments": s["arguments"],
            "elements": o["elements"],
        }
        for k in ("unlabelled_rooms", "notes"):
            if o.get(k):
                entry[k] = o[k]
        listed.append(entry)
    reply: dict[str, Any] = {
        "complete": True,
        "set": {"title": set_name, "paper": paper_name, "pages": count, "drawn_on": drawn_on},
        "written": {"pdf": pdf, "pdf_bytes": os.path.getsize(pdf), "sha256": pdf_sha, "svgs": paths},
        "sheets": listed,
    }
    if missing:
        reply["not_drawn"] = [{"number": n, "title": ti, "view": next(s["arguments"]["view"] for s in planned if s["number"] == n), "why": why} for n, ti, why in missing]
    reply["project"] = project
    reply["buildings"] = buildings
    if state["notes"]:
        reply["notes"] = list(state["notes"])
    reply["source"] = {"model_file": os.path.basename(model_path) if model_path else None, "sha256": sha}
    reply["seconds"] = round(time.monotonic() - started, 1)
    reply["units"] = u.say
    return reply


def _describe(args: dict[str, Any]) -> str:
    """A sheet not drawn, in a few words, when it has no title."""
    view = args.get("view", "plan")
    what = args.get("storey") if view != "elevation" else args.get("side")
    return f"{view.capitalize()}{' of ' + str(what) if what else ''}"
