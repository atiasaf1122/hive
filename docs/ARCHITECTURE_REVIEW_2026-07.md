# HIVE Architecture Review & Roadmap — July 2026

**Status: LIVING DOCUMENT — original review below is unchanged; the progress
log at the end tracks what has been implemented since. A copy of this file is
auto-synced to the Windows Desktop as `HIVE_ARCHITECTURE_REVIEW_2026-07.txt`.**

Reviewed at commit `d8d4766` (658 tests passing, clean tree). Scope: full walk of
`backend/` (~20.5k LOC Python), `desktop/` (~9.5k LOC TS + 429 LOC Rust), `cli/`,
`tests/` (34 files), and all docs — by one lead reviewer plus three deep-dive passes
(backend core, backend periphery, desktop/CLI/tests/docs).

---

## Executive summary — the five sentences that matter

1. **The foundation is genuinely good**: the Worker abstraction, NDJSON stream parser,
   worktree isolation, approval correlation IDs, and WS resume are solid, disciplined
   engineering. The invariants held.
2. **The swarm is not yet a swarm**: the planner picks role/model/count only, and
   `run_workers_node` sends **every agent the identical prompt** — N agents duplicating
   one task, not a decomposed plan. This, not MCP, is the single deepest gap between
   what HIVE is and your vision.
3. **~2,000+ LOC of your best features are built but never called from the run loop**:
   the security executor, deterministic validators, tiered Haiku summarizer, hybrid
   skills search, and pattern detector exist only behind HTTP endpoints nothing calls.
   HIVE's "safety/quality stack" is roughly half decorative today.
4. **Session lifecycle has a real hole**: nothing ever marks a session `completed`,
   `agents.pid` is never written (so recovery's liveness check is dead code), and
   recovery never reconciles the sessions table itself — that's your 5 stuck sessions.
5. **The MCP execution gap is real but small**: the plug point is clean
   (`WorkerConfig` → `ClaudeCLIWorker` cmd builder), the install path already writes a
   runtime-usable `~/.claude.json`, and per-agent attachment is low-to-moderate effort.

---

## 1. Architecture assessment

### Genuinely solid (keep, don't touch)

- **Worker Protocol + unified events** (`backend/workers/base.py:33-102`) — clean,
  minimal (`run`/`kill`), `HiveEvent` normalization means the orchestrator truly never
  knows which backend ran. Invariant #1 held.
- **NDJSON stream parser** (`backend/workers/stream_parser.py`) — buffer cap +
  oversize-line recovery (:26, :77-92), EOF flush, idle timeout, non-JSON tolerance,
  and the deliberate TEXT_DELTA/TEXT_DONE dedup design (:162-169). Invariant #4 held.
  Best file in the repo.
- **Worktree manager** (`backend/worktrees/manager.py`) — per-agent isolation with
  self-healing git identity (:82-102, the "Author identity unknown" stall fix).
  Invariant #3 held.
- **Approval correlation IDs** (`backend/api/http.py:61-76,168-201`, resume
  :499-565, Telegram `notifier.py:56` + `callbacks.py:70-99`) — persist-before-await,
  double-fire guard (`events.py:188-207`), survives restarts. Invariant #5 held. The
  best-engineered periphery subsystem.
- **WS event bus + resume handshake** — ring buffer replay (`api/event_bus.py:57-68`)
  matched by `desktop/src/lib/ws.ts:54` (`resume_from: lastEventId`) with backoff
  reconnect. Real, matched on both ends.
- **MCP install config safety** (`api/install_http.py:289-311`) — backs up corrupt
  `~/.claude.json` and refuses to clobber. Writes entries the `claude` binary actually
  reads at runtime, so install is meaningful, not cosmetic.
- **Circuit breakers + hard stops + per-session overrides** — wired live in
  `graph.py:246-279` (pre-spawn) and `graph.py:734-748,838-841` (per-worker).
  Invariant #6 held at the worker layer (`stream_parser.py:143-156` maps `api_retry`
  → RATE_LIMIT; `claude_cli.py:110-116` backs off).
- **Tauri shell** (`desktop/src-tauri/src/lib.rs`, 399 lines) — small, deliberate
  (poisoned-mutex recovery :51-54), not over-engineered. Desktop component tree has
  **zero dead components** — genuinely tidy.
- **Environment detection** (`backend/detection.py:85-106,181-255`) — the WSL host-IP
  Ollama fallback and claude-binary discovery are hard-won, load-bearing code.

### Fragile / broken (fix)

- **Session lifecycle** — three compounding gaps:
  - No code path ever sets `sessions.status='completed'`. Sessions leave `active` only
    via explicit close (`graph.py:440`, `http.py:631,662,721`) or failure. Partly by
    design (a session is a long-lived conversation) but there is no `idle`/orphan
    concept, so a backend killed between turns leaves the session `active` forever.
  - `agents.pid` is **never written** (`spawner.py:80-86` passes no pid; `graph.py:835`
    updates status only) → `_pid_alive` (`recovery.py:106-112`) is dead code, and every
    restart classifies all still-`active` agents as crashed via the `pid is None`
    branch (`recovery.py:35-37`).
  - Recovery reaches sessions only *transitively through crashed agents*
    (`recovery.py:51-60`); it never scans the sessions table. Parked, agentless
    sessions are invisible to it. **This is the stuck-5-sessions bug.**
- **Silent stall**: stream idle-timeout silently `return`s (`stream_parser.py:58-63`)
  — a hung `claude` process ends the stream as if it finished; no error event reaches
  the turn.
- **Stale model map** (`claude_cli.py:174-184`): `opus→claude-opus-4-7`,
  `sonnet→claude-sonnet-4-6`. Current lineup is Opus 4.8 (`claude-opus-4-8`),
  Sonnet 5 (`claude-sonnet-5`), Fable 5 (`claude-fable-5`), Haiku 4.5. HIVE cannot
  select any current frontier model by shorthand. (Note: the `claude` CLI resolves
  aliases like `sonnet`/`opus` itself — the map may be deletable, see §4.)
- **Trust scores measure the wrong thing**: `graph.py:845-850` records
  `passed_validation = (final_status == "completed")` — the validators that would
  justify that name (`validation/validators.py`) never run. Trust currently means
  "process exited 0", not "output validated".
- **Reviewer is not a reviewer**: `review_and_merge` (`reviewer.py:40-90`) is pure
  git-merge mechanics; docstring claims "Opus in production" but no LLM is ever
  called. `summarize_results` (`reviewer.py:93`) is imported at `graph.py:37` and
  never called.
- **Phantom UI feature**: "Delete permanently" (`ProjectCard.tsx:92`) calls
  `DELETE /api/sessions/{id}` which doesn't exist; it silently removes only local
  store state while claiming "SQLite history is dropped".
- **Cross-module private coupling**: Telegram handlers mutate `backend.api.http`
  underscore-globals (`telegram/handlers/commands.py:116`, `callbacks.py:79`,
  `chat.py`) — a refactor of http.py's five module-level dicts breaks Telegram
  silently.

### Built-but-unwired (decide: wire or delete — see §5)

| Subsystem | LOC | Reachable only via | Wired into run loop? |
|---|---|---|---|
| Security executor + command policy + approval mode | 848 | `POST /api/security/approvals/*` (nothing sends) | No |
| Deterministic validators + Haiku cross-check | 400 | `POST /api/validation/cross-check` (no caller) | No |
| Tiered Haiku summarizer | 276 | `POST /api/summarizer/run` (no caller) | No |
| Hybrid skills search (BM25 + rerank) | ~260 | `GET /api/skills/search/hybrid` (UI uses the other endpoint) | No — run loop uses plain cosine (`graph.py:758`) |
| Pattern detector (stuck-loop heuristics) | 188 | nothing — zero importers | No |
| Quality monitor | 72 | nothing — zero importers | No |
| `notify_session_end` (Telegram) | ~30 | never called despite config flag | No |

Plus ~8 orphaned endpoints (validation router, summarizer, `skills/search/hybrid`,
`registries/diagnose`, `detect/backends`, `security/approvals/*`,
`sessions/{id}/approvals`) and duplicated capability surfaces (two skill-search
endpoints; trust exposed via both `validation_http` and `security_http`).

### Dead code / cruft

- **`frontend/`** — the entire Phase-5 web UI (18 components, own stores/ws/types) is
  dead. Nothing imports it; only a CORS entry (`main.py:71-72`), CLI help
  (`hive.py:672,681`), and CLAUDE.md still reference it.
- Committed build artifacts: `hive.egg-info/*`, `desktop/tsconfig.tsbuildinfo`
  (missing `.gitignore` entries).
- `_IDLE_TIMEOUT_S` computed and never used (`claude_cli.py:22`); `WorkerInput`
  TypedDict defined, never used (`state.py:26-34`); unreachable fallback branch in
  `run_workers_node` (`graph.py:310-316`); `_fetch_awesome` HEAD-request that always
  returns `[]` (`registries/mcp.py:233-242`); dead `notify_at_burn_ratio` knob
  (`overrides.py:41-52` — written, read, never enforced).
- Telemetry/crash-report Settings surface (`settings.ts:42-43`,
  `Settings.tsx:443-490`) — toggle + sample report shown via `alert()`; nothing reads
  the flag, nothing is sent. `docs/OPTIONAL_INTEGRATIONS.md:46` documents it as real.
- Version drift ×4: `pyproject.toml`=0.1.0, `main.py`=0.6.0, desktop=0.8.0,
  README/BUILD.md=0.9.0.

---

## 2. The MCP execution gap — design (no code yet)

### What exists today

- Catalog + install: `registries/mcp.py` fetches listings; `install_http.py:222-260`
  writes `mcpServers` into **global** `~/.claude.json`, which every `claude`
  subprocess reads. So today MCP is **all-or-nothing global**: install one server and
  every agent in every session carries its tools (context cost + no least-privilege).
- `ClaudeCLIWorker.run()` builds the command in one place (`claude_cli.py:45-84`) and
  already threads a tool whitelist (`--allowed-tools`, :78-84). No `--mcp-config`,
  no `--strict-mcp-config`, no `--session-id`/`--resume` anywhere.

### Design: per-agent MCP as a first-class WorkerConfig capability

**1. Extend `WorkerConfig` (base.py) — the only interface change:**

```python
class WorkerConfig(BaseModel):
    ...
    allowed_tools: list[str] | None = None          # exists
    mcp_servers: dict[str, dict] | None = None       # NEW: {name: server-spec}
```

`OllamaWorker` ignores it (documented no-op) — the Worker Protocol is unchanged, so
invariant #1 holds.

**2. `ClaudeCLIWorker` changes (claude_cli.py cmd builder):**

- When `mcp_servers` is set: write `{"mcpServers": {...}}` to an ephemeral JSON file
  in the agent's worktree (e.g. `.hive-mcp.json`, gitignored), then pass
  `--mcp-config <path> --strict-mcp-config`.
- `--strict-mcp-config` is the important half: it **excludes** the global
  `~/.claude.json` servers, so agents get exactly what the orchestrator attached and
  nothing more. Without it, per-agent routing is additive-only and the global-leak
  problem persists.
- Extend `--allowed-tools` with MCP tool names (`mcp__<server>__<tool>`, or
  `mcp__<server>` to allow a whole server) — same CSV mechanism already in place.

**3. Capability registry (new, small — `backend/registries/capabilities.py`):**

A static, local mapping from *capability* → server spec + metadata:

```python
CAPABILITIES = {
  "browser":  {"server": {"command": "npx", "args": ["-y", "@playwright/mcp@latest"]},
               "tool_count": 25, "note": "navigation, click, screenshot, snapshot"},
  "github":   {"server": {...github-mcp...}, "tool_count": ~35},
  "docs":     {"server": {...context7...},   "tool_count": 2},
}
```

`tool_count` is the budget currency. This is data, not logic — same style as
`curated.py`. Installing from the Plugins UI can *add* entries here (project-local)
instead of (or in addition to) writing the global `~/.claude.json`.

**4. Orchestrator decides attachment — extend the planner JSON schema
(`planner.py:29-60`):**

```json
{"role": "Tester", "model": "claude:sonnet", "count": 1,
 "capabilities": ["browser"], "subtask": "..."}
```

`TeamMember` (`planner.py:63-68`) gains `capabilities: list[str]`. The spawner
resolves capabilities → server specs; `_execute_worker` (`graph.py:766-773`) puts
them on `WorkerConfig`. Default is **zero MCP servers** — native tools (Read, Write,
Bash, WebFetch, WebSearch...) cover most work and cost no extra context.

**5. Tool budget (~40-50 ceiling):**

- Budget check at spawn time: `sum(tool_count for attached) + ~15 native tools ≤ 50`.
- If over budget: prefer narrowing via `--allowed-tools` (attach the server but allow
  only the 5-8 tools the subtask needs) before dropping a server. The planner prompt
  states the ceiling; the spawner enforces it deterministically (same pattern as
  `check_hard_stops`).

**6. Add/swap/drop mid-session:**

Key insight: each `run()` is already a **stateless one-shot CLI invocation** — an
agent's toolset is naturally renegotiated every spawn, and the orchestrator loop runs
per user turn. So "mid-session swap" decomposes into two cases:

- **Between turns** (free): next turn's planner emits different capabilities. Nothing
  to build.
- **Mid-run escalation** (small build): an agent that hits a capability wall ends its
  run stating what it's missing (detectable: AGENT_ERROR, or a structured
  `NEED_CAPABILITY: browser` line in TEXT_DONE — the same trick the planner JSON
  contract uses). The orchestrator re-spawns the agent with the added server. To make
  re-spawn cheap, start passing `--session-id <uuid>` on first spawn and
  `--resume <uuid>` on re-spawn so the agent keeps its conversation context instead
  of re-exploring from zero. This also fixes today's hidden limitation that every
  turn's workers start amnesiac.

**7. Vision/browser/GUI — the integrate-don't-build payoff:**

Playwright MCP gives navigation, clicking, form-fill, **screenshots** in one server —
and the `claude` CLI models are multimodal, so a screenshot tool result *is* vision.
Browser + vision + GUI interaction cost you one capability-registry entry each, zero
custom code. This is exactly your principle applied.

**Effort estimate: 3-5 focused days** including tests (the `allowed_tools` plumbing
is the working template for every layer of this).

---

## 3. Self-improvement layer — META agent design

### Principle: deterministic triggers, LLM analysis, human-gated fixes

Almost everything META needs already exists — including some of the dead code, which
this layer redeems:

| Need | Already built |
|---|---|
| Failure/event history | `events` table (append-only), `cost_log`, breaker states, trust scores |
| Stuck-loop heuristics | `safety/pattern_detector.py` (188 LOC, currently dead) |
| Scheduled execution | `backend/pipelines/` + APScheduler (fully wired) |
| Structured reporting | `summarizer/runner.py` tiered reports (currently orphaned) |
| Gated self-modification | approval modes + worktrees + Telegram approvals |

### Design

**A. Goal-first grounding (the missing "understand the project" piece).**
On first session for a project, the orchestrator runs a one-time **Brief** step
(Sonnet, read-only tools, same shape as the planner call) that writes
`GOAL.md` + `ROADMAP.md` into the project root. Every subsequent planner call
includes GOAL.md in its prompt (it already has Read access — `planner.py:149` — this
is a one-line prompt change plus the generation step). Workers receive their subtask
plus the goal excerpt. This makes "spawn a swarm that serves the goal" literal.

**B. Deterministic failure detection (free, runs always).**
Wire `pattern_detector.py` into `_execute_worker`'s event loop — it's pure-Python
heuristics (same-error repetition, file thrash, token velocity, no-progress) and
costs zero tokens. Its findings become `system/pattern` events in the event log and
can trip the existing breakers.

**C. META analysis run (LLM, runs rarely).** A pipeline (reusing
`backend/pipelines/`) triggered by **whichever comes first**:
- event-driven: ≥N agent failures or ≥M breaker trips since last META run
  (cheap SQL over `events`/`worker_trust_scores`), or
- weekly cron as backstop.

Not on a tight loop — token cost and there's nothing to learn from a quiet week. The
META session (Opus/Fable tier — this is precisely the "when the task warrants it"
case) gets: aggregated failure clusters (SQL-precomputed, not raw logs), trust-score
trends, breaker history, cost anomalies, and HIVE's own git log. It produces:
1. a failure-pattern report (via the tiered summarizer — finally wired),
2. proposed fixes as concrete tasks,
3. an updated `ROADMAP.md` for HIVE itself — your living "what are we doing and
   where are we going".

**D. Self-fixing with a hard trust boundary.** META's proposed fixes to HIVE run as a
normal HIVE session **on the HIVE repo, in `checkpoint` approval mode, in a
worktree** — you approve the team, you approve the merge. META never auto-merges
into HIVE. (Self-improving systems that can silently modify their own safety code
are how you lose a weekend. The worktree + approval machinery you already built is
exactly the right cage.)

**Effort: ~1 week**, mostly prompt/aggregation design; the infrastructure exists.

---

## 4. Token economy

Context: you're on Claude Max via OAuth — "cost" is rate-limit window burn, not
dollars, which makes *waste* (duplicate work, bloated context) matter more than
per-token price.

### Model selection per role (recommended)

| Role | Today | Recommended |
|---|---|---|
| Orchestrator/planner | Sonnet, hardcoded (`planner.py:27`) | Sonnet 5 — right call; escalate to Opus only when confidence < threshold twice in a row |
| Workers (Builder/Tester/...) | All Sonnet (hardcoded in prompt, `planner.py:36-44`) | Sonnet 5 default; **Haiku for mechanical subtasks** (rename, fixture generation, doc updates) — planner picks per subtask |
| Reviewer | No LLM at all | Keep git-merge as the mechanical path; **LLM pass (Opus) only on merge conflict or failed validation** — rare, high-value |
| Summarizer | Haiku (`haiku.py:95`) — orphaned | Haiku — wire it in (below) |
| META | — | Opus/Fable, rare scheduled runs |
| Bulk/menial (commit messages, log triage) | — | Ollama when detected — the design-for-it-anyway invariant finally pays off |

### Fix the plumbing first

- `_resolve_model` (`claude_cli.py:174-184`) is stale **and probably unnecessary** —
  the `claude` CLI resolves `sonnet`/`opus`/`haiku` aliases itself. Either delete the
  map and pass shorthands through, or keep one **central** model registry (also
  replacing the hardcoded `claude:sonnet` literals in `api/schemas.py:9`,
  `pipelines_http.py:31`, `store.py:13`, `db.py:81`, `quality_monitor.py:22`).

### Where you're currently wasteful (ranked)

1. **N identical agents** — `run_workers_node` gives every agent the same prompt
   (`graph.py:318,754`). Three Sonnet workers exploring the same repo to do the same
   task is ~3× burn for ≤1× value. Per-agent subtasks (§3A/roadmap Phase B) is the
   single biggest token win available.
2. **Amnesiac turns** — no `--session-id`/`--resume`; every turn's workers re-explore
   the project from scratch. Resume support (§2.6) converts repeated exploration
   into cached conversation.
3. **Global MCP leak** — everything installed in `~/.claude.json` loads its tool
   definitions into *every* agent's context. `--strict-mcp-config` + per-agent
   attachment (§2) fixes this categorically.
4. **Unbounded review payloads** — `review_node` concatenates all worker output into
   history (`graph.py:361-378`); the orchestrator prompt replays the last 10 turns
   verbatim (`planner.py:204`). Wire the Haiku summarizer to compact worker output
   before it enters history — this is exactly what `summarizer/runner.py` was built
   for and never used for.
5. **Uniform `max_turns=20`** for every agent regardless of subtask size — make it
   part of the planner's per-agent output (a doc-tweak subtask doesn't need 20
   turns of runway).

---

## 5. What to remove / simplify

For a single-user personal tool, honestly:

**Delete now (no debate needed):**
- `frontend/` entirely, + its CORS entry (`main.py:71-72`), + CLI help references
  (`hive.py:672,681`). It doubles the apparent frontend surface for zero value.
- `hive.egg-info/`, `desktop/tsconfig.tsbuildinfo` from git; add `.gitignore`
  entries (`*.egg-info/`, `*.tsbuildinfo`).
- Telemetry/crash-report UI (`Settings.tsx:443-490`, `settings.ts:42-43,77`) and its
  section in `docs/OPTIONAL_INTEGRATIONS.md`. It sends nothing, and never should —
  you are the only user.
- Dead code: `_IDLE_TIMEOUT_S` (`claude_cli.py:22`), `WorkerInput` (`state.py:26-34`),
  `summarize_results` import (`graph.py:37`) *if* you don't wire the summarizer (but
  see Phase B — wiring is better), `_fetch_awesome` no-op (`mcp.py:233-242`),
  unreachable fallback (`graph.py:310-316`), `notify_at_burn_ratio` column+knob.
- `SUMMARY.md` (133KB stale narrative) — replace with a short generated STATUS.md, or
  just delete; git history is the real log.

**Park / de-scope (keep files, stop investing):**
- **Packaging/auto-updater/MSI** (Phase 9D): the updater was never built (good), the
  sidecar binary dir doesn't exist, and you run this from source on your own machine.
  Keep `packaging/BUILD.md` as a someday-runbook; remove Phase 9D from active plans.
  `npm run tauri:dev` (or one local release build) is the right distribution for a
  team of one.
- `CODE_OF_CONDUCT.md` / `CONTRIBUTING.md` / `SECURITY.md` — distribution-repo
  theater for a personal tool. Harmless; delete or ignore.
- Phase-named test files (`test_phase2..7`) — don't rewrite now, but fold into
  feature-named files opportunistically as those areas change.

**Decide: wire or delete (my recommendation per item):**

| Subsystem | Recommendation |
|---|---|
| Tiered Haiku summarizer (276) | **Wire** into `review_node` — token economy + tiered reporting become real (Phase B) |
| Validators (400) | **Wire** into `_execute_worker` post-run — makes trust scores honest (Phase B) |
| Pattern detector (188) | **Wire** as META's deterministic trigger (Phase D) |
| Hybrid skills search (~260) | **Wire** — make `graph.py:758` call `hybrid_search` (it's strictly better than plain cosine and already tested); delete the duplicate endpoint |
| Quality monitor (72) | **Delete** — its job (recommend model upgrades) is subsumed by META |
| Security executor + command policy (848) | **Delete or radically reduce.** With `--dangerously-skip-permissions` the CLI never routes commands through it, and wiring a true pre-execution gate means rebuilding permission brokering the CLI already has. If you want command oversight, a ~100-LOC post-hoc scanner of `tool/use` events (flag BLOCKED-pattern matches → event + breaker trip) buys 80% of the value. Keeping 848 LOC of unexercised policy engine is your largest maintenance-burden-to-value gap. |
| Telegram (646) | **Keep if you actually use phone approvals** (it's fully wired and working); fix or delete the never-called `notify_session_end` (`notifier.py:68`). If you don't use it — park it. |

**Small fixes while in there:** phantom `DELETE /api/sessions/{id}` (add the real
endpoint — the UI promise is good), dedupe trust surface (`validation_http` vs
`security_http`), unify version to one source, fix `usage_http.py:8` docstring/query
drift.

---

## 6. What to add — MCP servers, prioritized

Applying your integrate-don't-build principle *with restraint* — the biggest mistake
here would be attaching servers that duplicate native CLI tools:

1. **Playwright MCP** (`@playwright/mcp`) — browser control + screenshots = vision +
   GUI interaction, free, one entry. Give it to Tester ("open the app, click through
   the flow, screenshot the result") and Researcher. **This alone covers most of your
   vision item #4.**
2. **GitHub MCP** — issues/PRs/reviews for agents working across repos. (Note: `gh`
   CLI via Bash covers much of this natively; attach the MCP server when you want
   structured, allowlistable operations rather than free-form shell.)
3. **Context7** — up-to-date library docs on demand; small (≈2 tools), high value for
   Builder/Researcher, cheap on the tool budget.

**Deliberately NOT recommended:** Filesystem MCP (native Read/Write/Glob/Grep are
better and free), Fetch/web MCP (native WebFetch/WebSearch exist), memory/
knowledge-graph servers (you have SQLite + event log; add later only if META proves
a need), sequential-thinking (the models think fine).

Start with those three in the capability registry, defaulted **off** per agent,
attached by the planner only when the subtask needs them.

---

## 7. Prioritized roadmap

Effort assumes Claude Code doing the work with your review; phases are sequential
but small items can interleave.

### Phase A — Fix the foundation (1-2 days) — do first, no dependencies
1. Session lifecycle: write `agents.pid` at spawn; recovery pass over the **sessions
   table** (any `active` session with no live agents and no running backend turn →
   `idle`, surfaced in UI with one-click resume/close); one-time cleanup of the 5
   stuck rows.
2. Model registry: one module mapping tiers → current IDs (Sonnet 5 / Opus 4.8 /
   Haiku 4.5 / Fable); delete stale `_resolve_model`; replace scattered
   `claude:sonnet` literals.
3. Stream stall fix: idle-timeout emits `AGENT_ERROR` instead of silent return
   (`stream_parser.py:58-63`).
4. Deletions: `frontend/`, egg-info, tsbuildinfo, telemetry UI, SUMMARY.md,
   quality_monitor, dead knobs/imports; `.gitignore`; version unification; CLAUDE.md
   refresh (this doc's findings + current phase reality).
5. Add real `DELETE /api/sessions/{id}`.

### Phase B — Make the swarm real (3-5 days) — highest quality-per-token payoff
1. Planner emits **per-agent subtask briefs** + per-agent model tier + max_turns
   (schema change in `planner.py:29-60` + `TeamMember`); `run_workers_node` passes
   each agent its own prompt.
2. `--session-id`/`--resume` support in ClaudeCLIWorker → agents keep context across
   turns and across capability re-spawns.
3. Wire the summarizer into `review_node` (compact worker output before history) and
   validators into `_execute_worker` (trust scores become honest).
4. Reviewer LLM pass (Opus) on merge-conflict/validation-failure only.
5. Switch run-loop skill search to `hybrid_search`; delete duplicate endpoint.

### Phase C — MCP execution (3-5 days) — your vision items #4 and #5
1. `WorkerConfig.mcp_servers` + ephemeral `--mcp-config` + `--strict-mcp-config` +
   MCP-aware `--allowed-tools` in ClaudeCLIWorker.
2. Capability registry with tool-count budget enforcement at spawn.
3. Planner `capabilities` field; `NEED_CAPABILITY` escalation → re-spawn with
   `--resume`.
4. Integrate Playwright, GitHub, Context7; Plugins page installs into the capability
   registry (project-local) rather than only global `~/.claude.json`.
5. Desktop: show attached servers/tools per agent in AgentDrillDown.

### Phase D — META / self-improvement (≈1 week) — vision items #1-#3
1. Project Brief step → `GOAL.md`/`ROADMAP.md`; planner prompt includes goal.
2. Wire pattern_detector into the event loop (deterministic, free).
3. META pipeline (event-count trigger + weekly cron backstop) → failure report,
   proposed fixes, living HIVE roadmap.
4. Self-fix sessions on the HIVE repo in checkpoint mode (human-gated merges).

### Phase E — Ergonomics polish (ongoing, opportunistic)
Idle-session UX, `hive doctor` (detection + stuck-state + config sanity in one
command), decouple Telegram from http.py internals, test-file consolidation.

**Sequencing rationale:** A is cheap and stops the bleeding; B is the largest
capability-per-effort win and C builds on B's planner schema; D needs B+C to have
something worth analyzing. Resist starting with C (the shiny one) — attaching
browsers to agents that all run the same undifferentiated prompt multiplies waste,
not capability.

---

## 8. Open-ended senior judgment

### 8a. Thinking / conceptual level

**The queen + swarm + event-log model is still right — but your implementation of
"swarm" has a flawed assumption baked in: that parallelism comes from *count***. The
planner outputs "3 Builders" and HIVE runs the same prompt three times in three
worktrees. Real swarm value comes from *decomposition* (different subtasks) and
*perspective diversity* (same question, different lenses, then adjudicate) — both
need per-agent briefs, neither needs high counts. Your vision has outgrown the
count-based model; Phase B is the correction, not a new architecture.

**"Event sourcing" is honestly an audit log.** Events are append-only
(invariant #2's letter) but nothing ever *replays* them — projections (session/agent
status) are updated directly, and conversation truth lives in LangGraph checkpoints.
That's fine — it's the right design for this tool — but name it honestly and don't
invest in replay machinery you'll never use. The log's real future value is as
META's dataset, which needs aggregation, not replay.

**Would I architect it differently today?** Mostly no. Shelling to the `claude` CLI
under a Max subscription is the load-bearing economic decision and it's correct; the
Worker abstraction preserves your exit. LangGraph earns its keep narrowly — checkpoint
+ interrupt (approvals, multi-turn park) is real value — but the graph is an 8-node
loop; keep it boring, and if LangGraph 2.x churn ever costs you a weekend, know that
this graph is ~200 LOC of hand-rolled state machine. The one thing I'd have done
differently from day one: per-agent task briefs (the fan-out-same-prompt design was
the wrong default) and `--resume` from the start (amnesiac turns).

### 8b. Code level

Beyond §1's list, the patterns worth naming:
- **Comment-density-as-scar-tissue** in `api/http.py` — hundreds of lines narrating
  past bugs ("snake-game stall"). Good archaeology, but it signals the runner state
  machine (5 module-level mutable dicts, in-memory Futures) is the fragility hotspot.
  It works; treat it as high-care territory and stop other modules (Telegram)
  reaching into its privates.
- **Config by scattered literal** — `claude:sonnet` in ≥6 files, two concurrency
  constants (`graph.py:61`, `spawner.py:20`) both `=3` with a comment claiming a
  cap of 7 that nothing enforces. Centralize.
- **Consistent-but-broad exception swallowing** (`# noqa: BLE001` everywhere) — each
  instance is defensible (WS emit, cost write), but collectively HIVE can degrade
  silently. META's failure detection partially compensates; still, promote the
  event-write and cost-write failures from `debug` to `warning`+counter.
- Conventions are otherwise consistent and type-hint coverage is genuinely good.

### 8c. Ways of working / operational

- **Session semantics are the biggest day-to-day wart**: "active" means "ever opened
  and never closed", the dashboard fills with stale rows, and a backend restart
  strands parked sessions. Phase A's `idle` state + reconciliation fixes the model;
  the UI then honestly answers "what is HIVE doing right now".
- **Approval ergonomics are a strength** — correlation IDs surviving restarts +
  Telegram inline approve is genuinely good ops design.
- **Two front-ends (CLI + desktop) is fine**; the CLI is your scriptable surface and
  the pipelines webhook makes HIVE automatable. Just fix the CLI's stale help.
- **Missing: one diagnostic command.** You debugged "which sessions are stuck?" with
  raw SQL today. `hive doctor` (backends detected, DB counts by status, orphaned
  worktrees, breaker states, config paths) would have answered it in one line.
- **Observability gap**: when an agent fails, the *why* is spread across events
  table, logs, and worktree state. META's failure clustering (Phase D) is also the
  fix for the human version of this problem.

### 8d. Goal alignment

**There's a drift, and it has a name: you built a product, you want a partner.**
The last few phases (registries UI, plugins marketplace, packaging, telemetry
toggles, CODE_OF_CONDUCT) are distribution-shaped — features a *user base* would
need. Meanwhile the agent core — the thing you actually want to be excellent —
still sends every worker the same prompt, never reviews with an LLM, and never
summarizes. The unwired Tier-B code is the physical evidence of the drift: quality
machinery got built to satisfy the plan, but integration (the part that serves
*you*) kept losing to the next phase's feature list. The roadmap above is
deliberately anti-drift: A/B/D are all "make what exists actually run".

### 8e. Project folder structure

**Verdict: fundamentally sound — do targeted deletes, not a reorg.** `backend/` with
17 focused sub-packages + per-feature `*_http.py` routers is discoverable;
`desktop/src` grouped by feature with zero dead components is tidy; `cli/`, `tests/`,
`packaging/` are where you'd expect.

The real issues are all *stale artifacts*, not misdesign:
1. `frontend/` vs `desktop/` — the #1 confusion for anyone (including future agents)
   reading the repo. Delete `frontend/`.
2. Committed artifacts (`hive.egg-info/`, `tsconfig.tsbuildinfo`).
3. Root-level doc sprawl: 8 markdown files where 3 are live (README, ARCHITECTURE,
   CLAUDE). Move `HIVE_BUILD_PLAN.md` → `docs/history/`, delete SUMMARY.md, delete
   the community-repo trio.
4. `tests/unit/` containing HTTP/DB integration tests under a "unit" label, half
   phase-named — mislabeled, not misplaced. Rename opportunistically.

A big-bang restructure would cost churn (imports, docs, muscle memory) for near-zero
navigational gain. Don't.

---

## Decisions I need from you before implementation

1. **Security executor (848 LOC)**: delete, or reduce to a post-hoc `tool/use`
   scanner? (My rec: reduce; delete the pre-execution broker.)
2. **Telegram**: do you actually use phone approvals? Keep-and-decouple vs park.
3. **Packaging/MSI (Phase 9D)**: confirm parking it (run from source / local build).
4. **Roadmap order**: approve A → B → C → D, or pull C (MCP) ahead of B knowing it
   multiplies the same-prompt waste?

---

# Progress log (newest first)

## 2026-07-05 — Phase B "Make the swarm real" COMPLETE

All seven sections implemented and committed (`0bc8756`..HEAD). 451 tests
passing. End-to-end proof run: "Build a small Flask todo API with tests".

- **B0 test isolation**: tests/conftest.py points HIVE_DIR at a temp dir
  before any backend import; remaining hardcoded ~/.hive paths routed
  through HIVE_DIR. Real hive.db mtime unchanged across a full suite run.
- **B1 per-agent briefs (the core fix)**: planner emits one entry per agent
  with subtask / files_hint / per-agent max_turns / model tier; legacy
  count>1 expands at parse time; run_workers builds each agent its OWN
  prompt (goal + request + subtask + file scope); skill retrieval keys off
  the subtask; approval UI shows briefs.
- **B2 context reuse**: agents.claude_session_id (ALTER-migrated) — first
  spawn --session-id, re-spawn --resume; survives restarts.
- **B3 summarizer wired**: one budgeted Haiku call per completed worker;
  history carries the compact tier, full transcript stays in events;
  outage degrades to truncated raw.
- **B4 validators wired**: summarizer's CompletionReport checked against
  real git state (new validation/context.py); passed_validation means
  "claims verified", trust scores are honest; failures emit WS events and
  annotate the summary. TestRun/PackageInstall validators deferred (their
  evidence source died with Phase A's command-audit deletion).
- **B5**: run loop uses hybrid_search (semantic+BM25+tags); orphaned
  /api/skills/search/hybrid endpoint deleted.
- **B6**: mechanical merge stays the fast path; Opus llm_review runs ONLY
  on merge conflict or validation failure, in the project dir, resolving
  aborted merges in place. summarize_results dead import removed.

**E2E proof (session 75e32173)**: planner decomposed into Builder
(app.py, "do NOT write tests — Tester owns those") + Tester (test_app.py,
"do NOT modify app.py") — distinct subtasks, disjoint files, max_turns=15
each. Both agents ran distinct prompts in parallel worktrees with distinct
persisted claude session uuids and real PIDs; both merged (2 commits);
final history carried compact Haiku summaries (raw transcripts: 13+33
events in the DB); merged repo's pytest suite: 5/5 passing.

**Dogfooding found + fixed a real bug**: validation false-negatived every
claim because collect_git_context assumed a 'main' branch while git-init
workspaces default to 'master' — and the B6 Opus escalation caught it
exactly as designed, correctly diagnosing the false negatives and passing
the turn. Root cause fixed (main/master fallback) + regression tests on
both branch names.

**Flags carried forward**: (1) conflict_resolvers.py (468 LOC heuristics)
is still unwired — decide in Phase C/D whether llm_review should try it
first or it should be deleted; (2) trust table absorbed 2 false-negative
validation failures from the e2e run (cosmetic); (3) Phase C next: MCP
execution (per-agent --mcp-config + capability registry + tool budget).

## 2026-07-05 — Phase A "Fix the foundation" COMPLETE

All six sections implemented, tested, committed (`d0a6b61`..`a476836`), pushed
to origin/main. 428 tests passing; desktop typechecks clean; verified
end-to-end against a live backend (session create → real orchestrator reply →
close → hard delete → 404).

- **A1 session lifecycle**: `agents.pid` now written at spawn (stamped on
  AGENT_START); new `idle` status + idempotent `reconcile_idle_sessions()` at
  startup — the stuck-'active' sessions bug is fixed and the live DB was
  reconciled (0 stray active rows). Discovered + fixed a deeper gap: no
  runner-reattach path existed after restart, so added
  `POST /sessions/{id}/resume` and auto-resume on `/message`. Crashed-agent
  sessions now go `idle` (resumable), not `failed`.
- **A2 model registry**: `backend/models.py` — opus→claude-opus-4-8,
  sonnet→claude-sonnet-5, haiku→claude-haiku-4-5-20251001 (no Fable, being
  removed from subscription). Verified the claude CLI resolves tier aliases
  itself → aliases pass through, no dated-ID pinning. Stale `_resolve_model`
  deleted; a test greps the source for retired IDs.
- **A3 silent stall**: idle-timeout now yields AGENT_ERROR *and* the worker
  kills the hung process group + skips AGENT_END (the review missed that
  `proc.wait()` would hang one layer up). Breakers count the failure.
- **A4 deletions**: security stack (848 LOC + router + UI + audit schema),
  `frontend/`, quality_monitor, telemetry UI, `notify_at_burn_ratio`,
  `_fetch_awesome`, `WorkerInput`, build artifacts, community-repo docs,
  SUMMARY.md. Telegram parked (kept in-tree, not started, live path clean).
  No command scanner replacement — worktree is the containment until a real
  sandbox lands.
- **A5**: real `DELETE /api/sessions/{id}` (rows + checkpoints + worktrees,
  cancels live runner); UI only removes the card on backend success.
- **A6**: version 0.9.0 everywhere (pyproject is the source, /health reads
  metadata); CLAUDE.md rewritten to current reality; README honesty pass;
  packaging/9D parked.

Test delta 678 → 428: −255 tested deleted dead code, +25 new coverage.

**Flags carried forward**: (1) tests write to the real `~/.hive/hive.db` —
isolate via HIVE_DIR early in Phase B; (2) Phase B is next: per-agent subtask
briefs, `--resume` context reuse, wire summarizer/validators/hybrid search,
conflict-only LLM review.
