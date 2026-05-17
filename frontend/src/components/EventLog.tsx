import { useEffect, useRef } from 'react'
import { useSessionsStore } from '../stores/sessions'
import type { WSEvent } from '../types'

function formatEvent(e: WSEvent): { label: string; color: string } | null {
  switch (e.type) {
    case 'session_start': return { label: `▶ Session started: ${e.task ?? ''}`, color: 'text-blue-400' }
    case 'plan_complete': return { label: '📋 Plan complete — team selected', color: 'text-violet-400' }
    case 'spawn_complete': return { label: `🚀 Agents spawned (${(e.agents as unknown[])?.length ?? 0})`, color: 'text-violet-400' }
    case 'agent_start': return { label: `▶ [${e.agent_id}] started`, color: 'text-green-400' }
    case 'agent_end': return { label: `✓ [${e.agent_id}] completed`, color: 'text-green-600' }
    case 'agent_error': return { label: `✗ [${e.agent_id}] error: ${e.error}`, color: 'text-red-400' }
    case 'text_delta': return null
    case 'cost': return { label: `💰 [${e.agent_id}] $${(e.cost_usd ?? 0).toFixed(4)} (${e.input_tokens}in/${e.output_tokens}out)`, color: 'text-gray-500' }
    case 'interrupt': return { label: '⚠ Approval required', color: 'text-yellow-400' }
    case 'session_end': return { label: `■ Session ${e.status}`, color: e.status === 'completed' ? 'text-green-400' : 'text-red-400' }
    case 'session_error': return { label: `✗ Session error: ${e.error}`, color: 'text-red-400' }
    default: return null
  }
}

interface Props {
  sessionId: string
}

export function EventLog({ sessionId }: Props) {
  const events = useSessionsStore((s) => s.sessions[sessionId]?.events ?? [])
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [events.length])

  const displayEvents = events
    .map((e, i) => ({ ...formatEvent(e), i }))
    .filter((x): x is { label: string; color: string; i: number } => x.label !== null && x.label !== undefined)

  return (
    <div className="h-full overflow-y-auto p-3 font-mono text-xs">
      {displayEvents.length === 0 && (
        <div className="text-gray-700 text-center mt-4">Waiting for events…</div>
      )}
      {displayEvents.map(({ label, color, i }) => (
        <div key={i} className={`${color} mb-0.5 leading-relaxed truncate`}>
          {label}
        </div>
      ))}
      <div ref={bottomRef} />
    </div>
  )
}
