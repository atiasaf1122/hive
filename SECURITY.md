# Security Policy

## Reporting a Vulnerability

If you believe you've found a security vulnerability in HIVE, please
**do not open a public GitHub issue**. Instead email the project
maintainer:

> **simondera008@gmail.com**

(Replace this with a dedicated address before a public release.)

Please include:

- A description of the vulnerability.
- Steps to reproduce or proof-of-concept code.
- The HIVE version (`hive --version` or the git SHA you're on).
- The OS, Python version, and `claude` CLI version you observed it on.

We'll acknowledge receipt within 72 hours and aim to provide a more
detailed response, including next steps and an expected timeline,
within seven days.

## Scope

HIVE is a single-user, locally-running orchestrator. The threat models
we care about, in priority order:

1. **Malicious commands from an LLM**. The orchestrator may instruct
   worker agents to run shell commands. We treat every command as
   untrusted input until `backend.security.command_policy.classify_command`
   has rated it. See [README.md → Safety](./README.md#safety--security)
   for the policy layers.
2. **Exfiltration via tool calls**. An agent could attempt to read
   credentials and `curl` them somewhere. The command policy blocks
   reads of `~/.ssh`, `~/.aws`, `~/.kube`, `~/.netrc`, AppData
   credentials. Custom rules in `Settings → Security` let users tighten
   this further.
3. **Persistence beyond the session**. Agents only ever write inside
   their per-session git worktree. The Reviewer is the only path back
   into the main branch and runs under the user's manual approval in
   any non-`full-auto` mode.
4. **Supply-chain risks**. We pin every dependency in `uv.lock` /
   `package-lock.json`. PRs that add a transitive dependency without a
   note in the description will be asked to justify it.

## Out of scope

The following are **not** considered vulnerabilities for the purpose
of this policy:

- The user pointing the `claude` CLI at a different OAuth token than
  intended (we trust the OS-level user account).
- Worker agents using their authorised file-write permissions to
  modify files inside their own worktree.
- Anthropic API or Claude CLI bugs — please report those upstream.
- The desktop shell loading a remote Vite dev server in dev mode
  (`npm run tauri:dev`). Production builds load only bundled assets.

## Coordinated disclosure

We prefer a coordinated-disclosure timeline:

1. We confirm the report within 7 days.
2. We agree a fix window — usually 30 days from confirmation, longer
   for issues that require an upstream Anthropic or library fix.
3. We release the fix and credit the reporter (if they want credit)
   in the release notes and in `SECURITY.md`'s acknowledgements
   section below.
4. After the fix ships, we publish a brief advisory in GitHub's
   security tab.

## Acknowledgements

(Empty — be the first.)
