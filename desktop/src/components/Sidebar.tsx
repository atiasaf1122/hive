/**
 * 64px persistent left rail. Logo at top, nav buttons stacked, Settings at bottom.
 * Each button is a soft-pill icon that tints under the accent when active.
 */
import {
  IconLayoutGrid,
  IconClock,
  IconBook2,
  IconPlug,
  IconChartHistogram,
  IconSettings,
} from '@tabler/icons-react'
import clsx from 'clsx'
import { NavLink } from 'react-router-dom'
import { HiveLogo } from './HiveLogo'

interface NavItem {
  to: string
  label: string
  icon: typeof IconLayoutGrid
}

const PRIMARY: NavItem[] = [
  { to: '/', label: 'Projects', icon: IconLayoutGrid },
  { to: '/automations', label: 'Automations', icon: IconClock },
  { to: '/skills', label: 'Skills', icon: IconBook2 },
  { to: '/plugins', label: 'Plugins', icon: IconPlug },
  { to: '/usage', label: 'Usage', icon: IconChartHistogram },
]

const SETTINGS: NavItem = { to: '/settings', label: 'Settings', icon: IconSettings }

function NavButton({ item }: { item: NavItem }) {
  const Icon = item.icon
  return (
    <NavLink
      to={item.to}
      end={item.to === '/'}
      className={({ isActive }) =>
        clsx(
          'group relative flex items-center justify-center w-11 h-11 rounded-soft transition-colors',
          isActive
            ? 'bg-accent-gradient text-white shadow-sm'
            : 'text-ink-muted hover:text-ink hover:bg-surface-2',
        )
      }
      title={item.label}
    >
      <Icon size={20} strokeWidth={1.75} />
      {/* tooltip */}
      <span
        className="pointer-events-none absolute left-12 top-1/2 -translate-y-1/2 whitespace-nowrap text-xs px-2 py-1 rounded-md bg-ink text-bg opacity-0 group-hover:opacity-100 transition-opacity z-10"
      >
        {item.label}
      </span>
    </NavLink>
  )
}

export function Sidebar() {
  return (
    <aside className="w-16 shrink-0 border-r border-line bg-bg flex flex-col items-center py-4">
      {/* Logo — also acts as the home button */}
      <NavLink to="/" end className="mb-4 transition-transform hover:scale-105">
        <HiveLogo size={32} />
      </NavLink>

      <nav className="flex flex-col gap-1.5">
        {PRIMARY.map((item) => (
          <NavButton key={item.to} item={item} />
        ))}
      </nav>

      <div className="mt-auto">
        <NavButton item={SETTINGS} />
      </div>
    </aside>
  )
}
