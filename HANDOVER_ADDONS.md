# Subtitld — Add-on System Handover

## What Subtitld is

Desktop subtitle editor (PySide6 / Python). One single executable; users open a video, edit/transcribe subtitles, and can now generate **dubbed audio** per-speaker and **export** to WAV / FLAC / MP3 / MP4 (re-muxed) or stems / clips.

Code lives at `/home/jonata/Projetos/subtitld`. Key dirs:

- `src/subtitld/interface/` — Qt widgets (top bar, panels, dialogs)
- `src/subtitld/modules/` — non-UI logic (`session`, `file_io`, `bounce`, `audioengine`)
- `src/subtitld/modules/addons/` — add-on host runtime
- `src/subtitld/modules/addons/builtin/` — Edge TTS, whisper.cpp, etc. as built-in providers using the same ABC as out-of-process add-ons (no third-party cloud service ships as a built-in)

## The add-on architecture (already shipping)

**Why it exists.** TTS/ASR models are GB-scale. Subtitld stays small; users opt into heavy models via add-ons that ship as separate PyInstaller binaries.

**Protocol.** JSON-line over stdio, persistent process per add-on. One line = one JSON object. Frames:

- `hello` (add-on → host on startup) declaring `capabilities[]` per task
- `ready` (host → add-on)
- Request envelope: `{"id": "<uuid>", "type": "<task>", "params": {...}}`
- Response frames keyed by same `id`: `progress`, `partial`, then exactly one terminal `result` or `error`
- `cancel`, `shutdown` control frames

**Tasks v0:** `tts.synthesize`, `asr.transcribe`. `translation.translate` declared but not implemented.

**Manifest** (`manifest.json` inside each add-on zip): `id`, `version`, `executable`, `protocol`, `platforms`, `tasks[]`, `voices[]`/`languages[]`, `models[]` (url + sha256 + bundled/download), `config_schema` (renders Configure UI declaratively).

**Catalog.** `https://subtitld.org/addons/catalog.json` — `addons[].releases[].downloads[]` by platform with sha256. Hosted via the `addons-catalog` repo with an `addons.yml` source-of-truth; CI assembles the JSON.

**Reference add-ons that exist:**

- `piper-tts` — light, ~50 MB models
- `coqui-xtts` — XTTS-v2, ~2 GB, voice clone
- `supertonic` — `supertonic-py` SDK, with model selector (`supertonic` / `supertonic-2` / `supertonic-3`), released as v0.0.2

## Critical host-side modules

| File | Role |
|---|---|
| `modules/addons/protocol.py` | Frame encode/parse, error codes |
| `modules/addons/process.py` | `AddonProcess` — `Popen`, stdout/stderr reader threads, request correlation by UUID, timeouts, idle GC, crash recovery |
| `modules/addons/provider.py` | ABCs: `TTSProvider`, `ASRProvider`, `TranslationProvider` with Qt signals |
| `modules/addons/addon_provider.py` | Generic wrappers: `AddonTTSProvider`, `AddonASRProvider` — translate `request()` ↔ signals. **Owns env-piping**: `_build_addon_env(addon_id, options)` converts `CONFIG['addons']['options'][<id>]` into `<ADDON_ID_UPPER>_<KEY_UPPER>` env vars before `Popen` |
| `modules/addons/manager.py` | `AddonManager` singleton: `discover()`, `register_builtin()`, `providers_for_task()`, `default_for_task()`, `shutdown_all()` |
| `modules/addons/installer.py` | Catalog fetch, sha256 verify, atomic extract, uninstall |
| `modules/addons/registry.py` | Wrappers over `session.CONFIG['addons']` — `enabled[]`, `defaults{}`, `options{}` |
| `interface/addons_dialog.py` | 3-tab UI: Browse / Installed / Defaults |
| `interface/left_panel_dubbing.py` | TTS engine combobox is dynamic — populated from `manager.providers_for_task('tts.synthesize')` |
| `interface/left_panel_import.py` | ASR engine combobox dynamic the same way |

**`session.CONFIG['addons']`** layout:

```python
{
  "enabled": ["edge-tts", "piper-tts"],
  "defaults": {"tts.synthesize": "edge-tts", "asr.transcribe": "vosk"},
  "options": {"supertonic": {"model": "supertonic-3", "device": "cpu"}}
}
```

**Voice id namespacing:** project files use `<addon_id>:<voice_id>` (e.g. `edge-tts:pt-BR-AntonioNeural`). Compat layer in USF loader accepts unprefixed ids as `edge-tts:<id>`.

**Directories** (via `platformdirs`):

- `PATH_SUBTITLD_USER_DATA` — `~/.local/share/subtitld` (Linux). Persistent user state.
- `PATH_SUBTITLD_ADDONS = PATH_SUBTITLD_USER_DATA / 'addons'` — one subdir per installed add-on
- `PATH_SUBTITLD_USER_CACHE/dubbing/{uid}.wav` — generated audio cache

## Generation flow (the part that matters for an online service)

A "generation" in Subtitld today = TTS for one subtitle line, or ASR over one audio file. Inputs the host hands the add-on:

**TTS request** (`tts.synthesize`):

```json
{
  "text": "...",
  "voice": "<voice_id_native_to_addon>",
  "language": "pt-br",
  "rate": 1.0,
  "pitch": 0,
  "output_path": "/cache/dubbing/<uuid>.wav",
  "voice_ref_audio": "/path.wav"
}
```

Result: `{"path": "...", "duration_sec": ..., "sample_rate": ..., "channels": ...}`

**ASR request** (`asr.transcribe`):

```json
{
  "audio_path": "/tmp/16k_mono.wav",
  "language": "en",
  "options": {}
}
```

Streams `partial` per segment, then `result` with full list.

Provider abstraction is **the seam** for swapping local execution for a remote service: `AddonTTSProvider` could become `RemoteTTSProvider` and the rest of Subtitld doesn't care — the `TTSProvider` ABC (`provider.py`) defines the entire contract the UI consumes (`generate_speeches`, `stretch`, `list_voices`, signals `voices_updated`, `speech_ready`, `speech_error`).

## Recent finished work (last 2 sessions)

1. **Env piping fix** — `CONFIG['addons']['options']` was saved by the Configure dialog but never delivered to subprocesses. Fixed in `addon_provider._ensure_process`.
2. **Supertonic model selector** — v0.0.2 released with `model` field in `config_schema`, three models, default `supertonic-3`. Tag pushed, CI publishes zip.
3. **Async export with visual indicator** — `bounce.bounce_async` + `BounceThread` added; `ExportDialog` gained `enter_processing_state(msg)` / `finish_processing()` with indeterminate `QProgressBar`. `top_bar.toppanel_export_button_clicked` rewired to async. Exporting no longer freezes the UI; the dialog stays open showing "Exporting <fmt>…" until the worker reports done.

## Open items / known limits

- **Snap**: add-on execve in `~/.local/share/subtitld/addons/` not validated; UI shows banner if blocked. Built-ins still work.
- **macOS notarization** of third-party add-ons — author responsibility, documented in manifest spec.
- **MSIX sandbox** likely blocks subprocess spawn → built-ins only.
- **Auto-update of add-ons**: not in v0.
- **Catalog signing**: only sha256 today; ed25519 punted to v1.
- **Cancellation**: best-effort, depends on add-on honoring checkpoints (Piper/Coqui synchronous C loops).
- **`translation.translate` task**: declared in the protocol, no built-in provider exists yet.

## What I'd tell the new chat

If you're building an **online generation service** that Subtitld talks to, the cleanest integration is to write **one more provider** that implements `TTSProvider` (and/or `ASRProvider`) from `modules/addons/provider.py`, registered via `manager.register_builtin(YourProvider())` at startup — same shape as `edge_tts_provider.py`. Don't reinvent UI: Subtitld already has the speaker panel, voice list, dubbing flow, async export, and stems-vs-mixdown plumbing. You just need to:

1. Implement `list_voices()` (returns voices from your service catalog),
2. Implement `generate_speeches(subtitles)` — kick off async HTTP, emit `speech_ready(uid, subtitle, path)` when WAV lands in `PATH_SUBTITLD_USER_CACHE/dubbing/<uuid>.wav`,
3. Optionally implement `stretch(subtitle, ratio)` for timing fits.

The voice id format in saved projects is `<provider_id>:<voice>` — pick a stable provider id (e.g. `subtitld-cloud`) so projects survive offline reload.

Reference files to read first when starting the new chat:

- `src/subtitld/modules/addons/provider.py` — the ABC you'll implement
- `src/subtitld/modules/addons/builtin/edge_tts_provider.py` — closest analog (cloud TTS, async)
- `src/subtitld/modules/addons/manager.py` — registration entry point
- `src/subtitld/interface/left_panel_dubbing.py` — how the UI consumes providers
