/**
 * Skills tab — unified search across ClawHub + Cookbook + Community + Installed.
 *
 *   Hero with flow strip (task → match → top-3 → agent)
 *   Search bar + source filter pills
 *   Discover grid (results)
 *   Installed grid (local registry — via existing /api/sessions side-channel
 *                   is overkill; we just hit a thin endpoint in 9D)
 *
 * Phase 9C focus is *discovery* — the install flow goes through the preview
 * modal which gates unverified entries behind a confirmation per the
 * Feb 2026 incident.
 */
import { IconBook2, IconRefresh, IconSearch } from '@tabler/icons-react'
import clsx from 'clsx'
import { useEffect, useMemo, useState } from 'react'
import { SkillCard, type SkillItem } from '../components/skills/SkillCard'
import { SkillPreviewModal } from '../components/skills/SkillPreviewModal'
import { FlowStrip, HeroHeader } from '../components/ui/HeroHeader'
import { Skeleton } from '../components/ui/Skeleton'
import { api } from '../lib/api'

type SourceFilter = 'all' | 'clawhub' | 'cookbook' | 'community' | 'installed'

interface SearchResponse {
  items: SkillItem[]
  fallback: boolean
  sources_tried: string[]
  sources_failed: string[]
  cached_at_age_seconds: number | null
}

const FILTERS: { value: SourceFilter; label: string }[] = [
  { value: 'all', label: 'All sources' },
  { value: 'clawhub', label: 'ClawHub' },
  { value: 'cookbook', label: 'Cookbook' },
  { value: 'community', label: 'GitHub' },
  { value: 'installed', label: 'Installed only' },
]

export function Skills() {
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState<SourceFilter>('all')
  const [data, setData] = useState<SearchResponse | null>(null)
  const [installedIds, setInstalledIds] = useState<Set<string>>(new Set())
  const [previewing, setPreviewing] = useState<SkillItem | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [refreshing, setRefreshing] = useState(false)

  async function load(force = false) {
    if (force) setRefreshing(true)
    try {
      const url = new URL('/api/registries/skills/search', 'http://x')
      if (query) url.searchParams.set('q', query)
      if (filter !== 'all' && filter !== 'installed') url.searchParams.set('source', filter)
      if (force) url.searchParams.set('force_refresh', 'true')
      const res = await api.get<SearchResponse>(url.pathname + url.search)
      setData(res)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load skills')
    } finally {
      setRefreshing(false)
    }
  }
  useEffect(() => {
    void load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query, filter])

  const items = useMemo(() => {
    if (!data) return []
    if (filter === 'installed') return data.items.filter((i) => installedIds.has(i.id))
    return data.items
  }, [data, filter, installedIds])

  function onInstall(skill: SkillItem) {
    if (skill.auto_install_ok) {
      void doInstall(skill)
    } else {
      setPreviewing(skill)
    }
  }

  async function doInstall(skill: SkillItem) {
    try {
      await api.post('/api/registries/skills/install', {
        id: skill.id,
        name: skill.name,
        description: skill.description,
        source: skill.source,
        url: skill.url,
        tags: skill.tags,
      })
      setInstalledIds((prev) => new Set(prev).add(skill.id))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Install failed')
    }
  }

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-5xl mx-auto p-8 space-y-6">
        <HeroHeader
          icon={IconBook2}
          title="Skills"
          blurb="Reusable instruction packs the orchestrator injects into agents. Type a task, get back the three most relevant — no hand-wiring per project."
          flow={<FlowStrip steps={['task', 'embed', 'top-3 match', 'inject', 'agent']} />}
          stats={
            data ? (
              <span>
                {data.items.length} found · {data.fallback ? 'offline cache' : 'live'} · {' '}
                {data.sources_failed.length > 0 ? `${data.sources_failed.length} source(s) unreachable` : 'all sources OK'}
              </span>
            ) : null
          }
          actions={
            <button
              type="button"
              onClick={() => void load(true)}
              disabled={refreshing}
              className="btn-ghost text-xs inline-flex items-center gap-1.5"
            >
              <IconRefresh size={13} strokeWidth={1.75} className={refreshing ? 'animate-spin' : ''} />
              Refresh
            </button>
          }
        />

        <div className="card p-3 flex items-center gap-3">
          <IconSearch size={16} strokeWidth={1.5} className="text-ink-faint" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search skills by name, tag, or description…"
            className="flex-1 bg-transparent outline-none text-sm text-ink placeholder:text-ink-faint"
          />
        </div>

        <div className="flex items-center gap-1 flex-wrap">
          {FILTERS.map((f) => (
            <button
              key={f.value}
              type="button"
              onClick={() => setFilter(f.value)}
              className={clsx(
                'text-xs px-3 py-1.5 rounded-full transition-colors',
                filter === f.value
                  ? 'bg-accent-gradient text-white'
                  : 'text-ink-muted hover:text-ink hover:bg-surface-2',
              )}
            >
              {f.label}
            </button>
          ))}
        </div>

        {error && (
          <div className="text-xs text-red-500 bg-red-500/10 border border-red-500/20 rounded-soft px-3 py-2">
            {error}
          </div>
        )}

        {data?.fallback && (
          <div className="card p-3 bg-amber-500/5 border-amber-500/30 text-xs text-amber-700 dark:text-amber-300">
            Showing offline cache — couldn't reach{' '}
            {data.sources_failed.join(', ')}. Results are curated and may be
            out of date.
          </div>
        )}

        {!data ? (
          <SkillsSkeleton />
        ) : items.length === 0 ? (
          <div className="card p-8 text-center text-sm text-ink-muted">
            No matches. Try a broader query, or switch filter to "All sources".
          </div>
        ) : (
          <div className="grid grid-cols-2 gap-3">
            {items.map((s) => (
              <SkillCard
                key={s.id}
                skill={s}
                installed={installedIds.has(s.id)}
                onPreview={setPreviewing}
                onInstall={onInstall}
              />
            ))}
          </div>
        )}

        <SecurityNote />
      </div>

      <SkillPreviewModal
        skill={previewing}
        onClose={() => setPreviewing(null)}
        onInstall={(s) => void doInstall(s)}
      />
    </div>
  )
}

function SkillsSkeleton() {
  return (
    <div className="grid grid-cols-2 gap-3">
      {[0, 1, 2, 3, 4, 5].map((i) => (
        <Skeleton key={i} variant="block" className="h-32" />
      ))}
    </div>
  )
}

function SecurityNote() {
  return (
    <div className="text-[11px] text-ink-faint leading-relaxed pt-4 border-t border-line">
      <span className="text-ink-muted">Security</span> — HIVE downloads run through
      the backend (Python), never the WebView. We auto-allow installs from verified
      publishers and items with 100+ stars; everything else routes through the
      preview modal so you can read the SKILL.md before it touches your skills
      registry. After the February 2026 ClawHub supply-chain incident, we err on
      the cautious side.
    </div>
  )
}
