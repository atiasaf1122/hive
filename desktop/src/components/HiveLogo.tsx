/**
 * HIVE logo — three orange hexagons in a honeycomb cluster.
 * Pure SVG, scales with `size` prop. Uses the accent gradient.
 */
interface Props {
  size?: number
  className?: string
}

export function HiveLogo({ size = 32, className }: Props) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 40 40"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      aria-label="HIVE"
    >
      <defs>
        <linearGradient id="hive-grad" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#F5A623" />
          <stop offset="100%" stopColor="#D85A30" />
        </linearGradient>
      </defs>
      {/* top hex */}
      <path
        d="M20 2 L28 7 L28 17 L20 22 L12 17 L12 7 Z"
        fill="url(#hive-grad)"
        opacity="0.95"
      />
      {/* bottom-left hex */}
      <path
        d="M8 18 L16 23 L16 33 L8 38 L0 33 L0 23 Z"
        fill="url(#hive-grad)"
        opacity="0.75"
      />
      {/* bottom-right hex */}
      <path
        d="M32 18 L40 23 L40 33 L32 38 L24 33 L24 23 Z"
        fill="url(#hive-grad)"
        opacity="0.85"
      />
    </svg>
  )
}
