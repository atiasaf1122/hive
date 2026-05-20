/**
 * Theme store — light / dark / system, persisted to localStorage.
 * `initTheme()` is called once at boot from main.tsx; it picks up the saved
 * mode and listens to OS-level changes if "system" is selected.
 */
import { create } from 'zustand'

export type ThemeMode = 'light' | 'dark' | 'system'
type Resolved = 'light' | 'dark'

const STORAGE_KEY = 'hive.theme'

function resolveSystemTheme(): Resolved {
  if (typeof window === 'undefined') return 'light'
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

function applyTheme(resolved: Resolved) {
  if (typeof document === 'undefined') return
  const root = document.documentElement
  root.classList.toggle('dark', resolved === 'dark')
}

interface ThemeStore {
  mode: ThemeMode
  resolved: Resolved
  setMode: (mode: ThemeMode) => void
  toggle: () => void
}

export const useThemeStore = create<ThemeStore>((set, get) => ({
  mode: 'system',
  resolved: 'light',
  setMode: (mode) => {
    const resolved = mode === 'system' ? resolveSystemTheme() : mode
    applyTheme(resolved)
    localStorage.setItem(STORAGE_KEY, mode)
    set({ mode, resolved })
  },
  toggle: () => {
    const current = get().resolved
    get().setMode(current === 'dark' ? 'light' : 'dark')
  },
}))

export function initTheme() {
  const saved = (typeof localStorage !== 'undefined'
    ? (localStorage.getItem(STORAGE_KEY) as ThemeMode | null)
    : null) ?? 'system'

  const resolved = saved === 'system' ? resolveSystemTheme() : saved
  applyTheme(resolved)
  useThemeStore.setState({ mode: saved, resolved })

  // Respond to OS-level changes when "system" is selected
  if (typeof window !== 'undefined') {
    const mq = window.matchMedia('(prefers-color-scheme: dark)')
    mq.addEventListener('change', (e) => {
      if (useThemeStore.getState().mode === 'system') {
        const r: Resolved = e.matches ? 'dark' : 'light'
        applyTheme(r)
        useThemeStore.setState({ resolved: r })
      }
    })
  }
}
