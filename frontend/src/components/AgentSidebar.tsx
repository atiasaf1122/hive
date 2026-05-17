import { useSessionsStore } from '../stores/sessions'

interface Props {
  sessionId: string
  agentId: string | null
}

export function AgentSidebar({ sessionId, agentId }: Props) {
  const agent = useSessionsStore((s) =>
    agentId ? s.sessions[sessionId]?.agents[agentId] : null
  )

  if (!agent) {
    return (
      <div className="flex items-center justify-center h-full text-gray-600 text-sm">
        Click an agent node to inspect it
      </div>
    )
  }

  const statusColor: Record<string, string> = {
    running: 'text-green-400',
    completed: 'text-gray-500',
    failed: 'text-red-400',
    idle: 'text-gray-600',
    waiting: 'text-yellow-400',
  }

  return (
    <div className="p-4 h-full overflow-y-auto">
      <div className="mb-4">
        <div className="text-white font-medium">{agent.role}</div>
        <div className="text-xs text-gray-500">{agent.agent_id}</div>
        <div className={`text-xs mt-1 ${statusColor[agent.status] ?? 'text-gray-500'}`}>
          {agent.status} — {agent.currentActivity}
        </div>
      </div>

      <div className="grid grid-cols-3 gap-2 mb-4">
        <div className="bg-gray-800 rounded p-2 text-center">
          <div className="text-xs text-gray-500">In</div>
          <div className="text-white text-sm">{agent.inputTokens.toLocaleString()}</div>
        </div>
        <div className="bg-gray-800 rounded p-2 text-center">
          <div className="text-xs text-gray-500">Out</div>
          <div className="text-white text-sm">{agent.outputTokens.toLocaleString()}</div>
        </div>
        <div className="bg-gray-800 rounded p-2 text-center">
          <div className="text-xs text-gray-500">Cost</div>
          <div className="text-white text-sm">${agent.costUsd.toFixed(4)}</div>
        </div>
      </div>

      <div>
        <div className="text-xs text-gray-500 uppercase tracking-wider mb-2">Live output</div>
        <div className="bg-gray-900 rounded p-2 font-mono text-xs text-gray-300 h-48 overflow-y-auto">
          {agent.eventLog.length === 0 ? (
            <span className="text-gray-700">No output yet</span>
          ) : (
            agent.eventLog.join('')
          )}
        </div>
      </div>
    </div>
  )
}
