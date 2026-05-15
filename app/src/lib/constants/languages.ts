/**
 * Supported languages for voice generation, per engine.
 *
 * Qwen3-TTS supports 10 languages.
 * LuxTTS is English-only.
 * Chatterbox Multilingual supports 23 languages.
 * Chatterbox Turbo is English-only.
 * Kokoro supports 8 languages.
 */

/** All languages that any engine supports. */
export const ALL_LANGUAGES = {
  ar: 'Arabic',
  bg: 'Bulgarian',
  cs: 'Czech',
  da: 'Danish',
  de: 'German',
  el: 'Greek',
  en: 'English',
  es: 'Spanish',
  et: 'Estonian',
  fi: 'Finnish',
  fr: 'French',
  he: 'Hebrew',
  hi: 'Hindi',
  hr: 'Croatian',
  hu: 'Hungarian',
  id: 'Indonesian',
  it: 'Italian',
  ja: 'Japanese',
  ko: 'Korean',
  lt: 'Lithuanian',
  lv: 'Latvian',
  ms: 'Malay',
  nl: 'Dutch',
  no: 'Norwegian',
  pl: 'Polish',
  pt: 'Portuguese',
  ro: 'Romanian',
  ru: 'Russian',
  sk: 'Slovak',
  sl: 'Slovenian',
  sv: 'Swedish',
  sw: 'Swahili',
  tr: 'Turkish',
  uk: 'Ukrainian',
  vi: 'Vietnamese',
  zh: 'Chinese',
} as const;

export type LanguageCode = keyof typeof ALL_LANGUAGES;

/** Per-engine supported language codes. */
export const ENGINE_LANGUAGES: Record<string, readonly LanguageCode[]> = {
  qwen: ['zh', 'en', 'ja', 'ko', 'de', 'fr', 'ru', 'pt', 'es', 'it'],
  luxtts: ['en'],
  chatterbox: [
    'ar',
    'da',
    'de',
    'el',
    'en',
    'es',
    'fi',
    'fr',
    'he',
    'hi',
    'it',
    'ja',
    'ko',
    'ms',
    'nl',
    'no',
    'pl',
    'pt',
    'ru',
    'sv',
    'sw',
    'tr',
    'zh',
  ],
  chatterbox_turbo: ['en'],
  tada: ['en', 'ar', 'zh', 'de', 'es', 'fr', 'it', 'ja', 'pl', 'pt'],
  kokoro: ['en', 'es', 'fr', 'hi', 'it', 'pt', 'ja', 'zh'],
  qwen_custom_voice: ['zh', 'en', 'ja', 'ko', 'de', 'fr', 'ru', 'pt', 'es', 'it'],
  supertonic: [
    'en', 'ko', 'ja', 'ar', 'bg', 'cs', 'da', 'de', 'el', 'es',
    'et', 'fi', 'fr', 'hi', 'hr', 'hu', 'id', 'it', 'lt', 'lv',
    'nl', 'pl', 'pt', 'ro', 'ru', 'sk', 'sl', 'sv', 'tr', 'uk', 'vi',
  ],
} as const;

/** Helper: get language options for a given engine. */
export function getLanguageOptionsForEngine(engine: string) {
  const codes = ENGINE_LANGUAGES[engine] ?? ENGINE_LANGUAGES.qwen;
  return codes.map((code) => ({
    value: code,
    label: ALL_LANGUAGES[code],
  }));
}

// ── Backwards-compatible exports used elsewhere ──────────────────────
export const SUPPORTED_LANGUAGES = ALL_LANGUAGES;
export const LANGUAGE_CODES = Object.keys(ALL_LANGUAGES) as LanguageCode[];
export const LANGUAGE_OPTIONS = LANGUAGE_CODES.map((code) => ({
  value: code,
  label: ALL_LANGUAGES[code],
}));
