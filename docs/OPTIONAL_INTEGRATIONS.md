# Optional integrations

HIVE works fully offline + with a Claude subscription. The integrations
on this page are entirely optional — wire them up only if the feature
they enable matters to you.

## GitHub Personal Access Token (recommended for Skills + Plugins tabs)

The Skills and Plugins tabs hit `api.github.com` to enumerate the
Anthropic Cookbook + the `topic:claude-skill` community registry +
the `awesome-mcp-servers` README. Without a token, GitHub's
unauthenticated rate limit is **60 requests per hour** — easy to
exhaust if you browse Skills for a few minutes.

To lift the limit to 5 000 / hour:

1. Create a PAT at <https://github.com/settings/tokens?type=beta>.
   The token only needs **public read** scope — no repo write, no
   admin. Skills + Plugins both call public GitHub APIs only.
2. Set the env var **before** you run the backend:

   ```bash
   # Linux / WSL / macOS
   export GITHUB_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
   hive start
   ```

   ```powershell
   # Windows PowerShell
   setx GITHUB_TOKEN "ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
   # Then open a NEW PowerShell window for the env var to take effect.
   ```

The token is read once at request time by `backend/registries/skills.py`
and `backend/registries/mcp.py`. It is **never** written to disk by
HIVE and never persisted in any DB row. Rotate it on GitHub and your
next backend restart picks up the change.

If the token is invalid, the affected fetchers fall back to the
offline curated lists with `fallback: true` — the UI shows the
"showing offline cache" banner. Look at the backend stderr or hit
`GET /api/registries/diagnose` from the browser — that returns the
exact per-source error string.

## Telegram bot (already documented elsewhere)

See `README.md` → Daily Use → Telegram, and the on-screen onboarding
wizard (Settings → Account → Re-run onboarding).
