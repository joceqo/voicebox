# Voicebox → Product Vision: "Voice Console"

> Read-only product roadmap (no code). Repositions Voicebox from "an API with a
> test GUI" into a polished, console-style voice playground — Mistral/OpenAI
> build-console aesthetic — with use-case **templates** that turn the tool into a
> product, while keeping the local inference API fully assumed and exposed via a
> **"View code"** panel.

## TL;DR

Voicebox today is, in practice, **a local inference server (FastAPI) + a GUI to
drive and test it**. The vision is to keep that nature but *assume it proudly*:
make it look and feel like a developer **build console** (clean 3-column
playground, waveform player, "View code" snippets), and add a thin layer of
**use-case templates** (presets of config + example text) so the product knows
what it's for.

**Key finding: most of the hard parts already exist.** Waveform player
(WaveSurfer.js), a `VoiceProfile` data model with samples + cloning + preset
voices + ZIP import/export, an OpenAI-compatible API, an engine catalog, and a
shadcn + Storybook design system are all present. This is mostly a **UI
re-composition + a templates layer**, not new infrastructure.

The central conceptual shift: **"voice" (not "model") as the first-class
object**, and **"template" as a saved config preset**. Both map cleanly onto
existing primitives.

---

## 1. Current-state inventory (what we build on)

| Capability | Status | Evidence |
|---|---|---|
| Waveform audio player | **Exists** | `app/src/components/AudioPlayer/AudioPlayer.tsx` (WaveSurfer.js) |
| Voice profile model (save a voice) | **Exists** | `backend/routes/profiles.py`, `models.VoiceProfileCreate/Response` |
| Voice cloning from sample | **Exists** | `app/src/components/VoiceProfiles/AudioSampleRecording.tsx`, `SampleUpload.tsx`, `ProfileForm.tsx` |
| Preset voices per engine | **Exists** | `GET /profiles/presets/{engine}` (kokoro, supertonic, kyutai_pocket …) |
| Profile import/export (ZIP) | **Exists** | `POST /profiles/import` |
| Engine catalog (+ `license`) | **Exists** | `backend/routes/engines.py` (`GET /engines`), `useEngineCatalog()` |
| OpenAI-compatible API | **Exists** | `backend/routes/openai_compat.py` (`/v1/audio/speech`, `/transcriptions`, `/voices`) |
| Reader (fast→quality) | **Exists** | `backend/routes/reader.py` (`/v1/audio/reader`) |
| Model browser grouped by engine + license gate | **Exists (Phase 2)** | `app/src/components/ServerSettings/ModelManagement.tsx` |
| Design system (shadcn + Storybook) | **Exists** | `app/src/components/ui/`, Storybook 10 |
| Main editor / quick generate | **Exists** | `MainEditor`, `QuickTab`, `Generation/FloatingGenerateBox.tsx` |
| Sidebar nav + simple/advanced toggle | **Exists (Phase 1)** | `Sidebar.tsx`, `uiStore.simpleMode` |
| **Console 3-column playground layout** | **MISSING** | current layout is sidebar + single content pane |
| **"View code" snippet panel (cURL/Python/JS)** | **MISSING** | endpoints exist; no UI generates snippets |
| **Use-case templates (config presets + example text)** | **MISSING** | no template concept |
| **Voice gallery with inline preview (▶ per voice)** | **PARTIAL** | profiles list exists; no console-style preview gallery |

**Contradiction with earlier assumptions:** the "voice" object and cloning are
**not** missing — they exist as `VoiceProfile`. The gap is **presentation and a
templates layer**, not core capability.

---

## 2. Target architecture

### 2.1 First-class concepts

- **Voice** = an existing `VoiceProfile` (engine + voice_id/preset OR a cloned
  sample) + display metadata. No new backend model needed; possibly enrich
  `VoiceProfileResponse` with a short preview-clip URL.
- **Template** = a named preset: `{ model, voice, speed, temperature, format,
  chunking, placeholder_text }`. Pure config. Stored client-side first
  (a static `templates.ts` + optional user-saved templates in a Zustand store);
  optionally promoted to a backend `/templates` endpoint later if sharing is
  wanted. **Start client-side — do not build a backend for this in v1.**

### 2.2 Console layout (the main new UI work)

A 3-column shell, reusing existing components:

```
[ left nav ]  [ center playground ]  [ right params ]
```

- **Left nav**: reuse/refactor `Sidebar.tsx` into a denser "BUILD" tree
  (Audio ▸ TTS / STT, Voices, Models, API keys, Docs).
- **Center**: text input + `AudioPlayer` (waveform) + `[</> View code]` +
  `[▶ Generate]`. Reuses `MainEditor` / `FloatingGenerateBox` content.
- **Right params**: model, voice, speed, temperature, format — driven by
  `useEngineCatalog()` + `GET /profiles/presets/{engine}`.

`simpleMode` (Phase 1) maps naturally: **simple = hide right params + nav**,
**advanced = full console**.

### 2.3 "View code" panel

A modal/drawer that serializes the current playground params into runnable
snippets for the **already-existing** `/v1/audio/speech` endpoint:

- Tabs: cURL / Python (`openai` SDK or `requests`) / JS (`fetch`).
- Reads the live server URL from `useServerStore` (dynamic; Phase 3 of the
  desktop plan adds dynamic port — keep this URL-driven).
- Copy button. This is the single feature that most signals "build console".

### 2.4 Templates layer (the "product" feel)

- A launcher screen (or a "Templates" strip on the TTS playground) listing
  presets: *Lire un article*, *Doubler une vidéo*, *Voix off / podcast*,
  *Livre audio*, *Réponse vocale (faible latence)*.
- Selecting a template **pre-fills** the playground (params + placeholder text)
  and navigates to it. No separate engine, no separate screen logic — it just
  sets state.
- Each template is one object in a static catalog; trivially add/remove.

### 2.5 Voice gallery

Promote `VoiceProfiles` + preset voices into a console-style **Voices** screen:
cards with a ▶ inline preview (short pre-rendered clip per voice) and a
"+ Clone my voice" card reusing `AudioSampleRecording` / `SampleUpload`.

---

## 3. What stays the same (explicitly)

- The FastAPI sidecar, all `/v1/*` endpoints, CORS, engine registry — untouched.
- The API is **not hidden** — it is *assumed* and surfaced via "View code" + an
  "API keys / Docs" nav section.
- Existing routes keep working; the console is a re-composition, not a rewrite.
- Voice cloning machinery (`VoiceProfiles`) is reused as-is.

---

## 4. Phased roadmap

### Phase A — "Console-ify the current screen" (low risk, high signal) (~3-5 days)
Smallest set that makes it *feel* like a build console, on the existing layout:
- **"View code" panel** (cURL/Python/JS from current params → `/v1/audio/speech`). Reuse `useServerStore` URL.
- Ensure the **waveform `AudioPlayer`** is the primary output surface on the TTS screen, with download + "copy link".
- Right-hand **params panel** (model/voice/speed/format/temperature) driven by `useEngineCatalog` + presets.
- Files: `app/src/components/MainEditor/`, `app/src/components/Generation/`, new `ViewCodePanel.tsx`, `app/src/components/AudioPlayer/`.

### Phase B — Templates layer (the product feel) (~2-4 days)
- Static `templates.ts` catalog (5-6 use-case presets) + a launcher strip / screen.
- Selecting a template pre-fills the playground state and routes to TTS.
- Optional: user-saved templates in a Zustand `templateStore` (persisted).
- Files: new `app/src/lib/templates.ts`, `app/src/components/Templates/`, router entry, `uiStore`/new store.

### Phase C — Voices as first-class (gallery + clone) (~3-5 days)
- Console-style **Voices** gallery: cards + inline ▶ preview, "Clone my voice" reusing `VoiceProfiles` recording/upload.
- Pre-render or cache a short preview clip per preset/profile voice.
- Files: `app/src/components/VoicesTab/`, `app/src/components/VoiceProfiles/`, possibly enrich `VoiceProfileResponse` with a preview URL in `backend/routes/profiles.py`.

### Phase D — Full 3-column console shell (the visual refactor) (~1-2 weeks)
- Refactor `Sidebar.tsx` into the denser "BUILD" nav; introduce the 3-column shell; wire `simpleMode` to collapse to a clean single pane.
- Polish with the existing shadcn/Storybook design system (console/dark density).
- Files: `app/src/components/AppFrame/`, `Sidebar.tsx`, `app/src/router.tsx`, design-system stories.

> Sequencing rationale: A and B deliver the "build console + product" impression
> on the *current* layout with minimal risk, before committing to the larger
> visual refactor in D. C can run in parallel with B.

---

## 5. Risks / decisions

| Decision / risk | Recommendation |
|---|---|
| Templates: client-side vs backend | **Client-side static catalog first.** Add `/templates` only if sharing/sync is needed. |
| "Voice" as new model | **Reuse `VoiceProfile`.** Don't introduce a parallel model. |
| Dynamic server URL in "View code" | Read from `useServerStore`; aligns with desktop-plan Phase 3 (dynamic port). |
| Voice preview clips | Pre-render short clips lazily on first view; cache. Don't block gallery render on synthesis. |
| Scope creep into a full rewrite | Phase A/B explicitly avoid the big refactor; D is opt-in once value is proven. |
| Relationship to desktop-app-plan.md | Complementary: this doc = product/UX direction; `desktop-app-plan.md` = packaging/distribution (tray ✅ done, notarization/DMG pending). |

---

## 6. UI mockups (ASCII)

### Console TTS playground (3-column)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ ●●●  Voicebox                                              ● :17493   ⚙   👤  │
├──────────────┬──────────────────────────────────────────────┬───────────────┤
│ BUILD        │  Text to Speech                               │  Paramètres   │
│ ▸ Audio      │  ┌─────────────────────────────────────────┐ │  Modèle       │
│   • TTS  ◀   │  │ Entre le texte à synthétiser…           │ │  [Voxtral 4B ▾]│
│   • STT      │  │                                  0/5000 │ │  Voix         │
│ ▸ Voices     │  └─────────────────────────────────────────┘ │  [Léa · FR  ▾]│
│ ▸ Models     │  ┌─────────────────────────────────────────┐ │  Vitesse      │
│ ──────────── │  │  ▶ ▁▂▃▅▇▆▄▂▃▅▇▅  00:00 / 00:08          │ │  ──●──  1.0   │
│ ▸ API keys   │  │     [ ⬇ wav ]  [ ⧉ copier le lien ]      │ │  Format [wav▾]│
│ ▸ Docs       │  └─────────────────────────────────────────┘ │  Temp ──●─ 0.7│
│              │              [ </> View code ]  [ ▶ Generate ]│               │
└──────────────┴──────────────────────────────────────────────┴───────────────┘
```

### "View code" panel

```
┌──────────────────────────────────── View code ─────────── ✕ ──┐
│  [ cURL ]  [ Python ]  [ JS ]                        ⧉ copier   │
│  ─────────────────────────────────────────────────────────────│
│  curl http://localhost:17493/v1/audio/speech \                 │
│    -H "Content-Type: application/json" \                       │
│    -d '{ "model":"voxtral-tts","voice":"lea-fr",               │
│         "input":"Bonjour le monde","speed":1.0,                │
│         "response_format":"wav" }' --output speech.wav         │
└────────────────────────────────────────────────────────────────┘
```

### Launcher with templates

```
┌──────────────┬──────────────────────────────────────────────────────────────┐
│ ▸ Audio      │  Templates                                                     │
│   • TTS  ◀   │  ┌─────────────────────────────────────────────────────────┐  │
│              │  │ 📄 Lire un article    voix posée · studio · wav     ›   │  │
│              │  │ 🎬 Doubler une vidéo  timing · srt → audio          ›   │  │
│              │  │ 🎧 Voix off / podcast voix grave · expressive       ›   │  │
│              │  │ 📚 Livre audio        narrateur · chapitrage long   ›   │  │
│              │  │ 💬 Réponse vocale     rapide · faible latence        ›   │  │
│              │  └─────────────────────────────────────────────────────────┘  │
│              │  Récents · « Bonjour le monde »  Léa · voxtral   2 min   ↻     │
└──────────────┴──────────────────────────────────────────────────────────────┘
```

### Voices gallery

```
┌──────────────┬────────────────────────────────────────────────────────────┐
│ ▸ Voices ◀   │  Voices                                    [ recherche 🔍 ]  │
│              │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐         │
│              │  │ Léa   FR │ │ Hugo  FR │ │ Sofia ES │ │ + Cloner │         │
│              │  │ ▶ ▁▃▅▂   │ │ ▶ ▁▅▃▂   │ │ ▶ ▂▅▇▃   │ │  ma voix │         │
│              │  │[Utiliser]│ │[Utiliser]│ │[Utiliser]│ │          │         │
│              │  └──────────┘ └──────────┘ └──────────┘ └──────────┘         │
└──────────────┴────────────────────────────────────────────────────────────┘
```

---

## Critical files (for whoever implements)
- `app/src/components/AudioPlayer/AudioPlayer.tsx` (waveform — reuse)
- `app/src/components/MainEditor/`, `app/src/components/Generation/FloatingGenerateBox.tsx`
- `app/src/components/VoiceProfiles/`, `app/src/components/VoicesTab/`
- `app/src/lib/hooks/useEngineCatalog.ts`, `backend/routes/engines.py`
- `backend/routes/profiles.py` (presets, cloning, import/export)
- `app/src/components/Sidebar.tsx`, `app/src/router.tsx`, `app/src/stores/uiStore.ts`
- New: `app/src/lib/templates.ts`, `app/src/components/Templates/`, `ViewCodePanel.tsx`
