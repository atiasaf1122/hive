/**
 * Top-right pill switcher for theme.
 * Three segments: System / Light / Dark. Active one has the accent gradient.
 */
import { IconDeviceLaptop, IconMoon, IconSun } from '@tabler/icons-react'
import clsx from 'clsx'
import { useThemeStore, type ThemeMode } from '../stores/theme'

const OPTIONS: { value: ThemeMode; label: string; icon: typeof IconSun }[] = [
  { value: 'system', label: 'System', icon: IconDeviceLaptop },
  { value: 'light', label: 'Light', icon: IconSun },
  { value: 'dark', label: 'Dark', icon: IconMoon },
]

export function ThemeToggle() {
  const mode = useThemeStore((s) => s.mode)
  const setMode = useThemeStore((s) => s.setMode)

  return (
    <div className="titlebar-nodrag inline-flex items-center gap-0.5 bg-surface-2 border border-line rounded-full p-0.5">
      {OPTIONS.map(({ value, label, icon: Icon }) => {
        const active = mode === value
        return (
          <button
            key={value}
            type="button"
            onClick={() => setMode(value)}
            title={label}
            className={clsx(
              'flex items-center justify-center w-7 h-7 rounded-full transition-all',
              active
                ? 'bg-accent-gradient text-white shadow-sm'
                : 'text-ink-muted hover:text-ink',
            )}
          >
            <Icon size={14} strokeWidth={1.75} />
          </button>
        )
      })}
    </div>
  )
}
