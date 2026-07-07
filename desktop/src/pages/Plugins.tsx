/**
 * Plugins — the swarm's MCP equipment (post-1.0 Part 5).
 *
 * Primary view: the CURATED catalog (backend/mcp/catalog.py) — the only
 * servers the planner can equip agents with, shown with live preflight
 * status. The registry browser is demoted to a "Discover more" tab: it
 * feeds future catalog proposals and can add servers to YOUR interactive
 * claude CLI (~/.claude.json) — agents never read that file
 * (--strict-mcp-config).
 */
import { IconCircleCheck, IconCircleX, IconPlug, IconRefresh, IconSearch } from '@tabler/icons-react'
import clsx from 'clsx'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { PermissionDialog } from '../components/plugins/PermissionDialog'
import { PluginCard, type MCPItem } from '../components/plugins/PluginCard'
import { FlowStrip, HeroHeader } from '../components/ui/HeroHeader'
import { Skeleton } from '../components/ui/Skeleton'
import { api } from '../lib/api'
import { slugify } from '../lib/slug'

type Tab = 'catalog' | 'discover'

interface MCPResponse {
  items: MCPItem[]
  fallback: boolean
  sources_tried: string[]
  sources_failed: string[]
  categories: string[]
  cached_at_age_seconds: number | null
}

interface CatalogServer {
  id: string
  label: string
  tags: string[]
  notes: string
  when_to_use: string
  requires: string[]
  per_agent_isolation: boolean
  preflight_ok: boolean
  missing: string[]
}

export function Plugins() {
  const [tab, setTab] = useState<Tab>('catalog')
  const [catalog, setCatalog] = useState<CatalogServer[] | null>(null)
  const [query, setQuery] = useState('')
  const [category, setCategory] = useState<string>('all')
  const [data, setData] = useState<MCPResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [installedIds, setInstalledIds] = useState<Set<string>>(new Set())
  const [pendingInstall, setPendingInstall] = useState<MCPItem | null>(null)
  const [refreshing, setRefreshing] = useState(false)
  const requestIdRef = useRef(0)

  // Hydrate CLI-config badges (Discover tab) so they survive reloads.
  // Keys in ~/.claude.json mcpServers are name slugs (backend _safe_slug).
  useEffect(() => {
    void (async () => {
      try {
        const res = await api.get<{ items: { key: string }[] }>('/api/registries/mcp/installed')
        setInstalledIds(new Set(res.items.map((i) => i.key)))
      } catch {
        // Backend down or old backend without the endpoint — badges stay off.
      }
    })()
  }, [])

  // The curated catalog — what the swarm can actually use.
  useEffect(() => {
    void (async () => {
      try {
        const res = await api.get<{ servers: CatalogServer[] }>('/api/mcp/catalog')
        setCatalog(res.servers)
      } catch {
        setCatalog([])
      }
    })()
  }, [])

  const load = useCallback(async (force = false) => {
    const myId = ++requestIdRef.current
    if (force) setRefreshing(true)
    try {
      const url = new URL('/api/registries/mcp/list', 'http://x')
      if (query) url.searchParams.set('q', query)
      if (category !== 'all') url.searchParams.set('category', category)
      if (force) url.searchParams.set('force_refresh', 'true')
      const res = await api.get<MCPResponse>(url.pathname + url.search)
      if (myId !== requestIdRef.current) return
      setData(res)
      setError(null)
    } catch (e) {
      if (myId !== requestIdRef.current) return
      setError(e instanceof Error ? e.message : 'Could not load plugins')
    } finally {
      if (myId === requestIdRef.current) setRefreshing(false)
    }
  }, [query, category])

  useEffect(() => {
    const handle = window.setTimeout(() => { void load() }, 250)
    return () => window.clearTimeout(handle)
  }, [load])

  const visibleItems = useMemo(() => data?.items ?? [], [data])

  async function confirmInstall(item: MCPItem) {
    try {
      const res = await api.post<{
        ok: boolean
        command: string
        config_path: string
        config_key: string
      }>('/api/registries/mcp/install', {
        id: item.id,
        name: item.name,
        install: item.install,
        permissions: item.permissions,
      })
      if (res.ok) {
        setInstalledIds((prev) => new Set(prev).add(res.config_key))
        if (res.command && !res.command.startsWith('#')) {
          // Surface the runtime-install command the user still needs to run.
          alert(
            `Added to your Claude CLI config (${res.config_path}).\n\n` +
              `Run this once to install the runtime:\n\n  ${res.command}`,
          )
        }
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Add to CLI failed')
    }
  }

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-5xl mx-auto p-8 space-y-6">
        <HeroHeader
          icon={IconPlug}
          title="Plugins"
          blurb="The swarm's MCP equipment. The curated catalog below is what the planner can assign to agents, with live readiness checks. Discover more browses the public registry — it feeds future catalog proposals and can equip YOUR interactive claude CLI (never the agents)."
          flow={<FlowStrip steps={['curated catalog', 'planner assigns', 'agent equipped']} />}
          stats={
            catalog ? (
              <span>
                {catalog.length} in the swarm catalog ·{' '}
                {catalog.filter((s) => s.preflight_ok).length} ready ·{' '}
                {installedIds.size} in your CLI config
              </span>
            ) : null
          }
          actions={
            tab === 'discover' ? (
              <button
                type="button"
                onClick={() => void load(true)}
                disabled={refreshing}
                className="btn-ghost text-xs inline-flex items-center gap-1.5"
              >
                <IconRefresh size={13} strokeWidth={1.75} className={refreshing ? 'animate-spin' : ''} />
                Refresh
              </button>
            ) : null
          }
        />

        {/* Top tab bar */}
        <div className="inline-flex items-center bg-surface-2 border border-line rounded-full p-0.5 text-xs">
          {(['catalog', 'discover'] as Tab[]).map((t) => (
            <button
              key={t}
              type="button"
              onClick={() => setTab(t)}
              className={clsx(
                'px-3.5 py-1.5 rounded-full transition-colors',
                tab === t ? 'bg-accent-gradient text-white' : 'text-ink-muted hover:text-ink',
              )}
            >
              {t === 'catalog' ? 'Swarm catalog' : 'Discover more'}
            </button>
          ))}
        </div>

        {tab === 'catalog' ? (
          !catalog ? (
            <div className="grid grid-cols-2 gap-3">
              {[0, 1, 2, 3].map((i) => (
                <Skeleton key={i} variant="block" />
              ))}
            </div>
          ) : (
            <div className="grid grid-cols-2 gap-3">
              {catalog.map((s) => (
                <CatalogCard key={s.id} server={s} />
              ))}
            </div>
          )
        ) : (
        <div className="grid grid-cols-[180px_1fr] gap-6">
          <aside>
              <div className="text-xs text-ink-muted mb-2">Categories</div>
              <CategoryFilter
                categories={['all', ...(data?.categories ?? [])]}
                active={category}
                onChange={setCategory}
              />
            </aside>

            <div className="space-y-4">
              <div className="card p-3 flex items-center gap-3">
                <IconSearch size={16} strokeWidth={1.5} className="text-ink-faint" />
                <input
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="Search MCP servers…"
                  className="flex-1 bg-transparent outline-none text-sm text-ink placeholder:text-ink-faint"
                />
              </div>

              {error && (
                <div className="text-xs text-red-500 bg-red-500/10 border border-red-500/20 rounded-soft px-3 py-2">
                  {error}
                </div>
              )}

              {data?.fallback && (
                <div className="card p-3 bg-amber-500/5 border-amber-500/30 text-xs text-amber-700 dark:text-amber-300">
                  Showing offline cache — couldn't reach{' '}
                  {data.sources_failed.join(', ')}.
                </div>
              )}

              {!data ? (
                <div className="grid grid-cols-2 gap-3">
                  {[0, 1, 2, 3].map((i) => (
                    <Skeleton key={i} variant="block" />
                  ))}
                </div>
              ) : visibleItems.length === 0 ? (
                <div className="card p-8 text-center text-sm text-ink-muted">
                  No MCP servers match those filters.
                </div>
              ) : (
                <div className="grid grid-cols-2 gap-3">
                  {visibleItems.map((item) => (
                    <PluginCard
                      key={item.id}
                      item={item}
                      installed={installedIds.has(slugify(item.name))}
                      onInstall={setPendingInstall}
                    />
                  ))}
                </div>
              )}
            </div>
        </div>
        )}
      </div>

      <PermissionDialog
        item={pendingInstall}
        onClose={() => setPendingInstall(null)}
        onConfirm={confirmInstall}
      />
    </div>
  )
}

function CatalogCard({ server }: { server: CatalogServer }) {
  return (
    <div className="card card-hover p-4 flex flex-col gap-2">
      <div className="flex items-center gap-2 flex-wrap">
        <div className="text-sm text-ink">{server.label}</div>
        {server.preflight_ok ? (
          <span className="text-[10px] text-emerald-500 inline-flex items-center gap-1">
            <IconCircleCheck size={11} /> ready
          </span>
        ) : (
          <span className="text-[10px] text-amber-500 inline-flex items-center gap-1">
            <IconCircleX size={11} /> not ready
          </span>
        )}
        {server.per_agent_isolation && (
          <span className="text-[10px] uppercase tracking-wider text-ink-faint border border-line rounded px-1.5 py-px">
            per-agent
          </span>
        )}
      </div>
      <div className="text-xs text-ink-muted">{server.when_to_use}</div>
      {!server.preflight_ok && server.missing.length > 0 && (
        <div className="text-[11px] text-amber-600 dark:text-amber-400">
          {server.missing.join('; ')}
        </div>
      )}
      <div className="text-[11px] text-ink-faint truncate">
        {server.tags.slice(0, 6).join(' · ')}
      </div>
    </div>
  )
}

function CategoryFilter({
  categories,
  active,
  onChange,
}: {
  categories: string[]
  active: string
  onChange: (c: string) => void
}) {
  return (
    <div className="space-y-0.5">
      {categories.map((c) => (
        <button
          key={c}
          type="button"
          onClick={() => onChange(c)}
          className={clsx(
            'w-full text-left px-2.5 py-1.5 rounded-soft text-sm transition-colors capitalize',
            c === active
              ? 'bg-surface-2 text-ink'
              : 'text-ink-muted hover:text-ink hover:bg-surface-2/60',
          )}
        >
          {c === 'all' ? 'All' : c}
        </button>
      ))}
    </div>
  )
}
