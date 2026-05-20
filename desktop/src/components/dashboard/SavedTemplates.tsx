/**
 * Row of saved templates above the project grid. Each one is a tiny card that
 * pre-fills the quick start when clicked.
 *
 * Templates are user-saved (see stores/templates.ts) — there are no built-ins.
 * Empty state: nothing rendered (the row simply doesn't appear).
 */
import { IconBookmarks, IconX } from '@tabler/icons-react'
import clsx from 'clsx'
import { useTemplates, type SavedTemplate } from '../../stores/templates'

interface Props {
  onPick: (template: SavedTemplate) => void
}

export function SavedTemplates({ onPick }: Props) {
  const items = useTemplates((s) => s.items)
  const remove = useTemplates((s) => s.remove)

  if (items.length === 0) return null

  return (
    <section className="mb-6">
      <div className="flex items-center gap-2 mb-3 text-xs text-ink-muted">
        <IconBookmarks size={14} strokeWidth={1.5} />
        <span>Saved templates</span>
      </div>
      <div className="flex flex-wrap gap-2">
        {items.map((t) => (
          <div
            key={t.id}
            className={clsx(
              'group relative inline-flex items-center gap-2 max-w-[260px] px-3 py-2 rounded-soft',
              'bg-surface border border-line hover:border-ink-faint transition-colors',
            )}
          >
            <button
              type="button"
              onClick={() => onPick(t)}
              className="flex items-center gap-2 min-w-0 text-left"
            >
              <span className="text-lg leading-none">{t.emoji}</span>
              <span className="text-sm text-ink truncate">{t.name}</span>
            </button>
            <button
              type="button"
              aria-label="Remove template"
              onClick={() => remove(t.id)}
              className="opacity-0 group-hover:opacity-100 text-ink-faint hover:text-ink-muted transition-opacity"
            >
              <IconX size={12} strokeWidth={1.75} />
            </button>
          </div>
        ))}
      </div>
    </section>
  )
}
