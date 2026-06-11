import { createContext, useContext, type ReactNode } from 'react';
import { FormProvider, type UseFormReturn } from 'react-hook-form';
import { useGenerationForm, type GenerationFormValues } from '@/lib/hooks/useGenerationForm';

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
  // Also expose the react-hook-form context so shadcn Form components
  // (FormControl/FormField/etc.) work anywhere under the provider — e.g.
  // EngineModelSelector in ParamsPanel, which renders outside FloatingGenerateBox's
  // own <Form> wrapper.
  return (
    <GenerationFormContext.Provider value={value}>
      <FormProvider {...value.form}>{children}</FormProvider>
    </GenerationFormContext.Provider>
  );
}

export function useGenerationFormContext(): GenerationFormContextValue | null {
  return useContext(GenerationFormContext);
}
