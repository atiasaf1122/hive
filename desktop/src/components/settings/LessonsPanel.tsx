/**
 * Lessons tab (D1.5) — what HIVE has learned, with the stats to judge it.
 *
 * Each lesson shows applied/confirmed/unconfirmed counts; archived lessons
 * (manually or by the 3-strikes hygiene rule) are listed separately and can
 * be restored. "Distill now" runs the same grounded pipeline on any session.
 */
import { useCallback, useEffect, useState } from 'react'
import { api } from '../../lib/api'
import { toast } from '../../lib/toast'
import { SettingCard } from './SettingsLayout'

interface LessonRow {
  id: number
  scope: string
  project_path: string | null
  title: string
  description: string
  content: string
  origin: string
  times_applied: number
  times_confirmed: number
  times_unconfirmed: number
  status: string
  created_at: string
}

interface MetaNudge {
  should_nudge: boolean
  window_hours: number
  clusters: { failure_class: string; count: number }[]
}

export function LessonsPanel() {
  const [lessons, setLessons] = useState<LessonRow[]>([])
  const [distillSession, setDistillSession] = useState('')
  const [metaRunning, setMetaRunning] = useState(false)
  const [metaReport, setMetaReport] = useState<string | null>(null)
  const [nudge, setNudge] = useState<MetaNudge | null>(null)

  useEffect(() => {
    api.get<MetaNudge>('/api/meta/nudge').then(setNudge).catch(() => setNudge(null))
  }, [])

  async function runMeta() {
    setMetaRunning(true)
    try {
      const res = await api.post<{ report: string; report_path: string }>('/api/meta/run', {})
      setMetaReport(res.report)
      toast.success(`META report saved to ${res.report_path}`)
    } catch (e) {
      toast.error(`META failed: ${e instanceof Error ? e.message : e}`)
    } finally {
      setMetaRunning(false)
    }
  }

  const reload = useCallback(async () => {
    try {
      const res = await api.get<{ lessons: LessonRow[] }>('/api/lessons')
      setLessons(res.lessons)
    } catch (e) {
      toast.error(`Couldn't load lessons: ${e instanceof Error ? e.message : e}`)
    }
  }, [])

  useEffect(() => { void reload() }, [reload])

  async function act(id: number, action: 'archive' | 'restore' | 'delete') {
    try {
      if (action === 'delete') {
        if (!confirm('Delete this lesson permanently?')) return
        await api.delete(`/api/lessons/${id}`)
      } else {
        await api.post(`/api/lessons/${id}/${action}`)
      }
      await reload()
    } catch (e) {
      toast.error(`${action} failed: ${e instanceof Error ? e.message : e}`)
    }
  }

  async function distillNow() {
    const sid = distillSession.trim()
    if (!sid) return
    try {
      const res = await api.post<{ saved_lesson_ids: number[] }>(`/api/lessons/distill/${sid}`)
      toast.success(`Distilled ${res.saved_lesson_ids.length} lesson(s) from ${sid}`)
      await reload()
    } catch (e) {
      toast.error(`Distill failed: ${e instanceof Error ? e.message : e}`)
    }
  }

  const active = lessons.filter((l) => l.status === 'active')
  const archived = lessons.filter((l) => l.status === 'archived')

  const Row = ({ l }: { l: LessonRow }) => (
    <div className="border-t border-line first:border-t-0 py-2.5">
      <div className="flex items-center gap-2">
        <span className="text-sm text-ink font-medium flex-1 truncate">{l.title}</span>
        <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-surface-2 text-ink-muted">
          {l.origin}
        </span>
        <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-surface-2 text-ink-muted">
          {l.scope}
        </span>
      </div>
      <div className="text-xs text-ink-muted mt-1">{l.content}</div>
      <div className="flex items-center gap-3 mt-1.5 text-[11px] text-ink-faint">
        <span>applied {l.times_applied}</span>
        <span className="text-emerald-500">confirmed {l.times_confirmed}</span>
        <span className={l.times_unconfirmed > 0 ? 'text-amber-500' : ''}>
          unconfirmed {l.times_unconfirmed}
        </span>
        <span className="flex-1" />
        {l.status === 'active' ? (
          <button type="button" className="btn-ghost text-[11px]" onClick={() => void act(l.id, 'archive')}>
            Archive
          </button>
        ) : (
          <button type="button" className="btn-ghost text-[11px]" onClick={() => void act(l.id, 'restore')}>
            Restore
          </button>
        )}
        <button type="button" className="btn-ghost text-[11px] text-red-500" onClick={() => void act(l.id, 'delete')}>
          Delete
        </button>
      </div>
    </div>
  )

  return (
    <>
      <SettingCard
        title={`Active lessons (${active.length})`}
        description="Distilled from objective evidence only, gated before saving, and archived automatically if they fail to prevent their failure class 3 times."
      >
        {active.length === 0 ? (
          <div className="text-xs text-ink-faint italic">
            Nothing learned yet — lessons appear after sessions hit (and resolve) real failures.
          </div>
        ) : active.map((l) => <Row key={l.id} l={l} />)}
      </SettingCard>

      {archived.length > 0 && (
        <SettingCard title={`Archived (${archived.length})`} description="Manually archived or retired by the hygiene loop.">
          {archived.map((l) => <Row key={l.id} l={l} />)}
        </SettingCard>
      )}

      <SettingCard title="Distill now" description="Run the grounded distillation pipeline on a specific session's event log.">
        <div className="flex items-center gap-2">
          <input
            value={distillSession}
            onChange={(e) => setDistillSession(e.target.value)}
            placeholder="session id, e.g. f8df80d4"
            className="input-soft flex-1 text-sm font-mono"
          />
          <button type="button" className="btn-ghost text-xs" onClick={() => void distillNow()}>
            Distill
          </button>
        </div>
      </SettingCard>

      <SettingCard
        title="META analysis"
        description="One Opus pass over HIVE's own stats (lessons, trust, failure clusters, costs, estimates). Advises only — nothing auto-executes. Cost: ~$0.10–0.50 depending on history size."
      >
        {nudge?.should_nudge && (
          <div className="mb-3 rounded-soft bg-amber-500/10 border border-amber-500/30 p-2.5">
            <div className="text-xs font-medium text-amber-500">
              ⚠ Recurring failures in the last {nudge.window_hours}h — run META?
            </div>
            <ul className="mt-1 text-[11px] text-ink-muted">
              {nudge.clusters.slice(0, 3).map((c) => (
                <li key={c.failure_class}>
                  ×{c.count} — {c.failure_class}
                </li>
              ))}
            </ul>
          </div>
        )}
        <button
          type="button"
          className="btn-ghost text-xs"
          disabled={metaRunning}
          onClick={() => void runMeta()}
        >
          {metaRunning ? 'Analyzing…' : 'Analyze & Advise (global)'}
        </button>
        {metaReport && (
          <pre className="mt-3 text-[11px] text-ink-muted bg-surface-2 rounded-soft p-3 overflow-x-auto max-h-96 whitespace-pre-wrap">
            {metaReport}
          </pre>
        )}
      </SettingCard>
    </>
  )
}
