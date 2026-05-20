/**
 * Quick-start composer on the Projects dashboard.
 *
 *   ┌───────────────────────────────────────────────────────────┐
 *   │  task textarea (Ctrl + Enter)                              │
 *   ├───────────────────────────────────────────────────────────┤
 *   │  📁 workspace (real folder picker + recents)               │
 *   │  🧠 Orchestrator: Opus / Sonnet / Haiku / Ollama (warn)    │
 *   │  🛡 Approval: full-auto / checkpoint / manual              │
 *   │                                                            │
 *   │                                  Ctrl + Enter  [ Start → ] │
 *   └───────────────────────────────────────────────────────────┘
 *
 * On submit we run preflight FIRST. If blockers exist, the modal opens
 * and the project never starts. Self-healing git identity in the
 * backend covers the common case, but we still show the modal so the
 * user knows what just happened.
 */
import {
  IconAlertTriangle,
  IconArrowRight,
  IconChevronDown,
  IconCpu,
  IconFolder,
  IconShieldCheck,
} from '@tabler/icons-react'
import { open as openDialog } from '@tauri-apps/plugin-dialog'
import clsx from 'clsx'
import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../../lib/api'
import { useProjectTabs } from '../../stores/projectTabs'
import { useSessions } from '../../stores/sessions'
import { useSettings } from '../../stores/settings'
import {
  PreflightModal,
  runPreflight,
  type PreflightResponse,
} from './PreflightModal'

interface ModelOption {
  value: string
  label: string
  tier: 'recommended' | 'standard' | 'cheap' | 'local'
  hint?: string
}

const BASE_MODELS: ModelOption[] = [
  { value: 'claude:opus',   label: 'Claude Opus 4.7',  tier: 'recommended', hint: 'Best at planning. The architectural recommendation.' },
  { value: 'claude:sonnet', label: 'Claude Sonnet 4.6', tier: 'standard',  hint: 'Solid + cheaper. Fine for routine orchestration.' },
  { value: 'claude:haiku',  label: 'Claude Haiku 4.5', tier: 'cheap',      hint: 'Fast and cheap; may miss nuance on complex teams.' },
]

const APPROVAL_CHOICES = [
  { value: 'full-auto',  label: 'Full auto' },
  { value: 'checkpoint', label: 'Checkpoint' },
  { value: 'manual',     label: 'Manual' },
]

interface QuickStartProps {
  initialTask?: string
}

export function QuickStart({ initialTask = '' }: QuickStartProps) {
  const settings = useSettings()
  const [task, setTask] = useState(initialTask)
  const [model, setModel] = useState(settings.orchestratorModel || 'claude:opus')
  const [approval, setApproval] = useState<string>(settings.approvalMode || 'full-auto')
  const [workspace, setWorkspace] = useState(
    settings.recentWorkspaces[0] || settings.projectsDir || '',
  )
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [preflight, setPreflight] = useState<PreflightResponse | null>(null)
  const [ollamaModels, setOllamaModels] = useState<string[]>([])

  const navigate = useNavigate()
  const openTab = useProjectTabs((s) => s.openTab)
  const upsert = useSessions((s) => s.upsertSession)

  useEffect(() => {
    // Detect Ollama once so the picker can show local models.
    api
      .get<{ ollama_reachable: boolean; models: string[] }>('/api/detect/ollama')
      .then((r) => setOllamaModels(r.ollama_reachable ? r.models : []))
      .catch(() => setOllamaModels([]))
  }, [])

  const allModels: ModelOption[] = [
    ...BASE_MODELS,
    ...ollamaModels.map<ModelOption>((m) => ({
      value: `ollama:${m}`,
      label: `Ollama · ${m}`,
      tier: 'local',
      hint: 'Local model — less capable than Opus for planning, but $0.',
    })),
  ]

  function rememberWorkspace(path: string) {
    if (!path) return
    const next = [path, ...settings.recentWorkspaces.filter((p) => p !== path)].slice(0, 10)
    settings.update({ recentWorkspaces: next })
  }

  async function pickWorkspace() {
    try {
      const picked = await openDialog({
        directory: true,
        multiple: false,
        title: 'Pick a workspace folder for this project',
      })
      if (typeof picked === 'string' && picked) {
        setWorkspace(picked)
        rememberWorkspace(picked)
      }
    } catch (e) {
      // Running outside Tauri (pure web preview) — surface a friendly error
      setError(e instanceof Error ? e.message : 'Folder picker not available')
    }
  }

  async function attemptStart() {
    if (!task.trim() || submitting) return
    // Workspace is required — the backend now refuses an empty path
    // with HTTP 400, so we mirror that check at the client to avoid
    // a confusing round-trip when the user hits Ctrl+Enter.
    if (!workspace.trim()) {
      setError('Choose a workspace folder.')
      return
    }
    setSubmitting(true)
    setError(null)
    try {
      const res = await runPreflight(workspace)
      if (!res.ok) {
        // Open modal — don't launch the session until the user resolves it.
        setPreflight(res)
        setSubmitting(false)
        return
      }
      await launchSession()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Preflight failed')
      setSubmitting(false)
    }
  }

  async function launchSession() {
    setSubmitting(true)
    setError(null)
    try {
      const res = await api.post<{ session_id: string }>('/api/sessions', {
        task: task.trim(),
        model,
        approval_mode: approval,
        project_path: workspace || undefined,
        max_turns: 20,
      })
      upsert({
        session_id: res.session_id,
        name: task.trim().slice(0, 60),
        status: 'starting',
        approval_mode: approval,
        created_at: new Date().toISOString(),
        last_active: new Date().toISOString(),
      })
      openTab(res.session_id)
      rememberWorkspace(workspace)
      setTask('')
      navigate(`/project/${res.session_id}`)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not start session')
    } finally {
      setSubmitting(false)
    }
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
      e.preventDefault()
      void attemptStart()
    }
  }

  const orchIsLocal = model.startsWith('ollama:')

  return (
    <>
      <div className="card card-hover p-5">
        <textarea
          value={task}
          onChange={(e) => setTask(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder="What should we build today?"
          rows={3}
          className="w-full bg-transparent text-ink placeholder:text-ink-faint outline-none resize-none text-[15px] leading-relaxed"
          autoFocus
          data-quick-start
        />

        <div className="flex flex-wrap items-center gap-2 mt-3 pt-3 border-t border-line">
          <WorkspaceChip
            value={workspace}
            recents={settings.recentWorkspaces}
            onPick={() => void pickWorkspace()}
            onSelect={(p) => {
              setWorkspace(p)
              rememberWorkspace(p)
            }}
          />

          <ModelChip
            value={model}
            options={allModels}
            onChange={setModel}
          />

          <ApprovalChip value={approval} onChange={setApproval} />

          <div className="ml-auto flex items-center gap-3">
            <span className="text-[11px] text-ink-faint">Ctrl + Enter</span>
            <button
              type="button"
              disabled={!task.trim() || !workspace.trim() || submitting}
              onClick={() => void attemptStart()}
              title={
                !workspace.trim()
                  ? 'Select a workspace folder first'
                  : !task.trim()
                    ? 'Describe a task'
                    : 'Start the project'
              }
              className="btn-primary inline-flex items-center gap-1.5 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              <span>{submitting ? 'Checking…' : 'Start'}</span>
              <IconArrowRight size={16} strokeWidth={1.75} />
            </button>
          </div>
        </div>

        {orchIsLocal && (
          <div className="mt-3 px-3 py-2 rounded-soft bg-amber-500/10 border border-amber-500/30 text-[11px] text-amber-700 dark:text-amber-300 flex items-start gap-2">
            <IconAlertTriangle size={12} strokeWidth={1.75} className="shrink-0 mt-0.5" />
            <span>
              Local models are less capable than Opus for planning. This may
              affect team selection. Your choice stands — workers can still be
              Claude.
            </span>
          </div>
        )}

        {error && (
          <div className="mt-3 text-xs text-red-500 bg-red-500/10 border border-red-500/20 rounded-soft px-3 py-2">
            {error}
          </div>
        )}
      </div>

      {preflight && (
        <PreflightModal
          data={preflight}
          projectPath={workspace}
          onCancel={() => setPreflight(null)}
          onProceed={() => {
            setPreflight(null)
            void launchSession()
          }}
        />
      )}
    </>
  )
}

/* ── Chips ──────────────────────────────────────────────────────────────── */

function WorkspaceChip({
  value,
  recents,
  onPick,
  onSelect,
}: {
  value: string
  recents: string[]
  onPick: () => void
  onSelect: (p: string) => void
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    function onDoc(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [])

  const isEmpty = !value.trim()
  // "Required" red label vs. the actual folder path. Empty state pulls
  // attention; the chip stays clickable so the user can still pick.
  const display = isEmpty ? 'Required — pick a folder' : value
  const short =
    display.length > 26 && !isEmpty ? '…' + display.slice(-25) : display

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className={clsx(
          'inline-flex items-center gap-2 text-xs rounded-full pl-2.5 pr-2 py-1 transition-colors',
          isEmpty
            ? 'text-red-500 bg-red-500/10 border border-red-500/40 hover:bg-red-500/15'
            : 'text-ink-muted bg-surface-2 hover:bg-surface border border-line',
        )}
        title={isEmpty ? 'Workspace is required — pick a folder' : value}
        data-workspace-required={isEmpty || undefined}
      >
        <IconFolder size={14} strokeWidth={1.75} />
        <span className={clsx('truncate max-w-[200px]', isEmpty ? 'text-red-500 font-medium' : 'text-ink')}>
          {short}
        </span>
        <IconChevronDown size={12} className="opacity-60" />
      </button>

      {open && (
        <div className="absolute z-10 top-full mt-1.5 left-0 card shadow-hover py-1 min-w-[280px]">
          <button
            type="button"
            onClick={() => {
              setOpen(false)
              onPick()
            }}
            className="w-full text-left text-sm px-3 py-1.5 hover:bg-surface-2 text-ink"
          >
            📁 Pick a folder…
          </button>
          {recents.length > 0 && (
            <>
              <div className="text-[10px] uppercase tracking-wider text-ink-faint px-3 pt-2 pb-1">
                Recent
              </div>
              {recents.map((p) => (
                <button
                  key={p}
                  type="button"
                  onClick={() => {
                    onSelect(p)
                    setOpen(false)
                  }}
                  className="w-full text-left text-xs px-3 py-1 hover:bg-surface-2 text-ink-muted truncate"
                  title={p}
                >
                  {p}
                </button>
              ))}
            </>
          )}
        </div>
      )}
    </div>
  )
}

function ModelChip({
  value,
  options,
  onChange,
}: {
  value: string
  options: ModelOption[]
  onChange: (v: string) => void
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement | null>(null)
  useEffect(() => {
    function onDoc(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [])

  const current = options.find((o) => o.value === value) || options[0]

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="inline-flex items-center gap-2 text-xs text-ink-muted bg-surface-2 hover:bg-surface border border-line rounded-full pl-2.5 pr-2 py-1 transition-colors"
        title="Orchestrator model — not the worker model"
      >
        <IconCpu size={14} strokeWidth={1.75} />
        <span className="text-ink-faint">Orchestrator:</span>
        <span className="text-ink">{current?.label || value}</span>
        <IconChevronDown size={12} className="opacity-60" />
      </button>

      {open && (
        <div className="absolute z-10 top-full mt-1.5 left-0 card shadow-hover py-1 min-w-[300px]">
          <div className="text-[10px] uppercase tracking-wider text-ink-faint px-3 pt-2 pb-1">
            Orchestrator model (the planner)
          </div>
          {options.map((opt) => (
            <button
              key={opt.value}
              type="button"
              onClick={() => {
                onChange(opt.value)
                setOpen(false)
              }}
              className={clsx(
                'w-full text-left px-3 py-2 hover:bg-surface-2',
                value === opt.value && 'bg-surface-2',
              )}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="text-sm text-ink">{opt.label}</span>
                {opt.tier === 'recommended' && (
                  <span className="text-[10px] text-accent border border-accent/40 rounded px-1.5 py-px">
                    recommended
                  </span>
                )}
                {opt.tier === 'local' && (
                  <span className="text-[10px] text-ink-faint">local</span>
                )}
              </div>
              {opt.hint && (
                <div className="text-[11px] text-ink-faint mt-0.5">{opt.hint}</div>
              )}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

function ApprovalChip({
  value,
  onChange,
}: {
  value: string
  onChange: (v: string) => void
}) {
  return (
    <label className="inline-flex items-center gap-2 text-xs text-ink-muted bg-surface-2 hover:bg-surface border border-line rounded-full pl-2.5 pr-1.5 py-1 transition-colors cursor-pointer">
      <IconShieldCheck size={14} strokeWidth={1.75} />
      <span>Approval</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="bg-transparent text-ink outline-none cursor-pointer pr-0.5"
      >
        {APPROVAL_CHOICES.map((opt) => (
          <option key={opt.value} value={opt.value} className="bg-surface text-ink">
            {opt.label}
          </option>
        ))}
      </select>
    </label>
  )
}
