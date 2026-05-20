import { create } from 'zustand'
import type { Agent, InterruptPayload, Session, SessionStatus, WSEvent } from '../types'

interface ApiSession {
  session_id: string
  name: string
  status: string
  approval_mode: string
  created_at: string
}

interface SessionsStore {
  sessions: Record<string, Session>
  activeSessionId: string | null

  addSession: (id: string, name: string, approvalMode: string) => void
  setActiveSession: (id: string | null) => void
  setSessionStatus: (id: string, status: SessionStatus) => void
  handleWsEvent: (sessionId: string, event: WSEvent) => void
  loadFromApi: (sessions: ApiSession[]) => void
}

export const useSessionsStore = create<SessionsStore>((set) => ({
  sessions: {},
  activeSessionId: null,

  addSession: (id, name, approvalMode) =>
    set((s) => ({
      sessions: {
        ...s.sessions,
        [id]: {
          id,
          name,
          status: 'starting',
          approvalMode,
          agents: {},
          events: [],
          interrupt: null,
          createdAt: new Date().toISOString(),
          totalCost: 0,
          textOutput: '',
        },
      },
    })),

  setActiveSession: (id) => set({ activeSessionId: id }),

  setSessionStatus: (id, status) =>
    set((s) => ({
      sessions: s.sessions[id]
        ? { ...s.sessions, [id]: { ...s.sessions[id], status } }
        : s.sessions,
    })),

  loadFromApi: (apiSessions) =>
    set((s) => {
      const updated = { ...s.sessions }
      for (const apiS of apiSessions) {
        if (!updated[apiS.session_id]) {
          updated[apiS.session_id] = {
            id: apiS.session_id,
            name: apiS.name,
            status: apiS.status as SessionStatus,
            approvalMode: apiS.approval_mode || 'full-auto',
            agents: {},
            events: [],
            interrupt: null,
            createdAt: apiS.created_at || '',
            totalCost: 0,
            textOutput: '',
          }
        }
      }
      return { sessions: updated }
    }),

  handleWsEvent: (sessionId, event) =>
    set((s) => {
      const session = s.sessions[sessionId]
      if (!session) return s

      const updated: Session = { ...session, events: [...session.events, event] }

      switch (event.type) {
        case 'session_start':
          updated.status = 'planning'
          break

        case 'orchestrator_thinking':
          updated.status = 'planning'
          break

        case 'orchestrator_decision':
        case 'orchestrator_response':
          // Orchestrator either answered directly or queued a team.
          break

        case 'awaiting_user':
          updated.status = 'awaiting_user'
          break

        case 'session_closed':
          updated.status = 'closed'
          updated.interrupt = null
          break

        case 'plan_complete':
          updated.status = 'spawning'
          break

        case 'spawn_complete': {
          updated.status = 'running'
          const agentsList = (event.agents as Array<{ agent_id: string; role: string; model: string }> | undefined) ?? []
          updated.agents = { ...updated.agents }
          for (const a of agentsList) {
            updated.agents[a.agent_id] = {
              agent_id: a.agent_id,
              role: a.role,
              model: a.model,
              status: 'idle',
              currentActivity: '',
              inputTokens: 0,
              outputTokens: 0,
              costUsd: 0,
              eventLog: [],
            }
          }
          break
        }

        case 'interrupt':
          updated.status = 'waiting_approval'
          updated.interrupt = event.payload as InterruptPayload
          break

        case 'agent/start': {
          const agentId = event.agent_id!
          updated.agents = {
            ...updated.agents,
            [agentId]: {
              ...(updated.agents[agentId] ?? makeDefaultAgent(agentId)),
              status: 'running',
              currentActivity: 'Starting…',
            },
          }
          break
        }

        case 'text/delta': {
          const agentId = event.agent_id!
          const existing = updated.agents[agentId]
          if (existing) {
            updated.agents = {
              ...updated.agents,
              [agentId]: {
                ...existing,
                status: 'running',
                currentActivity: (event.text ?? '').slice(0, 80),
                eventLog: [...existing.eventLog, event.text ?? ''].slice(-500),
              },
            }
          }
          break
        }

        case 'agent/end': {
          const agentId = event.agent_id!
          if (updated.agents[agentId]) {
            updated.agents = {
              ...updated.agents,
              [agentId]: { ...updated.agents[agentId], status: 'completed', currentActivity: 'Done' },
            }
          }
          break
        }

        case 'agent/error': {
          const agentId = event.agent_id!
          const existing = updated.agents[agentId]
          updated.agents = {
            ...updated.agents,
            [agentId]: {
              ...(existing ?? makeDefaultAgent(agentId)),
              status: 'failed',
              currentActivity: event.error ?? 'Error',
            },
          }
          break
        }

        case 'system/cost': {
          const agentId = event.agent_id!
          updated.totalCost += event.cost_usd ?? 0
          if (updated.agents[agentId]) {
            updated.agents = {
              ...updated.agents,
              [agentId]: {
                ...updated.agents[agentId],
                inputTokens: event.input_tokens ?? 0,
                outputTokens: event.output_tokens ?? 0,
                costUsd: event.cost_usd ?? 0,
              },
            }
          }
          break
        }

        case 'session_end':
          updated.status = (event.status as SessionStatus) ?? 'completed'
          updated.textOutput = event.text_output ?? ''
          updated.totalCost += event.cost_usd ?? 0
          updated.interrupt = null
          break

        case 'session_error':
          updated.status = 'failed'
          updated.interrupt = null
          break
      }

      return { sessions: { ...s.sessions, [sessionId]: updated } }
    }),
}))

function makeDefaultAgent(agentId: string): Agent {
  return {
    agent_id: agentId,
    role: 'Worker',
    model: '',
    status: 'idle',
    currentActivity: '',
    inputTokens: 0,
    outputTokens: 0,
    costUsd: 0,
    eventLog: [],
  }
}
