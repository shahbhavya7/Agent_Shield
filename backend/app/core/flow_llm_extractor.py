"""LLM-assisted flow extraction — the fallback path used ONLY when
app.core.flow_parser's deterministic scanner cannot confidently find a node collection
in an uploaded flow file (i.e. app.core.flow_parser.parse_flow raised
FlowAmbiguousError).

Reuses the existing isolated app.core.llm.chat() — the same infra
app.core.scenarios/app.core.node_script already use — so there is no second OpenAI
integration. Its output is run through the EXACT SAME node/edge validation as the
deterministic tolerant path (app.core.flow_parser.normalize_nodes_tolerant /
normalize_explicit_edges): a hallucinated or malformed extraction is rejected the same
way a malformed deterministic file would be. The LLM's output is never trusted blindly.
"""
from app.core.flow_parser import normalize_explicit_edges, normalize_nodes_tolerant
from app.core.llm import chat

# How much of the uploaded file is sent to the model. Mirrors
# app.core.scenarios.MAX_KNOWLEDGE_CHARS — the cap only exists to stop a pathologically
# large paste, not to trim a normal flow file.
MAX_SOURCE_CHARS = 60000


class FlowExtractionError(ValueError):
    """Raised with every validation problem found in the LLM's output — never silently
    repaired, same discipline as app.core.node_script's generation path.
    """

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


SYSTEM_PROMPT = """You extract conversation-flow structure from an arbitrary voice-agent
configuration file (JSON or YAML, given to you below as text). Respond ONLY with a json
object.

STRICT RULES — every one of these matters:
- Extract ONLY nodes/states/steps that are ACTUALLY PRESENT in the source text. Never
  invent a node with no corresponding object in the source.
- Never invent a "purpose" that isn't supported by the source's own text (a prompt,
  instruction, or description field, etc). If nothing describes a node's purpose, return
  an empty string for it rather than guessing.
- Never invent a transition/edge. Include an edge ONLY if the source text itself
  explicitly encodes a transition (e.g. a next/goto/transitions field, or an explicit
  from/to or source/target pair). Do NOT add an edge just because one node happens to
  appear before another in the file.
- If you are not confident an object represents a conversational node/state/step, omit
  it rather than guessing.
- Every extracted node must have direct evidence in the source — no exceptions.
- Return valid JSON only, in exactly this shape:
{
  "agent_name": "<string, or omit/empty if not present in the source>",
  "nodes": [
    {"id": "...", "name": "...", "type": "...", "purpose": "..."}
  ],
  "edges": [
    {"from": "...", "to": "..."}
  ]
}
If you cannot confidently identify any nodes at all, return {"nodes": [], "edges": []}."""


async def extract_flow_with_llm(source_text: str, source_format: str) -> dict:
    """Ask the model to extract nodes/edges from `source_text`, then validate its
    output with the same rules the deterministic path uses.

    Raises FlowExtractionError if the model's output fails validation or contains no
    usable nodes.
    """
    content = source_text if len(source_text) <= MAX_SOURCE_CHARS else source_text[:MAX_SOURCE_CHARS]

    result = await chat(
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": content}],
        json_mode=True,
    )
    if not isinstance(result, dict):
        raise FlowExtractionError(["LLM did not return a JSON object."])

    raw_nodes = result.get("nodes")
    if not isinstance(raw_nodes, list) or not raw_nodes:
        raise FlowExtractionError(["LLM could not identify any conversation nodes in this file."])

    nodes, node_errors = normalize_nodes_tolerant(raw_nodes, "llm.nodes")
    if node_errors:
        raise FlowExtractionError(node_errors)

    seen_ids = {n["id"] for n in nodes}
    raw_edges = result.get("edges") or []
    if not isinstance(raw_edges, list):
        raw_edges = []
    edges, edge_errors = normalize_explicit_edges(raw_edges, seen_ids)
    if edge_errors:
        raise FlowExtractionError(edge_errors)

    agent_name = result.get("agent_name") or "Unnamed Agent"
    return {
        "agent_name": str(agent_name),
        "nodes": nodes,
        "edges": edges,
        "source_format": source_format,
        "extraction_method": "llm",
    }
