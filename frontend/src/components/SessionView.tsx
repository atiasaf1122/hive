import { useState, useEffect } from 'react'
import { useSessionsStore } from '../stores/sessions'
import { connectSession } from '../ws'
import { TreeCanvas } from './TreeCanvas'
import { EventLog } from './EventLog'
import { AgentSidebar } from './AgentSidebar'
import { ApprovalModal } from './ApprovalModal'
import { InputSection } from './InputSection'
import type { WSEvent } from '../types'

interface Props {
  sessionId: string
}

export function SessionView({ sessionId }: Props) {
  const { sessions, handleWsEvent } = useSessionsStore()
  const session = sessions[sessionId]
  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(null)

  useEffect(() => {
    if (!session) return
    if (['completed', 'failed', 'cancelled'].includes(session.status)) return

    const disconnect = connectSession(sessionId, (event) => {
      handleWsEvent(sessionId, event as unknown as WSEvent)
    })
    return disconnect
  }, [sessionId, session?.status]) // eslint-disable-line react-hooks/exhaustive-deps

  if (!session) {
    return (
      <div className="flex items-center justify-center h-full text-gray-600">
        Session not found
      </div>
    )
  }

  return (
    <div className="flex h-full overflow-hidden relative">
      <div className="flex-1 flex flex-col overflow-hidden">
        <div className="flex-1 relative">
          <TreeCanvas sessionId={sessionId} onAgentClick={setSelectedAgentId} />
        </div>

        <div className="h-32 border-t border-gray-800 bg-gray-950">
          <EventLog sessionId={sessionId} />
        </div>

        <InputSection sessionId={sessionId} />
      </div>

      <div className="w-64 border-l border-gray-800 bg-gray-950 flex flex-col shrink-0">
        <div className="px-3 py-2 border-b border-gray-800 text-xs text-gray-500 flex items-center justify-between">
          <span>{session.name.slice(0, 30)}</span>
          <span className="text-gray-600">{session.id}</span>
        </div>
        <div className="flex-1 overflow-hidden">
          <AgentSidebar sessionId={sessionId} agentId={selectedAgentId} />
        </div>
      </div>

      {session.interrupt && <ApprovalModal sessionId={sessionId} />}
    </div>
  )
}
