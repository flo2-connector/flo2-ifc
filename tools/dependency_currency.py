#!/usr/bin/env python3
"""Is every pin in flo2-ifc at or near its latest release, and is every one that
is not held back on purpose, in writing, with a date to look again?

    python3 tools/dependency_currency.py [--json report.json] [--today YYYY-MM-DD]

THE REQUIREMENT. Every MCP server Anthony runs has its dependencies reviewed
and updated monthly. .github/workflows/dependencies.yml runs this on the first
of every month, and on any pull request that moves a pin, a hold, a workflow
or this file. It is a smaller Python port of reflow2's
tools/dependency_currency.py, reading pip pins instead of Cargo ranges. It
reads four things:

  - every requirement in pyproject.toml ([project] dependencies, each
    [project.optional-dependencies] extra, and [build-system] requires),
    against the newest release on PyPI (its JSON API);
  - the one git pin, ifcmcp on Anthony's fork. For it, the report gives the
    fork commit, PyPI's latest ifcopenshell-mcp, and whether that release
    still requires mcp<2. When it no longer does, upstream has caught up and
    the pin can return to a PyPI release;
  - every `uses:` in .github/workflows, against the action's newest release;
  - the Agent Plugins schema version that plugin.json and mcp.json declare,
    against the newest tag of the specification's repository.

WHAT FAILS, AND WHAT ONLY REPORTS.
  - A patch or minor release newer than the pin is REPORTED (patch_owed,
    minor_owed): take it in the next monthly update. A gate that went red on
    every upstream patch would be ignored within a month.
  - A newer major, or a new 0.x minor (0.9 -> 0.10 is a new series), with no
    hold in dependency-holds.toml FAILS (series_owed). So does a hold whose
    look_again date has passed (hold_expired), and a hold with no reason or
    no look_again date (hold_invalid). An exception is recorded, never silent.
  - A lookup that could not be made FAILS (unknown). A currency check that
    cannot see is not a pass.
  - A requirement that is not pinned exactly FAILS (unpinned). This
    repository exists to hold an exact set; a range drifts by itself.

The report is dated. It goes to stdout, and is appended to
$GITHUB_STEP_SUMMARY when that is set. --json also writes it as JSON.
Standard library only: the workflow runs it with a bare python3.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import tomllib
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

REPO = Path(__file__).resolve().parent.parent
HOLDS = "dependency-holds.toml"
MANIFESTS = ("plugin.json", "mcp.json")
SPEC_REPO = "agentplugins/agent-plugins-spec"
USER_AGENT = "flo2-ifc-dependency-currency (github.com/sligara7/flo2-ifc)"

FAILING = ("series_owed", "hold_expired", "hold_invalid", "unknown", "unpinned")
ORDER = ("series_owed", "hold_expired", "hold_invalid", "unknown", "unpinned", "return_to_pypi",
         "hold_unneeded", "held", "minor_owed", "patch_owed", "fork", "branch_ref", "current")

Version = tuple[int, int, int]


# ---- versions -----------------------------------------------------------------


def parse_version(s: str) -> Version | None:
    """A final release, `1.2.3` (or `v1.2.3`, `1.2`, `1`, `3.7.post1`), as a
    tuple; None for a pre-release, a dev release, or anything else."""
    m = re.fullmatch(r"v?(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:\.post\d+)?", s.strip(), re.IGNORECASE)
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2) or 0), int(m.group(3) or 0))


def fmt(v: Version | None) -> str:
    return "?" if v is None else ".".join(map(str, v))


def series(v: Version) -> tuple[int, ...]:
    """What a breaking release moves: the major, or for 0.x the minor."""
    return (v[0],) if v[0] > 0 else (0, v[1])


def step(pinned: Version, latest: Version) -> str:
    """How far `latest` is ahead of `pinned`: current, patch, minor or series."""
    if latest <= pinned:
        return "current"
    if series(latest) > series(pinned):
        return "series"
    if latest[:2] > pinned[:2]:
        return "minor"
    return "patch"


def admits(specifier: str, v: Version) -> bool | None:
    """Whether a PEP 440 specifier such as `<2,>=1.0` admits `v`; None if this
    small reader cannot read it (then the caller says so rather than guess)."""
    for clause in (c.strip() for c in specifier.split(",")):
        if not clause:
            continue
        m = re.fullmatch(r"(~=|===|==|!=|<=|>=|<|>)\s*(\S+)", clause)
        if not m:
            return None
        op, raw = m.groups()
        if op in ("==", "!=") and raw.endswith(".*"):
            prefix = [int(p) for p in raw[:-2].split(".") if p.isdigit()]
            hit = list(v[: len(prefix)]) == prefix
            if hit != (op == "=="):
                return False
            continue
        target = parse_version(raw)
        if target is None:
            return None
        if op == "~=":
            parts = len(raw.split("."))
            if parts < 2:
                return None
            upper = (target[0] + 1, 0, 0) if parts == 2 else (target[0], target[1] + 1, 0)
            ok = target <= v < upper
        else:
            ok = {"==": v == target, "===": v == target, "!=": v != target, "<=": v <= target,
                  ">=": v >= target, "<": v < target, ">": v > target}[op]
        if not ok:
            return False
    return True


# ---- what this repository declares --------------------------------------------

REQUIREMENT = re.compile(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(\[[^\]]*\])?\s*([^;]*?)\s*(?:;(.*))?$")
GIT_URL = re.compile(r"git\+https://github\.com/([\w.-]+/[\w.-]+?)(?:\.git)?@([^#\s]+)(?:#(\S*))?$")


@dataclass
class Pin:
    name: str
    raw: str
    where: str
    exact: Version | None = None   # the version of an `==X.Y.Z` pin
    url: str | None = None         # the URL of a `name @ url` pin
    unreadable: bool = False


def parse_requirement(raw: str, where: str) -> Pin:
    m = REQUIREMENT.fullmatch(raw)
    if not m:
        return Pin(raw, raw, where, unreadable=True)
    name, _extras, rest, _marker = m.groups()
    pin = Pin(name.lower().replace("_", "-"), raw, where)
    rest = rest.strip()
    if rest.startswith("@"):
        pin.url = rest[1:].strip()
    else:
        exact = re.fullmatch(r"\(?\s*==\s*([^\s,*)]+)\s*\)?", rest)
        pin.exact = parse_version(exact.group(1)) if exact else None
    return pin


def declared_pins(repo: Path) -> list[Pin]:
    doc = tomllib.loads((repo / "pyproject.toml").read_text())
    pins = [parse_requirement(r, "pyproject.toml [project] dependencies")
            for r in doc.get("project", {}).get("dependencies", [])]
    for extra, reqs in sorted(doc.get("project", {}).get("optional-dependencies", {}).items()):
        pins += [parse_requirement(r, f"pyproject.toml [project.optional-dependencies] {extra}") for r in reqs]
    pins += [parse_requirement(r, "pyproject.toml [build-system] requires")
             for r in doc.get("build-system", {}).get("requires", [])]
    return pins


ACTION = re.compile(r"^\s*-?\s*uses:\s*([\w.-]+/[\w.-]+)(?:/[\w./-]+)?@([\w.-]+)", re.M)


def declared_actions(repo: Path) -> dict[str, dict]:
    """Every `uses: owner/repo@ref` in the workflows, with each ref it is used at."""
    out: dict[str, dict] = {}
    for wf in sorted((repo / ".github" / "workflows").glob("*.y*ml")):
        for m in ACTION.finditer(wf.read_text()):
            entry = out.setdefault(m.group(1), {"refs": set(), "files": set()})
            entry["refs"].add(m.group(2))
            entry["files"].add(str(wf.relative_to(repo)))
    return out


SCHEMA_URL = re.compile(r"^https://agent-plugins\.org/schemas/([^/]+)/[\w.-]+\.schema\.json$")


def declared_schema_versions(repo: Path) -> dict[str, list[str]]:
    """The Agent Plugins schema version each root manifest declares, by version."""
    out: dict[str, list[str]] = {}
    for name in MANIFESTS:
        path = repo / name
        if not path.exists():
            continue
        url = json.loads(path.read_text()).get("$schema", "")
        m = SCHEMA_URL.match(url)
        out.setdefault(m.group(1) if m else url or "(none)", []).append(name)
    return out


def load_holds(repo: Path) -> list[dict]:
    path = repo / HOLDS
    if not path.exists():
        return []
    return tomllib.loads(path.read_text()).get("hold", [])


# ---- what upstream has --------------------------------------------------------


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    token = os.environ.get("GITHUB_TOKEN")
    if token and url.startswith("https://api.github.com/"):
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def pypi_latest(name: str) -> dict:
    """PyPI's latest release of `name`: {"version": ..., "requires_dist": [...]}."""
    info = json.loads(_get(f"https://pypi.org/pypi/{name}/json"))["info"]
    return {"version": info["version"], "requires_dist": info.get("requires_dist") or []}


def github_latest(slug: str) -> Version | None:
    """The newest release of a GitHub repository, or its newest version tag."""
    try:
        rel = json.loads(_get(f"https://api.github.com/repos/{slug}/releases/latest"))
        v = parse_version(rel.get("tag_name", ""))
        if v is not None:
            return v
    except Exception:  # noqa: BLE001 — no releases; the tags below answer instead
        pass
    tags = json.loads(_get(f"https://api.github.com/repos/{slug}/tags?per_page=100"))
    versions = [v for v in (parse_version(t["name"]) for t in tags) if v is not None]
    return max(versions) if versions else None


def github_commit(slug: str, sha: str) -> str:
    """The committer date of `sha` in `slug`. Raises if GitHub does not have it."""
    commit = json.loads(_get(f"https://api.github.com/repos/{slug}/commits/{sha}"))
    return commit["commit"]["committer"]["date"]


@dataclass
class Upstream:
    pypi: Callable[[str], dict] = pypi_latest
    github_latest: Callable[[str], Version | None] = github_latest
    github_commit: Callable[[str, str], str] = github_commit


# ---- the verdict --------------------------------------------------------------


def find_hold(holds: list[dict], kind: str, name: str) -> dict | None:
    return next((h for h in holds if h.get("kind") == kind and h.get("name") == name), None)


def judge_hold(hold: dict | None, today: dt.date) -> tuple[str, str]:
    """(state, note) for something a whole series behind."""
    if hold is None:
        return "series_owed", f"no hold recorded in {HOLDS}"
    if not str(hold.get("reason") or "").strip():
        return "hold_invalid", "the hold has no reason, and a hold without one is silence"
    look = hold.get("look_again")
    try:
        look = look if isinstance(look, dt.date) else dt.date.fromisoformat(str(look))
    except ValueError:
        return "hold_invalid", f"the hold's look_again ({look!r}) is not a YYYY-MM-DD date"
    if look < today:
        return "hold_expired", f"held until {look}: {hold['reason']}"
    return "held", f"until {look}: {hold['reason']}"


def _behind(row: dict, pinned: Version, latest: Version, holds: list[dict], today: dt.date,
            kind: str, name: str, what: str) -> dict:
    """The row for something whose latest release is `latest` and pin is `pinned`."""
    row["latest"] = fmt(latest)
    how = step(pinned, latest)
    if how == "current":
        return {**row, "state": "current"}
    if how in ("patch", "minor"):
        return {**row, "state": f"{how}_owed", "note": f"{fmt(latest)} is out: {what}"}
    label = "a new major" if latest[0] > pinned[0] else "a new 0.x series"
    state, note = judge_hold(find_hold(holds, kind, name), today)
    return {**row, "state": state, "note": f"{label} ({fmt(latest)}) is out: {note}"}


def _git_row(pin: Pin, pins: list[Pin], upstream: Upstream) -> dict:
    row = {"kind": "git", "name": pin.name, "declared_in": [pin.where]}
    m = GIT_URL.match(pin.url or "")
    if not m:
        return {**row, "pinned": pin.url, "state": "unknown",
                "note": "not a git+https://github.com/owner/repo@commit URL this check reads"}
    slug, rev, fragment = m.groups()
    row["pinned"] = f"{slug}@{rev[:12]}" + (f" ({fragment})" if fragment else "")
    if not re.fullmatch(r"[0-9a-f]{40}", rev):
        return {**row, "state": "unpinned", "note": f"`{rev}` is not a full commit id, so it moves by itself"}
    notes = []
    try:
        notes.append(f"fork commit {rev[:12]} on {slug}, committed {upstream.github_commit(slug, rev)}")
    except Exception as e:  # noqa: BLE001 — reported, and it fails the run
        return {**row, "state": "unknown", "note": f"GitHub has no commit {rev} on {slug}, or the lookup failed: {e}"}
    try:
        latest = upstream.pypi(pin.name)
    except Exception as e:  # noqa: BLE001
        return {**row, "state": "unknown", "note": f"PyPI lookup failed: {e}"}
    row["latest"] = f"{latest['version']} (PyPI)"
    mcp_pin = next((p.exact for p in pins if p.name == "mcp" and p.exact), None)
    if mcp_pin is None:
        return {**row, "state": "unknown", "note": "; ".join(notes + ["no exact mcp pin to judge against"])}
    mcp_reqs = [r for r in latest["requires_dist"] if (REQUIREMENT.fullmatch(r) or [None])[1] == "mcp"]
    if not mcp_reqs:
        notes.append(f"PyPI's ifcopenshell-mcp {latest['version']} declares no mcp requirement; "
                     f"read it by hand before moving the pin back to PyPI")
        return {**row, "state": "return_to_pypi", "note": "; ".join(notes)}
    spec = REQUIREMENT.fullmatch(mcp_reqs[0]).group(3).strip().strip("()").strip()  # type: ignore[union-attr]
    ok = admits(spec, mcp_pin)
    if ok is None:
        return {**row, "state": "unknown",
                "note": "; ".join(notes + [f"its mcp requirement `{spec}` is not one this check reads"])}
    if ok:
        notes.append(f"PyPI's ifcopenshell-mcp {latest['version']} requires mcp `{spec}`, which admits mcp "
                     f"{fmt(mcp_pin)}: upstream has caught up, so the fork pin can return to "
                     f"ifcopenshell-mcp=={latest['version']} (README: moving the pin)")
        return {**row, "state": "return_to_pypi", "note": "; ".join(notes)}
    notes.append(f"PyPI's ifcopenshell-mcp {latest['version']} still requires mcp `{spec}` (mcp<2: "
                 f"yes), so the fork pin stays")
    return {**row, "state": "fork", "note": "; ".join(notes)}


def check(repo: Path, upstream: Upstream, today: dt.date) -> dict:
    holds = load_holds(repo)
    rows: list[dict] = []
    pins = declared_pins(repo)

    for pin in pins:
        if pin.unreadable:
            rows.append({"kind": "package", "name": pin.name, "pinned": pin.raw, "declared_in": [pin.where],
                         "state": "unknown", "note": "a requirement this check cannot read"})
            continue
        if pin.url is not None:
            rows.append(_git_row(pin, pins, upstream))
            continue
        row = {"kind": "package", "name": pin.name, "pinned": pin.raw.split(";")[0].strip()[len(pin.name):].strip()
               or "(any)", "declared_in": [pin.where]}
        if pin.exact is None:
            rows.append({**row, "state": "unpinned",
                         "note": "not pinned exactly (==X.Y.Z): a range drifts by itself"})
            continue
        try:
            latest = parse_version(upstream.pypi(pin.name)["version"])
        except Exception as e:  # noqa: BLE001 — reported, and it fails the run
            rows.append({**row, "state": "unknown", "note": f"PyPI lookup failed: {e}"})
            continue
        if latest is None:
            rows.append({**row, "state": "unknown", "note": "PyPI's latest is not a final release this check reads"})
            continue
        rows.append(_behind(row, pin.exact, latest, holds, today, "package", pin.name,
                            "move the pin in pyproject.toml"))

    for slug, use in sorted(declared_actions(repo).items()):
        refs = sorted(use["refs"])
        row = {"kind": "action", "name": slug, "pinned": ", ".join(refs), "declared_in": sorted(use["files"])}
        majors = [parse_version(r) for r in refs]
        if any(m is None for m in majors):
            rows.append({**row, "state": "branch_ref", "note": "pinned to a branch, which moves by itself"})
            continue
        try:
            latest = upstream.github_latest(slug)
        except Exception as e:  # noqa: BLE001
            rows.append({**row, "state": "unknown", "note": f"GitHub lookup failed: {e}"})
            continue
        if latest is None:
            rows.append({**row, "state": "unknown", "note": "no release or version tag found"})
            continue
        row["latest"] = fmt(latest)
        oldest = min(m for m in majors if m is not None)
        if oldest[0] >= latest[0]:
            rows.append({**row, "state": "current", "note": f"@v{latest[0]} follows every v{latest[0]}.x release"})
            continue
        state, note = judge_hold(find_hold(holds, "action", slug), today)
        rows.append({**row, "state": state, "note": f"v{latest[0]} is out: {note}"})

    declared = declared_schema_versions(repo)
    spec_latest: Version | None = None
    spec_error = None
    if declared:
        try:
            spec_latest = upstream.github_latest(SPEC_REPO)
        except Exception as e:  # noqa: BLE001
            spec_error = f"GitHub lookup of {SPEC_REPO} failed: {e}"
    for version, files in sorted(declared.items()):
        row = {"kind": "schema", "name": "agent-plugins", "pinned": version, "declared_in": files}
        pinned = parse_version(version)
        if pinned is None:
            rows.append({**row, "state": "unknown", "note": "not an agent-plugins.org schema URL with a version"})
        elif spec_error or spec_latest is None:
            rows.append({**row, "state": "unknown", "note": spec_error or f"{SPEC_REPO} has no version tag"})
        else:
            rows.append(_behind(row, pinned, spec_latest, holds, today, "schema", "agent-plugins",
                                f"move $schema in {' and '.join(files)} and the copies in tests/schemas/"))
    if len(declared) > 1:
        for row in rows:
            if row["kind"] == "schema" and row["state"] in ("current", "patch_owed", "minor_owed"):
                row["state"] = "unknown"
                row["note"] = (f"the manifests declare different schema versions ({', '.join(sorted(declared))}); "
                               "they must move together")

    # A hold on something no longer a series behind is reported, never failed:
    # a record that outlived its reason, which is how a hold file rots.
    for hold in holds:
        target = next((r for r in rows if r["kind"] == hold.get("kind") and r["name"] == hold.get("name")), None)
        if target is None:
            rows.append({"kind": hold.get("kind", "?"), "name": hold.get("name", "?"), "state": "hold_unneeded",
                         "note": "held, but nothing by that kind and name is declared: remove the hold"})
        elif target["state"] in ("current", "patch_owed", "minor_owed", "branch_ref"):
            target["note"] = (target.get("note", "") + f"; and a hold for it is still in {HOLDS}: remove it").lstrip("; ")
            target["state"] = "hold_unneeded"

    failing = [r for r in rows if r["state"] in FAILING]
    return {"date": today.isoformat(), "rows": rows, "failing": len(failing),
            "counts": {s: sum(r["state"] == s for r in rows) for s in sorted({r["state"] for r in rows})}}


def markdown(report: dict) -> str:
    rank = {s: i for i, s in enumerate(ORDER)}
    lines = [f"## flo2-ifc dependency currency, {report['date']}", "",
             "Every pin in pyproject.toml, every GitHub Action, and the Agent Plugins schema, against "
             "their latest releases. " + ", ".join(f"{n} {s}" for s, n in report["counts"].items()) + ".", "",
             "| state | kind | name | pinned | latest | note |",
             "|---|---|---|---|---|---|"]
    for r in sorted(report["rows"], key=lambda r: (rank.get(r["state"], 99), r["kind"], r["name"])):
        note = r.get("note", "").replace("|", "\\|")
        lines.append(f"| {r['state']} | {r['kind']} | {r['name']} | {r.get('pinned') or ''} | "
                     f"{r.get('latest') or ''} | {note} |")
    lines.append("")
    if report["failing"]:
        lines.append(f"**FAILED**: {report['failing']} owed, expired, invalid, unpinned or unreadable. Move each "
                     f"pin in its own pull request, or record a hold with a reason and a look_again date in "
                     f"{HOLDS}.")
    else:
        lines.append("**OK**: everything is current, owed only a patch or minor release, or held on the record.")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None, upstream: Upstream | None = None, repo: Path = REPO) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--json", help="also write the dated report here as JSON")
    ap.add_argument("--today", help="the date to judge holds against (default: today, UTC)")
    args = ap.parse_args(argv)
    today = dt.date.fromisoformat(args.today) if args.today else dt.datetime.now(dt.UTC).date()
    report = check(repo, upstream or Upstream(), today)
    md = markdown(report)
    print(md, end="")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as f:
            f.write(md)
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2) + "\n")
    return 1 if report["failing"] else 0


if __name__ == "__main__":
    sys.exit(main())
