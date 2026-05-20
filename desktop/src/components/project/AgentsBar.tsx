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
import { IconHexagon, IconPlayerPauseFilled } from '@tabler/icons-react'
import clsx from 'clsx'
import type { AgentInfo } from '../../lib/types'

interface Props {
  orchestratorModel?: string
  agents: AgentInfo[]
  activity: Record<string, string>
  costUsd?: number
  onPauseAll?: () => void
}

function ColorFor(role: string): string {
  // Stable hue per role name — keeps repeated Builders in the same colour.
  const palette = ['#F59E0B', '#10B981', '#3B82F6', '#A855F7', '#EF4444', '#EC4899', '#06B6D4']
  let hash = 0
  for (let i = 0; i < role.length; i++) hash = (hash * 17 + role.charCodeAt(i)) >>> 0
  return palette[hash % palette.length]
}

function AgentPill({ agent, activity }: { agent: AgentInfo; activity?: string }) {
  const passive = agent.status === 'idle' && !activity
  const running = agent.status === 'running'
  const colour = ColorFor(agent.role)

  return (
    <div
      className={clsx(
        'flex items-center gap-2.5 min-w-[180px] max-w-[260px] px-3 py-2 rounded-soft bg-surface border border-line',
        passive && 'opacity-55',
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
        </div>
        <div className="text-[11px] text-ink-muted truncate">
          {activity || (passive ? 'passive' : '—')}
        </div>
        <div className="text-[10px] text-ink-faint truncate">{agent.model}</div>
      </div>
    </div>
  )
}

export function AgentsBar({
  orchestratorModel = 'claude:opus',
  agents,
  activity,
  costUsd,
  onPauseAll,
}: Props) {
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
          agents.map((a) => <AgentPill key={a.agent_id} agent={a} activity={activity[a.agent_id]} />)
        )}
      </div>

      <div className="flex items-center gap-3 shrink-0 ml-auto">
        {typeof costUsd === 'number' && (
          <div className="text-xs text-ink-muted">${costUsd.toFixed(4)}</div>
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
