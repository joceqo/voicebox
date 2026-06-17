import { Zap } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useGenerationFormContext } from '@/components/Generation/GenerationFormContext';
import { EngineModelSelector } from '@/components/Generation/EngineModelSelector';
import { ProfileList } from '@/components/VoiceProfiles/ProfileList';
import { ALL_LANGUAGES } from '@/lib/constants/languages';
import {
  getLanguageOptionsForEngineFromCatalog,
  useEngineMetadata,
} from '@/lib/hooks/useEngineCatalog';
import type { GenerationFormValues } from '@/lib/hooks/useGenerationForm';
import { cn } from '@/lib/utils/cn';
import { useUIStore } from '@/stores/uiStore';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';

export function ParamsPanel() {
  const ctx = useGenerationFormContext();

  // All hooks must be called unconditionally (rules of hooks).
  const { t } = useTranslation();
  const engineMetadata = useEngineMetadata();
  const streamingPreview = useUIStore((s) => s.streamingPreview);
  const setStreamingPreview = useUIStore((s) => s.setStreamingPreview);

  const engine = ctx?.form.watch('engine') ?? 'supertonic';

  const languageOptions = getLanguageOptionsForEngineFromCatalog(
    engineMetadata,
    engine,
    ALL_LANGUAGES,
  );

  // If no context (not on index route / provider), render nothing
  if (!ctx) return null;

  const { form } = ctx;

  return (
    <div
      className="fixed right-0 top-0 h-full bg-sidebar border-l border-border flex flex-col overflow-hidden"
      style={{ width: 'var(--params-w, 260px)' }}
    >
      {/* Header */}
      <div className="px-4 pt-14 pb-3 border-b border-border/50">
        <h2 className="text-xs font-semibold uppercase tracking-widest text-muted-foreground/60">
          {t('params.title', 'Parameters')}
        </h2>
      </div>

      <div className="flex-1 overflow-y-auto px-4 py-4 flex flex-col gap-5">
        {/* Engine / Model */}
        <section className="flex flex-col gap-2">
          <span className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground/60">
            {t('params.model', 'Model')}
          </span>
          <EngineModelSelector form={form} compact />
        </section>

        {/* Streaming preview toggle — kept near the top so it's visible without
            scrolling past the (long) voice list. */}
        <section className="flex items-center justify-between gap-2 rounded-lg bg-card/50 border border-border/60 px-3 py-2">
          <div className="flex flex-col">
            <span className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground/70 flex items-center gap-1">
              <Zap className="h-3 w-3 text-accent" />
              {t('params.streaming', 'Streaming')}
            </span>
            <span className="text-[10px] text-muted-foreground/40">
              {t('params.streamingHint', 'Low-latency preview · not saved')}
            </span>
          </div>
          <button
            type="button"
            role="switch"
            aria-checked={streamingPreview}
            onClick={() => setStreamingPreview(!streamingPreview)}
            className={cn(
              'relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors',
              streamingPreview ? 'bg-accent' : 'bg-muted',
            )}
          >
            <span
              className={cn(
                'inline-block h-4 w-4 rounded-full bg-background shadow-sm transition-transform',
                streamingPreview ? 'translate-x-[18px]' : 'translate-x-0.5',
              )}
            />
          </button>
        </section>

        {/* Voice / Profile */}
        <section className="flex flex-col gap-2">
          <span className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground/60">
            {t('params.voice', 'Voice')}
          </span>
          <ProfileList compact />
        </section>

        {/* Language */}
        <section className="flex flex-col gap-2">
          <span className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground/60">
            {t('params.language', 'Language')}
          </span>
          <Select
            value={form.watch('language')}
            onValueChange={(v) =>
              form.setValue('language', v as GenerationFormValues['language'])
            }
          >
            <SelectTrigger className="h-8 text-xs bg-card border-border rounded-lg">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {languageOptions.map((lang) => (
                <SelectItem key={lang.value} value={lang.value} className="text-xs">
                  {lang.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </section>

        {/* Divider */}
        <div className="border-t border-border/40" />

        {/* Coming-soon fields */}
        <section className="flex flex-col gap-3 opacity-40 pointer-events-none select-none">
          <ComingSoonField label={t('params.speed', 'Speed')} />
          <ComingSoonField label={t('params.format', 'Format')} />
          <ComingSoonField label={t('params.temperature', 'Temperature')} />
        </section>
      </div>
    </div>
  );
}

function ComingSoonField({ label }: { label: string }) {
  const { t } = useTranslation();
  return (
    <div className="flex flex-col gap-1.5">
      <span className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground/60">
        {label}
      </span>
      <div className="h-8 rounded-lg border border-border bg-card flex items-center px-3 text-xs text-muted-foreground/40">
        {t('common.comingSoon', 'Coming soon')}
      </div>
    </div>
  );
}
