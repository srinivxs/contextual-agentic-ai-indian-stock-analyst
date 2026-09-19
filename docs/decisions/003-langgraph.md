# 003 — LangGraph as a controlled workflow

- **Status:** Accepted
- **Date:** 2026-09-19

## Context

The challenge requires the agent to be built on LangGraph/LangChain. The agent
must ground every claim in retrieved sources, cite them, say "I don't have that
in the data" when unsupported, and update investor memory from chat.

## Decision

Use **LangGraph** to implement each chat turn as an explicit, mostly
deterministic **workflow**, not a free-roaming ReAct loop:

`load_context` and `extract_memory` and `classify_intent` (parallel) →
`resolve_entities` → route (`retrieve_evidence` | `recommend` | `profile_ack` |
`out_of_scope`) → `grade_evidence` (else `abstain`) → `generate_answer` →
`validate_citations` (retry once, then `degrade`/`abstain`) → `render_answer` →
`persist_turn`.

- The **LLM decides intent and writes prose**; **code decides control flow**,
  retrieval, ranking, validation, and formatting.
- Tools are plain in-process Python functions. **No MCP** for now; the same
  functions could be exposed as an MCP server later.
- **No LangGraph checkpointer.** Conversation history and citations live in our
  own `messages` tables because the UI needs to query them, and we have no
  interrupt/resume flow. Per-turn graph state is short-lived.
- LangChain is used only for thin LLM/structured-output wrappers.
- **AWS AgentCore is not used** (optional in the challenge; an extra runtime with no requirement behind it).

## Alternatives considered

| Option | Why not |
|---|---|
| Single LLM call from FastAPI | Cannot express the retry, abstain, and routing branches cleanly; harder to test node by node. |
| Plain Python functions (no framework) | Genuinely viable for a fixed pipeline; but LangGraph is required by the challenge and its explicit state/edges document the flow well. |
| Autonomous ReAct agent with many tools | Less predictable, harder to guarantee grounding, more LLM calls (cost). |
| LangMem / Bedrock agentic memory | Generic memory stores; we need typed fields that deterministic code can filter on. |

## Reasoning

Explicit state and conditional edges make the anti-hallucination path
inspectable: evidence grading and citation validation are deterministic nodes the
LLM cannot skip. Each node is unit-testable in isolation.

## Tradeoffs

- For a fixed pipeline, LangGraph adds a dependency and concepts; the benefit is
  clarity and branching, not raw capability.
- Without a checkpointer we forgo built-in time-travel/resume; acceptable here.

## Amendment (2026-09-20): MVP workflow simplified

The MVP scope was reduced (ADRs 007–009), so the graph is kept deliberately small and explicit:

`analyze` → `update_memory` → `retrieve` → `grade` (else `abstain`) → `generate` → `validate`
(retry once, then drop claims or abstain) → `respond`

The parallel fan-out and separate entity-resolution node described above are folded into `analyze`
and `retrieve`. For a "recommend" question, `retrieve` also runs the deterministic matcher over the
stored facts. One agent, no checkpointer, no MCP, in-process tools. The design principles above are
unchanged: the LLM decides intent and writes prose; code decides the flow and validates the output.
