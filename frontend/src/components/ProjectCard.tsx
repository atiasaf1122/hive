import { useSessionsStore } from '../stores/sessions'
import type { Session } from '../types'

const statusColor: Record<string, string> = {
  running: 'text-green-400',
  spawning: 'text-green-400',
  planning: 'text-blue-400',
  waiting_approval: 'text-yellow-400',
  completed: 'text-gray-500',
  failed: 'text-red-400',
  starting: 'text-blue-400',
  cancelled: 'text-gray-600',
}

const statusLabel: Record<string, string> = {
  running: 'Running',
  spawning: 'Spawning agents',
  planning: 'Planning',
  waiting_approval: 'Needs approval',
  completed: 'Completed',
  failed: 'Failed',
  starting: 'Starting',
  cancelled: 'Cancelled',
}

interface Props {
  session: Session
}

export function ProjectCard({ session }: Props) {
  const { setActiveSession } = useSessionsStore()
  const agentCount = Object.keys(session.agents).length
  const runningCount = Object.values(session.agents).filter((a) => a.status === 'running').length

  return (
    <button
      onClick={() => setActiveSession(session.id)}
      className="bg-gray-900 border border-gray-800 hover:border-gray-700 rounded-lg p-4 text-left transition-colors w-full"
    >
      <div className="flex items-start justify-between mb-2">
        <div className="text-sm text-white font-medium leading-tight pr-2 line-clamp-2">
          {session.name}
        </div>
        <span className={`text-xs shrink-0 ${statusColor[session.status] ?? 'text-gray-500'}`}>
          {statusLabel[session.status] ?? session.status}
        </span>
      </div>

      <div className="flex items-center gap-3 text-xs text-gray-500">
        <span>ID: {session.id}</span>
        {agentCount > 0 && (
          <span>
            {runningCount > 0 ? `${runningCount}/${agentCount}` : agentCount} agents
          </span>
        )}
        {session.totalCost > 0 && (
          <span className="ml-auto">${session.totalCost.toFixed(4)}</span>
        )}
      </div>

      {session.status === 'waiting_approval' && (
        <div className="mt-2 text-xs text-yellow-400 bg-yellow-400/10 rounded px-2 py-1">
          ⚠ Waiting for approval
        </div>
      )}
    </button>
  )
}
