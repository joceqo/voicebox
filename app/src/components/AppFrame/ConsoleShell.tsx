import { useMatchRoute } from '@tanstack/react-router';
import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { GenerationFormProvider } from '@/components/Generation/GenerationFormContext';
import { useUIStore } from '@/stores/uiStore';
import { cn } from '@/lib/utils/cn';
import { BuildNav } from './BuildNav';
import { ParamsPanel } from './ParamsPanel';

// Simple platform check that works in both web and Tauri
const isMacOS = () => navigator.platform.toLowerCase().includes('mac');

const BUILD_NAV_W = '200px';
const PARAMS_W = '260px';

interface ConsoleShellProps {
  children: ReactNode;
}

export function ConsoleShell({ children }: ConsoleShellProps) {
  const { t } = useTranslation();
  const simpleMode = useUIStore((s) => s.simpleMode);
  const toggleSimpleMode = useUIStore((s) => s.toggleSimpleMode);
  const matchRoute = useMatchRoute();
  const isIndexRoute = !!matchRoute({ to: '/', fuzzy: false });

  const showColumns = !simpleMode;

  return (
    <GenerationFormProvider>
      <div
        className="flex flex-1 min-h-0 overflow-hidden"
        style={
          {
            '--build-nav-w': showColumns ? BUILD_NAV_W : '0px',
            '--params-w': showColumns && isIndexRoute ? PARAMS_W : '0px',
          } as React.CSSProperties
        }
      >
        {/* Left build nav */}
        {showColumns && (
          <BuildNav isMacOS={isMacOS()} />
        )}

        {/* Center content */}
        <main
          className={cn(
            'flex-1 overflow-hidden flex flex-col transition-all duration-200',
            showColumns ? 'ml-[var(--build-nav-w)]' : 'ml-0',
          )}
          style={{
            marginRight: showColumns && isIndexRoute ? PARAMS_W : undefined,
          }}
        >
          <div className="container mx-auto px-8 max-w-[1800px] h-full overflow-hidden flex flex-col">
            {children}
          </div>
        </main>

        {/* Right params panel — only on index route in advanced mode */}
        {showColumns && isIndexRoute && <ParamsPanel />}

        {/* Simple / Advanced toggle */}
        <button
          type="button"
          onClick={toggleSimpleMode}
          className="fixed bottom-4 right-4 z-50 text-[11px] font-medium px-3 py-1.5 rounded-full bg-muted/80 text-muted-foreground border border-border hover:bg-muted hover:text-foreground transition-all duration-200 backdrop-blur-sm"
          title={simpleMode ? t('mode.advanced', 'Advanced') : t('mode.simple', 'Simple')}
        >
          {simpleMode ? t('mode.advanced', 'Advanced') : t('mode.simple', 'Simple')}
        </button>
      </div>
    </GenerationFormProvider>
  );
}
