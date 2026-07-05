/**
 * One project tile in the Active projects grid.
 *
 *   ┌─[stripe colour from status]────────────┐
 *   │ 🪴  Project name                       │
 *   │     One-line task description          │
 *   │                                        │
 *   │ ● running · 3 agents · 4m 12s          │
 *   └────────────────────────────────────────┘
 */
import {
  IconBookmarkPlus,
  IconCopy,
  IconExternalLink,
  IconFileExport,
  IconPencil,
  IconTrash,
  IconX,
} from '@tabler/icons-react'
import clsx from 'clsx'
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../../lib/api'
import { toast } from '../../lib/toast'
import { useProjectTabs } from '../../stores/projectTabs'
import { useSessions, type ProjectState } from '../../stores/sessions'
import { useTemplates } from '../../stores/templates'
import { ContextMenu, type ContextMenuItem } from '../ui/ContextMenu'

const STATUS_TONE: Record<string, { stripe: string; dot: string; label: string }> = {
  active: { stripe: 'bg-emerald-400', dot: 'bg-emerald-400', label: 'active' },
  idle: { stripe: 'bg-violet-400', dot: 'bg-violet-400', label: 'idle — resumable' },
  running: { stripe: 'bg-emerald-400', dot: 'bg-emerald-400 animate-pulse', label: 'running' },
  starting: { stripe: 'bg-amber-400', dot: 'bg-amber-400 animate-pulse', label: 'starting' },
  planning: { stripe: 'bg-amber-400', dot: 'bg-amber-400 animate-pulse', label: 'planning' },
  spawning: { stripe: 'bg-amber-400', dot: 'bg-amber-400 animate-pulse', label: 'spawning' },
  awaiting_user: { stripe: 'bg-sky-400', dot: 'bg-sky-400', label: 'awaiting you' },
  waiting_approval: { stripe: 'bg-orange-400', dot: 'bg-orange-400 animate-pulse', label: 'needs approval' },
  completed: { stripe: 'bg-ink-faint/40', dot: 'bg-ink-faint', label: 'completed' },
  failed: { stripe: 'bg-red-400', dot: 'bg-red-400', label: 'failed' },
  cancelled: { stripe: 'bg-ink-faint/40', dot: 'bg-ink-faint', label: 'cancelled' },
  closed: { stripe: 'bg-ink-faint/40', dot: 'bg-ink-faint', label: 'closed' },
}

function emojiFor(name: string): string {
  // Stable but cheap — pick from a small set so cards feel varied.
  const palette = ['🪴', '🐝', '🍯', '✨', '🧠', '🛠', '📦', '🌱', '🌶', '🌀']
  let hash = 0
  for (let i = 0; i < name.length; i++) hash = (hash * 31 + name.charCodeAt(i)) >>> 0
  return palette[hash % palette.length]
}

function elapsedSince(iso: string | undefined): string {
  if (!iso) return ''
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return ''
  const s = Math.max(0, Math.floor((Date.now() - then) / 1000))
  if (s < 60) return `${s}s`
  if (s < 3600) return `${Math.floor(s / 60)}m`
  if (s < 86_400) return `${Math.floor(s / 3600)}h`
  return `${Math.floor(s / 86_400)}d`
}

interface Props {
  project: ProjectState
}

export function ProjectCard({ project }: Props) {
  const navigate = useNavigate()
  const openTab = useProjectTabs((s) => s.openTab)
  const removeFromStore = useSessions((s) => s.removeSession)
  const saveTemplate = useTemplates((s) => s.save)
  const [menuPos, setMenuPos] = useState<{ x: number; y: number } | null>(null)

  const info = project.info
  const tone = STATUS_TONE[info.status] ?? STATUS_TONE.completed
  const agentCount = Object.keys(project.agents).length

  function openProject() {
    openTab(info.session_id)
    navigate(`/project/${info.session_id}`)
  }

  async function closeSession() {
    try {
      await api.post(`/api/sessions/${info.session_id}/close`)
    } catch { /* noop — backend may already be closed */ }
  }

  async function resumeSession() {
    try {
      await api.post(`/api/sessions/${info.session_id}/resume`)
      useSessions.setState((s) => {
        const p = s.sessions[info.session_id]
        if (!p) return s
        return { sessions: { ...s.sessions, [info.session_id]: { ...p, info: { ...p.info, status: 'active' } } } }
      })
      openProject()
    } catch (e) {
      toast.error(`Couldn't resume: ${e instanceof Error ? e.message : e}`)
    }
  }

  async function deletePermanently() {
    if (!confirm(`Delete "${info.name || info.session_id}" permanently?\nThe SQLite history is dropped — this can't be undone.`)) return
    try {
      await api.delete(`/api/sessions/${info.session_id}`)
      removeFromStore(info.session_id)
    } catch (e) {
      // Keep the card — deleting only the local view while claiming the
      // history was dropped would be a lie.
      toast.error(`Delete failed: ${e instanceof Error ? e.message : e}`)
    }
  }

  async function exportToMarkdown() {
    let body = `# ${info.name || 'Project'}\n\n_${info.session_id} · ${info.status}_\n\n`
    for (const m of project.history) {
      const who = m.role === 'user' ? 'You' : m.role === 'assistant' ? 'Orchestrator' : 'System'
      body += `### ${who}\n\n${m.content}\n\n`
    }
    try {
      const blob = new Blob([body], { type: 'text/markdown' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `${info.session_id}.md`
      a.click()
      URL.revokeObjectURL(url)
    } catch (e) {
      console.error('export failed', e)
      toast.error(
        `Couldn't export this session. ${e instanceof Error ? e.message : ''}`.trim(),
      )
    }
  }

  function rename() {
    const next = prompt('New project name', info.name || '')
    if (next === null || !next.trim()) return
    // No backend endpoint yet — only renames the local view. 9D wires it.
    useSessions.setState((s) => {
      const p = s.sessions[info.session_id]
      if (!p) return s
      return { sessions: { ...s.sessions, [info.session_id]: { ...p, info: { ...p.info, name: next.trim() } } } }
    })
  }

  function duplicateAsTemplate() {
    saveTemplate({
      name: info.name || 'Untitled',
      task: info.name || '',
      model: 'claude:opus',
      approval_mode: info.approval_mode || 'full-auto',
      emoji: emojiFor(info.name || info.session_id),
    })
  }

  function saveAsTemplate() {
    const name = prompt('Template name', info.name || '')
    if (!name?.trim()) return
    saveTemplate({
      name: name.trim().slice(0, 60),
      task: info.name || '',
      model: 'claude:opus',
      approval_mode: info.approval_mode || 'full-auto',
      emoji: emojiFor(info.name || info.session_id),
    })
  }

  const menuItems: ContextMenuItem[] = [
    ...(info.status === 'idle'
      ? [{ label: 'Resume', icon: <IconExternalLink size={14} />, onClick: () => void resumeSession() }]
      : []),
    { label: 'Open', icon: <IconExternalLink size={14} />, onClick: openProject },
    { label: 'Rename', icon: <IconPencil size={14} />, onClick: rename },
    { label: 'Save as template', icon: <IconBookmarkPlus size={14} />, onClick: saveAsTemplate },
    { label: 'Duplicate as template', icon: <IconCopy size={14} />, onClick: duplicateAsTemplate },
    { label: 'Export to Markdown', icon: <IconFileExport size={14} />, onClick: () => void exportToMarkdown() },
    { divider: true, label: 'Close project', icon: <IconX size={14} />, onClick: () => void closeSession() },
    { divider: true, label: 'Delete permanently…', icon: <IconTrash size={14} />, danger: true, onClick: () => void deletePermanently() },
  ]

  return (
    <>
      <button
        type="button"
        onClick={openProject}
        onContextMenu={(e) => {
          e.preventDefault()
          setMenuPos({ x: e.clientX, y: e.clientY })
        }}
        className="card card-hover relative overflow-hidden text-left flex flex-col p-5 min-h-[148px]"
      >
        <span className={clsx('absolute left-0 right-0 top-0 h-[3px]', tone.stripe)} />

        <div className="flex items-start gap-3 mb-3">
          <div className="text-2xl leading-none mt-0.5">{emojiFor(info.name || info.session_id)}</div>
          <div className="min-w-0 flex-1">
            <div className="text-ink text-sm font-medium truncate">{info.name || 'Untitled project'}</div>
            <div className="text-ink-muted text-xs truncate mt-0.5">
              {info.session_id} · {info.approval_mode}
            </div>
          </div>
        </div>

        <div className="flex-1" />

        <div className="flex items-center justify-between text-xs">
          <div className="flex items-center gap-1.5 text-ink-muted">
            <span className={clsx('w-1.5 h-1.5 rounded-full', tone.dot)} />
            <span className="capitalize">{tone.label}</span>
          </div>
          <div className="text-ink-faint">
            {agentCount > 0 ? `${agentCount} agent${agentCount === 1 ? '' : 's'} · ` : ''}
            {elapsedSince(info.last_active || info.created_at)}
          </div>
        </div>
      </button>

      <ContextMenu
        open={menuPos !== null}
        x={menuPos?.x ?? 0}
        y={menuPos?.y ?? 0}
        items={menuItems}
        onClose={() => setMenuPos(null)}
      />
    </>
  )
}

interface NewProjectCardProps {
  onClick: () => void
}

export function NewProjectCard({ onClick }: NewProjectCardProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="rounded-card border border-dashed border-line hover:border-ink-faint text-ink-muted hover:text-ink transition-colors min-h-[148px] flex flex-col items-center justify-center gap-1.5"
    >
      <div className="w-9 h-9 rounded-full bg-surface-2 flex items-center justify-center">
        <span className="text-lg leading-none">＋</span>
      </div>
      <div className="text-sm">New project</div>
      <div className="text-[11px] text-ink-faint">Ctrl + T</div>
    </button>
  )
}
