/**
 * Slash-command autocomplete dropdown.
 *
 * Triggered when the composer text starts with "/". Filters by what's
 * typed after the slash. Keyboard nav: ↑/↓ to move, Enter to pick, Esc
 * to close. Commands are grouped by category so a 20-item list still
 * reads cleanly.
 */
import clsx from 'clsx'
import { useEffect, useMemo, useRef } from 'react'

export type SlashCategory = 'Session' | 'Project' | 'Tools' | 'Help'

export interface SlashCommand {
  name: string
  hint: string
  body: string           // text to insert; trailing space ⇒ command takes a param
  category: SlashCategory
  shortcut?: string      // optional keyboard hint, shown right-aligned
}

export const SLASH_COMMANDS: SlashCommand[] = [
  // Session
  { name: '/clear',     hint: 'Clear the visible conversation history',         body: '/clear',         category: 'Session' },
  { name: '/cost',      hint: 'Show current session cost',                      body: '/cost',          category: 'Session' },
  { name: '/compact',   hint: 'Compact conversation context (summarise older turns)', body: '/compact', category: 'Session' },
  { name: '/clear-context', hint: 'Drop all prior context — start fresh',       body: '/clear-context', category: 'Session' },
  { name: '/model',     hint: 'Change Orchestrator model mid-session',          body: '/model ',        category: 'Session' },
  { name: '/memory',    hint: "Show the orchestrator's memory entries",         body: '/memory',        category: 'Session' },
  { name: '/history',   hint: 'List past turns with timestamps',                body: '/history',       category: 'Session' },

  // Project
  { name: '/init',          hint: 'Create CLAUDE.md for this project',          body: '/init',          category: 'Project' },
  { name: '/save-template', hint: 'Save this configuration as a template',     body: '/save-template', category: 'Project' },
  { name: '/export',        hint: 'Export the conversation to Markdown',        body: '/export',        category: 'Project' },
  { name: '/close',         hint: 'Close this project (agents stop, history kept)', body: '/close',     category: 'Project', shortcut: 'Ctrl+W' },
  { name: '/workspace',     hint: "Show or change the project's workspace folder", body: '/workspace ', category: 'Project' },

  // Tools
  { name: '/agents',    hint: 'List active agents in this session',             body: '/agents',        category: 'Tools' },
  { name: '/skills',    hint: 'List active skills for this session',            body: '/skills',        category: 'Tools' },
  { name: '/tools',     hint: 'List available MCP tools',                       body: '/tools',         category: 'Tools' },
  { name: '/pause',     hint: 'Pause all running agents',                       body: '/pause',         category: 'Tools' },
  { name: '/resume',    hint: 'Resume paused agents',                           body: '/resume',        category: 'Tools' },
  { name: '/status',    hint: 'Show session status + cost + tokens',            body: '/status',        category: 'Tools' },

  // Help
  { name: '/search',    hint: 'Open global search',                             body: '/search',        category: 'Help', shortcut: 'Ctrl+K' },
  { name: '/help',      hint: 'Open the help panel for this page',              body: '/help',          category: 'Help', shortcut: 'Ctrl+Shift+?' },
]

interface Props {
  query: string
  selectedIndex: number
  onSelect: (cmd: SlashCommand) => void
  onMove: (delta: 1 | -1) => void
}

export function filterCommands(query: string): SlashCommand[] {
  const q = query.toLowerCase()
  if (!q.startsWith('/')) return []
  const tail = q.slice(1)
  if (!tail) return SLASH_COMMANDS
  return SLASH_COMMANDS.filter(
    (c) => c.name.slice(1).startsWith(tail) || c.hint.toLowerCase().includes(tail),
  )
}

export function SlashMenu({ query, selectedIndex, onSelect, onMove }: Props) {
  const items = useMemo(() => filterCommands(query), [query])
  const listRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    const nodes = listRef.current?.querySelectorAll('[data-slash-item]')
    const node = nodes?.[selectedIndex] as HTMLElement | undefined
    node?.scrollIntoView({ block: 'nearest' })
  }, [selectedIndex])

  if (items.length === 0) return null

  const order: SlashCategory[] = ['Session', 'Project', 'Tools', 'Help']
  const grouped = new Map<SlashCategory, SlashCommand[]>()
  for (const c of order) grouped.set(c, [])
  for (const item of items) grouped.get(item.category)?.push(item)

  let runningIndex = -1

  return (
    <div
      role="listbox"
      aria-label="Slash commands"
      className="absolute bottom-full left-0 right-0 mb-2 card overflow-hidden max-h-[320px] shadow-hover"
    >
      <div ref={listRef} className="max-h-[320px] overflow-y-auto py-1">
        {order.map((cat) => {
          const groupItems = grouped.get(cat) ?? []
          if (groupItems.length === 0) return null
          return (
            <div key={cat} className="py-1">
              <div className="px-3 py-1 text-[10px] uppercase tracking-wider text-ink-faint">
                {cat}
              </div>
              {groupItems.map((cmd) => {
                runningIndex++
                const active = runningIndex === selectedIndex
                const myIndex = runningIndex
                return (
                  <button
                    key={cmd.name}
                    data-slash-item
                    role="option"
                    aria-selected={active}
                    type="button"
                    onMouseEnter={() => onMove(myIndex > selectedIndex ? 1 : -1)}
                    onClick={() => onSelect(cmd)}
                    className={clsx(
                      'w-full flex items-center gap-3 px-3 py-2 text-left text-sm transition-colors',
                      active ? 'bg-surface-2' : 'hover:bg-surface-2',
                    )}
                  >
                    <span className="font-mono text-ink min-w-[140px]">{cmd.name}</span>
                    <span className="text-ink-muted text-xs truncate flex-1">{cmd.hint}</span>
                    {cmd.shortcut && (
                      <span className="text-[10px] text-ink-faint border border-line rounded px-1.5 py-0.5">
                        {cmd.shortcut}
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
  )
}
