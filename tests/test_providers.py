"""``canon.providers`` — the ONE provider-row table (row P0-12, master §6 S6).

W3.4 named six fixed provider rows; §6 S6 superseded that with "rows are
DATA". These tests pin the three things that makes real:

1. every September provider is a ROW (and the table, not a union, is what any
   consumer enumerates) — including ``MESHY_API_KEY`` with the corrected
   CC BY 4.0 licensing copy and Phase 1's chat-provider keys;
2. the ``PIXELLAB_SECRET`` / ``PIXELLAB_API_KEY`` pair agrees with what
   ``canon.backends.image_pixellab`` actually reads — the P0-12 var fix, pinned
   so the two can never drift again;
3. no surface here ever returns a key VALUE, and the key TEST never generates.
"""

from __future__ import annotations

import inspect

import pytest

from canon import providers


class TestTable:
    def test_every_september_provider_is_a_row(self) -> None:
        """The six September providers + Meshy + Phase 1's two chat providers."""
        by_var = {r["env_var"]: r for r in providers.PROVIDER_ROWS}
        for var in (
            "ANTHROPIC_API_KEY",
            "FAL_KEY",
            "GOOGLE_API_KEY",
            "ELEVENLABS_API_KEY",
            "PIXELLAB_SECRET",
            "RD_API_KEY",
            "MESHY_API_KEY",
            "OPENAI_API_KEY",
            "MOONSHOT_API_KEY",
        ):
            assert var in by_var, f"{var} is not a provider row"

    def test_every_row_is_complete(self) -> None:
        ids = set()
        for r in providers.PROVIDER_ROWS:
            for field in ("id", "label", "env_var", "unlocks", "docs"):
                assert r.get(field), (r.get("id"), field)
            assert r["id"] not in ids, f"duplicate provider id {r['id']}"
            ids.add(r["id"])
            assert isinstance(r.get("aliases"), list)
            assert isinstance(r.get("backends"), dict) and r["backends"]
            assert "note" in r and "test" in r

    def test_meshy_carries_the_corrected_licensing_copy(self) -> None:
        """``provider_price_table.md`` §4: the free tier is CC BY 4.0, so
        commercial use IS allowed with attribution. The older "paid tier
        required for commercial use" wording is wrong and must not appear."""
        note = providers.row("meshy")["note"]
        assert "CC BY 4.0" in note
        assert "full ownership / commercial use without attribution" in note
        assert "required for commercial use." not in note

    def test_rows_are_copies(self) -> None:
        rows = providers.provider_rows()
        rows[0]["label"] = "mutated"
        assert providers.PROVIDER_ROWS[0]["label"] != "mutated"

    def test_no_hardcoded_union_downstream(self) -> None:
        """``backend_key_vars`` and ``env_vars`` are DERIVED — adding a row is
        the whole edit. Proven by adding one and seeing both grow."""
        before_backends = providers.backend_key_vars()
        before_vars = providers.env_vars()
        extra = {
            "id": "demi",
            "label": "Demi gateway",
            "env_var": "DEMI_API_KEY",
            "aliases": [],
            "unlocks": "October's gateway.",
            "backends": {"chat": ["demi"]},
            "docs": "https://example.invalid",
            "note": "",
            "test": None,
        }
        providers.PROVIDER_ROWS.append(extra)
        try:
            assert providers.backend_key_vars()["chat"]["demi"] == "DEMI_API_KEY"
            assert "DEMI_API_KEY" in providers.env_vars()
            assert providers.key_var("demi") == "DEMI_API_KEY"
        finally:
            providers.PROVIDER_ROWS.remove(extra)
        assert providers.backend_key_vars() == before_backends
        assert providers.env_vars() == before_vars


class TestPixelLabAliasPair:
    """The P0-12 var fix: canon's canonical name is ``PIXELLAB_SECRET`` and the
    dashboard's ``PIXELLAB_API_KEY`` is its alias — pinned against the backend
    so the table and the reader cannot drift."""

    def test_canonical_var_and_alias(self) -> None:
        r = providers.row("pixellab")
        assert r["env_var"] == "PIXELLAB_SECRET"
        assert r["aliases"] == ["PIXELLAB_API_KEY"]

    def test_backend_reads_exactly_this_pair_in_this_order(self) -> None:
        from canon.backends import image_pixellab

        source = inspect.getsource(image_pixellab.PixelLabBackend)
        secret = source.index('"PIXELLAB_SECRET"')
        alias = source.index('"PIXELLAB_API_KEY"')
        assert secret < alias, "the backend must prefer PIXELLAB_SECRET"

    def test_resolve_prefers_the_canonical_var(self) -> None:
        env = {"PIXELLAB_SECRET": "canonical", "PIXELLAB_API_KEY": "alias"}
        assert providers.resolve_key("pixellab", env) == "canonical"
        assert providers.resolve_key("pixellab", {"PIXELLAB_API_KEY": "alias"}) == "alias"

    def test_key_status_names_which_var_answered(self) -> None:
        rows = {r["id"]: r for r in providers.key_status({"PIXELLAB_API_KEY": "alias"})}
        assert rows["pixellab"]["set"] is True
        assert rows["pixellab"]["set_via"] == "PIXELLAB_API_KEY"
        assert rows["anthropic"]["set"] is False
        assert rows["anthropic"]["set_via"] is None

    def test_key_status_never_carries_a_value_or_a_length(self) -> None:
        secret = "sk-do-not-leak-this"
        for r in providers.key_status({"ANTHROPIC_API_KEY": secret}):
            flat = repr(r)
            assert secret not in flat
            assert str(len(secret)) not in flat


class TestChatViewIsTheSameTable:
    def test_agent_key_envs_reads_the_rows(self) -> None:
        from canon.agent import providers as agent_providers

        envs = agent_providers.key_envs()
        assert envs["anthropic"] == "ANTHROPIC_API_KEY"
        assert envs["openai"] == "OPENAI_API_KEY"
        assert envs["kimi"] == "MOONSHOT_API_KEY"
        assert envs == {**envs, **providers.backend_key_vars()["chat"]}


class TestKeyTest:
    """User-initiated, cheapest possible, never a generation."""

    def test_a_row_without_an_endpoint_is_disabled_with_a_reason(self) -> None:
        out = providers.test_provider("fal", {"FAL_KEY": "x"}, fetch=_never)
        assert out["ran"] is False
        assert "never does" in out["reason"]

    def test_no_key_says_so_and_never_calls_out(self) -> None:
        out = providers.test_provider("anthropic", {}, fetch=_never)
        assert out == {
            "id": "anthropic",
            "ran": False,
            "ok": False,
            "status": None,
            "reason": "ANTHROPIC_API_KEY is not set in this process",
        }

    def test_ok_when_the_provider_accepts(self) -> None:
        seen: dict[str, object] = {}

        def fetch(url: str, headers: dict[str, str], timeout: float) -> tuple[int, str]:
            seen.update(url=url, headers=headers, timeout=timeout)
            return 200, ""

        out = providers.test_provider("anthropic", {"ANTHROPIC_API_KEY": "sk-secret"}, fetch=fetch)
        assert out["ok"] is True and out["status"] == 200
        # The key rides in a HEADER, never the URL.
        assert "sk-secret" not in str(seen["url"])
        assert seen["headers"]["x-api-key"] == "sk-secret"
        assert seen["headers"]["anthropic-version"] == "2023-06-01"

    def test_bearer_providers_carry_the_prefix(self) -> None:
        seen: dict[str, dict[str, str]] = {}

        def fetch(url: str, headers: dict[str, str], timeout: float) -> tuple[int, str]:
            seen["headers"] = headers
            return 200, ""

        providers.test_provider("openai", {"OPENAI_API_KEY": "sk-o"}, fetch=fetch)
        assert seen["headers"]["Authorization"] == "Bearer sk-o"

    def test_google_uses_a_header_never_a_query_key(self) -> None:
        seen: dict[str, object] = {}

        def fetch(url: str, headers: dict[str, str], timeout: float) -> tuple[int, str]:
            seen.update(url=url, headers=headers)
            return 200, ""

        providers.test_provider("lyria", {"GOOGLE_API_KEY": "g-secret"}, fetch=fetch)
        assert "?key=" not in str(seen["url"]) and "g-secret" not in str(seen["url"])
        assert seen["headers"]["x-goog-api-key"] == "g-secret"

    @pytest.mark.parametrize("status", [401, 403])
    def test_rejection_is_named(self, status: int) -> None:
        out = providers.test_provider("anthropic", {"ANTHROPIC_API_KEY": "x"}, fetch=lambda *_: (status, ""))
        assert out["ok"] is False and out["status"] == status
        assert "rejected" in out["reason"]

    def test_unreachable_is_named(self) -> None:
        out = providers.test_provider("anthropic", {"ANTHROPIC_API_KEY": "x"}, fetch=lambda *_: (0, "URLError"))
        assert out["ok"] is False and out["status"] is None
        assert "could not reach it" in out["reason"]

    def test_the_result_never_carries_the_key(self) -> None:
        out = providers.test_provider("anthropic", {"ANTHROPIC_API_KEY": "sk-leak"}, fetch=lambda *_: (200, ""))
        assert "sk-leak" not in repr(out)

    def test_every_declared_endpoint_is_https_and_read_only(self) -> None:
        for r in providers.PROVIDER_ROWS:
            spec = r["test"]
            if spec is None:
                continue
            assert spec["url"].startswith("https://"), r["id"]
            assert "key=" not in spec["url"], r["id"]
            assert spec["header"], r["id"]


def _never(*_args: object, **_kwargs: object) -> tuple[int, str]:
    raise AssertionError("the key test must not contact a provider here")
