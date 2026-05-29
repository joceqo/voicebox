"""Session-based "fast-first, single monotonic voice switch" reader.

The reader plays a piece of text with EXACTLY ONE voice transition. The
segments ``[0, Y)`` are spoken by the FAST engine (supertonic — one voice);
the segments ``[Y, n)`` are spoken by the QUALITY engine (voxtral — one
voice). The blocks are contiguous and the switch is monotonic: once playback
is on voxtral it NEVER falls back to supertonic for a later segment. This
avoids the previous per-segment "live upgrade" behaviour, which flip-flopped
the narrator's voice (supertonic and voxtral are different speakers).

Three-phase model:

1. ``POST /v1/audio/reader`` synthesizes a FAST baseline (supertonic) for
   EVERY chunk synchronously, so the caller can start playing right away.
   From the fast durations it computes ``playback_arrival_s(i)`` (when the
   ear reaches segment i on the fast track) and finds the SMALLEST ``Y`` such
   that voxtral can generate the WHOLE suffix ``[Y, n)`` in order and land
   every segment before playback reaches it (a "sustainable suffix"). Because
   voxtral is typically slower than realtime, the last segment is the
   tightest, so a larger Y is always easier. If no Y < n is sustainable,
   ``Y = n`` → the live track is entirely supertonic (quality is then only
   available via the full-HQ replay).

2. A LIVE background task regenerates the suffix ``[Y, n)`` with voxtral, in
   order, ONE SEGMENT PER CALL (each segment's audio is EXACTLY its own text —
   no merged-block / equal-frame re-splitting, which used to misalign audio to
   text), and swaps each segment in (``upgraded``). After the first real
   voxtral generation it refines the RTF/overhead estimate and RE-RUNS the Y
   computation for the remaining suffix; if voxtral fell behind, Y is moved
   LATER (the earliest not-yet-committed voxtral segments drop back to
   supertonic), keeping the suffix a single sustainable contiguous block.

   The supertonic prefix ``[0, Y)`` and the voxtral suffix ``[Y, n)`` are
   strictly DISJOINT in the live timeline: no text is spoken by both engines.

3. A FULL-HQ background task ensures EVERY segment has cached voxtral bytes
   (all-voxtral, one voice) for HQ re-listening, sharing the per-index
   voxtral cache with the live pass.

``GET /v1/audio/reader/{session_id}`` returns the current per-segment state
(best available WAV, recolored engine, status, timings), ``live_start_index``
(= Y), the detected/resolved ``language``, and a ``full_hq`` block.
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import re
import time
import uuid
import wave
from collections import OrderedDict
from dataclasses import dataclass, field

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1", tags=["reader"])

FAST_ENGINE = "supertonic"
QUALITY_ENGINE = "voxtral"
SAMPLE_RATE = 24000

# Voxtral wall-clock generation time model (refined adaptively after the
# first real generation): g_i ≈ VOXTRAL_RTF * audio_duration_i + OVERHEAD_S.
# Measured RTF on this machine is ≈3.5, so the default is intentionally
# pessimistic — it is expected and CORRECT that this yields a large Y (often
# Y = n, i.e. live stays all-supertonic and quality comes from the full_hq
# replay). We never force voxtral live if it cannot comfortably sustain.
VOXTRAL_RTF = 3.5          # wall-seconds of generation per second of audio
VOXTRAL_OVERHEAD_S = 0.5   # fixed per-call overhead (seconds)
SCHEDULE_SAFETY_S = 1.5    # margin: must finish this long before playback hits it

# Languages the QUALITY engine (voxtral) supports. Detection is clamped to
# this set; anything else (or low confidence) falls back to "en".
SUPPORTED_LANGS = {"en", "fr", "es", "de", "it", "pt", "nl", "ar", "hi"}
DEFAULT_LANG = "en"
LANGDETECT_MIN_SCORE = 0.5

# Keep at most this many sessions; oldest are evicted (rough TTL/size cap).
_MAX_SESSIONS = 20
_SESSIONS: "OrderedDict[str, ReaderSession]" = OrderedDict()


def _openai_error(status_code, message, err_type, code, headers=None):
    return JSONResponse(
        status_code=status_code,
        content={"error": {"message": message, "type": err_type, "code": code}},
        headers=headers,
    )


def _detect_language(text: str) -> str:
    """Detect the language of *text*, clamped to ``SUPPORTED_LANGS``.

    Uses fast-langdetect (FastText "lite" model, bundled/offline). Returns
    ``DEFAULT_LANG`` ("en") if detection fails, is low-confidence, or yields
    an unsupported language. Never returns Arabic for empty/garbage input
    (that would be a low-confidence result → "en").
    """
    sample = (text or "").strip()
    if not sample:
        return DEFAULT_LANG
    # FastText cannot handle embedded newlines; flatten to one line.
    sample = sample.replace("\n", " ").replace("\r", " ")
    try:
        from fast_langdetect import detect

        res = detect(sample, model="lite", k=1)
        if isinstance(res, list):
            res = res[0] if res else None
        if not res:
            return DEFAULT_LANG
        lang = str(res.get("lang", "")).lower()
        score = float(res.get("score", 0.0))
        if score < LANGDETECT_MIN_SCORE:
            return DEFAULT_LANG
        return lang if lang in SUPPORTED_LANGS else DEFAULT_LANG
    except Exception:
        logger.exception("reader: language detection failed; defaulting to %s", DEFAULT_LANG)
        return DEFAULT_LANG


@dataclass
class ReaderSegment:
    index: int
    text: str
    fast_wav_bytes: bytes
    fast_duration_ms: int = 0
    start_ms: int = 0  # when playback (on the fast track) reaches this segment
    quality_wav_bytes: bytes | None = None  # cached voxtral bytes (shared by both passes)
    engine: str = FAST_ENGINE
    # "fast_final"  : supertonic prefix [0, Y) — final, one voice
    # "fast"        : suffix segment awaiting its voxtral generation (transient)
    # "pending"     : alias of fast for a scheduled-but-not-done suffix segment
    # "upgraded"    : voxtral suffix [Y, n) — swapped in
    status: str = "fast_final"


@dataclass
class ReaderSession:
    id: str
    created_at: float
    segments: list[ReaderSegment] = field(default_factory=list)
    live_task: asyncio.Task | None = None
    full_task: asyncio.Task | None = None
    live_start_index: int = 0  # Y
    # Number of suffix segments the live pass has finished resolving.
    live_resolved: int = 0
    live_total: int = 0
    full_hq_ready: bool = False
    q_voice: str | None = None
    q_lang: str | None = None
    language: str = DEFAULT_LANG  # resolved/detected language (for the UI)
    # Observed voxtral timing model (refined from the first real generation).
    measured_rtf: float = VOXTRAL_RTF
    measured_overhead: float = VOXTRAL_OVERHEAD_S
    y_moved: bool = False  # did Y shift after RTF refinement?


def _split_segments(text: str) -> list[str]:
    """Split *text* into per-segment chunks (one sentence per segment, with
    over-long sentences broken up by the shared chunk helper).

    Whitespace (incl. newlines from console copy-paste / word-wrap) is collapsed
    first so a line break in the MIDDLE of a sentence does not get treated as a
    sentence boundary. Sentences are split only on ``.!?``."""
    from ..utils.chunked_tts import split_text_into_chunks

    text = re.sub(r"\s+", " ", text or "").strip()
    sentences = re.findall(r"[^.!?]+[.!?]*", text) or [text]
    out: list[str] = []
    for s in sentences:
        s = s.strip()
        if not s:
            continue
        out.extend(c for c in split_text_into_chunks(s) if c.strip())
    return out or [text.strip()]


def _wav_duration_ms(wav_bytes: bytes) -> int:
    """Duration in ms of a mono int16 WAV, parsed via ``wave``."""
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as w:
            frames = w.getnframes()
            rate = w.getframerate() or SAMPLE_RATE
            return int(frames * 1000 / rate)
    except Exception:
        n_samples = max(0, len(wav_bytes) - 44) // 2
        return int(n_samples * 1000 / SAMPLE_RATE)


def _best_wav(seg: ReaderSegment) -> bytes:
    return seg.quality_wav_bytes if seg.quality_wav_bytes is not None else seg.fast_wav_bytes


def _serialize_segments(session: ReaderSession) -> list[dict]:
    """Per-segment table with timings recomputed from the CURRENT best WAV."""
    out = []
    cumulative_ms = 0
    for seg in session.segments:
        wav = _best_wav(seg)
        dur = _wav_duration_ms(wav)
        start_ms = cumulative_ms
        cumulative_ms += dur
        out.append(
            {
                "index": seg.index,
                "text": seg.text,
                "engine": seg.engine,
                "status": seg.status,
                "audio_b64": base64.b64encode(wav).decode("ascii"),
                "start_ms": start_ms,
                "end_ms": cumulative_ms,
            }
        )
    return out


def _serialize_full_hq(session: ReaderSession) -> dict:
    """Complete all-voxtral version, per-segment, for HQ re-listening."""
    if not session.full_hq_ready:
        return {"ready": False, "segments": []}
    out = []
    cumulative_ms = 0
    for seg in session.segments:
        wav = seg.quality_wav_bytes
        if wav is None:
            wav = seg.fast_wav_bytes  # defensive: keep the HQ playlist continuous
        dur = _wav_duration_ms(wav)
        start_ms = cumulative_ms
        cumulative_ms += dur
        out.append(
            {
                "index": seg.index,
                "audio_b64": base64.b64encode(wav).decode("ascii"),
                "start_ms": start_ms,
                "end_ms": cumulative_ms,
            }
        )
    return {"ready": True, "segments": out}


def _resolve_voice(engine: str, hint: str | None, language: str | None) -> tuple[str, str]:
    """Pick a valid (voice_id, language) for *engine*.

    A missing/empty language defaults to "en" — it must NEVER fall through to
    an Arabic voice (which is what alphabetical ``sorted(valid)`` used to do
    for voxtral). For supertonic (language-agnostic) any preset voice works +
    the language is carried as the hint. For voxtral we prefer a voice whose
    pinned language matches the requested language; otherwise the caller's
    hint (if valid), otherwise an English voice.
    """
    from ..services.profiles import _get_preset_voice_ids, _get_preset_voice_language

    valid = _get_preset_voice_ids(engine)
    if not valid:
        raise ValueError(f"Engine '{engine}' does not expose preset voices")

    want_lang = (language or "").lower() or DEFAULT_LANG

    if engine == FAST_ENGINE:
        # Language-agnostic: any voice, language carried by the hint.
        voice_id = hint if hint in valid else sorted(valid)[0]
        return voice_id, want_lang

    # voxtral: match the pinned language when possible.
    for vid in sorted(valid):
        if (_get_preset_voice_language(engine, vid) or "").lower() == want_lang:
            return vid, want_lang
    if hint in valid:
        return hint, (_get_preset_voice_language(engine, hint) or want_lang)
    # No language match and no usable hint: prefer an English voice rather
    # than blindly taking the alphabetically-first one (which is Arabic).
    for vid in sorted(valid):
        if (_get_preset_voice_language(engine, vid) or "").lower() == DEFAULT_LANG:
            return vid, DEFAULT_LANG
    voice_id = sorted(valid)[0]
    return voice_id, (_get_preset_voice_language(engine, voice_id) or DEFAULT_LANG)


def _estimate_gen_seconds(audio_duration_ms: int, rtf: float, overhead: float) -> float:
    """Predicted voxtral wall-clock generation time for a chunk of the given
    fast-audio duration, under the current RTF/overhead estimate."""
    return rtf * (audio_duration_ms / 1000.0) + overhead


def _suffix_sustainable(
    session: ReaderSession, y: int, rtf: float, overhead: float, voxtral_free_at: float
) -> bool:
    """Is the contiguous suffix ``[y, n)`` sustainable?

    Voxtral generates the suffix in order starting at ``voxtral_free_at`` (the
    moment the live task can begin — t≈0 at POST time, or "now" when
    rescheduling). Segment i becomes ready at ``ready(i) = voxtral_free_at +
    sum(g_k for k in [y..i])``. The suffix is sustainable iff for EVERY i in
    ``[y, n)``: ``ready(i) <= playback_arrival_s(i) - SAFETY``.
    """
    clock = voxtral_free_at
    for seg in session.segments[y:]:
        clock += _estimate_gen_seconds(seg.fast_duration_ms, rtf, overhead)
        deadline = seg.start_ms / 1000.0 - SCHEDULE_SAFETY_S
        if clock > deadline:
            return False
    return True


def _compute_y(
    session: ReaderSession, rtf: float, overhead: float, voxtral_free_at: float = 0.0
) -> int:
    """Smallest Y in [0, n] such that the suffix ``[Y, n)`` is sustainable.

    A larger Y (more prefix time, fewer suffix segments) is always easier, so
    we scan from the smallest and return the first sustainable one. If none
    works, returns n (live stays entirely supertonic)."""
    n = len(session.segments)
    for y in range(0, n + 1):
        if _suffix_sustainable(session, y, rtf, overhead, voxtral_free_at):
            return y
    return n


def _apply_y(session: ReaderSession, y: int) -> None:
    """Set per-segment statuses for the chosen Y: prefix ``fast_final``,
    suffix ``fast`` (pending voxtral). Does NOT touch already-``upgraded``
    segments (their voxtral bytes are committed)."""
    for seg in session.segments:
        if seg.status == "upgraded":
            continue
        if seg.index < y:
            seg.status = "fast_final"
            seg.engine = FAST_ENGINE
        else:
            seg.status = "fast"  # scheduled suffix, awaiting voxtral


async def _generate_segment(session: ReaderSession, index: int) -> float:
    """Generate the voxtral audio for a SINGLE segment (one synthesis call) and
    commit it. The segment's audio is EXACTLY its own ``text`` — there is no
    merging and no equal-frame re-splitting, so audio always aligns with text.
    Returns measured wall seconds."""
    from ..services.generation import quick_generate_sync

    seg = session.segments[index]
    t0 = time.monotonic()
    wav = await quick_generate_sync(
        engine=QUALITY_ENGINE, voice_id=session.q_voice, text=seg.text, language=session.q_lang
    )
    elapsed = time.monotonic() - t0
    seg.quality_wav_bytes = wav
    seg.engine = QUALITY_ENGINE
    seg.status = "upgraded"
    return elapsed


async def _live_upgrade(session: ReaderSession) -> None:
    """LIVE pass: generate the voxtral suffix ``[Y, n)`` in order, ONE SEGMENT
    PER CALL (each segment's audio is exactly its own text). After the first
    real generation, refine RTF/overhead and RE-RUN the Y computation for the
    still-pending tail; if voxtral fell behind, move Y LATER (drop the earliest
    not-yet-committed suffix segments back to supertonic). The suffix stays one
    contiguous sustainable block, disjoint from the supertonic prefix."""
    if not session.q_voice:
        session.live_resolved = session.live_total
        return

    rtf = session.measured_rtf
    overhead = session.measured_overhead
    measured = False

    y = session.live_start_index
    while y < len(session.segments):
        idx = y
        try:
            elapsed = await _generate_segment(session, idx)
            session.live_resolved += 1

            if not measured:
                measured = True
                audio_s = max(0.001, session.segments[idx].fast_duration_ms / 1000.0)
                # Refine RTF crudely: keep a small fixed overhead, attribute the
                # rest to RTF.
                rtf = max(0.05, (elapsed - VOXTRAL_OVERHEAD_S) / audio_s)
                overhead = VOXTRAL_OVERHEAD_S
                session.measured_rtf = rtf
                session.measured_overhead = overhead

                # Re-run Y for the REMAINING tail using real elapsed time as
                # the voxtral clock origin. Already-committed (upgraded)
                # segments are immutable; only move Y forward within the
                # not-yet-generated tail.
                committed_end = idx + 1
                now = time.time() - session.created_at
                new_y = _compute_y(session, rtf, overhead, voxtral_free_at=now)
                # Never reintroduce supertonic AFTER a committed voxtral seg:
                # clamp new_y to >= committed_end so committed segments stay
                # upgraded, and recolor the not-yet-generated gap if any.
                if new_y > committed_end:
                    session.y_moved = True
                    session.live_start_index = new_y
                    # Drop pending tail segments [committed_end, new_y) back to
                    # supertonic (they were not generated yet).
                    for seg in session.segments[committed_end:new_y]:
                        if seg.status != "upgraded":
                            seg.status = "fast_final"
                            seg.engine = FAST_ENGINE
                            session.live_resolved += 1
                    y = new_y
                    continue
            y = idx + 1
        except Exception:
            logger.exception("reader: voxtral segment %d failed; keeping fast", idx)
            if session.segments[idx].status != "upgraded":
                session.segments[idx].status = "fast_final"
                session.segments[idx].engine = FAST_ENGINE
                session.live_resolved += 1
            y = idx + 1

    session.live_resolved = max(session.live_resolved, session.live_total)


async def _full_hq_pass(session: ReaderSession) -> None:
    """FULL-HQ pass: ensure EVERY segment has cached voxtral bytes (all-voxtral,
    one voice) for HQ re-listening, generating only the gaps the live pass did
    not produce. Shares the per-index voxtral cache."""
    from ..services.generation import quick_generate_sync

    if not session.q_voice:
        return

    for seg in session.segments:
        if seg.quality_wav_bytes is not None:
            continue
        # If the live pass owns this segment (in the suffix) and may still
        # produce it, wait briefly for the shared cache.
        if seg.index >= session.live_start_index:
            for _ in range(600):  # up to ~60s
                if seg.quality_wav_bytes is not None:
                    break
                await asyncio.sleep(0.1)
            if seg.quality_wav_bytes is not None:
                continue
        try:
            wav = await quick_generate_sync(
                engine=QUALITY_ENGINE,
                voice_id=session.q_voice,
                text=seg.text,
                language=session.q_lang,
            )
            seg.quality_wav_bytes = wav
        except Exception:
            logger.exception(
                "reader: full-HQ generation failed for segment %d; HQ falls back to fast",
                seg.index,
            )

    session.full_hq_ready = True


class ReaderRequest(BaseModel):
    input: str = Field(..., min_length=1, max_length=50000)
    voice: str | None = Field(default=None, max_length=100)
    language: str | None = Field(default=None, max_length=10)


@router.post("/audio/reader")
async def create_reader(req: ReaderRequest):
    """Phase 1: detect language (if absent), synthesize the FAST baseline for
    every chunk, compute the single monotonic switch Y, return immediately,
    then run the live + full-HQ passes in the background."""
    from ..services.generation import quick_generate_sync

    # Resolve language: explicit request wins; otherwise auto-detect.
    req_lang = (req.language or "").strip().lower()
    if req_lang in SUPPORTED_LANGS:
        language = req_lang
    elif req_lang:
        # Caller gave an unsupported code: detect from text instead.
        language = _detect_language(req.input)
    else:
        language = _detect_language(req.input)

    try:
        chunk_texts = _split_segments(req.input)

        f_voice, f_lang = _resolve_voice(FAST_ENGINE, req.voice, language)

        segments: list[ReaderSegment] = []
        cumulative_ms = 0
        for i, text in enumerate(chunk_texts):
            wav = await quick_generate_sync(
                engine=FAST_ENGINE, voice_id=f_voice, text=text, language=f_lang
            )
            dur = _wav_duration_ms(wav)
            segments.append(
                ReaderSegment(
                    index=i,
                    text=text,
                    fast_wav_bytes=wav,
                    fast_duration_ms=dur,
                    start_ms=cumulative_ms,
                )
            )
            cumulative_ms += dur
    except ValueError as e:
        return _openai_error(400, str(e), "invalid_request_error", "invalid_request")
    except Exception as e:
        logger.exception("reader: fast generation failed")
        return _openai_error(500, str(e), "server_error", "reader_failed")

    session = ReaderSession(
        id=str(uuid.uuid4()), created_at=time.time(), segments=segments, language=language
    )

    # Resolve the quality voice once (shared by both background passes).
    try:
        session.q_voice, session.q_lang = _resolve_voice(QUALITY_ENGINE, req.voice, language)
    except Exception:
        logger.exception("reader: cannot resolve quality voice; live+HQ disabled")
        session.q_voice = None

    # Compute the single monotonic switch Y (sustainable suffix).
    y = _compute_y(session, VOXTRAL_RTF, VOXTRAL_OVERHEAD_S, voxtral_free_at=0.0)
    session.live_start_index = y
    _apply_y(session, y)
    session.live_total = sum(1 for s in segments if s.index >= y)

    # Evict oldest sessions beyond the cap.
    _SESSIONS[session.id] = session
    while len(_SESSIONS) > _MAX_SESSIONS:
        _old_id, old = _SESSIONS.popitem(last=False)
        for t in (old.live_task, old.full_task):
            if t and not t.done():
                t.cancel()

    session.created_at = time.time()  # the moment we hand back / playback starts
    if session.q_voice:
        session.live_task = asyncio.create_task(_live_upgrade(session))
        session.full_task = asyncio.create_task(_full_hq_pass(session))

    return {
        "session_id": session.id,
        "sample_rate": SAMPLE_RATE,
        "done": session.live_resolved >= session.live_total,
        "live_start_index": session.live_start_index,
        "language": session.language,
        "segments": _serialize_segments(session),
        "full_hq": _serialize_full_hq(session),
    }


@router.get("/audio/reader/{session_id}")
async def get_reader(session_id: str):
    """Phase 2: current per-segment state, the switch index Y, the resolved
    language, and the full-HQ block once ready."""
    session = _SESSIONS.get(session_id)
    if session is None:
        return _openai_error(404, "Unknown reader session", "invalid_request_error", "not_found")

    done = session.live_resolved >= session.live_total
    return {
        "session_id": session.id,
        "sample_rate": SAMPLE_RATE,
        "done": done,
        "live_start_index": session.live_start_index,
        "language": session.language,
        "segments": _serialize_segments(session),
        "full_hq": _serialize_full_hq(session),
    }
