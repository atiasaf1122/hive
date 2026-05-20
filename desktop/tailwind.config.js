/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  darkMode: ['class'],
  theme: {
    extend: {
      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', 'sans-serif'],
      },
      colors: {
        // Semantic palette wired to CSS variables so light/dark swap is instant.
        bg: 'rgb(var(--c-bg) / <alpha-value>)',
        surface: 'rgb(var(--c-surface) / <alpha-value>)',
        'surface-2': 'rgb(var(--c-surface-2) / <alpha-value>)',
        ink: 'rgb(var(--c-ink) / <alpha-value>)',
        'ink-muted': 'rgb(var(--c-ink-muted) / <alpha-value>)',
        'ink-faint': 'rgb(var(--c-ink-faint) / <alpha-value>)',
        line: 'rgb(var(--c-line) / <alpha-value>)',
        accent: {
          DEFAULT: 'rgb(var(--c-accent) / <alpha-value>)',
          warm: 'rgb(var(--c-accent-warm) / <alpha-value>)',
        },
      },
      borderRadius: {
        soft: '14px',
        card: '16px',
        xl2: '18px',
      },
      boxShadow: {
        hover: '0 4px 12px rgba(0,0,0,0.06)',
        hoverDark: '0 4px 16px rgba(0,0,0,0.4)',
      },
      backgroundImage: {
        'accent-gradient': 'linear-gradient(135deg, #F5A623 0%, #D85A30 100%)',
      },
    },
  },
  plugins: [],
}
