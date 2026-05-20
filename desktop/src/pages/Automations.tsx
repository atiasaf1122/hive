/**
 * Automations — schedule + webhook pipelines.
 *
 *   HeroHeader with trigger→agents→result flow
 *   "Next run" featured strip
 *   Sections: Scheduled · On webhook · Paused
 *   "New automation" button → PipelineWizard
 */
import { IconClock, IconPlus } from '@tabler/icons-react'
import { useEffect, useMemo, useState } from 'react'
import { PipelineCard } from '../components/automations/PipelineCard'
import { PipelineWizard } from '../components/automations/PipelineWizard'
import type { Pipeline } from '../components/automations/types'
import { FlowStrip, HeroHeader } from '../components/ui/HeroHeader'
import { Skeleton } from '../components/ui/Skeleton'
import { api } from '../lib/api'

export function Automations() {
  const [pipelines, setPipelines] = useState<Pipeline[] | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [wizardOpen, setWizardOpen] = useState(false)

  async function refresh() {
    try {
      const list = await api.get<Pipeline[]>('/api/pipelines')
      setPipelines(list)
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not load automations')
    }
  }
  useEffect(() => {
    void refresh()
  }, [])

  const { scheduled, webhook, paused } = useMemo(() => {
    const grouped = { scheduled: [] as Pipeline[], webhook: [] as Pipeline[], paused: [] as Pipeline[] }
    for (const p of pipelines ?? []) {
      if (!p.enabled) grouped.paused.push(p)
      else if (p.schedule) grouped.scheduled.push(p)
      else grouped.webhook.push(p)
    }
    return grouped
  }, [pipelines])

  const nextRun = useMemo(() => {
    if (!pipelines) return null
    return pipelines
      .filter((p) => p.enabled && p.next_run_at)
      .sort((a, b) => (a.next_run_at || '').localeCompare(b.next_run_at || ''))[0]
  }, [pipelines])

  function update(p: Pipeline) {
    setPipelines((prev) => (prev ?? []).map((x) => (x.id === p.id ? p : x)))
  }
  function remove(id: string) {
    setPipelines((prev) => (prev ?? []).filter((x) => x.id !== id))
  }
  function add(p: Pipeline) {
    setPipelines((prev) => [p, ...(prev ?? [])])
  }

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-5xl mx-auto p-8 space-y-6">
        <HeroHeader
          icon={IconClock}
          title="Automations"
          blurb="Pipelines that fire on a schedule or webhook. Each one spins up a fresh session with its own orchestrator — same code path as everything else, just unattended."
          stats={
            pipelines ? (
              <span>
                {scheduled.length} scheduled · {webhook.length} webhook ·{' '}
                {paused.length} paused
              </span>
            ) : null
          }
          flow={<FlowStrip steps={['trigger', 'orchestrator', 'agents', 'result']} />}
          actions={
            <button
              type="button"
              onClick={() => setWizardOpen(true)}
              className="btn-primary text-xs inline-flex items-center gap-1.5"
            >
              <IconPlus size={14} strokeWidth={1.75} /> New automation
            </button>
          }
        />

        {nextRun && (
          <div className="card p-4 flex items-center gap-4 bg-gradient-to-r from-surface to-surface-2/40">
            <div className="w-10 h-10 rounded-soft bg-accent-gradient text-white flex items-center justify-center">
              <IconClock size={18} />
            </div>
            <div className="flex-1 min-w-0">
              <div className="text-xs text-ink-faint">Next scheduled run</div>
              <div className="text-sm text-ink">{nextRun.name}</div>
              <div className="text-[11px] text-ink-muted">
                {nextRun.schedule} · {nextRun.next_run_at}
              </div>
            </div>
          </div>
        )}

        {err && (
          <div className="text-xs text-red-500 bg-red-500/10 border border-red-500/20 rounded-soft px-3 py-2">
            {err}
          </div>
        )}

        {pipelines === null ? (
          <div className="grid grid-cols-2 gap-3">
            {[0, 1, 2, 3].map((i) => (
              <Skeleton key={i} variant="block" />
            ))}
          </div>
        ) : (
          <>
            <Section title="Scheduled" items={scheduled} onUpdate={update} onDelete={remove} />
            <Section title="On webhook" items={webhook} onUpdate={update} onDelete={remove} />
            <Section title="Paused" items={paused} onUpdate={update} onDelete={remove} />
          </>
        )}

        {pipelines && pipelines.length === 0 && (
          <div className="card p-8 text-center">
            <div className="text-2xl mb-2">⏰</div>
            <div className="text-sm text-ink mb-1">No automations yet</div>
            <div className="text-xs text-ink-muted mb-4">
              Schedule a daily haiku, a weekly report, a webhook-triggered build — whatever you'd rather not click manually.
            </div>
            <button
              type="button"
              onClick={() => setWizardOpen(true)}
              className="btn-primary text-xs inline-flex items-center gap-1.5"
            >
              <IconPlus size={14} /> Create your first
            </button>
          </div>
        )}
      </div>

      <PipelineWizard
        open={wizardOpen}
        onClose={() => setWizardOpen(false)}
        onCreated={add}
      />
    </div>
  )
}

function Section({
  title,
  items,
  onUpdate,
  onDelete,
}: {
  title: string
  items: Pipeline[]
  onUpdate: (p: Pipeline) => void
  onDelete: (id: string) => void
}) {
  if (items.length === 0) return null
  return (
    <section>
      <div className="text-xs text-ink-muted mb-2">{title}</div>
      <div className="grid grid-cols-2 gap-3">
        {items.map((p) => (
          <PipelineCard key={p.id} pipeline={p} onUpdated={onUpdate} onDeleted={onDelete} />
        ))}
      </div>
    </section>
  )
}
