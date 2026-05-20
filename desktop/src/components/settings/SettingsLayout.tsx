/**
 * Settings shell — left sub-nav + right content panel.
 *
 * Four groups (General / AI / Integrations / Advanced) so the page never
 * grows into a monolith. Sub-pages are pure presentational components
 * that read/write `useSettings()`.
 */
import {
  IconAdjustments,
  IconBrain,
  IconBrandTelegram,
  IconCode,
  IconPalette,
  IconShieldLock,
  IconShieldCheck,
  IconUserCircle,
} from '@tabler/icons-react'
import clsx from 'clsx'
import type { Icon } from '@tabler/icons-react'

export type SettingsTab =
  | 'general'
  | 'appearance'
  | 'ai'
  | 'routing'
  | 'security'
  | 'safety'
  | 'integrations'
  | 'advanced'

interface NavGroup {
  group: string
  items: { id: SettingsTab; label: string; icon: Icon }[]
}

const NAV: NavGroup[] = [
  {
    group: 'General',
    items: [
      { id: 'general', label: 'Account', icon: IconUserCircle },
      { id: 'appearance', label: 'Appearance', icon: IconPalette },
    ],
  },
  {
    group: 'AI',
    items: [
      { id: 'ai', label: 'Backends & models', icon: IconBrain },
      { id: 'routing', label: 'Routing rules', icon: IconAdjustments },
    ],
  },
  {
    group: 'Safety',
    items: [
      { id: 'security', label: 'Command sandbox', icon: IconShieldLock },
      { id: 'safety',   label: 'Limits & breakers', icon: IconShieldCheck },
    ],
  },
  {
    group: 'Integrations',
    items: [
      { id: 'integrations', label: 'Telegram & alerts', icon: IconBrandTelegram },
    ],
  },
  {
    group: 'Advanced',
    items: [
      { id: 'advanced', label: 'Storage & developer', icon: IconCode },
    ],
  },
]

interface Props {
  active: SettingsTab
  onChange: (next: SettingsTab) => void
  children: React.ReactNode
}

export function SettingsLayout({ active, onChange, children }: Props) {
  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-5xl mx-auto p-8">
        <h1 className="text-2xl font-medium text-ink mb-1">Settings</h1>
        <p className="text-ink-muted text-sm mb-6">
          Make HIVE feel like yours.
        </p>

        <div className="grid grid-cols-[220px_1fr] gap-6">
          <aside className="space-y-5">
            {NAV.map((group) => (
              <div key={group.group}>
                <div className="text-[10px] uppercase tracking-wider text-ink-faint mb-1.5 px-2">
                  {group.group}
                </div>
                <nav className="space-y-0.5">
                  {group.items.map((item) => {
                    const Icon = item.icon
                    const isActive = active === item.id
                    return (
                      <button
                        key={item.id}
                        type="button"
                        onClick={() => onChange(item.id)}
                        className={clsx(
                          'w-full flex items-center gap-2.5 px-2.5 py-1.5 rounded-soft text-sm transition-colors',
                          isActive
                            ? 'bg-surface-2 text-ink'
                            : 'text-ink-muted hover:text-ink hover:bg-surface-2/60',
                        )}
                      >
                        <Icon size={15} strokeWidth={1.5} />
                        {item.label}
                      </button>
                    )
                  })}
                </nav>
              </div>
            ))}
          </aside>

          <main className="space-y-4">
            {children}
          </main>
        </div>
      </div>
    </div>
  )
}

interface CardProps {
  title: string
  description?: string
  children: React.ReactNode
}

export function SettingCard({ title, description, children }: CardProps) {
  return (
    <section className="card p-5">
      <div className="mb-3">
        <h2 className="text-ink text-sm font-medium">{title}</h2>
        {description && (
          <p className="text-ink-muted text-xs mt-0.5 leading-relaxed">{description}</p>
        )}
      </div>
      {children}
    </section>
  )
}

interface RowProps {
  label: string
  hint?: string
  children: React.ReactNode
}

export function SettingRow({ label, hint, children }: RowProps) {
  return (
    <div className="flex items-start justify-between gap-4 py-2.5 border-t border-line first:border-t-0">
      <div className="min-w-0 flex-1">
        <div className="text-sm text-ink">{label}</div>
        {hint && <div className="text-[11px] text-ink-faint mt-0.5">{hint}</div>}
      </div>
      <div className="shrink-0">{children}</div>
    </div>
  )
}
