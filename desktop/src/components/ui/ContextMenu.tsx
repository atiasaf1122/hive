/**
 * Minimal context menu primitive used by project cards.
 *
 *   <ContextMenuTarget onMenu={(x, y) => open(x, y)}>
 *     <Card />
 *   </ContextMenuTarget>
 *   <ContextMenu open x y items={[…]} onClose={…} />
 */
import clsx from 'clsx'
import { useEffect, useRef } from 'react'

export interface ContextMenuItem {
  label: string
  icon?: React.ReactNode
  danger?: boolean
  onClick: () => void
  divider?: boolean   // if true, render a divider above this item
}

interface Props {
  open: boolean
  x: number
  y: number
  items: ContextMenuItem[]
  onClose: () => void
}

export function ContextMenu({ open, x, y, items, onClose }: Props) {
  const ref = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    if (!open) return
    function onDoc(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose()
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('mousedown', onDoc)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDoc)
      document.removeEventListener('keydown', onKey)
    }
  }, [open, onClose])

  if (!open) return null

  // Clamp to viewport.
  const clampedX = Math.min(x, window.innerWidth - 240)
  const clampedY = Math.min(y, window.innerHeight - items.length * 36 - 16)

  return (
    <div
      ref={ref}
      role="menu"
      className="fixed z-50 card shadow-hover py-1 min-w-[220px] text-sm"
      style={{ left: clampedX, top: clampedY }}
    >
      {items.map((item, i) => (
        <div key={i}>
          {item.divider && <div className="h-px bg-line my-1 mx-1" />}
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              item.onClick()
              onClose()
            }}
            className={clsx(
              'w-full flex items-center gap-2.5 px-3 py-1.5 text-left transition-colors',
              item.danger
                ? 'text-red-500 hover:bg-red-500/10'
                : 'text-ink hover:bg-surface-2',
            )}
          >
            {item.icon && <span className="text-ink-muted shrink-0">{item.icon}</span>}
            {item.label}
          </button>
        </div>
      ))}
    </div>
  )
}

/** Convenience wrapper to capture the right-click x/y on any child. */
interface TargetProps {
  onMenu: (x: number, y: number) => void
  children: React.ReactNode
  className?: string
}

export function ContextMenuTarget({ onMenu, children, className }: TargetProps) {
  return (
    <div
      className={className}
      onContextMenu={(e) => {
        e.preventDefault()
        onMenu(e.clientX, e.clientY)
      }}
    >
      {children}
    </div>
  )
}
