# Runbooks

Real incidents hit during this project, with the evidence for each (commit,
file, or script) cited rather than asserted. Only incidents with concrete
evidence in the repo are included.

## 1. Service crash-loops after Postgres is slow to come up (host sleep)

**Symptom:** After a laptop sleep/resume, `docker-compose`'s Postgres
container takes longer than usual to accept connections. Any app service
that hits its DB-connect-on-startup before Postgres is ready exits
immediately, and because `restart: on-failure` does not re-check
`depends_on: condition: service_healthy` on restart, the container just
crash-loops instead of waiting.

**Root cause:** every service did exactly one connect attempt on startup
(`engine.begin()` in `init_db()`), with no retry — first bad request out of
the gate is fatal.

**Resolution:** added `libs/resilience/retry.py` (`retry_with_backoff`):
bounded exponential backoff (5 attempts, 0.5s base delay doubling up to an
8s cap, +/-25% jitter), wrapped around each service's DB-connect-on-startup.
Wired into `init_db()` for all 13 app services. Succeeds on the first try
with zero added delay in the normal case, so it's a no-op once Postgres is
actually up. Commit `ce41997` ("Part 1: docker startup hardening + gateway
trailing-slash fix"), with unit tests in
`libs/resilience/tests/test_retry.py`.

**Prevention:** any new service's `database.py` must call its DB-connect
through `retry_with_backoff`, not a bare `engine.begin()` — check
`services/*/app/database.py` for the existing pattern before adding a new
one.

## 2. Gateway `/api/v1/events` returns empty/broken results (307 redirect swallowed)

**Symptom:** requests through the gateway to list events came back wrong —
the gateway was silently unable to reach catalog-service for this one route.

**Root cause:** `services/gateway/app/routes/events.py` proxied a bare
`/events` to catalog-service. catalog-service's router is mounted at
`prefix="/events"` with the list route registered at `"/"`, so FastAPI
307-redirects a bare `/events` request to `/events/` — but on catalog-service's
*internal Docker hostname*, which is unreachable from outside the Docker
network, and the gateway's httpx client doesn't follow redirects anyway.

**Resolution:** changed the proxy target to request `/events/` directly
(trailing slash), avoiding the redirect entirely. Regression test added:
`test_events_proxy_uses_trailing_slash` in `services/gateway/tests/test_gateway.py`,
which mocks only the trailing-slash URL so a regression to the bare path
fails the test via respx's `AllMockedAssertionError`. Commit `ce41997`.

**Prevention:** when proxying to any service whose router uses
`prefix="/x"` + a `"/"` list route, always request the collection URL with
the trailing slash from the proxy side — don't rely on redirect-following
across the internal Docker network.

## 3. Chaos scripts for broker/consumer failure (kill-and-recover)

Two PowerShell scripts exist for manually exercising failure paths against
the live docker-compose stack. **Not yet run as part of a load test** — see
`docs/load-test-results.md` / `docs/load-chaos-results.md` for load-only
results; a chaos-during-load run is a separate, still-pending step.

### `scripts/chaos_kill_broker.ps1`

Stops and restarts the `redpanda` (Kafka-compatible broker) container.
Verifies consumers resume from their last committed offset after the broker
comes back — i.e. that no message in flight during the outage is lost.

Usage: `./scripts/chaos_kill_broker.ps1`, then watch consumer lag drain back
to zero in redpanda-console (`http://localhost:8080`).

### `scripts/chaos_kill_consumer.ps1`

Stops and restarts `notification-service` mid-stream. Verifies it resumes
consuming without losing or double-processing events: the design relies on
the Kafka offset not being committed until `handle_envelope` succeeds, plus
an idempotency table to protect against replaying an already-handled
message after restart.

Usage: `./scripts/chaos_kill_consumer.ps1`, then check
notification-service logs / DB rows for no gaps and no duplicates.

**Status:** both scripts are real and already in the repo; neither has been
executed as part of this session's work (out of scope — the live docker
stack needs to stay untouched for a parallel process). Running them for
real, ideally concurrent with a load test, is pending follow-up work.
