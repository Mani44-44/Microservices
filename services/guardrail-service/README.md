# Guardrail Service (port 8011)

Deterministic, rule-based safety checks for TicketFlow's AI agents. No LLM
calls — this project has no LLM API keys, so detection is regex/keyword
heuristics, not semantic understanding. See docstrings in `app/checks.py`
for each heuristic's documented limitations.

## Endpoints

- `POST /guardrails/check-input` — `{"text": str}` -> prompt-injection +
  PII detection on user input before it reaches an agent/LLM.
- `POST /guardrails/check-output` — `{"text": str, "context_chunks": [str]}`
  -> PII redaction + hallucination heuristic on agent output before it's
  shown to a user.
- `GET /health`

No auth — internal use only, must not be exposed publicly.

## Thresholds / design choices

- **Injection block threshold: `risk_score >= 0.7`** (configurable via
  `injection_block_threshold`). Each matched pattern has a hand-assigned
  weight (0.4-0.9); weights sum and cap at 1.0, so a couple of weak
  signals stacking up can also cross the line.
- **`check-input` also blocks on any PII found** (not just high injection
  score) — raw PII must never be forwarded to an LLM prompt.
- **`check-output` blocks only on PII leakage.** A flagged hallucination
  is reported (`hallucination_flagged`) but does not block, since the
  heuristic (unsupported numbers/percentages not present in the context
  chunks) is coarse and would make any quantitative answer from the
  Support Agent look suspect.
- PII redaction order (EMAIL, SSN, CREDIT_CARD, PHONE) matters: more
  specific patterns are matched and stripped first so a credit card
  number isn't also double-counted as a phone number.

## Deviation from the literal spec

`phase3_prompt.md` documents a fuller schema (`pii_incidents`,
`injection_attempts` tables) and more endpoints (`/redact`, `/incidents`,
`/stats`). The task instructions for this service explicitly scoped the
contract down to `check-input` / `check-output` / `health` with a single
`guardrail_checks` audit table — implemented exactly that narrower
contract and did not add the extra tables/endpoints.

## Run

```
uvicorn app.main:app --port 8011
```

## Test

```
pytest
```

`tests/test_checks.py` includes the literal red-team strings from
`phase3_prompt.md` (prompt injection + PII exfiltration attempts) as
parametrized tests.
