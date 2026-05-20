/**
 * Skeleton placeholder — soft shimmering bar/box used during data fetches.
 * Use sparingly; pages already have skeleton variants for their own layouts.
 */
import clsx from 'clsx'

interface Props {
  className?: string
  /** Pre-baked aspect — line is a single text-line height, block is a card-ish square */
  variant?: 'line' | 'block' | 'circle'
}

export function Skeleton({ className, variant = 'line' }: Props) {
  const base =
    'relative overflow-hidden bg-surface-2 isolate before:absolute before:inset-0 ' +
    'before:bg-gradient-to-r before:from-transparent before:via-line/60 before:to-transparent ' +
    'before:animate-[shimmer_1.4s_ease-in-out_infinite] before:translate-x-[-100%]'

  return (
    <div
      className={clsx(
        base,
        variant === 'line' && 'h-3 rounded',
        variant === 'block' && 'h-20 rounded-soft',
        variant === 'circle' && 'rounded-full',
        className,
      )}
      style={{
        // Local @keyframes — we don't want to pollute the global stylesheet.
      }}
    >
      <style>{`
        @keyframes shimmer {
          100% { transform: translateX(100%); }
        }
      `}</style>
    </div>
  )
}
