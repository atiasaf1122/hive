/**
 * First-launch onboarding wizard.
 *
 *   1. Welcome — what HIVE is, what's about to happen
 *   2. Claude — confirm OAuth (backend already authenticated → just acknowledge)
 *   3. Ollama — auto-detect via /api/sessions side-channel (or skip)
 *   4. Projects dir — pick a default folder for new sessions
 *   5. Telegram — optional, points at the CLI
 *   6. Quick tour — 4 callouts that close into the dashboard
 *
 * Skippable in two places:
 *   - "Skip for now" button on every step
 *   - "Skip all" in the top right of step 1
 *
 * Re-openable from Settings → General.
 */
import {
  IconArrowLeft,
  IconArrowRight,
  IconBolt,
  IconBrandTelegram,
  IconCheck,
  IconCpu,
  IconFolder,
  IconHexagon,
  IconRobot,
} from '@tabler/icons-react'
import clsx from 'clsx'
import { useEffect, useState } from 'react'
import { api } from '../../lib/api'
import { useOnboarding } from '../../stores/onboarding'
import { useSettings } from '../../stores/settings'
import { HiveLogo } from '../HiveLogo'

type Step = 0 | 1 | 2 | 3 | 4 | 5

const STEP_TITLES = [
  'Welcome',
  'Connect Claude',
  'Look for Ollama',
  'Pick a workspace',
  'Optional: Telegram',
  'Quick tour',
]

export function OnboardingWizard() {
  const finish = useOnboarding((s) => s.finish)
  const [step, setStep] = useState<Step>(0)

  function next() {
    // Branch off the FRESH value inside the updater. The previous form read
    // `step` from the outer closure, which was stale right after setStep —
    // it happened to work on the last step (5 === 5) but would have walked
    // off the STEP_TITLES end if the close gate ever became async.
    setStep((s) => {
      if (s >= 5) {
        finish()
        return s
      }
      return (s + 1) as Step
    })
  }
  function back() {
    setStep((s) => Math.max(0, (s - 1) as Step) as Step)
  }

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/30 backdrop-blur-sm">
      <div className="w-[680px] max-w-[94vw] h-[560px] max-h-[92vh] card shadow-hover overflow-hidden flex flex-col">
        <header className="px-6 py-3 border-b border-line flex items-center justify-between">
          <div className="flex items-center gap-2">
            <HiveLogo size={20} />
            <span className="text-xs text-ink-muted">
              Setup · step {step + 1} of {STEP_TITLES.length}
            </span>
          </div>
          <button
            type="button"
            onClick={finish}
            className="text-xs text-ink-faint hover:text-ink-muted underline"
          >
            Skip all
          </button>
        </header>

        <div className="flex-1 overflow-y-auto px-8 py-6">
          {step === 0 && <StepWelcome onNext={next} />}
          {step === 1 && <StepClaude />}
          {step === 2 && <StepOllama />}
          {step === 3 && <StepWorkspace />}
          {step === 4 && <StepTelegram />}
          {step === 5 && <StepTour />}
        </div>

        <footer className="px-6 py-3 border-t border-line flex items-center justify-between">
          <Dots count={STEP_TITLES.length} active={step} />
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={back}
              disabled={step === 0}
              className="btn-ghost text-xs inline-flex items-center gap-1 disabled:opacity-40"
            >
              <IconArrowLeft size={14} strokeWidth={1.75} /> Back
            </button>
            <button
              type="button"
              onClick={next}
              className="btn-primary text-xs inline-flex items-center gap-1"
            >
              {step === 5 ? 'Finish' : 'Continue'}
              <IconArrowRight size={14} strokeWidth={1.75} />
            </button>
          </div>
        </footer>
      </div>
    </div>
  )
}

function Dots({ count, active }: { count: number; active: number }) {
  return (
    <div className="flex items-center gap-1.5">
      {Array.from({ length: count }).map((_, i) => (
        <span
          key={i}
          className={clsx(
            'w-1.5 h-1.5 rounded-full transition-colors',
            i === active ? 'bg-accent' : 'bg-line',
          )}
        />
      ))}
    </div>
  )
}

/* ── Step bodies ─────────────────────────────────────────────────────────── */

function StepWelcome(_: { onNext: () => void }) {
  return (
    <div className="h-full flex flex-col items-center justify-center text-center">
      <div className="mb-6">
        <HiveLogo size={64} />
      </div>
      <h2 className="text-2xl font-medium text-ink mb-2">Welcome to HIVE</h2>
      <p className="text-ink-muted text-sm max-w-md leading-relaxed">
        HIVE turns one task into a team of AI agents that work in parallel and
        report back. The orchestrator stays on the line — you can chat with it
        at any time, like a colleague at the next desk.
      </p>
      <div className="mt-6 text-[11px] text-ink-faint">
        We'll set up four small things. Should take less than a minute.
      </div>
    </div>
  )
}

function StepClaude() {
  const [healthy, setHealthy] = useState<boolean | null>(null)
  useEffect(() => {
    api
      .get<{ status: string }>('/health')
      .then(() => setHealthy(true))
      .catch(() => setHealthy(false))
  }, [])

  return (
    <div className="space-y-4">
      <Heading icon={IconRobot} title="Connect Claude" />
      <p className="text-ink-muted text-sm">
        HIVE uses your Claude subscription via the local <code>claude</code> CLI.
        Authentication happens once with <code>claude setup-token</code> in a
        terminal — nothing here in the app stores your token.
      </p>

      <div className="card p-4 flex items-center gap-3">
        <div
          className={clsx(
            'w-9 h-9 rounded-full flex items-center justify-center',
            healthy === true && 'bg-emerald-500/15 text-emerald-500',
            healthy === false && 'bg-red-500/15 text-red-500',
            healthy === null && 'bg-surface-2 text-ink-muted',
          )}
        >
          {healthy === true ? <IconCheck size={18} /> : <IconBolt size={18} />}
        </div>
        <div className="flex-1 min-w-0">
          <div className="text-sm text-ink">
            {healthy === true
              ? 'Backend reachable on port 8765 — Claude is wired up.'
              : healthy === false
              ? "Can't reach the backend yet."
              : 'Checking…'}
          </div>
          <div className="text-[11px] text-ink-faint">
            If this is your first run, start the backend with <code>hive start</code>{' '}
            inside WSL.
          </div>
        </div>
      </div>
    </div>
  )
}

interface OllamaProbe {
  ollama_reachable: boolean
  models: string[]
}

function StepOllama() {
  const settings = useSettings()
  const [probe, setProbe] = useState<OllamaProbe | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [testing, setTesting] = useState(false)

  async function runProbe() {
    setTesting(true)
    setError(null)
    try {
      const res = await api.get<OllamaProbe>('/api/detect/ollama')
      setProbe(res)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'detection unavailable')
      setProbe({ ollama_reachable: false, models: [] })
    } finally {
      setTesting(false)
    }
  }

  useEffect(() => {
    void runProbe()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return (
    <div className="space-y-4">
      <Heading icon={IconCpu} title="Look for Ollama (optional)" />
      <p className="text-ink-muted text-sm">
        If you run a local Ollama server, HIVE can route simpler tasks to it for
        zero cost. We just need its address.
      </p>

      <SettingsCardRow label="Ollama endpoint">
        <div className="flex items-center gap-2">
          <input
            value={settings.ollamaEndpoint}
            onChange={(e) => settings.update({ ollamaEndpoint: e.target.value })}
            onBlur={() => void runProbe()}
            className="input-soft text-sm w-60 font-mono"
          />
          <button
            type="button"
            onClick={() => void runProbe()}
            disabled={testing}
            className="btn-ghost text-xs"
          >
            {testing ? 'Testing…' : 'Test connection'}
          </button>
        </div>
      </SettingsCardRow>

      <div className="card p-4">
        <div className="text-xs text-ink-muted mb-1">Detection</div>
        {probe === null ? (
          <div className="text-sm text-ink-faint">Probing…</div>
        ) : probe.ollama_reachable ? (
          <>
            <div className="text-sm text-emerald-500 flex items-center gap-1.5">
              <IconCheck size={14} /> Ollama is reachable
            </div>
            <div className="mt-2 text-xs text-ink-muted">
              Detected models:{' '}
              {probe.models.length ? probe.models.join(', ') : 'none pulled yet'}
            </div>
          </>
        ) : (
          <div className="text-sm text-ink-muted">
            Not reachable — that's fine. HIVE works without it.
            {error && <div className="text-[10px] text-ink-faint mt-1">({error})</div>}
            <div className="text-[11px] text-ink-faint mt-1">
              Tried {settings.ollamaEndpoint} (and the IPv4 form if applicable).
              If you just started Ollama, hit "Test connection".
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

function StepWorkspace() {
  const s = useSettings()
  return (
    <div className="space-y-4">
      <Heading icon={IconFolder} title="Pick a workspace" />
      <p className="text-ink-muted text-sm">
        New projects get a fresh sub-folder here. You can override on a
        per-project basis in QuickStart.
      </p>
      <SettingsCardRow label="Default projects directory">
        <input
          value={s.projectsDir}
          onChange={(e) => s.update({ projectsDir: e.target.value })}
          placeholder="~/projects"
          className="input-soft text-sm w-72 font-mono"
        />
      </SettingsCardRow>
      <div className="text-[11px] text-ink-faint">
        Tip: pick a folder you already back up — HIVE doesn't run its own
        backups (yet).
      </div>
    </div>
  )
}

function StepTelegram() {
  return (
    <div className="space-y-4">
      <Heading icon={IconBrandTelegram} title="Optional: Telegram bot" />
      <p className="text-ink-muted text-sm">
        Once configured, the orchestrator can ping you on your phone when
        approval is needed. You can skip this and set it up later.
      </p>
      <div className="card p-4">
        <ol className="text-sm text-ink-muted space-y-1.5 list-decimal pl-5">
          <li>Create a bot with @BotFather and copy the token.</li>
          <li>
            In a WSL terminal: <code>hive telegram setup --token &lt;token&gt;</code>
          </li>
          <li>
            Send <code>/start</code> to your bot, copy the chat ID it prints.
          </li>
          <li>
            <code>hive telegram allow &lt;chat-id&gt;</code>
          </li>
        </ol>
      </div>
      <div className="text-[11px] text-ink-faint">
        Empty allowlist blocks everything — a leaked token can't drive HIVE
        without your explicit allow.
      </div>
    </div>
  )
}

function StepTour() {
  return (
    <div className="h-full flex flex-col">
      <Heading icon={IconHexagon} title="Quick tour" />
      <div className="grid grid-cols-2 gap-3 mt-3 flex-1 content-start">
        <TourCard
          title="QuickStart"
          text="Type a task on the dashboard and hit Ctrl + Enter. The orchestrator either answers you or proposes a team."
        />
        <TourCard
          title="Tab bar"
          text="Each project opens its own tab. Ctrl + W closes, Ctrl + 1..9 switches. Tabs persist across restarts."
        />
        <TourCard
          title="Background automations"
          text="Close the window and your interactive sessions stop — but scheduled automations + the Telegram bot keep running headlessly in your taskbar tray (Settings → Integrations turns this off)."
        />
        <TourCard
          title="Slash & search"
          text="Type / in the composer for /cost, /model, /pause and more. Ctrl + K opens the command palette to jump anywhere."
        />
      </div>
      <div className="text-[11px] text-ink-faint mt-3">
        You can re-run this tour anytime from Settings → Account → Re-run onboarding.
      </div>
    </div>
  )
}

/* ── Small helpers ───────────────────────────────────────────────────────── */

function Heading({ icon: Icon, title }: { icon: typeof IconBolt; title: string }) {
  return (
    <div className="flex items-center gap-3">
      <div className="w-10 h-10 rounded-xl2 bg-accent-gradient text-white flex items-center justify-center">
        <Icon size={20} strokeWidth={1.5} />
      </div>
      <h2 className="text-lg text-ink">{title}</h2>
    </div>
  )
}

function SettingsCardRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="card p-3 flex items-center justify-between">
      <div className="text-sm text-ink-muted">{label}</div>
      {children}
    </div>
  )
}

function TourCard({ title, text }: { title: string; text: string }) {
  return (
    <div className="card p-3">
      <div className="text-sm text-ink mb-1">{title}</div>
      <div className="text-xs text-ink-muted leading-relaxed">{text}</div>
    </div>
  )
}
