import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { useGenerationFormContext } from '@/components/Generation/GenerationFormContext';
import { EngineModelSelector } from '@/components/Generation/EngineModelSelector';
import { apiClient } from '@/lib/api/client';
import { ALL_LANGUAGES } from '@/lib/constants/languages';
import {
  getLanguageOptionsForEngineFromCatalog,
  useEngineMetadata,
} from '@/lib/hooks/useEngineCatalog';
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
  const setSelectedProfileId = useUIStore((s) => s.setSelectedProfileId);
  const selectedProfileId = useUIStore((s) => s.selectedProfileId);

  const engine = ctx?.form.watch('engine') ?? 'qwen';

  const { data: presetsData } = useQuery({
    queryKey: ['presetVoices', engine],
    queryFn: () => apiClient.listPresetVoices(engine),
    enabled: !!engine && !!ctx,
  });

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

        {/* Voice / Profile */}
        {presetsData && presetsData.voices.length > 0 && (
          <section className="flex flex-col gap-2">
            <span className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground/60">
              {t('params.voice', 'Voice')}
            </span>
            <Select
              value={selectedProfileId ?? ''}
              onValueChange={(v) => setSelectedProfileId(v || null)}
            >
              <SelectTrigger className="h-8 text-xs bg-card border-border rounded-lg">
                <SelectValue placeholder={t('params.voicePlaceholder', 'Select voice')} />
              </SelectTrigger>
              <SelectContent>
                {presetsData.voices.map((v) => (
                  <SelectItem key={v.id} value={v.id} className="text-xs">
                    {v.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </section>
        )}

        {/* Language */}
        <section className="flex flex-col gap-2">
          <span className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground/60">
            {t('params.language', 'Language')}
          </span>
          <Select
            value={form.watch('language')}
            onValueChange={(v) =>
              form.setValue('language', v as ReturnType<typeof form.watch<'language'>>)
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
