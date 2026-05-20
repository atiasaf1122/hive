/**
 * Settings → Security.
 *
 * Five-mode picker, BLIND_AUTO "I accept responsibility" gate, CUSTOM_AUTO
 * rule editor, audit-log opener. Settings → Security tab in Settings.tsx.
 */
import {
  IconAlertTriangle,
  IconClipboardCheck,
  IconLock,
  IconPlus,
  IconTrash,
} from '@tabler/icons-react'
import clsx from 'clsx'
import { useEffect, useState } from 'react'
import { api } from '../../lib/api'
import { useSettings } from '../../stores/settings'
import { AuditLogViewer } from '../AuditLogViewer'
import { SettingCard, SettingRow } from './SettingsLayout'

type Mode = 'manual' | 'smart_auto' | 'full_auto' | 'blind_auto' | 'custom_auto'

const MODES: { id: Mode; label: string; subtitle: string }[] = [
  { id: 'manual',      label: 'Manual',      subtitle: 'Every command requires my OK.' },
  { id: 'smart_auto',  label: 'Smart auto',  subtitle: 'Safe reads run; everything else asks. (recommended)' },
  { id: 'full_auto',   label: 'Full auto',   subtitle: 'Package installs and network requests run without asking.' },
  { id: 'blind_auto',  label: 'Blind auto',  subtitle: 'Run everything except the hard-blocked list. Power users only.' },
  { id: 'custom_auto', label: 'Custom auto', subtitle: 'Smart auto + my own per-command overrides.' },
]

interface CustomRule {
  pattern: string
  action: 'ALLOW' | 'CONFIRM' | 'BLOCK'
}

export function SecurityPanel() {
  const settings = useSettings()
  const [showBlindWarning, setShowBlindWarning] = useState(false)
  const [auditOpen, setAuditOpen] = useState(false)
  const [rules, setRules] = useState<CustomRule[]>([])
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)

  useEffect(() => {
    api
      .get<{ custom_rules: CustomRule[] }>('/api/security/policies')
      .then((r) => setRules(r.custom_rules ?? []))
      .catch(() => setRules([]))
  }, [])

  async function persistRules(next: CustomRule[]) {
    setRules(next)
    setSaving(true)
    setSaveError(null)
    try {
      await api.put('/api/security/policies', { custom_rules: next })
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  function pickMode(next: Mode) {
    if (next === 'blind_auto' && settings.commandApprovalMode !== 'blind_auto') {
      // Show responsibility modal before letting them flip.
      setShowBlindWarning(true)
      return
    }
    settings.update({ commandApprovalMode: next })
  }

  return (
    <>
      <SettingCard
        title="Command sandbox"
        description="HIVE classifies every shell command an agent wants to run. Pick how much you want it to ask first."
      >
        <div className="space-y-1.5 mt-1">
          {MODES.map((m) => {
            const active = settings.commandApprovalMode === m.id
            return (
              <button
                key={m.id}
                type="button"
                onClick={() => pickMode(m.id)}
                className={clsx(
                  'w-full text-left rounded-soft border transition-colors px-3 py-2.5',
                  active
                    ? 'border-accent bg-surface-2'
                    : 'border-line hover:border-ink-faint hover:bg-surface-2/40',
                )}
              >
                <div className="flex items-center justify-between">
                  <span className="text-sm text-ink">{m.label}</span>
                  {active && (
                    <span className="text-[10px] text-accent uppercase tracking-wider">
                      active
                    </span>
                  )}
                </div>
                <div className="text-[11px] text-ink-faint mt-0.5">{m.subtitle}</div>
              </button>
            )
          })}
        </div>
        <div className="mt-3 text-[11px] text-ink-faint">
          The hard-blocked list (rm -rf /, sudo, credential reads, …) applies in every
          mode, including blind auto.
        </div>
      </SettingCard>

      {settings.commandApprovalMode === 'custom_auto' && (
        <SettingCard
          title="Custom rules"
          description="Patterns are regular expressions, case-insensitive, applied to the whitespace-normalised command. Custom rules are evaluated before the built-in lists."
        >
          {rules.length === 0 ? (
            <div className="text-[11px] text-ink-faint italic py-2">
              No custom rules yet. Add one below.
            </div>
          ) : (
            <ul className="space-y-1.5 mt-1">
              {rules.map((rule, i) => (
                <li key={i} className="flex items-center gap-2">
                  <input
                    value={rule.pattern}
                    onChange={(e) => {
                      const next = rules.slice()
                      next[i] = { ...rule, pattern: e.target.value }
                      setRules(next)
                    }}
                    onBlur={() => void persistRules(rules)}
                    placeholder="^npm install\b"
                    className="input-soft text-xs flex-1 font-mono"
                  />
                  <select
                    value={rule.action}
                    onChange={(e) => {
                      const next = rules.slice()
                      next[i] = { ...rule, action: e.target.value as CustomRule['action'] }
                      void persistRules(next)
                    }}
                    className="input-soft text-xs w-24"
                  >
                    <option value="ALLOW">Allow</option>
                    <option value="CONFIRM">Confirm</option>
                    <option value="BLOCK">Block</option>
                  </select>
                  <button
                    type="button"
                    onClick={() => void persistRules(rules.filter((_, j) => j !== i))}
                    className="text-ink-faint hover:text-red-500 p-1"
                    title="Remove rule"
                  >
                    <IconTrash size={14} />
                  </button>
                </li>
              ))}
            </ul>
          )}

          <button
            type="button"
            onClick={() =>
              void persistRules([
                ...rules,
                { pattern: '', action: 'CONFIRM' },
              ])
            }
            className="btn-ghost text-xs mt-3 inline-flex items-center gap-1.5"
          >
            <IconPlus size={13} /> Add rule
          </button>

          {saving && (
            <div className="mt-2 text-[11px] text-ink-faint">Saving…</div>
          )}
          {saveError && (
            <div className="mt-2 text-[11px] text-red-500">{saveError}</div>
          )}
        </SettingCard>
      )}

      <SettingCard
        title="Audit log"
        description="A row is recorded for every command — including the ones the policy refused to run."
      >
        <SettingRow
          label="Retention"
          hint="Older rows are purged in the background. 30 days is plenty for after-the-fact review."
        >
          <input
            type="number"
            min={1}
            max={365}
            value={settings.auditRetentionDays}
            onChange={(e) =>
              settings.update({
                auditRetentionDays: Math.max(1, Math.min(365, Number(e.target.value) || 30)),
              })
            }
            className="input-soft text-sm w-20"
          />
        </SettingRow>

        <SettingRow label="View the log">
          <button
            type="button"
            onClick={() => setAuditOpen(true)}
            className="btn-ghost text-xs inline-flex items-center gap-1.5"
          >
            <IconClipboardCheck size={13} /> Open audit viewer
          </button>
        </SettingRow>
      </SettingCard>

      {showBlindWarning && (
        <BlindAutoModal
          onConfirm={() => {
            settings.update({ commandApprovalMode: 'blind_auto' })
            setShowBlindWarning(false)
          }}
          onCancel={() => setShowBlindWarning(false)}
        />
      )}

      <AuditLogViewer open={auditOpen} onClose={() => setAuditOpen(false)} />
    </>
  )
}

function BlindAutoModal({
  onConfirm, onCancel,
}: { onConfirm: () => void; onCancel: () => void }) {
  const [accepted, setAccepted] = useState(false)
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm">
      <div className="w-[520px] max-w-[92vw] card shadow-hover overflow-hidden">
        <header className="px-5 py-3 border-b border-line flex items-center gap-2">
          <IconAlertTriangle size={16} className="text-red-500" />
          <h2 className="text-sm text-ink font-medium">Blind auto — confirm</h2>
        </header>

        <div className="p-5 text-sm space-y-3">
          <p className="text-ink">
            Blind auto runs every command an agent decides to execute, including:
          </p>
          <ul className="text-xs text-ink-muted list-disc pl-5 space-y-1">
            <li>Installing arbitrary packages (npm / pip / cargo / brew / apt)</li>
            <li>Pushing to remotes, force-pushing, hard-resetting branches</li>
            <li>Hitting arbitrary URLs with curl/wget</li>
            <li>Running scripts the agent wrote itself</li>
            <li>Starting dev servers and Docker containers</li>
            <li>Modifying environment variables and shell rc files</li>
          </ul>
          <p className="text-xs text-ink-muted">
            The hard-block list (rm -rf /, sudo, credential reads, fork bombs, …)
            still applies — but everything else runs without asking.
          </p>

          <label className="mt-3 flex items-center gap-2 text-xs text-ink cursor-pointer select-none">
            <input
              type="checkbox"
              checked={accepted}
              onChange={(e) => setAccepted(e.target.checked)}
              className="accent-accent"
            />
            <span className="flex items-center gap-1">
              <IconLock size={12} />
              I accept responsibility for what HIVE runs in this mode.
            </span>
          </label>
        </div>

        <footer className="px-5 py-3 border-t border-line flex items-center justify-end gap-2">
          <button type="button" onClick={onCancel} className="btn-ghost text-xs">
            Cancel
          </button>
          <button
            type="button"
            disabled={!accepted}
            onClick={onConfirm}
            className="btn-primary text-xs disabled:opacity-50 disabled:cursor-not-allowed"
          >
            Enable blind auto
          </button>
        </footer>
      </div>
    </div>
  )
}
