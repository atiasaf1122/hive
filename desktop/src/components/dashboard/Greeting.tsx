/**
 * Big, friendly time-of-day greeting at the top of Projects.
 *
 * Phase 9C will wire the user's name from settings. Until then, the greeting
 * stands on its own — no awkward "[name]" placeholder.
 */
import { greetingForNow } from '../../lib/greeting'

interface Props {
  name?: string
}

export function Greeting({ name }: Props) {
  return (
    <div className="mb-8">
      <h1 className="text-4xl font-medium text-ink tracking-tight">
        {greetingForNow()}
        {name ? `, ${name}` : ''}
      </h1>
      <p className="mt-2 text-ink-muted text-sm">
        Start something new or jump back into a project.
      </p>
    </div>
  )
}
