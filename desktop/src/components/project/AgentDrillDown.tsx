/**
 * Slide-over panel that opens when you click an AgentPill — shows the
 * agent's live event stream so you can see *what* it's doing right
 * now (tool calls + text), not just a single one-liner in the bar.
 *
 * Reads from `ProjectState.events` (the same raw WS stream the rest
 * of the UI consumes) and filters to events tagged with this agent.
 */
import { IconLoader2, IconX } from '@tabler/icons-react'
import { useEffect, useMemo, useRef } from 'react'
import type { WSEvent } from '../../lib/types'

interface Props {
  agentId: string
  events: WSEvent[]
  onClose: () => void
}

export function AgentDrillDown({ agentId, events, onClose }: Props) {
  // Filter to this agent's events + collapse the text/delta → text/done
  // pair down to a single rendered line. Without this the panel showed
  // the same paragraph twice: once as the streaming delta, once as the
  // consolidated final message.
  //
  // useMemo'd because the inner dedupe is O(n²) over the per-agent slice
  // and re-running it on every WS event with no agent_id change is wasted
  // work. Each new WS event invalidates the cache exactly once.
  const ours = useMemo(() => {
    const mine = events.filter((e) => e.agent_id === agentId)
    return mine.filter((e, idx) => {
      if (e.type !== 'text/delta') return true
      for (let j = idx + 1; j < mine.length; j++) {
        const next = mine[j]
        if (next.type === 'text/done' && next.text) return false
        if (next.type === 'agent/end') break
      }
      return true
    })
  }, [events, agentId])

  const scrollRef = useRef<HTMLDivElement | null>(null)

  // Auto-scroll to the latest event so the user always sees the
  // freshest activity without manually scrolling.
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
  }, [ours.length])

  // Escape closes the panel — the header tooltip advertises it, and
  // the previous version silently lied. Listener is scoped to the
  // panel's lifetime so it doesn't fight other Esc consumers when
  // closed.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') {
        e.stopPropagation()
        onClose()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div
      role="dialog"
      aria-label={`Agent activity: ${agentId}`}
      className="fixed inset-y-0 right-0 z-30 w-[440px] max-w-[90vw] bg-bg border-l border-line shadow-hover flex flex-col"
    >
      <header className="px-4 py-3 border-b border-line flex items-center justify-between">
        <div className="min-w-0">
          <div className="text-sm text-ink font-medium truncate">{agentId}</div>
          <div className="text-[11px] text-ink-faint">
            {ours.length} event{ours.length === 1 ? '' : 's'}
          </div>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="text-ink-faint hover:text-ink"
          title="Close (Esc)"
          aria-label="Close agent activity panel"
        >
          <IconX size={16} />
        </button>
      </header>

      <div ref={scrollRef} className="flex-1 overflow-y-auto px-4 py-3 space-y-2 text-xs">
        {ours.length === 0 && (
          <div className="text-ink-faint italic flex items-center gap-2">
            <IconLoader2 size={12} className="animate-spin" />
            Waiting for events…
          </div>
        )}
        {ours.map((e, i) => (
          // Prefer the backend's monotonic event_id so list shifts (e.g.
          // when the 200-event ring buffer drops the oldest event) don't
          // re-key live rows. Fallback to a composite that includes
          // ts/text so the key is stable even for events that arrived
          // before this field was wired through the WS bus.
          <EventRow key={e.event_id ?? `${e.type}:${e.ts ?? ''}:${i}`} ev={e} />
        ))}
      </div>
    </div>
  )
}

function EventRow({ ev }: { ev: WSEvent }) {
  const kind = String(ev.type)
  // Pick a colour + label per event kind.
  if (kind === 'tool/use') {
    const e = ev as WSEvent & { tool_name?: string; tool_input?: Record<string, unknown> }
    const arg =
      (e.tool_input?.path as string | undefined) ||
      (e.tool_input?.file_path as string | undefined) ||
      (e.tool_input?.command as string | undefined) ||
      (e.tool_input?.pattern as string | undefined) ||
      ''
    return (
      <div className="border-l-2 border-sky-500/60 pl-2.5">
        <div className="text-[10px] uppercase tracking-wider text-sky-500">
          {e.tool_name || 'tool'}
        </div>
        {arg && (
          <div className="text-ink-muted font-mono text-[11px] break-all">
            {String(arg).slice(0, 240)}
          </div>
        )}
      </div>
    )
  }
  if (kind === 'tool/result') {
    return (
      <div className="border-l-2 border-emerald-500/40 pl-2.5">
        <div className="text-[10px] uppercase tracking-wider text-emerald-500">
          result
        </div>
        {ev.text && (
          <div className="text-ink-muted font-mono text-[11px] break-all whitespace-pre-wrap">
            {ev.text.slice(0, 240)}
          </div>
        )}
      </div>
    )
  }
  if (kind === 'text/delta' || kind === 'text/done') {
    if (!ev.text) return null
    return (
      <div className="text-ink leading-relaxed">{ev.text.slice(0, 400)}</div>
    )
  }
  if (kind === 'agent/start') {
    return <div className="text-[10px] uppercase tracking-wider text-ink-faint">started</div>
  }
  if (kind === 'agent/end') {
    return <div className="text-[10px] uppercase tracking-wider text-emerald-500">done</div>
  }
  if (kind === 'agent/error') {
    return (
      <div className="border-l-2 border-red-500 pl-2.5">
        <div className="text-[10px] uppercase tracking-wider text-red-500">error</div>
        <div className="text-red-500 font-mono text-[11px] whitespace-pre-wrap">{ev.error || ''}</div>
      </div>
    )
  }
  if (kind === 'system/cost') {
    return (
      <div className="text-[10px] text-ink-faint">
        cost · {ev.input_tokens ?? '?'} in / {ev.output_tokens ?? '?'} out · ${(ev.cost_usd ?? 0).toFixed(4)}
      </div>
    )
  }
  return null
}
