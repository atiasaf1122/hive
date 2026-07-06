/**
 * Mirror of the backend's name→slug transform (`_slugify` in
 * backend/skills/registry.py and `_safe_slug` in backend/api/install_http.py).
 * Installed skills and MCP config entries are keyed by this slug, so the
 * Skills/Plugins pages match registry search items via slugify(item.name).
 */
export function slugify(name: string): string {
  return name.toLowerCase().replace(/[^a-z0-9-]/g, '-').replace(/^-+|-+$/g, '') || 'untitled'
}
