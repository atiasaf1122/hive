import { useEffect, useState } from 'react'
import { Route, Routes } from 'react-router-dom'
import { CloseConfirmation } from './components/CloseConfirmation'
import { OnboardingWizard } from './components/onboarding/OnboardingWizard'
import { Sidebar } from './components/Sidebar'
import { Splash } from './components/Splash'
import { TitleBar } from './components/TitleBar'
import { SearchPalette } from './components/ui/SearchPalette'
import { useGlobalShortcuts } from './lib/shortcuts'
import { useTrayHeartbeat } from './lib/tray'
import { Automations } from './pages/Automations'
import { Plugins } from './pages/Plugins'
import { Projects } from './pages/Projects'
import { ProjectView } from './pages/ProjectView'
import { Settings } from './pages/Settings'
import { Skills } from './pages/Skills'
import { Usage } from './pages/Usage'
import { maybeStartOnboarding, useOnboarding } from './stores/onboarding'
import { applyAccent, useSettings } from './stores/settings'

export function App() {
  const [backendReady, setBackendReady] = useState(false)
  const [paletteOpen, setPaletteOpen] = useState(false)
  const onboardingActive = useOnboarding((s) => s.active)
  const accent = useSettings((s) => s.accent)

  useGlobalShortcuts()
  useTrayHeartbeat()

  // Apply saved accent on every boot so it survives reloads.
  useEffect(() => {
    applyAccent(accent)
  }, [accent])

  // Auto-open the onboarding wizard on first launch once the backend is up.
  useEffect(() => {
    if (backendReady) maybeStartOnboarding()
  }, [backendReady])

  // Ctrl+K opens the global command palette.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k' && !e.shiftKey) {
        e.preventDefault()
        setPaletteOpen((v) => !v)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  return (
    <div className="h-screen w-screen flex flex-col bg-bg text-ink overflow-hidden">
      <TitleBar />
      {!backendReady ? (
        <Splash onReady={() => setBackendReady(true)} />
      ) : (
        <div className="flex-1 flex overflow-hidden">
          <Sidebar />
          <main className="flex-1 overflow-hidden flex flex-col">
            <Routes>
              <Route path="/" element={<Projects />} />
              <Route path="/project/:id" element={<ProjectView />} />
              <Route path="/automations" element={<Automations />} />
              <Route path="/skills" element={<Skills />} />
              <Route path="/plugins" element={<Plugins />} />
              <Route path="/usage" element={<Usage />} />
              <Route path="/settings" element={<Settings />} />
            </Routes>
          </main>
        </div>
      )}

      <SearchPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} />
      <CloseConfirmation />
      {onboardingActive && <OnboardingWizard />}
    </div>
  )
}
