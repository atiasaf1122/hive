import { useMemo, useCallback } from 'react'
import {
  ReactFlow,
  Background,
  Controls,
  type Node,
  type Edge,
  type NodeTypes,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import dagre from 'dagre'
import { AgentNode } from './AgentNode'
import { OrchestratorNode } from './OrchestratorNode'
import { useSessionsStore } from '../stores/sessions'
import type { Session } from '../types'

const nodeTypes: NodeTypes = {
  agent: AgentNode as NodeTypes[string],
  orchestrator: OrchestratorNode as NodeTypes[string],
}

function buildLayout(session: Session): { nodes: Node[]; edges: Edge[] } {
  const agents = Object.values(session.agents)
  const g = new dagre.graphlib.Graph()
  g.setDefaultEdgeLabel(() => ({}))
  g.setGraph({ rankdir: 'TB', ranksep: 80, nodesep: 50 })

  g.setNode('orchestrator', { width: 64, height: 64 })
  for (const a of agents) {
    g.setNode(a.agent_id, { width: 144, height: 72 })
    g.setEdge('orchestrator', a.agent_id)
  }
  dagre.layout(g)

  const orch = g.node('orchestrator')
  const nodes: Node[] = [
    {
      id: 'orchestrator',
      type: 'orchestrator',
      position: { x: orch.x - 32, y: orch.y - 32 },
      data: { status: session.status },
    },
    ...agents.map((a) => {
      const n = g.node(a.agent_id)
      return {
        id: a.agent_id,
        type: 'agent',
        position: { x: n.x - 72, y: n.y - 36 },
        data: a as unknown as Record<string, unknown>,
      }
    }),
  ]

  const edges: Edge[] = agents.map((a) => ({
    id: `e-${a.agent_id}`,
    source: 'orchestrator',
    target: a.agent_id,
    animated: a.status === 'running',
    style: { stroke: a.status === 'running' ? '#8b5cf6' : '#374151', strokeWidth: 1.5 },
  }))

  return { nodes, edges }
}

interface Props {
  sessionId: string
  onAgentClick?: (agentId: string) => void
}

export function TreeCanvas({ sessionId, onAgentClick }: Props) {
  const session = useSessionsStore((s) => s.sessions[sessionId])

  const { nodes, edges } = useMemo(() => {
    if (!session) return { nodes: [], edges: [] }
    return buildLayout(session)
  }, [session])

  const onNodeClick = useCallback(
    (_: React.MouseEvent, node: Node) => {
      if (node.id !== 'orchestrator' && onAgentClick) {
        onAgentClick(node.id)
      }
    },
    [onAgentClick],
  )

  if (!session) return null

  const hasAgents = Object.keys(session.agents).length > 0
  const isEarlyStage = ['starting', 'planning'].includes(session.status)

  return (
    <div className="w-full h-full bg-gray-950 relative">
      {!hasAgents && (
        <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
          <div className="text-center text-gray-600">
            <div className="text-3xl mb-2">🐝</div>
            <div className="text-sm">
              {isEarlyStage ? 'Planning team composition…' : 'No agents spawned yet'}
            </div>
          </div>
        </div>
      )}
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        onNodeClick={onNodeClick}
        fitView
        fitViewOptions={{ padding: 0.3 }}
        minZoom={0.3}
        maxZoom={2}
        proOptions={{ hideAttribution: true }}
      >
        <Background color="#1e1e2e" gap={24} />
        <Controls className="!bg-gray-900 !border-gray-800 !text-gray-400" />
      </ReactFlow>
    </div>
  )
}
