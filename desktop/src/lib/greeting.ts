/**
 * "Good morning / afternoon / evening" based on the local clock.
 * Time bands keep the warm Notion-ish tone — never "good night".
 */
export function greetingForNow(now = new Date()): string {
  const h = now.getHours()
  if (h < 5) return 'Good evening'
  if (h < 12) return 'Good morning'
  if (h < 18) return 'Good afternoon'
  return 'Good evening'
}
