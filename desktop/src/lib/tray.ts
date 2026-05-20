/**
 * Tray heartbeat — polls /api/lifecycle/active-counts every 15 s and tells
 * the Rust side how many automations are running so it can show (or hide)
 * the system-tray icon.
 *
 * Phase-10 fix: only call `update_tray_status` when running/tooltip
 * actually change. The previous version invoked Tauri every poll, which
 * on Windows produced an "Error removing system tray icon" line in the
 * dev console every 15 s because the visibility toggle was a no-op
 * applied repeatedly.
 *
 * Running outside Tauri (pure web preview) is a no-op: `invoke` will
 * throw and we swallow it.
 */
import { invoke } from '@tauri-apps/api/core'
import { useEffect, useRef } from 'react'
import { api } from './api'

interface ActiveCounts {
  interactive_agents: number
  enabled_automations: number
  telegram_bot_running: boolean
  should_keep_background: boolean
}

interface TrayState extends Record<string, unknown> {
  running: number
  tooltip: string
}

function deriveTrayState(c: ActiveCounts): TrayState {
  const running = c.enabled_automations + (c.telegram_bot_running ? 1 : 0)
  const tooltip = running === 0
    ? 'HIVE'
    : `HIVE — ${c.enabled_automations} automation${c.enabled_automations === 1 ? '' : 's'}` +
      (c.telegram_bot_running ? ' · Telegram on' : '')
  return { running, tooltip }
}

export function useTrayHeartbeat() {
  const last = useRef<TrayState | null>(null)

  useEffect(() => {
    let cancelled = false

    async function tick() {
      try {
        const counts = await api.get<ActiveCounts>('/api/lifecycle/active-counts')
        if (cancelled) return
        const next = deriveTrayState(counts)

        // Only invoke when something the Rust side cares about has changed.
        // Repeated no-op set_visible(false) calls produce
        // "Error removing system tray icon" noise on Windows.
        if (
          last.current === null ||
          last.current.running !== next.running ||
          last.current.tooltip !== next.tooltip
        ) {
          try {
            await invoke('update_tray_status', next)
            last.current = next
          } catch {
            // Not running under Tauri — silent.
          }
        }
      } catch {
        // Backend unreachable — silent until next tick.
      }
    }

    void tick()
    const id = window.setInterval(tick, 15_000)
    return () => {
      cancelled = true
      window.clearInterval(id)
    }
  }, [])
}
