/**
 * Generic hero-style page placeholder used by every tab during Phase A.
 * Mirrors the design system's "visual header" pattern — colored icon block on
 * the left, title + blurb on the right — so the actual sections can later
 * slot in below without reshuffling the page chrome.
 */
import type { Icon } from '@tabler/icons-react'

interface Props {
  icon: Icon
  title: string
  blurb: string
  phase: string
}

export function PagePlaceholder({ icon: Icon, title, blurb, phase }: Props) {
  return (
    <div className="flex-1 overflow-y-auto p-8">
      <div className="max-w-5xl mx-auto">
      <header className="card card-hover p-6 flex items-start gap-6">
        <div className="shrink-0 w-16 h-16 rounded-xl2 bg-accent-gradient flex items-center justify-center text-white">
          <Icon size={32} strokeWidth={1.5} />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-3 mb-2">
            <h1 className="text-2xl font-medium text-ink">{title}</h1>
            <span className="text-[11px] uppercase tracking-wider text-ink-faint border border-line rounded-full px-2 py-0.5">
              {phase}
            </span>
          </div>
          <p className="text-ink-muted text-sm leading-relaxed">{blurb}</p>
        </div>
      </header>

      <div className="mt-8 grid grid-cols-3 gap-4">
        <div className="card p-5 text-sm text-ink-muted">
          <div className="text-ink mb-1 font-medium">Hero header</div>
          Every tab opens with a visual explanation of what lives there.
        </div>
        <div className="card p-5 text-sm text-ink-muted">
          <div className="text-ink mb-1 font-medium">Live data</div>
          Sections below the hero pull from the FastAPI sidecar.
        </div>
        <div className="card p-5 text-sm text-ink-muted">
          <div className="text-ink mb-1 font-medium">Sentence case</div>
          Tone is warm and friendly. No bold, no all-caps.
        </div>
      </div>
      </div>
    </div>
  )
}
