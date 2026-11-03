"""``canon.pricing`` — the product's ONLY price source (master §3.0-C, born at
row P0-7).

Every number here is seeded from ``docs/provider_price_table.md`` (researched
and user-approved 2026-09-01; every row links its source and carries the date
it was verified). Nothing else in the codebase carries a dollar figure:

- the backends' price views (``canon.backends.anthropic.PRICING``,
  ``music_lyria.PRICING``, ``sfx_elevenlabs.COST_PER_EFFECT``) are re-exports
  built from these tables;
- the estimator engine (``canon.estimator``) prices every unit through
  :func:`price_for`; a pack's ``cost_model.json`` carries counts and tokens
  only, never a price;
- later phases add ROWS (Meshy at W2.2, audio/splice, …), never tables.

Tables are plain dicts of plain dicts — ids/kinds are open ``str`` vocab,
never ``Literal`` unions (doctrine 8). Per-kind accessors (:func:`llm`,
:func:`image`, :func:`music`, :func:`sfx`, :func:`mesh`) return the row or
``None``; :func:`price_for` is the LOUD form every estimator/backend goes
through: an unpriced model appends a warning naming the model AND the table
it is missing from, and the caller prices it at $0 flagged ``estimated`` —
never a silent $0 (§3.0-B).

The two accuracy constants (``MEASURED`` / ``ESTIMATED``) are plain strings
compared by value (P0 paper P.8.8): ``measured`` = every component came from
a provider-reported quantity (token counts × this table, PixelLab
``usage.usd``, Retro ``balance_cost``); ``estimated`` = priced from this
table without a reported quantity (fal, Lyria, ElevenLabs — P.9 J3).

Deliberately absent, by row ownership: the ledger's per-row accuracy flag
(A6), the Meshy backend itself (W2.2 — only its price rows live here), the
wizard's price-range copy (P0-10 reads these rows; it does not add any).
"""

from __future__ import annotations

import os
from typing import Any

# ---------------------------------------------------------------------------
# Accuracy flags (P.8.8 — plain strings, compared by value)
# ---------------------------------------------------------------------------

MEASURED = "measured"
ESTIMATED = "estimated"

#: The table was researched/approved on this date; rows re-verified later
#: carry their own ``verified``.
VERIFIED = "2026-09-01"

_ANTHROPIC_SRC = "https://platform.claude.com/docs/en/models/overview"
_OPENAI_SRC = "https://developers.openai.com/api/docs/pricing"
_FAL_SRC = "https://fal.ai/models/"
_GEMINI_SRC = "https://ai.google.dev/gemini-api/docs/pricing"
_ELEVEN_SRC = "https://elevenlabs.io/pricing"
_MESHY_SRC = "https://docs.meshy.ai/en/api/pricing"


def _llm_row(
    provider: str,
    input_per_1m: float,
    output_per_1m: float,
    source: str,
    *,
    cache_read_per_1m: float | None = None,
    note: str = "",
    verified: str = VERIFIED,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "provider": provider,
        "input_per_1m": float(input_per_1m),
        "output_per_1m": float(output_per_1m),
        "source": source,
        "verified": verified,
        "accuracy": ESTIMATED,
    }
    if cache_read_per_1m is not None:
        row["cache_read_per_1m"] = float(cache_read_per_1m)
    if note:
        row["note"] = note
    return row


def _anthropic(input_per_1m: float, output_per_1m: float, note: str = "") -> dict[str, Any]:
    # Anthropic cache reads bill at 10% of the input rate (the table notes it
    # on the fable-5 row; it is the platform-wide rule on the models page).
    return _llm_row(
        "anthropic", input_per_1m, output_per_1m, _ANTHROPIC_SRC,
        cache_read_per_1m=input_per_1m * 0.10, note=note,
    )


# ---------------------------------------------------------------------------
# §1 LLM per-1M tokens (chat + VLM)
# ---------------------------------------------------------------------------

LLM: dict[str, dict[str, Any]] = {
    # Anthropic
    "claude-fable-5": _anthropic(10.00, 50.00, "Batch 50% off"),
    "claude-opus-5": _anthropic(5.00, 25.00, "Opus 4.6–4.8 remain at the same $5/$25"),
    "claude-opus-4-8": _anthropic(5.00, 25.00),
    "claude-opus-4-7": _anthropic(5.00, 25.00, "legacy runs"),
    "claude-sonnet-5": _anthropic(2.00, 10.00, "cheaper than sonnet-4-6 ($3/$15)"),
    "claude-sonnet-4-6": _anthropic(3.00, 15.00, "repo default / VLM judge"),
    "claude-haiku-4-5": _anthropic(1.00, 5.00, "200K context"),
    "claude-haiku-4-5-20251001": _anthropic(1.00, 5.00, "the dated haiku id (models.json cheap tier)"),
    # OpenAI
    "gpt-5.1": _llm_row(
        "openai", 1.25, 10.00, _OPENAI_SRC, cache_read_per_1m=0.125,
        note="named in Phase 1 copy; no longer flagship but still listed",
    ),
    "gpt-5.4-mini": _llm_row("openai", 0.75, 4.50, _OPENAI_SRC, note="current mini"),
    "gpt-5.4-nano": _llm_row("openai", 0.20, 1.25, _OPENAI_SRC, note="current nano"),
    # Moonshot (Kimi) — current ids only; kimi-k2 is retired (table §1, action item 5)
    "kimi-k3": _llm_row(
        "kimi", 3.00, 15.00, "https://platform.kimi.ai/docs/pricing/chat-k3",
        cache_read_per_1m=0.30, note="1M context",
    ),
    "kimi-k2.6": _llm_row(
        "kimi", 0.95, 4.00, "https://platform.kimi.ai/docs/pricing/chat-k26",
        cache_read_per_1m=0.19, note="262K context; cache-hit input $0.16–0.19 (upper bound entered)",
    ),
    "kimi-k2.7-code": _llm_row(
        "kimi", 0.95, 4.00, "https://platform.kimi.ai/docs/pricing/chat-k27-code",
        cache_read_per_1m=0.19, note="262K context; cache-hit input $0.16–0.19 (upper bound entered)",
    ),
}


# ---------------------------------------------------------------------------
# §2 Image generation (per image)
# ---------------------------------------------------------------------------


def _image_row(
    provider: str,
    usd: float,
    source: str,
    *,
    usd_high: float | None = None,
    measured_by_provider: bool = False,
    by_resolution: dict[str, float] | None = None,
    note: str = "",
    verified: str = VERIFIED,
) -> dict[str, Any]:
    """``usd`` is the point estimate the estimator prices with (the LOW end
    of a published range); ``usd_high`` the range's top (== ``usd`` for a
    flat price). ``measured_by_provider`` rows report the real figure per
    call (PixelLab ``usage.usd``, Retro ``balance_cost``) — the range is the
    ESTIMATE, the actual is ``measured``."""
    row: dict[str, Any] = {
        "provider": provider,
        "usd": float(usd),
        "usd_high": float(usd if usd_high is None else usd_high),
        "per": "image",
        "measured_by_provider": bool(measured_by_provider),
        "source": source,
        "verified": verified,
        "accuracy": ESTIMATED,
    }
    if by_resolution:
        row["by_resolution"] = dict(by_resolution)
    if note:
        row["note"] = note
    return row


_NB2_SCHEDULE = {"0.5K": 0.06, "1K": 0.08, "2K": 0.12, "4K": 0.16}
_PIXELLAB_NOTE = (
    "dollar-denominated GPU-time estimates; the response's usage.usd is the measured cost. "
    "$0.008 (64² Pixflux) → $0.185 (Pro char/anim); $0.0169 512² Pixen"
)
_RETRO_NOTE = (
    "prepaid balance; the response's balance_cost is the measured cost; the free check_cost "
    "dry-run returns the exact price pre-spend. RD Fast ~$0.015 / RD Plus ~$0.03 / RD Pro $0.18"
)

IMAGE: dict[str, dict[str, Any]] = {
    # fal.ai
    "fal-ai/nano-banana": _image_row(
        "fal", 0.039, _FAL_SRC + "fal-ai/nano-banana", note="text-to-image; 25 runs per $1 (repo default)",
    ),
    "fal-ai/nano-banana/edit": _image_row(
        "fal", 0.039, _FAL_SRC + "fal-ai/nano-banana/edit", note="same rate on the edit endpoint",
    ),
    "fal-ai/nano-banana-2": _image_row(
        "fal", 0.08, _FAL_SRC + "fal-ai/nano-banana-2", usd_high=0.16, by_resolution=_NB2_SCHEDULE,
        note="base 1K; +$0.015 web search, +$0.002 high thinking",
    ),
    "fal-ai/nano-banana-2/edit": _image_row(
        "fal", 0.08, _FAL_SRC + "fal-ai/nano-banana-2", usd_high=0.16, by_resolution=_NB2_SCHEDULE,
        note="edit variant, same schedule",
    ),
    "fal-ai/nano-banana-pro": _image_row(
        "fal", 0.15, _FAL_SRC + "fal-ai/nano-banana-pro", usd_high=0.30,
        by_resolution={"1K": 0.15, "2K": 0.15, "4K": 0.30},
    ),
    # Google direct (same models as fal's nano-banana line)
    "gemini-2.5-flash-image": _image_row(
        "google", 0.039, _GEMINI_SRC, note="std; $0.0195 batch — same model as fal nano-banana",
    ),
    "gemini-3.1-flash-image": _image_row(
        "google", 0.067, _GEMINI_SRC, usd_high=0.101,
        by_resolution={"0.5K": 0.045, "1K": 0.067, "2K": 0.101}, note="nb2; batch half",
    ),
    # PixelLab — provider-reported usage.usd; the published range is the estimate
    "pixellab": _image_row(
        "pixellab", 0.008, "https://www.pixellab.ai/pixellab-api", usd_high=0.185,
        measured_by_provider=True, note=_PIXELLAB_NOTE,
    ),
    "pixellab/pixflux": _image_row(
        "pixellab", 0.008, "https://api.pixellab.ai/v2/docs", usd_high=0.185,
        measured_by_provider=True, note=_PIXELLAB_NOTE,
    ),
    "pixellab/bitforge": _image_row(
        "pixellab", 0.008, "https://api.pixellab.ai/v2/docs", usd_high=0.185,
        measured_by_provider=True, note=_PIXELLAB_NOTE,
    ),
    # Retro Diffusion — provider-reported balance_cost; the published range is the estimate
    "retro-diffusion": _image_row(
        "retro-diffusion", 0.015, "https://www.retrodiffusion.ai/", usd_high=0.18,
        measured_by_provider=True, note=_RETRO_NOTE,
    ),
    "retro-diffusion/rd_fast": _image_row(
        "retro-diffusion", 0.015, "https://www.retrodiffusion.ai/", measured_by_provider=True, note=_RETRO_NOTE,
    ),
    "retro-diffusion/rd_plus": _image_row(
        "retro-diffusion", 0.03, "https://www.retrodiffusion.ai/", measured_by_provider=True, note=_RETRO_NOTE,
    ),
    "retro-diffusion/rd_pro": _image_row(
        "retro-diffusion", 0.18, "https://www.retrodiffusion.ai/", measured_by_provider=True, note=_RETRO_NOTE,
    ),
    "retro-diffusion/animation": _image_row(
        "retro-diffusion", 0.07, "https://www.retrodiffusion.ai/", measured_by_provider=True,
        note="from $0.07 / clip; scales with size/length",
    ),
}


# ---------------------------------------------------------------------------
# §3 Audio — music (per track) and SFX (per effect / per second)
# ---------------------------------------------------------------------------

MUSIC: dict[str, dict[str, Any]] = {
    "lyria-3-pro-preview": {
        "provider": "lyria", "usd": 0.08, "per": "track", "source": _GEMINI_SRC,
        "verified": VERIFIED, "accuracy": ESTIMATED, "note": "paid tier only; confirmed on Vertex page too",
    },
    "lyria-3-clip-preview": {
        "provider": "lyria", "usd": 0.04, "per": "track", "source": _GEMINI_SRC,
        "verified": VERIFIED, "accuracy": ESTIMATED, "note": "paid tier only",
    },
    "lyria-2": {
        "provider": "lyria", "usd": 0.06, "per": "track",
        "source": "https://cloud.google.com/vertex-ai/generative-ai/pricing",
        "verified": VERIFIED, "accuracy": ESTIMATED, "note": "Vertex; ~30s clips",
    },
}

SFX: dict[str, dict[str, Any]] = {
    "elevenlabs": {
        "provider": "elevenlabs", "usd": 0.04, "usd_low": 0.033, "per": "effect", "source": _ELEVEN_SRC,
        "verified": VERIFIED, "accuracy": ESTIMATED,
        "note": "auto duration: 200 credits/gen ≈ $0.033–0.040 by tier (Starter $0.040 entered)",
    },
    "elevenlabs/per-second": {
        "provider": "elevenlabs", "usd": 0.008, "usd_low": 0.0066, "per": "second",
        "source": "https://elevenlabs.io/docs/capabilities/sound-effects",
        "verified": VERIFIED, "accuracy": ESTIMATED,
        "note": "explicit duration_seconds: 40 credits/s ≈ $0.0066–0.008/s (upper bound entered)",
    },
}


# ---------------------------------------------------------------------------
# §4 Meshy (3D) — credits per op × a configurable $/credit
# ---------------------------------------------------------------------------

#: The Pro-tier proxy ($20/mo ÷ 1,000 credits). The API wallet's own $/credit
#: is UNVERIFIED (login-only) — the "dashboard-confirm" knob is the
#: ``MESHY_USD_PER_CREDIT`` env var; nothing else here needs re-entry.
MESHY_USD_PER_CREDIT_DEFAULT = 0.02
MESHY_USD_PER_CREDIT_ENV = "MESHY_USD_PER_CREDIT"

MESH: dict[str, dict[str, Any]] = {
    "image-to-3d/mesh": {"credits": 20, "note": "single or multi-image, mesh-only"},
    "image-to-3d/textured": {"credits": 30, "note": "textured"},
    "image-to-3d/textured-8k": {"credits": 35, "note": "8K textured"},
    "texture": {"credits": 10, "note": "texture / retexture, 2K/4K"},
    "texture-8k": {"credits": 15, "note": "texture / retexture, 8K"},
    "rig": {"credits": 5, "note": "auto-rigging (API; webapp free)"},
    "animation": {"credits": 3, "note": "text-to-motion clip (API; webapp free)"},
}
for _row in MESH.values():
    _row.update({
        "provider": "meshy", "per": "op", "source": _MESHY_SRC,
        "verified": VERIFIED, "accuracy": ESTIMATED,
    })


def meshy_usd_per_credit() -> float:
    """The $/credit Meshy rows are priced at: ``MESHY_USD_PER_CREDIT`` from
    the environment when set (the dashboard-confirm knob), else the Pro proxy."""
    raw = os.environ.get(MESHY_USD_PER_CREDIT_ENV)
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    return MESHY_USD_PER_CREDIT_DEFAULT


# ---------------------------------------------------------------------------
# Backend id → the row it prices by when no model is named
# ---------------------------------------------------------------------------

#: What a bare backend id (what cradle's selectors and ``--*-backend`` flags
#: carry) prices as. Absent = unpaid (fake / none / local / "") — the
#: estimator's mask reads this map, never a hardcoded set.
#:
#: Each entry is the SKU the backend class actually constructs when nobody
#: passes ``--*-model``: fal's ``DEFAULT_MODEL``, Retro's
#: ``DEFAULT_PROMPT_STYLE`` (``rd_pro__platformer`` → the ``rd_pro`` row, NOT
#: the generic ``retro-diffusion`` range row, which is the RD-Fast floor),
#: PixelLab's ``DEFAULT_MODEL`` (``pixflux``). ``tests/test_pricing.py``
#: pins the two together so the estimator and the backend cannot drift.
BACKEND_DEFAULT_MODEL: dict[str, dict[str, str]] = {
    "llm": {"anthropic": "claude-sonnet-4-6", "openai": "gpt-5.1", "kimi": "kimi-k2.6"},
    "vlm": {"anthropic": "claude-sonnet-4-6"},
    "image": {
        "fal": "fal-ai/nano-banana",
        "retro": "retro-diffusion/rd_pro",
        "retro-diffusion": "retro-diffusion/rd_pro",
        "pixellab": "pixellab/pixflux",
    },
    "music": {"lyria": "lyria-3-pro-preview"},
    "sfx": {"elevenlabs": "elevenlabs"},
    "mesh": {"meshy": "image-to-3d/textured"},
}

TABLES: dict[str, dict[str, dict[str, Any]]] = {
    "llm": LLM,
    "vlm": LLM,
    "image": IMAGE,
    "music": MUSIC,
    "sfx": SFX,
    "mesh": MESH,
}


# ---------------------------------------------------------------------------
# Accessors — one per kind; each returns the row (a copy) or None
# ---------------------------------------------------------------------------


def llm(model: str) -> dict[str, Any] | None:
    """``{input_per_1m, output_per_1m, cache_read_per_1m?, source, verified, …}``."""
    row = LLM.get(model)
    return dict(row) if row is not None else None


def _family_lookup(table: dict[str, dict[str, Any]], model: str) -> dict[str, Any] | None:
    """Exact key first; then the model's family — ``retro-diffusion/rd_pro__platformer``
    prices as ``retro-diffusion/rd_pro``, ``pixellab/pixflux-64`` as
    ``pixellab/pixflux`` — by trimming the last ``__`` / ``-`` segment, then
    the ``provider/`` prefix alone."""
    row = table.get(model)
    if row is not None:
        return dict(row)
    if "/" in model:
        head, _, tail = model.rpartition("/")
        for sep in ("__", "-"):
            if sep in tail:
                candidate = f"{head}/{tail.split(sep, 1)[0]}"
                if candidate in table:
                    return dict(table[candidate])
        if head in table:
            return dict(table[head])
    return None


def image(model: str) -> dict[str, Any] | None:
    """``{usd, usd_high, measured_by_provider, by_resolution?, source, verified, …}``."""
    return _family_lookup(IMAGE, model)


def music(model: str) -> dict[str, Any] | None:
    """``{usd, per: "track", source, verified, …}``."""
    row = MUSIC.get(model)
    return dict(row) if row is not None else None


def sfx(model: str) -> dict[str, Any] | None:
    """``{usd, usd_low, per: "effect" | "second", source, verified, …}``."""
    row = SFX.get(model)
    return dict(row) if row is not None else None


def mesh(op: str) -> dict[str, Any] | None:
    """``{credits, usd, usd_per_credit, source, verified, …}`` — ``usd`` is
    ``credits × meshy_usd_per_credit()`` evaluated at call time (env override)."""
    row = MESH.get(op)
    if row is None:
        return None
    rate = meshy_usd_per_credit()
    out = dict(row)
    out["usd_per_credit"] = rate
    out["usd"] = round(row["credits"] * rate, 6)
    return out


_ACCESSORS = {"llm": llm, "vlm": llm, "image": image, "music": music, "sfx": sfx, "mesh": mesh}
_TABLE_NAMES = {"llm": "LLM", "vlm": "LLM", "image": "IMAGE", "music": "MUSIC", "sfx": "SFX", "mesh": "MESH"}


def zero_row(kind: str) -> dict[str, Any]:
    """The $0 row an unpriced model prices at — flagged ``estimated`` so the
    figure never reads as a real zero."""
    row: dict[str, Any] = {"accuracy": ESTIMATED, "unpriced": True, "source": None, "verified": None}
    if kind in ("llm", "vlm"):
        row.update({"input_per_1m": 0.0, "output_per_1m": 0.0})
    elif kind == "mesh":
        row.update({"credits": 0, "usd": 0.0, "usd_per_credit": meshy_usd_per_credit()})
    else:
        row.update({"usd": 0.0, "usd_high": 0.0})
    return row


def price_for(kind: str, model: str, warnings: list[str]) -> dict[str, Any] | None:
    """The row for ``model`` in the ``kind`` table, or ``None`` after appending
    the LOUD warning — naming the model and the table it is missing from —
    so the caller prices it at $0 flagged ``estimated`` (:func:`zero_row`).
    ``kind`` is open vocabulary; an unknown kind is itself an unpriced row."""
    accessor = _ACCESSORS.get(kind)
    row = accessor(model) if accessor is not None else None
    if row is None:
        table = _TABLE_NAMES.get(kind, kind.upper())
        warnings.append(
            f"no {kind} price row for model {model!r} in canon.pricing.{table} — "
            f"priced at $0 and flagged {ESTIMATED!r}; add the row to canon.pricing"
        )
        return None
    return row


def default_model(kind: str, backend: str | None) -> str | None:
    """The model id a bare backend id prices by for ``kind`` (``None`` =
    the backend is unpaid: fake / none / local / "")."""
    return BACKEND_DEFAULT_MODEL.get(kind, {}).get((backend or "").strip().lower())


def is_paid(kind: str, backend: str | None) -> bool:
    """Whether a category's chosen backend bills at all — i.e. it has a
    default price row. fake / none / local / "" price at $0 everywhere."""
    return default_model(kind, backend) is not None


def per_token(row: dict[str, Any]) -> dict[str, float]:
    """An LLM row's per-1M figures as the per-token ``{"input", "output"}``
    shape the backends' ``PRICING`` views and the estimator multiply by."""
    return {
        "input": float(row.get("input_per_1m", 0.0)) / 1_000_000,
        "output": float(row.get("output_per_1m", 0.0)) / 1_000_000,
    }


def llm_per_token_view(provider: str | None = None) -> dict[str, dict[str, float]]:
    """``{model: {"input": $/token, "output": $/token}}`` for every LLM row
    (optionally one provider's) — what ``canon.backends.anthropic.PRICING``
    re-exports."""
    return {
        model: per_token(row)
        for model, row in LLM.items()
        if provider is None or row.get("provider") == provider
    }


def all_rows() -> list[tuple[str, str, dict[str, Any]]]:
    """Every ``(kind, model, row)`` across the tables — the audit surface
    (every row carries ``source`` + ``verified``)."""
    out: list[tuple[str, str, dict[str, Any]]] = []
    for kind, table in (("llm", LLM), ("image", IMAGE), ("music", MUSIC), ("sfx", SFX), ("mesh", MESH)):
        out.extend((kind, model, dict(row)) for model, row in table.items())
    return out
