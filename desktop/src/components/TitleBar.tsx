/**
 * Custom Windows-style title bar — used because tauri.conf.json sets
 * `decorations: false`. Drag region everywhere except the buttons.
 *   - Far left:   nothing (sidebar shows the logo)
 *   - Center:     subtle "HIVE" wordmark
 *   - Far right:  theme toggle, then ─ □ ✕ Windows window controls
 */
import { getCurrentWindow } from '@tauri-apps/api/window'
import { IconMinus, IconSquare, IconX } from '@tabler/icons-react'
import clsx from 'clsx'
import { ThemeToggle } from './ThemeToggle'

async function safe(fn: () => Promise<unknown>) {
  try {
    await fn()
  } catch {
    /* running in a plain browser preview — no Tauri window — silent */
  }
}

const closeWindow = () => safe(() => getCurrentWindow().close())
const minimizeWindow = () => safe(() => getCurrentWindow().minimize())
const toggleMaximize = () => safe(() => getCurrentWindow().toggleMaximize())

interface ControlProps {
  icon: typeof IconMinus
  onClick: () => void
  danger?: boolean
  label: string
}

function WinControl({ icon: Icon, onClick, danger, label }: ControlProps) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      onClick={onClick}
      className={clsx(
        'titlebar-nodrag w-11 h-full flex items-center justify-center text-ink-muted transition-colors',
        danger
          ? 'hover:bg-red-500 hover:text-white'
          : 'hover:bg-surface-2 hover:text-ink',
      )}
    >
      <Icon size={14} strokeWidth={1.5} />
    </button>
  )
}

export function TitleBar() {
  return (
    <div className="titlebar-drag flex items-stretch h-9 border-b border-line bg-bg">
      {/* Left pad to match the 64px sidebar width so the wordmark feels centred relative to content */}
      <div className="w-16 shrink-0" />

      <div className="flex-1 flex items-center px-3">
        <div className="text-[11px] tracking-[0.18em] text-ink-muted/80 select-none uppercase">
          hive
        </div>
      </div>

      <div className="titlebar-nodrag flex items-center pr-2">
        <ThemeToggle />
      </div>

      <div className="flex items-stretch">
        <WinControl icon={IconMinus} onClick={minimizeWindow} label="Minimize" />
        <WinControl icon={IconSquare} onClick={toggleMaximize} label="Maximize" />
        <WinControl icon={IconX} onClick={closeWindow} danger label="Close" />
      </div>
    </div>
  )
}
