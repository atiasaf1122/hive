/**
 * Browser-style tab bar at the top of the project view.
 *
 * - One tab per open project (state lives in stores/projectTabs)
 * - Click a tab → navigate to /project/{id}
 * - Middle-click or × on hover → close
 * - "+" at the end → go back to the dashboard
 *
 * Keyboard: Ctrl+1..9 → switch tabs, Ctrl+W → close current, Ctrl+T → new.
 * The shortcut handler lives in <App> so it works regardless of focus.
 */
import { IconPlus, IconX } from '@tabler/icons-react'
import clsx from 'clsx'
import { useNavigate, useParams } from 'react-router-dom'
import { useProjectTabs } from '../../stores/projectTabs'
import { useSessions } from '../../stores/sessions'

const STATUS_DOT: Record<string, string> = {
  running: 'bg-emerald-400 animate-pulse',
  starting: 'bg-amber-400 animate-pulse',
  planning: 'bg-amber-400 animate-pulse',
  spawning: 'bg-amber-400 animate-pulse',
  awaiting_user: 'bg-sky-400',
  waiting_approval: 'bg-orange-400 animate-pulse',
  closed: 'bg-ink-faint/60',
  failed: 'bg-red-400',
}

export function TabBar() {
  const open = useProjectTabs((s) => s.open)
  const close = useProjectTabs((s) => s.closeTab)
  const sessions = useSessions((s) => s.sessions)
  const { id: active } = useParams<{ id: string }>()
  const navigate = useNavigate()

  if (open.length === 0) return null

  return (
    <div className="flex items-end h-10 border-b border-line bg-bg pl-3 pr-2 gap-0.5 overflow-x-auto">
      {open.map((sid) => {
        const info = sessions[sid]?.info
        const name = info?.name || sid.slice(0, 8)
        const status = info?.status ?? ''
        const isActive = active === sid
        const dot = STATUS_DOT[status] ?? 'bg-ink-faint/40'
        return (
          <div
            key={sid}
            role="tab"
            tabIndex={0}
            onClick={() => navigate(`/project/${sid}`)}
            onAuxClick={(e) => {
              if (e.button === 1) {
                e.preventDefault()
                close(sid)
                if (isActive) {
                  const next = open.filter((x) => x !== sid)[0]
                  navigate(next ? `/project/${next}` : '/')
                }
              }
            }}
            className={clsx(
              'group h-9 inline-flex items-center gap-2 pl-3 pr-1.5 rounded-t-lg cursor-pointer text-xs select-none transition-colors max-w-[220px]',
              isActive
                ? 'bg-surface border border-line border-b-0 text-ink'
                : 'text-ink-muted hover:text-ink hover:bg-surface-2',
            )}
          >
            <span className={clsx('w-1.5 h-1.5 rounded-full shrink-0', dot)} />
            <span className="truncate">{name}</span>
            <button
              type="button"
              aria-label="Close tab"
              className="ml-1 w-5 h-5 rounded hover:bg-line/70 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity"
              onClick={(e) => {
                e.stopPropagation()
                close(sid)
                if (isActive) {
                  const next = open.filter((x) => x !== sid)[0]
                  navigate(next ? `/project/${next}` : '/')
                }
              }}
            >
              <IconX size={12} strokeWidth={1.75} />
            </button>
          </div>
        )
      })}

      <button
        type="button"
        title="New project (Ctrl+T)"
        onClick={() => navigate('/')}
        className="ml-1 mb-0.5 w-8 h-8 rounded-soft text-ink-muted hover:text-ink hover:bg-surface-2 flex items-center justify-center"
      >
        <IconPlus size={16} strokeWidth={1.75} />
      </button>
    </div>
  )
}
