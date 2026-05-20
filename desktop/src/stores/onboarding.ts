/**
 * First-run onboarding flag.
 *
 * `seen` flips to true after the user finishes (or skips) the wizard.
 * Settings → Help can call `reopen()` to walk through it again later.
 */
import { create } from 'zustand'

const STORAGE_KEY = 'hive.onboarded.v1'

interface Store {
  seen: boolean
  active: boolean
  reopen: () => void
  finish: () => void
}

export const useOnboarding = create<Store>((set) => ({
  seen:
    typeof localStorage === 'undefined'
      ? true
      : localStorage.getItem(STORAGE_KEY) === '1',
  active: false,
  reopen: () => set({ active: true }),
  finish: () => {
    try {
      localStorage.setItem(STORAGE_KEY, '1')
    } catch {
      /* silent */
    }
    set({ seen: true, active: false })
  },
}))

/** Called at App boot — opens the wizard automatically on first launch. */
export function maybeStartOnboarding() {
  const state = useOnboarding.getState()
  if (!state.seen) {
    useOnboarding.setState({ active: true })
  }
}
