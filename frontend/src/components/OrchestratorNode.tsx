import { memo } from 'react'
import { Handle, Position } from '@xyflow/react'

interface OrchestratorNodeData {
  status: string
}

export const OrchestratorNode = memo(({ data }: { data: OrchestratorNodeData }) => {
  const isActive = data.status === 'running' || data.status === 'planning' || data.status === 'spawning'

  return (
    <div className="relative flex items-center justify-center w-16 h-16">
      {isActive && (
        <div className="absolute inset-0 rounded-full border-2 border-violet-500 animate-spin [animation-duration:3s] opacity-60" />
      )}
      <div className={`w-12 h-12 rounded-full flex items-center justify-center text-xl border-2 ${
        isActive ? 'bg-violet-900 border-violet-500' : 'bg-gray-800 border-gray-600'
      }`}>
        🐝
      </div>
      <Handle type="source" position={Position.Bottom} className="!bg-violet-600 !border-violet-400" />
    </div>
  )
})
OrchestratorNode.displayName = 'OrchestratorNode'
