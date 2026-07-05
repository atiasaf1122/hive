/**
 * Trajectory replay (D7) — the session's full story as a vertical timeline,
 * built entirely from persisted events + checkpoint history. Read-only.
 */
import { IconAlertTriangle, IconRefresh } from '@tabler/icons-react'
import clsx from 'clsx'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '../../lib/api'

interface TrajectoryNode {
  ts: number
  agent_id: string
  category: string
  type: string
  title: string
  payload: Record<string, unknown>
}

const CATEGORY_TONE: Record<string, string> = {
  message: 'bg-ink text-bg',
  lifecycle: 'bg-surface-2 text-ink-muted',
  validation: 'bg-amber-500/15 text-amber-500',
  review: 'bg-purple-500/15 text-purple-500',
  compaction: 'bg-sky-500/15 text-sky-500',
  lesson: 'bg-emerald-500/15 text-emerald-500',
  mcp: 'bg-sky-500/15 text-sky-500',
  error: 'bg-red-500/15 text-red-500',
  estimate: 'bg-surface-2 text-ink-muted',
  cost: 'bg-surface-2 text-ink-faint',
  tool: 'bg-surface-2 text-ink-faint',
  text: 'bg-surface-2 text-ink-muted',
  other: 'bg-surface-2 text-ink-faint',
}

export function TrajectoryView({ sessionId }: { sessionId: string }) {
  const [nodes, setNodes] = useState<TrajectoryNode[]>([])
  const [agentFilter, setAgentFilter] = useState<string>('')
  const [expanded, setExpanded] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)

  const reload = useCallback(async () => {
    try {
      const res = await api.get<{ trajectory: TrajectoryNode[] }>(
        `/api/sessions/${sessionId}/trajectory`)
      setNodes(res.trajectory)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }, [sessionId])

  useEffect(() => { void reload() }, [reload])

  const agents = useMemo(
    () => Array.from(new Set(nodes.map((n) => n.agent_id))).sort(),
    [nodes])
  const shown = agentFilter ? nodes.filter((n) => n.agent_id === agentFilter) : nodes
  const firstErrorIdx = shown.findIndex((n) => n.category === 'error')

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-[860px] mx-auto px-6 py-4">
        <div className="flex items-center gap-2 mb-4">
          <select
            value={agentFilter}
            onChange={(e) => setAgentFilter(e.target.value)}
            className="input-soft text-xs"
          >
            <option value="">all agents</option>
            {agents.map((a) => <option key={a} value={a}>{a}</option>)}
          </select>
          {firstErrorIdx >= 0 && (
            <button
              type="button"
              className="btn-ghost text-xs inline-flex items-center gap-1 text-red-500"
              onClick={() => document
                .getElementById(`traj-${firstErrorIdx}`)
                ?.scrollIntoView({ behavior: 'smooth', block: 'center' })}
            >
              <IconAlertTriangle size={13} /> Jump to first error
            </button>
          )}
          <span className="flex-1" />
          <button type="button" className="btn-ghost text-xs inline-flex items-center gap-1"
                  onClick={() => void reload()}>
            <IconRefresh size={13} /> Refresh
          </button>
        </div>

        {error && <div className="text-xs text-red-500 mb-3">{error}</div>}
        {shown.length === 0 && !error && (
          <div className="text-xs text-ink-faint italic">No events recorded yet.</div>
        )}

        <ol className="relative border-l border-line ml-2 space-y-1.5">
          {shown.map((n, i) => (
            <li key={i} id={`traj-${i}`} className="ml-4">
              <span className={clsx(
                'absolute -left-[5px] mt-1.5 w-2.5 h-2.5 rounded-full border-2 border-bg',
                n.category === 'error' ? 'bg-red-500' :
                n.category === 'message' ? 'bg-ink' : 'bg-ink-faint',
              )} />
              <button
                type="button"
                onClick={() => setExpanded(expanded === i ? null : i)}
                className="w-full text-left py-1 group"
              >
                <div className="flex items-center gap-2">
                  <span className={clsx('text-[9px] px-1.5 py-0.5 rounded-full shrink-0',
                                        CATEGORY_TONE[n.category] ?? CATEGORY_TONE.other)}>
                    {n.category}
                  </span>
                  <span className="text-[10px] text-ink-faint font-mono shrink-0">
                    {n.agent_id}
                  </span>
                  <span className="text-xs text-ink truncate group-hover:text-clip">
                    {n.title || n.type}
                  </span>
                </div>
                {expanded === i && (
                  <pre className="mt-1.5 text-[10px] font-mono text-ink-muted bg-surface-2 rounded-soft p-2 overflow-x-auto max-h-64">
                    {JSON.stringify(n.payload, null, 2)}
                  </pre>
                )}
              </button>
            </li>
          ))}
        </ol>
      </div>
    </div>
  )
}
