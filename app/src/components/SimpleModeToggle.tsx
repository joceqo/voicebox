import { useUIStore } from '@/stores/uiStore'

export function SimpleModeToggle() {
  const simpleMode = useUIStore((s) => s.simpleMode)
  const setSimpleMode = useUIStore((s) => s.setSimpleMode)
  return (
    <button
      onClick={() => setSimpleMode(!simpleMode)}
      className="text-xs text-muted-foreground hover:text-foreground transition-colors px-2 py-1 rounded"
    >
      {simpleMode ? 'Advanced ›' : '‹ Simple'}
    </button>
  )
}
