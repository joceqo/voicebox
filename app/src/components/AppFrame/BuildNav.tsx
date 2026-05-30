import { Link, useMatchRoute } from '@tanstack/react-router';
import { ChevronRight, Mic, Box, Settings, KeyRound, BookOpen, Volume2 } from 'lucide-react';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import voiceboxLogo from '@/assets/voicebox-logo.png';
import { cn } from '@/lib/utils/cn';
import { usePlatform } from '@/platform/PlatformContext';
import type { UpdateStatus } from '@/platform/types';
import { usePlayerStore } from '@/stores/playerStore';
import { version } from '../../../package.json';

interface BuildNavProps {
  isMacOS?: boolean;
}

export function BuildNav({ isMacOS }: BuildNavProps) {
  const { t } = useTranslation();
  const matchRoute = useMatchRoute();
  const isPlayerOpen = !!usePlayerStore((s) => s.audioUrl);
  const platform = usePlatform();
  const [updateStatus, setUpdateStatus] = useState<UpdateStatus>(platform.updater.getStatus());
  useEffect(() => platform.updater.subscribe(setUpdateStatus), [platform.updater]);

  const isTTSActive = !!matchRoute({ to: '/', fuzzy: false });

  return (
    <div
      className={cn(
        'fixed left-0 top-0 h-full w-[var(--build-nav-w)] bg-sidebar border-r border-border flex flex-col py-6 overflow-hidden',
        isMacOS && 'pt-14',
      )}
    >
      {/* Logo */}
      <div className="flex items-center gap-2.5 px-4 mb-6">
        <img src={voiceboxLogo} alt="Voicebox" className="sidebar-logo w-8 h-8 object-contain shrink-0" />
        <span className="text-sm font-semibold tracking-wide text-foreground/80 uppercase">
          {t('nav.build', 'Build')}
        </span>
      </div>

      {/* Nav tree */}
      <nav className="flex-1 flex flex-col gap-1 px-2 overflow-y-auto">
        {/* Audio section */}
        <div className="mb-1">
          <div className="flex items-center gap-1.5 px-2 py-1 text-[11px] font-semibold uppercase tracking-widest text-muted-foreground/50 select-none">
            <Volume2 className="h-3 w-3" />
            {t('nav.audio', 'Audio')}
            <ChevronRight className="h-3 w-3 ml-auto" />
          </div>

          {/* TTS */}
          <Link
            to="/"
            className={cn(
              'flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm transition-colors ml-2',
              isTTSActive
                ? 'bg-accent/15 text-accent font-medium'
                : 'text-muted-foreground hover:bg-muted/50 hover:text-foreground',
            )}
          >
            <span className="h-1.5 w-1.5 rounded-full bg-current opacity-60 shrink-0" />
            {t('nav.tts', 'TTS')}
          </Link>

          {/* STT */}
          <Link
            to="/stt"
            className={cn(
              'flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm transition-colors ml-2',
              matchRoute({ to: '/stt', fuzzy: true })
                ? 'bg-accent/15 text-accent font-medium'
                : 'text-muted-foreground hover:bg-muted/50 hover:text-foreground',
            )}
          >
            <span className="h-1.5 w-1.5 rounded-full bg-current opacity-60 shrink-0" />
            {t('nav.stt', 'STT')}
          </Link>
        </div>

        {/* Voices */}
        <Link
          to="/voices"
          className={cn(
            'flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm transition-colors',
            matchRoute({ to: '/voices', fuzzy: true })
              ? 'bg-accent/15 text-accent font-medium'
              : 'text-muted-foreground hover:bg-muted/50 hover:text-foreground',
          )}
        >
          <Mic className="h-3.5 w-3.5 shrink-0" />
          {t('nav.voices', 'Voices')}
        </Link>

        {/* Models */}
        <Link
          to="/models"
          className={cn(
            'flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm transition-colors',
            matchRoute({ to: '/models', fuzzy: true })
              ? 'bg-accent/15 text-accent font-medium'
              : 'text-muted-foreground hover:bg-muted/50 hover:text-foreground',
          )}
        >
          <Box className="h-3.5 w-3.5 shrink-0" />
          {t('nav.models', 'Models')}
        </Link>

        {/* Separator */}
        <div className="my-2 border-t border-border/50" />

        {/* API keys */}
        <Link
          to="/api-keys"
          className={cn(
            'flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm transition-colors',
            matchRoute({ to: '/api-keys', fuzzy: true })
              ? 'bg-accent/15 text-accent font-medium'
              : 'text-muted-foreground hover:bg-muted/50 hover:text-foreground',
          )}
        >
          <KeyRound className="h-3.5 w-3.5 shrink-0" />
          {t('nav.apiKeys', 'API Keys')}
        </Link>

        {/* Docs — disabled */}
        <span
          className="flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm text-muted-foreground/40 cursor-not-allowed select-none"
          title="Coming soon"
        >
          <BookOpen className="h-3.5 w-3.5 shrink-0" />
          {t('nav.docs', 'Docs')}
          <span className="ml-auto text-[10px] text-muted-foreground/30">soon</span>
        </span>

        {/* Settings */}
        <Link
          to="/settings"
          className={cn(
            'flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm transition-colors',
            matchRoute({ to: '/settings', fuzzy: true })
              ? 'bg-accent/15 text-accent font-medium'
              : 'text-muted-foreground hover:bg-muted/50 hover:text-foreground',
          )}
        >
          <Settings className="h-3.5 w-3.5 shrink-0" />
          {t('nav.settings', 'Settings')}
        </Link>
      </nav>

      {/* Footer: version + update badge */}
      <div
        className="flex flex-col items-start gap-1.5 px-4 transition-all duration-300"
        style={{ paddingBottom: isPlayerOpen ? '7rem' : undefined }}
      >
        <span className="text-[10px] text-muted-foreground/50">v{version}</span>
        {updateStatus.available && (
          <Link
            to="/settings"
            className="text-[9px] font-semibold tracking-wide uppercase px-2 py-0.5 rounded-full bg-accent/15 text-accent hover:bg-accent/25 transition-colors"
          >
            {t('nav.updateBadge')}
          </Link>
        )}
      </div>
    </div>
  );
}
