# Load + Chaos Test: Redpanda Broker Kill During Active Load

## What was run

`scripts/loadtest/locustfile.py`, headless, 30 users, spawn rate 5/s, 60s run,
against the live `gateway` service (`http://localhost:8000`). ~20s into the
run, `redpanda` (the Kafka-compatible broker booking-service publishes events
to, and notification-service/analytics-service consume from) was stopped and
restarted 3 seconds later via `docker stop redpanda && docker start redpanda`
— the same action as `scripts/chaos_kill_broker.ps1`, run manually so it could
be timed against the in-flight load.

Timestamps (UTC): broker stopped 21:41:27, restarted 21:41:32 (~5s outage,
including container start time).

Raw output: `scripts/loadtest/results_chaos_stats.csv`, `chaos_locust.log`.

## Results

1503 total requests over 60s, 25.3 req/s aggregate.

| Endpoint | Requests | Failures | p50 | p95 | p99 |
|---|---|---|---|---|---|
| POST /api/v1/agent/chat | 64 | 0 | 96ms | 400ms | 1200ms |
| POST /api/v1/auth/register | 30 | 0 | 2000ms | 2300ms | 2400ms |
| GET /api/v1/bookings/{id} | 79 | 0 | 8ms | 30ms | 58ms |
| POST /api/v1/bookings/{id}/confirm | 25 | 0 | 55ms | 3000ms | 3100ms |
| POST /api/v1/bookings/{id}/release | 9 | 0 | 26ms | 44ms | 44ms |
| POST /api/v1/bookings/lock | 174 | 0 | 8ms | 170ms | 310ms |
| GET /api/v1/events (list, no trailing slash) | 374 | 374 (100%) | 12ms | 2600ms | 2900ms |
| GET /api/v1/events/{id} | 374 | 0 | 6ms | 58ms | 180ms |
| GET /api/v1/events/{id}/seats | 374 | 0 | 6ms | 250ms | 340ms |

**Every booking-flow, auth, and agent-chat request succeeded (0 failures)
through the broker outage** — booking-service's Kafka publish path degraded
without surfacing errors to callers, and notification-service/booking-service
never restarted or crash-looped (`docker compose ps` post-run: both `Up`,
no `RestartCount` increase).

The only failing endpoint (374/374, 100%) is `GET /api/v1/events` called
*without* a trailing slash — a pre-existing, documented issue unrelated to
this chaos test (see `locustfile.py`'s module docstring and
`docs/load-test-results.md`): the gateway's own route redirects on the
missing slash, and Locust's HTTP client doesn't follow the redirect to
catalog-service's internal Docker hostname. This reproduced identically with
no broker outage in the earlier 20u/100u runs, so it is not a chaos-induced
regression — it's isolated to that one un-fixed call pattern.

## Takeaway

A 5-second broker outage mid-load caused zero visible request failures on
any real user-facing path. This is consistent with booking-service treating
the Kafka publish as fire-and-forget from the request's perspective (the
booking itself commits in Postgres independent of the broker), which is the
right trade-off for this domain — a booking should not fail because the
event bus blipped.
