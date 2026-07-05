/**
 * User-visible settings — persisted to localStorage and applied on the fly.
 *
 * The Tauri shell is single-user, so localStorage is sufficient. Future
 * work may move this into the backend SQLite for sync.
 */
import { create } from 'zustand'

const STORAGE_KEY = 'hive.settings.v1'

export type AccentColor = 'orange' | 'amber' | 'rose' | 'violet' | 'emerald'
export type RoutingStrategy =
  | 'cloud-first'
  | 'balanced'
  | 'local-first'
  | 'local-only'

export interface AppSettings {
  /** Display name on the dashboard greeting (optional). */
  displayName: string
  /** Accent palette — pure cosmetic, written into a CSS variable on apply. */
  accent: AccentColor
  /** Default model for the Orchestrator. May be Ollama (user-chosen, with warning). */
  orchestratorModel: string
  /** Default model for sub-agent workers. */
  workerModel: string
  /** Approval mode applied to new sessions unless overridden in QuickStart. */
  approvalMode: 'full-auto' | 'checkpoint' | 'manual'
  /** Default workspace root for new projects. */
  projectsDir: string
  /** MRU list of workspaces the user has actually picked (newest first, up to 10). */
  recentWorkspaces: string[]
  /** Routing strategy hint passed to the planner. */
  routing: RoutingStrategy
  /** Maximum parallel agents (the backend enforces its own ceiling too). */
  maxParallelAgents: number
  /** Ollama HTTP endpoint. */
  ollamaEndpoint: string
  /** Notify on rate-limit / quota thresholds — thresholds only, never blocks. */
  notifyAtClaudeBurn: number  // ratio (1.0 = at-average, 2.0 = twice average)
  notifyAtExternalMonthly: number  // dollars
  /**
   * Keep enabled automations + Telegram bot running after the window is closed.
   * On true: backend stays alive in the system tray; closing the window only
   * stops interactive sessions. On false: everything exits on close.
   *
   * 9C stores the preference + exposes /api/lifecycle/active-counts. The
   * actual tray icon, close-confirmation dialog, and graceful shutdown
   * (interactive agents stop, scheduler keeps running) land in Phase 9D.
   */
  backgroundAutomations: boolean
}

const DEFAULTS: AppSettings = {
  displayName: '',
  accent: 'orange',
  orchestratorModel: 'claude:opus',
  workerModel: 'claude:sonnet',
  approvalMode: 'full-auto',
  projectsDir: '~/projects',
  recentWorkspaces: [],
  routing: 'cloud-first',
  maxParallelAgents: 3,
  ollamaEndpoint: 'http://localhost:11434',
  notifyAtClaudeBurn: 2.0,
  notifyAtExternalMonthly: 20,
  backgroundAutomations: true,
}

function load(): AppSettings {
  if (typeof localStorage === 'undefined') return DEFAULTS
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return DEFAULTS
    return { ...DEFAULTS, ...(JSON.parse(raw) as Partial<AppSettings>) }
  } catch {
    return DEFAULTS
  }
}

function persist(s: AppSettings) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(s))
  } catch {
    /* private mode, quota — silent */
  }
}

interface Store extends AppSettings {
  update: (patch: Partial<AppSettings>) => void
  reset: () => void
}

export const useSettings = create<Store>((set) => {
  const initial = load()
  return {
    ...initial,
    update: (patch) =>
      set((prev) => {
        const next = { ...prev, ...patch }
        persist(next)
        return next
      }),
    reset: () => {
      persist(DEFAULTS)
      set(DEFAULTS)
    },
  }
})

/** Accent palettes — each maps to a `--c-accent` pair the rest of the UI reads. */
export const ACCENT_PALETTES: Record<AccentColor, { light: string; warm: string; label: string }> = {
  orange:  { light: '245 166 35',  warm: '216 90 48',   label: 'Warm orange' },
  amber:   { light: '252 191 71',  warm: '224 134 28',  label: 'Amber' },
  rose:    { light: '244 114 152', warm: '210 70 110',  label: 'Rose' },
  violet:  { light: '167 139 250', warm: '124 99 233',  label: 'Violet' },
  emerald: { light: '52 211 153',  warm: '16 167 119',  label: 'Emerald' },
}

/** Apply the accent palette by writing CSS variables on <html>. */
export function applyAccent(accent: AccentColor) {
  if (typeof document === 'undefined') return
  const palette = ACCENT_PALETTES[accent]
  if (!palette) return
  document.documentElement.style.setProperty('--c-accent', palette.light)
  document.documentElement.style.setProperty('--c-accent-warm', palette.warm)
}
