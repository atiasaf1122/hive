/**
 * Global command palette (Ctrl + K).
 *
 * One overlay, fuzzy-ish search across:
 *   - Open projects (sessions store)
 *   - Settings shortcuts (Appearance / AI / Telegram / …)
 *   - Slash commands (mapped from SLASH_COMMANDS so the user can recall them)
 *   - Tab navigation (Projects / Automations / Skills / Plugins / Usage / Settings)
 *
 * Keyboard:
 *   ↑ ↓     move selection
 *   Enter   pick
 *   Esc     close
 */
import {
  IconBook2,
  IconChartHistogram,
  IconClock,
  IconHexagon,
  IconLayoutGrid,
  IconPlug,
  IconSettings,
  IconSearch,
  IconTerminal2,
} from '@tabler/icons-react'
import clsx from 'clsx'
import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useSessions } from '../../stores/sessions'
import type { Icon } from '@tabler/icons-react'
import { SLASH_COMMANDS } from '../project/SlashMenu'

interface Item {
  id: string
  label: string
  hint?: string
  group: 'Navigate' | 'Project' | 'Command' | 'Settings'
  icon: Icon
  onPick: () => void
}

interface Props {
  open: boolean
  onClose: () => void
}

export function SearchPalette({ open, onClose }: Props) {
  const [query, setQuery] = useState('')
  const [selected, setSelected] = useState(0)
  const inputRef = useRef<HTMLInputElement | null>(null)
  const navigate = useNavigate()
  const sessions = useSessions((s) => s.sessions)

  const items: Item[] = useMemo(() => {
    const list: Item[] = []
    // Tab navigation
    list.push(
      { id: 'go-projects', label: 'Projects', hint: 'Dashboard', group: 'Navigate', icon: IconLayoutGrid, onPick: () => navigate('/') },
      { id: 'go-autom', label: 'Automations', hint: 'Schedule & webhook pipelines', group: 'Navigate', icon: IconClock, onPick: () => navigate('/automations') },
      { id: 'go-skills', label: 'Skills', hint: 'Discover & install', group: 'Navigate', icon: IconBook2, onPick: () => navigate('/skills') },
      { id: 'go-plugins', label: 'Plugins', hint: 'MCP servers & integrations', group: 'Navigate', icon: IconPlug, onPick: () => navigate('/plugins') },
      { id: 'go-usage', label: 'Usage', hint: 'Rate limits & cost', group: 'Navigate', icon: IconChartHistogram, onPick: () => navigate('/usage') },
      { id: 'go-settings', label: 'Settings', hint: 'Account, AI, integrations', group: 'Navigate', icon: IconSettings, onPick: () => navigate('/settings') },
    )

    for (const p of Object.values(sessions).slice(0, 12)) {
      list.push({
        id: `proj-${p.info.session_id}`,
        label: p.info.name || p.info.session_id,
        hint: `${p.info.status} · ${p.info.session_id}`,
        group: 'Project',
        icon: IconHexagon,
        onPick: () => navigate(`/project/${p.info.session_id}`),
      })
    }

    for (const cmd of SLASH_COMMANDS) {
      list.push({
        id: `slash-${cmd.name}`,
        label: cmd.name,
        hint: cmd.hint,
        group: 'Command',
        icon: IconTerminal2,
        onPick: () => {
          // Slash commands need the composer; bounce to current project view if any.
          // Otherwise just close — user can type them in the active composer.
          onClose()
        },
      })
    }

    return list
  }, [navigate, sessions, onClose])

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return items
    return items.filter(
      (i) =>
        i.label.toLowerCase().includes(q) ||
        (i.hint ?? '').toLowerCase().includes(q),
    )
  }, [items, query])

  useEffect(() => {
    if (open) {
      setQuery('')
      setSelected(0)
      window.setTimeout(() => inputRef.current?.focus(), 10)
    }
  }, [open])

  useEffect(() => {
    if (!open) return
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') {
        e.preventDefault()
        onClose()
      } else if (e.key === 'ArrowDown') {
        e.preventDefault()
        setSelected((i) => Math.min(filtered.length - 1, i + 1))
      } else if (e.key === 'ArrowUp') {
        e.preventDefault()
        setSelected((i) => Math.max(0, i - 1))
      } else if (e.key === 'Enter') {
        e.preventDefault()
        const item = filtered[selected]
        if (item) {
          item.onPick()
          onClose()
        }
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, filtered, selected, onClose])

  if (!open) return null

  // Group items in display order, but preserve global selection index across groups
  const grouped: Record<string, Item[]> = {}
  for (const item of filtered) {
    grouped[item.group] = grouped[item.group] ?? []
    grouped[item.group].push(item)
  }
  let runningIndex = -1
  const order: ('Navigate' | 'Project' | 'Command' | 'Settings')[] = [
    'Navigate', 'Project', 'Command', 'Settings',
  ]

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center pt-[14vh] bg-black/30 backdrop-blur-sm" onClick={onClose}>
      <div
        className="w-[640px] max-w-[92vw] card shadow-hover overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-2 px-3 py-2 border-b border-line">
          <IconSearch size={16} strokeWidth={1.75} className="text-ink-faint" />
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => {
              setQuery(e.target.value)
              setSelected(0)
            }}
            placeholder="Jump to project, page, or command…"
            className="flex-1 bg-transparent outline-none text-sm text-ink placeholder:text-ink-faint py-1"
          />
          <kbd className="text-[10px] text-ink-faint border border-line rounded px-1.5 py-0.5">Esc</kbd>
        </div>

        <div className="max-h-[60vh] overflow-y-auto py-1">
          {filtered.length === 0 && (
            <div className="px-4 py-6 text-center text-sm text-ink-muted">No matches.</div>
          )}

          {order.map((groupName) => {
            const groupItems = grouped[groupName] ?? []
            if (groupItems.length === 0) return null
            return (
              <div key={groupName} className="py-1">
                <div className="px-3 py-1 text-[11px] uppercase tracking-wider text-ink-faint">
                  {groupName}
                </div>
                {groupItems.map((item) => {
                  runningIndex++
                  const active = runningIndex === selected
                  const Icon = item.icon
                  return (
                    <button
                      key={item.id}
                      type="button"
                      onClick={() => {
                        item.onPick()
                        onClose()
                      }}
                      onMouseEnter={() => setSelected(runningIndex)}
                      className={clsx(
                        'w-full flex items-center gap-3 px-3 py-2 text-left text-sm transition-colors',
                        active ? 'bg-surface-2' : 'hover:bg-surface-2',
                      )}
                    >
                      <Icon size={16} strokeWidth={1.5} className="text-ink-muted shrink-0" />
                      <span className="text-ink truncate">{item.label}</span>
                      {item.hint && (
                        <span className="text-ink-faint text-xs truncate ml-auto">
                          {item.hint}
                        </span>
                      )}
                    </button>
                  )
                })}
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}
