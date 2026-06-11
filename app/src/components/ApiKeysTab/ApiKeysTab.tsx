import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { CheckCircle2, KeyRound, Loader2, Trash2, Volume2, XCircle } from 'lucide-react';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { useToast } from '@/components/ui/use-toast';
import { apiClient } from '@/lib/api/client';
import type { ProviderStatus } from '@/lib/api/types';

function ProviderCard({ provider }: { provider: ProviderStatus }) {
  const { t } = useTranslation();
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [keyInput, setKeyInput] = useState('');

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['providers'] });

  const saveMutation = useMutation({
    mutationFn: (apiKey: string) => apiClient.setProviderKey(provider.provider, apiKey),
    onSuccess: () => {
      setKeyInput('');
      toast({ title: t('apiKeys.toast.saved', { name: provider.name }) });
      invalidate();
    },
    onError: (e: Error) => {
      toast({
        title: t('apiKeys.toast.invalid'),
        description: e.message,
        variant: 'destructive',
      });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: () => apiClient.deleteProviderKey(provider.provider),
    onSuccess: () => {
      setTestResult(null);
      toast({ title: t('apiKeys.toast.removed', { name: provider.name }) });
      invalidate();
    },
    onError: (e: Error) => {
      toast({ title: t('common.error'), description: e.message, variant: 'destructive' });
    },
  });

  const [testResult, setTestResult] = useState<{ ok: boolean; detail?: string | null } | null>(
    null,
  );

  const testMutation = useMutation({
    mutationFn: () => apiClient.testProviderGeneration(provider.provider),
    onSuccess: (res) => {
      setTestResult({ ok: res.valid, detail: res.detail });
      toast({
        title: res.valid ? t('apiKeys.toast.testOk') : t('apiKeys.toast.testFailed'),
        description: res.detail ?? undefined,
        variant: res.valid ? undefined : 'destructive',
      });
    },
    onError: (e: Error) => {
      setTestResult({ ok: false, detail: e.message });
      toast({ title: t('apiKeys.toast.testFailed'), description: e.message, variant: 'destructive' });
    },
  });

  const ttsModels = provider.models.filter((m) => m.kind === 'tts');
  const sttModels = provider.models.filter((m) => m.kind === 'stt');

  return (
    <div className="border rounded-lg p-4 space-y-4">
      {/* Header row */}
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold">{provider.name}</span>
          {provider.configured ? (
            <Badge className="text-[10px] h-5 bg-emerald-500/15 text-emerald-600 border-emerald-500/30 hover:bg-emerald-500/15">
              <CheckCircle2 className="h-3 w-3 mr-1" />
              {t('apiKeys.connected')}
            </Badge>
          ) : (
            <Badge variant="outline" className="text-[10px] h-5 text-muted-foreground">
              {t('apiKeys.notConfigured')}
            </Badge>
          )}
        </div>
        {provider.configured && (
          <span className="text-xs font-mono text-muted-foreground/70">{provider.masked_key}</span>
        )}
      </div>

      {/* Key entry */}
      <div className="flex items-center gap-2">
        <Input
          type="password"
          autoComplete="off"
          placeholder={
            provider.configured
              ? t('apiKeys.replacePlaceholder')
              : t('apiKeys.keyPlaceholder', { name: provider.name })
          }
          value={keyInput}
          onChange={(e) => setKeyInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && keyInput.trim()) saveMutation.mutate(keyInput.trim());
          }}
          className="h-9 text-sm font-mono"
        />
        <Button
          size="sm"
          onClick={() => saveMutation.mutate(keyInput.trim())}
          disabled={!keyInput.trim() || saveMutation.isPending}
        >
          {saveMutation.isPending ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            t('apiKeys.save')
          )}
        </Button>
        {provider.configured && (
          <>
            <Button
              size="sm"
              variant="outline"
              onClick={() => testMutation.mutate()}
              disabled={testMutation.isPending}
              title={t('apiKeys.testHint')}
            >
              {testMutation.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Volume2 className="h-4 w-4 mr-1" />
              )}
              {t('apiKeys.test')}
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={() => deleteMutation.mutate()}
              disabled={deleteMutation.isPending}
              title={t('apiKeys.remove')}
            >
              {deleteMutation.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Trash2 className="h-4 w-4" />
              )}
            </Button>
          </>
        )}
      </div>

      {/* Test result */}
      {testResult && (
        <div
          className={
            testResult.ok
              ? 'flex items-start gap-2 text-xs text-emerald-600'
              : 'flex items-start gap-2 text-xs text-destructive'
          }
        >
          {testResult.ok ? (
            <CheckCircle2 className="h-3.5 w-3.5 mt-0.5 shrink-0" />
          ) : (
            <XCircle className="h-3.5 w-3.5 mt-0.5 shrink-0" />
          )}
          <span className="break-all">
            {testResult.detail ??
              (testResult.ok ? t('apiKeys.toast.testOk') : t('apiKeys.toast.testFailed'))}
          </span>
        </div>
      )}

      {/* Models unlocked by this provider */}
      <div className="space-y-1.5">
        <p className="text-xs text-muted-foreground">
          {t('apiKeys.modelsHint')}
        </p>
        <div className="flex flex-wrap gap-1.5">
          {ttsModels.map((m) => (
            <code
              key={m.id}
              className="text-[11px] bg-muted/60 rounded px-1.5 py-0.5 font-mono"
              title={`${m.label} · TTS`}
            >
              {m.id}
            </code>
          ))}
          {sttModels.map((m) => (
            <code
              key={m.id}
              className="text-[11px] bg-muted/60 rounded px-1.5 py-0.5 font-mono"
              title={`${m.label} · STT`}
            >
              {m.id}
            </code>
          ))}
        </div>
      </div>
    </div>
  );
}

export function ApiKeysTab() {
  const { t } = useTranslation();
  const { data, isLoading } = useQuery({
    queryKey: ['providers'],
    queryFn: () => apiClient.listProviders(),
  });

  return (
    <div className="h-full flex flex-col">
      {/* Header */}
      <div className="shrink-0 pb-4">
        <h1 className="text-lg font-semibold flex items-center gap-2">
          <KeyRound className="h-4 w-4" />
          {t('apiKeys.title')}
        </h1>
        <p className="text-sm text-muted-foreground">{t('apiKeys.subtitle')}</p>
      </div>

      {isLoading ? (
        <div className="flex items-center justify-center py-16">
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
        </div>
      ) : (
        <div className="flex-1 min-h-0 overflow-y-auto space-y-4 pb-6">
          {data?.providers.map((p) => (
            <ProviderCard key={p.provider} provider={p} />
          ))}
        </div>
      )}
    </div>
  );
}
