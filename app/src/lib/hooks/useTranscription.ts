import { useMutation } from '@tanstack/react-query';
import { apiClient } from '@/lib/api/client';
import type { SttModel } from '@/lib/api/types';
import type { LanguageCode } from '@/lib/constants/languages';

export function useTranscription() {
  return useMutation({
    mutationFn: ({
      file,
      language,
      model,
    }: {
      file: File;
      language?: LanguageCode;
      model?: SttModel;
    }) => apiClient.transcribeAudio(file, language, model),
  });
}
