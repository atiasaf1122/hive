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
import { useEffect, useRef } from 'react'
import { api } from '../../lib/api'
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
}

export function Chat({ sessionId, history, team, agents, interrupt }: ChatProps) {
  const scrollRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
  }, [history.length, agents.length, interrupt?.type])

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
                  {m.role} ×{m.count}
                </span>
                <span className="text-ink-faint text-xs">{m.model}</span>
                {m.passive && <span className="text-ink-faint text-xs">(passive)</span>}
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

  async function respond(approved: boolean) {
    try {
      await api.post(`/api/sessions/${sessionId}/approve`, { approved })
    } catch (err) {
      console.error('approval failed', err)
    }
  }

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
                  • {m.role} ×{m.count}{' '}
                  <span className="text-ink-faint">[{m.model}]</span>
                  {m.passive && <span className="text-ink-faint"> (passive)</span>}
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
