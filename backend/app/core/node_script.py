"""Node script generation for flow-aware / node-based voice testing (Phase 2).

Turns a selected flow node (+ its prerequisite path) into:
  - a concise, test-oriented "test_goal" focused on THAT node
  - a deterministic, turn-by-turn caller script:
    [{"expected_agent_behavior": str, "caller_line": str}, ...]

This module is authoring-time only, called once per "Generate Test Script" click. It
never runs during a scenario's execution: app.core.runner, app.core.ai_caller and
app.core.voice_caller are untouched and know nothing about this module. A later phase
flattens a reviewed script into the existing scenario shape — seed_turns =
[t["caller_line"] for t in script], expected_behavior = the numbered
expected_agent_behavior values — so the EXISTING scripted runner (_run_scripted) plays
it verbatim. There is no runtime AI-Caller improvisation here: the LLM writes the
script once, a human reviews/edits it, and only the reviewed caller lines are ever
spoken.

CRITICAL role separation (mirrors app.core.ai_caller's docstring): "caller_line" is
what the SIMULATED CUSTOMER says. It must never mention nodes, flows, graphs,
functions, APIs, internal state, workflow names, traces, or any other implementation
detail of the Voice Agent under test — a real caller has no idea any of that exists.
"expected_agent_behavior" is an evaluation reference for the Judge only — it is NEVER
given to the Voice Agent as a line to speak, and the real Voice Agent's response is
never scripted or assumed.
"""
import json
from typing import Optional

from app.core.llm import chat

NODE_TEST_TYPE = "flow_node"

# Prerequisite turns + the node-under-test's own turns, combined. MUST match
# app.core.runner.MAX_TESTER_TURNS: _run_scripted() (reused unmodified to execute a
# saved node script — see Phase 3) hard-stops after that many tester turns regardless
# of how many seed_turns are stored, so a script longer than this would have its tail
# turns silently never played. runner.py is deliberately not touched by this feature,
# so this cap follows it rather than the other way around.
MAX_SCRIPT_TURNS = 5


class NodeScriptError(ValueError):
    """Raised with every validation problem found, not just the first. Malformed model
    output is rejected outright here — never silently repaired.
    """

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


def validate_script(script: object) -> list[dict]:
    """Validate `script` is a well-formed node script; return it normalized (order
    preserved, both fields trimmed). Raises NodeScriptError otherwise.
    """
    if not isinstance(script, list) or not script:
        raise NodeScriptError(["script must be a non-empty array of turns."])

    errors: list[str] = []
    if len(script) > MAX_SCRIPT_TURNS:
        errors.append(f"script has {len(script)} turns; the maximum is {MAX_SCRIPT_TURNS}.")

    turns: list[dict] = []
    for i, t in enumerate(script):
        if not isinstance(t, dict):
            errors.append(f"script[{i}] must be an object.")
            continue
        caller_line = t.get("caller_line")
        behavior = t.get("expected_agent_behavior")
        if not isinstance(caller_line, str) or not caller_line.strip():
            errors.append(f"script[{i}].caller_line must be a non-empty string.")
            continue
        if not isinstance(behavior, str) or not behavior.strip():
            errors.append(f"script[{i}].expected_agent_behavior must be a non-empty string.")
            continue
        turns.append({
            "expected_agent_behavior": behavior.strip(),
            "caller_line": caller_line.strip(),
        })

    if errors:
        raise NodeScriptError(errors)
    return turns


def prerequisite_path(nodes: list[dict], edges: list[dict], target_id: str) -> list[dict]:
    """The chain of nodes that must be passed through before `target_id`, root-first,
    NOT including the target itself.

    Deliberately NOT a graph engine: this just walks one predecessor per step (a node
    with several incoming edges uses the first one found in `edges`), which is enough
    to flatten a linear onboarding path into one ordered script without building
    branch-choice UI or any execution-time graph state. A cycle or missing node simply
    stops the walk early rather than looping forever.
    """
    by_id = {n["id"]: n for n in nodes}
    parents: dict[str, str] = {}
    for e in edges:
        parents.setdefault(e["to"], e["from"])  # first-found predecessor wins

    chain: list[str] = []
    seen: set[str] = {target_id}
    current = target_id
    while current in parents:
        prev = parents[current]
        if prev not in by_id or prev in seen:
            break
        chain.append(prev)
        seen.add(prev)
        current = prev

    chain.reverse()
    return [by_id[nid] for nid in chain]


SYSTEM_PROMPT = """You are a test-script writer for a SCRIPTED voice-agent tester. You write \
a deterministic, turn-by-turn test script for ONE node in a voice agent's conversation flow. \
Respond ONLY with a json object.

ROLE SEPARATION — read carefully:
- You are writing lines for the CALLER (a simulated customer), never for the Voice Agent under
  test. The Voice Agent is a black box; its real responses will be captured live later, never
  scripted. "expected_agent_behavior" is an EVALUATION reference for a judge, not a line anyone
  will say out loud — never write a literal sentence for the agent to speak.
- The caller must sound like a real human customer on a phone call: natural, concise, spoken
  language. NEVER mention nodes, flows, graphs, functions, APIs, internal state, workflow names,
  traces, or any other implementation detail of the system under test — a real caller has no
  idea any of that exists and would never reference it.
- Keep the caller's lines SHORT and to the point. Avoid unnecessary chit-chat.

WHAT YOU RECEIVE:
- The node under test (its name, type, purpose, and expected_inputs if any).
- Any PREREQUISITE nodes that must be passed through first, in order, to reach the node under
  test in a real conversation (their name/purpose only).
- A test intent (either given by the user, or left for you to infer from the node's purpose).

WHAT TO PRODUCE:
{
  "test_goal": "one or two sentences describing WHAT this test verifies, concise and
                test-oriented, focused ONLY on the node under test — never a generic summary
                of the whole flow (e.g. never 'verify greeting, auth and account help')",
  "script": [
    {"expected_agent_behavior": "...", "caller_line": "..."},
    ...
  ]
}

SCRIPT RULES:
- If prerequisite nodes are given, the script MUST begin with one short, natural, FULLY
  COOPERATIVE caller turn per prerequisite node (in the given order) that plausibly gets a real
  agent through that step — e.g. a greeting node gets a simple opening line; an authentication
  node gets a turn where the caller gives correct-looking information. These prerequisite turns
  exist ONLY to put a real agent into the right state — they are not what this test is about,
  and the goal must not describe them as the point of the test.
- After the prerequisite turns, write the turns that actually TEST the node under test. This is
  where the test intent lives — including deliberately incorrect/edge-case caller lines if the
  test intent calls for it (e.g. giving a wrong name before the right one).
- Every turn needs both fields. "caller_line" is EXACTLY what the simulated caller will say,
  verbatim, with no further improvisation — write it as a complete, natural spoken sentence.
- "expected_agent_behavior" describes what a good agent should do in response to that caller
  line — never a literal sentence for the agent to say, always a description of correct
  behavior a judge can check the real transcript against.
- Use at most 5 turns total (prerequisites + node-under-test turns combined). Prefer fewer,
  focused turns over padding."""


def _describe_node(node: dict) -> str:
    lines = [f"name: {node.get('name')}", f"id: {node.get('id')}"]
    if node.get("type"):
        lines.append(f"type: {node['type']}")
    if node.get("purpose"):
        lines.append(f"purpose: {node['purpose']}")
    if node.get("expected_inputs"):
        lines.append(f"expected_inputs: {', '.join(node['expected_inputs'])}")
    return "\n".join(lines)


def _build_prompt(
    node: dict, prerequisite_nodes: list[dict], user_goal_hint: Optional[str]
) -> str:
    parts = ["NODE UNDER TEST:", _describe_node(node)]
    if prerequisite_nodes:
        parts.append("\nPREREQUISITE NODES (in order, must be passed through first):")
        for i, p in enumerate(prerequisite_nodes, 1):
            parts.append(f"{i}. {_describe_node(p)}")
    else:
        parts.append("\nThis node has no prerequisites — the script opens directly on it.")
    hint = (user_goal_hint or "").strip()
    parts.append(
        "\nTEST INTENT: "
        + (hint if hint else
           "(none given — infer an appropriate, concrete test from the node's own "
           "purpose and expected_inputs.)")
    )
    parts.append("\nGenerate the test_goal and script now as json.")
    return "\n".join(parts)


async def generate_node_script(
    node: dict,
    prerequisite_nodes: list[dict],
    user_goal_hint: Optional[str] = None,
) -> dict:
    """Generate {"test_goal": str, "script": [...]} for `node` via one LLM call.

    `prerequisite_nodes` (root-first, NOT including `node` itself — see
    prerequisite_path()) are woven in as plain pass-through turns ahead of the
    node-under-test's own turns. `user_goal_hint`, if given, steers what the final
    test_goal verifies; otherwise one is written from the node's own purpose/
    expected_inputs alone.

    Raises NodeScriptError if the model's output fails validation. Unlike
    app.core.scenarios/app.core.ai_caller, there is deliberately NO canned fallback
    bank here: a malformed script must surface as a clear error for the user to
    retry/edit, never a silently substituted generic script.
    """
    result = await chat(
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": _build_prompt(node, prerequisite_nodes, user_goal_hint)}],
        json_mode=True,
    )

    if not isinstance(result, dict):
        raise NodeScriptError(["Model did not return a JSON object."])

    test_goal = result.get("test_goal")
    if not isinstance(test_goal, str) or not test_goal.strip():
        raise NodeScriptError(["Model did not return a non-empty 'test_goal' string."])

    script = validate_script(result.get("script"))
    return {"test_goal": test_goal.strip(), "script": script}


def to_scenario_dict(flow_id: int, node_id: str, node_name: str, test_goal: str, script: list[dict]) -> dict:
    """Flatten a reviewed node script into the EXISTING scenario/test-case shape
    (app.core.scenarios._normalize's canonical fields), so it can be saved and run
    through the unmodified existing pipeline (Phase 3):

      seed_turns        = [turn["caller_line"] for turn in script]   (played verbatim
                           by the existing app.core.runner._run_scripted — never
                           app.core.ai_caller, since customer_context stays empty)
      expected_behavior  = the expected_agent_behavior values, numbered, for the
                           existing Judge (app.core.judge) to grade the REAL Voice
                           Agent responses against — never shown to the caller/agent
      user_goal          = test_goal, so the existing Judge prompt's "goal:" line
                           carries the node's test intent as evaluation context
      test_type          = NODE_TEST_TYPE ("flow_node"), assigned_fault = "none"

    `script` is assumed already validated (see validate_script) — this function does
    not re-validate it.
    """
    seed_turns = [turn["caller_line"] for turn in script]
    expected_behavior = "\n".join(
        f"{i}. Expected behavior: {turn['expected_agent_behavior']}"
        for i, turn in enumerate(script, start=1)
    )
    return {
        "title": f"Flow node: {node_name}",
        "user_goal": test_goal,
        "test_type": NODE_TEST_TYPE,
        "assigned_fault": "none",
        "expected_behavior": expected_behavior,
        "seed_turns": seed_turns,
        # Empty, deliberately: this forces app.core.runner.run_scenario() onto the
        # SCRIPTED path (_run_scripted), never the dynamic AI Caller — the whole point
        # of a flow-node test is that the caller improvises nothing.
        "customer_context": "",
        "max_turns": None,
        "flow_id": flow_id,
        "node_id": node_id,
        "node_script_json": json.dumps(script),
    }
