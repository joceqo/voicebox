"""Benchmark corpus for the TTS rating CLI.

Texts are lifted from the project's design note
``voicebox-local-voice-api-test-ideas.md`` ("Concrete evaluation texts"):
short agent statuses, a technical readout, a recipe, a theater line set, and a
long reader paragraph — the scenarios Voicebox is meant to serve daily. A few
short English agent statuses are added so the EN/FR comparison is balanced.

Each fixture is ``{"id", "text", "language"}``. ``language`` is an ISO 639-1
code used both to tag results and (where the API accepts it) to hint the engine.
"""

from __future__ import annotations

from typing import Literal, TypedDict


class Fixture(TypedDict):
    id: str
    text: str
    language: str


# Languages this corpus covers. The runner filters fixtures by the requested
# ``--languages`` set.
SUPPORTED_LANGUAGES: tuple[str, ...] = ("fr", "en")

Language = Literal["fr", "en"]


FIXTURES: list[Fixture] = [
    # ── Short agent statuses (FR) — from the note ────────────────────────
    {
        "id": "fr-status-1",
        "language": "fr",
        "text": "Je regarde le dépôt Voicebox et je vérifie les changements récents.",
    },
    {
        "id": "fr-status-2",
        "language": "fr",
        "text": "Les tests ciblés passent, mais il reste des avertissements de dépréciation.",
    },
    {
        "id": "fr-status-3",
        "language": "fr",
        "text": "J'ai trouvé une piste plus simple pour tester l'API localement.",
    },
    # ── Short agent statuses (EN) — added for FR/EN balance ──────────────
    {
        "id": "en-status-1",
        "language": "en",
        "text": "I found the repository and I am checking the recent commits.",
    },
    {
        "id": "en-status-2",
        "language": "en",
        "text": "The tests passed. There are only deprecation warnings.",
    },
    {
        "id": "en-status-3",
        "language": "en",
        "text": "I need one more permission before I can continue.",
    },
    # ── Technical readout (FR) — from the note ───────────────────────────
    {
        "id": "fr-technical-1",
        "language": "fr",
        "text": (
            "La branche feat openai compat api ajoute un endpoint "
            "POST slash v1 slash audio slash speech, un fallback single engine, "
            "et des tests FastAPI pour WAV et PCM."
        ),
    },
    # ── Recipe (FR) — from the note ──────────────────────────────────────
    {
        "id": "fr-recipe-1",
        "language": "fr",
        "text": (
            "Préchauffe le four à cent quatre-vingts degrés. "
            "Coupe les pommes en quartiers. "
            "Mélange la farine, le sucre et le beurre jusqu'à obtenir une pâte sableuse. "
            "Enfourne trente minutes."
        ),
    },
    # ── Theater line set (FR) — from the note ────────────────────────────
    {
        "id": "fr-theater-1",
        "language": "fr",
        "text": "Noble bouffon et fou du roi.",
    },
    {
        "id": "fr-theater-2",
        "language": "fr",
        "text": "Je ne viens pas ici pour plaire, mais pour dire vrai.",
    },
    # ── Long reader paragraph (FR) — from the note ───────────────────────
    {
        "id": "fr-reader-long",
        "language": "fr",
        "text": (
            "Un bon lecteur vocal local ne devrait pas seulement transformer "
            "du texte en fichier audio. Il devrait pouvoir commencer vite, "
            "suivre la position dans le texte, s'interrompre naturellement, "
            "reprendre au bon endroit, et s'intégrer dans les logiciels qui "
            "produisent déjà du texte en continu."
        ),
    },
    # ── Long reader paragraph (EN) — the note's docs-reader example ──────
    {
        "id": "en-reader-long",
        "language": "en",
        "text": (
            "Voicebox is a local-first AI voice studio. Clone voices from a few "
            "seconds of audio, generate speech in multiple languages, dictate "
            "into any text field, and give any MCP-aware AI agent a voice."
        ),
    },
]


def fixtures_for_languages(languages: list[str]) -> list[Fixture]:
    """Return fixtures whose language is in ``languages`` (order preserved)."""
    wanted = {lang.lower() for lang in languages}
    return [f for f in FIXTURES if f["language"] in wanted]
