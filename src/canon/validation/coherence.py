"""Generic coherence utilities — reference integrity and cycle detection.

These are domain-agnostic helpers used by the ValidationPhase and
available to user-defined validators.
"""

from __future__ import annotations


def check_references(
    entity: dict,
    *,
    field_name: str,
    valid_ids: set,
    label: str = "",
) -> list[str]:
    """Check that *entity[field_name]* is in *valid_ids*.

    Returns a list of issues (empty if the reference resolves).
    Handles single values and lists of values.
    """
    issues: list[str] = []
    prefix = f"{label}: " if label else ""
    value = entity.get(field_name)

    if value is None:
        return issues

    if isinstance(value, list):
        for v in value:
            if v not in valid_ids:
                issues.append(f"{prefix}{field_name} references unknown ID {v}")
    elif value not in valid_ids:
        issues.append(f"{prefix}{field_name} references unknown ID {value}")

    return issues


def detect_cycles(
    items: list[dict],
    id_field: str = "id",
    parent_field: str = "prerequisite_id",
    max_depth: int = 20,
) -> list[str]:
    """Detect circular chains in a list of items with parent references.

    Each item should have *id_field* and optionally *parent_field*.
    Returns a list of issue strings for any cycles found.
    """
    issues: list[str] = []
    id_to_item = {item[id_field]: item for item in items if id_field in item}

    for item in items:
        item_id = item.get(id_field)
        parent = item.get(parent_field)
        if not parent:
            continue

        visited: set = set()
        current = parent
        depth = 0

        while current and depth < max_depth:
            if current in visited:
                issues.append(
                    f"Circular chain detected: {item_id} -> ... -> {current} -> (cycle)"
                )
                break
            visited.add(current)
            parent_item = id_to_item.get(current)
            if parent_item is None:
                issues.append(
                    f"{item_id}: {parent_field} references non-existent {current}"
                )
                break
            current = parent_item.get(parent_field)
            depth += 1

    return issues
