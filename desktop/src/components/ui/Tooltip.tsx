/**
 * Lightweight CSS-only tooltip. Pure presentational — no portals, no JS scheduling.
 * Hover the wrapped child for ~800 ms (the spec) and the bubble appears.
 *
 * Use:
 *   <Tooltip text="Open settings (Ctrl + ,)"><IconSettings /></Tooltip>
 */
import clsx from 'clsx'

interface Props {
  text: string
  children: React.ReactNode
  side?: 'top' | 'right' | 'bottom' | 'left'
  className?: string
}

export function Tooltip({ text, children, side = 'top', className }: Props) {
  return (
    <span className={clsx('group relative inline-flex', className)}>
      {children}
      <span
        role="tooltip"
        className={clsx(
          'pointer-events-none absolute z-30 whitespace-nowrap text-[11px] px-2 py-1 rounded-md',
          'bg-ink text-bg opacity-0 group-hover:opacity-100 transition-opacity',
          'delay-[600ms] group-hover:delay-[800ms]',
          side === 'top' && 'bottom-full mb-1.5 left-1/2 -translate-x-1/2',
          side === 'bottom' && 'top-full mt-1.5 left-1/2 -translate-x-1/2',
          side === 'right' && 'left-full ml-1.5 top-1/2 -translate-y-1/2',
          side === 'left' && 'right-full mr-1.5 top-1/2 -translate-y-1/2',
        )}
      >
        {text}
      </span>
    </span>
  )
}
