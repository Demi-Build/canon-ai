"""``canon.providers`` — the ONE provider-row table (master §3 row P0-12, §6 S6).

W3.4 named six fixed provider rows for cradle's Settings screen. Master §6 S6
superseded that: **provider rows are DATA** (doctrine 8's M0-readiness rule —
"backend/provider/template ids are data, never a hardcoded union"). This module
is that data, and adding a provider is adding a row here — nothing downstream
changes.

Why it lives in canon rather than in cradle: canon already owns the id→env-var
knowledge every consumer needs. The backends read the vars
(``image_fal.FAL_KEY``, ``image_pixellab.PIXELLAB_SECRET``,
``chat_openai.OPENAI_KEY_ENV`` …), ``canon.pricing`` owns the provider ids and
their sources, and ``canon.agent.providers.key_envs()`` needed a chat-shaped
slice of the same fact. Keeping a second list in cradle's TypeScript would be
exactly the "second hardcoded label list" the master exists to prevent, so
cradle renders ``canon providers`` output and holds no list at all.

What one row carries:

``id``
    The provider id. It is the same id ``canon.pricing`` rows carry in
    ``provider`` and (where a backend exists) the ``BackendRegistry`` key.
``label`` / ``docs``
    What the key screen shows and where a human gets a key.
``env_var``
    The **canonical** variable — canon's own name. ``aliases`` lists other
    names the backend also accepts (``PIXELLAB_API_KEY`` is the PixelLab
    dashboard's name for ``PIXELLAB_SECRET``; ``image_pixellab`` reads both,
    and the P0-12 row makes cradle agree).
``unlocks``
    One sentence naming what turning this key on buys, for the key screen.
``backends``
    ``{kind: [backend id, …]}`` — which ``--*-backend`` selections this key
    covers. This is the map cradle's missing-key precheck reads instead of its
    own ``BACKEND_KEYS`` literal.
``note``
    Anything a human must know before spending. The Meshy row carries the
    licensing correction from ``docs/provider_price_table.md`` §4: the free
    tier is **CC BY 4.0**, so commercial use IS allowed *with attribution* —
    the accurate line is "paid tier required for full ownership / commercial
    use without attribution", never the older "required for commercial use".
``test``
    The cheapest possible AUTHENTICATED ping, as data, or ``None``. A row with
    ``None`` renders disabled-with-a-reason (doctrine 4) rather than hidden.
    Never a generation: doctrine 3 keeps paid legs user-run, and this button is
    the one place a key is checked, so it must not bill. :func:`test_provider`
    runs it and is only ever reached from an explicit user click.

Deliberately absent, by row ownership: key STORAGE (cradle's keychain, row
P0-12's Rust half — canon never learns where a value came from, only that the
env var is set) and the Meshy backend itself (W2.2 — only its key row and its
price rows exist today).
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

#: Timeout for the key-test ping, in seconds. Small on purpose: the button is
#: a liveness check, not a job.
TEST_TIMEOUT = 12.0


def _test(
    url: str,
    header: str,
    *,
    prefix: str = "",
    headers: dict[str, str] | None = None,
    note: str = "",
) -> dict[str, Any]:
    """One key-test descriptor. ``header`` is the request header the key rides
    in — never a query parameter, because a key in a URL leaks into logs and
    proxies (that is why the Gemini row uses ``x-goog-api-key``, which the API
    accepts, rather than ``?key=``)."""
    return {
        "url": url,
        "header": header,
        "prefix": prefix,
        "headers": dict(headers or {}),
        "note": note or "a free, read-only list call: no tokens, no generation",
    }


#: The table. ORDER IS THE RENDER ORDER cradle shows.
PROVIDER_ROWS: list[dict[str, Any]] = [
    {
        "id": "anthropic",
        "label": "Anthropic",
        "env_var": "ANTHROPIC_API_KEY",
        "aliases": [],
        "unlocks": (
            "LLM generation (layouts, briefs, dialogue), VLM animation review, and the agent's Claude models."
        ),
        "backends": {"llm": ["anthropic"], "vlm": ["anthropic"], "chat": ["anthropic"]},
        "docs": "https://console.anthropic.com/settings/keys",
        "note": "",
        "test": _test(
            "https://api.anthropic.com/v1/models?limit=1",
            "x-api-key",
            headers={"anthropic-version": "2023-06-01"},
        ),
    },
    {
        "id": "fal",
        "label": "fal.ai",
        "env_var": "FAL_KEY",
        "aliases": [],
        "unlocks": "Image generation and animation frames on the nano-banana line.",
        "backends": {"image": ["fal"]},
        "docs": "https://fal.ai/dashboard/keys",
        "note": "",
        # fal publishes no free authenticated read endpoint; anything that
        # would answer runs a job, and this button never bills (doctrine 3).
        "test": None,
    },
    {
        "id": "lyria",
        "label": "Google (Lyria music)",
        "env_var": "GOOGLE_API_KEY",
        "aliases": [],
        "unlocks": "Music generation through Lyria on the Gemini API.",
        "backends": {"music": ["lyria"]},
        "docs": "https://aistudio.google.com/apikey",
        "note": "Lyria is paid-tier only — a free-tier Gemini key authenticates but cannot generate music.",
        "test": _test("https://generativelanguage.googleapis.com/v1beta/models", "x-goog-api-key"),
    },
    {
        "id": "elevenlabs",
        "label": "ElevenLabs",
        "env_var": "ELEVENLABS_API_KEY",
        "aliases": [],
        "unlocks": "Sound-effect generation.",
        "backends": {"sfx": ["elevenlabs"]},
        "docs": "https://elevenlabs.io/app/settings/api-keys",
        "note": (
            "The free tier does not carry a commercial licence; "
            "SFX credits are shared with the rest of the account."
        ),
        "test": _test("https://api.elevenlabs.io/v1/user", "xi-api-key", note="reads your own account row: free"),
    },
    {
        # THE P0-12 VAR FIX. Canon's own name is PIXELLAB_SECRET; the PixelLab
        # dashboard calls the same token PIXELLAB_API_KEY, and
        # `image_pixellab` reads SECRET first and falls back to API_KEY.
        # Cradle used to store only the alias, so a key added under canon's
        # name looked missing. One canonical var, one declared alias.
        "id": "pixellab",
        "label": "PixelLab",
        "env_var": "PIXELLAB_SECRET",
        "aliases": ["PIXELLAB_API_KEY"],
        "unlocks": "Pixel-art sprites and animation through PixelLab.",
        "backends": {"image": ["pixellab"]},
        "docs": "https://www.pixellab.ai/",
        "note": (
            "PIXELLAB_API_KEY is the dashboard's name for the same token; "
            "canon accepts either, PIXELLAB_SECRET first."
        ),
        "test": None,
    },
    {
        "id": "retro",
        "label": "Retro Diffusion",
        "env_var": "RD_API_KEY",
        "aliases": [],
        "unlocks": "Retro Diffusion pixel-art images and animation clips.",
        "backends": {"image": ["retro", "retro-diffusion"]},
        "docs": "https://www.retrodiffusion.ai/",
        "note": "",
        "test": None,
    },
    {
        # Master row P0-12: Meshy joins the September key screen even though
        # its backend is W2.2's. The key row and the price rows are the whole
        # of Meshy in Phase 0 — the row says so rather than pretending.
        "id": "meshy",
        "label": "Meshy (3D)",
        "env_var": "MESHY_API_KEY",
        "aliases": [],
        "unlocks": "Image-to-3D meshes, texturing, auto-rigging (the 3D lane arrives with W2.2).",
        "backends": {"mesh": ["meshy"]},
        "docs": "https://www.meshy.ai/api",
        "note": (
            "Free-tier outputs are CC BY 4.0 — commercial use IS allowed with attribution. "
            "A paid tier is required for full ownership / commercial use without attribution."
        ),
        "test": None,
    },
    {
        "id": "openai",
        "label": "OpenAI",
        "env_var": "OPENAI_API_KEY",
        "aliases": [],
        "unlocks": "The agent's GPT models.",
        "backends": {"chat": ["openai"]},
        "docs": "https://platform.openai.com/api-keys",
        "note": "",
        "test": _test("https://api.openai.com/v1/models", "Authorization", prefix="Bearer "),
    },
    {
        "id": "kimi",
        "label": "Moonshot (Kimi)",
        "env_var": "MOONSHOT_API_KEY",
        "aliases": [],
        "unlocks": "The agent's Kimi models.",
        "backends": {"chat": ["kimi"]},
        "docs": "https://platform.moonshot.ai/console/api-keys",
        "note": "",
        "test": _test("https://api.moonshot.ai/v1/models", "Authorization", prefix="Bearer "),
    },
]


def provider_rows() -> list[dict[str, Any]]:
    """The table, deep-copied so a caller cannot mutate the module state."""
    return json.loads(json.dumps(PROVIDER_ROWS))


def row(provider_id: str) -> dict[str, Any] | None:
    """One row by id, or ``None``."""
    for r in PROVIDER_ROWS:
        if r["id"] == provider_id:
            return json.loads(json.dumps(r))
    return None


def env_vars() -> list[str]:
    """Every variable the table names — canonical vars first, then aliases,
    de-duplicated in table order. This is what a key-status reader enumerates
    (names only; a value never leaves the machine that stores it)."""
    out: list[str] = [r["env_var"] for r in PROVIDER_ROWS]
    for r in PROVIDER_ROWS:
        for alias in r.get("aliases", []):
            if alias not in out:
                out.append(alias)
    return out


def key_var(provider_id: str) -> str | None:
    """The canonical env var for ``provider_id``."""
    r = row(provider_id)
    return r["env_var"] if r else None


def backend_key_vars() -> dict[str, dict[str, str]]:
    """``{kind: {backend id: env var}}`` — the missing-key precheck's map,
    derived from the rows' ``backends`` blocks. Cradle's ``BACKEND_KEYS``
    literal is replaced by this."""
    out: dict[str, dict[str, str]] = {}
    for r in PROVIDER_ROWS:
        for kind, ids in r.get("backends", {}).items():
            for backend_id in ids:
                out.setdefault(kind, {})[backend_id] = r["env_var"]
    return out


def resolve_key(provider_id: str, environ: dict[str, str] | None = None) -> str | None:
    """The key for ``provider_id`` from ``environ`` — canonical var first, then
    each alias, exactly as the backends resolve it. Returns the VALUE, so this
    is for in-process use only (the CLI never prints it)."""
    env = environ if environ is not None else os.environ
    r = row(provider_id)
    if r is None:
        return None
    for name in [r["env_var"], *r.get("aliases", [])]:
        value = env.get(name, "")
        if value:
            return value
    return None


def key_status(environ: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """``[{id, env_var, aliases, set, set_via}]`` — whether each row's key is
    visible in ``environ``, and under WHICH name. Names only, never a value and
    never a length (row P0-12's secrets discipline)."""
    env = environ if environ is not None else os.environ
    out: list[dict[str, Any]] = []
    for r in PROVIDER_ROWS:
        names = [r["env_var"], *r.get("aliases", [])]
        set_via = next((n for n in names if env.get(n)), None)
        out.append(
            {
                "id": r["id"],
                "env_var": r["env_var"],
                "aliases": list(r.get("aliases", [])),
                "set": set_via is not None,
                "set_via": set_via,
            }
        )
    return out


# ---------------------------------------------------------------------------
# The key test — user-initiated, never a generation
# ---------------------------------------------------------------------------

#: Signature of the HTTP leg, injected so tests never touch the network.
Fetch = Callable[[str, dict[str, str], float], tuple[int, str]]


def _urlopen(url: str, headers: dict[str, str], timeout: float) -> tuple[int, str]:
    request = urllib.request.Request(url, headers=headers, method="GET")  # noqa: S310 - https literal in the table
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:  # noqa: S310
            return int(resp.status), ""
    except urllib.error.HTTPError as e:
        # The STATUS is the answer; the body may quote the key back, so it is
        # never read and never surfaced.
        return int(e.code), ""
    except Exception as e:  # noqa: BLE001 — a network failure is a result here
        return 0, type(e).__name__


def _result(provider_id: str, status: int | None, *, ok: bool, reason: str) -> dict[str, Any]:
    return {"id": provider_id, "ran": True, "ok": ok, "status": status, "reason": reason}


def test_provider(
    provider_id: str,
    environ: dict[str, str] | None = None,
    fetch: Fetch | None = None,
) -> dict[str, Any]:
    """Run one row's cheapest authenticated ping and answer
    ``{id, ran, ok, status, reason}``.

    ``ran`` is False (with a reason) when the row declares no test or no key is
    set — doctrine 4: the caller renders disabled-with-a-reason, never a silent
    nothing. Nothing here bills: the descriptors are read-only list calls.

    The key is read from the environment and put in a request HEADER. It is
    never logged, never echoed into ``reason``, and never placed in the URL.
    """
    r = row(provider_id)
    if r is None:
        return {"id": provider_id, "ran": False, "ok": False, "status": None, "reason": "unknown provider id"}
    spec = r.get("test")
    if not spec:
        return {
            "id": provider_id,
            "ran": False,
            "ok": False,
            "status": None,
            "reason": (
                f"{r['label']} publishes no free authenticated endpoint to ping — testing the key would "
                "have to run a paid generation, which this button never does."
            ),
        }
    key = resolve_key(provider_id, environ)
    if not key:
        return {
            "id": provider_id,
            "ran": False,
            "ok": False,
            "status": None,
            "reason": f"{r['env_var']} is not set in this process",
        }
    headers = {**spec.get("headers", {}), spec["header"]: f"{spec.get('prefix', '')}{key}"}
    status, error = (fetch or _urlopen)(spec["url"], headers, TEST_TIMEOUT)
    if status == 0:
        return {"id": provider_id, "ran": True, "ok": False, "status": None, "reason": f"could not reach it ({error})"}
    if status in (401, 403):
        return _result(provider_id, status, ok=False, reason="the provider rejected the key")
    if 200 <= status < 300:
        return _result(provider_id, status, ok=True, reason="the provider accepted the key")
    return {
        "id": provider_id,
        "ran": True,
        "ok": False,
        "status": status,
        "reason": f"the provider answered {status} — the key reached it, but the account may not be usable",
    }


__all__ = [
    "PROVIDER_ROWS",
    "TEST_TIMEOUT",
    "backend_key_vars",
    "env_vars",
    "key_status",
    "key_var",
    "provider_rows",
    "resolve_key",
    "row",
    "test_provider",
]
