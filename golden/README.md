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
