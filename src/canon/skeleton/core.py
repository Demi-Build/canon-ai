"""Declarative skeleton system for canon.

A *skeleton* is the mechanical pre-roll for an entity: the small set of
rolled values (type, size, damage die, range, etc.) that the LLM-driven
narrative generation later wraps prose around. Canon models skeletons
*declaratively* — users describe the shape of an entity's pre-rolls as data
(``SkeletonSpec`` of ``SkeletonField`` entries) rather than writing a custom
imperative roll function per entity type.

Why declarative:

* **Serializable.** A ``SkeletonSpec`` is a Pydantic model; cradle (or any
  GUI tool) can introspect it to surface *why* a value was rolled and edit
  the distribution without touching Python.
* **Diffable.** Two specs can be compared field-by-field across game
  versions.
* **Game-agnostic.** Canon owns the rolling machinery; users own the
  schema. There are no hardcoded entity types here.

For derivations the declarative form can't express in v0.1 (probability
gates, multi-input computed fields, conditional choices), ``SkeletonSpec``
exposes a ``post: Callable[[dict], dict]`` escape hatch. v0.2 will replace
this with a richer DSL — see the ``# TODO(v0.2)`` markers throughout.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from typing import Any, TypeAlias

from pydantic import BaseModel, ConfigDict, model_validator

# A weighted choice is a (value, weight) pair. ``weight`` is a float so users
# can express probabilities directly (e.g. 0.1) or relative integer weights
# (e.g. 3, 2, 1) interchangeably — ``random.choices`` normalizes either.
Weighted: TypeAlias = tuple[Any, float]


class SkeletonField(BaseModel):
    """Declarative spec for a single pre-rolled field.

    Exactly one of ``choices``, ``range``, or ``lookup`` must be set. If
    ``lookup`` is set, ``depends_on`` must name an *earlier* field in the
    enclosing :class:`SkeletonSpec` whose rolled value is used as the lookup
    key.

    Examples:
        ``SkeletonField(choices=[("light", 3), ("heavy", 2), ("sacred", 1)])``
            Weighted random pick — relative or normalized weights.
        ``SkeletonField(range=(8, 40))``
            Inclusive integer range (calls ``rng.randint``).
        ``SkeletonField(lookup={"light": "d6", "heavy": "d10"}, depends_on="kind")``
            Deterministic table lookup keyed off another rolled field.

    # TODO(v0.2): Add a declarative ``probability: float | None`` gate so a
    # field can be conditionally populated without a ``post`` callback. The
    # spike doc (api_verification_and_spike.md §1.8) calls this out explicitly
    # for MazeWorld's weapon ``magic_element`` field (10% chance).
    # TODO(v0.2): Allow ``lookup`` to key off an external context dict (e.g.
    # ``room_level``) in addition to other rolled fields. See
    # ``roll_skeleton`` TODO below.
    # TODO(v0.2): Allow ``depends_on`` to be a list of field names so a
    # computed field can derive from multiple inputs declaratively, instead of
    # the current single-key lookup table.
    """

    choices: list[Weighted] | None = None
    range: tuple[int, int] | None = None
    lookup: dict[str, Any] | None = None
    depends_on: str | None = None

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def _validate_exactly_one_mode(self) -> SkeletonField:
        modes = {
            "choices": self.choices is not None,
            "range": self.range is not None,
            "lookup": self.lookup is not None,
        }
        set_modes = [name for name, present in modes.items() if present]
        if len(set_modes) == 0:
            raise ValueError(
                "SkeletonField requires exactly one of `choices`, `range`, or "
                "`lookup` to be set; got none."
            )
        if len(set_modes) > 1:
            raise ValueError(
                "SkeletonField requires exactly one of `choices`, `range`, or "
                f"`lookup` to be set; got {set_modes!r}."
            )

        if self.lookup is not None and self.depends_on is None:
            raise ValueError(
                "SkeletonField with `lookup` must also set `depends_on` to "
                "name the field whose rolled value keys the lookup table."
            )
        if self.lookup is None and self.depends_on is not None:
            raise ValueError(
                "SkeletonField sets `depends_on` but no `lookup` table; "
                "`depends_on` is only meaningful for lookup fields."
            )

        if self.choices is not None and len(self.choices) == 0:
            raise ValueError("SkeletonField `choices` must not be empty.")

        if self.range is not None:
            low, high = self.range
            if low > high:
                raise ValueError(
                    f"SkeletonField `range` requires low <= high; got ({low}, {high})."
                )

        return self


class SkeletonSpec(BaseModel):
    """Declarative spec for an entity type's mechanical pre-rolls.

    ``fields`` is *insertion-ordered* (Python 3.7+ dicts preserve insertion
    order): earlier fields are rolled before later ones, so ``depends_on``
    relationships resolve cleanly. A ``lookup`` field whose ``depends_on``
    references a field declared *after* it raises ``ValueError`` at spec
    construction time — long before any roll happens.

    The ``post`` callback is the v0.1 escape hatch for derivations that
    can't yet be expressed declaratively. It receives the fully-rolled dict
    and returns a (possibly modified) dict that becomes the final result.

    # TODO(v0.2): Replace the ``post`` callback with a declarative DSL —
    # probability gates, conditional choices, computed fields — so specs
    # can be authored as pure data (JSON / TOML) without any Python. The
    # callback escape hatch is a v0.1 concession; the long-term goal is
    # specs that round-trip through serialization untouched.
    """

    entity_type: str
    fields: dict[str, SkeletonField]
    post: Callable[[dict], dict] | None = None

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    @model_validator(mode="after")
    def _validate_dependency_order(self) -> SkeletonSpec:
        # Every `lookup` field must depend on a field that appears *earlier*
        # in insertion order, so the dependency is already rolled by the time
        # we resolve the lookup. We surface ordering bugs at construction
        # time, not at roll time, so cradle / users see them immediately.
        seen: list[str] = []
        for name, field in self.fields.items():
            if field.depends_on is not None:
                if field.depends_on == name:
                    raise ValueError(
                        f"SkeletonField {name!r} depends on itself; "
                        "depends_on must reference a different, earlier field."
                    )
                if field.depends_on not in self.fields:
                    raise ValueError(
                        f"SkeletonField {name!r} declares depends_on="
                        f"{field.depends_on!r}, but no such field exists in "
                        f"the spec. Known fields: {list(self.fields)!r}."
                    )
                if field.depends_on not in seen:
                    raise ValueError(
                        f"SkeletonField {name!r} declares depends_on="
                        f"{field.depends_on!r}, but that field is declared "
                        f"*after* {name!r}. depends_on must reference an "
                        "earlier field so it has already been rolled."
                    )
            seen.append(name)
        return self


def roll_skeleton(spec: SkeletonSpec, rng: random.Random) -> dict:
    """Deterministic pre-roll for one entity.

    Iterates ``spec.fields`` in insertion order and rolls each according to
    its declared mode (``choices`` / ``range`` / ``lookup``). After all
    fields are rolled, ``spec.post`` (if set) is invoked with the rolled
    dict and its return value replaces the result.

    Args:
        spec: The :class:`SkeletonSpec` to roll.
        rng: A seeded :class:`random.Random`. Caller is responsible for
            seeding — canon never auto-seeds, because reproducibility is
            owned by the bible's seed and any silent re-seeding would break
            the chain.

    Returns:
        A dict of ``{field_name: rolled_value}`` (in insertion order),
        post-processed by ``spec.post`` if provided.

    Raises:
        KeyError: A ``lookup`` field's resolved key is not present in its
            lookup table. The message names the field and the missing key.

    # TODO(v0.2): Accept an optional ``context: dict | None = None`` so
    # specs can roll values that depend on outer state (e.g. ``room_level``,
    # campaign difficulty). The spike doc (§1.8) flags this for MazeWorld's
    # monster scaling. ``SkeletonField.lookup`` would gain the ability to
    # key off context entries, and ``post`` would receive the context too.
    """
    rolled: dict[str, Any] = {}

    for name, field in spec.fields.items():
        if field.choices is not None:
            values = [v for v, _ in field.choices]
            weights = [w for _, w in field.choices]
            rolled[name] = rng.choices(values, weights=weights, k=1)[0]
        elif field.range is not None:
            low, high = field.range
            rolled[name] = rng.randint(low, high)
        elif field.lookup is not None:
            # ``depends_on`` is guaranteed to be set and to reference an
            # earlier (already-rolled) field by SkeletonField + SkeletonSpec
            # validators — we don't re-check that invariant at roll time.
            assert field.depends_on is not None  # noqa: S101 — invariant
            key = rolled[field.depends_on]
            try:
                rolled[name] = field.lookup[key]
            except KeyError as exc:
                raise KeyError(
                    f"SkeletonField {name!r}: lookup table has no entry for "
                    f"key {key!r} (rolled from depends_on field "
                    f"{field.depends_on!r}). Known keys: "
                    f"{list(field.lookup)!r}."
                ) from exc
        else:  # pragma: no cover — SkeletonField validator forbids this state
            raise RuntimeError(
                f"SkeletonField {name!r} has no roll mode set; this should "
                "have been caught at construction time."
            )

    if spec.post is not None:
        rolled = spec.post(rolled)

    return rolled
