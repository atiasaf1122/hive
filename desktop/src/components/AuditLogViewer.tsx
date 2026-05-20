/**
 * Audit-log viewer.
 *
 * Reads `/api/security/audit`, filters in-place, and surfaces a CSV
 * export. Opened as a full-screen sheet from Settings → Security.
 *
 * Every row drills down to a detail panel showing the (truncated)
 * stdout / stderr the backend captured.
 */
import { IconDownload, IconExternalLink, IconFilter, IconX } from '@tabler/icons-react'
import { useVirtualizer } from '@tanstack/react-virtual'
import clsx from 'clsx'
import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../lib/api'

interface AuditRow {
  id: number
  ts: string
  project_id: string
  agent_id: string
  command: string
  working_dir: string
  classification: string
  decision_source: string
  matched_pattern: string | null
  exit_code: number | null
  stdout_excerpt: string
  stderr_excerpt: string
  duration_ms: number
  user_approved: number | null
}

interface Props {
  open: boolean
  onClose: () => void
}

const CLASSIFICATION_COLOUR: Record<string, string> = {
  allowed:   'text-emerald-500',
  confirmed: 'text-amber-500',
  blocked:   'text-red-500',
}

export function AuditLogViewer({ open, onClose }: Props) {
  const [rows, setRows] = useState<AuditRow[]>([])
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [selected, setSelected] = useState<AuditRow | null>(null)

  // Filters
  const [classFilter, setClassFilter] = useState<string>('all')
  const [search, setSearch] = useState('')

  async function refresh() {
    setLoading(true)
    setErr(null)
    try {
      const body = await api.get<{ items: AuditRow[] }>(
        '/api/security/audit?limit=500',
      )
      setRows(body.items)
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Failed to load')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (open) void refresh()
  }, [open])

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase()
    return rows.filter((r) => {
      if (classFilter !== 'all' && r.classification !== classFilter) return false
      if (q && !(
        r.command.toLowerCase().includes(q) ||
        r.agent_id.toLowerCase().includes(q) ||
        r.project_id.toLowerCase().includes(q)
      )) return false
      return true
    })
  }, [rows, classFilter, search])

  function downloadCsv() {
    const params = new URLSearchParams({ limit: '10000' })
    if (classFilter !== 'all') params.set('classification', classFilter)
    // Use a full-page navigation so the browser handles the file save.
    window.open(`http://127.0.0.1:8765/api/security/audit/export.csv?${params}`, '_blank')
  }

  if (!open) return null

  return (
    <div className="fixed inset-0 z-50 flex bg-black/40 backdrop-blur-sm">
      <div className="m-auto w-[1100px] max-w-[96vw] h-[80vh] card shadow-hover overflow-hidden flex flex-col">
        <header className="px-5 py-3 border-b border-line flex items-center justify-between">
          <div>
            <h2 className="text-sm text-ink font-medium">Command audit log</h2>
            <div className="text-[11px] text-ink-faint mt-0.5">
              Every command HIVE classified — allowed, confirmed, or blocked.
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => void refresh()}
              className="btn-ghost text-xs"
              disabled={loading}
            >
              {loading ? 'Loading…' : 'Refresh'}
            </button>
            <button
              type="button"
              onClick={downloadCsv}
              className="btn-ghost text-xs inline-flex items-center gap-1.5"
            >
              <IconDownload size={13} /> Export CSV
            </button>
            <button
              type="button"
              onClick={onClose}
              className="text-ink-faint hover:text-ink"
              title="Close"
            >
              <IconX size={16} />
            </button>
          </div>
        </header>

        <div className="px-5 py-3 border-b border-line flex items-center gap-3">
          <IconFilter size={14} className="text-ink-muted" />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search by command, agent, or project…"
            className="input-soft text-xs flex-1"
          />
          <select
            value={classFilter}
            onChange={(e) => setClassFilter(e.target.value)}
            className="input-soft text-xs w-36"
          >
            <option value="all">All classes</option>
            <option value="allowed">Allowed</option>
            <option value="confirmed">Confirmed</option>
            <option value="blocked">Blocked</option>
          </select>
        </div>

        <div className="flex-1 overflow-hidden flex">
          {/* Virtualised list — only the visible rows are in the DOM, so a
              500-row payload doesn't choke the WebView. */}
          <AuditRows
            rows={filtered}
            selectedId={selected?.id}
            onSelect={setSelected}
            err={err}
          />

          {/* Detail panel */}
          {selected && (
            <aside className="w-[360px] shrink-0 border-l border-line bg-surface-2/40 overflow-y-auto p-4 text-xs">
              <div className="font-mono text-ink text-[13px] mb-3 break-all">
                {selected.command}
              </div>
              <Row k="When" v={selected.ts} />
              <Row k="Class" v={selected.classification} />
              <Row k="Rule source" v={selected.decision_source} />
              <Row k="Matched pattern" v={selected.matched_pattern ?? '—'} mono />
              <Row k="Agent" v={selected.agent_id || '—'} />
              <Row k="Project" v={selected.project_id || '—'} />
              <Row k="Working dir" v={selected.working_dir || '—'} mono />
              <Row k="Exit code" v={String(selected.exit_code ?? '—')} mono />
              <Row k="Duration" v={`${selected.duration_ms} ms`} />
              <Row k="User approved" v={
                selected.user_approved === null ? '—' :
                selected.user_approved === 1 ? 'yes' : 'no'
              } />

              {selected.stdout_excerpt && (
                <>
                  <div className="text-[10px] uppercase tracking-wider text-ink-faint mt-3 mb-1">
                    stdout (first 500 chars)
                  </div>
                  <pre className="bg-bg border border-line rounded p-2 whitespace-pre-wrap text-[11px] text-ink">
                    {selected.stdout_excerpt}
                  </pre>
                </>
              )}
              {selected.stderr_excerpt && (
                <>
                  <div className="text-[10px] uppercase tracking-wider text-ink-faint mt-3 mb-1">
                    stderr (first 500 chars)
                  </div>
                  <pre className="bg-bg border border-red-500/20 rounded p-2 whitespace-pre-wrap text-[11px] text-red-500">
                    {selected.stderr_excerpt}
                  </pre>
                </>
              )}
            </aside>
          )}
        </div>

        <footer className="px-5 py-2 border-t border-line text-[11px] text-ink-faint flex items-center justify-between">
          <span>{filtered.length} of {rows.length} commands</span>
          <a
            href="https://v2.tauri.app"
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1 hover:text-ink-muted underline"
          >
            About the policy <IconExternalLink size={10} />
          </a>
        </footer>
      </div>
    </div>
  )
}

function Row({ k, v, mono }: { k: string; v: string; mono?: boolean }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-1 border-b border-line/50">
      <span className="text-ink-faint text-[11px]">{k}</span>
      <span className={clsx('text-ink truncate text-right', mono && 'font-mono')}>{v}</span>
    </div>
  )
}


// ─── Virtualised audit-row list ─────────────────────────────────────────────


interface AuditRowsProps {
  rows: AuditRow[]
  selectedId: number | undefined
  onSelect: (row: AuditRow) => void
  err: string | null
}

const ROW_HEIGHT = 32

function AuditRows({ rows, selectedId, onSelect, err }: AuditRowsProps) {
  const scrollRef = useRef<HTMLDivElement | null>(null)
  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ROW_HEIGHT,
    overscan: 8,
  })

  if (err) {
    return (
      <div className="flex-1 overflow-y-auto">
        <div className="m-4 text-xs text-red-500 bg-red-500/10 border border-red-500/20 rounded-soft px-3 py-2">
          {err}
        </div>
      </div>
    )
  }

  if (rows.length === 0) {
    return (
      <div className="flex-1 overflow-y-auto">
        <div className="m-8 text-center text-xs text-ink-muted">
          No commands recorded.
        </div>
      </div>
    )
  }

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      {/* Header — outside the scrolled region so it doesn't move. */}
      <div
        className="grid items-center px-3 py-2 text-[11px] text-ink-faint border-b border-line shrink-0"
        style={{ gridTemplateColumns: '170px 90px 1fr 160px 50px 60px' }}
      >
        <div>When</div>
        <div>Class</div>
        <div>Command</div>
        <div>Agent</div>
        <div className="text-right">Exit</div>
        <div className="text-right">ms</div>
      </div>

      <div ref={scrollRef} className="flex-1 overflow-y-auto">
        <div style={{ height: virtualizer.getTotalSize(), position: 'relative' }}>
          {virtualizer.getVirtualItems().map((vi) => {
            const r = rows[vi.index]
            const active = selectedId === r.id
            return (
              <button
                key={r.id}
                type="button"
                onClick={() => onSelect(r)}
                style={{
                  position: 'absolute',
                  top: 0,
                  left: 0,
                  right: 0,
                  transform: `translateY(${vi.start}px)`,
                  height: ROW_HEIGHT,
                  gridTemplateColumns: '170px 90px 1fr 160px 50px 60px',
                }}
                className={clsx(
                  'grid items-center px-3 text-sm cursor-pointer border-b border-line text-left gap-3',
                  active ? 'bg-surface-2' : 'hover:bg-surface-2/60',
                )}
              >
                <div className="text-[11px] text-ink-faint whitespace-nowrap truncate">{r.ts}</div>
                <div className={clsx(
                  'text-[11px] uppercase tracking-wider truncate',
                  CLASSIFICATION_COLOUR[r.classification] ?? 'text-ink-muted',
                )}>
                  {r.classification}
                </div>
                <div className="text-ink font-mono text-xs truncate min-w-0" title={r.command}>
                  {r.command}
                </div>
                <div className="text-xs text-ink-muted truncate">{r.agent_id || '—'}</div>
                <div className="text-right text-xs font-mono">{r.exit_code ?? '—'}</div>
                <div className="text-right text-xs text-ink-faint font-mono">{r.duration_ms || ''}</div>
              </button>
            )
          })}
        </div>
      </div>
    </div>
  )
}
