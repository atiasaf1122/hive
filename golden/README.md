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

Expected cost per full run: record here after the first full execution
(single-spec smoke `tiny-fix` measured ~$0.15-0.30; a full 8-spec run is
roughly $3-6 and 45-90 minutes depending on plans the models choose).
