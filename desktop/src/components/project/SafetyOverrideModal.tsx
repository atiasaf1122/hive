/**
 * Per-project safety override modal.
 *
 * Opened from the project header. Lets the user tighten (or, with
 * eyes-open intent, loosen) the four most-actionable hard-stop limits
 * for this session only: token budget, max duration, max parallel
 * agents, max edits to one file. The build-time VRAM + disk floors
 * remain non-editable from the UI — they're machine-protection, not
 * cost-control.
 *
 * Persisted via `/api/safety/sessions/{id}/override`. Blank fields
 * (empty string) round-trip to `null` → inherit the global default.
 */
import { IconAlertTriangle, IconRefresh, IconX } from '@tabler/icons-react'
import { useEffect, useState } from 'react'
import { api } from '../../lib/api'

interface OverridePayload {
  max_tokens_per_autonomous_run: number | null
  max_session_duration_hours: number | null
  max_concurrent_agents: number | null
  max_same_file_edits: number | null
  notify_at_burn_ratio: number | null
}

interface EffectivePayload {
  max_concurrent_agents: number
  max_session_duration_hours: number
  max_same_file_edits: number
  max_tokens_per_autonomous_run: number
}

interface ApiResponse {
  session_id: string
  override: OverridePayload
  effective: EffectivePayload
  defaults: EffectivePayload
}

interface Props {
  sessionId: string
  open: boolean
  onClose: () => void
}

export function SafetyOverrideModal({ sessionId, open, onClose }: Props) {
  const [data, setData] = useState<ApiResponse | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  // Form state — string so empty input round-trips to null on save.
  const [tokens, setTokens] = useState('')
  const [hours, setHours] = useState('')
  const [agents, setAgents] = useState('')
  const [edits, setEdits] = useState('')

  useEffect(() => {
    if (!open) return
    setErr(null)
    api
      .get<ApiResponse>(`/api/safety/sessions/${sessionId}/override`)
      .then((d) => {
        setData(d)
        const o = d.override
        setTokens(o.max_tokens_per_autonomous_run?.toString() ?? '')
        setHours(o.max_session_duration_hours?.toString() ?? '')
        setAgents(o.max_concurrent_agents?.toString() ?? '')
        setEdits(o.max_same_file_edits?.toString() ?? '')
      })
      .catch((e) => setErr(e instanceof Error ? e.message : 'load failed'))
  }, [open, sessionId])

  if (!open) return null

  function parseOrNull(s: string): number | null {
    const t = s.trim()
    if (!t) return null
    const n = Number(t)
    return Number.isFinite(n) && n > 0 ? n : null
  }

  async function save() {
    setSaving(true)
    setErr(null)
    try {
      await api.put(`/api/safety/sessions/${sessionId}/override`, {
        max_tokens_per_autonomous_run: parseOrNull(tokens),
        max_session_duration_hours: parseOrNull(hours),
        max_concurrent_agents: parseOrNull(agents),
        max_same_file_edits: parseOrNull(edits),
      })
      onClose()
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'save failed')
    } finally {
      setSaving(false)
    }
  }

  async function clearAll() {
    setSaving(true)
    setErr(null)
    try {
      await api.delete(`/api/safety/sessions/${sessionId}/override`)
      onClose()
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'clear failed')
    } finally {
      setSaving(false)
    }
  }

  const defs = data?.defaults
  const eff = data?.effective

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm">
      <div className="w-[560px] max-w-[92vw] card shadow-hover overflow-hidden">
        <header className="px-5 py-3 border-b border-line flex items-center justify-between">
          <div>
            <h2 className="text-sm text-ink font-medium">Safety limits for this project</h2>
            <div className="text-[11px] text-ink-faint mt-0.5">
              Override the global ceilings for this session only. Empty field = use the default.
            </div>
          </div>
          <button type="button" onClick={onClose} className="text-ink-faint hover:text-ink">
            <IconX size={16} />
          </button>
        </header>

        <div className="p-5 space-y-3">
          {err && (
            <div className="text-xs text-red-500 bg-red-500/10 border border-red-500/20 rounded-soft px-3 py-2">
              {err}
            </div>
          )}

          <LimitRow
            label="Token budget per autonomous run"
            hint="The single biggest cost gate."
            value={tokens}
            onChange={setTokens}
            defaultValue={defs?.max_tokens_per_autonomous_run}
            effectiveValue={eff?.max_tokens_per_autonomous_run}
            placeholder="e.g. 250000"
            unit=""
          />
          <LimitRow
            label="Max session duration"
            hint="Long-running sessions tend to burn weekly Max quota."
            value={hours}
            onChange={setHours}
            defaultValue={defs?.max_session_duration_hours}
            effectiveValue={eff?.max_session_duration_hours}
            placeholder="e.g. 2"
            unit="hours"
          />
          <LimitRow
            label="Max parallel agents"
            hint="Higher = more rate-limit pressure on Claude."
            value={agents}
            onChange={setAgents}
            defaultValue={defs?.max_concurrent_agents}
            effectiveValue={eff?.max_concurrent_agents}
            placeholder="e.g. 3"
            unit=""
          />
          <LimitRow
            label="Max edits to one file"
            hint="Lower catches thrashing earlier."
            value={edits}
            onChange={setEdits}
            defaultValue={defs?.max_same_file_edits}
            effectiveValue={eff?.max_same_file_edits}
            placeholder="e.g. 3"
            unit=""
          />

          {hasLoosening(tokens, defs?.max_tokens_per_autonomous_run)
            || hasLoosening(hours, defs?.max_session_duration_hours)
            || hasLoosening(agents, defs?.max_concurrent_agents)
            || hasLoosening(edits, defs?.max_same_file_edits) ? (
            <div className="text-[11px] text-amber-700 dark:text-amber-300 bg-amber-500/10 border border-amber-500/30 rounded-soft px-3 py-2 flex items-start gap-2">
              <IconAlertTriangle size={14} className="shrink-0 mt-0.5" />
              <span>
                One or more values are <em>higher</em> than the default. Defaults are the
                build-time recommendation; raising them increases the risk of runaway
                runs. You can do it — just so it's not a surprise.
              </span>
            </div>
          ) : null}
        </div>

        <footer className="px-5 py-3 border-t border-line flex items-center justify-between gap-2">
          <button
            type="button"
            onClick={() => void clearAll()}
            disabled={saving}
            className="btn-ghost text-xs inline-flex items-center gap-1.5"
          >
            <IconRefresh size={12} /> Reset to defaults
          </button>
          <div className="flex items-center gap-2">
            <button type="button" onClick={onClose} className="btn-ghost text-xs" disabled={saving}>
              Cancel
            </button>
            <button
              type="button"
              onClick={() => void save()}
              disabled={saving}
              className="btn-primary text-xs"
            >
              {saving ? 'Saving…' : 'Save'}
            </button>
          </div>
        </footer>
      </div>
    </div>
  )
}

function LimitRow({
  label, hint, value, onChange, defaultValue, effectiveValue, placeholder, unit,
}: {
  label: string
  hint: string
  value: string
  onChange: (v: string) => void
  defaultValue: number | undefined
  effectiveValue: number | undefined
  placeholder: string
  unit: string
}) {
  return (
    <div className="flex items-start justify-between gap-3 py-2 border-t border-line first:border-t-0">
      <div className="min-w-0 flex-1">
        <div className="text-sm text-ink">{label}</div>
        <div className="text-[11px] text-ink-faint mt-0.5">{hint}</div>
        {defaultValue !== undefined && (
          <div className="text-[11px] text-ink-faint mt-0.5">
            Default: <span className="font-mono">{defaultValue}</span>
            {effectiveValue !== undefined && effectiveValue !== defaultValue && (
              <>
                {' · '}
                effective: <span className="font-mono text-ink">{effectiveValue}</span>
              </>
            )}
          </div>
        )}
      </div>
      <div className="flex items-center gap-1.5 shrink-0">
        <input
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          inputMode="numeric"
          className="input-soft text-sm w-28 font-mono"
        />
        {unit && <span className="text-xs text-ink-faint">{unit}</span>}
      </div>
    </div>
  )
}

function hasLoosening(value: string, defaultValue: number | undefined): boolean {
  if (!value.trim() || defaultValue === undefined) return false
  const n = Number(value)
  return Number.isFinite(n) && n > defaultValue
}
