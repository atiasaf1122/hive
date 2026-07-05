/**
 * Preflight blocker modal — opens when the user tries to start a project
 * but the environment is missing something critical (almost always git
 * identity in our case, which was the snake-game stall bug).
 *
 * The modal lists blockers and warnings, lets the user auto-fix git
 * identity, and only releases control back to QuickStart once preflight
 * passes (or the user explicitly bails out).
 */
import { IconAlertTriangle, IconCheck, IconX } from '@tabler/icons-react'
import { useEffect, useState } from 'react'
import { api } from '../../lib/api'

export interface PreflightIssue {
  id: string
  severity: 'blocker' | 'warning'
  title: string
  detail: string
  fix_hint: string
  auto_fixable: boolean
}

export interface PreflightResponse {
  ok: boolean
  blockers: PreflightIssue[]
  warnings: PreflightIssue[]
  git_user_name: string
  git_user_email: string
}

export async function runPreflight(projectPath: string): Promise<PreflightResponse> {
  const url = new URL('/api/preflight/check', 'http://x')
  if (projectPath) url.searchParams.set('project_path', projectPath)
  return api.get<PreflightResponse>(url.pathname + url.search)
}

interface Props {
  data: PreflightResponse
  projectPath: string
  onCancel: () => void
  onProceed: () => void
}

export function PreflightModal({ data, projectPath, onCancel, onProceed }: Props) {
  const [fixed, setFixed] = useState<PreflightResponse>(data)

  // Re-check every 2 s while the modal is open in case the user fixed
  // something in another terminal.
  useEffect(() => {
    const t = window.setInterval(() => {
      runPreflight(projectPath).then(setFixed).catch(() => {})
    }, 2000)
    return () => window.clearInterval(t)
  }, [projectPath])

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/30 backdrop-blur-sm">
      <div className="w-[560px] max-w-[92vw] card shadow-hover overflow-hidden flex flex-col max-h-[85vh]">
        <header className="px-5 py-3 border-b border-line flex items-center justify-between">
          <div className="flex items-center gap-2">
            <IconAlertTriangle size={16} className="text-amber-500" />
            <h2 className="text-sm text-ink">Before we start…</h2>
          </div>
          <button type="button" onClick={onCancel} className="text-ink-faint hover:text-ink">
            <IconX size={16} />
          </button>
        </header>

        <div className="overflow-y-auto p-5 space-y-3">
          <p className="text-sm text-ink-muted">
            HIVE checks a couple of things before spawning agents so projects
            don't stall silently mid-run.
          </p>

          {fixed.blockers.map((issue) => (
            <IssueRow key={issue.id} issue={issue} severity="blocker" />
          ))}
          {fixed.warnings.map((issue) => (
            <IssueRow key={issue.id} issue={issue} severity="warning" />
          ))}

          {fixed.ok && (
            <div className="text-xs text-emerald-500 inline-flex items-center gap-1.5">
              <IconCheck size={14} /> Looks good — ready to start.
            </div>
          )}
        </div>

        <footer className="px-5 py-3 border-t border-line flex items-center justify-end gap-2">
          <button type="button" onClick={onCancel} className="btn-ghost text-xs">
            Cancel
          </button>
          <button
            type="button"
            // Defensive onClick guard: even if the disabled styling fails
            // to render (custom utility class, dark-mode contrast issue),
            // the action still won't fire while blockers exist.
            onClick={() => { if (fixed.ok) onProceed() }}
            disabled={!fixed.ok}
            aria-disabled={!fixed.ok}
            title={
              fixed.ok
                ? 'Start the project with this configuration'
                : `Resolve ${fixed.blockers.length} blocker${fixed.blockers.length === 1 ? '' : 's'} above first`
            }
            // When blocked, swap btn-primary out for a flat surface
            // colour so the orange CTA can't be mistaken for an active
            // button (previous "opacity-50" alone wasn't a strong
            // enough signal in dark mode — users tried to click it).
            className={
              fixed.ok
                ? 'btn-primary text-xs'
                : 'text-xs px-3 py-1.5 rounded-soft bg-surface-2 text-ink-faint border border-line cursor-not-allowed'
            }
          >
            {fixed.ok ? 'Start project' : 'Fix blockers to continue'}
          </button>
        </footer>
      </div>
    </div>
  )
}

function IssueRow({ issue, severity }: { issue: PreflightIssue; severity: 'blocker' | 'warning' }) {
  const [fixing, setFixing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')

  async function autoFix() {
    if (issue.id !== 'git-identity') return
    if (!name.trim() || !email.includes('@')) {
      setError('Name and a real email are required.')
      return
    }
    setFixing(true)
    setError(null)
    try {
      const res = await api.post<{ ok: boolean; error?: string }>(
        '/api/preflight/fix-git',
        { name: name.trim(), email: email.trim() },
      )
      if (!res.ok) {
        setError(res.error || 'auto-fix failed')
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'auto-fix failed')
    } finally {
      setFixing(false)
    }
  }

  return (
    <div
      className={
        'card p-3 ' +
        (severity === 'blocker'
          ? 'border-red-500/30 bg-red-500/5'
          : 'border-amber-500/30 bg-amber-500/5')
      }
    >
      <div className="flex items-start gap-2">
        <IconAlertTriangle
          size={14}
          className={severity === 'blocker' ? 'text-red-500 mt-0.5' : 'text-amber-500 mt-0.5'}
        />
        <div className="flex-1 min-w-0">
          <div className="text-sm text-ink">{issue.title}</div>
          <div className="text-xs text-ink-muted mt-0.5 leading-relaxed">
            {issue.detail}
          </div>
          {issue.fix_hint && (
            <pre className="mt-2 text-[11px] bg-surface-2 border border-line rounded p-2 text-ink whitespace-pre-wrap font-mono">
              {issue.fix_hint}
            </pre>
          )}

          {issue.auto_fixable && issue.id === 'git-identity' && (
            <div className="mt-2 space-y-2">
              <div className="flex items-center gap-2">
                <input
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="Your name"
                  className="input-soft text-xs flex-1"
                />
                <input
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="you@example.com"
                  className="input-soft text-xs flex-1"
                />
              </div>
              <button
                type="button"
                onClick={() => void autoFix()}
                disabled={fixing}
                className="btn-primary text-xs"
              >
                {fixing ? 'Configuring…' : 'Configure for me'}
              </button>
              {error && <div className="text-[11px] text-red-500">{error}</div>}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
