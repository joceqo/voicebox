#!/usr/bin/env python3
"""TTS benchmark / rating CLI for the Voicebox backend.

Drives the *existing* HTTP API — ``POST /v1/audio/speech`` for generation and
``POST /v1/audio/transcriptions`` for the intelligibility (WER) step — and
rates the local TTS engines. It does NOT import the engines directly: it tests
the real integration surface a third-party app uses (per the project's design
note).

For each engine x language x fixture it measures:

  * ``ttfa_ms``  — time to first audio byte (response_format=pcm).
  * ``rtf``      — wallclock generation time / audio duration
                   (duration = pcm_bytes / 2 / 24000, int16 mono 24 kHz).
  * ``total_ms`` — total wallclock for the request.
  * ``intelligibility_wer`` — optional; feed generated audio back through STT
                   and compute normalized word error rate vs the input text.

Outputs one JSON record per engine plus a Markdown leaderboard sorted by a
composite ``fast_score``.

Run the server first (default port matches package.json ``dev:server``):

    uvicorn backend.main:app --reload --port 17493

Then, from the repo root:

    python -m backend.benchmarks.run
    python backend/benchmarks/run.py --engines kokoro,supertonic --skip-wer
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import re
import statistics
import struct
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

# httpx is already a backend dependency (see requirements.txt).
try:
    import httpx
except ImportError:  # pragma: no cover - dependency guard
    print(
        "ERROR: httpx is required. Install backend deps:\n"
        "  pip install -r backend/requirements.txt\n"
        "  (or: pip install httpx)",
        file=sys.stderr,
    )
    sys.exit(2)

# Support both ``python -m backend.benchmarks.run`` (package context) and
# ``python backend/benchmarks/run.py`` (script context, no package parent).
try:
    from .fixtures import Fixture, fixtures_for_languages
except ImportError:  # pragma: no cover - script-mode fallback
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from fixtures import Fixture, fixtures_for_languages  # type: ignore


# Audio format constants — the speech endpoint emits raw int16 mono 24 kHz for
# response_format=pcm (no header). Used to convert byte counts to seconds and
# to wrap PCM into a WAV for the transcription endpoint.
SAMPLE_RATE = 24000
BYTES_PER_SAMPLE = 2  # int16
CHANNELS = 1

DEFAULT_BASE_URL = "http://localhost:17493"
DEFAULT_LANGUAGES = ["fr", "en"]
DEFAULT_OUT = Path(__file__).resolve().parent / "results"

# Engines whose generated audio we WER against using these STT models, in
# preference order. The transcription route maps these names to local models.
STT_MODEL_CANDIDATES = ["whisper-turbo", "whisper-1"]

# Per-request timeout (seconds). First-run model downloads can be slow; this is
# generous but bounded so a wedged engine can't hang the whole run forever.
REQUEST_TIMEOUT = 600.0


# ──────────────────────────────────────────────────────────────────────────
# Text normalization + WER
# ──────────────────────────────────────────────────────────────────────────

_PUNCT_RE = re.compile(r"[^\w\s]", flags=re.UNICODE)
_WS_RE = re.compile(r"\s+")


def normalize_text(text: str) -> list[str]:
    """Lowercase, strip punctuation, collapse whitespace -> word tokens."""
    text = text.lower()
    text = _PUNCT_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text).strip()
    return text.split(" ") if text else []


def word_error_rate(reference: str, hypothesis: str) -> float:
    """Normalized word-level edit distance (Levenshtein over tokens).

    Returns errors / max(len(reference_words), 1). 0.0 = perfect match.
    """
    ref = normalize_text(reference)
    hyp = normalize_text(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0

    # Classic DP edit distance over word lists.
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, start=1):
        cur = [i] + [0] * len(hyp)
        for j, h in enumerate(hyp, start=1):
            cost = 0 if r == h else 1
            cur[j] = min(
                prev[j] + 1,      # deletion
                cur[j - 1] + 1,   # insertion
                prev[j - 1] + cost,  # substitution / match
            )
        prev = cur
    return prev[len(hyp)] / len(ref)


# ──────────────────────────────────────────────────────────────────────────
# WAV helpers (wrap raw PCM for the transcription endpoint)
# ──────────────────────────────────────────────────────────────────────────

def pcm_to_wav(pcm: bytes) -> bytes:
    """Wrap raw int16 mono 24 kHz PCM in a minimal WAV container."""
    data_len = len(pcm)
    byte_rate = SAMPLE_RATE * CHANNELS * BYTES_PER_SAMPLE
    block_align = CHANNELS * BYTES_PER_SAMPLE
    header = b"RIFF"
    header += struct.pack("<I", 36 + data_len)
    header += b"WAVE"
    header += b"fmt "
    header += struct.pack("<I", 16)            # fmt chunk size
    header += struct.pack("<H", 1)             # PCM
    header += struct.pack("<H", CHANNELS)
    header += struct.pack("<I", SAMPLE_RATE)
    header += struct.pack("<I", byte_rate)
    header += struct.pack("<H", block_align)
    header += struct.pack("<H", BYTES_PER_SAMPLE * 8)  # bits per sample
    header += b"data"
    header += struct.pack("<I", data_len)
    return header + pcm


def pcm_duration_s(pcm_bytes: int) -> float:
    """Seconds of audio for a raw PCM byte count (int16 mono 24 kHz)."""
    samples = pcm_bytes / BYTES_PER_SAMPLE / CHANNELS
    return samples / SAMPLE_RATE


# ──────────────────────────────────────────────────────────────────────────
# Engine discovery (via the live API — no engine imports)
# ──────────────────────────────────────────────────────────────────────────

async def discover_engines(client: httpx.AsyncClient) -> dict[str, str]:
    """Return ``{engine: a_valid_preset_voice_id}`` from ``GET /v1/audio/voices``.

    The voices endpoint lists exactly the preset-capable engines reachable for
    single-engine speech (it enumerates ``_get_preset_voice_ids`` per engine),
    so this is the authoritative, server-driven set. We keep the first voice id
    seen per engine as the voice to benchmark with.
    """
    resp = await client.get("/v1/audio/voices", timeout=30.0)
    resp.raise_for_status()
    data = resp.json()
    engine_voice: dict[str, str] = {}
    for voice in data.get("voices", []):
        engine = voice.get("engine")
        voice_id = voice.get("id")
        if engine and voice_id and engine not in engine_voice:
            engine_voice[engine] = voice_id
    return engine_voice


async def discover_stt_model(client: httpx.AsyncClient) -> str | None:
    """Pick an available STT model name for the WER step, or None."""
    try:
        resp = await client.get("/v1/audio/voices", timeout=30.0)
        resp.raise_for_status()
        available = set(resp.json().get("transcription_models", []))
    except Exception:
        available = set()
    for candidate in STT_MODEL_CANDIDATES:
        # whisper-1 is an alias the route maps internally; accept it regardless.
        if candidate == "whisper-1" or candidate in available:
            return candidate
    # Fall back to whisper-turbo: the route maps unknown names to turbo anyway.
    return "whisper-turbo"


# ──────────────────────────────────────────────────────────────────────────
# Single measurement
# ──────────────────────────────────────────────────────────────────────────

async def measure_one(
    client: httpx.AsyncClient,
    engine: str,
    voice: str,
    fixture: Fixture,
    stt_model: str | None,
) -> dict[str, Any]:
    """Generate one fixture for one engine; measure ttfa/rtf/total + WER.

    Returns a per-sample dict. On failure, the dict has an ``error`` key and
    null metrics so aggregation can skip it without aborting the run.
    """
    payload = {
        "model": f"voicebox-{engine}",
        "input": fixture["text"],
        "voice": voice,
        "response_format": "pcm",
    }

    result: dict[str, Any] = {
        "fixture_id": fixture["id"],
        "language": fixture["language"],
        "ttfa_ms": None,
        "rtf": None,
        "total_ms": None,
        "intelligibility_wer": None,
        "error": None,
    }

    pcm_chunks: list[bytes] = []
    start = time.perf_counter()
    first_byte_at: float | None = None

    try:
        async with client.stream("POST", "/v1/audio/speech", json=payload) as resp:
            if resp.status_code != 200:
                body = (await resp.aread()).decode("utf-8", "replace")[:500]
                result["error"] = f"speech HTTP {resp.status_code}: {body}"
                return result
            async for chunk in resp.aiter_bytes():
                if not chunk:
                    continue
                if first_byte_at is None:
                    first_byte_at = time.perf_counter()
                pcm_chunks.append(chunk)
    except Exception as e:  # network error, disconnect, timeout
        result["error"] = f"speech request failed: {type(e).__name__}: {e}"
        return result

    end = time.perf_counter()
    pcm = b"".join(pcm_chunks)

    if not pcm:
        result["error"] = "no audio bytes received"
        return result

    total_ms = (end - start) * 1000.0
    ttfa_ms = (first_byte_at - start) * 1000.0 if first_byte_at else total_ms
    duration_s = pcm_duration_s(len(pcm))
    rtf = (end - start) / duration_s if duration_s > 0 else None

    result["ttfa_ms"] = round(ttfa_ms, 2)
    result["total_ms"] = round(total_ms, 2)
    result["rtf"] = round(rtf, 4) if rtf is not None else None
    result["audio_s"] = round(duration_s, 3)

    # ── Optional WER via the transcription endpoint ──────────────────────
    if stt_model is not None:
        try:
            wav = pcm_to_wav(pcm)
            files = {"file": ("audio.wav", io.BytesIO(wav), "audio/wav")}
            form = {"model": stt_model, "language": fixture["language"]}
            tr = await client.post(
                "/v1/audio/transcriptions",
                files=files,
                data=form,
                timeout=REQUEST_TIMEOUT,
            )
            if tr.status_code == 200:
                transcript = tr.json().get("text", "")
                result["intelligibility_wer"] = round(
                    word_error_rate(fixture["text"], transcript), 4
                )
                result["transcript"] = transcript
            else:
                # WER is best-effort: record why it was skipped, keep going.
                result["wer_skipped"] = f"STT HTTP {tr.status_code}"
        except Exception as e:
            result["wer_skipped"] = f"{type(e).__name__}: {e}"

    return result


# ──────────────────────────────────────────────────────────────────────────
# Aggregation + scoring
# ──────────────────────────────────────────────────────────────────────────

def _mean(values: list[float]) -> float | None:
    return round(statistics.fmean(values), 4) if values else None


def _median(values: list[float]) -> float | None:
    return round(statistics.median(values), 4) if values else None


def aggregate_engine(
    engine: str,
    voice: str,
    samples: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the per-engine record (per-language mean/median) from samples."""
    model_name = engine  # display id; canonical model_name not surfaced via API
    record: dict[str, Any] = {
        "model_name": model_name,
        "engine": engine,
        "kind": "tts",
        "voice": voice,
        "measured_at": None,  # stamped by the runner at write time
        "per_language": {},
        "scores": {"speed": None, "quality": None},
        "tier_hint": None,
        "adaptive_priority_suggested": None,
        "samples": samples,
    }

    ok = [s for s in samples if s.get("error") is None]
    errors = [s["error"] for s in samples if s.get("error")]
    if errors and not ok:
        record["error"] = errors[0]

    by_lang: dict[str, list[dict[str, Any]]] = {}
    for s in ok:
        by_lang.setdefault(s["language"], []).append(s)

    for lang, rows in by_lang.items():
        ttfa = [r["ttfa_ms"] for r in rows if r.get("ttfa_ms") is not None]
        rtf = [r["rtf"] for r in rows if r.get("rtf") is not None]
        total = [r["total_ms"] for r in rows if r.get("total_ms") is not None]
        wer = [
            r["intelligibility_wer"]
            for r in rows
            if r.get("intelligibility_wer") is not None
        ]
        record["per_language"][lang] = {
            "ttfa_ms": _mean(ttfa),
            "ttfa_ms_median": _median(ttfa),
            "rtf": _mean(rtf),
            "rtf_median": _median(rtf),
            "total_ms": _mean(total),
            "intelligibility_wer": _mean(wer),
            "n": len(rows),
        }

    return record


def _overall_rtf(record: dict[str, Any]) -> float | None:
    vals = [
        pl["rtf"]
        for pl in record["per_language"].values()
        if pl.get("rtf") is not None
    ]
    return _mean(vals)


def _overall_ttfa(record: dict[str, Any]) -> float | None:
    vals = [
        pl["ttfa_ms"]
        for pl in record["per_language"].values()
        if pl.get("ttfa_ms") is not None
    ]
    return _mean(vals)


def _overall_wer(record: dict[str, Any]) -> float | None:
    vals = [
        pl["intelligibility_wer"]
        for pl in record["per_language"].values()
        if pl.get("intelligibility_wer") is not None
    ]
    return _mean(vals)


def _minmax_norm(value: float, lo: float, hi: float, invert: bool) -> float:
    """Scale ``value`` into 0..1 across [lo, hi]. ``invert`` -> lower is better."""
    if hi <= lo:
        return 1.0  # degenerate cohort (single engine) -> top score
    frac = (value - lo) / (hi - lo)
    return round(1.0 - frac if invert else frac, 4)


def compute_scores(records: list[dict[str, Any]]) -> None:
    """Fill ``scores``, ``tier_hint``, ``adaptive_priority_suggested`` in place.

    Normalization is min-max across the benchmarked cohort:
      * speed   — from rtf (lower better) and ttfa (lower better), averaged.
      * quality — from 1 - wer (higher better).
    ``fast_score`` = 0.5*speed + 0.5*quality (used only for ranking/tier here).
    """
    scored = [r for r in records if r.get("error") is None and r["per_language"]]
    if not scored:
        return

    rtfs = [_overall_rtf(r) for r in scored if _overall_rtf(r) is not None]
    ttfas = [_overall_ttfa(r) for r in scored if _overall_ttfa(r) is not None]
    wers = [_overall_wer(r) for r in scored if _overall_wer(r) is not None]

    rtf_lo, rtf_hi = (min(rtfs), max(rtfs)) if rtfs else (0.0, 0.0)
    ttfa_lo, ttfa_hi = (min(ttfas), max(ttfas)) if ttfas else (0.0, 0.0)
    wer_lo, wer_hi = (min(wers), max(wers)) if wers else (0.0, 0.0)

    for r in scored:
        rtf = _overall_rtf(r)
        ttfa = _overall_ttfa(r)
        wer = _overall_wer(r)

        speed_parts: list[float] = []
        if rtf is not None:
            speed_parts.append(_minmax_norm(rtf, rtf_lo, rtf_hi, invert=True))
        if ttfa is not None:
            speed_parts.append(_minmax_norm(ttfa, ttfa_lo, ttfa_hi, invert=True))
        speed = round(statistics.fmean(speed_parts), 4) if speed_parts else None

        if wer is not None:
            # quality from 1-wer, then min-max normalized across cohort.
            quality = _minmax_norm(wer, wer_lo, wer_hi, invert=True)
        else:
            quality = None

        r["scores"] = {"speed": speed, "quality": quality}

        # Composite fast_score (speed-weighted but quality-aware). Missing
        # quality falls back to speed-only so speed engines aren't penalized
        # when WER is unavailable.
        if speed is not None and quality is not None:
            fast = round(0.6 * speed + 0.4 * quality, 4)
        elif speed is not None:
            fast = speed
        else:
            fast = quality if quality is not None else 0.0
        r["fast_score"] = fast

        # tier_hint: speed-dominant engines are "fast", quality-dominant are
        # "quality". Tie-break toward "fast" (the adaptive tier-1 use case).
        if speed is not None and quality is not None:
            r["tier_hint"] = "fast" if speed >= quality else "quality"
        elif speed is not None:
            r["tier_hint"] = "fast"
        else:
            r["tier_hint"] = "quality"

    # adaptive_priority_suggested: rank by fast_score, 0 = fastest tier.
    ranked = sorted(scored, key=lambda r: r.get("fast_score", 0.0), reverse=True)
    for priority, r in enumerate(ranked):
        r["adaptive_priority_suggested"] = priority


# ──────────────────────────────────────────────────────────────────────────
# Leaderboard
# ──────────────────────────────────────────────────────────────────────────

def _fmt(v: Any, suffix: str = "") -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:g}{suffix}"
    return f"{v}{suffix}"


def write_leaderboard(records: list[dict[str, Any]], out_dir: Path) -> Path:
    """Write a Markdown leaderboard sorted by fast_score (desc)."""
    rows = sorted(
        records,
        key=lambda r: (r.get("error") is not None, -(r.get("fast_score") or 0.0)),
    )

    lines: list[str] = []
    lines.append("# Voicebox TTS Benchmark Leaderboard")
    lines.append("")
    lines.append(f"_Generated: {datetime.now().isoformat(timespec='seconds')}_")
    lines.append("")
    lines.append(
        "Sorted by composite **fast_score** (0.6 x speed + 0.4 x quality, "
        "min-max normalized across the benchmarked cohort). Speed is derived "
        "from RTF + TTFA (lower is better); quality from 1 - WER."
    )
    lines.append("")
    lines.append(
        "| # | Engine | fast_score | speed | quality | tier | adaptive_prio | "
        "RTF (fr/en) | TTFA ms (fr/en) | WER (fr/en) |"
    )
    lines.append(
        "|---|--------|-----------|-------|---------|------|---------------|"
        "------------|-----------------|-------------|"
    )

    for i, r in enumerate(rows, start=1):
        if r.get("error") is not None:
            lines.append(
                f"| {i} | `{r['engine']}` | — | — | — | — | — | — | — | "
                f"ERROR: {r['error'][:60]} |"
            )
            continue
        pl = r["per_language"]
        fr = pl.get("fr", {})
        en = pl.get("en", {})

        def pair(key: str, suffix: str = "") -> str:
            return f"{_fmt(fr.get(key), suffix)} / {_fmt(en.get(key), suffix)}"

        lines.append(
            f"| {i} "
            f"| `{r['engine']}` "
            f"| {_fmt(r.get('fast_score'))} "
            f"| {_fmt(r['scores'].get('speed'))} "
            f"| {_fmt(r['scores'].get('quality'))} "
            f"| {_fmt(r.get('tier_hint'))} "
            f"| {_fmt(r.get('adaptive_priority_suggested'))} "
            f"| {pair('rtf')} "
            f"| {pair('ttfa_ms')} "
            f"| {pair('intelligibility_wer')} |"
        )

    lines.append("")
    lines.append("## Notes")
    lines.append("")
    lines.append(
        "- **RTF** < 1.0 means faster than realtime. **TTFA** is time to first "
        "audio byte. **WER** is normalized word error rate vs the input text "
        "(0 = perfect intelligibility)."
    )
    lines.append(
        "- `adaptive_priority_suggested` ranks engines for the adaptive "
        "pipeline: 0 = fastest tier."
    )
    lines.append(
        "- v0 does NOT measure naturalness / MOS — only speed and "
        "intelligibility. Treat `quality` as an intelligibility proxy."
    )

    out_path = out_dir / "leaderboard.md"
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_path


# ──────────────────────────────────────────────────────────────────────────
# Orchestration
# ──────────────────────────────────────────────────────────────────────────

async def run_benchmark(args: argparse.Namespace) -> int:
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    base_url = args.base_url.rstrip("/")
    run_cmd = "uvicorn backend.main:app --reload --port 17493"

    async with httpx.AsyncClient(base_url=base_url, timeout=REQUEST_TIMEOUT) as client:
        # 1) Server reachable?
        try:
            await client.get("/v1/audio/voices", timeout=10.0)
        except Exception as e:
            print(
                f"ERROR: could not reach the Voicebox server at {base_url} "
                f"({type(e).__name__}).\n"
                f"Start the server first:\n  {run_cmd}\n"
                f"(then re-run; override the URL with --base-url if needed)",
                file=sys.stderr,
            )
            return 1

        # 2) Discover engines + a valid voice each.
        try:
            engine_voice = await discover_engines(client)
        except Exception as e:
            print(
                f"ERROR: failed to discover engines from /v1/audio/voices: {e}",
                file=sys.stderr,
            )
            return 1

        if not engine_voice:
            print(
                "ERROR: no preset-capable engines reported by the server.",
                file=sys.stderr,
            )
            return 1

        if args.engines:
            requested = [e.strip() for e in args.engines.split(",") if e.strip()]
            unknown = [e for e in requested if e not in engine_voice]
            if unknown:
                print(
                    f"WARNING: requested engines not available (skipping): "
                    f"{', '.join(unknown)}. "
                    f"Available: {', '.join(sorted(engine_voice))}",
                    file=sys.stderr,
                )
            engines = [e for e in requested if e in engine_voice]
        else:
            engines = sorted(engine_voice)

        if not engines:
            print("ERROR: no engines left to benchmark.", file=sys.stderr)
            return 1

        # 3) STT model for WER (unless skipped).
        stt_model: str | None = None
        if not args.skip_wer:
            stt_model = await discover_stt_model(client)

        fixtures = fixtures_for_languages(args.languages.split(","))
        if not fixtures:
            print(
                f"ERROR: no fixtures for languages '{args.languages}'.",
                file=sys.stderr,
            )
            return 1

        print(
            f"Benchmarking {len(engines)} engine(s) x {len(fixtures)} fixture(s) "
            f"on {base_url} (WER: {'off' if stt_model is None else stt_model})",
            file=sys.stderr,
        )

        records: list[dict[str, Any]] = []
        stamp = datetime.now().isoformat(timespec="seconds")

        for engine in engines:
            voice = engine_voice[engine]
            print(f"  -> {engine} (voice={voice})", file=sys.stderr)
            samples: list[dict[str, Any]] = []
            for fx in fixtures:
                sample = await measure_one(client, engine, voice, fx, stt_model)
                tag = "ok" if sample.get("error") is None else "ERR"
                wer = sample.get("intelligibility_wer")
                wer_s = "" if wer is None else f" wer={wer}"
                print(
                    f"     [{tag}] {fx['id']:<16} "
                    f"ttfa={_fmt(sample.get('ttfa_ms'))} "
                    f"rtf={_fmt(sample.get('rtf'))}{wer_s}"
                    + (f"  {sample['error']}" if sample.get("error") else ""),
                    file=sys.stderr,
                )
                samples.append(sample)

            record = aggregate_engine(engine, voice, samples)
            record["measured_at"] = stamp
            records.append(record)

        # 4) Scores + ranking across the cohort.
        compute_scores(records)

        # 5) Write per-engine JSON + leaderboard.
        for r in records:
            (out_dir / f"{r['engine']}.json").write_text(
                json.dumps(r, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        lb_path = write_leaderboard(records, out_dir)

        print(f"\nWrote {len(records)} engine record(s) + {lb_path}", file=sys.stderr)
        return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="benchmarks.run",
        description=(
            "Benchmark and rate local Voicebox TTS engines over the live HTTP "
            "API (POST /v1/audio/speech + /v1/audio/transcriptions)."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help="Base URL of a running Voicebox server (dev:server uses 17493).",
    )
    parser.add_argument(
        "--engines",
        default=None,
        help="Comma-separated engines to test (default: all preset-capable, "
        "auto-detected from /v1/audio/voices).",
    )
    parser.add_argument(
        "--languages",
        default=",".join(DEFAULT_LANGUAGES),
        help="Comma-separated language codes to benchmark.",
    )
    parser.add_argument(
        "--out",
        default=str(DEFAULT_OUT),
        help="Output directory for <engine>.json and leaderboard.md.",
    )
    parser.add_argument(
        "--skip-wer",
        action="store_true",
        help="Skip the intelligibility WER step (no STT round-trip).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return asyncio.run(run_benchmark(args))
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
