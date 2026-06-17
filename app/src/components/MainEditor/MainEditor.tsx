import { Sparkles, Upload } from 'lucide-react';
import { useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { FloatingGenerateBox } from '@/components/Generation/FloatingGenerateBox';
import { HistoryTable } from '@/components/History/HistoryTable';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { useToast } from '@/components/ui/use-toast';

import { useImportProfile } from '@/lib/hooks/useProfiles';
import { cn } from '@/lib/utils/cn';
import { usePlayerStore } from '@/stores/playerStore';
import { useUIStore } from '@/stores/uiStore';

export function MainEditor() {
  const { t } = useTranslation();
  const audioUrl = usePlayerStore((state) => state.audioUrl);
  const isPlayerVisible = !!audioUrl;
  const setDialogOpen = useUIStore((state) => state.setProfileDialogOpen);
  const importProfile = useImportProfile();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [importDialogOpen, setImportDialogOpen] = useState(false);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const { toast } = useToast();

  const handleImportClick = () => {
    fileInputRef.current?.click();
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) {
      if (!file.name.endsWith('.voicebox.zip')) {
        toast({
          title: t('main.import.invalidTitle'),
          description: t('main.import.invalidDescription'),
          variant: 'destructive',
        });
        return;
      }
      setSelectedFile(file);
      setImportDialogOpen(true);
    }
  };

  const handleImportConfirm = () => {
    if (selectedFile) {
      importProfile.mutate(selectedFile, {
        onSuccess: () => {
          setImportDialogOpen(false);
          setSelectedFile(null);
          if (fileInputRef.current) {
            fileInputRef.current.value = '';
          }
          toast({
            title: t('main.import.successTitle'),
            description: t('main.import.successDescription'),
          });
        },
        onError: (error) => {
          toast({
            title: t('main.import.failedTitle'),
            description: error.message,
            variant: 'destructive',
          });
        },
      });
    }
  };

  return (
    <div className="flex flex-col h-full min-h-0 overflow-hidden relative">
      {/* Slim header — title lives in the nav, so this is just the section label + actions */}
      <div className="flex items-center justify-between pt-4 pb-3 px-1 shrink-0">
        <h2 className="text-sm font-semibold uppercase tracking-widest text-muted-foreground/60">
          {t('history.title', 'History')}
        </h2>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={handleImportClick}>
            <Upload className="mr-2 h-4 w-4" />
            {t('main.importVoice')}
          </Button>
          <input
            ref={fileInputRef}
            type="file"
            accept=".voicebox.zip"
            onChange={handleFileChange}
            className="hidden"
          />
          <Button size="sm" onClick={() => setDialogOpen(true)}>
            <Sparkles className="mr-2 h-4 w-4" />
            {t('main.createVoice')}
          </Button>
        </div>
      </div>

      <div className={cn('flex-1 min-h-0 overflow-hidden', isPlayerVisible && 'lg:pb-32')}>
        <HistoryTable />
      </div>

      <FloatingGenerateBox isPlayerOpen={!!audioUrl} />

      <Dialog open={importDialogOpen} onOpenChange={setImportDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t('main.import.dialogTitle')}</DialogTitle>
            <DialogDescription>
              {t('main.import.dialogDescription', { name: selectedFile?.name })}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => {
                setImportDialogOpen(false);
                setSelectedFile(null);
                if (fileInputRef.current) {
                  fileInputRef.current.value = '';
                }
              }}
            >
              {t('common.cancel')}
            </Button>
            <Button
              onClick={handleImportConfirm}
              disabled={importProfile.isPending || !selectedFile}
            >
              {importProfile.isPending ? t('main.import.importing') : t('main.import.action')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
