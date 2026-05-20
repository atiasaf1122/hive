/**
 * Two action tiles below the quick start: "Continue recent" and "Schedule
 * automation". Both are flat cards that hover-lift; clicking either does
 * the obvious thing.
 */
import { IconClock, IconHistory, IconArrowRight } from '@tabler/icons-react'
import { useNavigate } from 'react-router-dom'
import { useSessions } from '../../stores/sessions'
import { useProjectTabs } from '../../stores/projectTabs'

export function ContinueRecentTile() {
  const sessions = useSessions((s) => s.sessions)
  const navigate = useNavigate()
  const openTab = useProjectTabs((s) => s.openTab)

  // pick the most-recently-active non-terminal session
  const recent = Object.values(sessions)
    .filter((p) => !['closed', 'failed', 'cancelled'].includes(p.info.status))
    .sort((a, b) => (b.info.last_active || '').localeCompare(a.info.last_active || ''))[0]

  if (!recent) {
    return (
      <div className="card p-5 flex items-center gap-3 text-ink-muted">
        <div className="w-10 h-10 rounded-soft bg-surface-2 flex items-center justify-center">
          <IconHistory size={20} strokeWidth={1.5} />
        </div>
        <div className="text-sm">
          <div className="text-ink">Nothing recent</div>
          <div className="text-xs">Start a project above and it'll land here.</div>
        </div>
      </div>
    )
  }

  return (
    <button
      type="button"
      onClick={() => {
        openTab(recent.info.session_id)
        navigate(`/project/${recent.info.session_id}`)
      }}
      className="card card-hover p-5 flex items-center gap-3 text-left w-full group"
    >
      <div className="w-10 h-10 rounded-soft bg-surface-2 flex items-center justify-center text-ink-muted group-hover:text-ink transition-colors">
        <IconHistory size={20} strokeWidth={1.5} />
      </div>
      <div className="flex-1 min-w-0">
        <div className="text-xs text-ink-faint">Continue recent</div>
        <div className="text-ink truncate text-sm">{recent.info.name || 'Untitled project'}</div>
      </div>
      <IconArrowRight
        size={16}
        strokeWidth={1.75}
        className="text-ink-faint group-hover:text-ink-muted transition-colors"
      />
    </button>
  )
}

export function ScheduleAutomationTile() {
  const navigate = useNavigate()
  return (
    <button
      type="button"
      onClick={() => navigate('/automations')}
      className="card card-hover p-5 flex items-center gap-3 text-left w-full group"
    >
      <div className="w-10 h-10 rounded-soft bg-surface-2 flex items-center justify-center text-ink-muted group-hover:text-ink transition-colors">
        <IconClock size={20} strokeWidth={1.5} />
      </div>
      <div className="flex-1 min-w-0">
        <div className="text-xs text-ink-faint">Recurring</div>
        <div className="text-ink truncate text-sm">Schedule an automation</div>
      </div>
      <IconArrowRight
        size={16}
        strokeWidth={1.75}
        className="text-ink-faint group-hover:text-ink-muted transition-colors"
      />
    </button>
  )
}
