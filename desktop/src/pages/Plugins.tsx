/**
 * Plugins — MCP servers + integrations + (later) local Ollama models.
 *
 *   Hero with HIVE ↔ MCP ↔ tool flow
 *   Top tabs: Installed · Discover · Models
 *   Discover: category sidebar + grid
 *   Permission dialog before install
 */
import { IconPlug, IconRefresh, IconSearch } from '@tabler/icons-react'
import clsx from 'clsx'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { PermissionDialog } from '../components/plugins/PermissionDialog'
import { PluginCard, type MCPItem } from '../components/plugins/PluginCard'
import { FlowStrip, HeroHeader } from '../components/ui/HeroHeader'
import { Skeleton } from '../components/ui/Skeleton'
import { api } from '../lib/api'

type Tab = 'installed' | 'discover' | 'models'

interface MCPResponse {
  items: MCPItem[]
  fallback: boolean
  sources_tried: string[]
  sources_failed: string[]
  categories: string[]
  cached_at_age_seconds: number | null
}

export function Plugins() {
  const [tab, setTab] = useState<Tab>('discover')
  const [query, setQuery] = useState('')
  const [category, setCategory] = useState<string>('all')
  const [data, setData] = useState<MCPResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [installedIds, setInstalledIds] = useState<Set<string>>(new Set())
  const [pendingInstall, setPendingInstall] = useState<MCPItem | null>(null)
  const [refreshing, setRefreshing] = useState(false)
  const requestIdRef = useRef(0)

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

  const visibleItems = useMemo(() => {
    if (!data) return []
    if (tab === 'installed') return data.items.filter((i) => installedIds.has(i.id))
    if (tab === 'discover') return data.items
    return [] // models tab handled separately below
  }, [data, tab, installedIds])

  async function confirmInstall(item: MCPItem) {
    try {
      const res = await api.post<{ ok: boolean; command: string; config_path: string }>(
        '/api/registries/mcp/install',
        {
          id: item.id,
          name: item.name,
          install: item.install,
          permissions: item.permissions,
        },
      )
      if (res.ok) {
        setInstalledIds((prev) => new Set(prev).add(item.id))
        if (res.command && !res.command.startsWith('#')) {
          // Surface the runtime-install command the user still needs to run.
          alert(
            `Added to Claude config (${res.config_path}).\n\n` +
              `Run this once to install the runtime:\n\n  ${res.command}`,
          )
        }
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Install failed')
    }
  }

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-5xl mx-auto p-8 space-y-6">
        <HeroHeader
          icon={IconPlug}
          title="Plugins"
          blurb="Model Context Protocol servers extend the orchestrator and its agents with external tools — file systems, databases, image generation, web search. Discover and install with a permission gate every time."
          flow={<FlowStrip steps={['HIVE', 'MCP server', 'external tool']} />}
          stats={
            data ? (
              <span>
                {data.items.length} available · {installedIds.size} running · {' '}
                {data.fallback ? 'offline cache' : 'live'}
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

        {/* Top tab bar */}
        <div className="inline-flex items-center bg-surface-2 border border-line rounded-full p-0.5 text-xs">
          {(['installed', 'discover', 'models'] as Tab[]).map((t) => (
            <button
              key={t}
              type="button"
              onClick={() => setTab(t)}
              className={clsx(
                'px-3.5 py-1.5 rounded-full transition-colors capitalize',
                tab === t ? 'bg-accent-gradient text-white' : 'text-ink-muted hover:text-ink',
              )}
            >
              {t}
              {t === 'installed' && installedIds.size > 0 && (
                <span className={clsx('ml-1.5 text-[10px]', tab === t ? 'text-white/80' : 'text-ink-faint')}>
                  {installedIds.size}
                </span>
              )}
            </button>
          ))}
        </div>

        {tab === 'models' ? (
          <ModelsTabPlaceholder />
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
                  {tab === 'installed'
                    ? 'No plugins installed yet. Switch to Discover to browse.'
                    : 'No plugins match those filters.'}
                </div>
              ) : (
                <div className="grid grid-cols-2 gap-3">
                  {visibleItems.map((item) => (
                    <PluginCard
                      key={item.id}
                      item={item}
                      installed={installedIds.has(item.id)}
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

function ModelsTabPlaceholder() {
  return (
    <div className="card p-8 text-center">
      <div className="text-2xl mb-2">🧠</div>
      <div className="text-sm text-ink mb-1">Local model management</div>
      <div className="text-xs text-ink-muted max-w-md mx-auto leading-relaxed">
        Pull, list, and remove Ollama models from inside HIVE — including
        live VRAM monitoring. Phase 9D ships this; for now use{' '}
        <code>ollama list</code> in a terminal.
      </div>
    </div>
  )
}
