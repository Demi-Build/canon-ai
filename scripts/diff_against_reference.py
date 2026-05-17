#!/usr/bin/env python3
"""Schema diff: compare canon-emitted JSON against a captured cradle reference.

Reports:
  - Keys present in reference but missing from canon (BAD — field-coverage gap)
  - Keys present in canon but not in reference (informational — canon adds new fields)
  - Type mismatches at matching keys (BAD — shape drift)

Compares structural shape only; does NOT compare values. Two JSON files with
the same keys + types pass even if all values differ.

Usage:
    python scripts/diff_against_reference.py <canon_file> <reference_file>

Exit code: 0 if no missing-keys-or-type-mismatches; 1 otherwise.
"""

from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path
from typing import Any


def shape_of(value: Any) -> str:
    """Return a string label for the value's structural shape."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        if not value:
            return "list[empty]"
        # Use the first element's shape as the list element shape
        return f"list[{shape_of(value[0])}]"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def diff_keys(canon: Any, reference: Any, path: str = "$") -> list[str]:
    """Walk both structures recursively. Return list of issue strings."""
    issues: list[str] = []

    if isinstance(reference, dict) and isinstance(canon, dict):
        ref_keys = set(reference.keys())
        canon_keys = set(canon.keys())
        # Missing keys (in reference but not canon) — BAD
        for k in sorted(ref_keys - canon_keys):
            issues.append(f"MISSING {path}.{k} (reference has it, canon does not)")
        # Extra keys (in canon but not reference) — informational
        for k in sorted(canon_keys - ref_keys):
            issues.append(f"EXTRA   {path}.{k} (canon adds, reference does not have)")
        # Recurse into shared keys
        for k in sorted(ref_keys & canon_keys):
            issues.extend(diff_keys(canon[k], reference[k], f"{path}.{k}"))
    elif isinstance(reference, list) and isinstance(canon, list):
        # Compare first element shapes if both non-empty
        if reference and canon:
            issues.extend(diff_keys(canon[0], reference[0], f"{path}[0]"))
    else:
        # Leaf comparison: type shape
        ref_shape = shape_of(reference)
        canon_shape = shape_of(canon)
        if ref_shape != canon_shape:
            issues.append(f"TYPE    {path}: canon={canon_shape}, reference={ref_shape}")

    return issues


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("canon_file", type=Path)
    parser.add_argument("reference_file", type=Path)
    args = parser.parse_args()

    if not args.canon_file.exists():
        print(f"ERROR: canon file not found: {args.canon_file}")
        return 2
    if not args.reference_file.exists():
        print(f"ERROR: reference file not found: {args.reference_file}")
        return 2

    canon = json.loads(args.canon_file.read_text())
    reference = json.loads(args.reference_file.read_text())

    # If both are top-level arrays of entities, compare first-element shapes
    # If both are top-level objects, walk recursively
    # If both are keyed-objects (like items.json), pick one entry from each
    if isinstance(canon, dict) and isinstance(reference, dict):
        # Could be a top-level singleton OR a keyed-object DB
        # Heuristic: if all keys look like int IDs (string), it's keyed-object
        canon_int_keys = all(k.isdigit() or k.lstrip("-").isdigit() for k in canon.keys()) if canon else False
        ref_int_keys = all(k.isdigit() or k.lstrip("-").isdigit() for k in reference.keys()) if reference else False
        if canon_int_keys and ref_int_keys:
            # Compare one entry from each
            canon_first = next(iter(canon.values()))
            ref_first = next(iter(reference.values()))
            issues = diff_keys(canon_first, ref_first, "$<entry>")
        else:
            issues = diff_keys(canon, reference, "$")
    elif isinstance(canon, list) and isinstance(reference, list):
        if not canon or not reference:
            issues = []
        else:
            issues = diff_keys(canon[0], reference[0], "$[0]")
    else:
        issues = diff_keys(canon, reference, "$")

    print(f"=== {args.canon_file.name} ===")
    print(f"  canon:     {args.canon_file}")
    print(f"  reference: {args.reference_file}")
    if not issues:
        print("  ✓ No structural differences.")
        return 0
    missing_count = sum(1 for i in issues if i.startswith("MISSING"))
    extra_count = sum(1 for i in issues if i.startswith("EXTRA"))
    type_count = sum(1 for i in issues if i.startswith("TYPE"))
    print(f"  {missing_count} missing, {extra_count} extra, {type_count} type mismatches")
    for issue in issues:
        print(f"    {issue}")
    # Exit non-zero only on MISSING or TYPE issues; EXTRA is informational
    return 1 if (missing_count or type_count) else 0


if __name__ == "__main__":
    sys.exit(main())
