import { memo } from 'react'
import { Handle, Position } from '@xyflow/react'
import type { AgentStatus } from '../types'

const roleEmoji: Record<string, string> = {
  Thinker: '🧠',
  Builder: '🔨',
  Tester: '🧪',
  Debugger: '🐛',
  Reviewer: '👁',
  Researcher: '🔍',
  Writer: '✏️',
  Editor: '📝',
  Worker: '⚙️',
}

const statusRing: Record<AgentStatus, string> = {
  idle: 'border-gray-700',
  running: 'border-violet-500 shadow-lg shadow-violet-500/20',
  completed: 'border-green-700',
  failed: 'border-red-500',
  waiting: 'border-yellow-500',
}

interface AgentNodeData {
  agent_id: string
  role: string
  model: string
  status: AgentStatus
  currentActivity: string
}

export const AgentNode = memo(({ data }: { data: AgentNodeData }) => {
  const emoji = roleEmoji[data.role] ?? '⚙️'

  return (
    <div
      className={`bg-gray-900 border-2 rounded-lg px-3 py-2 w-36 cursor-pointer select-none transition-all ${statusRing[data.status] ?? 'border-gray-700'}`}
    >
      <Handle type="target" position={Position.Top} className="!bg-gray-700 !border-gray-600" />
      <div className="flex items-center gap-2 mb-1">
        <span className="text-base">{emoji}</span>
        <span className="text-xs font-medium text-white truncate">{data.role}</span>
      </div>
      <div className="text-xs text-gray-500 truncate h-4">{data.currentActivity || data.model}</div>
      {data.status === 'running' && (
        <div className="mt-1.5 h-0.5 bg-gray-800 rounded overflow-hidden">
          <div className="h-full bg-violet-500 animate-[shimmer_1.5s_ease-in-out_infinite] w-1/2" />
        </div>
      )}
      <Handle type="source" position={Position.Bottom} className="!bg-gray-700 !border-gray-600" />
    </div>
  )
})
AgentNode.displayName = 'AgentNode'
