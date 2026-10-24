#!/usr/bin/env python3
"""Fetch buildingSMART's validation rules at the pinned release, checked by sha256.

buildingSMART publishes the normative rules its validation service runs as
code, not as a package: github.com/buildingSMART/ifc-gherkin-rules (MIT), whose
``ifc_validation_models`` submodule is github.com/buildingSMART/
ifc-validation-data-model (MIT). They are not on PyPI, so pyproject.toml
cannot pin them. This is their pin instead: one release, by tag and commit,
each archive's sha256, and the parts of it that run (the features and the
modules they import; not the test models, docs or CI scripts).

    python3 tools/ifc_rules.py /opt/ifc-gherkin-rules   # the Dockerfile's build stage
    python3 tools/ifc_rules.py --print                   # what is pinned, as JSON

The Dockerfile runs it in its build stage, where there is network; the image
then holds the rules at /opt/ifc-gherkin-rules and needs none. The rules'
own Python dependencies are pinned in pyproject.toml, beside flo2-ifc's.
tools/dependency_currency.py checks this pin's tag against upstream's latest
release once a month. Moving it is its own pull request: change RELEASE,
then run the rules' tests (tests/test_rules.py) and flo2's door test.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import tarfile
import urllib.request

#: The pinned release.
RELEASE = {
    "rules": {
        "repository": "buildingSMART/ifc-gherkin-rules",
        "tag": "v1.0.5",
        "commit": "23a5863df47b2af40a67ac4db885b70f2dc6b767",
        "sha256": "e2f47e9ac4d47f124a0c4d8d24b4fe43297cd59d78ab6bdd8a4fa11026c5e012",
        "licence": "MIT",
    },
    "data_model": {
        "repository": "buildingSMART/ifc-validation-data-model",
        "branch": "release/1.0.5",
        "commit": "f5b931e122d53fd91c0c1fd17dc744c7539d9dea",
        "sha256": "72c13f2c0b2dca02e4bd3fb8b84645cac4c08f69781a42c5f2c66c9ec73c38bf",
        "licence": "MIT",
    },
}

#: What of the rules' repository runs: the features (with their steps) and the
#: modules environment.py imports. Everything else (test/, docs/) is left out.
RULES_PARTS = ("features/", "validation_results.py", "main.py", "outcome_embedding_json_formatter.py", "behave.ini", "LICENSE", "README.md", "__init__.py")
#: What of the data model runs: its Python modules and its licence (not its migrations' tests).
MODEL_SKIP = ("tests.py", "test_settings.py")


def _fetch(repo: str, commit: str, sha256: str) -> bytes:
    url = f"https://github.com/{repo}/archive/{commit}.tar.gz"
    with urllib.request.urlopen(url, timeout=120) as r:
        data = r.read()
    got = hashlib.sha256(data).hexdigest()
    if got != sha256:
        raise SystemExit(f"{url}: sha256 {got}, pinned {sha256}; nothing was installed")
    return data


def _extract(data: bytes, into: str, keep) -> int:
    n = 0
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        for m in tar.getmembers():
            parts = m.name.split("/", 1)
            if len(parts) < 2 or not parts[1]:
                continue
            rel = parts[1]
            if not keep(rel) or not (m.isfile() or m.isdir()):
                continue
            target = os.path.join(into, rel)
            if not os.path.realpath(target).startswith(os.path.realpath(into) + os.sep):
                raise SystemExit(f"{m.name} would be written outside {into}")
            if m.isdir():
                os.makedirs(target, exist_ok=True)
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with tar.extractfile(m) as src, open(target, "wb") as dst:
                dst.write(src.read())
            n += 1
    return n


def install(into: str) -> dict:
    os.makedirs(into, exist_ok=True)
    r, d = RELEASE["rules"], RELEASE["data_model"]
    rules_files = _extract(_fetch(r["repository"], r["commit"], r["sha256"]), into, lambda rel: rel.startswith(RULES_PARTS) or rel in RULES_PARTS)
    model_dir = os.path.join(into, "ifc_validation_models")
    model_files = _extract(_fetch(d["repository"], d["commit"], d["sha256"]), model_dir, lambda rel: rel.split("/")[-1] not in MODEL_SKIP)
    said = {**RELEASE, "files": {"rules": rules_files, "data_model": model_files}}
    with open(os.path.join(into, "FLO2-RELEASE.json"), "w", encoding="utf-8") as f:
        json.dump(said, f, indent=2)
    return said


def main(argv: list[str]) -> int:
    if argv[1:] == ["--print"]:
        print(json.dumps(RELEASE, indent=2))
        return 0
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    said = install(argv[1])
    print(f"buildingSMART's rules {said['rules']['tag']} ({said['rules']['commit'][:12]}): {said['files']['rules']} files, data model {said['files']['data_model']} files, in {argv[1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
