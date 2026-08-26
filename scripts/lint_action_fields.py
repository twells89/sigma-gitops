#!/usr/bin/env python3
"""lint_action_fields.py — reject pre-rename Sigma action identifier keys.

Sigma renamed action identifier fields to an explicit *Id shape on 2026-08-26.
Every committed spec here is a GET-back captured BEFORE that date (config.yml
last_pulled is all 2026-08-05), so any spec carrying an action can hold a dead
key -- and sync-to-sigma.yml pushes these files on change.

The existing validation is `python3 -m json.tool`, i.e. syntax only. It cannot
see a dead key, so a push either 400s or, for one field, succeeds and does
nothing:

  * clear-control scope `page` -> `pageId` fails SILENTLY. The API returns 200 OK
    and DROPS the key, so the button persists and clears nothing.
  * `column-range` bounds are OPTIONAL in the API, so a range that lost its keys
    is schema-valid and silently matches everything.

Both are invisible to a status-code check, which is why this lints the shape.

THE RENAME IS SELECTIVE. Three fields were probed and deliberately NOT renamed;
"fixing" them to *Id is itself a live 400, so they must NOT be flagged:

  set-control-value.control                       stays `control`
  navigate         target{type:page}.page         stays `page`
  refresh-element  target{type:element}.element   stays `element`

That is why `page`/`control`/`container` are only dead inside a clear-control
`scope`, and this walker tracks position rather than matching key names globally.

Usage:  python3 scripts/lint_action_fields.py [files...]     (default: all specs)
Exit 0 = clean, 1 = at least one dead key.
"""
import json
import pathlib
import sys

# Dead anywhere inside an effect.
DEAD_IN_EFFECT = {
    "table": "tableElementId",
    "form": "formElementId",
    "tabbedContainer": "tabbedContainerElementId",
    "document": "documentId",
    "pluginElement": "pluginElementId",
    "pluginEffect": "pluginEffectId",
}
# Dead only inside a {type: column*} union member (value sources, whichRows
# selectors, custom-sort keys, column ranges).
DEAD_IN_COLUMN_UNION = {"column": "columnId", "min": "minColumnId", "max": "maxColumnId"}
COLUMN_UNION_TYPES = {"column", "column-range", "column-match"}
# Dead only inside a clear-control scope.
DEAD_IN_SCOPE = {"page": "pageId", "control": "controlId", "container": "containerElementId"}

findings = []


def walk(node, path, effect=None, in_scope=False):
    if isinstance(node, dict):
        if isinstance(node.get("effect"), str):
            effect = node["effect"]
        if effect:
            for key, new in DEAD_IN_EFFECT.items():
                if key in node and "effect" in node:
                    findings.append((path, effect, key, new, False))
            if node.get("type") in COLUMN_UNION_TYPES:
                for key, new in DEAD_IN_COLUMN_UNION.items():
                    if key in node:
                        findings.append((path, effect, key, new, False))
            if in_scope:
                for key, new in DEAD_IN_SCOPE.items():
                    if key in node:
                        findings.append((path, effect, key, new, key == "page"))
        for key, val in node.items():
            walk(val, f"{path}.{key}", effect,
                 in_scope or (effect == "clear-control" and key == "scope"))
    elif isinstance(node, list):
        for i, val in enumerate(node):
            walk(val, f"{path}[{i}]", effect, in_scope)


def main(argv):
    root = pathlib.Path(__file__).resolve().parent.parent
    if argv:
        paths = [pathlib.Path(a) for a in argv]
    else:
        paths = sorted(root.glob("workbooks/*.json")) + sorted(root.glob("data-models/*.json"))
    scanned = 0
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue  # json.tool already gates syntax
        scanned += 1
        before = len(findings)
        walk(data, "$")
        for _p, effect, key, new, silent in findings[before:]:
            note = "  <-- SILENT: 200 OK, key dropped, does nothing" if silent else ""
            print(f"::error file={path}::{effect}: `{key}` was renamed to `{new}`{note}")
            print(f"    at {_p}")

    if findings:
        print()
        print(f"{len(findings)} dead pre-rename action key(s) across {scanned} spec file(s).")
        print("These specs are pushed by sync-to-sigma.yml; a dead key either 400s or, for")
        print("clear-control scope.page, succeeds and silently does nothing.")
        print("Fix: re-pull the workbook from Sigma, or rename the key in place.")
        return 1
    print(f"OK: no dead pre-rename action keys ({scanned} spec file(s) scanned).")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
