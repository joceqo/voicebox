import { createContext, useContext, type ReactNode } from 'react';
import { useGenerationForm, type GenerationFormValues } from '@/lib/hooks/useGenerationForm';
import type { UseFormReturn } from 'react-hook-form';

interface GenerationFormContextValue {
  form: UseFormReturn<GenerationFormValues>;
  handleSubmit: (data: GenerationFormValues, selectedProfileId: string | null) => Promise<void>;
  isPending: boolean;
}

const GenerationFormContext = createContext<GenerationFormContextValue | null>(null);

interface GenerationFormProviderProps {
  children: ReactNode;
}

export function GenerationFormProvider({ children }: GenerationFormProviderProps) {
  const value = useGenerationForm();
  return (
    <GenerationFormContext.Provider value={value}>
      {children}
    </GenerationFormContext.Provider>
  );
}

export function useGenerationFormContext(): GenerationFormContextValue | null {
  return useContext(GenerationFormContext);
}
