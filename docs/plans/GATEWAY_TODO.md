# VoiceStudio — Voice Gateway: follow-up plan (handoff)

Handoff for the next agent. The voice **gateway** (proxy local + cloud voice
providers behind one OpenAI-compatible API) and the **VoiceStudio** rebrand are
done, committed, and pushed on branch `claude/phase-d-console-shell`:

- `a5a59fc` feat: voice gateway (Mistral proxy) + Supertonic/Parakeet defaults + STT tab + console polish
- `4e3f8f4` chore: rebrand to VoiceStudio

This doc lists what remains. Tasks are ordered by value; each is independent
unless noted.

## Architecture you need to know (don't relearn)

- **Provider abstraction**: `backend/services/providers/` — `base.py`
  (`VoiceProvider`), `mistral.py` (`MistralProvider`), `__init__.py` (registry +
  credential CRUD + voice cache/seeding), `tts_backend.py` (`ProviderTTSBackend`
  adapter that makes a cloud provider look like a local `TTSBackend`).
- **Key storage**: `provider_credentials` table (`backend/database/models.py`),
  managed via `backend/routes/providers.py` (`GET /providers`,
  `PUT/DELETE /providers/{p}/key`, `POST /providers/{p}/validate`,
  `POST /providers/{p}/test`).
- **Proxy**: `backend/routes/openai_compat.py` — `split_provider_model()` routes
  `model="<provider>/<upstream_model>"` to the provider; otherwise local. Used in
  `create_speech` and `create_transcription`.
- **TTS-in-UI**: Mistral is a first-class preset engine. `get_tts_backend_for_engine`
  (`backend/backends/__init__.py`) returns `ProviderTTSBackend` for provider
  engines; `/engines` (`routes/engines.py`) lists configured providers; voices
  are seeded as `VoiceProfile` rows on key-set and on startup
  (`app.py:_seed_provider_voices`). Preset-voice helpers in
  `backend/services/profiles.py` (`_get_preset_voice_ids`,
  `_get_preset_voice_language`) and `routes/profiles.py` have provider fallbacks.
- **Discovery**: `GET /v1/audio/voices` (enriched: `language`, `provider`, `name`)
  and `GET /v1/audio/voices/search?q=&language=&provider=&limit=` (autocomplete).
  Built by `_build_voice_catalog()` in `openai_compat.py`.
- **MCP**: `backend/mcp_server/tools.py` — `voicebox.speak`/`list_profiles` already
  route Mistral via profiles; `list_profiles` gained `query`/`language` filters.

### Constraints — DO NOT break
- Keep technical identifiers: `voicebox-*` model ids, `/v1` paths,
  `voicebox.speak` (MCP tool name), `X-Voicebox-Client-Id` header, bundle
  identifier `sh.voicebox.app`, sidecar binary names `voicebox-server` /
  `voicebox-mcp`, env var `VOICEBOX_CORS_ORIGINS`. The rebrand was display-only.
- **Mistral free tier is rate-limited** (HTTP 429). Don't hammer the API in
  tests/probes — a burst of requests exhausts it for ~a minute. The provider's
  errors map 429 → 502 with a readable detail.
- Mistral's `GET /audio/voices` is **paginated** — always pass `?limit=500`
  (already done in `MistralProvider.list_voices`). The hosted API voice slugs
  (`en_paul_*`, `gb_*`, `fr_marie_*`) differ from the open-weight model slugs
  (`fr_female`, etc.) — the latter 404 on the API.

### Dev loop
- Run server: `python -m uvicorn backend.main:app --port 17493` (the desktop app
  connects to 17493; standalone default is 8000).
- A real Mistral key is already stored in the local DB (`provider_credentials`).

---

## Task 1 — Wire Mistral STT into the STT tab (medium)

**Goal**: the STT tab (`app/src/components/SttTab/SttTab.tsx`) can transcribe via
Mistral Voxtral, not just local Whisper/Parakeet. The backend proxy already works
(`POST /v1/audio/transcriptions` with `model="mistral/voxtral-mini-latest"`), and
the local `/transcribe` endpoint is what the tab currently calls.

**Approach**:
- Decide routing: either (a) add provider STT models to the STT model selector in
  `SttTab` and call `/v1/audio/transcriptions` with `mistral/...` when a provider
  model is chosen, or (b) teach the local `/transcribe` route + `transcribe_upload`
  (`backend/services/transcribe.py`) to recognize a `mistral/...` model and proxy
  via `MistralProvider.transcribe`. Option (b) keeps the tab's existing hook;
  prefer it for consistency with how TTS was integrated.
- Surface provider STT models in the selector: extend `SttModel` type
  (`app/src/lib/api/types.ts`) and the `STT_MODELS` list in `SttTab.tsx`. Only show
  Mistral STT when a key is configured (fetch `/providers` or `/v1/audio/voices`'s
  `transcription_models` — note: that list is currently local-only, may need
  enriching to include provider STT models).

**Acceptance**: in the STT tab, selecting "Mistral (Voxtral)" + uploading a clip
returns a transcript via Mistral; local models still work; no provider option
shown when no key.

**Gotchas**: `MistralProvider.transcribe` sends multipart `file`. The local
transcribe path already ffmpeg-transcodes webm→wav (`utils/audio.py`); if proxying
raw upload to Mistral, confirm Mistral accepts webm or transcode first.

## Task 2 — App bundle icons (small, needs image export)

**Goal**: the installed app icon (`.icns`/`.ico`/`.png` in the Tauri bundle) still
shows the old logo. The in-app SVG mark (`app/src/components/brand/VoiceStudioMark.tsx`)
and HTML favicon are done.

**Approach**: export the VoiceStudio waveform mark to PNG at required sizes, then
regenerate Tauri icons (`tauri icon path/to/icon.png` generates the full set).
Replace the icon set referenced in `tauri/src-tauri/tauri.conf.json`. Also update
`app/src/assets/voicebox-logo.png` usages still present (e.g. `Sidebar.tsx`,
`App.tsx` loading splash) — or swap those to `<VoiceStudioMark>`.

**Acceptance**: built app shows the VoiceStudio mark as its OS icon + dock/taskbar.

## Task 3 — Verify provider key removal end-to-end (small)

**Goal**: confirm "hide on key removal" works (was implemented, not tested live).

**Approach**: with a key set, `DELETE /providers/mistral/key` →
`remove_provider_profiles` should delete the seeded Mistral `VoiceProfile` rows +
clear the voice cache; `configured_providers()` should drop Mistral so it
disappears from `/engines` and `/v1/audio/voices`. Verify the API Keys page
reflects "not configured" and the engine vanishes from the generation selector.

**Acceptance**: after delete, no Mistral engine/voices anywhere in UI or catalog;
re-adding the key restores them. **Note**: you'll need to re-enter the real key
afterward (it's not recoverable once deleted).

## Task 4 — Build verification (medium, can't be done in dev server)

**Goal**: confirm two things that only manifest in the packaged build:
1. The librosa `.pyi` PyInstaller fix (`backend/voicebox-server.spec`) actually
   resolves the "non-existent stub" crash on the packaged `voicebox-server`.
2. The rebrand (`productName: VoiceStudio`, bundle paths) builds cleanly.

**Approach**: `bun run build:server` (PyInstaller) then exercise transcription on
the built binary; `tauri build` for the bundle. Verify the MCP paths in
`MCPPage.tsx` (`/Applications/VoiceStudio.app/...`) match the actual built bundle.

**Acceptance**: packaged transcription works (no librosa stub error); built app is
named VoiceStudio; MCP onboarding paths are correct.

## Task 5 — Open the PR (small)

**Goal**: PR `claude/phase-d-console-shell` → `main`. Summarize: gateway, Mistral
TTS-in-UI, defaults (Supertonic/Parakeet), STT tab, console polish, rebrand. Call
out that technical ids were preserved and that a real-key Mistral round-trip was
verified live.

---

## Backlog (nice-to-have, not required)

- **More providers**: OpenAI (near pass-through), ElevenLabs. Implement a
  `VoiceProvider` subclass + add to the `_PROVIDERS` registry tuple in
  `services/providers/__init__.py`; routes/UI/catalog are generic.
- **Streaming TTS** through the proxy (Mistral supports SSE; current impl is
  non-streaming `{audio_data: base64}`).
- **Mistral voice cloning** (`ref_audio`): expose creating a custom voice from a
  reference clip — the documented path for non-preset/non-English voices.
- **Full-width topbar**: currently the brand sits in `BuildNav`'s top-left
  (LM-Studio-accurate). A dedicated top bar in `ConsoleShell` could host the brand
  + global actions (Import/Create currently in `MainEditor`).
- **Inbound auth / remote access**: today no auth on inbound requests and CORS is
  localhost-first (`VOICEBOX_CORS_ORIGINS`). For exposing VoiceStudio as a shared
  gateway (`--host 0.0.0.0`), add API-key auth for clients + widen CORS.
