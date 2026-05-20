/**
 * Sessions store — list of HIVE sessions fetched from the backend, plus per-session
 * live state (conversation, agents, pending interrupt) populated from the WebSocket.
 *
 * Important: this store deliberately tracks *current* state only. The backend is the
 * source of truth — on reload we re-fetch.
 */
import { create } from 'zustand'
import { api } from '../lib/api'
import type {
  AgentInfo,
  ConversationEntry,
  InterruptPayload,
  SessionInfo,
  SessionStatus,
  TeamComposition,
  WSEvent,
} from '../lib/types'

/* ── Per-session UI state ────────────────────────────────────────────── */

export interface ProjectState {
  info: SessionInfo
  agents: Record<string, AgentInfo>
  history: ConversationEntry[]
  interrupt: InterruptPayload | null
  /** Latest team_composition payload (used for inline activity card). */
  team: TeamComposition | null
  /** Live activity strings keyed by agent_id — drives the agents bar. */
  activity: Record<string, string>
  /** Per-event log (raw WS events) for debugging. Capped at 200 entries. */
  events: WSEvent[]
}

interface State {
  /** All sessions known to the backend, keyed by id. */
  sessions: Record<string, ProjectState>
  /** Loading state for the initial GET /api/sessions. */
  loading: boolean
  /** True after first successful fetch — used to distinguish "no projects" from "loading". */
  loaded: boolean
}

interface Actions {
  fetchSessions: () => Promise<void>
  upsertSession: (info: SessionInfo) => void
  applyWsEvent: (sessionId: string, event: WSEvent) => void
  appendUserMessage: (sessionId: string, text: string) => void
  setInterrupt: (sessionId: string, payload: InterruptPayload | null) => void
  removeSession: (sessionId: string) => void
}

export const useSessions = create<State & Actions>((set, get) => ({
  sessions: {},
  loading: false,
  loaded: false,

  fetchSessions: async () => {
    set({ loading: true })
    try {
      const rows = await api.get<SessionInfo[]>('/api/sessions')
      const next: Record<string, ProjectState> = { ...get().sessions }
      for (const info of rows) {
        const prior = next[info.session_id]
        next[info.session_id] = prior
          ? { ...prior, info: { ...prior.info, ...info } }
          : blankProject(info)
      }
      set({ sessions: next, loaded: true })
    } finally {
      set({ loading: false })
    }
  },

  upsertSession: (info) => {
    set((s) => {
      const prior = s.sessions[info.session_id]
      const next: ProjectState = prior
        ? { ...prior, info: { ...prior.info, ...info } }
        : blankProject(info)
      return { sessions: { ...s.sessions, [info.session_id]: next } }
    })
  },

  appendUserMessage: (sessionId, text) => {
    set((s) => {
      const proj = s.sessions[sessionId]
      if (!proj) return s
      const entry: ConversationEntry = { role: 'user', content: text, ts: Date.now() / 1000 }
      return {
        sessions: {
          ...s.sessions,
          [sessionId]: { ...proj, history: [...proj.history, entry] },
        },
      }
    })
  },

  setInterrupt: (sessionId, payload) => {
    set((s) => {
      const proj = s.sessions[sessionId]
      if (!proj) return s
      return {
        sessions: {
          ...s.sessions,
          [sessionId]: { ...proj, interrupt: payload },
        },
      }
    })
  },

  removeSession: (sessionId) => {
    set((s) => {
      const next = { ...s.sessions }
      delete next[sessionId]
      return { sessions: next }
    })
  },

  applyWsEvent: (sessionId, ev) => {
    set((s) => {
      const proj = s.sessions[sessionId]
      if (!proj) return s

      // Always log the event (capped)
      const events = [...proj.events, ev].slice(-200)
      let info: SessionInfo = proj.info
      let agents = proj.agents
      let history = proj.history
      let interrupt: InterruptPayload | null = proj.interrupt
      let team: TeamComposition | null = proj.team
      let activity = proj.activity

      const updateStatus = (status: SessionStatus) => {
        info = { ...info, status }
      }

      switch (ev.type) {
        case 'session_start':
          updateStatus('starting')
          break
        case 'orchestrator_thinking':
          updateStatus('planning')
          break
        case 'orchestrator_decision':
          if (ev.team_composition) team = ev.team_composition
          break
        case 'orchestrator_response':
          if (ev.text) {
            history = [...history, { role: 'assistant', content: ev.text, ts: Date.now() / 1000 }]
          }
          break
        case 'spawn_complete':
          updateStatus('running')
          if (Array.isArray(ev.agents)) {
            agents = { ...agents }
            for (const a of ev.agents) {
              agents[a.agent_id] = { ...a, status: 'idle' }
            }
          }
          break
        case 'awaiting_user':
          updateStatus('awaiting_user')
          interrupt = null
          if (ev.last_response) {
            // assistant message may already have been pushed via orchestrator_response;
            // skip duplicates (last entry is the same content)
            const last = history[history.length - 1]
            if (!last || last.content !== ev.last_response) {
              history = [...history, { role: 'assistant', content: ev.last_response, ts: Date.now() / 1000 }]
            }
          }
          break
        case 'interrupt':
          if (ev.payload?.type === 'team_approval') {
            updateStatus('waiting_approval')
            interrupt = ev.payload
          }
          break
        case 'session_closed':
        case 'session_end':
          updateStatus('closed')
          interrupt = null
          break
        case 'session_error': {
          updateStatus('failed')
          // Surface the failure inline in the chat so the user actually sees it.
          const msg = ev.error
            ? `Session failed: ${ev.error}`
            : 'Session failed with no error message.'
          history = [
            ...history,
            { role: 'system', content: msg, ts: Date.now() / 1000 },
          ]
          break
        }
        case 'agent/start': {
          const id = ev.agent_id
          if (id) {
            agents = { ...agents, [id]: { ...(agents[id] ?? { agent_id: id, role: 'Worker', model: '', status: 'running' }), status: 'running' } }
            activity = { ...activity, [id]: 'Starting…' }
          }
          break
        }
        case 'text/delta': {
          const id = ev.agent_id
          if (id && ev.text) {
            activity = { ...activity, [id]: ev.text.slice(0, 80) }
          }
          break
        }
        case 'agent/end': {
          const id = ev.agent_id
          if (id) {
            agents = { ...agents, [id]: { ...(agents[id] ?? { agent_id: id, role: 'Worker', model: '', status: 'completed' }), status: 'completed' } }
            activity = { ...activity, [id]: 'Done' }
          }
          break
        }
        case 'agent/error': {
          const id = ev.agent_id
          if (id) {
            agents = { ...agents, [id]: { ...(agents[id] ?? { agent_id: id, role: 'Worker', model: '', status: 'failed' }), status: 'failed' } }
            activity = { ...activity, [id]: ev.error ?? 'Error' }
            // Push the failure into chat too — a red agent pill on its own
            // is too easy to miss.
            const summary = `${id} failed: ${ev.error || 'unknown error'}`
            history = [
              ...history,
              { role: 'system', content: summary, ts: Date.now() / 1000 },
            ]
          }
          break
        }
      }

      return {
        sessions: {
          ...s.sessions,
          [sessionId]: { info, agents, history, interrupt, team, activity, events },
        },
      }
    })
  },
}))

function blankProject(info: SessionInfo): ProjectState {
  return {
    info,
    agents: {},
    history: [],
    interrupt: null,
    team: null,
    activity: {},
    events: [],
  }
}
