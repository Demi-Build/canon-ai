"""``canon.pricing`` — the single price/constants module (master §3.0-C,
row P0-7): every row sourced + dated, the LOUD unpriced rule, the per-kind
accessors, Meshy credit math + the env override, the accuracy constants,
the backends' price VIEWS and ``last_cost_accuracy``."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest

from canon import pricing


class TestTables:
    def test_every_row_has_source_and_verified(self) -> None:
        rows = pricing.all_rows()
        assert len(rows) >= 30
        for kind, model, row in rows:
            assert row.get("source"), (kind, model)
            assert row.get("verified") == pricing.VERIFIED, (kind, model)
            assert row.get("accuracy") == pricing.ESTIMATED, (kind, model)
            assert row.get("provider"), (kind, model)

    def test_seeded_rows_match_the_approved_table(self) -> None:
        # §1 LLM per-1M
        assert pricing.llm("claude-fable-5")["input_per_1m"] == 10.0
        assert pricing.llm("claude-fable-5")["output_per_1m"] == 50.0
        assert pricing.llm("claude-opus-5")["input_per_1m"] == 5.0
        assert pricing.llm("claude-opus-4-8")["output_per_1m"] == 25.0
        assert pricing.llm("claude-opus-4-7")["output_per_1m"] == 25.0
        assert pricing.llm("claude-sonnet-5") == pytest.approx(
            {**pricing.llm("claude-sonnet-5"), "input_per_1m": 2.0, "output_per_1m": 10.0}
        )
        assert pricing.llm("claude-sonnet-4-6")["input_per_1m"] == 3.0
        assert pricing.llm("claude-haiku-4-5")["input_per_1m"] == 1.0
        assert pricing.llm("claude-haiku-4-5-20251001")["output_per_1m"] == 5.0
        assert pricing.llm("gpt-5.1")["input_per_1m"] == 1.25
        assert pricing.llm("gpt-5.1")["cache_read_per_1m"] == 0.125
        assert pricing.llm("gpt-5.4-mini")["output_per_1m"] == 4.5
        assert pricing.llm("gpt-5.4-nano")["input_per_1m"] == 0.2
        assert pricing.llm("kimi-k3")["cache_read_per_1m"] == 0.3
        assert pricing.llm("kimi-k2.6")["input_per_1m"] == 0.95
        assert pricing.llm("kimi-k2.7-code")["output_per_1m"] == 4.0
        assert pricing.llm("kimi-k2") is None  # retired — never targeted
        # §2 images
        assert pricing.image("fal-ai/nano-banana")["usd"] == 0.039
        assert pricing.image("fal-ai/nano-banana/edit")["usd"] == 0.039
        assert pricing.image("fal-ai/nano-banana-2")["usd"] == 0.08
        assert pricing.image("fal-ai/nano-banana-2")["by_resolution"]["4K"] == 0.16
        assert pricing.image("fal-ai/nano-banana-pro")["usd"] == 0.15
        # §3 audio
        assert pricing.music("lyria-3-pro-preview")["usd"] == 0.08
        assert pricing.music("lyria-3-clip-preview")["usd"] == 0.04
        assert pricing.sfx("elevenlabs")["usd"] == 0.04
        assert pricing.sfx("elevenlabs/per-second")["per"] == "second"
        assert pricing.sfx("elevenlabs/per-second")["usd"] == 0.008

    def test_provider_reported_rows_carry_the_published_range_as_estimate(self) -> None:
        for model in ("pixellab", "pixellab/pixflux", "retro-diffusion"):
            row = pricing.image(model)
            assert row["measured_by_provider"] is True, model
            assert row["usd"] < row["usd_high"], model
        assert pricing.image("pixellab")["usd"] == 0.008
        assert pricing.image("pixellab")["usd_high"] == 0.185
        assert pricing.image("retro-diffusion")["usd"] == 0.015
        assert pricing.image("retro-diffusion")["usd_high"] == 0.18
        assert pricing.image("retro-diffusion/rd_pro")["usd"] == 0.18
        assert pricing.image("fal-ai/nano-banana")["measured_by_provider"] is False

    def test_family_lookup_prices_the_backend_model_ids(self) -> None:
        # the retro backend's `model` is "retro-diffusion/<prompt_style>"
        assert pricing.image("retro-diffusion/rd_pro__platformer")["usd"] == 0.18
        assert pricing.image("retro-diffusion/rd_fast__default")["usd"] == 0.015
        assert pricing.image("pixellab/pixflux-64")["usd"] == 0.008
        assert pricing.image("unknown-provider/model") is None

    def test_ids_are_data_not_literals(self) -> None:
        import inspect

        src = inspect.getsource(pricing)
        assert "Literal[" not in src and "import Literal" not in src
        for kind, table in pricing.TABLES.items():
            assert isinstance(kind, str) and isinstance(table, dict)

    def test_accessors_return_copies(self) -> None:
        row = pricing.llm("claude-sonnet-4-6")
        row["input_per_1m"] = 999
        assert pricing.llm("claude-sonnet-4-6")["input_per_1m"] == 3.0


class TestUnpricedRule:
    def test_unpriced_model_warns_naming_model_and_table(self) -> None:
        warnings: list[str] = []
        assert pricing.price_for("llm", "not-a-model", warnings) is None
        assert len(warnings) == 1
        assert "'not-a-model'" in warnings[0]
        assert "canon.pricing.LLM" in warnings[0]
        assert "estimated" in warnings[0]
        assert pricing.price_for("image", "fal-ai/nope", warnings) is None
        assert "canon.pricing.IMAGE" in warnings[1]
        assert pricing.price_for("no-such-kind", "x", warnings) is None
        assert len(warnings) == 3

    def test_zero_row_is_flagged_estimated_never_silent(self) -> None:
        for kind in ("llm", "vlm", "image", "music", "sfx", "mesh"):
            row = pricing.zero_row(kind)
            assert row["accuracy"] == pricing.ESTIMATED
            assert row["unpriced"] is True
            assert row.get("usd", 0.0) == 0.0 and row.get("input_per_1m", 0.0) == 0.0

    def test_priced_model_appends_nothing(self) -> None:
        warnings: list[str] = []
        row = pricing.price_for("music", "lyria-3-clip-preview", warnings)
        assert row["usd"] == 0.04 and warnings == []


class TestMeshy:
    def test_credit_math_at_the_default_rate(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(pricing.MESHY_USD_PER_CREDIT_ENV, raising=False)
        assert pricing.meshy_usd_per_credit() == 0.02
        assert pricing.mesh("image-to-3d/mesh")["credits"] == 20
        assert pricing.mesh("image-to-3d/mesh")["usd"] == pytest.approx(0.40)
        assert pricing.mesh("image-to-3d/textured")["usd"] == pytest.approx(0.60)
        assert pricing.mesh("image-to-3d/textured-8k")["usd"] == pytest.approx(0.70)
        assert pricing.mesh("texture")["usd"] == pytest.approx(0.20)
        assert pricing.mesh("texture-8k")["usd"] == pytest.approx(0.30)
        assert pricing.mesh("rig")["usd"] == pytest.approx(0.10)
        assert pricing.mesh("animation")["usd"] == pytest.approx(0.06)

    def test_env_override_is_the_dashboard_confirm_knob(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(pricing.MESHY_USD_PER_CREDIT_ENV, "0.05")
        assert pricing.meshy_usd_per_credit() == 0.05
        assert pricing.mesh("rig")["usd"] == pytest.approx(0.25)
        assert pricing.mesh("rig")["usd_per_credit"] == 0.05
        monkeypatch.setenv(pricing.MESHY_USD_PER_CREDIT_ENV, "garbage")
        assert pricing.meshy_usd_per_credit() == 0.02


class TestAccuracyConstants:
    def test_plain_strings_compared_by_value(self) -> None:
        assert pricing.MEASURED == "measured"
        assert pricing.ESTIMATED == "estimated"
        assert type(pricing.MEASURED) is str and type(pricing.ESTIMATED) is str
        assert "".join(["meas", "ured"]) == pricing.MEASURED


class TestBackendDefaults:
    def test_paid_vs_unpaid_backends(self) -> None:
        assert pricing.is_paid("llm", "anthropic")
        assert pricing.is_paid("llm", "openai") and pricing.is_paid("llm", "kimi")
        assert pricing.is_paid("image", "fal") and pricing.is_paid("image", "retro")
        assert pricing.is_paid("image", "pixellab")
        assert pricing.is_paid("music", "lyria") and pricing.is_paid("sfx", "elevenlabs")
        for kind in ("llm", "vlm", "image", "music", "sfx"):
            for backend in ("fake", "none", "", None, "local"):
                assert not pricing.is_paid(kind, backend), (kind, backend)
        assert pricing.default_model("image", "FAL ") == "fal-ai/nano-banana"
        assert pricing.default_model("llm", "kimi") == "kimi-k2.6"

    def test_image_defaults_price_what_the_backend_actually_constructs(self) -> None:
        """The estimator's default SKU per paid image backend == the model id
        the backend class builds when nobody passes ``--image-model``, so a
        default-backend forecast cannot drift from the real spend (the retro
        default is RD Pro at $0.18, NOT the generic range row's RD-Fast
        floor). ``tileset_art._make_image_backend`` constructs these."""
        from canon.backends import image_fal, image_pixellab, image_retro_diffusion

        constructed = {
            "fal": image_fal.DEFAULT_MODEL,
            "retro": f"retro-diffusion/{image_retro_diffusion.DEFAULT_PROMPT_STYLE}",
            "retro-diffusion": f"retro-diffusion/{image_retro_diffusion.DEFAULT_PROMPT_STYLE}",
            "pixellab": f"pixellab/{image_pixellab.DEFAULT_MODEL}",
        }
        for backend, model in constructed.items():
            priced = pricing.image(pricing.default_model("image", backend))
            assert priced["usd"] == pricing.image(model)["usd"], backend
            assert priced["usd_high"] == pricing.image(model)["usd_high"], backend
        assert pricing.image(pricing.default_model("image", "retro"))["usd"] == 0.18

    def test_per_token_view(self) -> None:
        view = pricing.llm_per_token_view("anthropic")
        assert view["claude-sonnet-4-6"] == {"input": 3.0 / 1e6, "output": 15.0 / 1e6}
        assert "gpt-5.1" not in view
        assert "gpt-5.1" in pricing.llm_per_token_view()


# ---------------------------------------------------------------------------
# The backends read prices from here — views + last_cost_accuracy
# ---------------------------------------------------------------------------


class TestBackendViews:
    def test_anthropic_pricing_is_the_table_view(self) -> None:
        from canon.backends.anthropic import PRICING

        assert PRICING == pricing.llm_per_token_view("anthropic")
        assert PRICING["claude-haiku-4-5-20251001"]["input"] == pytest.approx(1e-6)
        assert "claude-sonnet-5" in PRICING and "claude-opus-5" in PRICING

    def test_lyria_pricing_is_the_table_view(self) -> None:
        from canon.backends.music_lyria import DEFAULT_MODEL_CLIP, DEFAULT_MODEL_PRO, PRICING

        assert PRICING[DEFAULT_MODEL_PRO] == 0.08 and PRICING[DEFAULT_MODEL_CLIP] == 0.04

    def test_elevenlabs_constant_is_the_table_row(self) -> None:
        from canon.backends.sfx_elevenlabs import COST_PER_EFFECT

        assert COST_PER_EFFECT == pricing.SFX["elevenlabs"]["usd"] == 0.04

    def test_anthropic_last_cost_is_measured(self) -> None:
        pytest.importorskip("anthropic")
        from canon.backends.anthropic import AnthropicBackend
        from canon.llm.request import LLMRequest

        class _Usage:
            input_tokens = 1000
            output_tokens = 10

        class _Resp:
            usage = _Usage()
            content: list = []

        class _Client:
            class messages:  # noqa: N801
                @staticmethod
                def create(**kwargs):
                    return _Resp()

        backend = AnthropicBackend(client=_Client(), model="claude-sonnet-5")
        assert backend.last_cost_accuracy == pricing.MEASURED
        backend.generate(LLMRequest(system="s", user_message="u"))
        assert backend.last_cost == pytest.approx(1000 * 2e-6 + 10 * 10e-6)
        assert backend.last_cost_accuracy == pricing.MEASURED
        backend.generate(LLMRequest(system="s", user_message="u", model="unpriced"))
        assert backend.last_cost == 0.0 and backend.last_cost_accuracy == pricing.ESTIMATED

    def test_fal_last_cost_comes_from_the_table_and_is_estimated(self, caplog) -> None:
        pytest.importorskip("fal_client")
        from canon.backends.image_fal import FalImageBackend

        backend = FalImageBackend()
        assert backend.last_cost == 0.0  # nothing served yet
        assert backend.last_cost_accuracy == pricing.ESTIMATED
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
        cm = MagicMock()
        cm.__enter__ = MagicMock(return_value=cm)
        cm.__exit__ = MagicMock(return_value=False)
        cm.read = MagicMock(return_value=png)
        with (
            patch.object(backend._fal, "subscribe", return_value={"images": [{"url": "http://x/i.png"}]}),
            patch("urllib.request.urlopen", return_value=cm),
        ):
            backend.generate("a", 64, 64)
            assert backend.last_cost == 0.039  # never 0 when a row exists
            assert backend.last_cost_accuracy == pricing.ESTIMATED
            with patch.object(backend._fal, "upload", return_value="http://x/up.png"):
                backend.edit(png, "p", 64, 64)
            assert backend.last_cost == 0.039
        unpriced = FalImageBackend(model="fal-ai/no-such-model")
        with (
            patch.object(unpriced._fal, "subscribe", return_value={"images": [{"url": "http://x/i.png"}]}),
            patch("urllib.request.urlopen", return_value=cm),
            caplog.at_level(logging.WARNING),
        ):
            unpriced.generate("a", 64, 64)
            unpriced.generate("a", 64, 64)
        assert unpriced.last_cost == 0.0 and unpriced.last_cost_accuracy == pricing.ESTIMATED
        assert sum("No canon.pricing.IMAGE row" in r.getMessage() for r in caplog.records) == 1

    def test_provider_reported_backends_are_measured(self) -> None:
        pytest.importorskip("requests")
        from canon.backends.image_pixellab import PixelLabBackend
        from canon.backends.image_retro_diffusion import RetroDiffusionBackend

        assert PixelLabBackend().last_cost_accuracy == pricing.MEASURED
        assert RetroDiffusionBackend().last_cost_accuracy == pricing.MEASURED

    def test_flat_list_price_backends_are_estimated(self) -> None:
        genai = pytest.importorskip("google.genai")
        from canon.backends.music_lyria import LyriaMusicBackend

        with patch.object(genai, "Client"):
            assert LyriaMusicBackend(api_key="k").last_cost_accuracy == pricing.ESTIMATED
        pytest.importorskip("elevenlabs")
        from canon.backends.sfx_elevenlabs import ElevenLabsSFXBackend

        with patch("elevenlabs.ElevenLabs"):
            assert ElevenLabsSFXBackend(api_key="k").last_cost_accuracy == pricing.ESTIMATED

    def test_lyria_last_cost_comes_from_the_table_and_is_loud_when_unpriced(self, caplog) -> None:
        """§3.0-B, the mirror of the fal case: a Lyria model with no
        ``canon.pricing.MUSIC`` row prices at $0 ``estimated`` with ONE
        warning naming the model and the table — never a silent $0."""
        genai = pytest.importorskip("google.genai")
        from canon.backends.music_lyria import LyriaMusicBackend

        with patch.object(genai, "Client"):
            backend = LyriaMusicBackend(api_key="k", model_pro="lyria-9-not-a-model")
        backend._note_cost("lyria-3-pro-preview")
        assert backend.last_cost == 0.08 and backend.last_cost_accuracy == pricing.ESTIMATED
        backend._note_cost("lyria-3-clip-preview")
        assert backend.last_cost == 0.04
        with caplog.at_level(logging.WARNING):
            backend._note_cost("lyria-9-not-a-model")
            backend._note_cost("lyria-9-not-a-model")
        assert backend.last_cost == 0.0 and backend.last_cost_accuracy == pricing.ESTIMATED
        messages = [r.getMessage() for r in caplog.records]
        assert sum("No canon.pricing.MUSIC row" in m for m in messages) == 1
        assert any("lyria-9-not-a-model" in m for m in messages)

    def test_no_other_module_carries_a_dollar_table(self) -> None:
        """§3.0-C: the backends' tables are views; the packs' cost models
        carry counts/tokens only."""
        import inspect

        from canon.backends import anthropic, music_lyria, sfx_elevenlabs

        for module in (anthropic, music_lyria, sfx_elevenlabs):
            src = inspect.getsource(module)
            assert "/ 1_000_000" not in src.replace("llm_per_token_view", ""), module.__name__
        for module in (music_lyria, sfx_elevenlabs):
            src = inspect.getsource(module)
            assert "0.08" not in src and "0.04  #" not in src, module.__name__
