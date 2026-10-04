# Support Agent (port 8009)

Customer service agent: booking lookups, refund eligibility, cancellations, policy Q&A,
and human escalation. No LLM anywhere in this service.

## Refund-safety guarantee

`request_refund` **never** trusts a caller-stated amount. It always calls
`check_refund_eligibility` first (which runs `RefundRulesEngine.check_eligibility`,
a pure deterministic function — no LLM, no guessing) and refunds the
rules-engine-computed eligible amount. If the requested amount differs, the
response text explains the discrepancy instead of silently ignoring it or
trusting the user. See `app/rules_engine.py` and `tests/test_rules_engine.py`
(all 4 rules + boundary edges: exactly 24h, exactly 7 days, event already
passed) and `tests/test_tools.py::test_request_refund_uses_rules_engine_amount_not_user_amount`.

Rules (in order):
1. Event cancelled by organizer -> 100%
2. Event starts within 24 hours (or has passed) -> 0%
3. Event starts within 7 days -> 50%
4. Event starts more than 7 days out -> 100%

## Tools

- `get_booking_details(booking_id, user_bearer_token)` — GET booking-service `/bookings/{id}`.
- `check_refund_eligibility(booking_id, user_bearer_token)` — fetches booking + event (catalog-service), applies `RefundRulesEngine`.
- `cancel_booking(booking_id, reason, user_bearer_token)` — POST booking-service `/bookings/{id}/release` (the real cancel endpoint).
- `request_refund(booking_id, amount, user_id, user_bearer_token)` — resolves `payment_id` via payment-service's internal by-booking lookup, then POSTs `/payments/{id}/refund` with the eligible amount; records a local `refund_requests` row.
- `search_policy(query)` — POST rag-service `/search` with `category="support"`.
- `create_support_ticket(...)` / `escalate_to_human(...)` — local DB only.

## HTTP contract

`POST /handle`
```json
{"conversation_id": "...", "user_id": "...", "message": "...", "intent": "BOOKING_INQUIRY|REFUND_REQUEST", "user_bearer_token": "..."}
```
->
```json
{"response_text": "...", "ticket_id": "uuid|null", "escalated": false}
```

`GET /health` -> `{"status": "ok"}`

No auth on this service's own endpoints (internal, called only by the Agent
Gateway). The end user's JWT arrives as the `user_bearer_token` body field and
is forwarded as a real `Authorization: Bearer <token>` header on outbound
calls to booking-service, per the project-wide documented exception.

## Real downstream endpoints used

- `GET  {booking_service_url}/bookings/{booking_id}` — requires `Authorization: Bearer <jwt>`. Response has `event_id` only, not the event date — event date/status come from catalog-service.
- `POST {booking_service_url}/bookings/{booking_id}/release` — requires the same bearer auth. This is the real cancel endpoint.
- `GET  {catalog_service_url}/events/{event_id}` — no auth. Returns `event_date` and `status` (`DRAFT`/`PUBLISHED`/`CANCELLED`/`COMPLETED`) — used for both refund date-math and organizer-cancellation detection.
- `GET  {payment_service_url}/internal/payments/by-booking/{booking_id}` — no auth. Returns `{payment_id, booking_id, amount, currency, status}`.
- `POST {payment_service_url}/payments/{payment_id}/refund` — no auth, body `{"amount": float, "reason": str}`. Returns 409 if `payment.status != "SUCCEEDED"`; handled gracefully (refund request recorded as `DENIED`, no crash).
- `POST {rag_service_url}/search` — body `{"query": str, "category": str, "top_k": int}` (not the `/api/v1/rag/search` path from the original spec — matches the RAG service as actually built).

## Deviations from the literal Phase 3 spec

- Dropped `policy_documents` / `knowledge_base` tables — policy content lives centrally in the RAG service.
- No LLM synthesis: `search_policy` returns the RAG top hit verbatim, or triggers escalation if there are no hits.
- `/handle` performs the refund as a single-turn action (check eligibility -> refund immediately) rather than a confirm-then-execute two-turn flow. A real product would ask for confirmation before actually moving money; noted here rather than built, to keep this iteration's scope to one call.
- Booking-inquiry routing: if a UUID booking ID appears in the message, look it up directly; otherwise treat the message as a general policy question and route to `search_policy`.

## Running

```
uvicorn app.main:app --port 8009
```

## Tests

```
pytest
```
25 tests: `RefundRulesEngine` (all 4 rules + boundary edges), booking-ID/amount
regex extraction, tool functions with mocked downstream services (incl. the
refund-amount-discrepancy case), and `/handle` integration tests including a
downstream failure that resolves to a graceful escalation response instead of
a 500.
