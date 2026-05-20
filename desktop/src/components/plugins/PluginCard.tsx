/**
 * MCP plugin / server card. Used in both the Installed and Discover tabs.
 */
import { IconCircleDot, IconExternalLink, IconPlus } from '@tabler/icons-react'
import clsx from 'clsx'

export interface MCPItem {
  id: string
  name: string
  description: string
  source: string
  source_label: string
  install: { transport: string; package: string }
  category: string
  permissions: string[]
  homepage?: string
  verified: boolean
  installs?: number | null
}

function compactNum(n: number | null | undefined): string {
  if (n == null) return ''
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`
  return String(n)
}

interface Props {
  item: MCPItem
  installed: boolean
  onInstall: (i: MCPItem) => void
  onConfigure?: (i: MCPItem) => void
}

export function PluginCard({ item, installed, onInstall, onConfigure }: Props) {
  return (
    <div className="card card-hover p-4 flex flex-col gap-2">
      <div className="flex items-start gap-3">
        <div className="w-9 h-9 rounded-soft bg-surface-2 flex items-center justify-center text-ink-muted text-sm font-medium uppercase shrink-0">
          {item.name[0]}
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <div className="text-sm text-ink truncate max-w-[200px]">{item.name}</div>
            <span className="text-[10px] uppercase tracking-wider text-ink-faint border border-line rounded px-1.5 py-px">
              {item.source_label}
            </span>
            {item.verified && (
              <span className="text-[10px] text-emerald-500">verified</span>
            )}
            {installed && (
              <span className="text-[10px] text-accent border border-accent/40 rounded px-1.5 py-px">
                running
              </span>
            )}
          </div>
          <div className="text-xs text-ink-muted line-clamp-2 mt-0.5">
            {item.description}
          </div>
        </div>
      </div>

      <div className="flex items-center justify-between pt-2 border-t border-line">
        <div className="text-[11px] text-ink-faint flex items-center gap-2 min-w-0">
          <span className="truncate">{item.category}</span>
          {item.installs != null && (
            <>
              <span>·</span>
              <span>↓ {compactNum(item.installs)}</span>
            </>
          )}
          {item.homepage && (
            <a
              href={item.homepage}
              target="_blank"
              rel="noreferrer"
              className="hover:text-ink-muted inline-flex items-center gap-1"
              title="Open source"
            >
              <IconExternalLink size={11} />
            </a>
          )}
        </div>

        <div className="flex items-center gap-1">
          {installed ? (
            <>
              {onConfigure && (
                <button
                  type="button"
                  onClick={() => onConfigure(item)}
                  className="btn-ghost text-xs"
                >
                  Configure
                </button>
              )}
              <button
                type="button"
                className="btn-ghost text-xs text-ink-muted inline-flex items-center gap-1"
              >
                <IconCircleDot size={11} className="text-emerald-500" /> running
              </button>
            </>
          ) : (
            <button
              type="button"
              onClick={() => onInstall(item)}
              className={clsx('btn-primary text-xs inline-flex items-center gap-1')}
            >
              <IconPlus size={13} strokeWidth={1.75} /> Install
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
