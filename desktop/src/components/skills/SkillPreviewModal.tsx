/**
 * Modal preview shown before any install. Renders the security context
 * (verified / unverified / Feb 2026 supply-chain note) prominently — the
 * point is to give the user a chance to bail on shady items.
 */
import { IconAlertTriangle, IconExternalLink, IconShieldCheck, IconX } from '@tabler/icons-react'
import type { SkillItem } from './SkillCard'

interface Props {
  skill: SkillItem | null
  onClose: () => void
  onInstall: (skill: SkillItem) => void
}

export function SkillPreviewModal({ skill, onClose, onInstall }: Props) {
  if (!skill) return null

  return (
    <div
      className="fixed inset-0 z-40 flex items-center justify-center bg-black/30 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="w-[640px] max-w-[92vw] card shadow-hover overflow-hidden flex flex-col max-h-[80vh]"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="px-5 py-3 border-b border-line flex items-start justify-between gap-3">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <h2 className="text-sm font-medium text-ink truncate">{skill.name}</h2>
              <span className="text-[10px] uppercase tracking-wider text-ink-faint border border-line rounded px-1.5 py-px">
                {skill.source_label}
              </span>
            </div>
            <div className="text-xs text-ink-muted mt-0.5">{skill.id}</div>
          </div>
          <button type="button" onClick={onClose} className="text-ink-faint hover:text-ink">
            <IconX size={16} />
          </button>
        </header>

        <div className="overflow-y-auto p-5 space-y-4">
          <p className="text-sm text-ink">{skill.description}</p>

          {skill.warn_unverified && (
            <div className="card p-3 border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-300">
              <div className="flex items-start gap-2">
                <IconAlertTriangle size={16} strokeWidth={1.75} className="shrink-0 mt-0.5" />
                <div className="text-xs leading-relaxed">
                  <div className="font-medium mb-1">This skill is unverified.</div>
                  In February 2026 ClawHub had a supply-chain incident where attackers
                  pushed lookalike packages. Review the SKILL.md below — especially
                  the system-prompt and any shell snippets — before installing.
                  Skills with under 100 stars or no verification flag require this
                  manual confirmation.
                </div>
              </div>
            </div>
          )}

          {skill.verified && (
            <div className="card p-3 border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300">
              <div className="flex items-start gap-2">
                <IconShieldCheck size={16} strokeWidth={1.75} className="shrink-0 mt-0.5" />
                <div className="text-xs">
                  Verified publisher · safe to install without manual review.
                </div>
              </div>
            </div>
          )}

          <div>
            <div className="text-xs text-ink-muted mb-1">Details</div>
            <ul className="text-xs text-ink space-y-0.5">
              <li>Source: {skill.source_label}</li>
              {skill.author && <li>Author: {skill.author}</li>}
              <li>Stars: {skill.stars ?? '—'}</li>
              <li>Downloads: {skill.downloads ?? '—'}</li>
              {skill.tags.length > 0 && <li>Tags: {skill.tags.join(', ')}</li>}
            </ul>
          </div>

          <div>
            <div className="text-xs text-ink-muted mb-1">SKILL.md preview</div>
            <pre className="bg-surface-2 border border-line rounded-soft p-3 text-xs text-ink-muted whitespace-pre-wrap overflow-x-auto">
              {`# ${skill.name}

> Full SKILL.md is fetched from upstream at install time. Phase 9D will
> render the actual content here with a Markdown renderer; for now this
> is the metadata-only preview.

Source URL: ${skill.url}`}
            </pre>
          </div>

          <a
            href={skill.url}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1.5 text-xs text-ink-muted hover:text-ink underline"
          >
            Open source <IconExternalLink size={12} />
          </a>
        </div>

        <footer className="px-5 py-3 border-t border-line flex items-center justify-between">
          <button type="button" onClick={onClose} className="btn-ghost text-xs">
            Cancel
          </button>
          <button
            type="button"
            onClick={() => {
              onInstall(skill)
              onClose()
            }}
            className="btn-primary text-xs"
          >
            {skill.warn_unverified ? 'Install anyway' : 'Install'}
          </button>
        </footer>
      </div>
    </div>
  )
}
