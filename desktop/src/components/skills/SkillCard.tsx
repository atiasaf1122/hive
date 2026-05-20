/**
 * Skill card — discovery + installed views.
 *
 *   ┌──────────────────────────────────────────────────────────────┐
 *   │ name                       [source badge]   [verified badge] │
 *   │ description                                                  │
 *   │ tags…                                                        │
 *   │                                                              │
 *   │ ★ 1.2k · ↓ 8.4k                  [preview] [install]         │
 *   └──────────────────────────────────────────────────────────────┘
 */
import {
  IconAlertTriangle,
  IconDownload,
  IconEye,
  IconShieldCheck,
} from '@tabler/icons-react'
import clsx from 'clsx'

export interface SkillItem {
  id: string
  name: string
  description: string
  source: string
  source_label: string
  url: string
  tags: string[]
  stars: number | null
  downloads: number | null
  verified: boolean
  warn_unverified: boolean
  auto_install_ok: boolean
  author?: string
}

function compactNum(n: number | null): string {
  if (n == null) return '—'
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`
  return String(n)
}

interface Props {
  skill: SkillItem
  installed: boolean
  onPreview: (s: SkillItem) => void
  onInstall: (s: SkillItem) => void
}

export function SkillCard({ skill, installed, onPreview, onInstall }: Props) {
  return (
    <div className="card card-hover p-4 flex flex-col gap-2.5">
      <div className="flex items-start gap-3">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <div className="text-sm text-ink font-medium truncate max-w-[200px]" title={skill.name}>
              {skill.name}
            </div>

            <span className="text-[10px] uppercase tracking-wider text-ink-faint border border-line rounded px-1.5 py-px">
              {skill.source_label}
            </span>

            {skill.verified ? (
              <span className="inline-flex items-center gap-0.5 text-[10px] text-emerald-500" title="Verified publisher">
                <IconShieldCheck size={11} strokeWidth={2} /> verified
              </span>
            ) : skill.warn_unverified ? (
              <span
                className="inline-flex items-center gap-0.5 text-[10px] text-amber-500"
                title="Unverified — review before installing"
              >
                <IconAlertTriangle size={11} strokeWidth={2} /> unverified
              </span>
            ) : null}

            {installed && (
              <span className="text-[10px] text-accent border border-accent/40 rounded px-1.5 py-px">
                installed
              </span>
            )}
          </div>

          <div className="text-xs text-ink-muted mt-1 line-clamp-2">
            {skill.description}
          </div>
        </div>
      </div>

      {skill.tags.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {skill.tags.slice(0, 5).map((t) => (
            <span
              key={t}
              className="text-[10px] bg-surface-2 text-ink-muted rounded-full px-2 py-px"
            >
              {t}
            </span>
          ))}
        </div>
      )}

      <div className="flex items-center justify-between pt-2 border-t border-line">
        <div className="text-[11px] text-ink-faint flex items-center gap-2">
          <span>★ {compactNum(skill.stars)}</span>
          <span>·</span>
          <span>↓ {compactNum(skill.downloads)}</span>
          {skill.author && <><span>·</span><span>{skill.author}</span></>}
        </div>

        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={() => onPreview(skill)}
            className="btn-ghost text-xs inline-flex items-center gap-1"
            title="Preview SKILL.md before installing"
          >
            <IconEye size={13} strokeWidth={1.75} /> Preview
          </button>
          <button
            type="button"
            onClick={() => onInstall(skill)}
            disabled={installed}
            className={clsx(
              'text-xs inline-flex items-center gap-1 px-3 py-1.5 rounded-soft',
              installed
                ? 'text-ink-faint cursor-default'
                : 'btn-primary',
            )}
          >
            <IconDownload size={13} strokeWidth={1.75} />
            {installed ? 'Installed' : skill.auto_install_ok ? 'Install' : 'Install…'}
          </button>
        </div>
      </div>
    </div>
  )
}
