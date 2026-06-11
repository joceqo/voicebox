import { useEffect, useMemo } from 'react';
import type { UseFormReturn } from 'react-hook-form';
import { FormControl } from '@/components/ui/form';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import type { TTSEngineInfo, VoiceProfileResponse } from '@/lib/api/types';
import { ALL_LANGUAGES } from '@/lib/constants/languages';
import {
  type EngineMetadata,
  getLanguageOptionsForEngineFromCatalog,
  useEngineMetadata,
} from '@/lib/hooks/useEngineCatalog';
import type { GenerationFormValues } from '@/lib/hooks/useGenerationForm';

/**
 * Engine picker options derive from the server's /engines catalog at
 * runtime. Adding a new backend on the server appears here automatically
 * — no edit needed in this file.
 */
interface EngineOption {
  value: string; // either "<engine>" (single-model) or "<engine>:<modelSize>"
  label: string;
  engine: string;
}

function buildEngineOptions(ttsEngines: TTSEngineInfo[]): EngineOption[] {
  return ttsEngines.flatMap((e) => {
    if (e.models.length > 1) {
      return e.models.map((m) => ({
        value: `${e.engine}:${m.model_size}`,
        label: m.display_name,
        engine: e.engine,
      }));
    }
    return [{ value: e.engine, label: e.display_name, engine: e.engine }];
  });
}

function getSelectValue(
  engine: string,
  modelSize: string | undefined,
  ttsByEngine: Map<string, TTSEngineInfo>,
): string {
  const info = ttsByEngine.get(engine);
  if (!info) return engine;
  if (info.models.length > 1) {
    const size = modelSize ?? info.models[0]?.model_size;
    return `${engine}:${size}`;
  }
  return engine;
}

export function applyEngineSelection(
  form: UseFormReturn<GenerationFormValues>,
  value: string,
  metadata: EngineMetadata,
) {
  const [engineRaw, modelSize] = value.includes(':') ? value.split(':') : [value, undefined];
  const engine = engineRaw;
  const info = metadata.ttsByEngine.get(engine);

  form.setValue('engine', engine as GenerationFormValues['engine']);

  if (info && info.models.length > 1 && modelSize) {
    form.setValue('modelSize', modelSize as GenerationFormValues['modelSize']);
  } else {
    form.setValue('modelSize', undefined as GenerationFormValues['modelSize']);
  }

  if (info?.english_only) {
    form.setValue('language', 'en');
    return;
  }
  const currentLang = form.getValues('language');
  const available = getLanguageOptionsForEngineFromCatalog(metadata, engine, ALL_LANGUAGES);
  if (!available.some((l) => l.value === currentLang)) {
    form.setValue('language', (available[0]?.value ?? 'en') as GenerationFormValues['language']);
  }
}

interface EngineModelSelectorProps {
  form: UseFormReturn<GenerationFormValues>;
  compact?: boolean;
  selectedProfile?: VoiceProfileResponse | null;
}

export function EngineModelSelector({ form, compact, selectedProfile }: EngineModelSelectorProps) {
  const metadata = useEngineMetadata();
  const { ttsEngines, ttsByEngine } = metadata;

  const engineOptions = useMemo(() => buildEngineOptions(ttsEngines), [ttsEngines]);

  const engine = form.watch('engine') || 'supertonic';
  const modelSize = form.watch('modelSize');
  const selectValue = getSelectValue(engine, modelSize, ttsByEngine);

  const availableOptions = useMemo(() => {
    if (!selectedProfile) return engineOptions;
    return engineOptions.filter((opt) => isProfileCompatibleWithEngine(selectedProfile, opt.engine, metadata));
  }, [engineOptions, selectedProfile, metadata]);

  const currentEngineAvailable = availableOptions.some((opt) => opt.value === selectValue);

  useEffect(() => {
    if (!currentEngineAvailable && availableOptions.length > 0) {
      applyEngineSelection(form, availableOptions[0].value, metadata);
    }
  }, [availableOptions, currentEngineAvailable, form, metadata]);

  const itemClass = compact ? 'text-xs text-muted-foreground' : undefined;
  const triggerClass = compact
    ? 'h-8 text-xs bg-card border-border rounded-full hover:bg-background/50 transition-all'
    : undefined;

  return (
    <Select value={selectValue} onValueChange={(v) => applyEngineSelection(form, v, metadata)}>
      <FormControl>
        <SelectTrigger className={triggerClass}>
          <SelectValue />
        </SelectTrigger>
      </FormControl>
      <SelectContent>
        {availableOptions.map((opt) => (
          <SelectItem key={opt.value} value={opt.value} className={itemClass}>
            {opt.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

/** Human-readable description for the currently selected engine — pulled from the catalog. */
export function useEngineDescription(engine: string): string {
  const { ttsByEngine } = useEngineMetadata();
  return ttsByEngine.get(engine)?.description ?? '';
}

/**
 * Profile compatibility check. Preset profiles bind to one engine; cloned
 * profiles only work with engines that support audio cloning.
 */
export function isProfileCompatibleWithEngine(
  profile: VoiceProfileResponse,
  engine: string,
  metadata: EngineMetadata,
): boolean {
  const voiceType = profile.voice_type || 'cloned';
  if (voiceType === 'preset') return profile.preset_engine === engine;
  if (voiceType === 'cloned') return metadata.cloningEngines.has(engine);
  return true; // designed — future
}
