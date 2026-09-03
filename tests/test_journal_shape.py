"""Row P1-A6 — the ONE journal/ledger event shape (master §3.0-B; P0 paper P.8).

The paper's §P.8.6 gives eight worked one-line examples. Every one of them is a
FIXTURE here, and the assertion is that the code which emits that shape really
emits it — key for key, with only ``ts`` and content hashes allowed to vary.
Beside that: identity derivation from every actor form, half-up ``costCents``
rounding, per-backend accuracy, open vocabularies (an unknown ``genKind`` and an
unknown ``detail.kind`` both round-trip AND render), pre-A6 events read with
P.8.7's defaults and never rewritten, the reconciliation rule as a property over
a synthetic ledger, and cancelled runs' hash-less invisibility.

No test calls a real provider (doctrine 3: ``fake`` / ``none`` only). The two
worked examples whose figures come from paid backends (fal ``$0.039`` estimated,
PixelLab ``$0.0169`` measured) are driven through ``provenance.record`` with the
paper's own gen block — the stamping under test is ``record``'s, not the
provider's.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from canon import provenance, spend
from canon.agent.service import journal_turn_tokens
from canon.db_ops import update_db_row
from canon.packs.platformer import ops
from canon.provenance import (
    all_events,
    artifact_versions,
    cost_cents,
    identity_for,
    list_events,
    read_events,
    record,
    summarize_events,
)

REPO = Path(__file__).resolve().parents[1]

#: ``ts`` and content hashes vary per run; everything else in a worked example
#: is the contract.
VOLATILE = ("ts", "before_hash", "after_hash")


def _sans_volatile(event: dict) -> dict:
    return {k: v for k, v in event.items() if k not in VOLATILE}


def _last(pack: Path, kind: str) -> dict:
    events = [e for e in all_events(pack) if (e.get("detail") or {}).get("kind") == kind]
    assert events, f"no journal event with detail.kind {kind!r}"
    return events[-1]


@pytest.fixture(scope="module")
def built_pack(tmp_path_factory) -> Path:
    """A fresh $0 platformer tree — the same recipe the P0-6 suite uses."""
    out = tmp_path_factory.mktemp("a6_plat")
    subprocess.run(
        [
            sys.executable, "-m", "canon.packs.platformer.run_slice",
            "--backend", "fake", "--engine", "json", "--image-backend", "fake",
            "--music-backend", "none", "--sfx-backend", "none",
            "--num-stages", "1", "--num-levels", "1", "--num-enemies", "2",
            "--output-dir", str(out),
        ],
        check=True, capture_output=True, cwd=REPO,
    )
    return out


@pytest.fixture
def pack(built_pack: Path, tmp_path: Path) -> Path:
    copy = tmp_path / "pack"
    shutil.copytree(built_pack, copy)
    return copy


@pytest.fixture
def enemy_id(pack: Path) -> str:
    return sorted(p.stem for p in (pack / "enemy").glob("*.json"))[0]


# ---------------------------------------------------------------------------
# P.8.6 — the eight worked examples, reproduced by the code that emits them
# ---------------------------------------------------------------------------


class TestWorkedExamples:
    def test_today_a_user_field_edit_through_the_cradle_door(self, pack: Path, enemy_id: str) -> None:
        """Example 1 + 2 in one pass: the SAME verb, one call by a person and
        one by an agent specialist. ``identity`` is derived, never passed;
        ``source`` stays ``user`` for the agent edit (P.9 J5 — the correction
        pair filters by identity when it needs to)."""
        update_db_row(pack, "enemy", enemy_id, {"hp": 16}, actor="cradle:user")
        human = _last(pack, "db_update")
        assert _sans_volatile(human) == {
            "schema": 1,
            "artifact_id": f"enemy:{enemy_id}",
            "op": "edit",
            "source": "user",
            "actor": "cradle:user",
            "identity": "user",
            "detail": {"kind": "db_update", "type": "enemy",
                       "changed": human["detail"]["changed"]},
        }
        assert human["before_hash"].startswith("sha256:") and human["after_hash"].startswith("sha256:")

        update_db_row(
            pack, "enemy", enemy_id, {"hp": 20},
            actor="agent:mason/level_designer", session="mason",
        )
        agent = _last(pack, "db_update")
        assert agent["identity"] == "agent:mason/level_designer"
        assert agent["session"] == "mason"
        assert agent["actor"] == "agent:mason/level_designer"
        assert agent["source"] == "user", "P.9 J5: source stays user; identity carries the agent"
        assert agent["op"] == "edit"

    def test_editor_button_paid_image_fal_is_estimated(self, pack: Path, enemy_id: str) -> None:
        """Example 3, verbatim. fal reports no per-call cost, so the figure is
        the list price and the flag is ``estimated`` — four cents from $0.039,
        which is ``record``'s half-up rounding, not the caller's."""
        event = record(
            pack,
            artifact_id=f"enemy:{enemy_id}",
            op="regenerate",
            source="llm",
            actor="cradle:user",
            detail={"kind": "asset_generate"},
            before_hash="sha256:4b7e",
            after_hash="sha256:c0de",
            gen={
                "image_model": "fal-ai/nano-banana",
                "backend": "fal",
                "model": "fal-ai/nano-banana",
                "prompt_hash": "sha256:71aa",
                "cost_usd": 0.039,
                "cost_breakdown": {"llm_usd": 0, "image_usd": 0.039, "audio_usd": 0,
                                   "accuracy": {"image": "estimated"}},
            },
            gen_kind="image",
            accuracy="estimated",
        )
        assert event["identity"] == "user"
        assert event["genKind"] == "image"
        assert event["costCents"] == 4
        assert event["accuracy"] == "estimated"
        assert event["gen"]["backend"] == "fal" and event["gen"]["cost_usd"] == 0.039

    def test_agent_paid_generation_pixellab_is_measured(self, pack: Path, enemy_id: str) -> None:
        """Example 4: PixelLab reports ``usage.usd``, so the row is
        ``measured`` — and the W2.1 inputs manifest (refs/context/params) rides
        the gen block untouched, because nothing reads it as a schema."""
        manifest = {
            "refs": [{"tag": "@ember_ref", "role": "subject", "hash": "sha256:5a5a",
                      "origin": "board:boss_moods"}],
            "context": {"entity": f"enemy:{enemy_id}", "level": "l3", "stage": "s1"},
            "params": {"size": 64, "seed": "ember-7", "n": 3},
        }
        event = record(
            pack,
            artifact_id=f"enemy:{enemy_id}",
            op="regenerate",
            source="llm",
            actor="agent:mason/artist",
            session="mason",
            detail={"kind": "asset_generate"},
            before_hash="sha256:c0de",
            after_hash="sha256:e11a",
            gen={"image_model": "pixellab-pixflux", "backend": "pixellab", "model": "pixflux",
                 "cost_usd": 0.0169, **manifest},
            gen_kind="image",
            accuracy="measured",
        )
        assert event["identity"] == "agent:mason/artist" and event["session"] == "mason"
        assert event["costCents"] == 2 and event["accuracy"] == "measured"
        assert event["gen"]["refs"] == manifest["refs"], "W2.1 populates it; A6 must not touch it"
        assert event["gen"]["context"] == manifest["context"]

    @pytest.mark.parametrize(
        ("usd", "flag", "cents"),
        [(0.039, "estimated", 4), (0.0169, "measured", 2)],
    )
    def test_the_image_lane_is_metered_by_the_verb_that_emits_it(
        self, pack: Path, enemy_id: str, monkeypatch: pytest.MonkeyPatch,
        usd: float, flag: str, cents: int,
    ) -> None:
        """Examples 3 and 4 through the CODE, not through ``record``.

        The paid figures ($0.039 fal-estimated, $0.0169 PixelLab-measured) are
        forced onto the FAKE backend — no provider is called (doctrine 3) — so
        what is under test is the whole chain a real run takes: the producer's
        meter → ``ctx.stats.image_cost_usd`` → ``_cost_block`` → the event.
        This is the regression the hand-fed examples could not see: nothing in
        the platformer wrote ``image_cost_usd``, so every sprite/backdrop/
        animation event journalled ``costCents: 0`` whatever the backend billed,
        and ``accuracy`` was read off the producer WRAPPER (which has no
        ``last_cost_accuracy``) and so always came back ``measured``.
        """
        from canon.backends.testing import FakeImageBackend

        monkeypatch.setattr(FakeImageBackend, "last_cost", usd, raising=False)
        monkeypatch.setattr(FakeImageBackend, "last_cost_accuracy", flag, raising=False)
        before = len(all_events(pack))
        ops.generate_asset(pack, f"enemy:{enemy_id}", image_backend="fake", actor="cradle:user")
        event = all_events(pack)[before:][-1]
        assert event["genKind"] == "image"
        assert event["costCents"] == cents, "the image lane must reach the journal"
        assert event["accuracy"] == flag, "the flag is the BACKEND's, not the wrapper's"
        assert event["gen"]["cost_usd"] == pytest.approx(usd)
        assert event["gen"]["cost_breakdown"]["image_usd"] == pytest.approx(usd)
        assert event["gen"]["cost_breakdown"]["accuracy"]["image"] == flag
        # P.8.3: every generation event carries the hash of the prompt that ran,
        # not only of an edited override (which is None on a button reroll).
        assert str(event["gen"]["prompt_hash"]).startswith("sha256:")

    def test_a_fake_image_run_is_still_zero_and_measured(self, pack: Path, enemy_id: str) -> None:
        """The meter must not invent money: an unpriced fake reports no
        ``last_cost`` at all, so the row stays the honest $0 measured."""
        before = len(all_events(pack))
        ops.generate_asset(pack, f"enemy:{enemy_id}", image_backend="fake", actor="cradle:user")
        event = all_events(pack)[before:][-1]
        assert event["costCents"] == 0 and event["accuracy"] == "measured"

    def test_a_plan_batch_and_its_reverse_order_undo(self, pack: Path, enemy_id: str) -> None:
        """Example 5: two edits under one ``batchId``, then the batch restore
        in reverse ``ts`` order carrying a FRESH batchId + ``detail.undoes``."""
        with provenance.bind_batch("plan-7f3a"):
            update_db_row(pack, "enemy", enemy_id, {"hp": 21},
                          actor="agent:mason/level_designer", session="mason")
            update_db_row(pack, "enemy", enemy_id, {"patrol_range": 5},
                          actor="agent:mason/level_designer", session="mason")
        planned = [e for e in all_events(pack) if e.get("batchId") == "plan-7f3a"]
        assert len(planned) == 2
        assert {e["identity"] for e in planned} == {"agent:mason/level_designer"}

        undo = record(
            pack, artifact_id=f"enemy:{enemy_id}", op="restore", source="user",
            actor="cradle:user",
            detail={"kind": "row_restore", "to": planned[0]["before_hash"], "undoes": "plan-7f3a"},
            before_hash=planned[-1]["after_hash"], after_hash=planned[0]["before_hash"],
            batch_id="undo-7f3a",
        )
        assert undo["batchId"] == "undo-7f3a" and undo["detail"]["undoes"] == "plan-7f3a"
        assert undo["identity"] == "user" and "costCents" not in undo

    def test_an_accepted_tuning_and_a_hand_pixel_edit_round_trip(self, pack: Path, enemy_id: str) -> None:
        """Examples 6 + 7 — W2.1 EMITS these; A6 only owes them the open
        vocabulary. Both are recorded and read back with every key intact,
        which is the whole guarantee (P.8.4)."""
        tuning = record(
            pack, artifact_id="manifest", op="edit", source="user", actor="cradle:user",
            session="mason",
            detail={"kind": "accepted_tuning", "scope": "pack",
                    "changed": {"movement.gravity": {"from": 40.0, "to": 32.0}},
                    "proposed_by": "agent:mason/level_designer", "proposal_ref": "run-91c2"},
            before_hash="sha256:m001", after_hash="sha256:m002",
        )
        pixel = record(
            pack, artifact_id=f"enemy:{enemy_id}", op="edit", source="user", actor="cradle:user",
            detail={"kind": "pixel_edit", "mode": "sprite", "state": "jump", "frame": 3,
                    "changed_px": 412, "off_palette_px": 38},
            before_hash="sha256:e11a", after_hash="sha256:f2f2",
        )
        stored = {e["ts"]: e for e in read_events(pack)}
        assert stored[tuning["ts"]]["detail"] == tuning["detail"]
        assert stored[pixel["ts"]]["detail"] == pixel["detail"]
        assert tuning["identity"] == "user" and tuning["source"] == "user"
        assert pixel["op"] == "edit", "P.8.4: hand-pixel work is never an import"

    def test_a_conversation_token_row(self, pack: Path) -> None:
        """Example 8: hash-less, ``artifact_id: conversation:<id>``,
        ``genKind: tokens``, measured — and emitted by the SERVICE's own
        metering, which prices through ``canon.pricing`` and nothing else."""
        event = journal_turn_tokens(
            pack, "mason",
            {"input_tokens": 2140, "output_tokens": 380},
            model="claude-sonnet-4-6", backend_id="anthropic", turn=14,
        )
        assert event is not None
        assert event["artifact_id"] == "conversation:mason"
        assert event["op"] == "generate" and event["source"] == "llm"
        assert event["identity"] == "agent:mason/foreman" and event["session"] == "mason"
        assert event["genKind"] == "tokens" and event["accuracy"] == "measured"
        assert event["detail"] == {"kind": "turn", "turn": 14}
        assert "before_hash" not in event and "after_hash" not in event
        assert event["gen"]["input_tokens"] == 2140 and event["gen"]["output_tokens"] == 380
        assert event["costCents"] == cost_cents(event["gen"]["cost_usd"]) > 0

    def test_a_turn_that_burned_nothing_is_not_a_cost_row(self, pack: Path) -> None:
        assert journal_turn_tokens(pack, "mason", {}, model="claude-sonnet-4-6",
                                   backend_id="anthropic", turn=1) is None

    def test_a_cancelled_turn_still_meters_its_burn(self, pack: Path) -> None:
        event = journal_turn_tokens(
            pack, "mason", {"input_tokens": 900, "output_tokens": 40},
            model="claude-sonnet-4-6", backend_id="anthropic", turn=2, cancelled=True,
        )
        assert event is not None and event["detail"]["cancelled"] is True
        assert event["genKind"] == "tokens"


# ---------------------------------------------------------------------------
# identity · costCents · accuracy
# ---------------------------------------------------------------------------


class TestFieldRules:
    @pytest.mark.parametrize(
        ("actor", "expected"),
        [
            ("user", "user"),
            ("cradle", "user"),
            ("cradle:user", "user"),
            ("user:u_1234", "user"),
            ("", "user"),
            (None, "user"),
            ("agent:mason/artist", "agent:mason/artist"),
            ("agent:mason", "agent:mason"),
        ],
    )
    def test_identity_is_a_pure_function_of_actor(self, actor: str | None, expected: str) -> None:
        assert identity_for(actor) == expected

    def test_no_write_verb_takes_an_identity_flag(self) -> None:
        """P.8.2: identity is computed inside ``record`` from ``actor``. If it
        were ever a parameter, a caller could disagree with the journal about
        who acted — so ``record`` must not accept one, and no verb may offer
        one. The single ``--identity`` in the CLI is ``journal list``'s READ
        filter, which selects events rather than authoring them."""
        import inspect

        assert "identity" not in inspect.signature(record).parameters
        cli = (REPO / "src" / "canon" / "cli" / "main.py").read_text(encoding="utf-8")
        assert cli.count('"--identity"') == 1, (
            "the ONE --identity in the CLI is `journal list`'s read filter; a write verb that "
            "grew one would let a caller author an identity the journal disagrees with"
        )
        # …and it really is the read verb's: the nearest `def` above it.
        before = cli[: cli.index('"--identity"')]
        assert before.rsplit("def ", 1)[-1].startswith("journal_list")

    @pytest.mark.parametrize(
        ("usd", "cents"),
        [(0.039, 4), (0.0169, 2), (0.01212, 1), (0.005, 1), (0.004, 0), (0.0, 0),
         (1.0, 100), (0.125, 13), (-1.0, 0), (None, None)],
    )
    def test_cost_cents_rounds_half_up(self, usd: float | None, cents: int | None) -> None:
        assert cost_cents(usd) == cents

    def test_a_costed_event_without_an_accuracy_flag_is_a_write_time_error(self, pack: Path) -> None:
        with pytest.raises(ValueError, match="accuracy"):
            record(pack, artifact_id="x", op="generate", source="llm", actor="user",
                   gen={"cost_usd": 0.02})

    def test_fake_backends_are_zero_and_measured(self, pack: Path) -> None:
        """A fake run really did cost nothing, so ``$0 measured`` is honest —
        and it still lands in the journal (P.8.2: $0 fake runs are costed
        events, absent means "not a cost row", never "free")."""
        before = len(all_events(pack))
        ops.place_enemies(pack, level_id="l1", backend="fake", actor="cradle:user")
        fresh = all_events(pack)[before:]
        costed = [e for e in fresh if "costCents" in e]
        assert len(costed) == 1, "one op is one billable leg, however many steps it wrote"
        assert costed[0]["costCents"] == 0 and costed[0]["accuracy"] == "measured"
        assert all(e["genKind"] == "text" for e in fresh), "P.9 J4: LLM-authored DATA is text, not code"

    def test_a_multi_step_op_is_ONE_costed_row(self, pack: Path) -> None:
        """One op = one billable leg. ``place_enemies`` journals a step event
        per changed file; only the first carries the money, so the dashboard
        cannot count one run three times."""
        before = len(all_events(pack))
        ops.place_items(pack, level_id="l1", backend="fake", actor="cradle:user")
        fresh = all_events(pack)[before:]
        assert len(fresh) > 1, "the op writes several step artifacts"
        assert sum(1 for e in fresh if "costCents" in e) == 1
        assert all(e.get("genKind") == "text" for e in fresh)

    def test_accuracy_per_backend(self) -> None:
        """LLM/VLM token counts and PixelLab / Retro's reported figures are
        MEASURED; fal (and the flat list-price backends, P.9 J3) are
        ESTIMATED. The flag comes off the backend object row P0-7 stamped."""
        from canon import pricing

        class Stub:
            def __init__(self, flag: str) -> None:
                self.last_cost_accuracy = flag

        assert provenance.backend_accuracy(Stub(pricing.MEASURED)) == pricing.MEASURED
        assert provenance.backend_accuracy(Stub(pricing.ESTIMATED)) == pricing.ESTIMATED
        assert provenance.backend_accuracy(None) is None
        # one estimated component makes the whole row estimated; absent
        # components cannot make it worse
        assert provenance.combine_accuracy(pricing.MEASURED, None) == pricing.MEASURED
        assert provenance.combine_accuracy(pricing.MEASURED, pricing.ESTIMATED) == pricing.ESTIMATED
        assert provenance.combine_accuracy() == pricing.MEASURED
        # and the price table agrees about who reports their own cost
        assert pricing.IMAGE["pixellab/pixflux"]["measured_by_provider"] is True
        assert pricing.IMAGE["retro-diffusion"]["measured_by_provider"] is True
        assert pricing.image("fal-ai/nano-banana")["measured_by_provider"] is False

    def test_a_paid_backend_with_no_price_row_still_writes_its_event(self, pack: Path) -> None:
        """P.8.2's never-lose-the-write rule: hashes intact, no ``costCents``,
        and ``detail.cost_error`` says why. The dashboard renders it as an
        unpriced run — never a confident $0."""
        event = record(
            pack, artifact_id="enemy:x", op="regenerate", source="llm", actor="cradle:user",
            detail={"kind": "asset_generate"},
            before_hash="sha256:aaa", after_hash="sha256:bbb",
            gen={"backend": "fal", "model": "fal-ai/unheard-of", "cost_usd": 0.0},
            gen_kind="image", accuracy="estimated",
            cost_error="fal: no price row for 'fal-ai/unheard-of' in canon.pricing",
        )
        assert "costCents" not in event and "accuracy" not in event
        assert event["detail"]["cost_error"].startswith("fal:")
        assert event["before_hash"] == "sha256:aaa" and event["after_hash"] == "sha256:bbb"
        assert summarize_events(read_events(pack))["unpricedRuns"] == 1


# ---------------------------------------------------------------------------
# Open vocabularies (P.8.8) and pre-A6 compat (P.8.7)
# ---------------------------------------------------------------------------


class TestOpenVocabularyAndCompat:
    def test_an_unknown_gen_kind_and_detail_kind_round_trip_and_render(self, pack: Path) -> None:
        """``mesh`` joins at W2.2 as a VALUE. Nothing here may need editing for
        that to render — the roll-up groups by value and an unknown kind is its
        own row, never a dropped one."""
        record(pack, artifact_id="mesh:hero", op="generate", source="llm",
               actor="agent:wick/mesh_smith", session="wick",
               detail={"kind": "mesh_forge", "topology": "quad"},
               after_hash="sha256:m1",
               gen={"backend": "meshy", "model": "preview", "cost_usd": 0.12},
               gen_kind="mesh", accuracy="estimated")
        summary = summarize_events(read_events(pack))
        kinds = {row["genKind"]: row for row in summary["byKind"]}
        assert "mesh" in kinds and kinds["mesh"]["totalCents"] == 12
        assert kinds["mesh"]["backend"] == "meshy"
        assert provenance.GEN_KINDS and "mesh" not in provenance.GEN_KINDS, (
            "the launch list is DATA for labels — an unlisted kind still renders"
        )
        stored = [e for e in read_events(pack) if e["artifact_id"] == "mesh:hero"][0]
        assert stored["detail"] == {"kind": "mesh_forge", "topology": "quad"}

    def test_a_pre_a6_event_reads_with_defaults_and_is_never_rewritten(self, pack: Path) -> None:
        """P.8.7: identity is DERIVED at read time; every other A6 field stays
        absent (costCents absent = not a cost row, accuracy is never defaulted
        to measured, genKind absent is not a generation row even when op says
        generate). And the file on disk is untouched."""
        path = provenance.journal_path(pack)
        path.parent.mkdir(parents=True, exist_ok=True)
        legacy = {"schema": 1, "ts": "2026-08-01T00:00:00+00:00",
                  "artifact_id": "enemy:old", "op": "generate", "source": "llm",
                  "actor": "cradle:user", "after_hash": "sha256:old"}
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(legacy) + "\n")
        raw_before = path.read_bytes()

        read = [e for e in read_events(pack) if e["artifact_id"] == "enemy:old"][0]
        assert read["identity"] == "user"
        for absent in ("costCents", "accuracy", "genKind", "batchId", "session"):
            assert absent not in read
        assert path.read_bytes() == raw_before, "read-time defaults never rewrite the file"

        summary = summarize_events(read_events(pack))
        assert summary["costedEvents"] == 0, "an uncosted legacy row counts nowhere"

    def test_list_events_applies_every_p87_filter(self, pack: Path) -> None:
        record(pack, artifact_id="enemy:a", op="edit", source="user", actor="cradle:user",
               detail={"kind": "db_update"}, after_hash="sha256:a")
        record(pack, artifact_id="enemy:b", op="generate", source="llm",
               actor="agent:mason/artist", session="mason", after_hash="sha256:b",
               gen={"cost_usd": 0.01}, gen_kind="image", accuracy="measured")
        record(pack, artifact_id="conversation:mason", op="generate", source="llm",
               actor="agent:mason/foreman", session="mason",
               gen={"cost_usd": 0.02}, gen_kind="tokens", accuracy="measured")

        assert all(e["identity"] == "agent:mason/artist"
                   for e in list_events(pack, identity="agent:mason/artist"))
        assert all(e["session"] == "mason" for e in list_events(pack, session="mason"))
        assert [e["artifact_id"] for e in list_events(pack, gen_kind="tokens")] == ["conversation:mason"]
        assert all(e["artifact_id"].startswith("conversation:")
                   for e in list_events(pack, artifact_prefix="conversation:"))
        assert list_events(pack, since="2999-01-01T00:00:00+00:00") == []
        assert len(list_events(pack, limit=2)) == 2
        assert list_events(pack, limit=2) == list_events(pack)[-2:], "limit keeps the NEWEST"


# ---------------------------------------------------------------------------
# Reconciliation (P.8.7) — the tables sum ONE field, so they cannot disagree
# ---------------------------------------------------------------------------


def _synthetic(n: int) -> list[dict]:
    """A ledger with every awkward case in it: two identities, three
    specialists, tokens beside generation, an unknown kind, uncosted rows, an
    unpriced row, and a hash-less cancelled row."""
    identities = ["user", "cradle:user", "agent:mason/artist",
                  "agent:mason/level_designer", "agent:wick/writer"]
    kinds = ["image", "animation", "audio", "text", "tokens", "mesh", "video"]
    out: list[dict] = []
    for i in range(n):
        actor = identities[i % len(identities)]
        kind = kinds[i % len(kinds)]
        event: dict[str, Any] = {
            "schema": 1,
            "ts": f"2026-09-{(i % 28) + 1:02d}T12:00:00+00:00",
            "artifact_id": f"enemy:e{i}",
            "op": "generate",
            "source": "llm",
            "actor": actor,
            "identity": identity_for(actor),
            "genKind": kind,
            "gen": {"backend": "fal" if i % 2 else "pixellab", "model": f"m{i % 3}",
                    "cost_usd": round(i * 0.0137, 6)},
            "accuracy": "estimated" if i % 2 else "measured",
            "costCents": cost_cents(round(i * 0.0137, 6)),
        }
        if actor.startswith("agent:"):
            event["session"] = actor.split(":", 1)[1].split("/", 1)[0]
        if i % 11 == 0:  # an uncosted row: History yes, dashboard no
            event.pop("costCents"), event.pop("accuracy")
        if i % 17 == 0:  # a run the price module could not price
            event["detail"] = {"kind": "asset_generate", "cost_error": "fal: no price row"}
            event.pop("costCents", None), event.pop("accuracy", None)
        out.append(event)
    return out


class TestReconciliation:
    @pytest.mark.parametrize("n", [1, 7, 40, 123])
    def test_every_table_sums_the_same_field(self, n: int) -> None:
        events = _synthetic(n)
        s = summarize_events(events, today="2026-09-05")
        one_number = sum(e["costCents"] for e in events if "costCents" in e)

        assert s["totalCents"] == one_number
        assert s["tokensCents"] + s["generationCents"] == one_number
        assert s["youCents"] + s["agentCents"] == s["generationCents"]
        assert sum(r["totalCents"] for r in s["byKind"]) == one_number
        assert sum(r["totalCents"] for r in s["byIdentity"]) == one_number
        assert sum(r["youCents"] + r["agentCents"] for r in s["byKind"]) == one_number
        # by-conversation covers the agent lanes exactly (human rows have no
        # conversation — that is the door they came through, not a gap)
        assert sum(r["totalCents"] for r in s["byConversation"]) == sum(
            r["totalCents"] for r in s["byIdentity"] if r["kind"] == "agent"
        )
        assert s["todayCents"] == sum(
            e["costCents"] for e in events
            if "costCents" in e and e["ts"].startswith("2026-09-05")
        )
        assert sum(s["accuracyCents"].values()) == one_number

    def test_unconfirmed_and_unpriced_rows_are_counted_nowhere(self) -> None:
        events = _synthetic(40)
        s = summarize_events(events)
        assert s["costedEvents"] == sum(1 for e in events if "costCents" in e)
        assert s["unpricedRuns"] == sum(
            1 for e in events if (e.get("detail") or {}).get("cost_error")
        )
        assert s["unpricedRuns"] > 0 and s["eventCount"] == len(events)

    def test_the_spend_ledger_becomes_a_derived_index(self, pack: Path) -> None:
        """P.8.7: a spend row derived from a journal event carries
        ``journal_ref``, which is what stops a reconciler counting it twice.
        ``summarize()`` is untouched — the ledger's own shape did not change."""
        event = record(
            pack, artifact_id="enemy:z", op="regenerate", source="llm",
            actor="agent:mason/artist", session="mason", after_hash="sha256:z",
            gen={"backend": "pixellab", "model": "pixflux", "cost_usd": 0.0169,
                 "input_tokens": 0, "output_tokens": 0, "calls": 1},
            gen_kind="image", accuracy="measured",
        )
        row = spend.spend_row_from_journal(event, op="sprite", scope="asset")
        assert row["journal_ref"] == event["ts"]
        assert row["identity"] == "agent:mason/artist" and row["session"] == "mason"
        assert row["category"] == "generation" and row["accuracy"] == "measured"
        assert row["actual_usd"] == 0.0169 and row["op"] == "sprite"
        spend.record_spend(pack, row)
        stored = spend.read_spend(pack)[-1]
        assert stored["schema"] == spend.SCHEMA and stored["journal_ref"] == event["ts"]
        # the one-number rule: pre-A6 rows (no journal_ref) are the only ones a
        # reconciler adds to the journal's costCents total
        assert [r for r in spend.read_spend(pack) if not r.get("journal_ref")] == []

    def test_a_token_row_derives_the_tokens_category(self, pack: Path) -> None:
        event = journal_turn_tokens(pack, "mason", {"input_tokens": 100, "output_tokens": 10},
                                    model="claude-sonnet-4-6", backend_id="anthropic", turn=1)
        assert event is not None
        row = spend.spend_row_from_journal(event)
        assert row["category"] == "tokens" and row["genKind"] == "tokens"
        assert row["tokens"] == {"input": 100, "output": 10, "calls": 1}


# ---------------------------------------------------------------------------
# P.8.5 — cancelled runs are hash-less, and hash-less means invisible
# ---------------------------------------------------------------------------


class TestCancelled:
    def test_a_cancelled_item_costs_but_creates_no_version(self, pack: Path, enemy_id: str) -> None:
        target = f"enemy:{enemy_id}"
        before = artifact_versions(pack, target)
        event = record(
            pack, artifact_id=target, op="regenerate", source="llm",
            actor="agent:mason/artist", session="mason",
            detail={"kind": "asset_generate", "cancelled": True},
            before_hash="sha256:c0de",
            gen={"backend": "pixellab", "model": "pixflux", "cost_usd": 0.0084},
            gen_kind="image", accuracy="measured",
        )
        assert "after_hash" not in event, "P.8.5: the in-flight item has no result"
        assert event["costCents"] == 1 and event["detail"]["cancelled"] is True
        assert artifact_versions(pack, target) == before, (
            "hash-less events are invisible to the version chain — nothing to restore to"
        )
        assert any(e.get("costCents") == 1 for e in read_events(pack))

    def test_a_cancelled_event_is_not_a_change_signal(self, pack: Path, enemy_id: str) -> None:
        """``_change_signal`` (what the job tray's "did it change anything?"
        reads) must ignore hash-less rows: they carry money, not new bytes."""
        cursor = len(all_events(pack))
        record(pack, artifact_id=f"enemy:{enemy_id}", op="regenerate", source="llm",
               actor="cradle:user", detail={"kind": "asset_generate", "cancelled": True},
               gen={"cost_usd": 0.01}, gen_kind="image", accuracy="measured")
        assert ops._change_signal(pack, cursor) == {"changed": False, "changed_artifacts": []}

    def test_the_jobs_ledger_gained_the_value_not_a_schema(self) -> None:
        from canon import jobs

        assert "cancelled" in jobs.STATUSES
        assert jobs.SCHEMA == "cradle-jobs/v1", "P.8.7: schema strings unchanged"
        assert spend.SCHEMA == "cradle-spend/v1"
        assert provenance.SCHEMA_VERSION == 1, "§3.0-B: schema stays 1; A6 is additive"


# ---------------------------------------------------------------------------
# The verb: `canon journal list`
# ---------------------------------------------------------------------------


def test_canon_journal_list_is_a_pure_read_with_a_summary(pack: Path) -> None:
    ops.place_enemies(pack, level_id="l1", backend="fake", actor="cradle:user")
    before = provenance.journal_path(pack).read_bytes()

    def run(*flags: str) -> dict:
        result = subprocess.run(
            [sys.executable, "-m", "canon.cli.main", "journal", "list", str(pack), *flags],
            capture_output=True, text=True, cwd=REPO,
        )
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    plain = run()
    assert plain["result"] == "journal_list"
    assert all(e.get("identity") for e in plain["events"])

    document = run("--summary")
    assert document["result"] == "journal_list"
    assert set(document["summary"]) >= {
        "totalCents", "generationCents", "tokensCents", "todayCents",
        "youCents", "agentCents", "byKind", "byIdentity", "byConversation",
    }
    # BUILD 2: the roll-up REPLACES the event list — a --summary that also
    # shipped every event would cost more than not passing the flag.
    assert "events" not in document
    # …unless the caller bounded the read itself, which is how a client asks
    # for both.
    both = run("--summary", "--limit", "5")
    assert len(both["events"]) <= 5 and both["summary"]["totalCents"] >= 0
    assert provenance.journal_path(pack).read_bytes() == before, "read verbs write nothing"
