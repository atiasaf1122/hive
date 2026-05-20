/**
 * Global keyboard shortcuts.
 *
 *   Ctrl + T          new project (focus quick-start on dashboard)
 *   Ctrl + W          close current project tab
 *   Ctrl + Shift + N  open a fresh Tauri window (multi-window)
 *   Ctrl + 1..9       switch to nth open tab
 *   Ctrl + ,          settings
 *   Ctrl + K          global search (Phase 9C — stub for now)
 *   Ctrl + Shift + ?  shortcuts help (Phase 9C)
 *
 * Wired once from <App> so the bindings work regardless of focus. Slash
 * commands (Ctrl+/) are handled in the composer itself.
 */
import { invoke } from '@tauri-apps/api/core'
import { useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { useProjectTabs } from '../stores/projectTabs'

export function useGlobalShortcuts() {
  const navigate = useNavigate()
  const open = useProjectTabs((s) => s.open)
  const closeTab = useProjectTabs((s) => s.closeTab)

  useEffect(() => {
    function handler(e: KeyboardEvent) {
      // Only react to Ctrl/Cmd combos — not bare keys.
      if (!(e.ctrlKey || e.metaKey)) return

      // Ctrl+Shift+N → open a brand-new Tauri window. Outside Tauri the
      // invoke promise rejects; we swallow that so the browser preview
      // keeps working.
      if (e.shiftKey && e.key.toLowerCase() === 'n') {
        e.preventDefault()
        void invoke('open_new_window').catch(() => undefined)
        return
      }

      // Ctrl+T → focus quick-start on dashboard
      if (e.key.toLowerCase() === 't' && !e.shiftKey) {
        e.preventDefault()
        navigate('/')
        window.setTimeout(() => {
          document
            .querySelector<HTMLTextAreaElement>('[data-quick-start]')
            ?.focus()
        }, 50)
        return
      }

      // Ctrl+W → close current tab (if on /project/:id)
      if (e.key.toLowerCase() === 'w' && !e.shiftKey) {
        const match = window.location.hash.match(/#\/project\/([^/?]+)/)
        if (match) {
          e.preventDefault()
          const id = match[1]
          closeTab(id)
          const next = useProjectTabs.getState().open[0]
          navigate(next ? `/project/${next}` : '/')
        }
        return
      }

      // Ctrl+, → settings
      if (e.key === ',' && !e.shiftKey) {
        e.preventDefault()
        navigate('/settings')
        return
      }

      // Ctrl+1..9 → switch tab
      if (/^[1-9]$/.test(e.key) && !e.shiftKey) {
        const idx = parseInt(e.key, 10) - 1
        if (idx < open.length) {
          e.preventDefault()
          navigate(`/project/${open[idx]}`)
        }
        return
      }
    }

    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [navigate, open, closeTab])
}
