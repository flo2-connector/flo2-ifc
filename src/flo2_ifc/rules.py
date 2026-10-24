"""buildingSMART's normative validation rules, run offline on a model file.

buildingSMART's validation service (validate.buildingsmart.org) checks a model
in stages: the IFC header and schema, the schema's EXPRESS rules, and its
NORMATIVE RULES, which are Gherkin features (implementer agreements and
informal propositions) run with behave over ifcopenshell, plus industry-practice
rules that only warn, and a bSDD check that looks classifications up online.
The normative rules' code is published, MIT-licensed, at
github.com/buildingSMART/ifc-gherkin-rules, with the data model it imports
(github.com/buildingSMART/ifc-validation-data-model). flo2-ifc runs that code
unchanged, at the release pinned in ``tools/ifc_rules.py`` (fetched and
checked by sha256 when the image is built), never a rewrite:

- the features run in a ``python -m behave`` child of the server, in the
  rules' own folder, on the model FILE (the rules open it themselves), with
  no network and no database: run from the console, the rules hand each
  feature's outcomes to the formatters (``environment.after_feature``), and
  ``flo2_ifc.rules_formatter.Lines`` writes them out one feature at a time;
- the child is stopped at the call's time limit, and the reply says which
  rules finished and where to go on from (``start``), as ifc_validate does;
- the reply says which rule sets ran, and which did not (the schema and
  EXPRESS checks are ifc_validate's; the bSDD check needs the network).

ifc_validate checks the schema; this checks the rules beyond it.
"""

from __future__ import annotations

import json
import os
import re
import select as _io_select
import signal
import subprocess
import sys
import time
from functools import lru_cache
from typing import Any

from flo2_ifc import geometry, reading

#: Where the rules are: the release ``tools/ifc_rules.py`` fetched (the image
#: puts it at /opt/ifc-gherkin-rules).
RULES_DIR = os.environ.get("FLO2_IFC_RULES_DIR", "/opt/ifc-gherkin-rules")
#: The rule sets, by the tag a feature carries, and what each one is.
RULE_SETS = {
    "critical": "critical: the header names an official IFC version (IFC101); the service checks nothing else of a file that fails it",
    "implementer-agreement": "normative: implementer agreements",
    "informal-proposition": "normative: informal propositions",
    "industry-practice": "not normative: industry practice, which only warns",
}
NORMATIVE = ("critical", "implementer-agreement", "informal-proposition")
#: What the validation service also checks, which this does not run, and why.
NOT_RUN_HERE = {
    "header, schema and EXPRESS rules": "ifc_validate checks these (IfcOpenShell's schema and EXPRESS-rule check)",
    "bSDD": "the service looks classifications up in the buildingSMART Data Dictionary online, and this server has no network",
}
#: The rules whose OWN tests do not all pass in this build. Measured
#: 2026-10-24 with the pinned release: upstream's test suite (test/test_main.py,
#: 849 test files) passes 804 and fails 45, the same 45 with the rules' own
#: requirements.txt pins and with flo2-ifc's. Every one is a file meant to pass:
#: for 43 the rule reported nothing where its test expects it to run (so here
#: it may say not_applicable where the service says passed), and for 2 (PSE001)
#: it reported an error. Both sets share ifcopenshell 0.9.0 from PyPI; the
#: cause is not established. A reply flags any of these it ran.
UPSTREAM_TESTS_NOT_ALL_PASSING = {
    "ALB012": 2, "ALB023": 1, "ALS011": 2, "ALS012": 2, "ALS016": 3, "ALS017": 3, "AXG000": 1, "BBX000": 1,
    "BBX001": 1, "GDP000": 1, "GEM003": 3, "GEM004": 9, "GEM011": 3, "IFC106": 2, "LAY000": 2, "MPD001": 2,
    "OJT001": 1, "PSE001": 2, "QTY000": 1, "SPS007": 2, "SWE002": 1,
}

#: How many issues one reply lists, at most, before it says the rest by count.
MAX_ISSUES_KEPT = 2000
#: The line prefix the formatter writes; anything else on the child's output is not the rules'.
MARK = "FLO2-RULES "

_CODE = re.compile(r"^([A-Z]{3}\d{3})")


@lru_cache(maxsize=4)
def _features(rules_dir: str) -> tuple[dict[str, Any], ...]:
    """Every rule the release holds: its code, name, file and tags, by code."""
    root = os.path.join(rules_dir, "features", "rules")
    if not os.path.isdir(root):
        raise RuntimeError(f"buildingSMART's rules are not installed here (no {root}); the image installs them with tools/ifc_rules.py")
    out = []
    for group in sorted(os.listdir(root)):
        folder = os.path.join(root, group)
        if not os.path.isdir(folder):
            continue
        for name in sorted(os.listdir(folder)):
            if not name.endswith(".feature"):
                continue
            path = os.path.join(folder, name)
            tags: list[str] = []
            title = ""
            with open(path, encoding="utf-8") as f:
                for line in f:
                    s = line.strip()
                    if s.startswith("@"):
                        tags += [t[1:] for t in s.split() if t.startswith("@")]
                    elif s.startswith("Feature:"):
                        title = s[len("Feature:") :].strip()
                        break
            m = _CODE.match(name)
            out.append({"code": m.group(1) if m else name, "title": title, "path": os.path.relpath(path, rules_dir), "tags": tags})
    out.sort(key=lambda r: r["code"])
    return tuple(out)


def release() -> dict[str, Any]:
    """Which release of the rules is installed, as tools/ifc_rules.py recorded it."""
    try:
        with open(os.path.join(RULES_DIR, "FLO2-RELEASE.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {"note": "not recorded"}


def rules_for(sets: list[str] | None) -> list[dict[str, Any]]:
    """The rules to run, in code order: those of `sets` (default: every set), never one upstream disabled."""
    wanted = set(sets or RULE_SETS)
    return [r for r in _features(RULES_DIR) if "disabled" not in r["tags"] and wanted & set(r["tags"])]


def _sets_of(text: str) -> list[str] | None:
    if not text or not text.strip():
        return None
    names = [t.strip().lower() for t in text.split(",") if t.strip()]
    if names == ["normative"]:
        return list(NORMATIVE)
    bad = [n for n in names if n not in RULE_SETS]
    if bad:
        raise ValueError(f"rule_sets names {', '.join(RULE_SETS)} (or \"normative\" for the first three), not {', '.join(bad)}")
    return names


def _run(model_path: str, rules: list[dict[str, Any]], budget: float) -> tuple[dict[str, list[dict[str, Any]]], bool, str]:
    """Run `rules` on the file in a behave child, stopped at `budget` seconds.
    Returns each finished rule's outcomes by its file, whether it was stopped,
    and what the child said on its error stream (its last lines)."""
    cmd = [
        sys.executable,
        "-m",
        "behave",
        "--no-summary",
        "--no-snippets",
        "--tags=-disabled",
        "-f",
        "flo2_ifc.rules_formatter:Lines",
        "--define",
        f"input={model_path}",
        "--define",
        "max_outcomes_per_rule=0",
        *[r["path"] for r in rules],
    ]
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "LANG", "LC_ALL", "HOME", "TMPDIR")}
    env |= {"PYTHONDONTWRITEBYTECODE": "1", "PYTHONWARNINGS": "ignore", "PYTHONUNBUFFERED": "1"}
    proc = subprocess.Popen(cmd, cwd=RULES_DIR, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    done: dict[str, list[dict[str, Any]]] = {}
    out_buf, err_buf = b"", b""
    deadline = time.monotonic() + max(0.0, budget)
    timed_out = False
    streams = {proc.stdout.fileno(): "out", proc.stderr.fileno(): "err"}
    open_fds = set(streams)
    while open_fds:
        left = deadline - time.monotonic()
        if left <= 0:
            timed_out = True
            break
        ready, _, _ = _io_select.select(list(open_fds), [], [], left)
        for fd in ready:
            chunk = os.read(fd, 65536)
            if not chunk:
                open_fds.discard(fd)
                continue
            if streams[fd] == "out":
                out_buf += chunk
                *lines, out_buf = out_buf.split(b"\n")
                for line in lines:
                    text = line.decode("utf-8", "replace")
                    if text.startswith(MARK):
                        try:
                            said = json.loads(text[len(MARK) :])
                        except json.JSONDecodeError:
                            continue
                        done[said["file"]] = said["outcomes"]
            else:
                err_buf = (err_buf + chunk)[-8192:]
    if timed_out:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    proc.wait()
    return done, timed_out, err_buf.decode("utf-8", "replace")


def _verdict(outcomes: list[dict[str, Any]]) -> str:
    severities = {str(o.get("severity", "")).upper() for o in outcomes}
    if "ERROR" in severities:
        return "error"
    if "WARNING" in severities:
        return "warning"
    if severities & {"PASSED", "EXECUTED"}:
        return "passed"
    return "not_applicable"


def check_rules(
    model,
    model_path: str | None,
    rule_sets: str = "",
    start: int = 0,
    offset: int = 0,
    limit: int = 50,
    edits: int = 0,
    seconds: float | None = None,
) -> dict[str, Any]:
    """Run buildingSMART's rules on the model file, as far as one call's time allows."""
    if not model_path or not os.path.isfile(model_path):
        raise ValueError("the rules run on a model FILE, and this model has none: save it first, then check the saved file")
    if start < 0 or offset < 0 or limit < 0:
        raise ValueError("start, offset and limit are 0 or more")
    limit = min(limit, reading.MAX_LIMIT)
    sets = _sets_of(rule_sets)
    rules = rules_for(sets)
    total = len(rules)
    if start >= total:
        raise ValueError(f"start is {start}, and {total} rule(s) are run, numbered from 0")
    started = time.monotonic()
    budget = reading.SECONDS if seconds is None else seconds
    todo = rules[start:]
    done, timed_out, said = _run(model_path, todo, budget)
    # The rules finish in the order given; the first not finished is where to go on from.
    finished = 0
    for r in todo:
        if r["path"] in done or os.path.basename(r["path"]) in done:
            finished += 1
        else:
            break
    if finished == 0 and not timed_out and not done:
        tail = " ".join(said.strip().splitlines()[-3:])[:600]
        raise RuntimeError(f"buildingSMART's rules could not run on this file: {tail or 'the rules gave no reason'}")
    by_global_id = {}
    results = []
    issues: list[dict[str, Any]] = []
    for r in todo[:finished]:
        outcomes = done.get(r["path"]) or done.get(os.path.basename(r["path"])) or []
        verdict = _verdict(outcomes)
        bad = [o for o in outcomes if str(o.get("severity", "")).upper() in ("ERROR", "WARNING")]
        results.append({"code": r["code"], "rule": r["title"], "set": next((t for t in r["tags"] if t in RULE_SETS), None), "result": verdict, "issues": len(bad)})
        for o in bad:
            inst = o.get("inst")
            gid = None
            if isinstance(inst, int) and model is not None:
                if inst not in by_global_id:
                    try:
                        by_global_id[inst] = getattr(model.by_id(inst), "GlobalId", None)
                    except RuntimeError:
                        by_global_id[inst] = None
                gid = by_global_id[inst]
            issues.append(
                {
                    "code": r["code"],
                    "severity": str(o.get("severity", "")).lower(),
                    "id": inst,
                    "global_id": gid,
                    "expected": o.get("expected"),
                    "observed": o.get("observed"),
                    **({"scenario": o["scenario"]} if o.get("scenario") else {}),
                }
            )
    # A rule that cannot finish within one call on this model is passed over,
    # named, so the check can go on; the reply says it did not run.
    too_long = [todo[0]["code"]] if finished == 0 and timed_out else []
    checked_to = start + (finished or (1 if too_long else 0))
    complete = checked_to >= total
    ran_sets = sorted({res["set"] for res in results if res["set"]})
    errors = sum(1 for i in issues if i["severity"] == "error")
    warnings = sum(1 for i in issues if i["severity"] == "warning")
    page = issues[offset : offset + limit]
    out: dict[str, Any] = {
        "model": {"file": os.path.basename(model_path), "sha256": geometry.file_sha256(model_path)},
        "passed": errors == 0 if results else None,
        "complete": complete,
        "checked": {"from": start, "to": checked_to, "of": total},
        "next_start": None if complete else checked_to,
        "summary": {
            "rules_run": len(results),
            "with_errors": sum(1 for x in results if x["result"] == "error"),
            "with_warnings": sum(1 for x in results if x["result"] == "warning"),
            "passed": sum(1 for x in results if x["result"] == "passed"),
            "not_applicable": sum(1 for x in results if x["result"] == "not_applicable"),
            "errors": errors,
            "warnings": warnings,
        },
        "rules_with_issues": [x for x in results if x["result"] in ("error", "warning")],
        "rules_passed": [x["code"] for x in results if x["result"] == "passed"],
        "rules_not_applicable": [x["code"] for x in results if x["result"] == "not_applicable"],
        **({"rules_not_finished": too_long} if too_long else {}),
        **(
            {
                "rules_with_a_caveat": {
                    "codes": caveats,
                    "why": "upstream's own tests for these rules do not all pass in this build (on files meant to pass, the rule reported nothing, or for PSE001 an error): read a not_applicable or an error from them with care",
                }
            }
            if (caveats := [x["code"] for x in results if x["code"] in UPSTREAM_TESTS_NOT_ALL_PASSING])
            else {}
        ),
        "issues": page,
        "issues_page": {"offset": offset, "listed": len(page), "of": len(issues), "next_offset": offset + limit if len(issues) > offset + limit else None},
        "rule_sets": {
            "asked": sets or list(RULE_SETS),
            "ran": {s: RULE_SETS[s] for s in ran_sets},
            "not_run_here": NOT_RUN_HERE,
            "disabled_upstream": [r["code"] for r in _features(RULES_DIR) if "disabled" in r["tags"]],
        },
        "rules_release": release(),
        "checked_by": f"buildingSMART's ifc-gherkin-rules (MIT), run unchanged with behave over ifcopenshell {_ifcopenshell_version()}, flo2-ifc {geometry._version()}",
    }
    if edits:
        out["model"]["edited"] = (
            f"The session's copy holds {edits} change(s) since it was opened. The rules check the FILE the sha256 names, not the edits: save the copy and check the saved file."
        )
    if too_long:
        out["passed"] = None
        out["note"] = (
            f"Rule {too_long[0]} did not finish in {budget:.0f} s, one call's time, on this model, so it was NOT checked: say so to the person. "
            + (f"Call again with start {checked_to} to go on with the rest." if not complete else "It was the last rule.")
        )
    elif not complete:
        out["note"] = (
            f"Partial: rules {start} to {checked_to - 1} of {total} (numbered from 0, in code order) finished in {budget:.0f} s. "
            f"Call again with start {checked_to} to go on; `passed` covers only the rules that finished."
        )
    out["seconds"] = round(time.monotonic() - started, 1)
    return out


def _ifcopenshell_version() -> str:
    import ifcopenshell

    return ifcopenshell.version
