"""Orchestrator decision-maker.

The orchestrator is the user's permanent contact for a project. Every user
message is fed to `orchestrate()`, which decides what to do:

  - Just answer (chat, question, follow-up)        → response, empty team
  - Spawn agents to build something                → response + team
  - Both (announce the plan + spawn)               → response + team

Backwards-compat:
  - `_parse_team_composition` and `plan_team` are still exported so existing
    parser-only tests keep working.
  - `OrchestratorDecision` is a thin wrapper over `TeamComposition` with an
    extra `response` string.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from backend.models import DEFAULT_MODEL
from backend.workers.base import EventType, WorkerConfig
from backend.workers.claude_cli import ClaudeCLIWorker

logger = logging.getLogger(__name__)

PLANNER_MODEL = DEFAULT_MODEL

_INSTRUCTIONS = """You are the Orchestrator for HIVE, an AI agent swarm.
The user is your permanent contact for this project — they may send many messages over its lifetime.
Look at the conversation history and the latest user message, then decide what to do.

Return ONLY a JSON object — no explanation, no markdown.

Available roles: Thinker (architecture), Builder (code), Tester (tests),
Debugger (fixing failures — passive only), Researcher, Writer, Editor,
Translator, DocReader.

Model tiers per agent:
- "claude:sonnet" — default for real engineering work
- "claude:haiku"  — mechanical subtasks only (renames, boilerplate, doc tweaks)
__LOCAL_MODELS__

Return this exact JSON structure:
{
  "response": "your direct reply to the user (always present, can be brief)",
  "team": [
    {
      "role": "Builder",
      "model": "claude:sonnet",
      "subtask": "Implement the Flask routes in app.py — CRUD for /todos. Do NOT write tests (a Tester agent owns those).",
      "files_hint": ["app.py"],
      "max_turns": 15,
      "passive": false
    }
  ],
  "contract": "GET /todos -> 200 [{id:int, task:str, done:bool}]; POST /todos body {task:str} -> 201 {id,task,done}; DELETE /todos/<id> -> 200 | 404",
  "confidence": 0.8,
  "rationale": "one line reason"
}

Rules:
- If the user is just chatting / asking a question / following up → set `team: []` and put your answer in `response`.
- If the user wants something built/edited/fixed → list 1-5 agents in `team`, ONE ENTRY PER AGENT.
- EVERY agent gets its own `subtask`: a concrete, self-contained brief. Parallel agents are only
  worth spawning when you can DECOMPOSE the work into distinct subtasks — never emit two agents
  with the same brief just to "go faster"; that duplicates the work and wastes tokens.
  The one exception is deliberate perspective diversity (e.g. investigating a bug from several
  angles): then give each agent the same question but a DISTINCT lens, stated in its subtask.
- `files_hint`: files/dirs this agent will touch — AND any file it needs to READ that another
  agent produces (a Tester verifying index.html must list index.html). Overlapping hints are
  enforced mechanically: same-role overlaps get merged into one agent, different-role overlaps
  get sequenced into waves (the later agent starts after its predecessor's work is committed).
  Omitting a consumed file puts producer and consumer in the SAME wave — the consumer then
  waits forever for a file that never appears in its isolated worktree. Two agents must NOT
  WRITE the same file — merge them or make one a reviewer instead.
- `max_turns`: per-agent budget — every tool call costs a turn, so even a one-line edit takes
  ~6-8 turns (read, edit, verify, respond). 10-12 for small/mechanical subtasks, 15-20 standard,
  30 only for large builds. MCP-equipped agents (browser etc.) burn turns fast — give them 25-30
  (the golden palette Tester died at the finish line on turns and its work was lost).
- passive=true for Debugger only.
- `contract` (optional, top-level): when the team has a producer/consumer pair around a shared
  INTERFACE — API routes, function signatures, file formats, data shapes — state that interface
  CONCRETELY here (routes + methods + status codes + payload shapes, or exact signatures, or the
  file schema). It is injected verbatim into EVERY agent's prompt so the Builder and the Tester
  build against the SAME stated contract instead of each guessing — a mismatch here is the
  classic "tests assert a different API than the code implements" failure. Omit when there is no
  shared interface (single agent, or independent files).
- No markdown, no extra text outside the JSON.

MCP servers (optional per-agent field `"mcp_servers": ["id", ...]`):
__MCP_DIGEST__
Assign servers ONLY when the subtask truly needs them. Most coding subtasks need NONE —
workers already have file tools, bash, and git built in. Omit the field when unused."""


def _mcp_digest() -> str:
    """Compact catalog digest — stays in sync with backend/mcp/catalog.py."""
    from backend.mcp.catalog import list_specs

    return "\n".join(
        f"- {s.id} ({s.label}; tags: {', '.join(s.tags)}): {s.when_to_use}"
        for s in list_specs()
    )


_INSTRUCTIONS = _INSTRUCTIONS.replace("__MCP_DIGEST__", _mcp_digest())


class TeamMember:
    """One agent slot in a team plan.

    `subtask` is the agent's own brief (B1: per-agent decomposition). `count`
    survives for backwards compatibility with old checkpoints/tests — the
    parser expands count>1 into individual members, so downstream code can
    treat every TeamMember as exactly one agent.
    """

    def __init__(
        self,
        role: str,
        model: str,
        count: int = 1,
        passive: bool = False,
        subtask: str = "",
        files_hint: list[str] | None = None,
        max_turns: int | None = None,
        mcp_servers: list[str] | None = None,
        wave: int = 0,
        predecessor_note: str = "",
        fallback: str = "haiku",
        contract: str = "",
    ) -> None:
        self.role = role
        self.model = model
        self.count = count
        self.passive = passive
        self.subtask = subtask
        self.files_hint = files_hint
        self.max_turns = max_turns
        self.mcp_servers = mcp_servers or []
        # G3: shared interface contract injected into every teammate's prompt.
        self.contract = contract
        # E2: Claude tier used when a local model can't spawn (VRAM full).
        self.fallback = fallback
        # D4: execution wave — wave>0 runs after all lower waves complete
        # (file-overlap sequencing). predecessor_note tells the agent whose
        # branch to look at.
        self.wave = wave
        self.predecessor_note = predecessor_note

    def __repr__(self) -> str:
        return (
            f"TeamMember(role={self.role}, model={self.model}, count={self.count}, "
            f"passive={self.passive}, subtask={self.subtask[:40]!r})"
        )


class TeamComposition:
    def __init__(self, team: list[TeamMember], confidence: float, rationale: str,
                 plan_adjustments: list[dict] | None = None) -> None:
        self.team = team
        self.confidence = confidence
        self.rationale = rationale
        # F4.2: parse-time wave resequencing (produce/consume dependencies).
        self.plan_adjustments = plan_adjustments or []

    @property
    def total_active(self) -> int:
        return sum(m.count for m in self.team if not m.passive)

    def __repr__(self) -> str:
        return f"TeamComposition(members={self.team}, confidence={self.confidence:.2f})"


class OrchestratorDecision:
    """A single orchestrator turn output: a reply to the user, plus an optional team to spawn."""

    def __init__(self, response: str, composition: TeamComposition) -> None:
        self.response = response
        self.composition = composition

    @property
    def has_active_team(self) -> bool:
        return self.composition.total_active > 0

    def __repr__(self) -> str:
        return f"OrchestratorDecision(response={self.response[:40]!r}, team={self.composition})"


async def orchestrate(
    message: str,
    session_id: str,
    history: list[dict] | None = None,
    model: str = PLANNER_MODEL,
    project_path: str | None = None,
    state_doc: str = "",
) -> OrchestratorDecision:
    """Run one orchestrator turn — answer the user, optionally with a team to spawn.

    `project_path` is used as the planner's cwd so Read/Glob/Grep tools
    can actually inspect the user's files when choosing a team. Originally
    we forced cwd=/tmp because an unconstrained planner sometimes ignored
    the JSON-only instruction and started building the project itself
    (leaving untracked files that broke the Reviewer's merge). That risk
    is now contained by `allowed_tools=["Read","Glob","Grep"]` below —
    the planner has no write capability, so cwd=project_path is safe.
    Falls back to /tmp if the path is missing/inaccessible (e.g. tests).
    """
    worker = ClaudeCLIWorker()
    prompt = _build_prompt(
        message, history or [], state_doc=state_doc,
        local_digest=await _local_models_digest(),
    )

    cwd = "/tmp"
    if project_path:
        try:
            if Path(project_path).is_dir():
                cwd = project_path
        except OSError:
            pass

    config = WorkerConfig(
        agent_id=f"orchestrator-{session_id}",
        session_id=session_id,
        model=model,
        worktree_path=cwd,
        # E0.3 finding: claude CLI counts EVERY tool iteration as a turn,
        # so max_turns=1 + allowed tools meant the first Read killed the
        # planner (error_max_turns at num_turns=2 — observed in the D e2e
        # and every E0 run where it glanced at a file; only the D2 gate's
        # revision loop kept those sessions alive). 6 turns = a few
        # Read/Glob/Grep calls then the decision. The original fear
        # (planner burning turns on WebSearch research) is already solved
        # by the allowed_tools whitelist below, not by the turn cap.
        max_turns=6,
        # No WebSearch / WebFetch / Bash here — those are research
        # tools the planner doesn't need. Inspecting the local project
        # tree (Read/Glob/Grep) is enough to choose a team. If
        # research is actually required, that's the team's job, not
        # the planner's.
        allowed_tools=["Read", "Glob", "Grep"],
    )

    # Stream planner activity live to the session's WebSocket so the user
    # sees tool calls + thinking instead of staring at a static
    # "orchestrator is thinking" pill. Import lazily — keeps the planner
    # importable from tests that don't have a backend running.
    from backend.api import event_bus

    chunks: list[str] = []
    final_text: str | None = None  # populated when TEXT_DONE arrives
    async for event in worker.run(prompt, config):
        # Mirror everything except the final cost/end into the
        # WebSocket so the UI can render a live activity feed.
        # `event_bus.emit` is non-blocking (put_nowait + ring append,
        # drops on QueueFull) so awaiting here cannot backpressure
        # the worker's stream parser.
        if event.type in (
            EventType.TEXT_DELTA, EventType.TOOL_USE, EventType.TOOL_RESULT,
        ):
            try:
                await event_bus.emit(session_id, {
                    "type": "planner_event",
                    "session_id": session_id,
                    "agent_id": event.agent_id,
                    "kind": event.type,
                    "text": event.text,
                    "tool_name": event.tool_name,
                    "tool_input": event.tool_input,
                })
            except Exception as exc:  # noqa: BLE001
                logger.debug("Planner WS emit failed: %s", exc)

        if event.type == EventType.TEXT_DELTA and event.text:
            chunks.append(event.text)
        elif event.type == EventType.TEXT_DONE and event.text:
            # The consolidated assistant message — supersedes partial
            # chunks so we don't end up double-counting the text.
            final_text = event.text
        elif event.type == EventType.COST:
            # F0.1: the planner was the single biggest per-session cost
            # NOT in cost_log — every economics number was understating.
            try:
                from backend.persistence.events import write_cost
                await write_cost(session_id, f"planner-{session_id}",
                                 event.input_tokens or 0,
                                 event.output_tokens or 0,
                                 event.cost_usd or 0.0)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Planner cost write failed: %s", exc)
        elif event.type == EventType.AGENT_ERROR:
            logger.error("Orchestrator failed: %s", event.error)
            return OrchestratorDecision(
                response="(orchestrator failed — please try again)",
                composition=_fallback_team(),
            )

    return _parse_decision(final_text if final_text is not None else "".join(chunks))


async def _local_models_digest() -> str:
    """Availability-checked local pool for the planner prompt (E2).

    Rebuilt every plan turn — VRAM headroom changes as workers spawn and
    the user games/renders. Empty pool → empty string → the planner never
    hears about local models (Claude-only degradation)."""
    try:
        from backend.models_local import discover_local_models

        models = [m for m in await discover_local_models() if m.available]
    except Exception as exc:  # noqa: BLE001 — planning must survive discovery
        logger.debug("Local model digest skipped: %s", exc)
        return ""
    if not models:
        return ""
    lines = ["Local models (FREE — cost $0, run on the user's own GPUs):"]
    lines += [
        f'- "local:{m.name}" — {m.tier_equivalence}; good for: '
        f"{', '.join(sorted(m.capabilities))}"
        for m in models
    ]
    lines += [
        "Routing guidance:",
        "- Local models cost $0 — prefer them for mechanical edits, boilerplate,",
        "  renames, config changes, test scaffolding from clear specs, doc updates,",
        "  and high-volume repetitive subtasks.",
        "- Claude tiers for: novel logic, cross-file reasoning, debugging",
        "  unfamiliar failures, anything ambiguous or exploratory.",
        "- When uncertain, split: a local drafter + a Claude reviewer subtask is",
        "  often cheaper than one big Claude worker.",
        "- Subtasks with MCP servers (browser etc.) stay on Claude tiers —",
        "  local models cannot drive tools.",
        '- A local agent may carry "fallback": "haiku" (used automatically when',
        "  VRAM headroom vanishes at spawn time).",
    ]
    return "\n".join(lines)


def _build_prompt(message: str, history: list[dict], state_doc: str = "",
                  local_digest: str = "") -> str:
    parts = [_INSTRUCTIONS.replace("__LOCAL_MODELS__", local_digest), ""]
    if state_doc:
        # D3: the compact CURRENT STATE doc replaces the turns that were
        # pruned by compaction — it comes BEFORE the remaining raw turns.
        parts.append("Current project state (compacted from earlier turns):")
        parts.append(state_doc)
        parts.append("")
    if history:
        parts.append("Conversation so far:")
        for entry in history[-10:]:  # last 10 turns
            role = entry.get("role", "user").capitalize()
            content = (entry.get("content") or "").strip()
            if content:
                parts.append(f"{role}: {content}")
        parts.append("")
    parts.append(f"User: {message}")
    parts.append("")
    parts.append("JSON only:")
    return "\n".join(parts)


def _parse_decision(raw: str) -> OrchestratorDecision:
    data = _extract_first_json_object(raw)
    if data is None:
        logger.warning("Orchestrator returned no JSON -- defaulting to chat-only response")
        return OrchestratorDecision(
            response=raw.strip() or "(no response)",
            composition=TeamComposition(team=[], confidence=0.5, rationale="no JSON returned"),
        )

    response = (data.get("response") or "").strip()
    composition = _parse_composition_dict(data)
    return OrchestratorDecision(response=response, composition=composition)


async def plan_team(
    task: str,
    session_id: str,
    model: str = PLANNER_MODEL,
) -> TeamComposition:
    """Backwards-compat shim: returns only the team part of an orchestrator turn."""
    decision = await orchestrate(message=task, session_id=session_id, model=model)
    return decision.composition


def _parse_team_composition(raw: str) -> TeamComposition:
    """Extract the first JSON object from the LLM response.

    Guarantees the returned TeamComposition has at least one active (non-passive)
    agent when one was clearly intended — coding tasks must never reach spawn
    with an empty team.
    """
    data = _extract_first_json_object(raw)
    if data is None:
        logger.warning("Planner returned no JSON -- using fallback team")
        return _fallback_team()

    composition = _parse_composition_dict(data)

    if composition.total_active == 0:
        logger.warning("Planner returned 0 active agents -- inserting default Builder")
        composition.team.append(
            TeamMember(role="Builder", model=DEFAULT_MODEL, count=1, passive=False)
        )
        if not composition.rationale:
            composition.rationale = "auto-added default Builder (planner returned no active agents)"

    return composition


def _parse_composition_dict(data: dict) -> TeamComposition:
    """Same parsing path as `_parse_team_composition` but without the auto-floor.

    The orchestrator path needs an empty team to be a legitimate signal that
    "no agents needed, just answer". So we keep raw zero-active teams intact.

    Legacy `count` fields (old checkpoints, pre-B1 plans) are expanded into
    individual members here so downstream code never sees count>1.
    """
    # G3: contract-first — a concrete shared interface injected into EVERY
    # brief so producer and consumer build against the SAME spec (the
    # flask-todo failure was the Tester asserting a different API than the
    # Builder implemented).
    contract = str(data.get("contract") or "").strip()
    team = []
    for member in data.get("team", []):
        role = member.get("role", "Builder")
        model = member.get("model", DEFAULT_MODEL)
        count = max(1, int(member.get("count", 1)))
        # E2: "local:<name>" is the planner-facing spelling; the worker
        # backend convention is "ollama:<name>" — normalize here so the
        # spawner's startswith("claude:") routing just works.
        if model.startswith("local:"):
            model = "ollama:" + model[len("local:"):]
        fallback = str(member.get("fallback") or "haiku").strip() or "haiku"
        passive = bool(member.get("passive", False))
        subtask = (member.get("subtask") or "").strip()
        files_hint = member.get("files_hint") or None
        if files_hint is not None:
            files_hint = [str(f) for f in files_hint if str(f).strip()] or None
        raw_turns = member.get("max_turns")
        # Floor of 10 (E0.3): claude CLI counts every tool iteration as a
        # turn — the golden tiny-fix builder starved at a plan-assigned 6
        # (read+edit+verify+respond already needs ~6-8). Planner budgets
        # below a working minimum are optimism, not economy.
        max_turns = max(10, min(int(raw_turns), 50)) if raw_turns else None

        # C3: validate MCP server ids against the catalog — an unknown id
        # is dropped with a warning rather than failing the whole plan.
        mcp_servers: list[str] = []
        for sid in member.get("mcp_servers") or []:
            from backend.mcp.catalog import get_spec
            sid = str(sid).strip()
            if get_spec(sid):
                mcp_servers.append(sid)
            else:
                logger.warning(
                    "Planner assigned unknown MCP server %r — dropped", sid
                )

        for _ in range(count):
            team.append(TeamMember(
                role=role, model=model, count=1, passive=passive,
                subtask=subtask, files_hint=files_hint, max_turns=max_turns,
                mcp_servers=list(mcp_servers), fallback=fallback,
                contract=contract,
            ))

    adjustments = _resolve_file_overlaps(team)

    return TeamComposition(
        team=team,
        confidence=float(data.get("confidence", 0.7)),
        rationale=data.get("rationale", ""),
        plan_adjustments=adjustments,
    )


def _norm_hint(hint: str) -> str:
    return hint.strip().replace("\\", "/").lstrip("./").rstrip("/")


def _hints_overlap(a: str, b: str) -> bool:
    import fnmatch

    a, b = _norm_hint(a), _norm_hint(b)
    if not a or not b:
        return False
    if a == b or a.startswith(b + "/") or b.startswith(a + "/"):
        return True
    return fnmatch.fnmatch(a, b) or fnmatch.fnmatch(b, a)


def _members_overlap(m1: TeamMember, m2: TeamMember) -> list[str]:
    hits = []
    for h1 in m1.files_hint or []:
        for h2 in m2.files_hint or []:
            if _hints_overlap(h1, h2):
                hits.append(h2)
    return hits


def _resolve_file_overlaps(team: list[TeamMember]) -> list[dict]:
    """D4: no two agents may run in parallel on intersecting files_hint.

    Mechanical and free — the semantic sibling lives in the D2 plan gate.
    Same-role overlap → merge the later brief into the earlier agent (one
    agent doing both can't conflict with itself). Different roles →
    sequentialize: the later agent moves to the next wave and is told whose
    branch to consult. Empty/vague hints are exempt (don't fake precision).

    F4.2: returns the adjustments it made so the caller can emit
    PLAN_ADJUSTED events — a produce/consume dependency that would have
    dead-locked in the same wave is now visibly resequenced.
    """
    adjustments: list[dict] = []
    merged_away: set[int] = set()
    active = [i for i, m in enumerate(team) if not m.passive]
    for j_pos, j in enumerate(active):
        if j in merged_away:
            continue
        for i in active[:j_pos]:
            if i in merged_away:
                continue
            first, second = team[i], team[j]
            hits = _members_overlap(first, second)
            if not hits:
                continue
            if first.role == second.role:
                # Merge: one agent owns both briefs.
                first.subtask = (
                    f"{first.subtask}\n\nAdditionally: {second.subtask}"
                    if second.subtask else first.subtask
                )
                first.files_hint = sorted(
                    {*(first.files_hint or []), *(second.files_hint or [])})
                first.max_turns = max(first.max_turns or 0, second.max_turns or 0) or None
                first.mcp_servers = sorted({*first.mcp_servers, *second.mcp_servers})
                merged_away.add(j)
                adjustments.append({
                    "kind": "merged", "files": hits, "role": first.role})
                logger.warning(
                    "Plan overlap on %s: merged duplicate %s briefs into one agent",
                    hits, first.role)
            else:
                second.wave = max(second.wave, first.wave + 1)
                second.predecessor_note = (
                    f"A {first.role} agent works on {', '.join(hits)} before you "
                    f"in this turn; its commits are on its hive/<session>/ branch "
                    f"— read them (git branch -a; git show) before starting."
                )
                adjustments.append({
                    "kind": "resequenced", "files": hits,
                    "consumer": second.role, "producer": first.role,
                    "wave": second.wave})
                logger.warning(
                    "Plan overlap on %s: %s sequenced after %s (wave %d)",
                    hits, second.role, first.role, second.wave)
            break
    for j in sorted(merged_away, reverse=True):
        del team[j]
    return adjustments


def _extract_first_json_object(text: str) -> dict | None:
    """Find the first complete JSON object by tracking brace depth."""
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    for i, ch in enumerate(text[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        if depth == 0:
            try:
                return json.loads(text[start : i + 1])
            except json.JSONDecodeError as exc:
                logger.warning("Planner JSON parse error: %s", exc)
                return None
    return None


def _fallback_team() -> TeamComposition:
    """Minimal single-builder team used when planning fails.

    Confidence is intentionally set ABOVE the 0.7 approval-gate
    threshold. A 0.5 confidence dropped the user into a "Approval
    needed (50%)" interrupt even in full-auto mode — surprising and
    annoying when the planner just failed transiently. The fallback
    is a known-safe single-Builder spawn; we trust it enough not to
    block on approval. Users in `checkpoint` / `manual` modes still
    see the gate (mode is the primary signal, confidence is secondary).
    """
    return TeamComposition(
        team=[TeamMember(role="Builder", model=DEFAULT_MODEL, count=1)],
        confidence=0.75,
        rationale="fallback: planning failed, using single Builder",
    )
