/**
 * Skills — a browser over the LOCAL skills library (post-1.0 Part 4).
 *
 * The library lives at ~/.hive/skills/<family>/<slug>/SKILL.md and is what
 * the orchestrator hybrid-searches when equipping agents. Online discovery
 * (clawhub/cookbook/community) happens ONLY during a manual Sync — the
 * sources are flaky and skills are tiny text files, so we own the whole
 * library locally and refresh it every few months.
 */
import { IconBook2, IconRefresh, IconSearch } from '@tabler/icons-react'
import clsx from 'clsx'
import { useEffect, useMemo, useState } from 'react'
import { FlowStrip, HeroHeader } from '../components/ui/HeroHeader'
import { Skeleton } from '../components/ui/Skeleton'
import { api } from '../lib/api'

interface LocalSkill {
  id: string
  name: string
  description: string
  tags: string[]
  version: number
  family: string
}

interface SyncReport {
  discovered: number
  duplicates: number
  sources_failed: string[]
  new: string[]
  updated: string[]
  unchanged: string[]
  synthesized: string[]
  failed: { slug: string; error: string }[]
  families: Record<string, number>
  total_in_library: number
  disk_bytes: number
}

export function Skills() {
  const [items, setItems] = useState<LocalSkill[] | null>(null)
  const [query, setQuery] = useState('')
  const [family, setFamily] = useState<string>('all')
  const [error, setError] = useState<string | null>(null)
  const [syncing, setSyncing] = useState(false)
  const [lastReport, setLastReport] = useState<SyncReport | null>(null)

  async function load() {
    try {
      const res = await api.get<{ items: LocalSkill[] }>('/api/registries/skills/installed')
      setItems(res.items)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load the local library')
      setItems([])
    }
  }

  useEffect(() => {
    void load()
  }, [])

  async function runSync() {
    setSyncing(true)
    setError(null)
    try {
      const res = await api.post<{ ok: boolean; report: SyncReport }>(
        '/api/registries/skills/sync', {},
      )
      setLastReport(res.report)
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Sync failed')
    } finally {
      setSyncing(false)
    }
  }

  const families = useMemo(() => {
    const fams = new Set((items ?? []).map((s) => s.family))
    return ['all', ...Array.from(fams).sort()]
  }, [items])

  const visible = useMemo(() => {
    let list = items ?? []
    if (family !== 'all') list = list.filter((s) => s.family === family)
    const q = query.trim().toLowerCase()
    if (q) {
      list = list.filter((s) =>
        `${s.name} ${s.description} ${s.tags.join(' ')}`.toLowerCase().includes(q),
      )
    }
    return list
  }, [items, family, query])

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-5xl mx-auto p-8 space-y-6">
        <HeroHeader
          icon={IconBook2}
          title="Skills"
          blurb="Your local library of reusable instruction packs — the orchestrator embeds a task, matches the top 3, and injects them into agents. Online sources are consulted only when you press Sync."
          flow={<FlowStrip steps={['task', 'embed', 'top-3 match', 'inject', 'agent']} />}
          stats={
            items ? (
              <span>
                {items.length} in library · {families.length - 1} families ·
                {' '}synced manually
              </span>
            ) : null
          }
          actions={
            <button
              type="button"
              onClick={() => void runSync()}
              disabled={syncing}
              className="btn-ghost text-xs inline-flex items-center gap-1.5"
              title="Re-run online discovery and pull new/updated skills"
            >
              <IconRefresh size={13} strokeWidth={1.75} className={syncing ? 'animate-spin' : ''} />
              {syncing ? 'Syncing…' : 'Sync'}
            </button>
          }
        />

        <div className="card p-3 flex items-center gap-3">
          <IconSearch size={16} strokeWidth={1.5} className="text-ink-faint" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search the local library by name, tag, or description…"
            className="flex-1 bg-transparent outline-none text-sm text-ink placeholder:text-ink-faint"
          />
        </div>

        <div className="flex items-center gap-1 flex-wrap">
          {families.map((f) => (
            <button
              key={f}
              type="button"
              onClick={() => setFamily(f)}
              className={clsx(
                'text-xs px-3 py-1.5 rounded-full transition-colors capitalize',
                family === f
                  ? 'bg-accent-gradient text-white'
                  : 'text-ink-muted hover:text-ink hover:bg-surface-2',
              )}
            >
              {f === 'all' ? 'All families' : f}
            </button>
          ))}
        </div>

        {error && (
          <div className="text-xs text-red-500 bg-red-500/10 border border-red-500/20 rounded-soft px-3 py-2">
            {error}
          </div>
        )}

        {lastReport && (
          <div className="card p-3 text-xs text-ink-muted">
            Sync: {lastReport.new.length} new · {lastReport.updated.length} updated ·{' '}
            {lastReport.unchanged.length} unchanged · {lastReport.failed.length} failed
            {lastReport.synthesized.length > 0 &&
              ` · ${lastReport.synthesized.length} synthesized from metadata`}
            {lastReport.sources_failed.length > 0 &&
              ` · sources unreachable: ${lastReport.sources_failed.join(', ')}`}
            {' '}— {lastReport.total_in_library} skills,{' '}
            {(lastReport.disk_bytes / 1024).toFixed(0)} KB
          </div>
        )}

        {!items ? (
          <div className="grid grid-cols-2 gap-3">
            {[0, 1, 2, 3, 4, 5].map((i) => (
              <Skeleton key={i} variant="block" className="h-32" />
            ))}
          </div>
        ) : visible.length === 0 ? (
          <div className="card p-8 text-center text-sm text-ink-muted">
            {items.length === 0
              ? 'The local library is empty — press Sync to download every discoverable skill.'
              : 'No skills match those filters.'}
          </div>
        ) : (
          <div className="grid grid-cols-2 gap-3">
            {visible.map((s) => (
              <LocalSkillCard key={s.id} skill={s} />
            ))}
          </div>
        )}

        <SecurityNote />
      </div>
    </div>
  )
}

function LocalSkillCard({ skill }: { skill: LocalSkill }) {
  return (
    <div className="card card-hover p-4 flex flex-col gap-2">
      <div className="flex items-center gap-2 flex-wrap">
        <div className="text-sm text-ink truncate max-w-[220px]">{skill.name}</div>
        <span className="text-[10px] uppercase tracking-wider text-ink-faint border border-line rounded px-1.5 py-px">
          {skill.family}
        </span>
        <span className="text-[10px] text-ink-faint">v{skill.version}</span>
      </div>
      <div className="text-xs text-ink-muted line-clamp-3">{skill.description}</div>
      {skill.tags.length > 0 && (
        <div className="text-[11px] text-ink-faint truncate">
          {skill.tags.slice(0, 6).join(' · ')}
        </div>
      )}
    </div>
  )
}

function SecurityNote() {
  return (
    <div className="text-[11px] text-ink-faint leading-relaxed pt-4 border-t border-line">
      <span className="text-ink-muted">Security</span> — downloads run through the
      backend (Python) against an https allowlist (github.com, raw.githubusercontent.com,
      clawhub.dev), and only during a manual Sync. Skills that fail validation are
      skipped and flagged in the report; synthesized entries (no SKILL.md upstream)
      are marked so you can review them.
    </div>
  )
}
