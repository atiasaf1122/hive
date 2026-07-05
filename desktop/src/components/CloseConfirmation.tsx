/**
 * Close-confirmation flow (Phase 9D-C).
 *
 *   The Rust side calls `window.prevent_close()` and emits
 *   "hive://close-requested" whenever the user tries to close the window.
 *   This component listens, asks the backend for `/api/lifecycle/active-counts`,
 *   and either:
 *
 *     • silently confirms the close (nothing active and `backgroundAutomations`
 *       is off, OR nothing active at all),
 *     • shows the confirmation modal if interactive agents are still running,
 *     • or silently closes-to-tray if only automations are running and
 *       `backgroundAutomations` is on (the tray comes from 9D-B).
 *
 *   The Rust `confirm_close` IPC command receives the final decision.
 */
import { IconAlertTriangle } from '@tabler/icons-react'
import { invoke } from '@tauri-apps/api/core'
import { listen, type UnlistenFn } from '@tauri-apps/api/event'
import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { useSettings } from '../stores/settings'

interface ActiveCounts {
  interactive_agents: number
  enabled_automations: number
  telegram_bot_running: boolean
  has_interactive_work: boolean
  should_keep_background: boolean
}

export function CloseConfirmation() {
  const backgroundAutomations = useSettings((s) => s.backgroundAutomations)
  const [pending, setPending] = useState<ActiveCounts | null>(null)

  useEffect(() => {
    // `unlisten()` is assigned asynchronously inside listen().then(...).
    // If this effect re-runs (backgroundAutomations toggle) before the
    // promise resolves, the cleanup below would read a stale null and
    // the previous listener would leak — `cancelled` plus assigning
    // through the closure lets us tear down both halves correctly.
    let cancelled = false
    let unlistenFn: UnlistenFn | null = null

    const handler = async () => {
      let counts: ActiveCounts
      try {
        counts = await api.get<ActiveCounts>('/api/lifecycle/active-counts')
      } catch {
        // Couldn't reach backend — just close.
        await invoke('confirm_close', { confirm: true })
        return
      }

      if (counts.has_interactive_work) {
        setPending(counts)
        return
      }
      if (counts.should_keep_background && backgroundAutomations) {
        await invoke('confirm_close', { confirm: true })
        return
      }
      await invoke('confirm_close', { confirm: true })
    }

    listen('hive://close-requested', handler).then((fn) => {
      if (cancelled) {
        // Effect cleanup already ran while we were waiting for listen()
        // to resolve — detach immediately so the listener doesn't leak.
        fn()
        return
      }
      unlistenFn = fn
    }).catch(() => {})

    return () => {
      cancelled = true
      if (unlistenFn) unlistenFn()
    }
  }, [backgroundAutomations])

  if (!pending) return null

  async function confirm(stop: boolean) {
    setPending(null)
    if (stop) {
      await invoke('confirm_close', { confirm: true })
    } else {
      await invoke('confirm_close', { confirm: false })
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm">
      <div className="card shadow-hover w-[440px] max-w-[92vw] overflow-hidden">
        <header className="px-5 py-3 border-b border-line flex items-center gap-2">
          <IconAlertTriangle size={16} className="text-amber-500" />
          <h2 className="text-sm text-ink">Agents still working</h2>
        </header>

        <div className="p-5 text-sm space-y-2">
          <p className="text-ink">
            {pending.interactive_agents} agent{pending.interactive_agents === 1 ? ' is' : 's are'}{' '}
            still running across your projects.
          </p>
          <p className="text-ink-muted text-xs">
            Closing will stop them. Project state lives in SQLite so you can resume
            from where you left off the next time you open HIVE.
            {pending.enabled_automations > 0 && backgroundAutomations && (
              <>
                {' '}
                {pending.enabled_automations} enabled automation{pending.enabled_automations === 1 ? '' : 's'}{' '}
                will keep running in the tray after close.
              </>
            )}
          </p>
        </div>

        <footer className="px-5 py-3 border-t border-line flex items-center justify-end gap-2">
          <button type="button" onClick={() => void confirm(false)} className="btn-ghost text-xs">
            Cancel
          </button>
          <button
            type="button"
            autoFocus
            onClick={() => void confirm(true)}
            className="btn-primary text-xs"
          >
            Stop and close
          </button>
        </footer>
      </div>
    </div>
  )
}
