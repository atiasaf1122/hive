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

## 2026-07-07 — Post-1.0 session: 2 live-bug clusters fixed + 4 upgrades (Parts 1–6)

Six parts from real first-use findings. Tests 623 → **655 passing**. Golden
suite at close-out: **9/10, then 10/10** — lessons-injection failed once
(its claude:haiku Writer died at spawn with ZERO emitted events and zero
cost; no instant-fail path matches, breaker was fresh, classifier/plan were
correct) and passed cleanly on re-run — this time the planner put the
Writer on **ollama:gemma4:latest**, a model pulled after the resolver
shipped, classified live by the new inference rules and listed by the
audition nudge alongside the other two locals. Flag carried forward: a
worker that dies with an EMPTY event stream records no cause anywhere —
add a terminal diagnostic (exit code / stderr tail) when zero events were
emitted. Six commits (one per part + close-out).

- **Part 1 — chat duplication BUG: the layer was the WS TRANSPORT.**
  Reproduced with 3 keyword turns, then captured live WS frames. Persisted
  history and the model's replies were clean (layers a+b exonerated); the
  frontend sends `resume_from: 0` on every ProjectView mount and the WS
  endpoint treated 0 as "replay the ENTIRE ring", re-appending every prior
  orchestrator_response on top of the fetched /history — plus stale queue
  backlog (turns finished with no client attached) delivered on top.
  Affects ALL session shapes, not just CHAT. Fix in ws.py: resume 0/absent
  = fresh client → live-only + backlog skipped; resume>0 within ring =
  replay exactly the gap once; stale ids from a dead process = fresh.
  Verified live: 5 turns of backlog, fresh mount receives only the new
  reply. 3 regression tests (test_ws_resume.py).
- **Part 3 — 49641e2b forensics corrected the report**: "what can i do
  with hive?" was correctly CHAT (event 12111) and answered; the SOLO
  Writer came from the FOLLOW-UP ("give me a small prompt…"), a
  text-deliverable request. Rubric now routes capability questions and
  text-deliverables (prompts/advice/plans, even imperative) to CHAT; solo
  requires an explicit on-disk artifact. Solo wrap-ups stop repeating the
  "On it — routing…" line as if it answered ("Here's what the agent
  produced this turn:"). Stuck spinner = stale pre-sync frontend missing
  the awaiting_user planner-log clear + the Part-1 replay bug; solo
  completion emitting orchestrator_response + awaiting_user is now pinned
  by tests.
- **Part 2 — three-layer local-model resolver** (models_local.py):
  /api/show metadata cached per (model, digest) in SQLite (probe once per
  VERSION); inference rules in one table (coder families + size tiers —
  fictional "qwen5-coder:40b" resolves full-coding by pattern+size;
  unknown family stays conservative); `hive models audition <model>` runs
  fixed $0 micro-tasks (median function w/ real pytest, summary graded
  0-10 by Haiku, 5-way classification) storing MEASURED caps that
  override inference. Planner digest marks [measured|inferred|default]
  and prefers measured. New digests emit MODEL_DISCOVERED + GET
  /api/models/local/nudge (informational, no auto-runs).
- **Part 4 — local skills library**: `hive skills sync` bulk-downloaded
  the discoverable universe into ~/.hive/skills/<family>/<slug>/ —
  **110 skills, 524 KB, 0 failures** (frontend=29 backend=27 devops=21
  misc=10 ai-agents=9 data=9 docs-writing=5; 94 synthesized from metadata,
  flagged). Online sources consulted ONLY during sync. Three root fixes
  from the bulk run: synthesized frontmatter now JSON-quoted (": " broke
  YAML for ~40), semver versions parse leniently, cookbook fetcher follows
  GitHub's 301 (source restored). Skills page = local-library browser
  (search + family filter + Sync button).
- **Part 5 — curated MCP catalog: 4 → 6 (approved: "7 minus sqlite")**.
  Added postgres (official read-only reference, POSTGRES_URL preflight)
  and youtube-transcript (@kimtaeyoon83, npx, no key). Rejected with
  reasons: search/fetch MCPs duplicate workers' built-in
  WebSearch/WebFetch; git/sqlite ride on Bash; memory duplicates lessons;
  whisper/piper/comfyui have no vetted runnable packages (some need
  OPENAI_API_KEY — stack lock); comms servers aren't swarm equipment.
  Plugins page: primary tab = Swarm catalog with live preflight,
  registry browser demoted to "Discover more".
- **Part 6 — the X performs the hermetic shutdown**: POST
  /api/lifecycle/shutdown (workers via stop-hive-wsl.sh --workers-only —
  kill pattern lives in ONE place — then graceful self-exit);
  CloseConfirmation calls it on all closing paths except close-to-tray;
  mid-work confirm dialog kept. `wsl --shutdown` stays exclusive to the
  Stop script, now reframed as the fallback/repair tool (.lnk description
  updated). Two bugs found live: **confirm_close used window.close(),
  which re-enters the prevent+emit handler — an infinite loop; the X had
  NEVER actually closed the app** (now window.destroy()); and the worker
  kill pattern matched processes that merely mention it (both killers now
  skip cmdlines containing a literal ".*"). Verified live: X → backend
  gone in 4s, app + dev tooling gone, zero orphans, interactive claude
  untouched. Daily flow: launch icon to start, X to stop.

## 2026-07-07 — Q&A audit follow-ups: Plugins discovery-only, installed-list endpoints, Windows copy synced

All four recommendations from the audit entry below are now DONE:

- **Plugins page → discovery-only**: blurb now says what the page is (a
  browser for MCP servers; adding one equips YOUR interactive claude CLI via
  ~/.claude.json — HIVE agents get equipment from the curated catalog, per
  invariant). "Install" relabeled **"Add to CLI"** (card button, badge,
  PermissionDialog copy all updated); placeholder Models tab (parked 9D)
  removed.
- **Installed-list endpoints shipped** (the "thin endpoint in 9D" that never
  did): `GET /api/registries/skills/installed` (registry via `list_skills`)
  and `GET /api/registries/mcp/installed` (reads mcpServers from the Claude
  config). Both pages hydrate `installedIds` on mount, so Installed views
  survive reloads. IDs are name slugs — the same `_slugify`/`_safe_slug`
  transform both install paths use; new `desktop/src/lib/slug.ts` mirrors it
  and pages match search items by `slugify(item.name)`. Install handlers now
  use the server-returned `skill_id`/`config_key` instead of the search id.
- **Windows desktop copy synced** from repo `desktop/` via
  `robocopy /MIR /XD node_modules target dist` (40 files copied, 6 stale
  extras purged — SecurityPanel/AuditLogViewer among them). Verified: src
  trees byte-identical, no "4.7" anywhere (QuickStart shows Opus 4.8 /
  Sonnet 5), TrajectoryView/LessonsPanel/AgentDrillDown/ErrorBoundary
  present, `tsc --noEmit` clean on BOTH sides, backend restarted (new
  endpoints probed live), app launched and rendered.
- Tests: 620 → **623 passing** (3 new endpoint tests, monkeypatched config
  path / registry — no real ~/.claude.json reads).

## 2026-07-07 — Q&A audit: Plugins & Skills pages, model labels, stale Windows desktop copy

Four questions asked and answered in a working session (no code changes yet —
findings + recommendations recorded here for follow-up).

**Q: Is the Plugins page needed / functional?** Half-functional, and its
install path is disconnected from the live pipeline:
- Discover works — live probe returned 31 items, but both remote sources
  (official, smithery) were unreachable → offline curated cache + amber banner.
- **Install is misleading**: it writes `mcpServers` into `~/.claude.json`,
  which HIVE agents never read — workers run `--mcp-config <per-agent file>
  --strict-mcp-config` (claude_cli.py), so agent MCP equipment comes solely
  from the curated code catalog (`backend/mcp/catalog.py`). The button
  actually configures the user's *interactive* claude CLI, while the page
  blurb claims it extends "the orchestrator and its agents".
- Installed tab is amnesiac (`installedIds` is in-memory React state, no
  backend list endpoint; the DELETE/uninstall endpoint exists but no UI calls
  it). Models tab is a placeholder referencing parked Phase 9D.
- **Verdict: not needed as-is** — keep as discovery-only browser (fix blurb,
  relabel Install) or delete the page.

**Q: Is the Skills page needed / functional?** Yes on both counts:
- Search works live (30 results; clawhub + cookbook down, GitHub community
  source carried it).
- Install is real: fetches/synthesizes SKILL.md → `~/.hive/skills/<slug>/` →
  `import_skill` → the SAME registry whose `hybrid_search` the orchestrator
  (graph.py) uses to inject top-3 skills into agent briefs. Solid SSRF
  hardening on the fetch path.
- Same in-memory installed-list flaw as Plugins (the "thin endpoint in 9D"
  promised in a code comment never shipped — 9D was parked).
- **Verdict: keep** — it's the only UI for growing the skills registry.
  Worthwhile fix for both pages: a small GET endpoint listing installed
  skills / configured MCP servers so Installed views survive reloads.

**Q: Why does the composer say "Claude Opus 4.7" — where are 4.8/Sonnet 5?**
The running desktop app is a **stale fork**: `C:\Users\The One\hive-desktop`
has drifted from the repo's `desktop/` — old "Opus 4.7" labels, still ships
Phase-A-deleted components (SecurityPanel, AuditLogViewer → dead endpoints,
those screens 404), missing everything newer (ErrorBoundary, TrajectoryView,
LessonsPanel, AgentDrillDown…). Cosmetically wrong only for models: the UI
sends `claude:opus` and the CLI resolves the alias to the latest Opus, so
swarms already run Opus 4.8 / Sonnet 5 / Haiku 4.5 (current lineup;
`backend/models.py` is correct). **Fix: sync the Windows copy from the repo's
`desktop/`** — pending user go-ahead.

**Q: Why "400 project_path does not exist: /mnt/c/.../hive test/New folder"?**
Correct validation, stale target: the Windows→WSL path translation worked,
but the folder was renamed to `1` after the picker grabbed it as "New
folder". Re-picking the folder under its current name resolves it.

## 2026-07-07 — One-click desktop launcher (Windows shortcuts)

QoL for daily use: double-clickable **HIVE** / **Stop HIVE** shortcuts on the
Windows desktop, backed by four committed scripts in `scripts/`.

- `launch-hive.ps1` — TCP-probes 127.0.0.1:8765; if down, starts the backend
  detached in WSL via `start-hive-backend.sh`, waits ≤30s for the port
  (window stays open on failure with the backend log tail), then launches the
  desktop app: prefers `src-tauri\target\release\hive.exe` if a release build
  ever exists, else `npm run tauri:dev` in `C:\Users\The One\hive-desktop`
  hidden (log: `%TEMP%\hive-tauri-dev.log`).
- `start-hive-backend.sh` — setsid double-detach + 1s grace. Found the hard
  way: `wsl.exe -- bash -lc "nohup … &"` silently does nothing — bash exits
  instantly and WSL tears the session down before the backgrounded subshell
  runs; inline commands through wsl.exe also get inner quotes mangled. Fix:
  keep logic in a WSL-side script, detach with setsid, keep wsl.exe alive 1s.
- `stop-hive.ps1` + `stop-hive-wsl.sh` — kills hive.exe + tauri dev tooling
  (matched by `hive-desktop|hive-tauri-dev` in command lines), the uvicorn
  backend, and orphaned workers (narrow pattern: `--output-format
  stream-json.*--dangerously-skip-permissions` — interactive claude sessions
  never match). Reports everything killed; offers `wsl --shutdown` (y/N) only
  when no user processes remain (systemd --user infra excluded).
- Tested live end-to-end: cold launch → backend up ~1s, app up ~40s (dev
  mode) → CHAT session answered "ready" with zero spawns → stop killed
  app + 4 dev processes + backend, spared the interactive claude session.
- **Release build deliberately skipped**: `tauri:build` uses
  tauri.conf.prod.json which needs the `binaries/hive-backend` sidecar
  (packaging is PARKED); a default-config build would bake the frontend and
  go stale on every UI change. Launcher auto-prefers a release exe if one
  appears later — zero changes needed then.
- Noticed + fixed: `/health` reported version **0.9.0** (stale venv install
  metadata) — `uv pip install -e .` refreshed it; `/health` now says 1.0.0.

## 2026-07-06 — Phase G COMPLETE — HIVE graduates to v1.0.0

The final close-out: every item on F's "honest next" list cleared, the
golden suite deterministic, version 1.0.0 tagged. **620 tests passing**
(613 → 620), 6 commits. The build (phases A→G) is complete; the system
enters real use.

- **G1 — needs_tools as a first-class classifier field** (F's live failure
  mode). The 8B twice routed tool-reliant tasks to tool-less local
  workers; F5's two-keyword band-aid is replaced by a real judgment: the
  classifier now emits `needs_tools` alongside shape, and needs_tools=true
  never routes local (browser-shaped solos get the playwright MCP). The
  keyword scan is demoted to a validation backstop — a disagreement logs
  CLASSIFIER_DISAGREEMENT (measuring the 8B, not silently overriding) and
  routes to the safer Claude verdict. The palette misroute now routes by
  field, unit-covered.

- **G2 — live multi-agent salvage proof** (F's #1). Drove a real 2-agent
  session (`g2aea7f8`): a Builder that succeeded plus a Writer engineered
  to hit error_max_turns AFTER committing a 119-line NOTES.md. Full path
  fired live — Writer failed → its branch had commits → SALVAGE_REVIEW
  event → Opus verdict `merge` ("self-contained, accurate, useful as-is")
  → merged into main through merge_to_main → salvage Opus cost logged
  ($0.0511) → session completed with both files on main. Pre-F3 that
  NOTES.md was silently dropped; now it's rescued.

- **G3 — flask-todo de-flaked at the root.** The ~⅓ failure was a
  coordination gap, not variance. Two fixes: (a) a top-level `contract`
  field on the plan (concrete routes/methods/status/shapes) injected
  VERBATIM into every teammate's prompt as a BINDING interface, so
  producer and consumer build to the same spec instead of guessing; (b)
  the 3× re-run exposed the deeper cause — the 8B sometimes classified
  "API AND tests" as SOLO-local, one coder guessing both halves — so the
  SWARM rubric now routes any must-agree-deliverables task (API+tests,
  module+consumer, impl+doc) to swarm, never solo. Result: **3/3 in
  isolation**, PASS in the graduation run. The golden suite has no
  known-flaky specs left.

- **G4 — the unexercised, now measured** (F's #4). Two new golden specs:
  - **local-multifile-refactor**: rename a function across 4 coupled
    files + tests. The local qwen3-coder:30b did it at **$0.00 in 42s**
    (pytest green, no old-name references) — F's "OllamaWorker on
    multi-file refactors, unproven" answered: it works, for free.
  - **three-wave-chain**: data module → CLI → e2e test. Passed; the
    planner correctly MERGED the two same-role build steps and
    resequenced the Tester to wave 1 — both PLAN_ADJUSTED kinds fired
    live. Since realistic tasks collapse to 2 waves, >2-wave EXECUTION
    ordering is proven directly in a unit test (strict wave 0→1→2; a
    mid-wave fail-fast doesn't block later waves).
  - Both passed first try, so no failures to distill — the lessons store
    stays n=2. It grows from real use, not the golden suite (which
    doesn't trigger the close-path distiller).

- **G5 — graduation.** Full 10-spec golden on hybrid: **10/10 · $2.62 ·
  ~25 min** — a perfect run, no regressions on the original 8, both new
  specs pass. (The $2.62 counts planner cost F0.1 made visible; pre-F
  totals understated by ~⅓.) CLAUDE.md refreshed: per-phase
  contributions, the containment model (worktree + guard, no OS sandbox),
  the full routing rules (shape + needs_tools + local guidance), and an
  "Operating HIVE" section. Version bumped to **1.0.0** across
  pyproject/package.json/tauri.conf/Cargo; tagged **v1.0.0**.

### Where the E6/F "unproven" list stands now
- VRAM fallback — PROVEN (F0.4). RAM fallback — PROVEN (F0.4).
- Live multi-agent salvage — PROVEN (G2).
- OllamaWorker on multi-file refactors — PROVEN, $0 (G4).
- Multi-wave >2 execution — PROVEN in unit (G4); realistic plans collapse
  to 2 waves by correct merging.
- Lessons at scale — still n=2, the one honest open item. Real use is the
  only thing that grows it; the machinery (write/gate/retrieve/hygiene/
  nudge) is proven, the volume isn't there yet.

### Closing note
Build phases A through G are complete. A fixed the foundation, B made the
swarm real, C gave it hands, D gave it memory, E made it economical, F
hardened it, G cleared the board. The system's failure modes are now
variance-shaped (model nondeterminism), not architecture-shaped; the
golden suite catches regressions; the learning loop is wired end-to-end
and waiting only for real material. **HIVE 1.0.0 enters real use.**

The one caveat the first real-use sessions should carry: the lessons
store is empty of real experience (n=2, both from synthetic proofs).
Watch whether retrieval at the 0.35 bar injects usefully or not at all as
it fills — that threshold was measured against synthetic lessons and may
want retuning once real ones accumulate.

## 2026-07-06 — Phase F "Hardening" COMPLETE

The safety/accounting/salvage/coordination pass — took E6's own ranked
"next" list, merged the hooks research finding, and cleared the flagged
cruft. **609 tests passing** (602 → 609 across the phase; started at 557),
7 commits (`<F0>`..`<F5>`). Backend 15.4k LOC (pattern_detector's 490 out,
guard + salvage + resources in).

- **F0 — accounting closed, cruft cleared, resource net proven.**
  - F0.1: the planner's cost was the single biggest per-session spend NOT
    in cost_log — every prior economics number understated. Now logged
    (role prefix `planner-`), along with the chat route and task-shape
    classifier (whose Haiku fallback had been silently FK-failing its
    writes against a fake session id). Measured live: in the final golden
    run the planner was **$0.34 of $1.59** — a third of spend that was
    dark. Full audit: every model call site (gate, distiller, summarizer,
    llm_review, META, salvage) verified logging.
  - F0.2: GET /api/cost/session/{id} role breakdown + a click-through cost
    popover on the AgentsBar (planner / gate / workers / summarizers /
    review / meta, 🏠 $0 local rows, saved-via-local line).
  - F0.3: pattern_detector.py DELETED (490 LOC + 7 tests). Decision,
    documented: its ActivityWindow inputs modelled live-session stalls
    (file-thrash, no-progress) that nothing ever assembled, NOT the
    cross-session failure clustering a META nudge needs — only ~20 lines
    would have mapped over. Replaced by GET /api/meta/nudge (~40 lines:
    ≥3 same-class failures in 24h → amber "run META?" badge in
    Settings→Lessons; guard/tripped included so denials cluster). No
    schedule, no auto-run — this also settles E6's #4 (scheduled META):
    for one user, nudge-on-signal beats a cron. Empty backend/security/
    husk removed.
  - F0.4: a RAM floor (psutil, 4GB default, HIVE_MIN_FREE_RAM_GB) before a
    local model load — the 32GB box runs backend + Tauri + CLI workers +
    sometimes ComfyUI, and RAM is the likelier bottleneck than VRAM. Same
    fallback path as VRAM, reason in the model/fallback event. NO disk
    checks (by decision — the user pulls models manually). **Both
    fallbacks proven LIVE** (E6 flagged VRAM fallback had never fired):
    forced RAM floor → ram_pressure event → worker fell to haiku → task
    completed; a saturated VRAM ledger → the VRAM refusal path. Two real
    bugs surfaced by the live proof: (1) a Solo override skipped
    classification, so mechanical stayed False and overridden solos never
    routed local; (2) resident models were double-counted against VRAM
    headroom — /api/ps residency now wired (a loaded model needs no new
    headroom). So E6's "VRAM fallback unproven" is now closed.

- **F1 — PreToolUse guard hook, the deterministic tripwire.** Research
  finding (verified against the current hooks reference + LIVE):
  **PreToolUse hooks fire before the permission-mode check, and a `deny`
  blocks the tool even under `--dangerously-skip-permissions`** — hooks
  can only tighten. So a ~200-line stdlib script
  (backend/guard/pretooluse_guard.py) gives the catastrophic-command net
  the deleted Phase-A executor never could (the CLI never routed through
  it). Denies a SHORT list only: rm -rf outside the worktree/~/protected
  paths, credential reads (~/.ssh ~/.aws *.pem ~/.claude/credentials
  .git-credentials), fork bombs, mkfs/dd-to-device. Everything else
  allowed. Injected per Claude worker via a worktree .claude/settings.json
  (Bash matcher, `.claude/.gitignore '*'` so auto-commit can't sweep it).
  Denials → GUARD_TRIPPED events (origin=agent). **Proven end-to-end**: a
  worker under --dangerously-skip-permissions attempted `ls ~/.ssh/`, the
  guard denied it, the worker was told why and recorded the reason, the
  event fired, the session continued. Overhead ~24ms per Bash call (Python
  startup) — negligible; the golden wall time is unchanged. **bwrap/
  container drops off the backlog** — containment is now worktree
  isolation + this guard (CLAUDE.md invariant #3 updated). Local workers
  have no Bash tool, so the guard is Claude-scoped by construction.

- **F2 — Stop/SubagentStop push signals.** The same worktree settings
  register Stop hooks that append a signal line the instant a turn ends;
  a watcher reaps a process still streaming 15s past its Stop signal (hung
  with a dead turn). Idempotent with A1's pid-death polling — a signal for
  a finished stream is a no-op.

- **F3 — salvage review (E6 #2).** A failed agent's committed branch is no
  longer silently dropped (the palette Tester died at the finish line in
  D). Opus judges merge-vs-discard over the branch diff; a merge goes
  through the normal merge_to_main path. Cost-guarded: ≥1 commit AND >5
  changed lines, or it never pays for Opus. SALVAGE_REVIEW events; the
  call is cost-logged.

- **F4 — producer/consumer runtime net (E6 #3).** F4.2: D4's overlap
  resolver now emits a PLAN_ADJUSTED event when it resequences a
  produce/consume dependency into a later wave (was a silent log line).
  F4.1: before a wave>0 agent runs, its declared consumed inputs (files a
  lower-wave agent produces) must exist on a producer branch — a missing
  one fails the spawn instantly with a named-file event naming the likely
  producer, instead of the 20-minute poll E6 hit. A file only this agent
  lists is its own output, never a consumed input.

- **F5 — regression proof + close-out.** Hybrid golden vs the E5 baseline:

  | | E5 hybrid | F hybrid |
  |---|---|---|
  | passed | 6/8 | **7/8** |
  | total cost | $0.88 | $1.59 |
  | wall | ~8.8 min | ~13 min |

  The cost rise is honest, not a regression: **$0.34 is the planner cost
  E5 never counted** (F0.1) and palette-playwright is back on Claude
  ($0.54) because F5 correctly stops routing browser tasks to a
  tool-less local worker. Real worker spend was $0.85; summarizers ran
  local at $0; saved-via-local ~$0.05 on this small batch. The guard hook
  + lifecycle signals cost ~nothing (wall unchanged within variance).
  The lone failure is flask-todo-api — the documented flaky coordination
  canary (generated-test logic mismatch, ~⅓ fail rate), not F-caused.

  Two real routing gaps the F golden run surfaced and fixed (F5): the SOLO
  path routed browser-verification and multi-part tasks to a local worker
  (local has no tool loop) — now a tool-reliant keyword guard keeps them
  on Claude; and a tool-reliant solo starved at 12 turns (the E0.3
  MCP-turn lesson again) — now 28.

### What E6's "unproven" list looks like after F

- **VRAM fallback**: now PROVEN live (F0.4) — plus a resident-model
  double-count bug fixed and a RAM sibling added.
- **multi-wave >2 / OllamaWorker on multi-file refactors**: still only
  exercised at wave-1 and single-file/creation depth — F4 hardened the
  wave machinery but didn't stress it past 2 waves.
- **lessons at scale**: still n=2 stored; the 0.35 retrieval bar and
  3-strike archive remain unexercised at volume.

### Honest next, ranked

1. **Salvage/guard/nudge have unit + (guard) live proof but no
   multi-agent live salvage yet** — drive one real session where a
   mid-team agent fails with committed work and watch salvage merge it
   end-to-end (small).
2. **Classifier misroutes are the live failure mode now** — the 8B
   sent browser/coordination tasks to solo-local twice in one golden run.
   F5 patched the two known keywords; a cleaner fix is to let the
   classifier see "needs tools" as a first-class output field (medium).
3. **flask-todo canary**: either accept it as intentional variance signal
   or split it into a deterministic single-agent spec (small).
4. **Multi-wave >2 and local multi-file refactors** still need a
   deliberate stress spec (medium).
5. **A real OS sandbox is no longer planned** — the guard + worktree
   model is the containment story. Revisit only if the guard's
   catastrophic list proves insufficient in real use (watch GUARD_TRIPPED
   clusters).

## 2026-07-06 — Phase E COMPLETE + full-project review

Phase E in one line: the loops D left open are now closed and proven, the
hybrid brain the project was designed around is real and measured, and the
whole system has been verified end-to-end. **557 tests passing** (510 → 557),
14 commits (`c6992dc`..`c5ef999` + this one), backend 14.8k LOC / desktop
9.3k LOC.

### E0 — Phase D loose ends, all closed with live proof

- **E0.1 learning loop CLOSED.** Audit trail first: every distillation
  attempt now emits exactly one of lesson/stored | lesson/discarded |
  lesson/none (crash included) — no silent path can exist. The instrumented
  conflict re-run answered the D ambiguity: it was a LEGITIMATE 'NONE' (the
  distiller refused to treat a resolved conflict as a failure). Three
  deliberate, measured loosenings followed: distill prompt (a resolved
  conflict with named collision IS learnable), gate prompt (advice may
  generalize from the evidenced mechanism; facts may not), retrieval
  (max-over-views at 0.35 — measured: the old 0.55 bar was UNREACHABLE for
  realistic queries; retrieval was off, not conservative). Then the full
  loop ran live: conflict → Opus resolution → lesson stored (gate 10/10) →
  similar task → injected → clean run → hygiene CONFIRMED (applied=1,
  confirmed=1). A second lesson later stored itself organically
  ("F-string escaping in generated code", gate 9). Current stats:
  2 stored / 1 discarded / 1 none — complete trail.
- **E0.2 + E0.4 cost accounting completed**: Opus llm_review ($0.18–0.37
  observed) and META's own Opus call now land in cost_log. Known remaining
  gap: the PLANNER's Sonnet call has never been cost-logged — flagged below.
- **E0.3 golden baseline**: 7/8 · $2.51 · ~16 min (golden-20260706-181620).
  The first real run found five real bugs, all fixed: planner max_turns=1
  killed by its own first tool call (it had been dying in EVERY session that
  planned with file context — the D2 gate revision loop was silently
  rescuing it); worker turn-budget starvation (floor 10 now); untracked
  fixture files → phantom merge conflicts costing an Opus review each; a
  worktree-creation race that SILENTLY DROPPED whole agents (the flask-todo
  Builder — now a per-project lock + a visible infrastructure event); an
  unachievable spec criterion. flask-todo stays flaky (~⅔) as an honest
  coordination canary.
- **E0.4 META baseline**: ran live after fixing its own turn budget (same
  bug class as the planner). $0.34/run (3.1k in / 6.4k out, Opus).
  Sanity-checked: its failure clusters, cost outliers, and golden numbers
  all match reality; recommendations are reasonable and its own caveat
  ("≤37 sessions — nothing is statistically solid") is correct.
- **E0.5 compaction verified live**: with the env knob at 3 turns, the
  compaction event fired, the state doc carried all three seeded facts,
  pruned turns were preserved in the event, and the post-compaction turn
  answered from the compacted state ("BLUEBIRD"). Found+fixed on the way:
  the E3 chat route had full tool access and a 2-turn cap — it died on
  tools and silently fell back to the planner every time.

### E1–E5 — the hybrid brain (measured, not promised)

- **E1**: local pool discovery (qwen3-coder:30b + qwen3:8b pulled onto the
  2×3090 box; ~24GB), curated capability map, minimal VRAM manager
  (nvidia-smi + reservation ledger + 85% guard — NOTE: the prompt's premise
  that a ResourceManager already existed was wrong; it was built this phase),
  GET /api/models/local, Claude-only degradation intact.
- **E2**: `local:<model>` tiers in briefs; OllamaWorker gained a
  file-block harness (write-only + think-strip + traversal guard) so local
  workers ride the same auto-commit→validation→merge pipeline; per-turn
  availability digest + routing guidance in the planner; VRAM-aware spawn
  with declared fallback tier + model/fallback event; 🏠 $0 chips in the UI.
- **E3**: SOLO/SWARM/CHAT task-shape router, classified BY the local 8B
  (dogfooding), visible reasoning, composer override, task/shape events.
  Observed decisions so far: 4 solo + 1 swarm classified locally, 0
  misroutes. Solo skips planner+gate; chat answers in-session.
- **E4**: summarization + distillation behind internal_task_caller
  (local-first, Haiku fallback, hard timeout). HONEST quality check on real
  inputs (docs/E4_LOCAL_QUALITY_COMPARISON.md): summarization local by
  default (accurate, slightly thinner evidence); **distillation stays on
  Haiku** — qwen3:8b invented an unevidenced mechanism, and a local
  distiller would run the groundedness gate on the model that confabulated.
- **E5 economics, measured**: baseline 7/8 · $2.51 · ~16 min → hybrid
  6/8 · $0.88 · ~8.8 min (**cost −65%, wall −45%**), and both hybrid
  failures were diagnosed as non-local (a claude CLI stdin flake — re-run
  passed at $0.00 with the local coder writing the whole snake game; and a
  spec-fixture bug the now-sighted planner correctly refused — fixed with
  the golden `git_branch` field). flask-todo, the baseline's flaky canary,
  passed hybrid at $0.05: one local worker, no coordination to flake.
  cost_log has a local flag; Usage shows "saved via local" at Haiku pricing.

### E6 — full verification

- Code spot-checks of every A–D progress-log claim passed (lifecycle
  recovery, hard delete, no-retired-IDs test, --resume, hybrid skills
  search, llm_review only on conflict/validation-failure, per-agent
  --mcp-config --strict, preflight, origin-filtered trust, trajectory
  endpoint, META gate on accept-lesson, Stop-hook Desktop sync).
- **Mixed live session** (swarm override): Sonnet Builder + Sonnet Tester
  with playwright MCP + Writer, with ALL THREE summarizers running local at
  $0. Deliverables real (index.html, 331KB browser screenshot, NOTES.md);
  validation caught the Writer claiming a file outside its lane; Opus
  review resolved it and its cost was logged. Total $0.87.
  The FIRST attempt exposed a real coordination gap: the Tester polled 20
  minutes for the Builder's index.html in its own isolated worktree —
  produce/consume dependencies only sequence when the consumer lists the
  file it reads. Planner prompt now requires consumed files in files_hint
  (overlap → D4 waves). Fixed and re-proven same session.

### Honest stock-taking

**Works, verified live**: the full pipeline (plan → gate → waves → workers →
auto-commit → validation → summarize → merge → llm_review), the learning
loop end-to-end, compaction, estimates + estimate/actual, MCP execution
with real browsers, hybrid routing with real savings, task-shape routing,
local meta-tasks, trajectory endpoint, session lifecycle + recovery.

**Exists but unproven/undertested in real use**: lessons at scale (n=2 —
retrieval precision at 0.35 needs watching as the store grows; the 3-strike
archive has never fired); META's recommendations have never driven an
accepted lesson or config change; VRAM fallback has never triggered under
real GPU contention (0 model/fallback events); the replay UI renders but
hasn't been used in anger; multi-wave plans beyond 2 waves; OllamaWorker on
true multi-file refactors (only creations and single-file edits proven).

**Missing vs the vision**: (1) planner cost is invisible — the single
biggest per-session cost is not in cost_log; (2) no sandbox — worktrees
are still the only blast-radius containment for --dangerously-skip-
permissions workers; (3) failed agents' committed work is discarded (the
palette Tester died at the finish line in D and its branch was dropped —
llm_review should evaluate failed agents' branches); (4) coordination
between dependent agents relies on planner discipline (waves) — no runtime
signal lets a consumer WAIT for a producer; (5) META is on-demand only —
the D-vision's scheduled self-analysis + pattern_detector trigger were
never wired.

**Too much / deletion candidates**: safety/pattern_detector.py (488 LOC,
still unwired since Phase 8 — D8 went on-demand instead; wire it or delete
it); backend/security/ is an empty directory (the executor was reduced in
Phase A — remove the husk); backend/telegram/ stays parked by decision but
is untested against everything B–E changed (if phone approvals ever return,
budget a rewrite, not a revival); the estimator's optional semantic
tiebreak and TestRun/PackageInstall validators remain deliberately unbuilt.

**Trajectory**: A fixed the foundation, B made the swarm real, C gave it
hands, D gave it memory and self-knowledge, E made it economical and
closed D's loops. Each phase demonstrably improved the tool for its one
user; the golden suite now catches regressions between phases, and the
learning loop means recurring failure classes should decay instead of
repeat. The system's honest failure modes are now variance-shaped (model
nondeterminism, CLI flakes), not architecture-shaped.

**Next, ranked**: (1) log planner cost + surface per-session cost breakdown
in the UI (small, closes the last accounting hole); (2) llm_review over
failed agents' branches — stop discarding finished work (medium); (3) let
consumers wait on producers at runtime (wave-aware file signal or
respawn-after-wave, medium); (4) scheduled META + wire or delete
pattern_detector (small-medium); (5) bwrap/container sandbox for workers
(large, the real security debt); (6) revisit local distillation with a
bigger local model or split-gate wiring (small, after more lessons exist).

## 2026-07-06 — Phase D "META / self-improvement" COMPLETE

The learning layer: HIVE now diagnoses its own failures from the event log,
distills lessons only from objective evidence, gates its own plans, and can
explain any session after the fact. 510 tests passing. Nine sections
(D0–D8), one commit each (`d82e784`..`fc29718`), plus a post-e2e fix commit
(`ed5c398`).

- **D0 reliability floor**: worker stderr drained into a bounded 2KB tail —
  the C5 "exited 1 (no stderr)" mode is now diagnosable from the event log
  alone; failure origin ∈ {agent, infrastructure, unknown} on every
  event/result, and trust only charges origin=agent (Phase C charged workers
  for HIVE's own integration bugs); backend/mcp/doctor.py productizes the C5
  stdio probe — spawn + initialize handshake before any agent pays for a
  broken server (live: playwright/context7/filesystem OK, github correctly
  reports the missing token); contaminated trust scores reset once.
- **D1 lessons store**: grounded writes only (validation-failure followed by
  a later clean run, llm_review resolutions, infra failures with concrete
  causes) — never an agent's self-diagnosis; Haiku distiller behind an
  interface (local-Ollama swap point later); 0–10 groundedness gate, <8
  discarded and logged; conservative reads (0.55 similarity bar, zero
  injections is the normal outcome, foreign-project lessons never inject,
  max 3 per brief); hygiene counters + 3-strike archive + Settings → Lessons
  tab. Deviation: staleness measured in days, not the spec's "sessions"
  (not a monotonic clock).
- **D2 plan gate**: one budgeted Haiku call scores each plan
  (coverage/overlap/fit/size); <7 triggers ONE revision round; a
  still-flagged plan forces the approval modal even in full-auto (amber
  issues panel). Fails OPEN — a Haiku outage never blocks work.
- **D3 compaction**: at ~20k history tokens or 12 turns, one Haiku call
  rewrites older turns into a CURRENT STATE doc; pruned turns preserved
  inside the compaction event; a failed doc build skips compaction rather
  than losing context.
- **D4 file-overlap resolution**: the deterministic sibling of the D2 gate —
  normalized files_hint intersections at parse time; same-role overlap →
  briefs merged; cross-role → later agent moves a wave with a
  read-my-branch note; vague hints exempt.
- **D5 golden suite**: 8 fixed specs through the REAL pipeline with
  executable success criteria (file_exists/file_contains/command_succeeds),
  timestamped reports + regression diff vs previous run; manual `hive
  golden run` only, never CI.
- **D6 estimates**: median/p90 cost + duration from similar past sessions
  (agent count ±1) in the approval modal; <3 similar sessions → "no
  estimate yet", never invented numbers; estimate-vs-actual event recorded
  at review. Deviation: the optional semantic-similarity tiebreak skipped.
- **D7 trajectory replay**: GET /api/sessions/{id}/trajectory rebuilds the
  whole session story from persisted events + checkpoint turns (no new
  backend state); desktop Chat ⇄ Replay toggle with typed timeline, agent
  filter, jump-to-first-error.
- **D8 META agent**: input assembly is pure code (lessons stats,
  origin-split trust, failure clusters, cost by model/role, estimate drift,
  golden trends); ONE Opus call — the one place the strong model earns it —
  writes META_REPORT.md; nothing auto-executes, and [lesson] recommendations
  go through the same D1 groundedness gate (422 on failure).

**E2E proof (4 live sessions, 2026-07-05 21:33–21:45)**: a docs task on a
`trunk`-default-branch repo (×2) and a forced-merge-conflict task (×2).
What demonstrably worked live: the plan gate scored every plan and fired
its one revision round on the conflict plans (two gate calls in cost_log);
D6 estimates appeared with honest "based on N similar sessions" and
estimate/actual events recorded on all four; the planner tiered all four
trivial agents to Haiku (cost discipline held); mechanical merge hit a REAL
conflict in e2e-conflict2 and Opus llm_review resolved it correctly (kept
the upstream hotfix line atop the builder's rewrite, conflicts: 1, merge
commit `e1fb346`); session close fired the lesson distiller on that
llm_review evidence.

**The learning loop did NOT visibly close.** The distiller ran ($0.0127)
but stored nothing — no lesson row, no discard event. That's consistent
with a legitimate 'NONE' verdict on thin evidence, but nothing was ever
injected (lesson_applications is empty), so fail-once-learn-not-fail-again
remains UNPROVEN end-to-end. First follow-up next session: one instrumented
re-run of the conflict pair to observe the distiller's raw output (NONE vs
silently dropped draft) and drive a real injection.

**Two bugs found by the e2e (fixed + regression-tested, `ed5c398`)**:
1. Spawner role-index collision — two same-role count=1 members (the normal
   B1 plan shape) got identical agent_ids; the second worktree creation
   failed and its entire subtask was SILENTLY dropped.
2. Validation merge-base candidates were only {main_branch, master, main} —
   any nonstandard default branch ('trunk', 'develop') false-negatived
   every claim. Candidates now include all non-`hive/` local branches.

**Measured costs**: plan gate $0.007–0.026/call (Haiku; ×2 when the
revision round fires); lesson distillation $0.013; summarizer
$0.017–0.029/agent; a whole trivial e2e session lands at ~$0.06–0.09.
Opus llm_review cost is NOT captured in cost_log (only the reviewer notes
event) — a gap worth closing. The golden suite and META agent were NOT run
live (golden/reports/ is empty, no META cost logged) — their real costs
are unmeasured until a `hive golden run` + `hive meta` baseline.

**Flags carried forward**: (1) close the learning loop with an instrumented
conflict re-run — decide whether the grounded triggers are too conservative
or a draft is being dropped without its discard event; (2) log reviewer and
META costs to cost_log; (3) first live golden baseline + META run; (4) once
(1) is understood, make D5's lessons-injection golden spec actually observe
an injection, not just a nonstandard branch.

## 2026-07-05 — Phase C "MCP execution" COMPLETE

Eyes and hands via integration, not building: agents can now drive real
browsers (and reach GitHub/docs servers) through per-agent MCP configs.
456 tests passing. Server-level management only, per the design decision —
Claude Code's own MCP client handles tool loading.

- **C0**: conflict_resolvers.py deleted (468 LOC + 18 tests) — B6's Opus
  llm_review owns conflicts.
- **C1**: backend/mcp/catalog.py — 4 curated servers (playwright, github
  [hosted remote; the npm server is deprecated upstream], context7,
  filesystem [worktree-scoped]), placeholder + ${ENV} expansion, preflight
  (node>=20, env vars), GET /api/mcp/catalog.
- **C2**: per-agent --mcp-config + --strict-mcp-config (no global-server
  leakage, verified flag); configs under HIVE_DIR/mcp-configs (outside the
  worktree so auto-commit can't sweep secrets into merges); preflight
  fails spawns FAST with the missing requirement named; MCP_ATTACHED
  events; process-group teardown reaps MCP children (verified: no orphans).
- **C3**: planner assigns servers per agent from a catalog-synced digest
  with restraint guidance; unknown ids dropped with warning; 🔌 chips in
  approval cards (pre-approval), team checklist, and agent pills;
  re-engagement (add server on respawn) composes with B2 --resume.
- **C5 e2e (color-palette generator + real-browser verification)**: the
  planner put playwright on the Tester ONLY; the Tester navigated to the
  built page via file://, clicked Generate, verified 5 distinct swatches,
  and merged a screenshot as evidence (hex labels in the PNG match its
  report). Review success: true. No orphan processes after close.

**Dogfooding found + fixed FIVE integration bugs** (each e2e run peeled
one layer; the last two came from probing the MCP over raw stdio instead
of burning more swarm runs):
1. @playwright/mcp rejects --user-data-dir in --isolated mode — server
   crashed at startup; --isolated alone IS the per-agent isolation.
2. Equipped agents weren't TOLD about their tools → the Tester
   npm-installed Playwright from scratch. Agent prompts now carry an
   "## Equipment" section.
3. Validation false-positived on absolute-vs-relative claim paths
   (+ a latent lstrip('./') bug that ate leading '/').
4. The MCP defaults to the system Chrome channel — absent on WSL;
   --browser chromium added.
5. Browsers must come from the MCP's OWN installer
   (install-browser chrome-for-testing); standalone playwright installs
   a mismatched build.

**Token overhead (C5 measurement)**: the playwright-equipped Tester cost
$0.41 vs the plain Builder's $0.10 (~4×; output tokens ~2.8×: 3073 vs
1110) — tool schemas + page snapshots are the overhead. Confirms the
restraint guidance: attach browsers only where the subtask needs them.
Haiku summarizer runs cost ~$0.02-0.03 each.

**Flags for Phase D (META layer)**: (1) the "claude exited 1 (no stderr)"
failure mode appeared in every failed Tester run — worth a first-class
diagnosis path (capture stderr tail into the agent error); (2) trust
scores absorbed several failures from HIVE's own integration bugs, not
worker faults — META's failure clustering should distinguish
infrastructure errors from agent errors; (3) MCP server startup health
isn't checked before the agent runs — a doctor-style "server starts and
answers initialize" preflight would have caught bugs 1/4/5 without any
agent run.

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
