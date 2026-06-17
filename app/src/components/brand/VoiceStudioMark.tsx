import { cn } from '@/lib/utils/cn';

/**
 * VoiceStudio brand mark — a waveform/equalizer glyph in a rounded tile,
 * LM-Studio-style. Uses the accent color via `currentColor` so it adapts to
 * the theme; size it with `className` (e.g. "w-8 h-8").
 */
export function VoiceStudioMark({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 32 32"
      className={cn('text-accent', className)}
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden="true"
    >
      <rect width="32" height="32" rx="8" fill="currentColor" fillOpacity="0.15" />
      <g stroke="currentColor" strokeWidth="2.75" strokeLinecap="round">
        <line x1="9" y1="13" x2="9" y2="19" />
        <line x1="14" y1="9" x2="14" y2="23" />
        <line x1="19" y1="11" x2="19" y2="21" />
        <line x1="24" y1="14.5" x2="24" y2="17.5" />
      </g>
    </svg>
  );
}
