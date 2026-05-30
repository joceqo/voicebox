import { Check, Copy, FileAudio, Loader2, Mic, Square, Upload, X } from 'lucide-react';
import { useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { useToast } from '@/components/ui/use-toast';
import type { SttModel } from '@/lib/api/types';
import { ALL_LANGUAGES, type LanguageCode } from '@/lib/constants/languages';
import { useCaptureSettings } from '@/lib/hooks/useSettings';
import { useTranscription } from '@/lib/hooks/useTranscription';
import { cn } from '@/lib/utils/cn';

// Parakeet (ONNX, CPU, light, 25 European langs, auto-detect) is the default;
// Whisper sizes stay available for languages Parakeet doesn't cover (e.g. CJK).
const STT_MODELS: { value: SttModel; label: string }[] = [
  { value: 'parakeet-v3', label: 'Parakeet (fast)' },
  { value: 'turbo', label: 'Whisper Turbo' },
  { value: 'large', label: 'Whisper Large' },
  { value: 'medium', label: 'Whisper Medium' },
  { value: 'small', label: 'Whisper Small' },
  { value: 'base', label: 'Whisper Base' },
];

const AUTO_LANGUAGE = 'auto';

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/**
 * Speech-to-text playground. Drop or record an audio clip, pick a Whisper
 * model + language (or auto-detect), and transcribe. Mirrors the TTS layout:
 * a composer card on top, the transcript below.
 */
export function SttTab() {
  const { t } = useTranslation();
  const { toast } = useToast();
  const { settings } = useCaptureSettings();
  const transcribe = useTranscription();

  const [file, setFile] = useState<File | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const [model, setModel] = useState<SttModel | null>(null);
  const [language, setLanguage] = useState<string>(AUTO_LANGUAGE);
  const [transcript, setTranscript] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const [isRecording, setIsRecording] = useState(false);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Default to the server's configured STT model once it loads (Parakeet by default).
  const effectiveModel = model ?? settings?.stt_model ?? 'parakeet-v3';

  const acceptFile = (f: File) => {
    setFile(f);
    setTranscript(null);
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
    const dropped = e.dataTransfer.files?.[0];
    if (dropped) acceptFile(dropped);
  };

  const handleBrowse = (e: React.ChangeEvent<HTMLInputElement>) => {
    const picked = e.target.files?.[0];
    if (picked) acceptFile(picked);
  };

  const startRecording = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const recorder = new MediaRecorder(stream);
      chunksRef.current = [];
      recorder.ondataavailable = (ev) => {
        if (ev.data.size > 0) chunksRef.current.push(ev.data);
      };
      recorder.onstop = () => {
        const blob = new Blob(chunksRef.current, { type: recorder.mimeType || 'audio/webm' });
        const ext = blob.type.includes('ogg') ? 'ogg' : 'webm';
        acceptFile(new File([blob], `recording.${ext}`, { type: blob.type }));
        for (const track of stream.getTracks()) track.stop();
      };
      recorder.start();
      mediaRecorderRef.current = recorder;
      setIsRecording(true);
    } catch (err) {
      toast({
        title: t('stt.micErrorTitle', 'Microphone unavailable'),
        description: err instanceof Error ? err.message : t('stt.micErrorBody', 'Could not access the microphone.'),
        variant: 'destructive',
      });
    }
  };

  const stopRecording = () => {
    mediaRecorderRef.current?.stop();
    mediaRecorderRef.current = null;
    setIsRecording(false);
  };

  const handleTranscribe = () => {
    if (!file) return;
    transcribe.mutate(
      {
        file,
        language: language === AUTO_LANGUAGE ? undefined : (language as LanguageCode),
        model: effectiveModel,
      },
      {
        onSuccess: (res) => setTranscript(res.text),
        onError: (error) => {
          toast({
            title: t('stt.failedTitle', 'Transcription failed'),
            description: error instanceof Error ? error.message : t('stt.failedBody', 'Unknown error'),
            variant: 'destructive',
          });
        },
      },
    );
  };

  const handleCopy = async () => {
    if (!transcript) return;
    await navigator.clipboard.writeText(transcript);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  const clearFile = () => {
    setFile(null);
    setTranscript(null);
    if (fileInputRef.current) fileInputRef.current.value = '';
  };

  return (
    <div className="flex flex-col h-full min-h-0 overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between pt-4 pb-3 px-1 shrink-0">
        <h2 className="text-sm font-semibold uppercase tracking-widest text-muted-foreground/60">
          {t('stt.title', 'Transcription')}
        </h2>
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto flex flex-col gap-4 pb-8">
        {/* Source card */}
        <div className="rounded-xl border border-border bg-card p-4 flex flex-col gap-4">
          {!file ? (
            // biome-ignore lint/a11y/useSemanticElements: dropzone contains a nested Record button + file input, so it can't be a real <button>
            <div
              role="button"
              tabIndex={0}
              onClick={() => fileInputRef.current?.click()}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault();
                  fileInputRef.current?.click();
                }
              }}
              onDragOver={(e) => {
                e.preventDefault();
                setIsDragging(true);
              }}
              onDragLeave={() => setIsDragging(false)}
              onDrop={handleDrop}
              className={cn(
                'flex flex-col items-center justify-center gap-3 rounded-lg border-2 border-dashed py-12 px-6 text-center transition-colors cursor-pointer',
                isDragging
                  ? 'border-accent bg-accent/5'
                  : 'border-border hover:border-muted-foreground/40',
              )}
            >
              <Upload className="h-8 w-8 text-muted-foreground/50" />
              <div className="text-sm text-muted-foreground">
                {t('stt.dropHint', 'Drag & drop an audio file, or click to browse')}
              </div>
              <div className="flex items-center gap-2 text-xs text-muted-foreground/50">
                <span className="h-px w-8 bg-border" />
                {t('common.or', 'or')}
                <span className="h-px w-8 bg-border" />
              </div>
              <Button
                variant={isRecording ? 'destructive' : 'outline'}
                size="sm"
                onClick={(e) => {
                  e.stopPropagation();
                  isRecording ? stopRecording() : startRecording();
                }}
              >
                {isRecording ? (
                  <>
                    <Square className="mr-2 h-3.5 w-3.5" />
                    {t('stt.stopRecording', 'Stop recording')}
                  </>
                ) : (
                  <>
                    <Mic className="mr-2 h-3.5 w-3.5" />
                    {t('stt.record', 'Record')}
                  </>
                )}
              </Button>
              <input
                ref={fileInputRef}
                type="file"
                accept="audio/*"
                onChange={handleBrowse}
                className="hidden"
              />
            </div>
          ) : (
            <div className="flex items-center gap-3 rounded-lg border border-border bg-background px-3 py-2.5">
              <FileAudio className="h-5 w-5 text-accent shrink-0" />
              <div className="min-w-0 flex-1">
                <div className="text-sm font-medium truncate">{file.name}</div>
                <div className="text-xs text-muted-foreground">{formatBytes(file.size)}</div>
              </div>
              <Button
                variant="ghost"
                size="icon"
                className="h-7 w-7 shrink-0"
                onClick={clearFile}
                aria-label={t('common.remove', 'Remove')}
              >
                <X className="h-4 w-4" />
              </Button>
            </div>
          )}

          {/* Controls */}
          <div className="flex flex-wrap items-end gap-3">
            <div className="flex flex-col gap-1.5">
              <span className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground/60">
                {t('stt.model', 'Model')}
              </span>
              <Select value={effectiveModel} onValueChange={(v) => setModel(v as SttModel)}>
                <SelectTrigger className="h-8 w-40 text-xs bg-background border-border rounded-lg">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {STT_MODELS.map((m) => (
                    <SelectItem key={m.value} value={m.value} className="text-xs">
                      {m.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="flex flex-col gap-1.5">
              <span className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground/60">
                {t('stt.language', 'Language')}
              </span>
              <Select value={language} onValueChange={setLanguage}>
                <SelectTrigger className="h-8 w-40 text-xs bg-background border-border rounded-lg">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={AUTO_LANGUAGE} className="text-xs">
                    {t('stt.autoDetect', 'Auto-detect')}
                  </SelectItem>
                  {Object.entries(ALL_LANGUAGES).map(([code, label]) => (
                    <SelectItem key={code} value={code} className="text-xs">
                      {label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <Button
              className="ml-auto"
              onClick={handleTranscribe}
              disabled={!file || transcribe.isPending || isRecording}
            >
              {transcribe.isPending ? (
                <>
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  {t('stt.transcribing', 'Transcribing…')}
                </>
              ) : (
                t('stt.transcribe', 'Transcribe')
              )}
            </Button>
          </div>
        </div>

        {/* Transcript card */}
        {transcript !== null && (
          <div className="rounded-xl border border-border bg-card p-4 flex flex-col gap-3">
            <div className="flex items-center justify-between">
              <span className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground/60">
                {t('stt.transcript', 'Transcript')}
              </span>
              <Button variant="ghost" size="sm" className="h-7 text-xs" onClick={handleCopy}>
                {copied ? (
                  <>
                    <Check className="mr-1.5 h-3.5 w-3.5" />
                    {t('common.copied', 'Copied')}
                  </>
                ) : (
                  <>
                    <Copy className="mr-1.5 h-3.5 w-3.5" />
                    {t('common.copy', 'Copy')}
                  </>
                )}
              </Button>
            </div>
            <p className="text-sm leading-relaxed whitespace-pre-wrap select-text">
              {transcript || t('stt.emptyResult', '(no speech detected)')}
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
