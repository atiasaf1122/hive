/**
 * Project view — the chat-style page for one open session.
 *
 *   ┌─ TabBar (browser tabs across all open projects) ──────────────────┐
 *   ├─ AgentsBar (orchestrator pill + sub-agent pills + cost) ──────────┤
 *   │  Chat                                                              │
 *   ├─ Composer (with slash menu) ──────────────────────────────────────┤
 *
 * On mount: fetch /api/sessions/:id + /history, then subscribe to /ws/:id.
 * The WS listener pushes events through useSessions.applyWsEvent.
 */
import { IconBookmarkPlus, IconShield, IconX } from '@tabler/icons-react'
import { useEffect, useMemo, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { TabBar } from '../components/project/TabBar'
import { AgentsBar } from '../components/project/AgentsBar'
import { AgentDrillDown } from '../components/project/AgentDrillDown'
import { Chat } from '../components/project/Chat'
import { Composer } from '../components/project/Composer'
import { SafetyOverrideModal } from '../components/project/SafetyOverrideModal'
import { api } from '../lib/api'
import { toast } from '../lib/toast'
import { subscribeSession } from '../lib/ws'
import type { ConversationEntry, SessionInfo } from '../lib/types'
import { useProjectTabs } from '../stores/projectTabs'
import { useSessions } from '../stores/sessions'
import { useTemplates } from '../stores/templates'

export function ProjectView() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()

  const project = useSessions((s) => (id ? s.sessions[id] : undefined))
  const upsert = useSessions((s) => s.upsertSession)
  const applyWsEvent = useSessions((s) => s.applyWsEvent)
  const openTab = useProjectTabs((s) => s.openTab)
  const closeTab = useProjectTabs((s) => s.closeTab)
  const saveTemplate = useTemplates((s) => s.save)
  const [safetyOpen, setSafetyOpen] = useState(false)
  // Which agent's drill-down panel is open (null = closed).
  const [drillAgent, setDrillAgent] = useState<string | null>(null)

  // Make sure this session is in the tabs row.
  useEffect(() => {
    if (id) openTab(id)
  }, [id, openTab])

  // Initial fetch + history.
  useEffect(() => {
    if (!id) return
    let cancelled = false

    async function load() {
      try {
        const info = await api.get<SessionInfo>(`/api/sessions/${id}`)
        if (!cancelled) upsert(info)
      } catch (err) {
        console.error('failed to load session', err)
        toast.error(
          `Couldn't load this project. ${err instanceof Error ? err.message : ''}`.trim(),
        )
      }
      try {
        const { history } = await api.get<{ history: ConversationEntry[] }>(
          `/api/sessions/${id}/history`,
        )
        if (cancelled) return
        useSessions.setState((s) => {
          const proj = s.sessions[id!]
          if (!proj) return s
          return {
            sessions: {
              ...s.sessions,
              [id!]: { ...proj, history },
            },
          }
        })
      } catch {
        /* no history yet */
      }
    }
    void load()
    return () => {
      cancelled = true
    }
  }, [id, upsert])

  // Live WS subscription for the visible session.
  useEffect(() => {
    if (!id) return
    const sub = subscribeSession(id, (ev) => applyWsEvent(id, ev))
    return () => sub.close()
  }, [id, applyWsEvent])

  const agentArray = useMemo(
    () => (project ? Object.values(project.agents) : []),
    [project],
  )
  // Reads the store's monotonic accumulator (not the events buffer)
  // so the displayed total doesn't regress when older system/cost
  // events fall off the 200-entry ring.
  const totalCost = project?.totalCostUsd ?? 0

  if (!id) {
    return <div className="p-8 text-ink-muted">No project selected.</div>
  }
  if (!project) {
    return (
      <div className="h-full flex flex-col">
        <TabBar />
        <div className="flex-1 flex items-center justify-center text-ink-muted text-sm">
          Loading session…
        </div>
      </div>
    )
  }

  const status = project.info.status
  const isTerminal = ['closed', 'failed', 'cancelled'].includes(status)

  async function close() {
    try {
      await api.post(`/api/sessions/${id}/close`)
    } catch (err) {
      console.error('close failed', err)
      toast.error(
        `Couldn't close the session. ${err instanceof Error ? err.message : ''}`.trim(),
      )
    }
  }

  async function cancel() {
    if (!id) return
    try {
      await api.post(`/api/sessions/${id}/cancel`)
    } catch (err) {
      console.error('cancel failed', err)
      toast.error(
        `Couldn't cancel the session. ${err instanceof Error ? err.message : ''}`.trim(),
      )
    }
  }

  // "Cancel" is meaningful only when the session is actively working —
  // mid-orchestration, spawning, or running agents. Once it's parked
  // (awaiting_user) or done, Stop turns into a no-op. 'spawning' was
  // missing here so Stop was disabled in the seconds between
  // team_approval and spawn_complete — fixed.
  const cancelEnabled = [
    'starting', 'planning', 'spawning', 'running', 'waiting_approval',
  ].includes(status)

  function saveAsTemplate() {
    if (!project) return
    const name = prompt('Template name?', project.info.name) || project.info.name
    if (!name) return
    saveTemplate({
      name: name.slice(0, 60),
      task: project.info.name,
      model: 'claude:sonnet',
      approval_mode: project.info.approval_mode || 'full-auto',
      emoji: '🪴',
    })
  }

  return (
    <div className="h-full flex flex-col">
      <TabBar />

      {/* Project header strip — title + status + actions */}
      <div className="flex items-center gap-3 px-6 py-3 border-b border-line bg-bg">
        <div className="min-w-0 flex-1">
          <div className="text-ink text-sm font-medium truncate">
            {project.info.name || 'Untitled project'}
          </div>
          <div className="text-xs text-ink-faint truncate">
            {id} · {project.info.approval_mode} · {status}
          </div>
        </div>
        <button
          type="button"
          onClick={() => setSafetyOpen(true)}
          className="text-xs text-ink-muted hover:text-ink inline-flex items-center gap-1 px-2.5 py-1.5 rounded-soft border border-line hover:bg-surface-2"
          title="Per-project safety limits"
        >
          <IconShield size={14} strokeWidth={1.75} />
          Safety
        </button>
        <button
          type="button"
          onClick={saveAsTemplate}
          className="text-xs text-ink-muted hover:text-ink inline-flex items-center gap-1 px-2.5 py-1.5 rounded-soft border border-line hover:bg-surface-2"
          title="Save this configuration as a template"
        >
          <IconBookmarkPlus size={14} strokeWidth={1.75} />
          Save as template
        </button>
        {!isTerminal && (
          <button
            type="button"
            onClick={() => {
              if (confirm('Close this project? The orchestrator and agents will stop.')) {
                void close()
                if (id) closeTab(id)
                navigate('/')
              }
            }}
            className="text-xs text-red-500 hover:text-red-400 inline-flex items-center gap-1 px-2.5 py-1.5 rounded-soft border border-red-500/30 hover:bg-red-500/10"
          >
            <IconX size={14} strokeWidth={1.75} />
            Close project
          </button>
        )}
      </div>

      <AgentsBar
        agents={agentArray}
        activity={project.activity}
        costUsd={totalCost}
        onAgentClick={(aid) => setDrillAgent(aid)}
        onCancel={() => void cancel()}
        cancelEnabled={cancelEnabled}
      />

      {drillAgent && (
        <AgentDrillDown
          agentId={drillAgent}
          events={project.events}
          onClose={() => setDrillAgent(null)}
        />
      )}

      <Chat
        sessionId={id}
        history={project.history}
        team={project.team}
        agents={agentArray}
        interrupt={project.interrupt}
        plannerLog={project.plannerLog}
        stallHint={project.stallHint}
      />

      <Composer sessionId={id} disabled={isTerminal} />

      <SafetyOverrideModal
        sessionId={id}
        open={safetyOpen}
        onClose={() => setSafetyOpen(false)}
      />
    </div>
  )
}
