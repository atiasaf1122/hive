/**
 * Browser-style tab bar for the project view.
 * Tabs persist in localStorage so closing/reopening the app restores them.
 */
import { create } from 'zustand'

const STORAGE_KEY = 'hive.project_tabs'

interface State {
  /** Ordered list of session IDs currently open as tabs. */
  open: string[]
}

interface Actions {
  openTab: (sessionId: string) => void
  closeTab: (sessionId: string) => void
  reorder: (next: string[]) => void
  clearAll: () => void
}

function loadInitial(): string[] {
  if (typeof localStorage === 'undefined') return []
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    return raw ? (JSON.parse(raw) as string[]) : []
  } catch {
    return []
  }
}

function persist(open: string[]) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(open))
  } catch {
    /* private mode, quota — silent */
  }
}

export const useProjectTabs = create<State & Actions>((set, get) => ({
  open: loadInitial(),
  openTab: (sessionId) => {
    if (get().open.includes(sessionId)) return
    const next = [...get().open, sessionId]
    persist(next)
    set({ open: next })
  },
  closeTab: (sessionId) => {
    const next = get().open.filter((id) => id !== sessionId)
    persist(next)
    set({ open: next })
  },
  reorder: (next) => {
    persist(next)
    set({ open: next })
  },
  clearAll: () => {
    persist([])
    set({ open: [] })
  },
}))
