/**
 * Conversation column for the project view.
 *
 *   ┌─ user bubble (dark, right-aligned) ───────────────────────────────────┐
 *   ├─ orchestrator bubble (light, left-aligned, with avatar) ──────────────┤
 *   ├─ activity card (inline, when agents spawned) ────────────────────────┤
 *   ├─ approval card (inline, when team_approval interrupt arrives) ───────┤
 *   └───────────────────────────────────────────────────────────────────────┘
 *
 * Auto-scrolls to bottom on new content.
 */
import { IconCheck, IconHexagon, IconLoader2, IconUser } from '@tabler/icons-react'
import { useEffect, useRef, useState } from 'react'
import { api } from '../../lib/api'
import { toast } from '../../lib/toast'
import { useSessions } from '../../stores/sessions'
import type {
  ConversationEntry,
  InterruptPayload,
  TeamComposition,
} from '../../lib/types'
import type { AgentInfo } from '../../lib/types'

interface ChatProps {
  sessionId: string
  history: ConversationEntry[]
  team: TeamComposition | null
  agents: AgentInfo[]
  interrupt: InterruptPayload | null
  plannerLog?: string[]
  stallHint?: string | null
}

export function Chat({ sessionId, history, team, agents, interrupt, plannerLog, stallHint }: ChatProps) {
  const scrollRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
  }, [history.length, agents.length, interrupt?.type, plannerLog?.length])

  return (
    <div ref={scrollRef} className="flex-1 overflow-y-auto">
      <div className="max-w-[760px] mx-auto px-6 py-6 space-y-4">
        {history.length === 0 && (
          <div className="text-center text-ink-faint text-sm py-12">
            The orchestrator is thinking… messages will appear here.
          </div>
        )}

        {history.map((m, i) => (
          <Bubble key={i} entry={m} />
        ))}

        {/* Live planner activity — shown while the orchestrator is mid-turn
            so the user sees what it's reading / thinking instead of a
            frozen spinner. */}
        {plannerLog && plannerLog.length > 0 && (
          <PlannerActivity log={plannerLog} stallHint={stallHint ?? null} />
        )}

        {/* Inline activity card — shown once we know a team has been planned */}
        {team && team.team.length > 0 && (
          <ActivityCard team={team} agents={agents} />
        )}

        {/* Inline approval card */}
        {interrupt?.type === 'team_approval' && (
          <ApprovalCard sessionId={sessionId} payload={interrupt} />
        )}
      </div>
    </div>
  )
}

function PlannerActivity({ log, stallHint }: { log: string[]; stallHint: string | null }) {
  return (
    <div className="flex items-start gap-3">
      <div className="w-8 h-8 rounded-full bg-surface-2 text-ink-muted flex items-center justify-center shrink-0 mt-0.5">
        <IconLoader2 size={16} strokeWidth={1.75} className="animate-spin" />
      </div>
      <div className="card flex-1 p-3 border-dashed">
        <div className="text-[11px] uppercase tracking-wider text-ink-faint mb-2">
          orchestrator activity
        </div>
        <ul className="space-y-1 font-mono text-[12px] text-ink-muted leading-snug max-h-48 overflow-y-auto">
          {log.map((line, i) => (
            <li key={i} className="truncate" title={line}>
              {line}
            </li>
          ))}
        </ul>
        {stallHint && (
          <div className="mt-2 text-[11px] text-amber-600 dark:text-amber-300">
            {stallHint}
          </div>
        )}
      </div>
    </div>
  )
}

function Bubble({ entry }: { entry: ConversationEntry }) {
  if (entry.role === 'user') {
    return (
      <div className="flex justify-end">
        <div className="max-w-[80%] bg-ink text-bg px-4 py-2.5 rounded-2xl rounded-tr-md text-sm leading-relaxed whitespace-pre-wrap">
          {entry.content}
        </div>
      </div>
    )
  }
  if (entry.role === 'system') {
    return (
      <div className="flex justify-center">
        <div className="max-w-[80%] text-xs text-red-600 dark:text-red-400 bg-red-500/10 border border-red-500/30 px-3 py-2 rounded-lg whitespace-pre-wrap text-center">
          {entry.content}
        </div>
      </div>
    )
  }
  return (
    <div className="flex items-start gap-3">
      <div className="w-8 h-8 rounded-full bg-accent-gradient text-white flex items-center justify-center shrink-0 mt-0.5">
        <IconHexagon size={16} strokeWidth={1.5} />
      </div>
      <div className="max-w-[80%] bg-surface border border-line text-ink px-4 py-2.5 rounded-2xl rounded-tl-md text-sm leading-relaxed whitespace-pre-wrap">
        {entry.content}
      </div>
    </div>
  )
}

function ActivityCard({ team, agents }: { team: TeamComposition; agents: AgentInfo[] }) {
  return (
    <div className="flex items-start gap-3">
      <div className="w-8 h-8 rounded-full bg-surface-2 text-ink-muted flex items-center justify-center shrink-0 mt-0.5">
        <IconLoader2 size={16} strokeWidth={1.75} className="animate-spin" />
      </div>
      <div className="card flex-1 p-4">
        <div className="text-xs text-ink-muted mb-2">Plan</div>
        <ul className="space-y-1.5">
          {team.team.map((m, i) => {
            const matching = agents.filter((a) => a.role.toLowerCase() === m.role.toLowerCase())
            const done = matching.length > 0 && matching.every((a) => a.status === 'completed')
            const running = matching.some((a) => a.status === 'running')
            return (
              <li key={`${m.role}-${i}`} className="flex items-center gap-2 text-sm">
                {done ? (
                  <IconCheck size={14} className="text-emerald-500 shrink-0" />
                ) : running ? (
                  <IconLoader2 size={14} className="text-amber-500 animate-spin shrink-0" />
                ) : (
                  <div className="w-3.5 h-3.5 rounded-full border border-ink-faint shrink-0" />
                )}
                <span className="text-ink">
                  {m.role}{(m.count ?? 1) > 1 ? ` ×${m.count}` : ''}
                </span>
                <span className="text-ink-faint text-xs">{m.model}</span>
                {m.passive && <span className="text-ink-faint text-xs">(passive)</span>}
                {m.subtask && (
                  <span className="text-ink-muted text-xs truncate" title={m.subtask}>
                    — {m.subtask}
                  </span>
                )}
              </li>
            )
          })}
        </ul>
      </div>
    </div>
  )
}

function ApprovalCard({ sessionId, payload }: { sessionId: string; payload: InterruptPayload }) {
  const comp = payload.team_composition
  const confidence = payload.confidence ?? 1
  // Local "submitted" state — once the user clicks, hide the card
  // immediately rather than waiting for the WS round-trip. The backend
  // will follow up with spawn_complete / awaiting_user; if it fails,
  // the next render shows whatever state landed.
  const [submitted, setSubmitted] = useState<'approved' | 'rejected' | null>(null)

  async function respond(approved: boolean) {
    // Capture the interrupt payload BEFORE the optimistic clear so we
    // can restore it on error. Without this, the card unmounts on the
    // optimistic store mutation and `setSubmitted(null)` runs on a
    // dead component — the user gets no feedback that /approve failed.
    const prevInterrupt = payload
    setSubmitted(approved ? 'approved' : 'rejected')
    useSessions.setState((s) => {
      const proj = s.sessions[sessionId]
      if (!proj || !proj.interrupt) return s
      return {
        sessions: { ...s.sessions, [sessionId]: { ...proj, interrupt: null } },
      }
    })
    try {
      // correlation_id is required when more than one approval is in
      // flight for the same session (invariant #5). Sending it always
      // — even when only one is pending — costs nothing and keeps the
      // backend's "ambiguous fallback" branch out of the hot path.
      await api.post(`/api/sessions/${sessionId}/approve`, {
        approved,
        correlation_id: payload.correlation_id,
      })
    } catch (err) {
      console.error('approval failed', err)
      // Restore the interrupt so the card reappears and the user can
      // retry. Also toast — the chat re-render alone is too quiet a
      // signal for a network failure.
      useSessions.getState().setInterrupt(sessionId, prevInterrupt)
      toast.error(
        `Approval didn't go through: ${err instanceof Error ? err.message : 'unknown error'}. Try again.`,
      )
    }
  }

  if (submitted) return null

  return (
    <div className="flex items-start gap-3">
      <div className="w-8 h-8 rounded-full bg-orange-500/15 text-orange-500 flex items-center justify-center shrink-0 mt-0.5">
        <IconUser size={16} strokeWidth={1.75} />
      </div>
      <div className="card flex-1 p-4 border-orange-500/30">
        <div className="flex items-baseline gap-2 mb-2">
          <div className="text-ink text-sm font-medium">Approval needed</div>
          <div className="text-xs text-ink-faint">
            confidence {(confidence * 100).toFixed(0)}%
          </div>
        </div>
        {comp && (
          <>
            <ul className="space-y-1 mb-3">
              {comp.team.map((m, i) => (
                <li key={i} className="text-sm text-ink">
                  • {m.role}{(m.count ?? 1) > 1 ? ` ×${m.count}` : ''}{' '}
                  <span className="text-ink-faint">[{m.model}]</span>
                  {m.passive && <span className="text-ink-faint"> (passive)</span>}
                  {m.subtask && (
                    <div className="text-xs text-ink-muted pl-3">{m.subtask}</div>
                  )}
                </li>
              ))}
            </ul>
            {comp.rationale && (
              <div className="text-xs text-ink-muted italic mb-3">"{comp.rationale}"</div>
            )}
          </>
        )}
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => void respond(true)}
            className="btn-primary text-xs"
          >
            Approve
          </button>
          <button
            type="button"
            onClick={() => void respond(false)}
            className="btn-ghost text-xs"
          >
            Reject
          </button>
        </div>
      </div>
    </div>
  )
}
