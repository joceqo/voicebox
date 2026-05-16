import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { apiClient } from '@/lib/api/client';
import type {
  EngineCatalogResponse,
  LLMEngineInfo,
  TTSEngineInfo,
} from '@/lib/api/types';

/**
 * Fetch the server-side engine catalog (GET /engines). All UI engine
 * metadata — picker options, supported languages, preset/cloning flags,
 * display badges — derives from this single response. Adding a new
 * backend on the server is reflected in the UI without any TS changes.
 *
 * Cached for the lifetime of the app (engine list doesn't change at
 * runtime). React-query refetches on window focus by default so a hot
 * server restart with a new engine list propagates quickly enough.
 */
export function useEngineCatalog() {
  return useQuery<EngineCatalogResponse>({
    queryKey: ['engines'],
    queryFn: () => apiClient.listEngines(),
    staleTime: 5 * 60 * 1000,
  });
}

export interface EngineMetadata {
  /** All TTS engines, in registry order. */
  ttsEngines: TTSEngineInfo[];
  /** All LLM engines, in registry order. */
  llmEngines: LLMEngineInfo[];
  /** Map engine id → metadata for O(1) lookup. */
  ttsByEngine: Map<string, TTSEngineInfo>;
  llmByEngine: Map<string, LLMEngineInfo>;
  /** Engines that ship preset voice catalogs (no audio cloning). */
  presetEngines: Set<string>;
  /** Engines that accept a user reference audio for cloning. */
  cloningEngines: Set<string>;
  /** Engines whose language is forced to en. */
  englishOnlyEngines: Set<string>;
  isLoading: boolean;
}

/**
 * Higher-level view over useEngineCatalog. Pre-computes the Sets and Maps
 * that almost every UI consumer needs, so individual components don't have
 * to re-derive them.
 */
export function useEngineMetadata(): EngineMetadata {
  const { data, isLoading } = useEngineCatalog();

  return useMemo<EngineMetadata>(() => {
    const ttsEngines = data?.tts ?? [];
    const llmEngines = data?.llm ?? [];

    const ttsByEngine = new Map<string, TTSEngineInfo>();
    const presetEngines = new Set<string>();
    const cloningEngines = new Set<string>();
    const englishOnlyEngines = new Set<string>();

    for (const e of ttsEngines) {
      ttsByEngine.set(e.engine, e);
      if (e.voice_mode === 'preset') presetEngines.add(e.engine);
      if (e.voice_mode === 'cloning') cloningEngines.add(e.engine);
      if (e.english_only) englishOnlyEngines.add(e.engine);
    }

    const llmByEngine = new Map<string, LLMEngineInfo>();
    for (const e of llmEngines) {
      llmByEngine.set(e.engine, e);
    }

    return {
      ttsEngines,
      llmEngines,
      ttsByEngine,
      llmByEngine,
      presetEngines,
      cloningEngines,
      englishOnlyEngines,
      isLoading,
    };
  }, [data, isLoading]);
}

/**
 * Helper: resolve the model_name + display_name to surface for a given
 * engine + (optional) model_size. Replaces the modelName/displayName
 * ternary chains that used to live in useGenerationForm.
 */
export function resolveModelNameForEngine(
  catalog: EngineMetadata,
  engine: string,
  modelSize?: string,
): { modelName: string; displayName: string } | null {
  const info = catalog.ttsByEngine.get(engine);
  if (!info) return null;

  const models = info.models;
  if (models.length === 0) return null;

  if (modelSize) {
    const match = models.find((m) => m.model_size === modelSize);
    if (match) return { modelName: match.model_name, displayName: match.display_name };
  }
  return { modelName: models[0].model_name, displayName: models[0].display_name };
}

/**
 * Helper: language options to surface in the language picker for a given
 * engine. Replaces ENGINE_LANGUAGES in lib/constants/languages.ts.
 */
export function getLanguageOptionsForEngineFromCatalog(
  catalog: EngineMetadata,
  engine: string,
  allLanguages: Record<string, string>,
): Array<{ value: string; label: string }> {
  const info = catalog.ttsByEngine.get(engine);
  const codes = info?.languages ?? ['en'];
  return codes
    .filter((code) => code in allLanguages)
    .map((code) => ({ value: code, label: allLanguages[code] }));
}
