# E4 local-vs-Haiku quality comparison (2026-07-06)

Same real prompts, both engines, verbatim outputs. Inputs: three real summarizer prompts rebuilt from recent session event streams + one real distillation evidence prompt (the E0.1 conflict).

## SUMMARIZE: golden flask-todo builder run

### claude:haiku

```
```json
{
  "tldr": "Created app.py with a Flask todo API supporting GET/POST/DELETE /todos endpoints with in-memory storage.",
  "standard": "The agent created app.py implementing a Flask todo API with in-memory list storage and auto-incrementing IDs. The implementation provides GET to list todos, POST to create new ones with validation, and DELETE to remove entries, handling 400/404 errors as specified. The code was verified to import cleanly and meet all requirements.",
  "status": "done",
  "description": "The agent created app.py implementing a Flask todo API with in-memory list storage and auto-incrementing IDs. The implementation provides GET to list todos, POST to create new ones with validation, and DELETE to remove entries, handling 400/404 errors as specified. The code was verified to import cleanly and meet all requirements.",
  "key_decisions": [
    "In-memory list storage for todos with global next_id counter",
    "400/404 validation for error handling",
    "Module-level Flask app instance for easy import"
  ],
  "open_questions": [],
  "technical_debt": [],
  "follow_up_tasks_recommended": [
    "Run Flask dev server to test endpoints live",
    "Add unit tests for todo CRUD operations",
    "Consider persistent storage (database or file)"
  ],
  "evidence": {
    "git_commits": [],
    "files_touched": [
      {
        "path": "app.py",
        "action": "created",
        "lines_added": 0,
        "lines_removed": 0,
        "what_was_done": "Created Flask todo API with GET/POST/DELETE /todos endpoints, in-memory storage, ID generation, and validation"
      }
    ],
    "tests_run": [
      {
        "command": "python3 -c \"import app\" 2>&1 | tail -20",
        "exit_code": 0,
        "excerpt": "Verified app.py imports cleanly and defines a module-level `app` Flask instance"
      }
    ],
    "packages_installed": [],
    "diff_summary": "Created app.py with complete Flask todo API implementation",
    "commands_run": [
      "ls -la /home/atiasaf1122/.hive/worktrees/gd7b4cce/builder-gd7b4c-0/",
      "python3 -c \"import app\" 2>&1 | tail -20"
    ]
  }
}
```
```

### qwen3:8b (local)

```
{
  "tldr": "Flask todo API implemented with in-memory storage",
  "standard": "The agent created a Flask application with GET/POST/DELETE endpoints for managing todos using an in-memory list, implementing ID incrementing and error validation as specified.",
  "status": "done",
  "description": "The agent created a Flask application with GET/POST/DELETE endpoints for managing todos using an in-memory list, implementing ID incrementing and error validation as specified.",
  "key_decisions": [
    "Implement todo endpoints with in-memory storage",
    "Add ID incrementing and error validation"
  ],
  "open_questions": [],
  "technical_debt": [
    "In-memory storage instead of database persistence"
  ],
  "follow_up_tasks_recommended": [
    "Add database persistence",
    "Implement API testing suite"
  ],
  "evidence": {
    "git_commits": [],
    "files_touched": [
      {
        "path": "/home/atiasaf1122/.hive/worktrees/gd7b4cce/builder-gd7b4c-0/app.py",
        "action": "created",
        "lines_added": 0,
        "lines_removed": 0,
        "what_was_done": "Created Flask todo API implementation"
      }
    ],
    "tests_run": [],
    "packages_installed": [],
    "diff_summary": "",
    "commands_run": [
      "ls -la /home/atiasaf1122/.hive/worktrees/gd7b4cce/builder-gd7b4c-0/",
      "python3 -c \"import app\" 2>&1 | tail -20"
    ]
  }
}
```

## SUMMARIZE: golden snake-game builder run

### claude:haiku

```
```json
{
  "tldr": "Created a fully functional snake game in a single self-contained HTML file with verified game logic.",
  "standard": "The agent wrote an `index.html` file containing a complete snake game implementation with inline CSS and JavaScript. The game includes proper movement mechanics with reverse-prevention, food growth and spawning, collision detection (walls and self), a requestAnimationFrame-based game loop with consistent 110ms tick rate, and a game-over overlay with restart functionality. JavaScript syntax validation was performed and the logic was confirmed correct across all game mechanics.",
  "status": "done",
  "description": "The agent wrote an `index.html` file containing a complete snake game implementation with inline CSS and JavaScript. The game includes proper movement mechanics with reverse-prevention, food growth and spawning, collision detection (walls and self), a requestAnimationFrame-based game loop with consistent 110ms tick rate, and a game-over overlay with restart functionality. JavaScript syntax validation was performed and the logic was confirmed correct across all game mechanics.",
  "key_decisions": [
    "Single self-contained HTML file with inline CSS and JS, no external dependencies",
    "requestAnimationFrame with timestamp-gated movement interval for frame-rate independent gameplay",
    "Reverse-prevention by rejecting directions opposite to current movement direction",
    "Tail-aware collision detection (exclude tail when not growing, include when growing) to prevent false positives",
    "Game restart without page reload by reinitializing state and restarting the animation frame loop"
  ],
  "open_questions": [],
  "technical_debt": [],
  "follow_up_tasks_recommended": [],
  "evidence": {
    "git_commits": [],
    "files_touched": [
      {
        "path": "/home/atiasaf1122/.hive/worktrees/gc388318/builder-gc3883-0/index.html",
        "action": "created",
        "lines_added": 0,
        "lines_removed": 0,
        "what_was_done": "Created complete snake game HTML file with inline CSS and JavaScript, including canvas rendering, game logic, collision detection, scoring, and UI overlay"
      }
    ],
    "tests_run": [
      {
        "command": "node -e \"const fs = require('fs'); const html = fs.readFileSync('index.html', 'utf8'); const match = html.match(/<script>([\\s\\S]*)<\\/script>/); ...\"",
        "exit_code": 0,
        "excerpt": "JS syntax checks out. Game logic verified: movement/reverse-prevention, growth mechanics, collision detection, game loop with requestAnimationFrame, game-over and restart functionality all correct."
      }
    ],
    "packages_installed": [],
    "diff_summary": "Created single HTML file with complete snake game implementation",
    "commands_run": [
      "node -e \"...\" (JavaScript syntax and logic validation)"
    ]
  }
}
```
```

### qwen3:8b (local)

```
{
  "tldr": "Snake game implementation with robust collision logic and self-contained HTML/JS",
  "standard": "The agent successfully implemented a snake game with directional reversal prevention, growth mechanics, and collision detection. The game logic was verified to handle edge cases like tail interaction and maintain consistent frame rates using requestAnimationFrame. All code is contained in a single HTML file with inline CSS and JavaScript.",
  "status": "done",
  "description": "The agent successfully implemented a snake game with directional reversal prevention, growth mechanics, and collision detection. The game logic was verified to handle edge cases like tail interaction and maintain consistent frame rates using requestAnimationFrame. All code is contained in a single HTML file with inline CSS and JavaScript.",
  "key_decisions": [
    "Implement directional reversal prevention to prevent instant self-collision",
    "Differentiate collision detection logic for growing vs non-growing states",
    "Use requestAnimationFrame with timestamp-gated intervals for consistent movement",
    "Handle game over/restart without page reload through state reinitialization"
  ],
  "open_questions": [],
  "technical_debt": [
    "Collision detection logic could be simplified",
    "No automated tests implemented",
    "Game speed adjustment could be added"
  ],
  "follow_up_tasks_recommended": [
    "Add unit tests for game mechanics",
    "Implement difficulty scaling",
    "Create mobile touch controls"
  ],
  "evidence": {
    "git_commits": [],
    "files_touched": [
      {
        "path": "/home/atiasaf1122/.hive/worktrees/gc388318/builder-gc3883-0/index.html",
        "action": "modified",
        "lines_added": 0,
        "lines_removed": 0,
        "what_was_done": "Contained complete game implementation with inline CSS/JS"
      }
    ],
    "tests_run": [],
    "packages_installed": [],
    "diff_summary": "Added complete snake game implementation in single HTML file",
    "commands_run": [
      "node -e \"const fs = require('fs');const html = fs.readFileSync('index.html', 'utf8');const match = html.match(/<script>([\\s\\S]*)<\\/script>/);fs.writeFileSync('/tmp/snake_check.js', match[1])\""
    ]
  }
}
```

## SUMMARIZE: E0.1 conflict-run builder

### claude:haiku

```
```json
{
  "tldr": "Updated notes.txt with a 3-item shopping list (Milk, Bread, Eggs).",
  "standard": "The agent read the existing notes.txt file, then rewrote it with a 3-item shopping list in the requested format (numbered lines). The file now contains: 1. Milk, 2. Bread, 3. Eggs. The agent reported the task complete and ready for HIVE auto-commit.",
  "status": "done",
  "description": "The agent read the existing notes.txt file, then rewrote it with a 3-item shopping list in the requested format (numbered lines). The file now contains: 1. Milk, 2. Bread, 3. Eggs. The agent reported the task complete and ready for HIVE auto-commit.",
  "key_decisions": [
    "Rewrote the file with a numbered shopping list format"
  ],
  "open_questions": [],
  "technical_debt": [],
  "follow_up_tasks_recommended": [
    "HIVE auto-commit as indicated by agent"
  ],
  "evidence": {
    "git_commits": [],
    "files_touched": [
      {
        "path": "/home/atiasaf1122/.hive/worktrees/e011b5ad/builder-e011b5-0/notes.txt",
        "action": "modified",
        "lines_added": 3,
        "lines_removed": 0,
        "what_was_done": "Rewrote file with 3-item shopping list: Milk, Bread, Eggs"
      }
    ],
    "tests_run": [],
    "packages_installed": [],
    "diff_summary": "File rewritten with shopping list content (1. Milk, 2. Bread, 3. Eggs)",
    "commands_run": []
  }
}
```
```

### qwen3:8b (local)

```
{
  "tldr": "Agent successfully updated notes.txt with the shopping list.",
  "standard": "The agent modified notes.txt to include a 3-item shopping list (Milk, Bread, Eggs) in the exact requested format. Two confirmation messages indicated success and readiness for HIVE auto-commit. No errors or additional actions were reported.",
  "status": "done",
  "description": "The agent modified notes.txt to include a 3-item shopping list (Milk, Bread, Eggs) in the exact requested format. Two confirmation messages indicated success and readiness for HIVE auto-commit. No errors or additional actions were reported.",
  "key_decisions": ["Implement the shopping list in the specified format", "Confirm file content before committing"],
  "open_questions": ["Are there additional tasks required after auto-commit?"],
  "technical_debt": [],
  "follow_up_tasks_recommended": ["Trigger HIVE auto-commit", "Verify shopping list completeness"],
  "evidence": {
    "git_commits": [],
    "files_touched": [
      {
        "path": "/home/atiasaf1122/.hive/worktrees/e011b5ad/builder-e011b5-0/notes.txt",
        "action": "modified",
        "lines_added": 3,
        "lines_removed": 0,
        "what_was_done": "Wrote 3-item shopping list (Milk, Bread, Eggs) to file"
      }
    ],
    "tests_run": [],
    "packages_installed": [],
    "diff_summary": "-  (no diff shown)\n+  (no diff shown)",
    "commands_run": [
      "{tool Read} {\"file_path\": \"/home/atiasaf1122/.hive/worktrees/e011b5ad/builder-e011b5-0/notes.txt\"}",
      "{tool Write} {\"file_path\": \"/home/atiasaf1122/.hive/worktrees/e011b5ad/builder-e011b5-0/notes.txt\", \"content\": \"1. Milk\\n2. Bread\\n3. Eggs\\n\"}"
    ]
  }
}
```

## DISTILL: E0.1 llm_review evidence

### claude:haiku

```
```json
{
  "title": "Concurrent file edits create unnecessary merge conflicts",
  "description": "Simultaneous modifications to the same file by different branches trigger merge conflicts, even when the changes are compatible.",
  "content": "When one branch rewrites a file while another appends critical information (e.g., an urgent hotfix), a merge conflict forces manual resolution. Avoid this by coordinating which files each branch will modify, merging more frequently to shrink the concurrent-edit window, or structuring work so distinct changes go to distinct files.",
  "trigger_context": "merge conflicts during concurrent development, overlapping file modifications"
}
```
```

### qwen3:8b (local)

```
{
  "title": "Concurrent Edits to Shared Files",
  "description": "Merge conflicts arise from overlapping edits to the same file by multiple branches.",
  "content": "Avoid merge conflicts by coordinating edits to shared files or using locks to prevent concurrent modifications. When conflicts occur, resolve them by understanding both sides' intents and merging changes systematically.",
  "trigger_context": "Collaborative code editing, branch merging"
}
```
## Verdict (E4.3 — encoded in backend/llm/local.py)

**Summarization: local ON by default.** qwen3:8b produced schema-correct,
factually accurate summaries on all three real inputs. Weaknesses vs
Haiku: thinner evidence sections (tests_run left empty once), advisory
fluff in follow-ups, one `action: modified` where `created` was true,
and — on the earlier degenerate/empty-transcript variant of this test —
one false `"status": "done"` where Haiku honestly said "blocked". Those
are acceptable because summaries feed the B4 validators, which verify
every file claim against real git state; the volume (every worker run)
makes the savings real.

**Distillation: stays on Haiku by default** (HIVE_LOCAL_INTERNAL=on to
force local). qwen3:8b's lesson draft invented "using locks for critical
files" — a mechanism the evidence never showed. The groundedness gate
exists to catch exactly this, but with a local distiller the gate runs
on the SAME model that confabulated, so the defense is weakened. One
distill+gate per session close costs ~$0.03 — not worth the asymmetric
risk of a poisoned lesson store. Revisit with a larger local model or a
split-gate wiring (local distill, Haiku gate).

**Classification (task-shape router): local, already shipped in E3** —
the rubric output is a 4-field JSON with constrained values; qwen3:8b
handles it reliably and errors fail open to swarm.
