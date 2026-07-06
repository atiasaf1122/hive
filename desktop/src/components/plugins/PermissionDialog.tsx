/**
 * Permission gate shown before any MCP server is added to Claude's config.
 *
 * Lists everything the server will be allowed to do (files, network, tokens).
 * The user has to actively click Continue — there's no auto-accept path.
 */
import { IconShieldLock, IconX } from '@tabler/icons-react'
import type { MCPItem } from './PluginCard'

interface Props {
  item: MCPItem | null
  onClose: () => void
  onConfirm: (item: MCPItem) => void
}

export function PermissionDialog({ item, onClose, onConfirm }: Props) {
  if (!item) return null
  return (
    <div
      className="fixed inset-0 z-40 flex items-center justify-center bg-black/30 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="w-[520px] max-w-[92vw] card shadow-hover overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="px-5 py-3 border-b border-line flex items-center justify-between">
          <div className="flex items-center gap-2">
            <IconShieldLock size={16} className="text-ink-muted" />
            <h2 className="text-sm text-ink">Permission needed</h2>
          </div>
          <button type="button" onClick={onClose} className="text-ink-faint hover:text-ink">
            <IconX size={16} />
          </button>
        </header>

        <div className="p-5 space-y-3 text-sm">
          <p className="text-ink">
            <span className="font-medium">{item.name}</span> will be added to
            your interactive Claude CLI config (~/.claude.json) — not to HIVE
            agents — and can access:
          </p>

          {item.permissions.length === 0 ? (
            <div className="text-xs text-ink-muted italic">
              No declared permissions.
            </div>
          ) : (
            <ul className="text-xs text-ink-muted space-y-1.5 list-disc pl-5">
              {item.permissions.map((p, i) => (
                <li key={i}>{p}</li>
              ))}
            </ul>
          )}

          <div className="text-xs text-ink-faint pt-2 border-t border-line">
            Install transport: <code className="text-ink-muted">{item.install.transport}</code>
            {' · '}
            <code className="text-ink-muted">{item.install.package}</code>
          </div>
        </div>

        <footer className="px-5 py-3 border-t border-line flex items-center justify-end gap-2">
          <button type="button" onClick={onClose} className="btn-ghost text-xs">
            Cancel
          </button>
          <button
            type="button"
            onClick={() => {
              onConfirm(item)
              onClose()
            }}
            className="btn-primary text-xs"
          >
            Continue
          </button>
        </footer>
      </div>
    </div>
  )
}
