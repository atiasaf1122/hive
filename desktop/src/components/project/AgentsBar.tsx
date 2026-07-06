/**
 * Horizontal agents bar at the top of the project view.
 *
 *   Orchestrator pill (large, gradient)  │   Builder · writing models.py    Tester · idle    Debugger (passive)
 *                                        │
 *                                                              total cost · Pause all
 *
 * Live activity strings come from `text/delta` events handled by useSessions.
 * Passive agents render at 55% opacity. Active agents have a pulsing dot.
 */
import { IconHexagon, IconPlayerPauseFilled, IconPlayerStopFilled } from '@tabler/icons-react'
import clsx from 'clsx'
import { useEffect, useRef, useState } from 'react'
import { api } from '../../lib/api'
import type { AgentInfo } from '../../lib/types'

/** F0.2 — per-session cost breakdown by role (GET /api/cost/session/:id). */
interface RoleCost {
  role: string
  cost_usd: number
  input_tokens: number
  output_tokens: number
  calls: number
  local: boolean
}
interface CostBreakdown {
  session_id: string
  total_usd: number
  saved_via_local_usd: number
  by_role: RoleCost[]
}

interface Props {
  orchestratorModel?: string
  agents: AgentInfo[]
  activity: Record<string, string>
  costUsd?: number
  sessionId?: string
  onPauseAll?: () => void
  /** Open the agent's drill-down panel. Click an AgentPill to trigger. */
  onAgentClick?: (agentId: string) => void
  /** Cancel the current orchestrator turn / worker run. */
  onCancel?: () => void
  /** When true, the cancel button is the primary action (something's
   *  actually running). */
  cancelEnabled?: boolean
}

function ColorFor(role: string): string {
  // Stable hue per role name — keeps repeated Builders in the same colour.
  const palette = ['#F59E0B', '#10B981', '#3B82F6', '#A855F7', '#EF4444', '#EC4899', '#06B6D4']
  let hash = 0
  for (let i = 0; i < role.length; i++) hash = (hash * 17 + role.charCodeAt(i)) >>> 0
  return palette[hash % palette.length]
}

function AgentPill({
  agent,
  activity,
  onClick,
}: {
  agent: AgentInfo
  activity?: string
  onClick?: () => void
}) {
  const passive = agent.status === 'idle' && !activity
  const running = agent.status === 'running'
  const colour = ColorFor(agent.role)
  const Wrapper = onClick ? 'button' : 'div'
  // The pill lives inside an overflow-x-auto row; a trackpad horizontal
  // swipe across it would otherwise fire a click and accidentally open
  // the drill-down. Suppress the click if the pointer moved more than a
  // few px between down and up — the standard drag-vs-click heuristic.
  const downPosRef = useRef<{ x: number; y: number } | null>(null)
  const handlePointerDown = (e: React.PointerEvent) => {
    downPosRef.current = { x: e.clientX, y: e.clientY }
  }
  const handleClick = (e: React.MouseEvent) => {
    if (!onClick) return
    const down = downPosRef.current
    if (down) {
      const moved = Math.abs(e.clientX - down.x) + Math.abs(e.clientY - down.y)
      downPosRef.current = null
      if (moved > 8) return  // dragged, not clicked
    }
    onClick()
  }

  return (
    <Wrapper
      type={onClick ? 'button' : undefined}
      onClick={handleClick}
      onPointerDown={onClick ? handlePointerDown : undefined}
      title={onClick ? `${agent.role} — click for live log` : undefined}
      className={clsx(
        'flex items-center gap-2.5 min-w-[180px] max-w-[260px] px-3 py-2 rounded-soft bg-surface border border-line text-left',
        passive && 'opacity-55',
        onClick && 'cursor-pointer hover:bg-surface-2',
      )}
    >
      <div
        className="w-8 h-8 rounded-full flex items-center justify-center text-white text-xs font-medium shrink-0"
        style={{ background: colour }}
      >
        {agent.role[0]?.toUpperCase()}
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5">
          <span className={clsx('w-1.5 h-1.5 rounded-full shrink-0', running ? 'bg-emerald-400 animate-pulse' : 'bg-ink-faint/60')} />
          <span className="text-sm text-ink truncate">{agent.role}</span>
          {(agent.mcp_servers ?? []).map((s) => (
            <span key={s} className="text-[9px] px-1 py-0.5 rounded-full bg-sky-500/15 text-sky-500 shrink-0" title={`MCP: ${s}`}>
              🔌 {s}
            </span>
          ))}
        </div>
        <div className="text-[11px] text-ink-muted truncate">
          {activity || (passive ? 'passive' : '—')}
        </div>
        <div className="text-[10px] text-ink-faint truncate">{agent.model}</div>
      </div>
    </Wrapper>
  )
}

function CostPopover({ sessionId, onClose }: { sessionId: string; onClose: () => void }) {
  const [data, setData] = useState<CostBreakdown | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    api
      .get<CostBreakdown>(`/api/cost/session/${sessionId}`)
      .then(setData)
      .catch((e) => setError(e instanceof Error ? e.message : 'failed'))
  }, [sessionId])
  return (
    <div className="absolute right-0 top-8 z-30 w-72 card p-3 shadow-lg border border-line bg-bg">
      <div className="flex items-center justify-between mb-2">
        <div className="text-xs font-medium text-ink">Session cost</div>
        <button type="button" onClick={onClose} className="text-ink-faint text-xs hover:text-ink">✕</button>
      </div>
      {error && <div className="text-[11px] text-red-500">{error}</div>}
      {!data && !error && <div className="text-[11px] text-ink-faint">loading…</div>}
      {data && (
        <>
          <div className="space-y-1">
            {data.by_role.map((r) => (
              <div key={`${r.role}-${r.local}`} className="flex items-center justify-between text-[11px]">
                <span className="text-ink-muted truncate">
                  {r.local ? '🏠 ' : ''}{r.role} <span className="text-ink-faint">×{r.calls}</span>
                </span>
                <span className={r.local ? 'text-emerald-500' : 'text-ink'}>
                  {r.local ? '$0' : `$${r.cost_usd.toFixed(3)}`}
                </span>
              </div>
            ))}
          </div>
          <div className="flex items-center justify-between text-[11px] mt-2 pt-2 border-t border-line">
            <span className="text-ink font-medium">total</span>
            <span className="text-ink font-medium">${data.total_usd.toFixed(3)}</span>
          </div>
          {data.saved_via_local_usd > 0 && (
            <div className="text-[10px] text-emerald-500 mt-1">
              saved ~${data.saved_via_local_usd.toFixed(3)} via local (vs haiku)
            </div>
          )}
        </>
      )}
    </div>
  )
}

export function AgentsBar({
  orchestratorModel = 'claude:opus',
  agents,
  activity,
  costUsd,
  sessionId,
  onPauseAll,
  onAgentClick,
  onCancel,
  cancelEnabled,
}: Props) {
  const [showCosts, setShowCosts] = useState(false)
  return (
    <div className="flex items-center gap-3 px-6 py-3 border-b border-line bg-bg overflow-x-auto">
      {/* Orchestrator pill (always present, larger, gradient) */}
      <div className="flex items-center gap-3 min-w-[220px] pl-3 pr-4 py-2.5 rounded-xl2 bg-accent-gradient text-white shadow-sm shrink-0">
        <div className="w-9 h-9 rounded-full bg-white/15 flex items-center justify-center">
          <IconHexagon size={20} strokeWidth={1.5} />
        </div>
        <div>
          <div className="text-sm leading-tight">Orchestrator</div>
          <div className="text-[11px] opacity-80">{orchestratorModel}</div>
        </div>
      </div>

      <div className="w-px h-10 bg-line shrink-0" />

      <div className="flex items-center gap-2 flex-1 min-w-0 overflow-x-auto">
        {agents.length === 0 ? (
          <div className="text-xs text-ink-faint italic">No sub-agents yet — orchestrator may chat or spawn workers.</div>
        ) : (
          agents.map((a) => (
            <AgentPill
              key={a.agent_id}
              agent={a}
              activity={activity[a.agent_id]}
              onClick={onAgentClick ? () => onAgentClick(a.agent_id) : undefined}
            />
          ))
        )}
      </div>

      <div className="flex items-center gap-3 shrink-0 ml-auto relative">
        {typeof costUsd === 'number' && (
          <button
            type="button"
            onClick={() => sessionId && setShowCosts((v) => !v)}
            title="Cost breakdown by role"
            className={clsx('text-xs text-ink-muted', sessionId && 'hover:text-ink underline decoration-dotted')}
          >
            ${costUsd.toFixed(4)}
          </button>
        )}
        {showCosts && sessionId && (
          <CostPopover sessionId={sessionId} onClose={() => setShowCosts(false)} />
        )}
        {onCancel && (
          <button
            type="button"
            onClick={onCancel}
            disabled={!cancelEnabled}
            title={cancelEnabled ? 'Stop the current task' : 'Nothing to stop'}
            className={clsx(
              'text-xs inline-flex items-center gap-1 px-2 py-1 rounded-soft border transition-colors',
              cancelEnabled
                ? 'text-red-500 border-red-500/40 hover:bg-red-500/10'
                : 'text-ink-faint border-line opacity-50 cursor-not-allowed',
            )}
          >
            <IconPlayerStopFilled size={12} />
            Stop
          </button>
        )}
        {onPauseAll && agents.length > 0 && (
          <button
            type="button"
            onClick={onPauseAll}
            className="text-xs text-ink-muted hover:text-ink inline-flex items-center gap-1 px-2 py-1 rounded-soft border border-line hover:bg-surface-2"
          >
            <IconPlayerPauseFilled size={12} />
            Pause all
          </button>
        )}
      </div>
    </div>
  )
}
