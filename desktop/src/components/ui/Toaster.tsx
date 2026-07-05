import { useEffect, useState } from 'react'
import { dismiss, subscribeToToasts, type Toast } from '../../lib/toast'

const STYLES: Record<Toast['kind'], string> = {
  error: 'border-red-500/40 bg-red-500/10 text-red-200',
  success: 'border-emerald-500/40 bg-emerald-500/10 text-emerald-200',
  info: 'border-accent/40 bg-accent/10 text-ink',
}

export function Toaster() {
  const [items, setItems] = useState<readonly Toast[]>([])

  useEffect(() => subscribeToToasts(setItems), [])

  if (items.length === 0) return null

  return (
    <div className="fixed bottom-4 right-4 z-50 flex flex-col gap-2 max-w-sm pointer-events-none">
      {items.map((t) => (
        <div
          key={t.id}
          className={`pointer-events-auto rounded border ${STYLES[t.kind]} px-3 py-2 text-sm shadow-hover flex items-start gap-2`}
          role={t.kind === 'error' ? 'alert' : 'status'}
        >
          <div className="flex-1 break-words">{t.message}</div>
          <button
            type="button"
            className="opacity-60 hover:opacity-100 text-xs"
            onClick={() => dismiss(t.id)}
            aria-label="Dismiss notification"
          >
            ✕
          </button>
        </div>
      ))}
    </div>
  )
}
