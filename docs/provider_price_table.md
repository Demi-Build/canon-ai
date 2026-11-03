# Provider price table — reference for the constants module

**Status: ✅ APPROVED by the user 2026-09-01.** This is the seeded reference for the single price/constants module born at row P0-7 (master §3.0-C); the module is the only place code reads prices from, and the build session wires these numbers in (see the action items at the bottom). Researched 2026-09-01 via official provider pages (five parallel research agents + a repo scan); Anthropic rows cross-checked against the current API model table. The three cells marked UNVERIFIED (Meshy API-wallet $/credit, Meshy Premium/Ultra credit counts, retired kimi-k2) still need a dashboard/browser check before those specific rows are wired — everything else is confirmed.

Provider prices drift; re-verify before wiring numbers into billing logic. Every row links its source.

---

## 0. What the code assumes today (repo anchor)

| Where | Constant | Value today | vs. researched | ✔ |
|---|---|---|---|---|
| `src/canon/backends/anthropic.py` PRICING | claude-sonnet-4-6 | $3.00 / $15.00 per 1M | ✅ matches official | |
| 〃 | claude-opus-4-8 (models.json "top") | $5.00 / $25.00 | ✅ matches official | |
| 〃 | claude-opus-4-7 (legacy runs) | $5.00 / $25.00 | ✅ matches official | |
| 〃 | claude-haiku-4-5-20251001 ("cheap") | $1.00 / $5.00 | ✅ matches official | |
| `src/canon/backends/music_lyria.py` PRICING | lyria-3-pro-preview / lyria-3-clip-preview | $0.08 / $0.04 per track | ✅ matches Gemini API page | |
| `src/canon/backends/sfx_elevenlabs.py` | COST_PER_EFFECT | $0.04 flat | ✅ inside the $0.033–0.040 tier band | |
| `src/canon/backends/image_fal.py` | last_cost | **always $0.00 (untracked, TODO)** | ❌ real cost is $0.039/image — the gap the `measured\|estimated` flag + a `_pricing_for` entry closes | |
| `examples/platformer_pack/cost_model.json` | image_usd_per_call | $0.04 | ✅ ≈ fal $0.039 (Retro Diffusion runs are cheaper, ~$0.015–0.03) | |
| 〃 | music_usd_per_track | **$0.10** | ⚠️ **mismatch** — Lyria backend charges $0.08 (pro) / $0.04 (clip) | |
| 〃 | sfx_usd_per_event | **$0.05** | ⚠️ **mismatch** — backend charges $0.04 | |
| `examples/platformer_pack/estimate.py:269-271` | duplicate `.get()` defaults ($0.04/$0.10/$0.05) | duplicates cost_model.json | ⚠️ two places to change — dissolves into the P0-7 module | |

## 1. LLM per-1M tokens (chat + VLM)

| Provider | Model | Input / Output per 1M | Notes | Source | ✔ |
|---|---|---|---|---|---|
| Anthropic | claude-fable-5 | $10.00 / $50.00 | Batch 50% off; cache reads 10% of input | [models overview](https://platform.claude.com/docs/en/models/overview) | |
| Anthropic | claude-opus-5 | $5.00 / $25.00 | Opus 4.6–4.8 remain at the same $5/$25 | [models overview](https://platform.claude.com/docs/en/models/overview) | |
| Anthropic | claude-sonnet-5 | $2.00 / $10.00 | note: **cheaper than sonnet-4-6** ($3/$15) — a models.json bump saves money | [models overview](https://platform.claude.com/docs/en/models/overview) | |
| Anthropic | claude-sonnet-4-6 (repo default/mid/VLM) | $3.00 / $15.00 | current repo default | [models overview](https://platform.claude.com/docs/en/models/overview) | |
| Anthropic | claude-haiku-4-5 ("cheap" tier) | $1.00 / $5.00 | 200K context | [models overview](https://platform.claude.com/docs/en/models/overview) | |
| OpenAI | gpt-5.1 (named in Phase 1 copy) | $1.25 / $10.00 | cached input $0.125; no longer flagship but still listed | [openai pricing](https://developers.openai.com/api/docs/pricing) | |
| OpenAI | gpt-5.4-mini / gpt-5.4-nano | $0.75 / $4.50 · $0.20 / $1.25 | current mini/nano; legacy gpt-5-mini $0.25/$2.00 | [openai pricing](https://developers.openai.com/api/docs/pricing) | |
| Moonshot (Kimi) | kimi-k3 | $3.00 / $15.00 | cache-hit input $0.30; 1M context | [kimi k3 pricing](https://platform.kimi.ai/docs/pricing/chat-k3) | |
| Moonshot (Kimi) | kimi-k2.6 / kimi-k2.7-code | $0.95 / $4.00 | cache-hit input $0.16–0.19; 262K context | [k2.6](https://platform.kimi.ai/docs/pricing/chat-k26) · [k2.7-code](https://platform.kimi.ai/docs/pricing/chat-k27-code) | |
| Moonshot (Kimi) | kimi-k2 (original) | **UNVERIFIED — retired ~May 2026** | Phase 1's "kimi" backend should target k2.6/k3 ids | [pricing index](https://platform.kimi.ai/docs/pricing/chat) | |

## 2. Image generation

| Provider | Item | Price | Notes | Source | ✔ |
|---|---|---|---|---|---|
| fal.ai | fal-ai/nano-banana (repo default) | **$0.039 / image** | text-to-image; 25 runs per $1 | [model page](https://fal.ai/models/fal-ai/nano-banana) | |
| fal.ai | fal-ai/nano-banana/edit (repo default edit) | **$0.039 / image** | same rate on the edit endpoint | [edit page](https://fal.ai/models/fal-ai/nano-banana/edit) | |
| fal.ai | fal-ai/nano-banana-2 (future models.json bump) | $0.08 base (1K); $0.06 @0.5K · $0.12 @2K · $0.16 @4K | +$0.015 web search, +$0.002 high thinking; edit variant same schedule | [nb2 page](https://fal.ai/models/fal-ai/nano-banana-2) | |
| fal.ai | fal-ai/nano-banana-pro | $0.15 (1K/2K); $0.30 (4K) | | [pro page](https://fal.ai/models/fal-ai/nano-banana-pro) | |
| Google direct | gemini-2.5-flash-image | $0.039 std; $0.0195 batch | same model as fal nano-banana, batch is half price | [gemini pricing](https://ai.google.dev/gemini-api/docs/pricing) | |
| Google direct | gemini-3.1-flash-image (nb2) | $0.067 @1K ($0.045 @0.5K, $0.101 @2K); batch half | cheaper than fal's $0.08 at 1K | [gemini pricing](https://ai.google.dev/gemini-api/docs/pricing) | |
| PixelLab | API per-generation | $0.008 (64² Pixflux) → $0.185 (Pro char/anim); $0.0169 512² Pixen | dollar-denominated; provider-reported `usage.usd` per response (code already reads it); prices are GPU-time estimates | [API page](https://www.pixellab.ai/pixellab-api) · [full schedule](https://api.pixellab.ai/v2/docs) | |
| PixelLab | subscriptions (all tiers incl. free get API) | $12/mo 2,000 img · $24/mo 5,000 · $50/mo 10,000 | ≈ $0.005–0.006/image at cap | [pixellab.ai](https://www.pixellab.ai/) | |
| Retro Diffusion | RD Fast / RD Plus / RD Pro | ~$0.015 / ~$0.03 / $0.18 per image | prepaid balance; provider-reported `balance_cost` (code already reads it); **free `check_cost` dry-run returns the exact price pre-spend** — use it for estimates | [retrodiffusion.ai](https://www.retrodiffusion.ai/) | |
| Retro Diffusion | Animations | from $0.07 / clip | scales with size/length | [retrodiffusion.ai](https://www.retrodiffusion.ai/) | |

## 3. Audio

| Provider | Item | Price | Notes | Source | ✔ |
|---|---|---|---|---|---|
| ElevenLabs | SFX, auto duration | 200 credits/gen ≈ **$0.033–0.040** by tier | Starter $0.040 · Creator $0.0364 · Pro+ $0.033; free tier = 50 SFX/mo | [pricing](https://elevenlabs.io/pricing) | |
| ElevenLabs | SFX with explicit `duration_seconds` | 40 credits/s ≈ $0.0066–0.008/s | a 3s SFX ≈ $0.02 — cheaper than auto for short sounds | [SFX docs](https://elevenlabs.io/docs/capabilities/sound-effects) | |
| ElevenLabs | developer rate card | $0.12 / minute of audio | consistent with credit math | [API pricing](https://elevenlabs.io/pricing/api) | |
| ElevenLabs | subscription tiers | Free $0 (10k cr) · Starter $6 (30k) · Creator $22 (121k) · Pro $99 (600k) | credits shared with TTS etc.; Free lacks commercial license | [pricing](https://elevenlabs.io/pricing) | |
| Google | Lyria 3 Clip Preview (repo `lyria-3-clip-preview`) | **$0.04 / song** | paid tier only | [gemini pricing](https://ai.google.dev/gemini-api/docs/pricing) | |
| Google | Lyria 3 Pro Preview (repo `lyria-3-pro-preview`) | **$0.08 / song** | confirmed on Vertex page too | [gemini pricing](https://ai.google.dev/gemini-api/docs/pricing) | |
| Google | Lyria 2 (Vertex) | $0.06 / generation | ~30s clips | [vertex pricing](https://cloud.google.com/vertex-ai/generative-ai/pricing) | |
| Google | Lyria RealTime | UNVERIFIED — experimental, no published price | don't meter against a number | [docs](https://ai.google.dev/gemini-api/docs/realtime-music-generation) | |

## 4. Meshy (3D)

| Item | Price | Notes | Source | ✔ |
|---|---|---|---|---|
| Free tier | $0 · 100 credits/mo | ⚠️ **Licensing correction:** free-tier outputs are **CC BY 4.0 — commercial use IS allowed with attribution.** Accurate key-screen copy: "paid tier required for **full ownership / commercial use without attribution**," not "required for commercial use" | [pricing FAQ](https://www.meshy.ai/pricing) | |
| Pro tier (cheapest full-ownership) | $20/mo · 1,000 credits → **$0.02/credit** | paid tiers grant full private ownership, no attribution | [pricing](https://www.meshy.ai/pricing) | |
| Premium / Ultra / Studio | $40 / $100 / $70+$10 per seat | credit counts render client-side — confirm in browser | [pricing](https://www.meshy.ai/pricing) | |
| Image-to-3D (single or multi-image, API) | 20 cr mesh-only / 30 textured / 35 8K → **≈ $0.40 / $0.60 / $0.70** at Pro rate | webapp table differs slightly (25 cr) — meter API spend by the API table | [API pricing](https://docs.meshy.ai/en/api/pricing) | |
| Texture / retexture | 10 cr (2K/4K) / 15 cr (8K) → ≈ $0.20 / $0.30 | | [API pricing](https://docs.meshy.ai/en/api/pricing) | |
| Auto-rigging | 5 cr API → ≈ **$0.10** (webapp: free) | | [API pricing](https://docs.meshy.ai/en/api/pricing) | |
| Animation (text-to-motion) | 3 cr → ≈ $0.06/clip (webapp: free) | | [API pricing](https://docs.meshy.ai/en/api/pricing) | |
| API credit-pack $ rate | **UNVERIFIED — login-only** | API wallet is a separate prepaid balance; $/credit not published. All $ figures above use the Pro proxy ($0.02/cr) — confirm in your dashboard before wiring | [API pricing](https://docs.meshy.ai/en/api/pricing) | |

---

## Build-session action items (from the mismatches above)

1. **Music**: `cost_model.json music_usd_per_track` $0.10 → $0.08 (or keep $0.10 as a deliberate buffer — your call; the estimator's ×4 worst-case convention already buffers).
2. **SFX**: `sfx_usd_per_event` $0.05 → $0.04 (same buffer question).
3. **fal gap**: add `fal-ai/nano-banana*` → $0.039 to the `_pricing_for`-style table in the P0-7 module so fal rows stop reporting $0 (pairs with the `measured|estimated` flag).
4. **Dedupe**: `estimate.py`'s hardcoded `.get()` defaults dissolve into the P0-7 module — one source, per master §3.0-C.
5. **Kimi ids**: Phase 1's kimi backend should target current ids (kimi-k2.6 / kimi-k3); original k2 appears retired.
6. **Meshy copy**: use the corrected licensing line on the key screen (see §4 row 1); Meshy rows enter the module in credits × a configurable $/credit (default $0.02, confirm from dashboard).
7. **Sonnet bump worth knowing**: claude-sonnet-5 ($2/$10) is cheaper than the repo's default claude-sonnet-4-6 ($3/$15) — a models.json id bump is a price *cut* if quality holds.
