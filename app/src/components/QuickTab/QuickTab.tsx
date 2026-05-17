import { useMutation, useQuery } from '@tanstack/react-query';
import { Loader2, Zap } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { Button } from '@/components/ui/button';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import { useToast } from '@/components/ui/use-toast';
import { apiClient } from '@/lib/api/client';
import { ALL_LANGUAGES } from '@/lib/constants/languages';
import { useEngineMetadata } from '@/lib/hooks/useEngineCatalog';

/**
 * Quick generate — preset-voice TTS without creating a profile. Pick an
 * engine, a built-in voice, a language, type some text, hit Generate.
 * Audio plays in-place; nothing is persisted (no DB row, no profile).
 */
export function QuickTab() {
  const { toast } = useToast();
  const { ttsEngines } = useEngineMetadata();

  const presetEngines = useMemo(
    () => ttsEngines.filter((e) => e.voice_mode === 'preset'),
    [ttsEngines],
  );

  const [engine, setEngine] = useState<string>('');
  const [voiceId, setVoiceId] = useState<string>('');
  const [language, setLanguage] = useState<string>('en');
  const [text, setText] = useState<string>('');
  const [audioUrl, setAudioUrl] = useState<string | null>(null);

  // Default the engine picker to the first preset engine once the catalog loads.
  useEffect(() => {
    if (!engine && presetEngines.length > 0) {
      setEngine(presetEngines[0].engine);
    }
  }, [engine, presetEngines]);

  // Fetch the preset voices for the selected engine.
  const { data: voicesData } = useQuery({
    queryKey: ['preset-voices', engine],
    queryFn: () => apiClient.listPresetVoices(engine),
    enabled: !!engine,
  });
  const voices = voicesData?.voices ?? [];

  // Reset voice + language when the engine changes.
  useEffect(() => {
    if (voices.length > 0 && !voices.some((v) => v.voice_id === voiceId)) {
      setVoiceId(voices[0].voice_id);
    }
  }, [voices, voiceId]);

  const engineInfo = ttsEngines.find((e) => e.engine === engine);
  const supportedLanguages = engineInfo?.languages ?? ['en'];

  useEffect(() => {
    if (engineInfo?.english_only) {
      setLanguage('en');
      return;
    }
    if (!supportedLanguages.includes(language)) {
      setLanguage(supportedLanguages[0] ?? 'en');
    }
  }, [engineInfo, supportedLanguages, language]);

  const generation = useMutation({
    mutationFn: () =>
      apiClient.quickGenerate({
        text,
        engine,
        voice_id: voiceId,
        language,
      }),
    onSuccess: (blob) => {
      const url = URL.createObjectURL(blob);
      setAudioUrl((prev) => {
        if (prev) URL.revokeObjectURL(prev);
        return url;
      });
    },
    onError: (err: Error) => {
      toast({
        title: 'Generation failed',
        description: err.message,
        variant: 'destructive',
      });
    },
  });

  // Release any blob URL on unmount.
  useEffect(() => {
    return () => {
      if (audioUrl) URL.revokeObjectURL(audioUrl);
    };
  }, [audioUrl]);

  const canGenerate = !!engine && !!voiceId && text.trim().length > 0 && !generation.isPending;

  return (
    <div className="flex flex-col gap-6 py-6 max-w-3xl mx-auto w-full">
      <div className="flex items-center gap-3">
        <Zap className="h-6 w-6 text-accent" />
        <h1 className="text-2xl font-semibold">Quick generate</h1>
      </div>
      <p className="text-sm text-muted-foreground -mt-3">
        Pick a built-in voice and synthesize without creating a profile. Nothing is saved.
      </p>

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        <div className="space-y-1.5">
          <label className="text-xs font-medium text-muted-foreground">Engine</label>
          <Select value={engine} onValueChange={setEngine}>
            <SelectTrigger>
              <SelectValue placeholder="Engine" />
            </SelectTrigger>
            <SelectContent>
              {presetEngines.map((e) => (
                <SelectItem key={e.engine} value={e.engine}>
                  {e.display_name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="space-y-1.5">
          <label className="text-xs font-medium text-muted-foreground">Voice</label>
          <Select value={voiceId} onValueChange={setVoiceId} disabled={voices.length === 0}>
            <SelectTrigger>
              <SelectValue placeholder="Voice" />
            </SelectTrigger>
            <SelectContent>
              {voices.map((v) => (
                <SelectItem key={v.voice_id} value={v.voice_id}>
                  {v.name} ({v.gender})
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="space-y-1.5">
          <label className="text-xs font-medium text-muted-foreground">Language</label>
          <Select
            value={language}
            onValueChange={setLanguage}
            disabled={engineInfo?.english_only}
          >
            <SelectTrigger>
              <SelectValue placeholder="Language" />
            </SelectTrigger>
            <SelectContent>
              {supportedLanguages
                .filter((code) => code in ALL_LANGUAGES)
                .map((code) => (
                  <SelectItem key={code} value={code}>
                    {ALL_LANGUAGES[code as keyof typeof ALL_LANGUAGES]}
                  </SelectItem>
                ))}
            </SelectContent>
          </Select>
        </div>
      </div>

      <Textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="Type the text you want to hear…"
        rows={6}
        className="resize-none"
      />

      <div className="flex items-center gap-3">
        <Button onClick={() => generation.mutate()} disabled={!canGenerate} className="gap-2">
          {generation.isPending ? (
            <>
              <Loader2 className="h-4 w-4 animate-spin" />
              Generating…
            </>
          ) : (
            <>
              <Zap className="h-4 w-4" />
              Generate
            </>
          )}
        </Button>
        {engineInfo?.description && (
          <span className="text-xs text-muted-foreground">{engineInfo.description}</span>
        )}
      </div>

      {audioUrl && (
        <div className="mt-2">
          <audio src={audioUrl} controls autoPlay className="w-full" />
        </div>
      )}
    </div>
  );
}
