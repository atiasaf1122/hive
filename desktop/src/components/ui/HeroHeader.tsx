/**
 * Visual hero header used at the top of every tab.
 *
 *   ┌────────────────────────────────────────────────────────────────┐
 *   │ [icon block]  Title                                            │
 *   │              One- or two-sentence explanation of this tab      │
 *   │                                                                │
 *   │              optional stat row · optional flow diagram         │
 *   └────────────────────────────────────────────────────────────────┘
 *
 * The icon block is the warm-orange gradient by default but pages can pass
 * a tinted variant when the accent would be too loud.
 */
import type { Icon } from '@tabler/icons-react'
import clsx from 'clsx'

interface Props {
  icon: Icon
  title: string
  blurb: string
  /** Optional small stat row, e.g. "3 active · next run in 2h". */
  stats?: React.ReactNode
  /** Optional ascii-ish flow strip rendered under the blurb. */
  flow?: React.ReactNode
  /** Optional right-aligned actions slot. */
  actions?: React.ReactNode
  /** Use a soft-tinted icon block instead of the full gradient. */
  tone?: 'gradient' | 'soft'
}

export function HeroHeader({
  icon: Icon,
  title,
  blurb,
  stats,
  flow,
  actions,
  tone = 'gradient',
}: Props) {
  return (
    <header className="card card-hover p-6">
      <div className="flex items-start gap-5">
        <div
          className={clsx(
            'shrink-0 w-14 h-14 rounded-xl2 flex items-center justify-center',
            tone === 'gradient'
              ? 'bg-accent-gradient text-white'
              : 'bg-surface-2 text-accent',
          )}
        >
          <Icon size={28} strokeWidth={1.5} />
        </div>

        <div className="flex-1 min-w-0">
          <div className="flex items-start justify-between gap-3">
            <div className="flex-1 min-w-0">
              <h1 className="text-2xl font-medium text-ink leading-tight">{title}</h1>
              <p className="mt-1.5 text-ink-muted text-sm leading-relaxed max-w-2xl">
                {blurb}
              </p>
            </div>
            {actions && <div className="shrink-0">{actions}</div>}
          </div>

          {stats && (
            <div className="mt-3 text-xs text-ink-muted flex items-center gap-3">
              {stats}
            </div>
          )}

          {flow && (
            <div className="mt-4 text-[11px] text-ink-faint">
              {flow}
            </div>
          )}
        </div>
      </div>
    </header>
  )
}

/**
 * Pre-baked flow strip — used by Automations + Plugins + Skills heros to
 * show their conceptual pipeline in a single line of ascii-like blocks.
 */
interface FlowStripProps {
  steps: string[]
}

export function FlowStrip({ steps }: FlowStripProps) {
  return (
    <div className="flex items-center gap-1.5 flex-wrap">
      {steps.map((s, i) => (
        <span key={s} className="flex items-center gap-1.5">
          <span className="px-2.5 py-1 rounded-full bg-surface-2 border border-line text-ink-muted text-[11px]">
            {s}
          </span>
          {i < steps.length - 1 && (
            <span className="text-ink-faint select-none">→</span>
          )}
        </span>
      ))}
    </div>
  )
}
