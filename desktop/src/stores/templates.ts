/**
 * Saved project templates — user-curated, not pre-made.
 *
 * A template captures a starting point: a name, the task prompt, model, and
 * approval mode. Saved from any project via "Save as template" in Phase 9B,
 * surfaced as a row above the dashboard project grid.
 *
 * Storage: localStorage (per-machine). Phase 9C can migrate this into the
 * backend SQLite if we want sync.
 */
import { create } from 'zustand'

const STORAGE_KEY = 'hive.templates'

export interface SavedTemplate {
  id: string
  name: string
  task: string
  model: string
  approval_mode: string
  emoji: string
  saved_at: number
}

interface State {
  items: SavedTemplate[]
}

interface Actions {
  save: (t: Omit<SavedTemplate, 'id' | 'saved_at'>) => SavedTemplate
  remove: (id: string) => void
}

function loadInitial(): SavedTemplate[] {
  if (typeof localStorage === 'undefined') return []
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    return raw ? (JSON.parse(raw) as SavedTemplate[]) : []
  } catch {
    return []
  }
}

function persist(items: SavedTemplate[]) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(items))
  } catch {
    /* silent */
  }
}

export const useTemplates = create<State & Actions>((set, get) => ({
  items: loadInitial(),
  save: (input) => {
    const tpl: SavedTemplate = {
      id: crypto.randomUUID(),
      saved_at: Date.now(),
      ...input,
    }
    const next = [tpl, ...get().items].slice(0, 50)
    persist(next)
    set({ items: next })
    return tpl
  },
  remove: (id) => {
    const next = get().items.filter((t) => t.id !== id)
    persist(next)
    set({ items: next })
  },
}))
