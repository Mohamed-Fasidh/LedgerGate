# Building and Evaluating AI Agents

**Duration:** 18 minutes

## Learning objectives
Distinguish an agent from a single model call; design bounded tool execution; validate arguments; enforce authorization outside the model; trace execution; and evaluate behavior with deterministic cases.

## Prerequisites
Python, Pydantic, REST APIs, authentication, and basic database concepts.

## Architecture whiteboard
Draw: authenticated request and tenant context → task interpretation → bounded plan → allowlisted tool selection → schema validation → application authorization → tenant-scoped query → validated/redacted result → persisted trace → structured response. Add deadlines, retries, cancellation, iteration limits, and human approval for sensitive actions. Emphasize that model output is a proposal, not a permission grant. Tool output is untrusted data, not instructions.

## Timed teaching script
- 0–3 min: compare a one-shot LLM response with a tool-using agent.
- 3–6 min: draw allowlist, schema validation, authorization, and execution boundary.
- 6–9 min: discuss retries, idempotency, deadlines, trace events, and cancellation.
- 9–13 min: inspect backend/app/agent/tool_executor.py and runtime.py.
- 13–16 min: inspect research/agent-evaluation and its deterministic dataset.
- 16–18 min: reason through prompt-injection and cross-tenant attack cases.

## Coding demonstration
From backend, run pytest -q tests/test_agent.py tests/test_tool_executor.py tests/test_mcp_server.py. Trace an invoice lookup and identify the tenant filter. Try an unknown tool and malformed arguments; confirm rejection. Run the agent-evaluation package against its versioned dataset. Distinguish measured fields from not_measured fields requiring production traces.

## Common mistakes
- Letting the model choose a tenant ID or override authenticated context.
- Treating schema validation as authorization.
- Retrying non-idempotent writes without idempotency protection.
- Returning raw exceptions or secrets.
- Measuring final-answer quality but ignoring tool selection, arguments, and recovery.
- Treating an LLM-as-judge score as ground truth.

## Applications
Enterprise workflow automation, evidence gathering, internal knowledge assistants, and human-supervised financial operations.

## Interview questions
1. Why must authorization be enforced outside the LLM?
2. How do retries interact with idempotency?
3. How can tool output inject malicious instructions?
4. Which metrics expose tool-selection errors?
5. How would you test tenant isolation?

## Student exercise
Add a deterministic test for a read-only tool: validate arguments, reject invalid input, assert tenant-scoped querying, and include one success and two failure cases in the evaluation dataset.

## Instructor note
LedgerGate's financial approval remains a human-controlled application operation. The agent does not gain authority to approve or pay invoices.
