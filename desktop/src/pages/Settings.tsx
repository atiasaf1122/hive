/**
 * Settings — six sub-pages behind a left sub-nav.
 *
 * Every panel is self-contained and writes through `useSettings()`. The
 * shell page is just routing + scaffolding.
 */
import {
  IconAlertTriangle,
  IconCheck,
  IconExternalLink,
  IconRefresh,
  IconShieldLock,
} from '@tabler/icons-react'
import { useEffect, useMemo, useState } from 'react'
import { useOnboarding } from '../stores/onboarding'
import {
  ACCENT_PALETTES,
  applyAccent,
  useSettings,
  type AccentColor,
  type RoutingStrategy,
} from '../stores/settings'
import { useThemeStore, type ThemeMode } from '../stores/theme'
import {
  SettingCard,
  SettingRow,
  SettingsLayout,
  type SettingsTab,
} from '../components/settings/SettingsLayout'
import { LessonsPanel } from '../components/settings/LessonsPanel'
import { SafetyPanel } from '../components/settings/SafetyPanel'

/** `costly` worker picks trigger a confirm() prompt — HIVE invariant #7
 *  reserves Opus for Orchestrator + Reviewer, but power users may still
 *  want Opus on a hard one-off task. We don't hide the option, only warn. */
const MODEL_CHOICES = [
  { value: 'claude:opus',    label: 'Claude Opus',          tier: 'premium',  costlyAsWorker: true  },
  { value: 'claude:sonnet',  label: 'Claude Sonnet',        tier: 'standard', costlyAsWorker: false },
  { value: 'claude:haiku',   label: 'Claude Haiku',         tier: 'cheap',    costlyAsWorker: false },
  { value: 'ollama:llama3.1', label: 'Ollama · Llama 3.1',  tier: 'local',    costlyAsWorker: false },
  { value: 'ollama:qwen2.5', label: 'Ollama · Qwen 2.5',    tier: 'local',    costlyAsWorker: false },
] as const

const ROUTING_OPTIONS: { value: RoutingStrategy; label: string; hint: string }[] = [
  { value: 'cloud-first', label: 'Cloud first',  hint: 'Prefer Claude for everything. Ollama as fallback.' },
  { value: 'balanced',    label: 'Balanced',     hint: 'Cheap/local for simple work, Claude for hard parts.' },
  { value: 'local-first', label: 'Local first',  hint: 'Try Ollama first; fall back to Claude when needed.' },
  { value: 'local-only',  label: 'Local only',   hint: 'Never call Claude. Warn if a task seems beyond local.' },
]

export function Settings() {
  const [tab, setTab] = useState<SettingsTab>('general')

  return (
    <SettingsLayout active={tab} onChange={setTab}>
      {tab === 'general' && <GeneralPanel />}
      {tab === 'appearance' && <AppearancePanel />}
      {tab === 'ai' && <AIPanel />}
      {tab === 'routing' && <RoutingPanel />}
      {tab === 'safety' && <SafetyPanel />}
      {tab === 'lessons' && <LessonsPanel />}
      {tab === 'integrations' && <IntegrationsPanel />}
      {tab === 'advanced' && <AdvancedPanel />}
    </SettingsLayout>
  )
}

/* ── General ─────────────────────────────────────────────────────────────── */

function GeneralPanel() {
  const s = useSettings()
  const reopen = useOnboarding((s) => s.reopen)

  return (
    <>
      <SettingCard
        title="Account"
        description="Used for the dashboard greeting and Save-as-template defaults."
      >
        <SettingRow label="Display name">
          <input
            value={s.displayName}
            onChange={(e) => s.update({ displayName: e.target.value })}
            placeholder="Your name"
            className="input-soft text-sm w-56"
          />
        </SettingRow>
        <SettingRow label="Default projects directory" hint="Used by QuickStart for new sessions.">
          <input
            value={s.projectsDir}
            onChange={(e) => s.update({ projectsDir: e.target.value })}
            className="input-soft text-sm w-72 font-mono"
          />
        </SettingRow>
      </SettingCard>

      <SettingCard
        title="Help & tour"
        description="The onboarding tour is the same one you saw on first launch."
      >
        <SettingRow label="Re-run onboarding">
          <button
            type="button"
            onClick={reopen}
            className="btn-ghost text-xs inline-flex items-center gap-1.5"
          >
            <IconRefresh size={14} strokeWidth={1.75} /> Open tour
          </button>
        </SettingRow>
      </SettingCard>
    </>
  )
}

/* ── Appearance ──────────────────────────────────────────────────────────── */

function AppearancePanel() {
  const settings = useSettings()
  const mode = useThemeStore((s) => s.mode)
  const setMode = useThemeStore((s) => s.setMode)

  // Re-apply accent on mount so a stored choice survives reload
  useEffect(() => {
    applyAccent(settings.accent)
  }, [settings.accent])

  return (
    <>
      <SettingCard title="Theme" description="Switch between light, dark, or follow the OS.">
        <div className="grid grid-cols-3 gap-2">
          {(['system', 'light', 'dark'] as ThemeMode[]).map((m) => (
            <button
              key={m}
              type="button"
              onClick={() => setMode(m)}
              className={`p-3 rounded-soft border text-sm capitalize transition-colors ${
                mode === m
                  ? 'border-accent text-ink bg-surface-2'
                  : 'border-line text-ink-muted hover:border-ink-faint'
              }`}
            >
              {m}
            </button>
          ))}
        </div>
      </SettingCard>

      <SettingCard
        title="Accent colour"
        description="Tints the orchestrator pill, primary buttons, and active nav."
      >
        <div className="flex flex-wrap gap-2">
          {(Object.keys(ACCENT_PALETTES) as AccentColor[]).map((acc) => {
            const palette = ACCENT_PALETTES[acc]
            const active = settings.accent === acc
            return (
              <button
                key={acc}
                type="button"
                onClick={() => {
                  settings.update({ accent: acc })
                  applyAccent(acc)
                }}
                className={`group flex items-center gap-2 pl-1.5 pr-3 py-1.5 rounded-full border ${
                  active ? 'border-ink' : 'border-line hover:border-ink-faint'
                }`}
                title={palette.label}
              >
                <span
                  className="w-5 h-5 rounded-full"
                  style={{
                    background: `linear-gradient(135deg, rgb(${palette.light}), rgb(${palette.warm}))`,
                  }}
                />
                <span className="text-xs text-ink-muted group-hover:text-ink">
                  {palette.label}
                </span>
                {active && <IconCheck size={12} strokeWidth={2} className="text-ink" />}
              </button>
            )
          })}
        </div>
      </SettingCard>
    </>
  )
}

/* ── AI ──────────────────────────────────────────────────────────────────── */

function AIPanel() {
  const s = useSettings()
  const orchIsLocal = s.orchestratorModel.startsWith('ollama:')

  return (
    <>
      <SettingCard
        title="Claude (Anthropic)"
        description="Authenticated via Claude Code OAuth. No tokens are stored in HIVE."
      >
        <SettingRow
          label="Status"
          hint="Re-authenticate from a terminal with `claude setup-token`."
        >
          <span className="inline-flex items-center gap-1.5 text-xs text-emerald-500">
            <IconCheck size={14} strokeWidth={2} /> Connected
          </span>
        </SettingRow>

        <SettingRow
          label="Default Orchestrator model"
          hint="Opus is recommended; the Orchestrator's job is planning, not bulk work."
        >
          <ModelSelect
            value={s.orchestratorModel}
            onChange={(v) => s.update({ orchestratorModel: v })}
          />
        </SettingRow>

        {orchIsLocal && (
          <div className="mt-2 p-3 rounded-soft bg-amber-500/10 border border-amber-500/30 text-xs text-amber-700 dark:text-amber-300 flex items-start gap-2">
            <IconAlertTriangle size={14} strokeWidth={1.75} className="shrink-0 mt-0.5" />
            <div>
              Local models are less capable than Opus for planning. This may affect
              the quality of team selection and agent coordination. You can change
              this anytime — your choice stands.
            </div>
          </div>
        )}

        <SettingRow label="Default worker model">
          <ModelSelect
            value={s.workerModel}
            onChange={(v) => s.update({ workerModel: v })}
            kind="worker"
          />
        </SettingRow>

        <SettingRow label="Default approval mode">
          <select
            value={s.approvalMode}
            onChange={(e) => s.update({ approvalMode: e.target.value as typeof s.approvalMode })}
            className="input-soft text-sm"
          >
            <option value="full-auto">Full auto</option>
            <option value="checkpoint">Checkpoint</option>
            <option value="manual">Manual</option>
          </select>
        </SettingRow>
      </SettingCard>

      <SettingCard
        title="Ollama"
        description="Local LLM endpoint. When reachable, HIVE will use it for cheap tasks per your routing rules."
      >
        <SettingRow label="Endpoint">
          <input
            value={s.ollamaEndpoint}
            onChange={(e) => s.update({ ollamaEndpoint: e.target.value })}
            className="input-soft text-sm w-64 font-mono"
          />
        </SettingRow>
      </SettingCard>
    </>
  )
}

function ModelSelect({
  value,
  onChange,
  kind = 'orchestrator',
}: {
  value: string
  onChange: (v: string) => void
  /** 'worker' adds a (costly) badge to Opus and confirms the pick. */
  kind?: 'orchestrator' | 'worker'
}) {
  function handleChange(next: string) {
    if (kind === 'worker') {
      const choice = MODEL_CHOICES.find((m) => m.value === next)
      if (choice?.costlyAsWorker) {
        const ok = window.confirm(
          `${choice.label} is intended for the Orchestrator + Reviewer only — running it as a worker is significantly more expensive. Use it anyway?`,
        )
        if (!ok) return
      }
    }
    onChange(next)
  }

  return (
    <select
      value={value}
      onChange={(e) => handleChange(e.target.value)}
      className="input-soft text-sm"
    >
      {MODEL_CHOICES.map((m) => (
        <option key={m.value} value={m.value}>
          {m.label} ({m.tier}{kind === 'worker' && m.costlyAsWorker ? ' · costly' : ''})
        </option>
      ))}
    </select>
  )
}

/* ── Routing ─────────────────────────────────────────────────────────────── */

function RoutingPanel() {
  const s = useSettings()
  return (
    <>
      <SettingCard
        title="Routing strategy"
        description="How HIVE decides which backend handles each agent — defaults can be overridden per project."
      >
        <div className="space-y-2 mt-1">
          {ROUTING_OPTIONS.map((opt) => {
            const active = s.routing === opt.value
            return (
              <button
                key={opt.value}
                type="button"
                onClick={() => s.update({ routing: opt.value })}
                className={`w-full text-left p-3 rounded-soft border transition-colors ${
                  active
                    ? 'border-accent bg-surface-2'
                    : 'border-line hover:border-ink-faint'
                }`}
              >
                <div className="flex items-center gap-2">
                  <span className="text-sm text-ink">{opt.label}</span>
                  {active && <IconCheck size={12} strokeWidth={2} className="text-accent" />}
                </div>
                <div className="text-[11px] text-ink-faint mt-0.5">{opt.hint}</div>
              </button>
            )
          })}
        </div>
      </SettingCard>

      <SettingCard
        title="Limits"
        description="Caps applied to every project."
      >
        <SettingRow label="Max parallel agents" hint="3–7; defaults match RAM/rate-limit headroom.">
          <input
            type="number"
            min={1}
            max={7}
            value={s.maxParallelAgents}
            onChange={(e) => s.update({ maxParallelAgents: Number(e.target.value) || 3 })}
            className="input-soft text-sm w-20"
          />
        </SettingRow>
      </SettingCard>
    </>
  )
}

/* ── Integrations ────────────────────────────────────────────────────────── */

function IntegrationsPanel() {
  const s = useSettings()
  return (
    <>
      <SettingCard
        title="Background behaviour"
        description="When you close the window, what should keep running?"
      >
        <SettingRow
          label="Run automations in background when app is closed"
          hint="If on: scheduled pipelines + the Telegram bot stay alive in a system-tray icon. Interactive project sessions always stop on close. Off: everything exits when the window closes."
        >
          <Toggle
            checked={s.backgroundAutomations}
            onChange={(v) => s.update({ backgroundAutomations: v })}
          />
        </SettingRow>
        <div className="text-[11px] text-ink-faint pt-2">
          Tray icon + close-confirmation dialog ship in Phase 9D. The preference
          is honoured then.
        </div>
      </SettingCard>

      <SettingCard
        title="Telegram bot"
        description="Set up from a terminal with `hive telegram setup --token <bot>` then `hive telegram allow <chat-id>`."
      >
        <SettingRow label="Bot status" hint="Configured via the CLI; this page is read-only for now.">
          <span className="text-xs text-ink-muted">Open a WSL terminal to configure.</span>
        </SettingRow>
      </SettingCard>

      <SettingCard
        title="Usage notifications"
        description="Banners and (later) OS notifications — never blocks your work."
      >
        <SettingRow
          label="Claude burn-rate alert"
          hint="Notify when the last hour exceeds the 7-day average by this multiple."
        >
          <div className="flex items-center gap-2">
            <input
              type="number"
              step="0.1"
              min="1"
              value={s.notifyAtClaudeBurn}
              onChange={(e) => s.update({ notifyAtClaudeBurn: Number(e.target.value) || 2 })}
              className="input-soft text-sm w-20"
            />
            <span className="text-xs text-ink-faint">×</span>
          </div>
        </SettingRow>

        <SettingRow label="External API monthly cap" hint="USD spent on direct API calls (not Max).">
          <div className="flex items-center gap-2">
            <span className="text-xs text-ink-faint">$</span>
            <input
              type="number"
              min="0"
              value={s.notifyAtExternalMonthly}
              onChange={(e) => s.update({ notifyAtExternalMonthly: Number(e.target.value) || 20 })}
              className="input-soft text-sm w-20"
            />
          </div>
        </SettingRow>
      </SettingCard>
    </>
  )
}

/* ── Advanced ────────────────────────────────────────────────────────────── */

function AdvancedPanel() {
  const reset = useSettings((s) => s.reset)

  const dataFlow = useMemo(
    () => [
      { label: 'Conversation history', target: 'Local SQLite', detail: '~/.hive/hive.db' },
      { label: 'Agent prompts', target: 'Claude / Ollama', detail: 'one of the configured backends per agent' },
      { label: 'Worktree files', target: 'Local git', detail: '~/.hive/worktrees/<session>' },
      { label: 'Skills (downloaded)', target: 'Local files', detail: '~/.hive/skills/<name>/SKILL.md' },
    ],
    [],
  )

  return (
    <>
      <SettingCard
        title="Data flow"
        description="Where each kind of HIVE data goes. We don't pretend everything stays local — Claude is a cloud API."
      >
        <table className="w-full text-sm">
          <tbody>
            {dataFlow.map((row) => (
              <tr key={row.label} className="border-t border-line first:border-t-0">
                <td className="py-2 text-ink-muted">{row.label}</td>
                <td className="py-2 text-ink">{row.target}</td>
                <td className="py-2 text-[11px] text-ink-faint">{row.detail}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </SettingCard>

      <SettingCard
        title="Storage"
        description="HIVE keeps everything under ~/.hive/. Backend logs are streamed to the WSL terminal."
      >
        <SettingRow label="Database">
          <code className="text-xs text-ink font-mono">~/.hive/hive.db</code>
        </SettingRow>
        <SettingRow label="Worktrees">
          <code className="text-xs text-ink font-mono">~/.hive/worktrees/</code>
        </SettingRow>
      </SettingCard>

      <SettingCard
        title="Reset"
        description="Wipes your in-app preferences (theme, accent, model defaults). Doesn't touch HIVE data."
      >
        <SettingRow label="Reset preferences">
          <button
            type="button"
            onClick={() => {
              if (confirm('Reset all preferences to defaults? Project data is not affected.')) {
                reset()
              }
            }}
            className="btn-ghost text-xs inline-flex items-center gap-1.5"
          >
            <IconShieldLock size={14} strokeWidth={1.75} /> Reset
          </button>
        </SettingRow>
      </SettingCard>

      <div className="text-[11px] text-ink-faint pt-2">
        Looking for raw logs?{' '}
        <a
          href="https://v2.tauri.app"
          target="_blank"
          rel="noreferrer"
          className="inline-flex items-center gap-1 underline hover:text-ink-muted"
        >
          Open the WSL terminal where you ran <code>hive start</code>
          <IconExternalLink size={11} />
        </a>
      </div>
    </>
  )
}

function Toggle({ checked, onChange }: { checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      onClick={() => onChange(!checked)}
      className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors ${
        checked ? 'bg-accent' : 'bg-surface-2 border border-line'
      }`}
    >
      <span
        className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
          checked ? 'translate-x-4' : 'translate-x-0.5'
        }`}
      />
    </button>
  )
}
