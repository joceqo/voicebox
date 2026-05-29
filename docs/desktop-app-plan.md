# Voicebox → "LM Studio for Voice": Architecture Plan

> Read-only architecture plan (no code) for turning Voicebox into a simple
> desktop app: a main window + a macOS menu-bar (tray) icon + voice-model
> download, with the FastAPI server running as a managed local sidecar that
> other apps (Clicky, crier) can still hit.

## TL;DR — the premise is mostly already built

The biggest finding: **the FastAPI sidecar is already fully wired into Tauri** —
auto-started on launch, health-checked, port-managed, and cleanly shut down.
Voicebox is much closer to "LM Studio for voice" than expected. The model
browser/download/delete UI also already exists end-to-end. The genuinely missing
pieces are narrow: **a macOS menu-bar tray, the LSUIElement/dock policy,
packaging-size strategy, and notarization/CI automation.**

---

## 1. Current-state inventory

| Capability | Status | Evidence |
|---|---|---|
| Tauri spawns FastAPI sidecar on launch | **Exists** | `tauri/src-tauri/src/main.rs:206 start_server`; `app/src/App.tsx:172 startServer` (prod only) |
| `externalBin` / sidecar declared | **Exists** | `tauri.conf.json:16` `externalBin: ["binaries/voicebox-server","binaries/voicebox-mcp"]` |
| Health check on a port | **Exists** | `main.rs:171 check_health` (validates `/health` JSON) |
| Port selection / free / reuse | **Partial** | Fixed `SERVER_PORT=17493` (`main.rs:120`); legacy `8000` cleanup; reuses external server. **No dynamic free-port pick.** |
| Graceful shutdown on quit | **Exists** | `main.rs:692 stop_server`, `RunEvent::Exit` (`main.rs:1430`); parent-pid watchdog `backend/server.py:102` |
| "Keep server running after close" | **Exists** | `main.rs:771 set_keep_server_running` |
| **macOS menu-bar tray** | **MISSING** | No tray/TrayIcon/Menu in `tauri/src-tauri/src/`. `tray-icon`/`muda` are transitive deps only. |
| **LSUIElement / dock-vs-tray policy** | **MISSING** | No `LSUIElement` in `Info.plist`; no `set_activation_policy`. |
| Main window | **Exists** | `tauri.conf.json:42` 1200×800, overlay title bar |
| Text→speech playground | **Exists** | `MainEditor` (`/`), `QuickTab` (`/quick`) |
| Voice/model browser + download + delete | **Exists (rich)** | `app/src/components/ServerSettings/ModelManagement.tsx` (1117 lines): HF model-card, license, download/cancel/delete, live progress |
| Reader (fast→quality) | **Exists** | `backend/routes/reader.py` `/v1/audio/reader` |
| OpenAI-compatible API | **Exists** | `backend/routes/openai_compat.py` (`/v1/audio/speech`, `/transcriptions`, `/voices`) |
| Model download machinery | **Exists** | `backend/routes/models.py` (`/models/download`, cancel, `DELETE`, status, SSE); `backend/utils/tasks.py TaskManager` |
| Engine catalog API | **Exists** | `backend/routes/engines.py list_engines`; `backend/backends/__init__.py:837 _TTS_REGISTRY` |
| CORS for webview + external apps | **Exists** | `backend/app.py:117 _configure_cors` (localhost, `tauri://localhost`, env `VOICEBOX_CORS_ORIGINS`) |
| PyInstaller bundling | **Exists** | `backend/build_binary.py` (CPU `--onefile`, CUDA `--onedir`, MLX on Apple Silicon) |
| Tauri updater configured | **Partial** | `tauri.conf.json:62` pubkey + `latest.json` endpoint. **No sign/notarize/CI automation.** |
| macOS sign / notarize / DMG automation | **MISSING** | `justfile build-tauri` = `bun run tauri build` only |

**Contradictions to earlier assumptions:**
- Tauri already spawns the sidecar — the work is hardening (dynamic port), not building.
- There is effectively **one** frontend (`app/`); `tauri/src/main.tsx` is a 40-line shim whose vite alias `@` → `../app/src`. No separate "simple window" to choose.
- Code uses fixed `17493` + legacy `8000`; no dynamic selection.
- `tray-icon` is present only transitively; not wired in app code.

---

## 2. Target architecture

### 2.1 Sidecar lifecycle (keep; harden)
- **Dynamic free port** instead of fixed `17493`: bind-probe in Rust, pass via `--port` (already an arg, `server.py:235`), report URL back through `start_server` → `useServerStore.setServerUrl`.
- **Already-running server**: reuse-by-name / health-check existing listener already implemented (`main.rs:227-293`); make it dynamic-port-aware.
- **Quit**: keep watchdog + `RunEvent::Exit` + keep-running sentinel.

### 2.2 Menu-bar tray (macOS) — the main new Rust work
Add `tauri::tray::TrayIconBuilder` + `tauri::menu` in `setup()` (`main.rs:1245`). Tray menu:
- Server status line (from the health/`server-log` signal at `main.rs:585`).
- Start / Stop / Restart server → existing commands.
- Open Voicebox → show/focus window.
- Quit.
- Status icon swap (idle vs running).

**Dock vs tray:** default to a **regular dock app** (like LM Studio). Provide an opt-in "menu-bar only" mode via runtime `app.set_activation_policy(Accessory)` (toggleable, not a hard `LSUIElement` in Info.plist).

### 2.3 Main window — keep `app/`, add a "Simple" route
Keep the `app/` frontend (MainEditor, VoicesTab, ModelsTab, Reader, ModelManagement). Add a **lightweight default landing route** (text box + voice/model dropdown + play, reusing `MainEditor`/`QuickTab` + `ModelManagement`) and a **simple/advanced toggle** in the UI store rather than deleting tabs. Don't build a second frontend.

### 2.4 Model download UX — reuse as-is
No new backend. `ModelManagement.tsx` already drives `/models/*` with SSE progress + HF model-card + license. Surface it as a top-level "Discover/Models" screen driven by `engines.py list_engines`.

### 2.5 API exposure
Keep server on localhost; CORS already permits webview + `VOICEBOX_CORS_ORIGINS`. For Clicky/crier to find a dynamic port: write the chosen port to a well-known file in `app_data_dir` (`main.rs:349`) and advertise it via `/health`. Remote mode (`--host 0.0.0.0`) already exists (`main.rs:477`).

---

## 3. Packaging & distribution

### Sidecar size (the real constraint)
`build_binary.py` bundles torch + transformers + (Apple Silicon) MLX whole + librosa + spacy + espeak + kokoro/chatterbox/etc. The CPU `--onefile` binary will be **very large (multi-hundred-MB to ~1GB+)**; first import is slow (Rust startup timeout already 120s, `main.rs:547`).

**Strategy:**
- **Ship the engine binary, NOT the model weights.** Weights download on first run via `/models/download` (LM Studio's model).
- Default to small CPU-friendly engines: Supertonic (400MB), Kyutai Pocket (400MB), Kokoro (350MB), Parakeet STT (478MB) — already adaptive-tiered.
- If the macOS bundle is too large, mirror the existing CUDA `--onedir` split: ship a thin launcher, fetch the full sidecar on first run. Decide after measuring.

### macOS sign / notarize / DMG (NEW)
- `targets: "all"` already produces `.app`/`.dmg`. Add to `justfile build-tauri`: `TAURI_SIGNING_PRIVATE_KEY` (updater) + Apple `APPLE_CERTIFICATE`/`APPLE_ID`/`APPLE_TEAM_ID` for codesign + `notarytool`.
- **The sidecar binary must also be signed** (hardened runtime + existing `Entitlements.plist`).

### Auto-update
- Tauri updater configured (`tauri.conf.json:62`, pubkey + `latest.json`); frontend shim `tauri/src/platform/updater.ts`.
- Missing: release CI that signs → notarizes → generates `latest.json` → publishes.
- **Do not copy crier's appcast** — crier is Swift/Sparkle (`appcast.xml`); Voicebox/Tauri uses the updater's `latest.json`. Reference crier only for the workflow, not the format.

### Voxtral CC BY-NC
Registered (`backend/backends/__init__.py:916`), **not bundled** (download-on-demand) — keep it that way. Gate as **opt-in download + license notice** in `ModelManagement.tsx` (`formatLicense` already plumbed). Never ship Voxtral weights in the `.app`/`.dmg`.

---

## 4. Key decisions / risks

| Decision / risk | Recommendation |
|---|---|
| Two frontends | Non-issue — one real frontend (`app/`). Add a simpler default route + simple/advanced toggle. |
| Sidecar size & 120s startup | Ship engine code, download weights on first run; if too big, download-sidecar-on-first-run (CUDA-style split). |
| Default engines | CPU-friendly tiers (Supertonic/Kyutai/Kokoro + Parakeet STT). Heavy (Qwen/Chatterbox/TADA/Voxtral) = download-on-demand. |
| RAM (24GB) | bf16 Voxtral ~8GB and TADA-3B (~8GB) are tight; default playground to a small engine, warn on heavy load. |
| Dock vs tray | Default dock app; runtime-toggleable Accessory (menu-bar-only) mode. |
| External client port discovery | Stable default port + write chosen port to app-data file + expose via `/health`. |
| Notarization | Must sign the sidecar executable too, not just the app. |
| Voxtral licensing | Opt-in download + license notice; never bundled. |

---

## 5. Phased roadmap

### Phase 1 — MVP: window + autostart + tray (~2-4 days)
Most done; the real build is the tray + dock policy.
- `TrayIconBuilder` + menu (status / start-stop-restart / open / quit) in `main.rs setup()`; wire to existing commands + health signal.
- Runtime activation-policy toggle (dock ⇄ menu-bar-only) + UI-store setting.
- LM-Studio-style simple default route reusing `MainEditor`/`ModelManagement`; simple/advanced toggle.
- Files: `tauri/src-tauri/src/main.rs`, `tauri/src-tauri/Info.plist`, `app/src/router.tsx`, `app/src/components/Sidebar.tsx`, new simple-home component.

### Phase 2 — Model browser polish (~2-3 days, mostly UI)
- Promote `ModelManagement.tsx` to a first-class "Discover/Models" screen grouped by engine.
- Voxtral license-gate dialog before download.
- Files: `app/src/components/ServerSettings/ModelManagement.tsx`, `app/src/components/ModelsTab/`, optionally `backend/routes/engines.py` (add `license` to the catalog).

### Phase 3 — Packaging + notarized DMG (~1-2 weeks, the real lift)
- Measure CPU `--onefile` size; decide bundle-vs-download-sidecar.
- Add codesign + `notarytool` + DMG to `justfile`; sign the sidecar with hardened runtime + `Entitlements.plist`.
- Harden sidecar startup (dynamic port; port-discovery file).
- Files: `justfile`, `tauri/src-tauri/tauri.conf.json`, `tauri/src-tauri/Entitlements.plist`, `tauri/src-tauri/src/main.rs`, `backend/server.py`, `backend/build_binary.py`.

### Phase 4 — Auto-update (~3-5 days once Phase 3 done)
- GitHub release CI: build → sign → notarize → generate `latest.json` (Tauri format, not Sparkle) → publish.
- Verify in-app updater against the published manifest.
- Files: `.github/workflows/`, `justfile`, `tauri/src-tauri/tauri.conf.json`.

---

## 6. UI mockups (ASCII)

### Menu-bar (tray) — macOS
```
              ┌─────────────────────────────┐
  🔊  ◀───────│ Voicebox                    │
              │ ● Serveur actif · :17493    │
              ├─────────────────────────────┤
              │ Ouvrir Voicebox         ⌘O  │
              │ ─────────────────────────── │
              │ ■ Arrêter le serveur        │
              │ ↻ Redémarrer                │
              │ ─────────────────────────── │
              │ Mode menu-bar seul      ☐   │
              │ Quitter                 ⌘Q  │
              └─────────────────────────────┘
```

### Main window — simple mode
```
┌────────────────────────────────────────────────────────────────┐
│ ●●●   Voicebox                                     ● actif :17493│
├───────────┬────────────────────────────────────────────────────┤
│ ▶ Parler  │  Texte → Voix                                       │
│ ◌ Lecteur │  ┌──────────────────────────────────────────────┐  │
│ ⬇ Modèles │  │ Colle ou écris ton texte ici…                │  │
│ ⚙ Réglages│  │                                              │  │
│           │  └──────────────────────────────────────────────┘  │
│           │  Voix [Kokoro · ff_siwis ▾]   Langue [auto ▾]       │
│           │  Vitesse [1× ▾]                       [ ▶ Lire ]    │
│ ───────── │  ────────────────────────────────────────────────  │
│ ◉ simple  │  ⚡ rapide ───────────────▶ 🟢 qualité (Voxtral)    │
│ ○ avancé  │  «La lecture vocale locale change la façon…»        │
└───────────┴────────────────────────────────────────────────────┘
```

### Models / Discover (LM-Studio-style)
```
┌────────────────────────────────────────────────────────────────┐
│  Modèles de voix                              [ recherche … 🔍 ] │
├────────────────────────────────────────────────────────────────┤
│  TTS                                                             │
│   Kokoro 82M       350 Mo   Apache-2.0    ✓ installé      🗑     │
│   Supertonic-3     400 Mo   —             ✓ installé      🗑     │
│   Kyutai Pocket    400 Mo   —             ⬇ Télécharger          │
│   Voxtral 4B bf16  8 Go     CC BY-NC ⚠    ⬇ Télécharger          │
│  STT                                                             │
│   Whisper turbo    1.6 Go   MIT           ⬇ Télécharger          │
│   Parakeet v3      478 Mo   FR/EN auto    ✓ installé      🗑     │
│  ─────────────────────────────────────────────────────────────  │
│  ⬇ Voxtral 4B — 62%   ████████░░░░   4.9 / 8 Go                  │
└────────────────────────────────────────────────────────────────┘
```

### Voxtral license gate (before download)
```
        ┌────────────────────────────────────────────┐
        │  Licence — Voxtral 4B TTS                  │
        │  ──────────────────────────────────────── │
        │  CC BY-NC 4.0 (non-commercial).            │
        │  Perso/local OK. Interdit en produit       │
        │  commercial sans accord de Mistral.        │
        │                                            │
        │           [ Annuler ]  [ Accepter & DL ]   │
        └────────────────────────────────────────────┘
```

---

## Critical files
- `tauri/src-tauri/src/main.rs`
- `tauri/src-tauri/tauri.conf.json`
- `backend/build_binary.py`
- `app/src/components/ServerSettings/ModelManagement.tsx`
- `justfile`
