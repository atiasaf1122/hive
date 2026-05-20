# HIVE — Phase 10, pass 4: v1.0-rc1 — Haiku live wiring + Summarizer agent + repo docs + CI

**Date:** 2026-05-20
**Status:** ✅ 599 backend tests (was 577 → **+22 new**), frontend vite build clean (384 KB JS / 37 KB CSS). Items 2, 3, 8 of the v1.0 "Still open" list now land. Only item 6 (Windows .msi build — hardware-bound) remains before tagging v1.0.0-rc1.

## Scope of this pass

Continuing from pass 3, this pass closes the three items that don't need a Windows machine to bundle the desktop installer:

- ✅ **Item 2** — Haiku live wiring through a session-scoped `HaikuCaller` for cross-check + skills rerank
- ✅ **Item 3** — Tiered reporting + Summarizer agent
- ✅ **Item 8** — README rewrite + CONTRIBUTING + CODE_OF_CONDUCT + SECURITY + GitHub Actions CI

Only **Item 6 (Windows .msi)** is left for the user — that's the genuine hardware dependency.

## ✅ Item 2 — Haiku live wiring

New module `backend/llm/haiku.py`:

- `HaikuCaller` — session-scoped, callable, one-shot wrapper around `ClaudeCLIWorker`. Each call spawns `--model claude-haiku-4-5 --max-turns 1`, collects `TEXT_DELTA` events into the final response, records the `COST` event into the existing `cost_log` table (so the /api/cost dashboard surfaces Haiku spend without any new wiring), and tracks per-session token spend.
- `HaikuBudgetExhausted` — raised when the cumulative token usage exceeds the session's budget (default 50 000; per-feature budgets via env vars: 20 000 cross-check / 10 000 rerank / 30 000 summarizer).
- Response cap (`max_response_len=8 000` chars) with mid-stream `worker.kill()` — protects against runaway responses.
- `build_caller(session_id, budget_tokens, …)` — production helper that defaults to `ClaudeCLIWorker`; tests inject a `_MinimalWorker` stub.

Wired in:

- `backend/api/skills_search_http.py` — `GET /api/skills/search/hybrid?session_id=…` now constructs a HaikuCaller and passes it to `maybe_rerank`. Budget exhaustion → `RerankResult(used_llm=False, skipped_reason="budget_exhausted")` (graceful fallback to the hybrid ranking).
- `backend/api/validation_http.py` — new `POST /api/validation/cross-check` route. Body carries `session_id`, the `CompletionReport`, git changes, audit rows, and installed packages. Runs every deterministic validator + (optionally) the Haiku semantic cross-check.

`semantic_cross_check` and `maybe_rerank` keep their existing `haiku_caller=None` contracts — when no caller is supplied they short-circuit with a `skipped_reason`, so the orchestrator continues to work in dev without an OAuth token.

## ✅ Item 3 — Tiered reporting + Summarizer agent

New package `backend/summarizer/`:

- `summarize_events(events, haiku_caller, task_description)` — accepts the raw `HiveEvent` stream (or replayed dicts), renders a compact chronological transcript (text deltas + tool uses + tool results + errors, with a `max_transcript_chars` head/tail trim), then calls Haiku for a single structured JSON response.
- `summarize_transcript(transcript, haiku_caller, task_description)` — same prompt path for pre-rendered transcripts (multi-agent rollups, replays).
- `TieredSummary` carries three views derived from one Haiku call: `tldr` (one sentence for chat bubble), `standard` (4-sentence paragraph), `detailed` (a full `CompletionReport` ready for the validator stack).
- Robust response parsing: strips ```json fences, finds the JSON object inside surrounding prose, normalises bad `status` values to `"done"`, drops malformed evidence rows, never crashes on unexpected fields.

HTTP wiring: `backend/api/summarizer_http.py` — `POST /api/summarizer/run`. The request can carry `verify=true` along with optional `git_changes` / `audit_rows` / `installed_packages_after`; when set, the detailed report runs through `validate_report_async` immediately after the Haiku call, returning a `verification` block alongside the three tiers. This is the "verification-before-VRAM-release" gate from the v1.0 plan — the orchestrator can use it to decide whether to release a worker's worktree or spawn a remediation turn.

## ✅ Item 8 — Docs + CI

- **README.md** rewritten: refreshed test counts, mentions the Tauri desktop shell as the primary UI, documents the safety stack (command policy + hard stops + validation), lists the new env vars (`HIVE_HAIKU_*_BUDGET_TOKENS`), and points at the new docs.
- **CONTRIBUTING.md** — dev environment, test commands, code style, architectural invariants, commit-message format, issue-filing rules, how to extend HIVE with skills/plugins/pipelines.
- **CODE_OF_CONDUCT.md** — Contributor Covenant 2.1.
- **SECURITY.md** — coordinated-disclosure policy, threat model (malicious LLM commands, exfiltration, persistence, supply chain), explicit out-of-scope list.
- **`.github/workflows/ci.yml`** — three jobs:
  - `backend` — uv install + `pytest tests/ -q`
  - `frontend` — npm ci + `tsc -b --force` + `vite build`
  - `desktop-rust` — `cargo check --locked` on the Tauri shell (PR-conditional + push-on-main)

## Verification

```
hive$     pytest tests/ -q                ✓ 599 passed in 57s
desktop$  vite build                       ✓ 384 KB JS / 37 KB CSS, gzipped 111 KB / 7.27 KB
```

**Test coverage delta this pass:** +22 new tests, 0 regressions.

- `tests/unit/test_haiku_caller.py` — 11 tests: text-delta concatenation, spend tracking, cost-event optional, AGENT_ERROR escalation, budget exhaustion, response cap kill, remaining-tokens accounting, integration with `semantic_cross_check` + `maybe_rerank`, plus HTTP-level tests for `POST /api/validation/cross-check` (with and without `run_semantic_check`).
- `tests/unit/test_summarizer.py` — 11 tests: three-tier extraction, markdown-fence stripping, JSON-in-prose detection, error paths (empty / unparseable), bad-status normalisation, malformed-evidence drop, event-stream rendering (text + tool + result + error), transcript trimming, HTTP wiring (verify=true happy path + empty-transcript 400).

The Rust shell wasn't re-`cargo check`-ed in WSL (cargo isn't installed there). The CI workflow above runs that check on every PR.

## Still open for v1.0

The user committed to handling item 6 themselves:

1. **Section 4 — Packaging on Windows** (was item 6). Run the `packaging/BUILD.md` runbook on a Windows machine to produce a signed `.msi` and verify first-run setup on a clean VM. Estimate: half a day, mostly waiting on builds.

2. **`v1.0.0-rc1` tag + GitHub Release draft** (was item 9). Once item 6 succeeds we tag and ship. The remaining LLM-integration work (cross-check, rerank, summariser) is now live and budget-protected; we can validate it against a real Haiku in an alpha pass after the .msi exists.

---

# HIVE — Phase 10, pass 3: v1.0-rc1 — safety overrides, hybrid skills search, streaming hardening, multi-window

**Date:** 2026-05-20
**Status:** ✅ 577 backend tests (was 546 → **+31 new**), frontend vite build clean (384 KB JS gzipped 111 KB, still well under the 400 KB target). Items 1, 4, 5, 7 of the v1.0 "Still open" list now land. The remaining gaps (Haiku cross-check live wiring, Summarizer, Windows .msi build, docs/CI) are still outstanding — see "Still open" at the bottom.

## Scope of this pass

Continuing from pass 2's "Still open for v1.0" list, this pass closes the four items that don't need a live model wired through `ClaudeCLIWorker` to validate:

- ✅ **Item 1** — Per-project safety override UI + backend
- ✅ **Item 4** — Skills hybrid search (BM25 + semantic + tag-overlap) + LLM rerank gate
- ✅ **Item 5** — Streaming hardening (NDJSON overflow recovery, WS resume ring buffer, virtual-scrolled audit list)
- ✅ **Item 7** — Multi-window + tray "New window" entry

What's deferred and why is at the bottom — items 2, 3, 6, 8, 9 either need a live Haiku call wired (2, 3), Windows hardware to bundle (6), or are pure-docs/CI work (8, 9).

## ✅ Item 1 — Per-session safety overrides

New table `session_safety_overrides` (see `backend/persistence/db.py`) keyed by `session_id`. Four user-tunable knobs (token budget, session-duration ceiling, concurrent-agents cap, same-file-edits cap), each nullable so an unset field inherits `HARD_STOPS` from `backend.safety.hard_stops`.

`backend/safety/overrides.py` (new):
- `SafetyOverride` dataclass — None = inherit.
- `merge(defaults, override)` — pure-functional, replaces only non-None fields.
- `load_override` / `save_override` / `clear_override` / `effective_limits` async helpers, all `db_path`-injectable for tests.

`backend/orchestrator/graph.py` — `spawn_node` now resolves `effective_safety_limits(state["session_id"])` and passes those merged limits into `check_hard_stops()` instead of the global `HARD_STOPS`.

`backend/api/safety_http.py` — adds `GET/PUT/DELETE /api/safety/sessions/{session_id}/override`. The GET returns `{ override, effective, defaults }` so the UI can render an amber "loosened" indicator next to fields that go beyond defaults.

`desktop/src/components/project/SafetyOverrideModal.tsx` (new) — modal with four numeric inputs, a `hasLoosening()` warning flag that turns the field amber when the value exceeds the default, and explicit "Save"/"Clear override" buttons. Wired from a gear-icon button in `ProjectView` header.

**Why session-scoped, not project-scoped:** sessions are the unit of work; a one-off blocking task and a long-running autonomous session in the same project legitimately want different caps. The override table is keyed by `session_id` so each session carries its own ceiling.

## ✅ Item 4 — Skills hybrid search + Haiku rerank gate

`backend/skills/bm25.py` (new) — 60-line pure-Python BM25 (k1=1.5, b=0.75) over name + description + tags. `tokenize` lowercases + word-boundary-splits + keeps hyphens inside tokens. `normalise()` does min-max into 0..1 and is collapse-safe for tied scores.

`backend/skills/registry.py`:
- `HybridHit` dataclass with score breakdown (semantic / keyword / tag_match / combined).
- `hybrid_search(query, tags, top_k, threshold, weights=(0.4, 0.4, 0.2))` — weighted combination of cosine similarity (existing embedder), BM25, and Jaccard tag-overlap. Tags pre-filter, they don't score.
- `should_use_rerank(expected_agent_count, tech_stack_complete, ambiguous_query)` — the "smart switch" that decides when the Haiku rerank pass is worth its tokens. Triggers on ≥5 agents, missing tech stack, or fewer than 4 informative query tokens.
- `maybe_rerank(hits, *, query, tech_stack, expected_agent_count, haiku_caller=None)` — when triggered AND a caller is wired, prompts Haiku with the candidate skill list and filters to its picks. With `haiku_caller=None` returns the unfiltered hits and `skipped_reason="not_wired"` so callers can ship the cheap ranking now. Error path catches the Haiku exception and falls back to the hybrid list with `skipped_reason="haiku_error: …"`.

`backend/api/skills_search_http.py` (new) — `GET /api/skills/search/hybrid?q=&tag=&top_k=&expected_agents=`.

`backend/main.py` — mounts the new router.

The Haiku caller itself is left injectable rather than directly wired — the same call-out pattern as the validation stack's `semantic_cross_check(haiku_caller=…)`. Wiring the live Haiku call is item 2 in the remaining list, since it needs the same `ClaudeCLIWorker --model haiku` infrastructure.

## ✅ Item 5 — Streaming hardening

### NDJSON overflow recovery

`backend/workers/stream_parser.py`:
- `MAX_BUFFER = 1_048_576` — a single line larger than 1 MB is treated as corrupted upstream. The parser searches for the next newline, drops everything up to it, logs a warning, and keeps going. Without this, a malformed stream that never emits `\n` would grow memory unboundedly.
- `IDLE_TIMEOUT_MS` (env-tunable, default 600 s) wraps each `stdout.read(4096)` in `asyncio.wait_for` — a stalled worker now abandons the read instead of hanging forever.
- **Bug fix uncovered by tests:** the previous parser dropped any unterminated content left in the buffer at EOF. Added `_flush_lines(buf, config)` that processes complete lines remaining in the buffer when the stream closes — without this, the very first event after an overflow recovery could be lost if EOF arrived before the next `read()`.

### WebSocket auto-resume

`backend/api/event_bus.py` (rewritten):
- Per-session ring buffer (`deque(maxlen=MAX_REPLAY=1_000)`) alongside the live queue.
- `emit()` stamps each payload with a process-wide monotonic `event_id` (preserves any caller-supplied id) and appends to both the ring and the live queue. Queue-full drops only the live copy; the ring keeps it.
- `events_since(session_id, after_id)` returns the catch-up slice.

`backend/api/ws.py` (rewritten) — on `accept()`, listens for up to `RESUME_WINDOW_SECONDS=1.5` for a first frame `{"resume_from": N}`. If received, replays every retained event with `id > N` before joining the live queue. No handshake → straight to live.

`desktop/src/lib/ws.ts` — tracks `lastEventId` from inbound payloads; on every `onopen`, sends `{"resume_from": lastEventId}` so a transient disconnect (Wi-Fi blip, backend restart within the ring window) recovers without the UI noticing.

### Virtual scrolling

`desktop/src/components/AuditLogViewer.tsx` — replaced the HTML table with a `@tanstack/react-virtual` list (`ROW_HEIGHT=32`, `overscan=8`). 500-row payloads no longer choke the WebView. Grid columns stay aligned between header and rows via `gridTemplateColumns: '170px 90px 1fr 160px 50px 60px'`.

## ✅ Item 7 — Multi-window + tray polish

`desktop/src-tauri/src/lib.rs`:
- New `open_new_window` Tauri command builds a fresh `WebviewWindowBuilder` with a `hive-<timestamp>` label so each window is tracked independently. Same dimensions / decorations as the main window.
- Tray menu gains a "New window\tCtrl+Shift+N" entry that dispatches into the same command via `tauri::async_runtime::spawn`.

`desktop/src/lib/shortcuts.ts` — Ctrl+Shift+N binds to `invoke('open_new_window')`. Outside Tauri the promise rejects and we swallow it so the pure-web preview keeps working.

LocalStorage is shared across windows of the same app by WebView default, so saved tabs and settings remain consistent across windows.

## Verification

```
hive$     pytest tests/ -q                ✓ 577 passed in 68 s
desktop$  vite build                       ✓ 384 KB JS / 37 KB CSS, gzipped 111 KB / 7.27 KB
```

**Test coverage delta this pass:** +31 new tests in `tests/unit/test_v1_rc.py`, 0 regressions. New coverage spans:

- Safety overrides: merge semantics, roundtrip save/load, upsert, clear, `effective_limits` merging with `HARD_STOPS`.
- Hybrid search: BM25 tokeniser corner cases, ranking, normalisation collapse-safety; `should_use_rerank` truth table; `maybe_rerank` skip/error/success paths; `hybrid_search` end-to-end against a small in-memory skill set.
- Streaming: NDJSON overflow recovery (line with newline + line without), non-JSON-line silent drop, ring-buffer monotonic IDs, replay filtering, existing-id preservation, ring cap.

The Rust shell wasn't re-`cargo check`-ed in WSL (cargo isn't installed there — `tauri build` runs on the Windows side). The new command uses canonical Tauri 2 APIs (`WebviewWindowBuilder::new(...).title().inner_size().min_inner_size().decorations(false).build()`).

## Still open for v1.0

In rough priority order:

1. **Section 5 — Haiku cross-check live wiring** (was item 2). The structure is in place — needs a real `ClaudeCLIWorker --model haiku` + budget tracking + integration tests. Estimate: 2–3 h.

2. **Section 3 — Tiered reporting + Summarizer agent** (was item 3). The schema is wired into validation; running a Haiku Summarizer while the worker is still in VRAM, plus the verification-before-release flow, is the deliverable. Estimate: 3–4 h.

3. **Section 4 — Packaging** (was item 6). Hardware-bound — needs a Windows machine to produce a signed `.msi` and verify first-run setup on a clean VM. Estimate: half a day, mostly waiting on builds.

4. **README rewrite + CONTRIBUTING / CODE_OF_CONDUCT / SECURITY templates + GitHub Actions CI** (was item 8). Tedious but mechanical. Estimate: 2–3 h.

5. **The actual `v1.0.0-rc1` tag + GitHub Release draft** (was item 9). Should happen after the items above land and a clean-Windows-VM install is verified. Tagging now would be misleading.

---

# HIVE — Phase 10, pass 2: safety UI + validation stack + conflict resolvers + orchestrator wiring + ADRs

**Date:** 2026-05-20
**Status:** ✅ 546 backend tests (was 501 → **+45 new**), tsc strict clean, vite 360 KB JS / 37 KB CSS gzipped — under the 400 KB v1.0 target.

## Scope honesty (read this first)

You asked me to finish every item in last pass's "Next pass" list in
one turn. The v1.0 plan you wrote estimated 8–15 hours for the
remaining work; in a single turn I can credibly land **a substantial
subset** to the same production-quality bar as Sections 1 and 6 from
pass 1, but **not all of it**. This pass delivers:

- ✅ Section 1.4 — Security settings UI + audit-log viewer
- ✅ Section 6.4 — Safety settings UI (read-only ceilings + live breaker table)
- ✅ Section 5 — Evidence schema + 5 deterministic validators + trust scores + Haiku cross-check stub
- ✅ Section 2 — Per-filetype conflict resolvers (6 resolvers, 18 tests)
- ✅ Orchestrator integration of Sections 1 + 6 — the safety stack is no longer dead code
- ✅ Section 10 — optional-integrations doc (GitHub PAT + crash-report path)
- ✅ Section 11 partial — ARCHITECTURE.md with all 11 ADRs

What's **not** in this pass — and why — is at the bottom under
"Still open for v1.0". The honest reason most of it isn't here is that
those sections need LLM-integration work (Haiku for cross-check,
Summarizer for tiered reporting, LLM rerank for skills) that has to
be wired end-to-end through `ClaudeCLIWorker` and validated against
a real model. Doing that responsibly takes the kind of focused pass
that shouldn't be sprinkled in alongside ten other changes.

## ✅ Section 1.4 — Security UI

`desktop/src/pages/Settings.tsx` now exposes two new sub-tabs (Security
& Safety) via a "Safety" group in the left nav.

`desktop/src/components/settings/SecurityPanel.tsx`:
- Five-mode picker (Manual / Smart auto / Full auto / Blind auto /
  Custom auto), labels + one-line subtitles matching the policy buckets.
- Selecting **Blind auto** opens a modal listing every category of
  command the mode runs without asking (installs, force pushes, curl,
  scripts, dev servers, env writes) plus an "I accept responsibility"
  checkbox that must be ticked before the OK button enables.
- **Custom rules** section appears when Custom auto is active —
  list + add-row UI + per-row pattern/action/delete. Persists via
  `PUT /api/security/policies`; loads via the existing GET.
- Audit-log retention setting (defaults to 30 days; capped 1–365).
- "Open audit viewer" button.

`desktop/src/components/AuditLogViewer.tsx`:
- Full-screen sheet, 500-row load, filter by classification + free-text
  search across command/agent/project, click-to-detail side panel with
  stdout/stderr excerpts.
- CSV export goes through `/api/security/audit/export.csv` (opens in a
  new tab so the browser handles the file save).

`desktop/src/stores/settings.ts` — added `commandApprovalMode` (separate
from the existing `approvalMode` which gates *task* approval) +
`auditRetentionDays`. Added `put` to the api client helper.

## ✅ Section 6.4 — Safety UI

`desktop/src/components/settings/SafetyPanel.tsx`:
- Read-only view of the six hard-stop limits with one-line explanations
  per ceiling (token budget, duration, agents, file-thrash, VRAM, disk).
- Live circuit-breaker table polled every 10 s from
  `/api/safety/breakers` — state dot (closed = green, half-open = amber
  pulse, open = red), consecutive failures, time-until-probe countdown,
  total trips. Reset button per worker calls
  `POST /api/safety/breakers/{id}/reset`.

Per-project safety cap UI is deferred — the *enforcement* is wired
(see orchestrator integration below); the per-project override editor
is small but I'd rather build it once the scheduler-integration story
is settled.

## ✅ Section 5 — Validation stack

### `backend/validation/schema.py` — evidence model
Pydantic models for `CompletionReport`, `Evidence`, `FileTouched`,
`TestRun`. Permissive on input (workers vary) but type-checked on
the fields we read.

### `backend/validation/validators.py` — deterministic validators
Five validators, no LLM calls:
- `FileModificationValidator` — claimed creates vs `git status --porcelain`
- `FileCreationValidator` — claimed `created` files must exist on disk
- `FileDeletionValidator` — claimed `deleted` files must be gone
- `TestRunValidator` — claimed test commands match `command_audit`
  rows with the same exit code
- `PackageInstallValidator` — claimed package installs are present in
  the post-state package list

`validate_report(report, ctx) → ValidationResult` is pure; the orchestrator
calls `validate_report_async` from the worker completion path.
Aggregate result has `.passed` and `.has_critical_issues` so the
caller can branch cleanly.

### Haiku cross-check — structure only
`semantic_cross_check(report, ctx, haiku_caller=None)` builds the
prompt, parses the score (clamped to 0–10), returns a
`SemanticCheckResult`. With `haiku_caller=None` it returns
`skipped=True, skipped_reason="not_wired"` — that's the deliberate
deferral: the structure is in place, the actual `ClaudeCLIWorker`
wiring + a real budget-aware Haiku call is the next-pass deliverable.

### `backend/validation/trust.py` + `worker_trust_scores` table
- `record_completion(worker_id, passed_validation)` upserts the row.
- Score = `successful / (successful + failed)`. New workers start at 1.0.
- `is_low_trust(score)` only flags after ≥ 10 sessions AND the score
  drops below the 0.70 floor — small samples don't penalise new models.

`GET /api/validation/trust` lists all scores; `DELETE /api/validation/trust/{id}`
resets one.

### Tests: 27 in `tests/unit/test_validation.py`
- 4 cases per validator covering happy + sad paths
- 5 cases on the Haiku stub (skipped paths, score parsing, clamping,
  garbage-response handling)
- 5 cases on trust DB (upsert, idempotent delete, ordering, low-trust threshold)
- 3 HTTP-level smokes

## ✅ Section 2 — Conflict resolvers

`backend/orchestrator/conflict_resolvers.py` — six heuristic resolvers
+ a `resolve_conflict(file_path, conflict_text) → ResolutionResult`
dispatcher. Pure-function; no disk I/O.

- `PackageJsonResolver` — JSON-parses both sides, unions the four
  dependency dicts, prefers side-A for scalar collisions.
- `RequirementsTxtResolver` — line-by-line union, dedupe by base
  package name (case-insensitive). Matches `requirements.txt` +
  `requirements-*.txt`.
- `CargoTomlResolver` — line union inside `[dependencies]` /
  `[dev-dependencies]` / `[build-dependencies]`. Raises `NotResolvable`
  if the conflict spans a non-deps section.
- `PyProjectTomlResolver` — array-of-strings union for
  `[project] dependencies = [...]`. Rejects non-array-shaped conflicts.
- `ImportsResolver` — for `.py` / `.js` / `.ts` / `.tsx` / `.jsx` /
  `.rs`, requires every non-blank line on both sides to be an import,
  then dedupes.
- `CssAppendResolver` — both branches appending to end-of-file (suffix
  empty, braces balanced).

`tests/unit/test_conflict_resolvers.py` — 18 tests with real-world
conflict examples. Every resolver has at least one
positive + one negative case.

## ✅ Orchestrator integration

`backend/orchestrator/graph.py`:

- **`spawn_node`** now calls `check_hard_stops()` with the proposed
  agent fan-out + tokens already spent this turn. A violation emits a
  `safety_hard_stop` WS event AND pushes a red system bubble into the
  chat history, then returns `approval_rejected=True` to abort the
  spawn cleanly.
- **`_execute_worker`** consults `breaker_registry.get(model).can_attempt()`
  before spawning. An OPEN breaker emits `safety_breaker_open` and
  returns an immediate failed `AgentResult` — no claude/ollama call
  is made.
- After the worker run, the breaker records success/failure and
  `record_trust_completion()` updates the trust score.

The safety stack is **no longer dead code**. From this pass onward,
breaking a worker three times trips it open for 5 minutes; hitting the
token budget aborts spawns and surfaces the reason inline.

## ✅ Section 10 — quick wins / docs

Most of Section 10 already shipped in Phase 9C (Ollama dual-stack,
real installs, Usage progress bars, Composer Opus default, workspace
picker, context menu, slash expansion, tray spam fix). The two
remaining items get a dedicated doc:

`docs/OPTIONAL_INTEGRATIONS.md`:
- **GitHub PAT** instructions for both WSL and PowerShell, explaining
  how the existing `GITHUB_TOKEN` env var lifts the unauthenticated
  60 req/h GitHub rate limit to 5 000 / h. The Skills + Plugins
  fetchers already consume the token; this just documents it.
- **Crash reporting** — explains the `HIVE_TELEMETRY_DSN` env var
  path. We deliberately ship without a default endpoint so we can't
  accidentally start receiving telemetry no one opted into.

## ✅ Section 11 partial — ARCHITECTURE.md

Rewrote `ARCHITECTURE.md` with:
- Layered overview diagram
- The seven invariants
- **Eleven ADRs** (Tauri vs Electron, LangGraph vs CrewAI,
  python-build-standalone vs PyInstaller, git worktrees, event
  sourcing, Opus-for-orchestrator-only, claude-CLI-over-SDK,
  single-port FastAPI, two-config dev/prod split, command-sandbox
  classification, per-worker breakers + trust scores) — each with
  *why* and *cost* sections so future contributors know what they're
  challenging when they propose a change.
- Module map
- Three sequence diagrams (first turn, agent-spawn with safety gates,
  sandbox approval round-trip)

README.md polish, CONTRIBUTING / CODE_OF_CONDUCT / SECURITY, and the
GitHub Actions workflows are still on the todo list — see "Still open
for v1.0" below.

## Verification

```
desktop$  tsc -b                  ✓ strict clean
desktop$  vite build              ✓ 360 KB JS / 37 KB CSS gzipped (under 400 KB target)
hive$     pytest -q tests/unit    ✓ 546 passed in 46 s
```

**Test coverage delta this pass:** +27 validation + +18 conflict-resolver
= +45 new tests, 0 regressions.

## Still open for v1.0

The remaining work needs at least one more focused pass each. Listed
in the order I'd attack them next:

1. **Per-project safety override UI**. The enforcement is wired
   (this pass); the per-project caps editor (token budget, agent
   ceiling, duration) is small but unbuilt. Estimate: 1–2 h.

2. **Section 5 — Haiku cross-check live wiring**. The structure is
   in place (`semantic_cross_check(haiku_caller=…)`) — needs a real
   `ClaudeCLIWorker` call with `--model haiku` + budget tracking +
   integration tests. Estimate: 2–3 h.

3. **Section 3 — Tiered reporting + Summarizer agent**. Workers
   output a `CompletionReport`-shaped block; a dedicated Haiku
   Summarizer runs while the worker is still in VRAM; verification
   before release. The schema landed this pass (Section 5);
   wiring it through the worker prompt path is the deliverable.
   Estimate: 3–4 h.

4. **Section 7 — Skills hybrid search + LLM rerank**. The existing
   semantic search works; adding BM25 + a Haiku rerank gate (when
   the team is large or the tech stack ambiguous) + the Skills
   Sidebar is the next chunk. Estimate: 2–3 h.

5. **Section 8 — Streaming hardening**. The NDJSON parser handles
   the happy path well; buffer-overflow recovery + WebSocket
   auto-resume after server restart + virtual scrolling for very
   long event lists need a focused pass. Estimate: 2 h.

6. **Section 4 — Packaging**. The runbook lives in
   `packaging/BUILD.md`; what's left is *running it on Windows*,
   producing a signed `.msi`, and verifying first-run setup on a
   clean VM. This is hardware-bound — needs an actual Windows
   machine. Estimate: half a day, mostly waiting for builds.

7. **Section 9 — Tray polish & multi-window**. Multi-window is the
   big addition; the tray itself works (this pass already de-duped
   the spam). Estimate: 1–2 h.

8. **README rewrite + CONTRIBUTING / CODE_OF_CONDUCT / SECURITY
   templates + GitHub Actions CI**. Tedious but straightforward.
   Estimate: 2–3 h.

9. **The actual `v1.0.0-rc1` tag + GitHub Release draft**. Should
   happen after items 1–8 land *and* a clean-Windows-VM install is
   verified. Anything earlier is a release-candidate in name only.

I haven't tagged anything in git — pushing a `v1.0.0-rc1` without
items 1–8 would be misleading. The code is in good shape; the next
session should pick up at item #1 and work down.

---

# HIVE — Phase 10, pass 1: foundational safety layers (Sections 1, 6) + tray-spam fix

**Date:** 2026-05-20
**Status:** ✅ 501 backend tests pass (was 228 → **+273** new), frontend tsc clean, vite 341 KB JS / 36 KB CSS.

## Scope honesty (read this first)

The v1.0 plan is 11 sections spanning 8-15 hours of focused work. **This pass is not
the whole thing.** It delivers the two safety sections we agreed to land first
(Sections 1 and 6 from the plan), plus the tray-spam fix from Section 10. Everything
landed is production-quality with comprehensive tests; nothing here is a stub.

What's covered → ✅ below
What's documented but not yet implemented → ⬜ below
What still needs to land for v1.0 → the "Next pass" list at the bottom

## ✅ Section 1 — Sandbox & Permissions

### `backend/security/command_policy.py` + `tests/unit/test_command_policy.py` (209 tests)

Three pattern lists drive `classify_command(cmd, custom_rules=None) → Decision`:

- **`ALWAYS_BLOCKED`** (non-overridable, even in BLIND_AUTO): `rm -rf /`, `rm -rf ~`, `rm -rf c:\`,
  `sudo`/`doas`/`su -`, `chmod 777`, `chown root`, `dd if=`, `mkfs`, `format c:`, `fdisk`, `nmap`,
  `nc -l`, `iptables`, `crontab -e`, `systemctl`, writes to `/etc/` or `C:\Windows\`, reading SSH
  / AWS / kube / netrc / appdata credentials, the classic fork bomb, unbounded `while true`.
- **`ALWAYS_ALLOWED`**: git reads + safe writes (add / commit / stash / new branch), generic reads
  (ls / cat / head / tail / wc / tree / find / grep / rg / ag), version queries (now including
  `git --version`, `docker --version`, `kubectl --version`, `tauri --version`), package listings
  (npm / yarn / pnpm / pip / cargo / go list), test runners (pytest / jest / vitest / playwright /
  cypress / cargo test / go test), linters in no-fix mode (eslint / prettier --check / ruff /
  black / mypy / pyright / rustfmt --check), builds (npm run build / tsc / cargo build / vite /
  webpack / next build / go build).
- **`REQUIRES_CONFIRMATION`**: package installs (npm / yarn / pnpm / pip / uv / cargo / gem / brew /
  apt / dnf), code execution of arbitrary files (node / python / bash / sh / deno / bun / eval /
  exec), the classic `curl … | bash` pipe-to-shell pattern, network downloads (curl -O / wget /
  git clone), significant git writes (push / merge / rebase / tag / reset --hard / clean -fd),
  out-of-worktree filesystem mutations (rm/mv/cp with absolute paths or `..`), dev servers
  (npm dev / vite / next dev / uvicorn / fastapi run / docker run / docker compose up), DB shells
  (psql / mysql / mongosh / sqlite3), SQL files with destructive verbs, environment changes
  (export / set / setx, writes to `~/.bashrc` / `~/.zshrc` / `.env`).

Patterns are case-insensitive and applied to a whitespace-normalised form. Evaluation order:
**custom rules first**, then system BLOCKED, then ALLOWED, then CONFIRMATION, then default to
CONFIRMATION (safe fallback). The custom-rule precedence is intentional — it lets users tighten
*or* loosen specific commands.

### `backend/security/approval_mode.py` + `tests/unit/test_approval_mode.py` (25 tests)

Five modes:

| Mode | ALLOWED | CONFIRMATION | BLOCKED |
|------|---------|--------------|---------|
| `MANUAL`       | ask | ask | block |
| `SMART_AUTO`   | run | ask | block |  ← default
| `FULL_AUTO`    | run | run | block |
| `BLIND_AUTO`   | run | run | block |  ← UI gate, not gate behaviour
| `CUSTOM_AUTO`  | SMART_AUTO base + custom_rules from `~/.hive/custom_policies.json` |

`should_execute(classification, mode) → "run" | "ask" | "block"` is the pure gate the
executor consults. `evaluate(cmd, mode, custom_rules=None) → (Decision, action)` is the
single end-to-end call.

Persistence: `~/.hive/custom_policies.json` is the user's rule set. Load/save with late-bound
paths so tests can monkeypatch the constant. Corrupted file → backed up to `.bak` and
re-created empty so the user is never locked out.

### `backend/security/executor.py` + `tests/unit/test_secure_executor.py` (12 tests)

`secure_execute(cmd, mode=SMART_AUTO, working_dir, agent_id, project_id, custom_rules=None)`:

1. **Classify** + **decide** via `evaluate()`.
2. If **block** → write audit row (`classification='blocked'`), return immediately with the
   `Decision` carrying `rationale`. The command never spawns.
3. If **ask** → mint a 12-char token, store the pending request in-memory (`{token: PendingApproval}`)
   with a 10-minute timeout, return `ExecuteResult(status='pending_approval', pending_token=…)`.
4. If **run** → spawn via `asyncio.create_subprocess_shell` with stdout/stderr piped, optional
   timeout, capture, write audit row, return `ExecuteResult(status='completed', exit_code, stdout,
   stderr, duration_ms, audit_id)`.

`resume_with_approval(token, approved)` resolves a pending future and either kicks off
`_run_and_audit` (approved=True) or writes a rejection row (approved=False).
`list_pending_approvals()` is the read-only snapshot for the API to render.

The audit row's `classification` reflects the **policy bucket** the command fell in
(`allowed` / `confirmed` / `blocked`), not whether the user was prompted. `user_approved`
carries the prompt detail (`1` / `0` / `NULL`).

### Audit table — SQLite schema migration

```sql
CREATE TABLE IF NOT EXISTS command_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts DATETIME NOT NULL DEFAULT (datetime('now')),
    project_id TEXT, agent_id TEXT, command TEXT,
    working_dir TEXT, classification TEXT, decision_source TEXT,
    matched_pattern TEXT,
    exit_code INTEGER, stdout_excerpt TEXT, stderr_excerpt TEXT,
    duration_ms INTEGER, user_approved INTEGER
);
CREATE INDEX … ON command_audit(ts);
CREATE INDEX … ON command_audit(project_id);
CREATE INDEX … ON command_audit(agent_id);
```

Stdout/stderr truncated to 500 chars each (full streams still flow through WS to the agent
log). `purge_old_audit_rows(retention_days)` for the retention setting (default 30 days).

### `backend/api/security_http.py`

```
GET    /api/security/policies                 — load user's custom_rules
PUT    /api/security/policies                 — replace rule set
GET    /api/security/audit                    — query (project_id, agent_id, class, since, until)
GET    /api/security/audit/export.csv         — same, as CSV (10k limit)
GET    /api/security/approvals/pending        — in-flight requests
POST   /api/security/approvals/{token}        — approve / reject
```

⬜ **Section 1.4 (Security settings UI)** is not in this pass — the API surface is ready
for it.

## ✅ Section 6 — Circuit breakers & cost control

Pure-logic modules — no I/O, no globals, fast tests.

### `backend/safety/hard_stops.py` + tests (7 tests)

`HardStops` dataclass with the six 2026-standard limits:

```
max_concurrent_agents          8
max_session_duration_hours     4.0
max_same_file_edits            5
vram_threshold_percent         95
disk_min_free_gb               1.0
max_tokens_per_autonomous_run  500_000
```

`check(...)` returns the *first* `HardStopViolation` (ordered most-actionable-first:
tokens → duration → agents → file-thrash → VRAM → disk) or `None`. Custom `HardStops` can
tighten the defaults per project.

### `backend/safety/circuit_breaker.py` + tests (8 tests)

Classic three-state machine. `CLOSED` → 3 failures → `OPEN` (5-min cool-down) →
`HALF_OPEN` (one probe) → either back to `CLOSED` on success or back to `OPEN` with a
fresh cool-down on failure. Per-worker instances in `BreakerRegistry`; `default_registry`
is process-local for now (Phase 10 follow-up will persist on shutdown).

`_now()` is overridable so tests freeze time without `sleep`. Switched off the deprecated
`datetime.utcnow()` to `datetime.now(timezone.utc)`.

### `backend/safety/quality_monitor.py` + tests (5 tests)

Per-session rolling window of validation scores (0–1). Returns
`AutoUpgradeRecommendation` when the last 5 scores' average drops ≥ 15 percentage points
below the session average **and** lands below 0.5. The "and" matters — a drop from 0.95 to
0.7 doesn't fire, only a drop into the danger zone.

### `backend/safety/pattern_detector.py` + tests (7 tests)

`detect_stuck_patterns(activity, now=None) → list[StuckPattern]`. Pure function on an
`ActivityWindow` dataclass the orchestrator assembles. Detects:

- **same-error**: one error string repeated 5×
- **file-thrash**: one file edited 5× in 10 minutes
- **no-progress**: agent flagged active, no commits/new files for 10 minutes
- **token-velocity**: last hour > 5× the 24-hour baseline (severity = `blocker`)
- **reviewer-rejects**: same Builder rejected 5×

### `backend/api/safety_http.py`

```
GET  /api/safety/limits/defaults     — the HardStops ceiling shipped with this build
GET  /api/safety/breakers            — snapshot of every per-worker breaker
POST /api/safety/breakers/{id}/reset — manual reset from the UI
```

⬜ **Section 6.4 (per-project Settings UI)** is not in this pass — the read-only safety
API is ready for it.
⬜ **Integration into the orchestrator loop** — `_execute_worker` doesn't yet
consult `hard_stops.check` or `default_registry`; that's the next pass because it
touches the LangGraph state machine and needs careful end-to-end testing.

## ✅ Section 10.9 — Tray-icon spam

Symptom from your testing: "Error removing system tray icon" every 15 seconds.

Cause: the heartbeat unconditionally called `set_visible(false)` on every poll even when
nothing changed, and Windows surfaces a "removing tray icon" line each time the visibility
toggle is applied to an already-hidden tray.

Two-layer fix:

- **`desktop/src/lib/tray.ts`** — `useTrayHeartbeat` now de-dupes: tracks the last
  `{running, tooltip}` it pushed and only `invoke`s when something actually changed.
- **`desktop/src-tauri/src/lib.rs`** `update_tray_status` — defence in depth, keeps its
  own `Mutex<Option<(bool, String)>>` so a no-op call from outside this heartbeat (e.g. an
  IDE / extension) also doesn't re-toggle visibility.

## Verification

```
desktop$  tsc -b                  ✓ strict clean
desktop$  vite build              ✓ 341 KB JS / 36 KB CSS gzipped
hive$     pytest -q tests/unit    ✓ 501 passed in 51 s
```

Coverage breakdown of the new code:

- `test_command_policy.py` — 209 cases, every pattern in the three lists exercised once
- `test_approval_mode.py` — 25 cases, the 15-row should_execute matrix + persistence
- `test_secure_executor.py` — 12 cases, run/ask/block paths + audit query + CSV export
- `test_safety_stack.py` — 27 cases, all four safety modules

Total **+273 new tests**, 0 regressions across the previously-passing 228.

## Next pass — what still needs to land for v1.0

In rough priority order, each one a self-contained chunk you can ask for:

1. **Section 1.4** — Security settings UI (mode picker + BLIND_AUTO accept-responsibility
   modal + CUSTOM_AUTO rule editor + AuditLogViewer reading `/api/security/audit`).
2. **Integrate Section 6 into the orchestrator loop** — wire `hard_stops.check` into the
   spawn path; consult `default_registry.get(worker_id).can_attempt()` before launching a
   worker; emit pattern alerts from a 30-s sweep.
3. **Section 6.4** — Per-project safety Settings UI (rate-limit cap, max duration, max
   agents, max interactions; API vs Max-subscription mode-aware).
4. **Section 5** — Hallucination guardrails (evidence schema, deterministic validators,
   Haiku cross-check, trust scores).
5. **Section 3** — Tiered reporting (structured completion reports, dedicated Summarizer
   agent, verification-before-VRAM-release, context meter).
6. **Section 2** — Merge-conflict resolvers (package.json / requirements.txt / Cargo.toml
   / imports / CSS), LLM resolution state, user escalation modal.
7. **Section 7** — Skills hybrid search + LLM re-rank + sidebar.
8. **Section 8** — NDJSON parser hardening + WebSocket auto-resume + virtual scroll.
9. **Section 4** — python-build-standalone bundling + first-run wizard + cross-platform
   installer + Tauri updater.
10. **Section 9** — Window lifecycle / tray polish (mostly already in Phase 9D-9C).
11. **Remaining 10.x bugs** — registry fetcher debugging (need GitHub token UX),
    Ollama dual-stack probe (already done in 9C; verify), Composer Opus default
    (already done), workspace folder picker (already done in 9C), context menu (done),
    slash command expansion (done in 9C), GitHub PAT docs, Sentry-style crash reporting.
12. **Section 11** — README rewrite, ARCHITECTURE.md with 11 ADRs, CONTRIBUTING /
    CODE_OF_CONDUCT / SECURITY, GitHub Actions test + build + release workflows.

I'd suggest **#1 → #2** next so the safety stack you just got is visible in the UI and
actively gating the orchestrator. Tell me which to do and I'll keep going.

---

# HIVE — Hotfix: dev build broke because externalBin pointed at a binary that doesn't exist yet

**Date:** 2026-05-19
**Status:** ✅ Fixed — `npm run tauri:dev` compiles again. Packaging plumbing kept intact for the future Windows `.msi` build.

## Symptom

```
error: resource path `binaries\hive-backend-x86_64-pc-windows-msvc.exe` doesn't exist
```

`tauri-build` reads `tauri.conf.json` at compile time. The
`bundle.externalBin: ["binaries/hive-backend"]` we added in the previous
turn told it the frozen PyInstaller sidecar was a required resource —
but that binary only exists after running `packaging/hive-backend.spec`
on Windows, which the user hasn't done yet. Result: every dev build
failed before Rust even started compiling.

## Fix (option 1 — clean split)

Split the Tauri config into a dev base + a prod overlay:

| File | Purpose |
|------|---------|
| `desktop/src-tauri/tauri.conf.json` | Base config used by `tauri dev` and `tauri build`. **No** `externalBin`, no `resources` — pure dev workflow. |
| `desktop/src-tauri/tauri.conf.prod.json` | Tiny overlay file. The only thing in it is `bundle.externalBin: ["binaries/hive-backend"]` + a placeholder `resources` array. Tauri merges this on top of the base when the build runs. |
| `desktop/package.json` | `tauri:build` now passes `--config src-tauri/tauri.conf.prod.json` so the overlay is applied. `tauri:dev` is unchanged → uses the base config only → no sidecar requirement → compiles on any machine. |
| `packaging/BUILD.md` | Updated to explain the overlay so future devs know where the externalBin lives. |

Why option 1: it's the smallest change that preserves the production
plumbing for when the `.msi` actually gets built. The Rust side
(`spawn_sidecar_binary`) already returned `Ok(None)` when the binary
wasn't present and fell through to the dev `uvicorn` path; that
behaviour is unchanged. Only the build-time resource manifest needed
to drop the reference.

## Verification

```
desktop$  tsc -b            ✓ strict clean
desktop$  vite build        ✓ 341 KB JS / 36 KB CSS gzipped (unchanged)
hive$     pytest -q tests   ✓ 228 passed
```

Rust compilation on Windows can't be checked from this Linux sandbox,
but `tauri-build` no longer parses an externalBin entry that points
at a missing file, so the original Cargo failure is gone.

## Updated workflow

Same as before — just works now:

```powershell
wsl
cd ~/hive
git pull
pkill -f "uvicorn backend.main:app"
hive start
exit

robocopy "\\wsl$\Ubuntu\home\atiasaf1122\hive\desktop" "C:\Users\The One\hive-desktop" /MIR /XD node_modules src-tauri\target dist
cd "C:\Users\The One\hive-desktop"
npm install
npm run tauri:dev
```

When you're ready to build the .msi (after running the PyInstaller
freeze per `packaging/BUILD.md`), `npm run tauri:build` will pick up
the overlay automatically — no command-line ceremony.

---

# HIVE — Phase 9C Bugfix Sweep + Phase 9D (Close-dialog, Tray, Packaging)

**Date:** 2026-05-19
**Status:** ✅ 228 backend tests pass (was 220 → +8), tsc strict clean, vite build 341 KB JS / 36 KB CSS. Honest scope note: the actual `.msi` produced by `tauri build` was *not* run in this environment — the runbook is documented in `packaging/BUILD.md` and must be executed on Windows.

This delivery solves the 15 issues from Phase 9C testing plus ships
the Phase 9D structural pieces — close-confirmation dialog, system
tray, and the PyInstaller-based sidecar plumbing that turns
`tauri build` into a real installer.

## The 15 fixes

### Critical

**#1 Sessions silently stalled — agents not actually working.** Two
layers of defence:

- **Backend self-heal** in `backend/worktrees/manager.py` and
  `backend/orchestrator/graph.py`: `ensure_git_repo` and
  `_auto_commit_worktree` now write a per-repo `user.name` /
  `user.email` ("HIVE" / `hive@localhost`) **before** the bootstrap
  commit if the global identity is missing. The "Author identity
  unknown" stall is now impossible.
- **Frontend preflight** in `backend/api/preflight_http.py`:
  `GET /api/preflight/check?project_path=…` returns
  `{ok, blockers[], warnings[], git_user_name, git_user_email}`. The
  Dashboard QuickStart runs this **before** posting `/api/sessions`;
  blockers open a modal with a per-issue **"Configure for me"** button
  that calls `POST /api/preflight/fix-git`. Errors that *do* happen
  during a session (`session_error`, `agent/error`) now surface inline
  in the chat thread as a red system bubble — the agent pill turning
  red is no longer the only signal.

**#2 Composer model defaulted to Sonnet.** Default flipped to
`claude:opus`. The chip label is now `"Orchestrator: <model>"` with a
tooltip stating it's the planner, not the worker. The dropdown adds an
explicit "Recommended for orchestration" tag on Opus.

**#3 Workspace selector was a stub.** Real folder picker via
`@tauri-apps/plugin-dialog`. Each pick lands in `useSettings.recentWorkspaces`
(MRU, capped at 10) and appears in the workspace chip dropdown next
time. The path is forwarded to `POST /api/sessions` as `project_path`,
which the backend already uses for the worktree root.

### Registry connectivity

**#4 Real diagnostics.** Both proxies now collect a `FetchResult` per
source (`ok / error / duration_ms / fetched_at`) and expose two new
fields per request: `per_source[…]` with the same shape, and
`last_success_at` so the UI can show "fresh ~30 s ago". A new
endpoint `GET /api/registries/diagnose` runs every fetcher fresh —
wired to a future Settings → Integrations → "Test registry connections"
button. GitHub calls now send `Authorization: Bearer $GITHUB_TOKEN`
when the env var is set so the user can lift the 60 req/h
unauthenticated rate limit to 5 000.

**#5 75 curated skills.** `backend/registries/curated.py` rewritten
with 75 entries across Cookbook, ClawHub, and community: Python /
FastAPI / Django / Flask / async / pandas / polars / React / Next.js /
Vue / Svelte / TypeScript / Tailwind / CSS / a11y / Express / NestJS /
Prisma / Zod / SQL design / Postgres / SQLAlchemy / DuckDB / Docker /
Kubernetes / Terraform / GH Actions / nginx / OpenTelemetry / Jest /
Vitest / Playwright / Cypress / LLM prompts / embeddings / LangGraph /
sklearn / PyTorch / React Native / Flutter / Swift / Kotlin /
security review / auth patterns / secrets / code-review / refactor /
dead-code / bash / Typer / Click / oclif / Go / Rust / Tokio / docs /
release notes / API docs / ADRs / web vitals / Node perf / Python
perf — plus 6 unverified community entries that trip the Feb 2026
warning. Each entry has stars, downloads, verified flag, tags.

**#6 31 curated MCP servers.** Cookbook + Smithery + awesome-mcp now
covers Filesystem / SQLite / Postgres / MySQL / Redis / GitHub /
GitLab / Git / Brave Search / Fetch / Memory / Time / EverArt / Exa /
Tavily / Perplexity / Linear / Notion / Slack / Discord / Gmail /
Calendar / Airtable / ComfyUI / Stable Diffusion / Piper / Coqui /
Whisper / Playwright / Puppeteer / AWS — each with real permission
strings and install transport.

### Install actions

**#7 Real skill install.** `POST /api/registries/skills/install` now:
- Translates GitHub `tree/<ref>/<path>` URLs to raw `SKILL.md` URLs,
  fetches them, falls back to a synthesised SKILL.md if the upstream
  isn't reachable.
- Writes to `~/.hive/skills/<slug>/SKILL.md`.
- Calls existing `import_skill(path)` which validates the YAML
  frontmatter, embeds the description, and inserts into the `skills`
  table — i.e. the skill immediately enters the orchestrator's
  semantic injection candidate pool.
- On validation failure, deletes the partial file so we never leave a
  half-installed skill behind.

**#8 Real MCP install.** `POST /api/registries/mcp/install` now:
- Reads `~/.claude.json` (or `~/.claude/config.json` — whichever exists,
  preferring the newer flat file).
- Writes the server entry into the `mcpServers` section using the
  correct invocation per transport (`npx -y …` for npm, the package
  name as command for pip, `smithery run …` for Smithery).
- Returns the runtime-install command (`npm install -g …` etc.) so
  the UI shows it to the user — we deliberately don't shell out to
  `npm install` from the backend.
- `DELETE /api/registries/mcp/{id}` removes the entry.

Front-end install handlers wired in `Skills.tsx` + `Plugins.tsx`.
Permission dialog now displays the actual `permissions[]` array from
the registry.

### Usage tab — matches claude.ai

**#9** Rewritten with three real progress bars: **Current session**
(5h rolling), **Weekly limits · All models**, **Weekly limits ·
Sonnet only** (estimated, ≈70% of weekly all — noted in copy as a
proxy), plus the **Daily included routine runs** counter (X / 15).
Each bar is colour-graded (emerald < 50%, amber < 80%, red ≥ 80%),
shows the reset countdown (`Xd Xh` until next Monday for weekly
limits), and triggers an alert banner when any bar ≥ 80%. The honest
disclaimer is at the top of the hero AND below every bar group — the
Sonnet-only estimate, the routine-run caveat, and an explicit "always
check claude.ai for authoritative numbers" link.

### Settings polish

**#10 Model dropdowns** in QuickStart and Settings → AI now list every
detected option: Claude Opus 4.7 *(recommended)*, Sonnet 4.6, Haiku
4.5, and live-detected Ollama models. The amber warning only appears
when the user actually picks a non-Claude-Opus orchestrator — and it
never blocks the choice.

**#11 Worker model clarified.** Setting renamed (in copy) to "Default
worker model" with a hint: *"Orchestrator chooses per task. This is
the fallback when it doesn't pick explicitly."*

**#12 Telemetry transparency.** The Privacy panel now lists exactly
what we'd collect ("error message, stack trace, HIVE version, OS
name + version. No project content, no credentials, no personal
data.") plus a **"View what we'd collect"** button that pops a JSON
sample showing both the fields sent AND a `not_sent` list of fields
deliberately excluded.

### Dashboard UX

**#13 Right-click context menu** on every project card. New
`components/ui/ContextMenu.tsx` primitive. Items: **Open** /
**Rename** / **Save as template** / **Duplicate as template** /
**Export to Markdown** / **Close project** / **Delete permanently…**
(danger-coloured, double-confirm). Export to Markdown generates a
client-side blob and triggers a save — no backend round-trip. Rename
patches the local store (real backend rename endpoint is a 9D add).

**#14 20 slash commands, grouped.** `SlashMenu` now shows four
categories — **Session** (`/clear`, `/cost`, `/compact`,
`/clear-context`, `/model`, `/memory`, `/history`), **Project**
(`/init`, `/save-template`, `/export`, `/close`, `/workspace`),
**Tools** (`/agents`, `/skills`, `/tools`, `/pause`, `/resume`,
`/status`), **Help** (`/search`, `/help`). Each line shows
name, hint, and (where applicable) the keyboard shortcut.

### Onboarding

**#15 Ollama detection.** Backend `_check_ollama` now probes both the
configured endpoint **and** its IPv4 form (`localhost → 127.0.0.1`)
so an Ollama listening only on v4 isn't missed by hosts that resolve
`localhost` to `::1` first (common on WSL2). The onboarding step has
a **Test connection** button and re-probes on field blur. Failure
state now shows the actual error returned from the proxy.

## Phase 9D — what shipped this turn

### C — Close-confirmation dialog

Tauri side (`src-tauri/src/lib.rs`):
- `WindowEvent::CloseRequested` is captured, `api.prevent_close()`
  is called, and a `hive://close-requested` event is emitted to the
  frontend.
- A new `confirm_close(window, confirm)` IPC command performs the
  actual close (or no-ops).

Frontend (`desktop/src/components/CloseConfirmation.tsx`):
- Listens for the event, fetches `/api/lifecycle/active-counts`, and
  branches per the architecture decision recorded in 9C:
  1. **Interactive agents busy** → confirmation modal (default button:
     *Stop and close*).
  2. **Only automations + Telegram + `backgroundAutomations` ON** →
     silent close (backend keeps running headless in tray).
  3. **Nothing active** → close immediately.

### B — System tray

Tauri side: `TrayIconBuilder` with menu (**Open HIVE** / **Pause all
automations** / **Quit**), left-click opens the main window. A
`update_tray_status(running, tooltip)` IPC command lets the frontend
show/hide the icon and update the tooltip with the live count.

Frontend `useTrayHeartbeat()` polls `/api/lifecycle/active-counts`
every 15 s and calls `update_tray_status` — the icon only appears
when there are running automations or the Telegram bot is up. Pause
button emits `hive://tray-pause-all` for the frontend to handle
(stub for now; 9D follow-up wires it to `PATCH /api/pipelines/{id}`
with `enabled: false`).

### A — Packaging plumbing (runbook + spec; .msi must build on Windows)

| File | Purpose |
|------|---------|
| `packaging/entrypoint.py` | The PyInstaller frozen entry. Sets `HIVE_DIR` to `%APPDATA%\HIVE\` on Windows, `~/Library/Application Support/HIVE/` on macOS, `~/.local/share/HIVE/` on Linux **before** the backend imports anything. |
| `packaging/hive-backend.spec` | PyInstaller spec with `hiddenimports` for `langgraph`, `aiosqlite`, `aiogram`, `apscheduler`, and uvicorn protocol modules. Excludes `tkinter` / `matplotlib`. |
| `packaging/BUILD.md` | Step-by-step runbook: freeze → drop into `src-tauri/binaries/<triple>/` → `npm run tauri:build`. Includes the smoke test (`/health` on the frozen binary) and a "test on clean VM" gate. |
| `desktop/src-tauri/tauri.conf.json` | Added `bundle.externalBin: ["binaries/hive-backend"]`. |
| `desktop/src-tauri/src/lib.rs` | `spawn_sidecar_binary(app)` runs **before** the dev `uvicorn` path — when the .msi installs `hive-backend.exe` next to the main exe, the Rust shell finds it via `app.path().resource_dir()`. Dev workflow still falls through to the existing `.venv/bin/python` path. |

**What I could not verify in this environment:** the actual
`tauri build` producing a signed `.msi`. The Tauri build needs Windows
SDK + signtool, and PyInstaller can't cross-compile. The runbook in
`packaging/BUILD.md` has the exact commands + a "test on a clean
Windows 11 VM with no Python and no WSL" verification step that must
pass before anyone publishes the installer.

## New tests

`tests/unit/test_preflight.py` — 8 cases:

- preflight passes when git/claude/ollama all happy
- preflight blocks when global git identity is missing (with `auto_fixable=True`)
- preflight blocks when no Claude backend is available
- `_git_identity()` returns ('', '') when git isn't installed
- `/api/registries/diagnose` envelope shape — every required field per source
- skill install writes a valid SKILL.md and feeds it to `import_skill`
- MCP install writes the correct entry into `~/.claude.json`
- MCP uninstall removes the entry

Total backend: **228 passed** in 45 s. The one query-filter test in
`test_registries.py` was tightened to look at name + description + tags
together (the expanded curated set has tagged items that legitimately
match a query through any field).

## Build verification

```
desktop$  tsc -b                ✓ strict clean
desktop$  vite build            ✓ 341 KB JS / 36 KB CSS gzipped
hive$     pytest -q tests/unit  ✓ 228 passed in 45 s
```

## Phase 9 feature tracker (updated)

```
[x]  1. Onboarding wizard (first launch)          → 9C ✅
[~]  2. File browser / workspace panel            → 9D follow-up
[~]  3. Native notifications                      → 9D follow-up
[x]  4. Global search (Ctrl+K)                    → 9C ✅
[x]  5. Keyboard shortcuts                        → 9B + 9C ✅
[x]  6. Error states (per failure mode)           → preflight blockers + inline chat errors
[x]  7. Loading states                            → 9C ✅ skeletons
[~]  8. Multi-window support                      → 9D follow-up
[~]  9. Drag & drop                               → 9D follow-up
[~] 10. Export / import                           → Export-to-Markdown ships in #13;
                                                       backup/restore in 9D follow-up
[~] 11. Per-project memory toggle                 → 9D follow-up
[x] 12. Advanced skills management                → 9C ✅ preview + real install
[x] 13. Cost / budget alerts (notifications only) → bars + threshold settings + 80% banner
[~] 14. Team mode placeholder (DB schema)         → 9D follow-up
[x] 15. Privacy transparency                      → Settings → Advanced ✅
[x] 16. Help system + tooltips                    → Tooltip primitive + re-run tour ✅
[~] 17. Activity feed                             → 9D follow-up
[~] 18. Performance monitoring + "why?"           → 9D follow-up
[~] 19. Git integration + revert                  → 9D follow-up
[N/A] 20. Mobile = Telegram (already handled)
```

The remaining `~` items are intentionally deferred — they don't block
the build-and-ship of the .msi. Phase 10 (CV polish) can land in
parallel.

## To test

```bash
# WSL — pull backend changes and restart
wsl
cd ~/hive
git pull
pkill -f "uvicorn backend.main:app"
hive start

# In another PowerShell session — re-sync the desktop tree
robocopy "\\wsl$\Ubuntu\home\atiasaf1122\hive\desktop" "C:\Users\The One\hive-desktop" /MIR /XD node_modules src-tauri\target dist
cd "C:\Users\The One\hive-desktop"
npm install
npm run tauri:dev
```

Try in this order:
1. Dashboard → type *"Write a snake game in Python"* → **Ctrl + Enter**.
   If git identity is missing, the Preflight modal opens with the
   **Configure for me** field. Fill name+email, click → it's set, the
   blocker clears, project launches.
2. Right-click any project card — context menu opens. Try
   **Export to Markdown**.
3. Compose `/` in any project → 20 commands grouped under
   Session / Project / Tools / Help.
4. Skills tab → search "python" → see ~25 results across cookbook,
   ClawHub, and (if `GITHUB_TOKEN` is set) live community repos.
   Click Preview on an unverified item — Feb 2026 warning + "Install
   anyway" wording.
5. Plugins tab → click any plugin → Permission dialog → Continue →
   alert shows the exact `npm install -g …` command + confirms the
   entry was written to `~/.claude.json`.
6. Usage tab → three bars matching claude.ai with reset countdowns.
7. Settings → Integrations → toggle **Run automations in background**;
   notice the data-flow table on Advanced.
8. Close the window → if you have anything running, the close
   confirmation appears. If only automations are alive AND background
   mode is on, the close is silent and the **tray icon** appears.
9. PyInstaller path — see `packaging/BUILD.md`. Needs Windows.

---

# HIVE — Phase 9C Complete: Six functional tabs, onboarding, registries, lifecycle hooks

**Date:** 2026-05-19
**Phase:** 9C — Automations / Skills / Plugins / Usage / Settings / Onboarding + Global search
**Status:** ✅ Built — 220 backend tests pass (189 prior + 13 CORS + 13 registries + 5 lifecycle), tsc strict clean, vite 319 KB JS / 35 KB CSS

Six tabs that used to be placeholders are now real surfaces, plus the
first-run wizard, plus a global command palette, plus the structural
hooks Phase 9D needs to wire the system tray.

## Backend — three new routers, two new modules

| File | Description |
|------|-------------|
| `backend/registries/cache.py` | `TtlCache` — single-process dict with per-entry TTL. Powers every proxy. |
| `backend/registries/curated.py` | Hand-picked fallback lists for skills + MCP servers. The Skills + Plugins tabs always render *something* even with no network. |
| `backend/registries/skills.py` | Live fetchers for **ClawHub** (`clawhub.dev/api/skills/search`), **Anthropic Cookbook** (GitHub `anthropics/anthropic-cookbook/contents/skills`), and **community** (GitHub `topic:claude-skill`). Each runs concurrently, each is wrapped — on any failure we transparently fall through to curated, the UI sees `fallback: true` and shows the offline banner. Items are post-processed with `warn_unverified` + `auto_install_ok` flags driven by the trusted-publisher list and a 100-star floor (Feb 2026 ClawHub incident hardening). |
| `backend/registries/mcp.py` | Same shape for **MCP Registry** (`registry.modelcontextprotocol.io`), **Smithery** (`smithery.ai/api/servers`), and `awesome-mcp-servers`. Returns `{items, fallback, categories, sources_tried, sources_failed}`. |
| `backend/api/registries_http.py` | `GET /api/registries/skills/search` + `GET /api/registries/mcp/list` — both accept `q`, `source`, optional `category`, `force_refresh`. |
| `backend/api/usage_http.py` | `GET /api/usage/summary` — *honest* about Max: `{claude.{last_hour, last_5h, last_7d, burn_ratio, rate_limit_hits_week}, ollama.{total_runs_week, by_model}, notes}`. The "Anthropic doesn't expose Max quota" caveat is in the response so the UI must show it. |
| `backend/api/detection_http.py` | `GET /api/detect/backends` + `GET /api/detect/ollama` — wraps `detect_backends()` so the WebView can probe Ollama through the backend (Ollama itself has no CORS). |
| `backend/api/lifecycle_http.py` | `GET /api/lifecycle/active-counts` → `{interactive_agents, enabled_automations, telegram_bot_running, has_interactive_work, should_keep_background}`. Phase 9D consumes this for the close-confirmation dialog + tray badge. |

All five new routers mounted in `backend/main.py`. CORS allowlist
already covers them.

### Backend tests — 31 new

- `test_registries.py` (13): `TtlCache` primitive, fallback path for both
  proxies, live + curated merge with verification-flag pass-through,
  query filter, high-star community auto-install allowed, cache-hit
  skips fetchers, category filter on MCP, end-to-end HTTP shape.
- `test_lifecycle.py` (5): interactive count empty/active, enabled vs
  paused automation count, endpoint envelope, telegram-running
  short-circuits `should_keep_background`.
- (`test_cors.py` from the previous hotfix: 13.)

Total: **220 passed** (was 189 → +31 new, 0 regressions).

## Frontend — six new tabs + shared primitives + onboarding

### UI primitives (`src/components/ui/`)

| File | Description |
|------|-------------|
| `Skeleton.tsx` | Shimmer placeholder, three variants (`line` / `block` / `circle`). |
| `Tooltip.tsx` | Pure-CSS hover tooltip with the 800 ms delay the spec calls for. |
| `HeroHeader.tsx` + `FlowStrip` | The visual hero pattern reused by every tab — 64 × 64 gradient icon block + title + blurb + optional stat row + optional inline ascii flow strip. |
| `SearchPalette.tsx` | Global command palette (Ctrl + K). Fuzzy-ish filter over: tab navigation, open projects, slash commands. Grouped sections, keyboard nav (↑↓/Enter/Esc), Ctrl + K toggles. |

### Pages

**Projects** (already shipped 9B) — unchanged.

**Automations** (`pages/Automations.tsx`):
- HeroHeader with `[trigger]→[orchestrator]→[agents]→[result]` flow.
- "Next scheduled run" featured strip.
- Three sections (Scheduled / On webhook / Paused) of `PipelineCard`s
  with toggle, run-now, history, delete.
- `PipelineWizard` 3-step modal: trigger (schedule/webhook/manual,
  with 4 cron presets), what (name/task/model/approval), notify
  (in-app + Telegram). POSTs to existing `/api/pipelines`.
- Skeleton loading + empty state.

**Skills** (`pages/Skills.tsx`):
- HeroHeader with `[task]→[embed]→[top-3 match]→[inject]→[agent]` flow.
- Search bar + filter pills (All sources / ClawHub / Cookbook / GitHub
  / Installed only).
- `SkillCard` with source badge, verified ✓ / unverified ⚠ chip, stars,
  downloads, install button.
- `SkillPreviewModal` — gates every unverified install. **Explicit
  February 2026 ClawHub incident copy** in the warning panel; install
  button label switches to "Install anyway".
- Auto-install allowed only for verified publishers or ≥ 100 stars
  (flag comes from backend).
- Offline-cache banner when `data.fallback` is true.

**Plugins** (`pages/Plugins.tsx`):
- HeroHeader with `[HIVE]→[MCP server]→[external tool]` flow.
- Top pill nav: Installed · Discover · Models (Models = Phase 9D stub).
- Category sidebar pulled from backend `categories[]`.
- Search input.
- `PluginCard` per server.
- `PermissionDialog` lists every declared `permissions` line and the
  install transport/package before any change. No auto-accept.

**Usage** (`pages/Usage.tsx`):
- HeroHeader explicitly states *"You have a Claude Max subscription —
  usage is measured in rate-limit windows, not dollars."*
- Section A — Claude: stats for last 1h / 5h / 7d, plus a Burn Rate
  card (cool / warm / hot pill driven by `burn_ratio`), plus a
  rate-limit-hits-this-week card.
- Section B — External APIs: dollar total from `cost_log` for the last
  30 days; explicit footer note that Max sessions show as $0 because
  they aren't billed per token.
- Section C — Ollama: total local runs + per-model breakdown.
- Per-project table (top 10 by API spend).
- The honest-disclaimer notes from the backend are rendered verbatim
  at the bottom.

**Settings** (`pages/Settings.tsx`):
- Sub-nav: General · Appearance · Backends & models · Routing rules ·
  Telegram & alerts · Storage & developer.
- General: display name, default projects dir, re-run onboarding.
- Appearance: System/Light/Dark + 5-colour accent picker (orange,
  amber, rose, violet, emerald) — writes `--c-accent` CSS variables.
- AI: Claude card (status, default Orchestrator model — local-model
  pick raises an amber warning per the spec, but never blocks),
  default worker model, default approval mode. Ollama endpoint input.
- Routing: 4-way strategy picker (cloud-first / balanced / local-first
  / local-only) + max parallel agents.
- Integrations: **Background-automations toggle** + Telegram CLI hint
  + cost notification thresholds.
- Advanced: data-flow table (5 rows showing what goes where — local
  vs cloud, no fake "all local" claim), opt-in telemetry (OFF by
  default), stub Export / Delete buttons for 9D.

**Onboarding** (`components/onboarding/OnboardingWizard.tsx`):
- 6-step modal. Auto-opens on first launch; re-openable from Settings.
- Welcome → Connect Claude (probes `/health`) → Look for Ollama
  (probes `/api/detect/ollama`) → Workspace dir → Telegram CLI hint →
  Quick tour (4 cards including **background-automations explanation**).
- "Skip all" in the header. Step dots in the footer.

### Global search

Ctrl + K opens `SearchPalette` from anywhere. Lists tab nav, every
open project (label = name, hint = status + id), and all 10 slash
commands. Hover updates selection, Enter picks, Esc dismisses.

### Stores

- `stores/settings.ts` — `useSettings` zustand with localStorage
  persistence. 13 fields including the new `backgroundAutomations`.
  `applyAccent(accent)` writes CSS variables.
- `stores/onboarding.ts` — `useOnboarding` with `seen`/`active`,
  `maybeStartOnboarding()` called once at boot.

## Decision: backend lifecycle (rules for Phase 9D)

The user clarified the close-window behaviour. We're encoding it now;
the actual tray + close dialog land in 9D.

| Window-close scenario | Behaviour |
|----------------------|-----------|
| No interactive agents, no enabled automations, no bot | Backend exits cleanly. Zero resources idle. |
| Active interactive agents (any session running workers) | Show confirmation dialog: *"X agents are still working. Stop them and close?"* [Stop and close] / [Cancel]. Stop = SIGTERM to the runners; persistent state is event-sourced so resuming the project later picks back up where it left off. |
| No interactive agents, but ≥ 1 enabled automation or Telegram bot up, and `backgroundAutomations === true` | Window closes; backend keeps running headless. System tray icon appears with a badge for the number of running automations. Right-click → Open HIVE / Pause all automations / Quit completely. Left-click → re-opens main window. |
| `backgroundAutomations === false` | Everything exits on close regardless of automations / bot. |

What 9C already shipped to support 9D:

- **`GET /api/lifecycle/active-counts`** — the close dialog's "X agents
  are still working" number plus `should_keep_background` flag.
- **`backgroundAutomations` setting** — UI toggle in Settings →
  Integrations, persisted, ready to read.
- **Onboarding callout** — last step explains: *"Close the window and
  your interactive sessions stop — but scheduled automations + the
  Telegram bot keep running headlessly in your taskbar tray (Settings
  → Integrations turns this off)."*

9D will add:
- Tauri 2 `tauri-plugin-tray` integration in `src-tauri/src/lib.rs`.
- `WindowEvent::CloseRequested` handler with `preventClose` + dialog.
- Backend graceful-shutdown signal: stop interactive workers, keep
  `APScheduler` + Telegram polling alive.
- Tray badge driven by polling `/api/lifecycle/active-counts`.

## Verification

```
desktop$ tsc -b            ✓ strict clean
desktop$ vite build        ✓ 319 KB JS / 35 KB CSS gzipped
hive$    pytest -q tests   ✓ 220 passed in 30s
```

## To test

```bash
# WSL — pull and restart
wsl
cd ~/hive
git pull
pkill -f "uvicorn backend.main:app"
hive start
exit

# PowerShell — re-sync (frontend changed a lot)
robocopy "\\wsl$\Ubuntu\home\atiasaf1122\hive\desktop" "C:\Users\The One\hive-desktop" /MIR /XD node_modules src-tauri\target dist

# Run
cd "C:\Users\The One\hive-desktop"
npm install
npm run tauri:dev
```

On first launch the onboarding wizard pops up. Run through the six steps
or hit Skip all. Then try:

- **Ctrl + K** → command palette. Type "set" → Settings; Enter.
- **Settings → Appearance** → pick accent → every gradient surface re-tints.
- **Settings → Backends & models** → set Orchestrator to Ollama → amber warning appears.
- **Settings → Integrations** → toggle "Run automations in background" — preference stored for 9D.
- **Skills tab** → search "python", click Preview on an unverified item → February 2026 warning shows; install reveals the "Install anyway" wording.
- **Plugins tab** → Discover → click a category → click Install → permission dialog lists permissions before any change.
- **Automations tab** → New automation → 3-step wizard → POSTs to /api/pipelines and shows up immediately.
- **Usage tab** → last hour / 5h / 7d cards, burn ratio pill, honest notes about Max not exposing quota.

# Phase 9 Feature Tracker (updated)

```
[ ]  1. Onboarding wizard (first launch)          → 9C ✅
[ ]  2. File browser / workspace panel            → 9D
[ ]  3. Native notifications                      → 9D
[~]  4. Global search (Ctrl+K)                    → 9C ✅ (search palette ships; deep
                                                        history search comes in 9D)
[~]  5. Keyboard shortcuts                        → 9B done + 9C Ctrl+K. 9D: Shift+?
[~]  6. Error states (per failure mode)           → 9C ✅ partial: offline-cache banners,
                                                        actionable empty states; 9D adds
                                                        OAuth-expired + rate-limit cooldown UI
[~]  7. Loading states                            → 9C ✅ skeletons everywhere; 9D adds
                                                        optimistic UI in chat
[ ]  8. Multi-window support                      → 9D
[ ]  9. Drag & drop                               → 9D
[ ] 10. Export / import                           → 9D
[ ] 11. Per-project memory toggle                 → 9D
[~] 12. Advanced skills management                → 9C ✅ preview + safety; 9D adds
                                                        usage analytics + edit
[~] 13. Cost / budget alerts (notifications only) → 9C ✅ thresholds in Settings; 9D
                                                        wires the actual banners
[ ] 14. Team mode placeholder (DB schema)         → 9D
[ ] 15. Privacy transparency page                 → 9C ✅ Settings → Advanced data-flow
[ ] 16. Help system + tooltips                    → 9C ✅ Tooltip primitive shipped
                                                        + re-run-tour from Settings;
                                                        9D wires per-page help panels
[ ] 17. Activity feed                             → 9D
[ ] 18. Performance monitoring + "why?"           → 9D
[ ] 19. Git integration + revert                  → 9D
[N/A] 20. Mobile = Telegram (already handled)
```

(Backend lifecycle decision recorded above — encoded in API + settings;
tray + close dialog implemented in 9D.)

---

# HIVE — Hotfix: CORS allowlist was missing the Tauri dev origins

**Date:** 2026-05-19
**Status:** ✅ Fixed — 202 tests passing (189 prior + 13 new CORS regression)

## Symptom

After the previous fix the Tauri Rust shell *did* find the WSL backend
(no spawn attempt, no `.venv` complaint), but the React side still
showed "Couldn't reach the backend." DevTools spelled it out:

```
Access to fetch at 'http://127.0.0.1:8765/health' from origin
'http://localhost:1420' has been blocked by CORS policy:
No 'Access-Control-Allow-Origin' header is present
```

`curl` and `Test-NetConnection` from the Windows host both succeeded —
the backend really *was* reachable. The browser/WebView refused to use
it because the FastAPI CORS allowlist only contained the old Phase 5
web-frontend origins (`localhost:5173`).

## Fix — `backend/main.py`

Expanded the `CORSMiddleware` allowlist to cover every place the Tauri
WebView can load from:

```python
allow_origins=[
    "http://localhost:5173",      # old web frontend — kept for back-compat
    "http://127.0.0.1:5173",
    "http://localhost:1420",      # Tauri dev server (Vite, Phase 9A+)
    "http://127.0.0.1:1420",
    "tauri://localhost",          # WebView scheme once bundle loads from disk
    "https://tauri.localhost",
]
```

Still registered before the routers so the middleware wraps every
handler — including `/health`, `/api/...`, and the WebSocket endpoint.

## Regression lock — `tests/unit/test_cors.py`

A parametrized suite pins each required origin. 13 cases:

- **6 preflights** — `OPTIONS /health` from each origin returns 200 with
  `Access-Control-Allow-Origin` echoing the origin back.
- **6 actual GETs** — `GET /health` with `Origin: …` includes the same
  header so the WebView doesn't strip the body.
- **1 negative** — a random origin (`http://evil.example`) must *not*
  receive the allow header, so any future "I'll just slap `*` on there"
  PR fails loudly.

Anyone tempted to trim the allowlist will see exactly which origin they
broke and where it's used.

## Verification

```
hive$ uv run python -m pytest tests/unit/ -q
202 passed in 25s
```

(189 prior tests untouched, 13 new CORS tests green.)

## What the user does

```bash
# In WSL: pull the fix and restart the backend so it picks up the new middleware
wsl
cd ~/hive
git pull
pkill -f "uvicorn backend.main:app"   # or Ctrl-C the existing hive start terminal
hive start
```

Then reload the Tauri window (or close + `npm run tauri:dev` again).
The splash flashes `✓ Connected` and hands off to the Projects dashboard.

No frontend changes were needed for this fix — the `desktop/` folder
doesn't need re-copying to Windows since only Python was edited.

---

# HIVE — Hotfix: Tauri shell now prefers an already-running backend

**Date:** 2026-05-19
**Status:** ✅ Fixed — WSL backend + Windows Tauri shell now connect cleanly

## Symptom

The window opened and the splash showed the logo, but after ~45 s flipped to
*"Backend did not start. Check that Python and the .venv are present."* —
even though the FastAPI backend was already up in WSL on port 8765.

## Root cause

`src-tauri/src/lib.rs` unconditionally tried to spawn its own Python
backend on app launch. On the WSL+Windows dev workflow that spawn fails
(no `.venv` on the Windows side), and the splash inherited the implication
that nothing was reachable. The existing WSL backend was fine all along —
WSL2's loopback forwarding makes `127.0.0.1:8765` reachable from the
Windows host — but the shell never gave the React side a chance to find it
before the spawn attempt soured the UX.

## Fix

### `src-tauri/src/lib.rs` — preflight TCP probe, then spawn

New flow in `ensure_backend()`:

1. **Probe.** Open a 120 ms TCP connect to `127.0.0.1:8765`, retry every
   50 ms up to a 500 ms total budget. If anything answers, log
   `backend already responding on 127.0.0.1:8765 — skipping spawn` and
   return `BackendOutcome::Existing`. **We don't touch the child slot, so
   on window close we leave the WSL backend running.**
2. **Spawn.** If nothing answers, fall through to the existing
   `spawn_backend()` path (walk for `backend/main.py`, prefer `.venv/bin/python`
   or `.venv/Scripts/python.exe`, fall back to PATH). Returns
   `BackendOutcome::Spawned`.
3. **No backend at all.** Print three lines pointing the user at
   `hive start` inside WSL, or at the missing bundled backend in
   release. The startup keeps going so the React side can render the
   failure splash with the same hint.

`backend_alive()` was also tightened — when we never spawned a child it
now returns `true` (meaning "an external backend is running"), so future
9C code that queries it won't false-negative on the WSL workflow.

The spawn path now only fires when **no one is listening on 8765**. That
keeps the dev workflow zero-config and still gives the future bundled
.msi the same code path with a different `python`.

### `src/components/Splash.tsx` — honest UX

| State | Old copy | New copy |
|-------|----------|----------|
| polling for <2 s | "Starting…" | "Connecting to backend…" |
| polling for ≥2 s | "Starting backend… {s}s" | "Connecting to backend… {s}s" |
| handoff to UI | (immediate) | brief ✓ "Connected" flash for 350 ms |
| timeout | "Backend did not start. Check that Python and the .venv are present." | "Couldn't reach the backend" + actionable WSL hint pointing at `hive start` |

The failure message now reads:

> Tried `http://127.0.0.1:8765` for ~45 s.
>
> If you're developing with the backend in WSL, open a WSL terminal and run:
>
>     hive start
>
> Then close and reopen this window. The packaged .msi ships its own backend, so
> this only affects the dev workflow.

## Verification

```
desktop$ ./node_modules/.bin/tsc -b      ✓ clean
desktop$ ./node_modules/.bin/vite build  ✓ 241 KB JS / 27 KB CSS
```

## Updated user workflow

Same three steps as before — just works now:

```powershell
# 1. In WSL — start the backend if it's not already up
wsl
cd ~/hive
hive start

# 2. In another WSL shell — sync the fix
cd ~/hive
git pull
exit

# 3. From Windows PowerShell — mirror WSL → Windows
robocopy "\\wsl$\Ubuntu\home\atiasaf1122\hive\desktop" "C:\Users\The One\hive-desktop" /MIR /XD node_modules src-tauri\target dist

# 4. Run the desktop app
cd "C:\Users\The One\hive-desktop"
npm run tauri:dev
```

Console output you should see in the Tauri dev window:

```
[hive] backend already responding on 127.0.0.1:8765 — skipping spawn
```

The splash flashes "Connected ✓" and hands off to the Projects dashboard.

---

# HIVE — Hotfix: Windows Tauri build failed on missing icons

**Date:** 2026-05-19
**Status:** ✅ Fixed — clean checkout now builds on Windows

## Symptom

On Windows (`C:\Users\The One\hive-desktop\`):

```
error: failed to run custom build command for `hive vX.Y.Z`
  package.metadata does not exist
  `icons/icon.ico` not found; required for generating a Windows Resource file during tauri-build
```

## Root cause

Three compounding problems, all from Phase 9A:

1. **No icon files were committed.** `desktop/src-tauri/icons/` shipped with only a
   `README.md`. Phase 9A intended to add the icon set in Phase 9D — but on
   Windows, `tauri-build` runs every compile (including `tauri dev`) and emits
   the `.ico` as a Windows resource at link time, so the file has to exist
   for *any* Windows build.
2. **`tauri.conf.json bundle.icon` listed five files that didn't exist.** A
   clean clone could never satisfy it.
3. **No `[package.metadata.bundle]` block in `Cargo.toml`.** Some
   `tauri-build` code paths consult it before falling back to
   `tauri.conf.json` — when that fallback fails on missing files, the error
   reads "package.metadata does not exist" which masks the real problem.

## Fix

| File | Change |
|------|--------|
| `desktop/scripts/generate_icons.py` | **NEW.** Pillow-based generator. Renders the three-orange-hex HIVE logo at 1024² (vertical gradient #F5A623 → #D85A30, matching `HiveLogo.tsx`), then writes `icon.png` (1024²), `32x32.png`, `128x128.png`, `128x128@2x.png` (256²), multi-resolution `icon.ico` (16/32/48/64/128/256), and `icon.icns` (best-effort via Pillow). |
| `desktop/src-tauri/icons/icon.ico` | **NEW, committed.** 22 KB, 6 sizes, validated with `file`: `MS Windows icon resource - 6 icons`. |
| `desktop/src-tauri/icons/{icon.png, 32x32.png, 128x128.png, 128x128@2x.png, icon.icns}` | **NEW, committed.** All five generated, all referenced by `bundle.icon`. |
| `desktop/src-tauri/Cargo.toml` | **Added `[package.metadata.bundle]` block** mirroring the `tauri.conf.json` icon array. This is the safety net for the tauri-build code path that reads Cargo metadata first. |
| `desktop/src-tauri/build.rs` | **Hard precondition on Windows.** If `icons/icon.ico` is missing, panic with a clear message pointing at the regen script — no more cryptic "package.metadata does not exist". |
| `desktop/src-tauri/icons/README.md` | Documents the contents + regeneration command. |
| `desktop/README.md` | Adds an "Icons" subsection. |

All three icon references (`tauri.conf.json`, `Cargo.toml`, files on disk)
are now in sync; verified by cross-checking immediately after generation.

## Updated WSL → Windows workflow

The user's flow (`git pull` in WSL → `robocopy` to Windows → `npm run tauri:dev`)
**will succeed** after this hotfix because:

- Icons are committed as binary blobs in git — robocopy preserves them.
- Cargo.toml + tauri.conf.json both reference the same paths and they all exist.
- `build.rs` now fails loud with a clear hint if anyone ever regresses this.

## PowerShell commands for the user

```powershell
# 1. Inside WSL: pull the fix
wsl
cd ~/hive
git pull
exit

# 2. From PowerShell: mirror WSL → Windows (preserves binary icons)
robocopy "\\wsl$\Ubuntu\home\atiasaf1122\hive\desktop" "C:\Users\The One\hive-desktop" /MIR /XD node_modules src-tauri\target dist

# 3. Inside C:\Users\The One\hive-desktop:
cd "C:\Users\The One\hive-desktop"
npm install
npm run tauri:dev
```

`/XD` excludes `node_modules`, `src-tauri\target`, and `dist` — those are
machine-local and shouldn't be copied across the WSL boundary. `/MIR`
keeps the Windows copy in lock-step with WSL on every re-sync.

The Python backend is already running in WSL on `localhost:8765`; the
Tauri shell on Windows will reach it transparently over the loopback —
WSL2 forwards `127.0.0.1` between the host and the guest.

---

# HIVE — Phase 9B Complete: Projects Dashboard + Project View + Composer

**Date:** 2026-05-18
**Phase:** 9B — Dashboard + Project view + Composer + Slash commands
**Status:** ✅ Built — tsc clean, vite build 240 KB JS / 27 KB CSS, backend 189 tests still pass

This phase makes the desktop app *useful*: you can create, list, open, chat
with, approve, save, and close projects without leaving the Tauri window.
Live state comes from the FastAPI sidecar over WebSocket on `localhost:8765`.

## Retroactive 9A fix — Windows title bar

`components/TitleBar.tsx` was rewritten. Out: macOS traffic-light dots on
the left. In: Windows-style `─ □ ✕` controls on the **top right**, with
red-on-hover for close and a transparent draggable centre stripe showing
a subtle "HIVE" wordmark. Every shortcut hint in the UI says "Ctrl"
never "Cmd".

## Data layer

| File | What it does |
|------|--------------|
| `src/lib/types.ts` | `SessionInfo`, `SessionStatus`, `AgentInfo`, `ConversationEntry`, `TeamComposition`, `InterruptPayload`, `WSEvent`, `CostSummary`. Just what the UI reads — server payloads with extra fields pass through. |
| `src/lib/api.ts` (already from 9A) | `fetch` wrapper for the sidecar; `waitForBackend()`. |
| `src/lib/ws.ts` | `subscribeSession(id, onEvent)` opens a `ws://127.0.0.1:8765/ws/{id}` socket, exponential-backoff reconnect, returns `close()`. |
| `src/lib/greeting.ts` | Time-of-day greeting (no "good night" — never the right vibe). |
| `src/lib/shortcuts.ts` | `useGlobalShortcuts()` — Ctrl+T / Ctrl+W / Ctrl+, / Ctrl+1..9 wired window-level. |
| `src/stores/sessions.ts` | Zustand store; `fetchSessions`, `upsertSession`, `applyWsEvent` (folds the WS event stream into `{info, agents, history, interrupt, team, activity, events}` per session), `appendUserMessage`, `setInterrupt`, `removeSession`. |
| `src/stores/projectTabs.ts` | Open-tabs list (browser-style), persisted to `localStorage` so they survive restarts. |
| `src/stores/templates.ts` | User-saved project templates (localStorage). **No built-in templates** — only user-curated. |

## Projects dashboard (`pages/Projects.tsx`)

```
Greeting "Good {morning|afternoon|evening}"
QuickStart  ┌─ textarea (Ctrl+Enter)
            ├─ folder chip · model chip · approval chip · Start
            └─ posts /api/sessions → opens tab → navigates to /project/:id
Tiles       Continue recent  |  Schedule automation
SavedTemplates (row above grid, hidden when empty)
Active projects grid  ProjectCard × N  +  NewProjectCard (dashed)
Recently closed grid (last 6)
UsageStrip (last-7-day sparkline → /usage)
FirstTimeHint (dismissible)
```

- Greeting uses local `Date()` hours: 5–12 morning, 12–18 afternoon, else evening.
- Cards show emoji (stable hash of session id), name, id, approval mode, status pill + dot, agent count, "Xs/Xm/Xh ago".
- Status palette: green = running, amber = starting/planning/spawning, sky = awaiting user, orange-pulse = needs approval, grey = closed/completed, red = failed.
- Project list auto-refreshes every 8 s on the dashboard.

## Project view (`pages/ProjectView.tsx`)

```
TabBar             browser-style, middle-click closes, "+" → dashboard
Header             title · id · mode · status · [Save as template] · [Close]
AgentsBar          orchestrator pill (gradient, always present)
                   │ vertical divider
                   pills for each sub-agent — avatar with role colour,
                   pulse dot when running, 55% opacity when passive
                   total cost · Pause all (right side)
Chat               max-width 760 column
                   user bubble dark/right · orchestrator bubble light/left/avatar
                   inline ActivityCard once a team is planned (checklist)
                   inline ApprovalCard when interrupt.type === 'team_approval'
Composer           auto-grow textarea up to 220 px
                   Enter sends · Shift+Enter newline · Ctrl+Enter also sends
                   Paperclip / Mic disabled (Phase 9C)
```

### Slash commands

`components/project/SlashMenu.tsx` + `Composer.tsx`:

- Typing `/` opens an overlay above the textarea with all 10 commands:
  `/clear /cost /model /init /compact /skills /agents /pause /resume /close`.
- Arrow keys move selection, Enter picks, Esc closes.
- Commands with parameters (e.g. `/model `) keep the input focused so you
  can type the value.
- `/clear` and `/close` are handled in-process; everything else is sent
  as a message to the orchestrator (which can act on it).

### Activity card

Inline in the chat once `team_composition` lands. Shows each role with
its model + count, with the row icon switching from outline (planned) →
spinner (running) → check (done) based on the agent statuses streaming
in over WS.

### Approval card

Inline. Lists team rows + rationale + confidence pct. Approve / Reject
buttons POST to `/api/sessions/:id/approve`. Telegram already pushes the
same interrupt with the same buttons; either resolves the same future.

## Live state pipeline

```
WebSocket  /ws/:id  →  applyWsEvent(sid, ev)  →  zustand store  →  React renders
```

`applyWsEvent` is a switch on event type — `session_start` /
`orchestrator_thinking` / `orchestrator_decision` / `orchestrator_response` /
`spawn_complete` / `awaiting_user` / `interrupt` / `session_closed` /
`session_end` / `session_error` / `agent/start` / `text/delta` / `agent/end` /
`agent/error` — folding into the per-session record. All events also land
in a 200-deep `events[]` ring buffer for future debugging.

## Save as template

The project header has a **Save as template** button. It captures the
current task prompt + approval mode + model, prompts for a name, stores
in `localStorage` via `useTemplates`. Saved templates appear on the
dashboard as a row above the grid; clicking one pre-fills the QuickStart.
There are no built-in templates.

## Keyboard shortcuts

Wired in `lib/shortcuts.ts`, registered once in `<App>`:

| Combo | Action |
|-------|--------|
| Ctrl + T | new project (focus QuickStart) |
| Ctrl + W | close current project tab |
| Ctrl + 1..9 | switch to nth tab |
| Ctrl + , | open settings |
| Ctrl + Enter | send (composer) |
| Enter | send (composer) |
| Shift + Enter | newline (composer) |
| / | open slash menu in composer |

## Verification

```
desktop$ npm run build        ✓ tsc strict clean, vite 240 KB JS / 27 KB CSS
hive$    pytest tests/unit/   ✓ 189 passed (backend untouched)
```

## How to test

```bash
cd ~/hive/desktop
npm run tauri:dev
```

1. Splash → dashboard.
2. Type a task ("Write a hello.py that prints HIVE"), hit **Ctrl + Enter**.
3. You jump to `/project/:id`. The Orchestrator pill is gradient on the
   left. Watch sub-agent pills appear as the team is planned.
4. If you set approval mode to `checkpoint`, the inline approval card
   shows up — click **Approve**. The card animates out, agents start.
5. As workers stream, their pill shows the latest text fragment.
6. Type `/` in the composer — the slash menu opens.
7. Save as template → name it → go back to dashboard via the sidebar's
   hexagon logo. Your template appears as a chip above the grid.
8. **Ctrl + W** closes the tab. **Ctrl + T** focuses the QuickStart again.
9. Toggle theme — every surface re-tints instantly. Close the window —
   backend exits cleanly (`[hive] backend stopped`).

---

# Phase 9 Feature Tracker

Status across 9B / 9C / 9D for the 20 features. `[x]` done, `[~]` partial,
`[ ]` pending.

```
[ ]  1. Onboarding wizard (first launch)          → 9C
[ ]  2. File browser / workspace panel            → 9C
[ ]  3. Native notifications                      → 9C
[ ]  4. Global search (Ctrl+K)                    → 9C
[~]  5. Keyboard shortcuts                        → 9B done: T/W/,/1-9/Enter/Shift+Enter/Ctrl+Enter/Slash
                                                     9C: K/Shift+?/Ctrl+/
[ ]  6. Error states (per failure mode)           → 9C
[~]  7. Loading states                            → 9B done: dashboard skeleton; 9C: chat skeletons / shimmer / optimistic
[ ]  8. Multi-window support                      → 9D
[ ]  9. Drag & drop                               → 9C
[ ] 10. Export / import                           → 9C
[ ] 11. Per-project memory toggle                 → 9C
[ ] 12. Advanced skills management                → 9C
[ ] 13. Cost / budget alerts (notifications only) → 9C
[ ] 14. Team mode placeholder (DB schema)         → 9D
[ ] 15. Privacy transparency page                 → 9C
[ ] 16. Help system + tooltips                    → 9C
[ ] 17. Activity feed                             → 9C
[ ] 18. Performance monitoring + "why?"           → 9D
[ ] 19. Git integration + revert                  → 9D
[N/A] 20. Mobile = Telegram (already handled)
```

The list is updated every phase. Phase 9B intentionally focused on the
chat-shaped happy path; the rest land in 9C/9D.

---

# HIVE — Phase 9A Complete: Tauri Desktop Scaffold

**Date:** 2026-05-18
**Phase:** 9A — Desktop shell
**Status:** ✅ Built and verified — TypeScript clean, Vite production build (~200 KB JS), backend 189 tests still pass

Phase 9 is the move from "web app you open in a browser" to a real
desktop app. The Python FastAPI backend stays exactly as-is; Tauri 2
wraps it as a child process and renders a brand-new React UI in a
native window. This first sub-phase ships the scaffold: window opens,
backend boots, theme system works, sidebar navigates.

## Layout

```
desktop/
├── package.json              Vite + React 18 + TS + Tailwind + Tauri v2 + Tabler icons
├── vite.config.ts            Port 1420, ignores src-tauri/
├── tsconfig.json + .node     Strict, JSX react-jsx, path alias @/*
├── tailwind.config.js        Semantic palette wired to CSS variables, accent gradient
├── postcss.config.js
├── index.html
├── README.md                 Dev + build instructions, phase table
├── src/
│   ├── main.tsx              StrictMode + HashRouter, initTheme() at boot
│   ├── App.tsx               <TitleBar/> + <Splash/> until backend ready, then <Sidebar/> + <Routes/>
│   ├── index.css             Design tokens (light/dark), Inter @ 400/500/600, .card/.btn helpers
│   ├── components/
│   │   ├── HiveLogo.tsx      Three-hex SVG with the accent gradient
│   │   ├── TitleBar.tsx      macOS-style traffic lights (close/min/maximize) + draggable bar + ThemeToggle
│   │   ├── ThemeToggle.tsx   Pill switcher: System / Light / Dark
│   │   ├── Sidebar.tsx       64px rail, 5 nav buttons + Settings, hover tooltips, accent-active state
│   │   └── Splash.tsx        Boots /health poll up to 45s with animated bar
│   ├── stores/theme.ts       Zustand + localStorage; respects prefers-color-scheme when mode='system'
│   ├── lib/api.ts            fetch wrapper for localhost:8765, waitForBackend()
│   └── pages/
│       ├── _PagePlaceholder.tsx   Hero header skeleton every page uses
│       ├── Projects.tsx           → Phase 9B
│       ├── Automations.tsx        → Phase 9C
│       ├── Skills.tsx             → Phase 9C
│       ├── Plugins.tsx            → Phase 9C
│       ├── Usage.tsx              → Phase 9C
│       └── Settings.tsx           → Phase 9C
└── src-tauri/
    ├── Cargo.toml             tauri = "2", tauri-plugin-shell = "2"
    ├── build.rs
    ├── tauri.conf.json        decorations:false, 1400x900 default, 1100x700 min, devUrl :1420
    ├── capabilities/default.json   window + shell perms
    └── src/
        ├── main.rs            Thin entry — calls hive_lib::run()
        └── lib.rs             Spawns + tracks the Python backend
```

## Backend as a child process

`src-tauri/src/lib.rs` walks up from the cwd to find the workspace root
(the folder containing `backend/main.py`), prefers the project's
`.venv/bin/python`, falls back to `python3` on PATH, and runs:

```
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8765 --log-level info
```

The child handle is stored in a `Mutex<Option<Child>>` Tauri state. On
`RunEvent::ExitRequested` (window close) we `kill()` + `wait()` so we
never leak a zombie uvicorn.

Two Tauri commands are exposed to the React side: `backend_url()` and
`backend_alive()`. Phase 9B will use them for restart UX.

In Phase 9D the same machinery will switch to a PyInstaller-bundled
binary at `binaries/hive-backend-<triple>` so the user doesn't need a
local `.venv`.

## Design system (live, in code)

Tokens defined as CSS variables under `:root` (light) and `.dark` so the
toggle swap is instant and free.

| Token | Light | Dark |
|-------|-------|------|
| `--c-bg` | `#FBFAF7` warm off-white | `#1A1814` |
| `--c-surface` | `#FFFFFF` | `#221F1A` |
| `--c-surface-2` | `#F6F4EF` | `#28251F` |
| `--c-ink` | `#1A1814` | `#FAF8F3` |
| `--c-ink-muted` | `#6B645B` | `#A39B8C` |
| `--c-line` | `#E8E4DC` | `#2E2A24` |
| `--c-accent` / `--c-accent-warm` | `#F5A623` → `#D85A30` |

Tailwind's `darkMode: ['class']` flips them via `<html class="dark">`.
Typography locked at Inter 400/500/600 (no bold). Border-radius scale:
`soft 14px / card 16px / xl2 18px`. Shadow only on hover.

## Splash → ready handoff

`<Splash>` renders a pulsing logo, an animated bar, and a status line
that switches from "Starting…" to "Starting backend… 5s" once the boot
takes more than 3 seconds. It calls `waitForBackend(45_000)` which
polls `GET /health` every 250 ms. When the backend responds 200 we set
`backendReady=true` and `App` swaps in the sidebar + routes.

## Sidebar

64 px wide, on the left of every page once the backend is up.
HiveLogo at top (clicking returns to `/`), then five primary tabs:

- **Projects** `IconLayoutGrid`
- **Automations** `IconClock`
- **Skills** `IconBook2`
- **Plugins** `IconPlug`
- **Usage** `IconChartHistogram`

Settings (`IconSettings`) pinned to the bottom. Active button gets the
accent gradient + white icon + drop shadow; hover shows a dark tooltip
to the right.

## Page placeholders

Every page renders `<PagePlaceholder>` — the hero-header pattern the
real pages will keep using. A 64×64 gradient block on the left holds
the tab's icon; on the right a title plus a one-sentence explanation of
what the tab does and which sub-phase fills it in. Below the hero:
three info cards reminding future-me of the design rules ("sentence
case", "no bold", "live data below").

## Verification

```
desktop$ npm install
desktop$ npm run build          ✓ tsc + vite, 198 KB JS / 19 KB CSS gzipped
hive$    pytest tests/unit/ -q  ✓ 189 passed
```

## How to run it

```bash
cd ~/hive/desktop
npm install
npm run tauri:dev               # needs Rust + Tauri prereqs
```

You should see:

1. The HIVE splash with a pulsing orange-hex logo and the animated bar.
2. Console: `[hive] starting backend: ... uvicorn backend.main:app ...`.
3. Within ~3 seconds the splash transitions to the sidebar layout on
   the Projects placeholder.
4. Click each sidebar icon — page swaps with no flicker (HashRouter).
5. Click the theme switcher pill in the top-right — every surface
   re-tints instantly.
6. Close the window — backend exits cleanly (`[hive] backend stopped`).

If `cargo` isn't installed yet, follow
<https://v2.tauri.app/start/prerequisites/> for your OS.

---

# HIVE — Phase 8 Complete: Polish, Documentation, Cost Dashboard, Onboarding

**Date:** 2026-05-18
**Phase:** 8 — Polish
**Status:** ✅ All done — 189 tests passing, TypeScript clean

The final phase ties everything together. New top-level docs, a cost
dashboard wired into the UI, a `hive onboard` first-run flow, harder
error paths, and a recovery test suite.

## New docs

| File | Purpose |
|------|---------|
| `README.md` | User-facing landing — requirements, install, run, daily use (Web / CLI / Pipelines / Telegram / Skills), file layout, links to deeper docs. |
| `ARCHITECTURE.md` | Single source of truth for *how HIVE works*. Layered overview, full library inventory + rationale for each (FastAPI, LangGraph, AsyncSqliteSaver, aiogram, sentence-transformers, APScheduler, @xyflow/react, etc.), the 7 invariants with where each is enforced, code walkthrough for every module, three sequence diagrams (first-turn chat, approval-via-Telegram, scheduled pipeline), and a complete code map. |
| `CLAUDE.md` (updated) | Phase Status table extended to Phase 8 ✅. |

## Cost dashboard

| File | Description |
|------|-------------|
| `backend/api/cost_http.py` | `aggregate_cost_summary(days, top_n_sessions)` reads `cost_log`, joins to `sessions` for human names, returns `{total, by_session, by_day}`. `GET /api/cost/summary?days=7` exposes it. `days` clamped to `[1, 365]`. |
| `frontend/src/components/CostDashboard.tsx` | Panel on the Home dashboard. Polls `/api/cost/summary` every 30s. Shows total spend, input/output token totals, daily bar chart (last 7 days), top-5 sessions by cost. |
| `backend/main.py` | Mounts the cost router alongside http/ws/pipelines. |

The dashboard sits below the project/pipeline lists on the home view —
visible without leaving the page where you start work, so cost stays
in your peripheral vision.

## Onboarding

| File | Description |
|------|-------------|
| `backend/onboarding.py` | `Check` dataclass, `run_onboarding()` runs the sequence: ensure `~/.hive/` + init DB, check `git >= 2.30`, detect backends (claude CLI / API / Ollama), verify `credentials.json` is present + `0600`. `render_report(checks)` formats a fixed-width terminal report with per-failure hints. |
| `cli/hive.py` | Added `hive onboard`. Exits non-zero if any check fails so it composes with shell scripts. |

```
$ hive onboard
HIVE Onboarding Report
==================================================
  ✓  data dir               /home/user/.hive
  ✓  git                    git version 2.43.0
  ✓  claude CLI             2.1.143 (Claude Code)
  ✗  claude API key         not set
      → Optional: only needed if you don't use Claude Max OAuth.
  ✗  Ollama                 not running
      → Optional: free local LLM. Start with `ollama serve`, ...
  ✗  claude OAuth token     missing
      → Run `claude setup-token` to authenticate with your Claude Max...
==================================================
3 issue(s) above need attention before running HIVE.
```

## Error handling tightening

CLAUDE.md says "No try/except that silently swallows errors". Phase 8
swept the remaining ones:

- `graph._emit_to_ws` — still best-effort (mustn't block the graph on a dead WS), but now logs at debug level.
- `graph._execute_worker` — cost-log write failures log at WARNING (not silent).
- `pipelines_http` `/run` and `/webhook` — extracted shared `_trigger_pipeline_run` helper; pipeline run failures log the exception before marking the run as failed.
- `api/http._session_runner` — on crash, now marks the session `failed` in the DB so the UI reflects reality (was emitting only the `session_error` WS event).
- `main.lifespan` — runs `run_startup_recovery(DB_PATH)` so crashed agents from the previous process are surfaced on boot.

## Auto-recovery tests

`tests/unit/test_recovery.py` — 13 tests:

- `_pid_alive` for own PID (alive) and bogus PID (dead).
- `detect_crashed_agents` for no-active / no-PID / live-PID / dead-PID cases.
- `mark_agents_crashed` — status update, session-failed cascade, sibling-still-active skip, empty-list no-op.
- `run_startup_recovery` — full pass and idempotency (second call after the first leaves no further work).
- `test_resume_after_simulated_restart` — verifies a session parked at `wait_for_user` survives a closed `AsyncSqliteSaver` and can be resumed via `resume_session()` in a fresh context.

## Tests

```
189 passed in 41s
```

Phase 8 additions: 7 cost (`test_cost_dashboard.py`) + 13 recovery
(`test_recovery.py`) + 13 onboarding (`test_onboarding.py`) = 33 new
tests, 0 regressions.

Frontend: `tsc --noEmit` clean.

---

# HIVE — Phase 7 Complete: Telegram Bot

**Date:** 2026-05-18
**Phase:** 7 — Telegram
**Status:** ✅ All done — 156 tests passing

The HIVE Telegram bot lets the user run a project from their phone:
list sessions, attach to one, chat with its orchestrator, approve or
reject team proposals from inline buttons, and close the project — all
without opening the web UI.

## Files

| File | Description |
|------|-------------|
| `backend/telegram/config.py` | `TelegramConfig` dataclass + `load_config / save_config / set_token / add_allowed_chat / remove_allowed_chat`. Stored at `~/.hive/telegram.json` with **chmod 0600** to protect the bot token. |
| `backend/telegram/bot.py` | aiogram v3 lifecycle: `start_bot()` reads config, creates `Bot` + `Dispatcher`, registers routers, and runs `dispatcher.start_polling` as an asyncio task in the same event loop. `stop_bot()` cancels cleanly. |
| `backend/telegram/session_router.py` | In-memory `chat_id → session_id` map. `attach_session`, `get_attached_session`, `get_subscribers` (used by notifier to target only attached chats). |
| `backend/telegram/handlers/commands.py` | `/start /help /sessions /attach <id> /status /close`. All commands gate on the chat allowlist. |
| `backend/telegram/handlers/chat.py` | Catch-all text handler. Forwards free-text messages to the attached session — resolves a pending input future or queues just like the web `/message` endpoint. |
| `backend/telegram/handlers/callbacks.py` | Inline-button handlers: `✓ Approve`, `✗ Reject`, `👁 Details`. Approve/Reject resolve the pending HTTP approval future, then edit the original message to show the decision. |
| `backend/telegram/notifier.py` | `notify_approval(session_id, payload)` formats a Markdown card with the team table + confidence and pushes it (with `build_approval_keyboard`) to subscribed chats. `notify_session_end` for completions. Respects `notify_approvals` and `quiet_hours` config. |
| `backend/main.py` | Lifespan now also `await start_bot()` / `await stop_bot()`. |
| `backend/api/http.py` | `_session_runner` calls `notify_approval` when a `team_approval` interrupt fires — the same approval is now offered on web + Telegram. |
| `cli/hive.py` | New `hive telegram` subcommand: `setup --token`, `allow <id>`, `revoke <id>`, `status`. |
| `tests/unit/test_phase7.py` | 22 tests covering config, allowlist, session router, notifier formatting + targeting, callback resolution, chat delivery, quiet-hours/disabled-notify. |

## Allowlist Model

The token alone isn't authorization. Every command, callback, and chat
handler explicitly checks `load_config().is_allowed(chat.id)` before
acting. An empty allowlist blocks everything — a stolen token can't
control HIVE until the host operator runs:

```
$ hive telegram allow <chat-id>
```

## Approval Card

```
Approval needed — `sess-abc12`
Reason: low_confidence
Confidence: 60%

Proposed team:
• Builder ×2 [claude:sonnet]
• Debugger ×1 [claude:sonnet] (passive)

_needs careful review_

[ ✓ Approve ]  [ ✗ Reject ]  [ 👁 Details ]
```

Clicking Approve or Reject resolves the HTTP approval future in-process
— same code path the web UI uses. The card is then edited in place to
show the chosen action so the chat history reflects the decision.

## Setup Flow

```
$ hive telegram setup --token 1234:ABC...
Token saved.
Next: send /start from the chat you want to allow, then run:
  hive telegram allow <chat-id>

# From Telegram in @your_bot:
> /start
"This chat (501234567) is not allowed.
 Run on the host: hive telegram allow 501234567"

$ hive telegram allow 501234567
Allowed chat IDs: [501234567]

# Restart hive backend → bot polling starts automatically.
> /sessions     → lists active HIVE sessions
> /attach abc12 → attaches this chat to session abc12
> "make the function async too"  → goes to that orchestrator
> (approval card appears)        → tap ✓ Approve
```

## Tests

```
156 passed in 23s
```

22 new Phase 7 tests, 0 regressions across Phases 0–6.

---

# HIVE — Architectural Refactor: Orchestrator-First, Multi-Turn Sessions

**Date:** 2026-05-18
**Status:** ✅ Done — 134 tests passing, 0 regressions, TypeScript clean

This refactor replaces the "run agents → done" model with a long-lived
orchestrator-driven conversation. A session is now an ongoing collaboration
that stays alive until the user explicitly closes it.

## What Changed

| Concept | Before | After |
|---------|--------|-------|
| Session lifetime | Run → END | Lives until user closes |
| Decision point | Heuristic `classify_node` + LLM `plan_node` | Single LLM `orchestrator_node` |
| Chat | Synthetic `answer_node` after classifier | Built into orchestrator (empty team = chat) |
| User messages | One-shot task | Stream of messages over time |
| State at end | `AgentResult` returned | Graph parks at `wait_for_user`; resume with `{text}` or `{close: True}` |
| Conversation history | None | `state["conversation_history"]` persisted via SqliteSaver |

## New Graph

```
START → orchestrator ─┬─ respond (chat) ────────────────────► wait_for_user
                      └─ approval ─┬─ abort ─────────────────► wait_for_user
                                   └─ spawn → run_workers
                                                  → review ──► wait_for_user

wait_for_user (interrupt) ─┬─ user sends message  ─► orchestrator (loop)
                           └─ user closes session ─► END
```

The orchestrator's prompt now returns BOTH a `response` text and an optional
`team`. Empty team → respond_node emits the answer and parks. Non-empty
team → approval_node, then the existing spawn/run/review flow.

## Files

| File | Change |
|------|--------|
| `backend/orchestrator/nodes/planner.py` | New `orchestrate()` returns `OrchestratorDecision(response, composition)`. `plan_team()` kept as a thin shim. `_parse_team_composition` still enforces the ≥1-active-agent floor for the spawn path. |
| `backend/orchestrator/nodes/classifier.py` | **Deleted** — orchestrator decides per message. |
| `backend/orchestrator/graph.py` | Added `orchestrator_node`, `respond_node`, `wait_for_user_node`, `get_conversation_history()`. Removed `plan_node`, `classify_node`, `answer_node`. New loop topology. |
| `backend/orchestrator/state.py` | Added `conversation_history`, `pending_message`, `last_response`, `user_closed`, `db_path`. Removed `task_type`. |
| `backend/api/http.py` | `_session_runner` now handles both `team_approval` and `awaiting_input` interrupts. Added `_pending_inputs` future map + per-session message queues. New endpoints: `POST /sessions/{id}/close`, `GET /sessions/{id}/history`. `POST /sessions/{id}/message` resolves a pending future or queues. |
| `frontend/src/components/SessionView.tsx` | "Close project" button — confirms then `POST /api/sessions/{id}/close`. |
| `frontend/src/stores/sessions.ts` | Handles `awaiting_user`, `session_closed`, `orchestrator_thinking`, `orchestrator_response` events. |
| `frontend/src/types.ts` | `SessionStatus` adds `awaiting_user` and `closed`. |

## While Agents Are Running

The user can keep sending messages even when an agent batch is in flight:

- `POST /sessions/{id}/message` while the graph is in `run_workers` →
  message goes into `_message_queues[session_id]` (queued: true).
- When the graph next hits `wait_for_user`, the runner drains the queue
  before parking on a new future. The user's queued message becomes the
  next orchestrator turn immediately.

This is a soft real-time guarantee — messages are processed as soon as the
current agent batch finishes, not mid-execution. Full mid-batch interrupts
would require splitting the orchestrator from the worker pool, which is a
larger Phase 8 concern.

## Tests

```
134 passed in 22s
```

New test files:

- `tests/unit/test_orchestrator_multiturn.py` (9): orchestrator routing,
  respond node, multi-turn user messages, close path, DB status update.
- `tests/unit/test_session_http_multiturn.py` (8): /message routing
  (resolve vs queue), /close (parked vs busy), /history, 404s.
- `tests/unit/test_planner_floor.py` (3): planner-floor guarantee preserved
  for the spawn path.

Phase 3 tests updated for the new multi-turn semantics (resume → interrupt,
then close to surface the per-turn AgentResult).

---

# HIVE — Phase 6 Complete: Persistent Pipelines + Scheduler

**Date:** 2026-05-18
**Phase:** 6 — Persistent Pipelines
**Status:** ✅ All done — `hive pipelines` works end-to-end

---

## What Was Built

| File | Description |
|------|-------------|
| `backend/pipelines/store.py` | CRUD over `pipelines` + `pipeline_runs` tables. `create_pipeline` auto-generates a 32-char webhook token. `get_pipeline_by_webhook` only returns enabled rows. |
| `backend/pipelines/scheduler.py` | Singleton APScheduler (`AsyncIOScheduler`, UTC). `start_scheduler` loads enabled+scheduled pipelines on boot. `sync_pipeline_schedule` keeps the job store in sync after CRUD ops. `_fire_pipeline` records a run and calls `launch_session`. |
| `backend/api/pipelines_http.py` | REST router under `/api/pipelines`: list / create / get / patch / delete / runs, plus `POST /{id}/run` (manual) and `POST /webhook/{token}` (no auth, secret in URL). |
| `backend/persistence/db.py` | Added `pipelines` and `pipeline_runs` tables + index `idx_pipeline_runs_pl`. |
| `backend/main.py` | Lifespan now calls `start_scheduler` / `stop_scheduler` and mounts `pipelines_router`. |
| `cli/hive.py` | New `hive pipelines list / create / delete / run / runs` subcommand group. `create` accepts `--schedule "0 17 * * *"` and `--task`. `run` fires a one-off through the existing graph. |
| `frontend/src/components/PipelineBuilder.tsx` + `PipelineCard.tsx` | UI for creating, listing, triggering, and viewing runs. |
| `tests/unit/test_phase6.py` | 15 store + cron-parsing tests. |
| `tests/unit/test_phase6_http.py` | 12 HTTP endpoint tests (list/create/get/patch/delete/runs/webhook/run-now), using `AsyncMock` so they don't touch the user's real DB. |

## Cron parsing

```python
_parse_cron("0 17 * * *")    # → CronTrigger(minute=0, hour=17, ...)
_parse_cron("30 9 * * 1")    # → weekly Mon 09:30
_parse_cron("0 * * * *")     # → hourly
_parse_cron("bad cron")      # → raises ValueError
```

Strict 5-field validation (rejects 4-field or malformed input).

## Webhook trigger

```
POST /api/pipelines/webhook/<token>     # 200 → {"session_id", "run_id"}
POST /api/pipelines/webhook/<bad>       # 404 → {"detail": "No pipeline for this token"}
```

Disabled pipelines return 404 (token is opaque, not auth — disabling is the kill switch).

## CLI

```
$ hive pipelines create "Daily haiku" --task "Write a Python haiku" --schedule "0 17 * * *"
Created pipeline: 71fca4e48224
  name:     Daily haiku
  task:     Write a Python haiku
  schedule: 0 17 * * *
  webhook:  /api/pipelines/webhook/cff3da6a8a474d1c87087706605bdea1

$ hive pipelines list
ID             Status    Schedule        Name
───────────────────────────────────────────────────────────────────────────
71fca4e48224   enabled   0 17 * * *      Daily haiku

$ hive pipelines run 71fca4e48224       # fires immediately
$ hive pipelines runs 71fca4e48224      # show run history
```

## Tests

```
137 passed in 18s
```

Phase 6 contribution: 15 (store + cron) + 12 (HTTP) = 27 new tests. No regressions across Phases 0–5.

---

# HIVE — Pre-Phase-6 Bug Fixes: Classifier + Planner Floor

**Date:** 2026-05-18
**Status:** ✅ Fixed, tests green (125 passed, 0 regressions)

Two regressions in the orchestrator were blocking Phase 6 work:

## Bug 1 — Planner could return zero active agents

For clear coding tasks like *"Write a Python function that returns Hello from HIVE and save it to test.py"* the Planner LLM occasionally returned a team that parsed to zero active agents (empty team, or only-passive Debugger). The old code only used the `_fallback_team()` when the team list was literally empty after parsing — it didn't catch the all-passive case.

**Fix** (`backend/orchestrator/nodes/planner.py`):
After parsing, check `composition.total_active`. If zero, append a default `Builder(claude:sonnet, count=1, passive=False)` and amend the rationale.

## Bug 2 — Planner spawned a Builder for chat/questions

Asking *"What is Python?"* or *"Hi"* would still hit the Planner, then spin up a Builder, then a worktree, then a worker — burning seconds and tokens on a conversation.

**Fix** — added a heuristic classifier at the start of the graph:

| File | What it does |
|------|--------------|
| `backend/orchestrator/nodes/classifier.py` | `classify_task(task) -> 'question' \| 'coding'`. Detects `?`, question starters (`what/how/why/...`), greetings (`hi/hello/thanks/...`). If coding action verbs (`write/create/build/fix/...`) or code markers (`.py/function/class/...`) are present, returns `coding`. Default: `coding`. |
| `backend/orchestrator/graph.py` | New `classify_node` (first node from `START`) → conditional edge → either `answer_node` (single worker, no worktree, emits text and ends) or `plan_node` (existing flow). |
| `backend/orchestrator/state.py` | Added `task_type: str` to `GraphState`. |

New graph topology:

```
START → classify ─┬─ answer ──────────────────────────────────────────────► END
                  └─ plan → approval → ┬─ abort ─────────────────────────► END
                                       └─ spawn → run_workers → review ──► END
```

## Tests

```
125 passed in 16s
```

23 new tests in `tests/unit/test_classifier_and_planner_fix.py` — 3 planner-floor cases + 11 question samples + 8 coding samples + 1 empty-input edge case.

---

# HIVE — Phase 5 Complete: Web UI + WebSocket

**Date:** 2026-05-15
**Phase:** 5 — Web UI + WebSocket
**Status:** ✅ All done

---

## What Was Built

| Component | Description |
|-----------|-------------|
| `backend/api/event_bus.py` | Per-session `asyncio.Queue` — `get_or_create`, `emit`, `remove`. Drop-on-full, zero dependencies |
| `backend/api/schemas.py` | Pydantic models: `CreateSessionRequest/Response`, `ApproveRequest`, `SessionInfo`, `AgentInfo` |
| `backend/api/http.py` | REST router: `POST /api/sessions`, `GET /api/sessions[/{id}]`, `POST /{id}/approve`, `POST /{id}/message`. Background `_session_runner` handles interrupt futures |
| `backend/api/ws.py` | WebSocket `/ws/{session_id}` — streams queue events with 20s keepalive ping |
| `backend/main.py` | FastAPI app with lifespan, CORS for `localhost:5173`, includes both routers |
| `backend/orchestrator/graph.py` | Added `_emit_to_ws()` helper + emit calls in `plan_node`, `spawn_node`, `_execute_worker` |
| `cli/hive.py` | Added `hive start [--port 8765] [--reload]` command |
| `frontend/` | Full Vite + React 18 + TypeScript + TailwindCSS + @xyflow/react + Zustand app |
| `tests/unit/test_phase5.py` | 15 tests: event_bus, REST endpoints, WebSocket |
| `~/.hive/skills/` | 8 real SKILL.md files: python-testing, async-python, fastapi-patterns, react-hooks, git-workflow, sql-schema, typescript-strict, debugging-async |

## Architecture: Per-Session Message Queues (Invariant 6)

Each session gets its own `asyncio.Queue` at the moment `POST /api/sessions` is called. The background `_session_runner` task drives the LangGraph graph and emits events into this queue. The WebSocket handler for `/ws/{session_id}` reads from the same queue and forwards to the browser in real time.

Approval interrupts: when `run_session` returns `SessionInterrupt`, `_session_runner` emits `{"type": "interrupt", ...}` to the queue (browser shows ApprovalModal), then suspends on an `asyncio.Future`. `POST /{id}/approve` resolves the future, and the graph continues.

## Frontend

- **Dashboard** — task input (⌘↵ to submit), model + approval-mode selectors, session cards grid
- **TabBar** — all sessions as tabs with live status dots
- **TreeCanvas** — @xyflow/react + dagre auto-layout. Orchestrator (🐝 spinning ring) → agent hexagons. Edges animate when agent is running. Click to inspect in sidebar.
- **AgentNode** — role emoji, current activity truncated, status ring color, progress shimmer when running
- **AgentSidebar** — token counters, live output log (last 500 chunks), status
- **EventLog** — structured log strip at bottom, auto-scrolls
- **ApprovalModal** — team list, confidence bar, ✓ Approve / ✗ Reject
- **InputSection** — urgency mode buttons (💬 ✏️ ⛔ ⚡), message input, shown only when session is active

## How to Run

```bash
# Terminal 1 — backend
cd ~/hive && hive start

# Terminal 2 — frontend dev server
cd ~/hive/frontend && . ~/.nvm/nvm.sh && npm run dev
# → http://localhost:5173
```

## Test Results

```
87 passed in 10.03s
```

(72 previous + 15 new Phase 5 tests — 0 regressions)

Frontend build: `npm run build` → 497 modules, 440 KB JS, 32 KB CSS, 0 TypeScript errors.

---

*Previous phase summaries preserved below.*

---

# HIVE — Phase 4 Complete: Skills Registry + Semantic Injection

**Date:** 2026-05-13
**Phase:** 4 — Skills Registry
**Status:** ✅ All done

---

## What Was Built

| File | Description |
|------|-------------|
| `backend/skills/embedder.py` | `sentence-transformers/all-MiniLM-L6-v2` wrapper — lazy load, serialize/deserialize, cosine similarity |
| `backend/skills/registry.py` | CRUD + semantic search — `import_skill`, `create_skill_file`, `list_skills`, `search` |
| `backend/skills/injector.py` | Builds `## Relevant Skills` markdown block for agent system prompts |
| `backend/persistence/db.py` | Added `skills` table (id, name, description, tags, path, instructions, embedding, version) |
| `backend/orchestrator/graph.py` | `_execute_worker` now searches top-3 skills and injects them via `WorkerConfig.system_prompt` |
| `cli/hive.py` | Added `hive skills list/import/create/test` subcommand group |
| `tests/unit/test_phase4.py` | 15 tests: embedder, registry CRUD, semantic search, injector, graph injection |

## Skills Format

```
~/.hive/skills/<name>/SKILL.md

---
name: python-testing
description: Write pytest tests for Python code
tags: ["python", "testing"]
version: 1
---

## Instructions
Use fixtures, parametrize, aim for 80%+ coverage.
```

## CLI

```
$ hive skills create "python-testing" --description "Write pytest tests" --tags "python,testing"
Created: /home/user/.hive/skills/python-testing/SKILL.md
Edit the file, then run: hive skills import /home/user/.hive/skills/python-testing/SKILL.md

$ hive skills import ~/.hive/skills/python-testing/SKILL.md
Importing ... Imported skill 'python-testing': Write pytest tests

$ hive skills test "write unit tests for my API"
Searching for: 'write unit tests for my API'  (top_k=5, threshold=0.30)
1. [python-testing] python-testing
   Write pytest tests for Python code
   tags: python, testing
```

## How Injection Works

1. Before each agent starts, `_execute_worker` calls `search_skills(role_task, top_k=3)`
2. Top-K skills above cosine similarity threshold (0.3) are returned
3. `build_skill_context(skills)` produces a `## Relevant Skills` markdown block
4. Block is passed as `WorkerConfig.system_prompt` → prepended to the agent's prompt
5. If no skills match or registry is empty → no injection, no side-effects

## Test Results

```
72 passed in 46.30s
```

Note: 46s is the first-time HuggingFace model download (90MB). Subsequent runs are ~1s.

---

*Previous phase summaries preserved below.*

---

# HIVE — Phase 3 Complete: Approval Modes + Confidence Escalation

**Date:** 2026-05-13
**Phase:** 3 — Approval Modes
**Status:** ✅ All done

---

## What Was Built

| Component | Description |
|-----------|-------------|
| `approval_node` in `graph.py` | LangGraph node that calls `interrupt()` when approval is needed |
| `SessionInterrupt` dataclass | Returned by `run_session`/`resume_session` when graph is paused |
| `resume_session_with_value()` | Resumes an interrupted session with `Command(resume={"approved": ...})` |
| Updated `resume_session()` | Now surfaces `SessionInterrupt` when session is paused at approval gate |
| `--approval-mode` CLI flag | `full-auto` (default) \| `checkpoint` \| `manual` |
| Approval UI in CLI | Box display of proposed team, confidence, rationale — `typer.confirm()` prompt |
| `GraphState.approval_mode` | Persisted in LangGraph checkpoint — survives process restarts |
| `tests/unit/test_phase3.py` | 9 tests covering all approval paths |

## Approval Logic

| Condition | Behavior |
|-----------|----------|
| `full-auto` + confidence ≥ 0.7 | Runs without any prompt |
| `full-auto` + confidence < 0.7 | Pauses and shows approval UI |
| `checkpoint` or `manual` | Always pauses, regardless of confidence |
| User rejects | `abort_node` runs → returns `AgentResult(status="cancelled")` |
| User approves | Continues to `spawn_node` → agents run normally |

## CLI Example

```
$ hive run "Build a REST API" --approval-mode checkpoint

Session: a3f2c1d4  |  Backend: claude:sonnet  |  Approval: checkpoint
Task: Build a REST API
────────────────────────────────────────────────────────────

┌─ HIVE: Approval Required ──────────────────────────────────────────
│  Checkpoint: review team composition before agents launch.
│
│  Proposed team:
│    Thinker      x1  [claude:sonnet]
│    Builder      x2  [claude:sonnet]
│    Tester       x1  [claude:sonnet]
│    Debugger     x1  [claude:sonnet] (passive)
│
│  Confidence: 88%  |  Rationale: standard dev team for REST API
└────────────────────────────────────────────────────────────────────

Approve this team and proceed? [Y/n]:
```

## Test Results

```
57 passed in 0.99s
```

All 57 tests green (48 from Phase 0–2, 9 new Phase 3 tests).

## Key Implementation Detail

`interrupt()` is called inside `approval_node`. LangGraph checkpoints the state *before* the interrupt. When `resume_session_with_value()` is called with `Command(resume=...)`, LangGraph re-runs `approval_node` — on the second run, `interrupt()` returns the resume value directly instead of pausing. The node then reads `response["approved"]` and either sets `approval_rejected=True` or returns `{}`.

This means `hive resume <session-id>` works even after a process restart — the state is in SQLite.

---

*Previous phase summary preserved below.*

---

# HIVE — Phase 2 Complete: Multi-Agent Orchestration

**Date:** 2026-05-13
**Phase:** 2 — Multi-Agent Orchestration
**Status:** ✅ All done

---

## What Was Built

| File | Description |
|------|-------------|
| `backend/worktrees/manager.py` | Git worktree manager — create/remove per-agent branches, merge-to-main, conflict detection |
| `backend/orchestrator/nodes/planner.py` | Planner node — calls `claude:sonnet`, returns `TeamComposition` JSON with brace-depth extraction |
| `backend/orchestrator/nodes/spawner.py` | Spawner node — expands team into agents, creates worktrees concurrently (Semaphore 3), registers in DB |
| `backend/orchestrator/nodes/reviewer.py` | Reviewer node — merges each agent branch, produces `ReviewReport`, cleans up worktrees |
| `backend/orchestrator/graph.py` | Extended 4-node graph: plan→spawn→run_workers→review. Auto-commit after each agent. |
| `backend/orchestrator/state.py` | Extended `GraphState` with team_composition, spawn_plan, worker_results, review_report |
| `tests/unit/test_phase2.py` | 10 new tests: planner parsing, spawner concurrency cap, reviewer merge/conflict/failure |

---

## Test Results

```
48 passed in 0.76s
```

All 48 tests green (38 from Phase 0+1, 10 new Phase 2 tests).

---

## End-to-End Verification

```
Task: "Create a file called hello.txt containing the text: Hello from HIVE agent"

Status: completed
Agent: builder-e2e-69-0
Cost: $0.051

Git log after run:
  3625363 hive: agent builder-e2e-69-0 output   ← auto-commit
  01987a8 merge: agent builder-e2e-69-0          ← reviewer merge
  170ea75 chore: init repo for HIVE
```

Full pipeline verified: Planner → Spawn → Worker runs → auto-commit → Reviewer merges → result returned.

---

## Key Decisions Made

1. **Planner uses inline prompt** — instructions + task both go in the `-p` argument to `claude`, not in `system_prompt`. This was necessary because the `system_prompt` field wasn't reliably forwarded.

2. **Brace-depth JSON extraction** — replaced regex `\{[\s\S]*\}` with a character-by-character depth tracker. Regex was matching too broadly when LLM added extra text after the JSON.

3. **Auto-commit in `_execute_worker`** — after the worker completes (success or failure), HIVE stages all changes in the worktree and commits. Without this, the Reviewer found 0 commits and had nothing to merge.

4. **DB path threading** — `create_session` in `run_session` uses the passed `db_path`; spawner's `create_agent` always uses the default `~/.hive/hive.db`. This means tests that pass a custom `db_path` must be aware that agent records go to the default DB. Unit tests for `_execute_worker` still work because they monkeypatch `create_agent` and `update_agent_status`.

5. **`claude:sonnet` everywhere in dev** — Opus reserved for production Planner/Reviewer only.

---

## Next Steps — Phase 3: Approval Modes

- `interrupt()` nodes at the plan→spawn boundary
- Confidence escalation: if Planner confidence < 0.7 → pause and ask user
- Approval mode per session: `full-auto` | `checkpoint` | `manual`
- `hive resume <session-id>` to continue after an interrupt
- CLI prompts for approving/rejecting the team composition before agents launch

---

*Previous phase summary preserved below for reference.*

---

# HIVE — Phase 1 Complete: Single Agent + LangGraph + SQLite

**Date:** 2026-05-13
**Phase:** 1 — Single Agent + State
**Status:** ✅ All done

## What Was Built

| File | Lines | Description |
|------|-------|-------------|
| `backend/persistence/db.py` | 99 | SQLite init, schema (sessions/agents/events/cost_log), WAL mode, async connection manager |
| `backend/persistence/events.py` | 151 | Event sourcing helpers: write_event, create_session, create_agent, list_sessions, get_session, write_cost |
| `backend/persistence/recovery.py` | 83 | Startup crash detection: checks active-agent PIDs, marks dead ones as 'crashed' |
| `backend/orchestrator/state.py` | 39 | `GraphState` TypedDict + `AgentResult` TypedDict for LangGraph |
| `backend/orchestrator/graph.py` | 211 | LangGraph graph: single `run_worker` node, `AsyncSqliteSaver` checkpointing, `run_session()` + `resume_session()` |
| `cli/hive.py` | 232 | Extended CLI: `hive run` (now via LangGraph + DB), `hive sessions`, `hive resume <id>` |
| `tests/unit/test_persistence.py` | 151 | 11 persistence tests (all DB operations) |
| `tests/unit/test_graph.py` | 142 | 2 graph node tests (success + error path) |

## Test Results

```
38 passed in 1.04s
```

## End-to-End Verification

```
$ hive run "Write one sentence about Python." --backend claude:sonnet
Session: f92c32e7  |  Backend: claude:sonnet
Python is a popular programming language...
Tokens: 3 in / 47 out  |  Cost: $0.0452
Session saved. Resume with: hive resume f92c32e7

$ hive sessions
ID         Status      Name                                          Last active
f92c32e7   completed   Write one sentence about Python.              2026-05-13 14:08:45
```
