/**
 * The Projects dashboard.
 *
 *   Greeting
 *   Quick start (textarea + chips + Start)
 *   Continue recent  |  Schedule automation
 *   Saved templates (optional row)
 *   Active projects grid
 *   Usage strip
 */
import { useEffect, useMemo, useState } from 'react'
import { useSessions } from '../stores/sessions'
import { useTemplates } from '../stores/templates'
import { Greeting } from '../components/dashboard/Greeting'
import { QuickStart } from '../components/dashboard/QuickStart'
import { ContinueRecentTile, ScheduleAutomationTile } from '../components/dashboard/Tiles'
import { SavedTemplates } from '../components/dashboard/SavedTemplates'
import { NewProjectCard, ProjectCard } from '../components/dashboard/ProjectCard'
import { UsageStrip } from '../components/dashboard/UsageStrip'

export function Projects() {
  const fetchSessions = useSessions((s) => s.fetchSessions)
  const sessions = useSessions((s) => s.sessions)
  const loaded = useSessions((s) => s.loaded)
  const loading = useSessions((s) => s.loading)

  // Quick-start preset (e.g. when picking a saved template)
  const [preset, setPreset] = useState<string>('')

  useEffect(() => {
    void fetchSessions()
    const interval = window.setInterval(() => void fetchSessions(), 8000)
    return () => window.clearInterval(interval)
  }, [fetchSessions])

  const active = useMemo(() => {
    return Object.values(sessions)
      .filter((p) => !['closed', 'cancelled'].includes(p.info.status))
      .sort((a, b) => (b.info.last_active || '').localeCompare(a.info.last_active || ''))
  }, [sessions])

  const closed = useMemo(() => {
    return Object.values(sessions)
      .filter((p) => ['closed', 'cancelled', 'completed', 'failed'].includes(p.info.status))
      .sort((a, b) => (b.info.last_active || '').localeCompare(a.info.last_active || ''))
      .slice(0, 6)
  }, [sessions])

  const focusQuickStart = () => {
    const el = document.querySelector<HTMLTextAreaElement>('[data-quick-start]')
    el?.focus()
  }

  return (
    <div className="flex-1 overflow-y-auto p-8">
      <div className="max-w-5xl mx-auto">
      <Greeting />

      <QuickStart key={preset} initialTask={preset} />

      <div className="grid grid-cols-2 gap-3 mt-4 mb-8">
        <ContinueRecentTile />
        <ScheduleAutomationTile />
      </div>

      <SavedTemplates
        onPick={(t) => {
          setPreset(t.task)
          window.setTimeout(focusQuickStart, 0)
        }}
      />

      <section className="mb-8">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm text-ink-muted">Active projects</h2>
          {active.length > 0 && (
            <span className="text-xs text-ink-faint">
              {active.length} open · click any card to jump in
            </span>
          )}
        </div>

        {!loaded && loading ? (
          <SkeletonGrid />
        ) : active.length === 0 ? (
          <div className="grid grid-cols-3 gap-3">
            <NewProjectCard onClick={focusQuickStart} />
          </div>
        ) : (
          <div className="grid grid-cols-3 gap-3">
            {active.map((p) => (
              <ProjectCard key={p.info.session_id} project={p} />
            ))}
            <NewProjectCard onClick={focusQuickStart} />
          </div>
        )}
      </section>

      {closed.length > 0 && (
        <section className="mb-8">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm text-ink-muted">Recently closed</h2>
            <span className="text-xs text-ink-faint">{closed.length} item(s)</span>
          </div>
          <div className="grid grid-cols-3 gap-3">
            {closed.map((p) => (
              <ProjectCard key={p.info.session_id} project={p} />
            ))}
          </div>
        </section>
      )}

      <UsageStrip />

      <FirstTimeHint />
      </div>
    </div>
  )
}

function SkeletonGrid() {
  return (
    <div className="grid grid-cols-3 gap-3">
      {[0, 1, 2].map((i) => (
        <div
          key={i}
          className="rounded-card border border-line bg-surface min-h-[148px] p-5 animate-pulse"
        >
          <div className="h-4 w-32 bg-surface-2 rounded mb-2" />
          <div className="h-3 w-44 bg-surface-2 rounded" />
        </div>
      ))}
    </div>
  )
}

/**
 * Tiny first-time hint surfaced once per machine.
 * Phase 9C will deepen this into a "Did you know?" rotation.
 */
function FirstTimeHint() {
  const templates = useTemplates((s) => s.items)
  const [dismissed, setDismissed] = useState<boolean>(
    typeof localStorage !== 'undefined' && localStorage.getItem('hive.hint.quickstart') === '1',
  )
  if (dismissed) return null
  if (templates.length > 0) return null

  return (
    <div className="mt-8 text-xs text-ink-faint flex items-center gap-3 justify-center">
      <span>Tip — when a project goes well, hit "Save as template" to reuse it.</span>
      <button
        type="button"
        className="underline hover:text-ink-muted"
        onClick={() => {
          localStorage.setItem('hive.hint.quickstart', '1')
          setDismissed(true)
        }}
      >
        got it
      </button>
    </div>
  )
}
