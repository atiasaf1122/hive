# Golden regression suite (Phase D5)

Fixed task specs executed through the REAL pipeline — real models, real
cost. Manual/on-demand only, never CI.

    hive golden run                # all specs
    hive golden run --only tiny-fix

Each run writes `golden/reports/golden-<timestamp>.json` and prints a
comparison against the previous report, with regressions highlighted.

Specs: tiny-fix (solo-worthy), flask-todo-api (B-phase proof), palette-
playwright (C-phase proof, needs the playwright browser installed),
snake-game, lessons-injection (nonstandard default branch — exercises the
D1 loop), ambiguous-task (exercises the D2 plan gate), multi-file-python,
docs-only.

## Baseline — 2026-07-06 (`golden-20260706-181620.json`, post-E0.3 fixes)

First complete execution. The run before this one (the actual first)
found and led to fixes for: planner max_turns=1 killing every tool-using
plan turn, worker turn-budget starvation (floor now 10), untracked
fixture files causing phantom merge conflicts (+$0.2 Opus review each),
a worktree-creation race silently dropping agents, and an
underspecified palette criterion. Numbers below are AFTER those fixes.

| spec | result | wall | cost | agents |
|---|---|---|---|---|
| ambiguous-task | PASS | 7s | $0.00 | 0 |
| docs-only | PASS | 73s | $0.07 | 1 |
| flask-todo-api | FAIL* | 178s | $0.28 | 2 |
| lessons-injection | PASS | 95s | $0.20 | 1 |
| multi-file-python | PASS | 104s | $0.41 | 2 |
| palette-playwright | PASS | 186s | $0.56 | 2 |
| snake-game | PASS | 291s | $0.93 | 2 |
| tiny-fix | PASS | 42s | $0.06 | 1 |

**Total: 7/8 passed · $2.51 · ~16 min wall.**

*flask-todo-api is FLAKY, not broken: it passed in isolation the same
hour; across three full-pipeline runs it showed three different failure
modes (builder dropped by the now-fixed race; tester wrote tests before
the app existed; tester merged no test file). It exercises the hardest
coordination shape (two agents, dependent outputs) and is kept as-is —
a coordination-quality canary, expected to pass ~⅔ of runs.

## Hybrid routing comparison — 2026-07-06 (`golden-20260706-191035.json`)

Same 8 specs, hybrid routing ON (E1-E4: task-shape router classifying via
local qwen3:8b, mechanical solos on local qwen3-coder:30b, local-first
summarization, planner offered the local pool).

| spec | baseline | hybrid | Δ cost |
|---|---|---|---|
| ambiguous-task | PASS $0.00 7s | PASS $0.00 19s | — |
| docs-only | PASS $0.07 73s | PASS $0.22 78s | +$0.15 |
| flask-todo-api | FAIL* $0.28 178s | **PASS $0.05 41s** | −$0.23 |
| lessons-injection | PASS $0.20 95s | FAIL† $0.00 42s | — |
| multi-file-python | PASS $0.41 104s | PASS $0.40 114s | ≈ |
| palette-playwright | PASS $0.56 186s | PASS $0.09 101s | −$0.47 |
| snake-game | PASS $0.93 291s | FAIL‡ $0.10 111s | — |
| tiny-fix | PASS $0.06 42s | PASS $0.03 21s | −$0.03 |

**Batch totals: baseline 7/8 · $2.51 · ~16 min → hybrid 6/8 · $0.88 ·
~8.8 min (cost −65%, wall −45%).**

†‡ Both hybrid failures diagnosed — neither was caused by local models:
- ‡ snake-game: claude CLI stdin flake ("no stdin data received in 3s",
  exit 1) on a CLAUDE solo worker; re-run PASSED at **$0.00** — the local
  coder wrote the whole game.
- † lessons-injection: the planner (correctly) declined an impossible
  premise — the fixture wasn't a git repo until spawn time, and the
  E0.3-fixed planner now actually reads the workspace. Spec bug; fixed
  with the new `git_branch:` fixture field; re-run PASSES at $0.08.
- flask-todo-api — the baseline's flaky coordination canary — passed
  under hybrid at $0.05: the solo route had ONE local worker write
  app.py + tests in a single shot (no coordination to flake).
- palette-playwright's solo agent npm-installed Playwright itself and
  drove a real Chromium (verified: real screenshot via node script) —
  browser verification held without MCP at −$0.47.
- docs-only got MORE expensive (+$0.15): the swarm path with planner ran
  where baseline's plan was lighter. Routing guidance left as-is — one
  regression of $0.15 against $0.73 saved elsewhere.

**Verdict: hybrid routing holds quality (every failure was flake or spec
bug, both now fixed) while cutting cost ~65% and wall time ~45%.**

## Phase F hybrid re-run — 2026-07-06 (`golden-20260706-214951.json`)

Regression check after F1-F4 (guard hook, lifecycle signals, salvage,
producer/consumer net). **7/8 · $1.59 · ~13 min** vs E5 hybrid (6/8 ·
$0.88 · ~8.8 min).

The cost rise is honest accounting, not a regression:
- **$0.34 is the planner cost E5 never logged** (F0.1) — a third of spend
  that was previously dark. Role breakdown of this run: workers $0.85,
  planner $0.34, llm-review $0.31, plan-gate $0.09, summarizers/classifier
  $0 (local).
- palette-playwright is back on Claude ($0.54, was $0.09 local) because
  F5 correctly stops routing browser-verification to a tool-less local
  worker. Real worker spend was $0.85.
- The guard hook (~24ms/Bash call, Python startup) + Stop signals cost
  ~nothing; wall time is within run-to-run variance.

The lone failure is flask-todo-api — the documented flaky coordination
canary (generated-test logic mismatch), not F-caused. Two SOLO-routing
gaps this run surfaced were fixed in F5 (browser tasks stay on Claude;
tool-reliant solos get 28 turns not 12).
