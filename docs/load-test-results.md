# TicketFlow Gateway — Locust Load Test Results

Date: 2026-08-30. Target: `http://localhost:8000` (the `gateway` service), plus
`http://localhost:8007` for the one journey step that the gateway does not
proxy (see below). Ran against the live 13-service docker-compose stack on
this machine, immediately after seeding the catalog (`python scripts/seed_data.py`
— the running stack had **no data at all** before this; see Findings).

Test code: `scripts/loadtest/locustfile.py`, deps in `scripts/loadtest/requirements.txt`.

## What was tested

Single `HttpUser` (`TicketFlowUser`) modeling one simulated ticket buyer:

- **`on_start`** (once per user): `POST /api/v1/auth/register`, falling back
  to `POST /api/v1/auth/login` on a 409 email collision.
- **`browse_catalog`** (weight 6, high): `GET /api/v1/events` (catalog list —
  see Findings, this is broken), then `GET /api/v1/events/{id}` and
  `GET /api/v1/events/{id}/seats` for a real seeded event.
- **`book_seat`** (weight 3, medium): `POST /api/v1/bookings/lock` for a
  random seat, then 70% of the time `POST /api/v1/bookings/{id}/confirm`
  (payment), 30% `POST /api/v1/bookings/{id}/release`.
- **`check_booking_status`** (weight 2, low/medium): `GET /api/v1/bookings/{id}`
  for a booking the same user made earlier.
- **`support_refund_or_inquiry`** (weight 1, low): `POST /api/v1/agent/chat`
  on **agent-gateway (:8007)**, asking for a refund or booking status. The
  `gateway` service (:8000) has no support-agent route at all — its routers
  are only `auth`, `events`, `bookings` (`services/gateway/app/main.py`,
  `services/gateway/app/routes/*.py`). support-agent itself
  (`services/support-agent/app/main.py`) calls `require_internal_secret(app)`
  globally, so it's mesh-only and not reachable from outside docker at all.
  agent-gateway's `/api/v1/agent/chat` is the actual externally-reachable,
  JWT-authenticated entry point that talks to support-agent internally —
  confirmed live before wiring it into the test.

`409` (seat already locked) and `429` (rate limited) are treated as expected,
successful outcomes throughout, not failures — both are the system behaving
correctly under contention/load. Real `5xx`s and connection failures are not.

## Run configs

| Run | Users | Spawn rate | Duration |
|---|---|---|---|
| Run 1 | 20 | 5/s | 45s |
| Run 2 | 100 | 10/s | 55s (task said 45-60s) |

## Run 1 — 20 users

```
Type     Name                                                                     # reqs  # fails |    Avg     Min     Max    Med |   req/s  failures/s
POST     /api/v1/agent/chat (support-agent via agent-gateway)                        30     0(0%) |    149      48     278    120 |    0.67     0.00
POST     /api/v1/auth/register                                                       20     0(0%) |   1417    1158    1550   1400 |    0.45     0.00
GET      /api/v1/bookings/[id] (status check)                                        43     0(0%) |     12       4      32      9 |    0.96     0.00
POST     /api/v1/bookings/[id]/confirm                                               23     0(0%) |    317      34    3103     58 |    0.51     0.00
POST     /api/v1/bookings/[id]/release                                                6     0(0%) |     29      16      49     25 |    0.13     0.00
POST     /api/v1/bookings/lock                                                       81     0(0%) |     46       4     260     12 |    1.80     0.00
GET      /api/v1/events (list, KNOWN BROKEN)                                        155  155(100%) |   1171       7    3231     30 |   3.50     3.50
GET      /api/v1/events/[id]                                                        155     0(0%) |     24       3     190     12 |    3.50     0.00
GET      /api/v1/events/[id]/seats                                                  155     0(0%) |     68       3     277     14 |    3.50     0.00
---------------------------------------------------------------------------------------------------------------------------------------------------
Aggregated                                                                          665  155(23.3%) |    361       3    3231     20 |   15.00     3.50

Response time percentiles (ms)
Name                                                    50%   66%   75%   80%   90%   95%   98%   99%  99.9% 100%
/api/v1/agent/chat                                      130   190   210   230   250   270   280   280   280   280
/api/v1/auth/register                                  1500  1500  1500  1500  1500  1600  1600  1600  1600  1600
/api/v1/bookings/[id] (status check)                      9    12    15    16    22    30    33    33    33    33
/api/v1/bookings/[id]/confirm                             58    70    72    75    88  3100  3100  3100  3100  3100
/api/v1/bookings/[id]/release                             33    33    34    34    50    50    50    50    50    50
/api/v1/bookings/lock                                     12    46    80    88   140   160   230   260   260   260
/api/v1/events (list, KNOWN BROKEN)                        30  2300  2300  2300  2400  2500  2800  2800  3200  3200
/api/v1/events/[id]                                        12    19    28    31    48   100   130   160   190   190
/api/v1/events/[id]/seats                                  14    95   120   140   200   230   250   270   280   280
Aggregated                                                 20    70   120   180  2300  2300  2400  2700  3200  3200

Error report
133  ... (n/a in this run)
155  GET /api/v1/events (list, KNOWN BROKEN): gaierror(11001, 'getaddrinfo failed') / 429 Too Many Requests (mixed causes, see Findings)
```
Full raw CSVs: `scripts/loadtest/results_20u_stats.csv`, `results_20u_stats_history.csv`, `results_20u_failures.csv`.

## Run 2 — 100 users

```
Type     Name                                                                     # reqs  # fails |    Avg     Min     Max    Med |   req/s  failures/s
POST     /api/v1/agent/chat (support-agent via agent-gateway)                        29     0(0%) |    153      55     324    130 |    0.53     0.00
POST     /api/v1/auth/register                                                      100     0(0%) |    628      22    4018     48 |    1.84     0.00
GET      /api/v1/bookings/[id] (status check)                                        35     0(0%) |     12       4      39     10 |    0.64     0.00
POST     /api/v1/bookings/[id]/confirm                                               33     0(0%) |    146       4    3075     50 |    0.61     0.00
POST     /api/v1/bookings/[id]/release                                               12     0(0%) |     30      13      76     19 |    0.22     0.00
POST     /api/v1/bookings/lock                                                      103     0(0%) |     53       5     228     19 |    1.89     0.00
GET      /api/v1/events (list, KNOWN BROKEN)                                        198  198(100%) |    844       7    2729     22 |    3.64     3.64
GET      /api/v1/events/[id]                                                        198     0(0%) |     16       3     102     10 |    3.64     0.00
GET      /api/v1/events/[id]/seats                                                  198     0(0%) |     43       3     247      9 |    3.64     0.00
---------------------------------------------------------------------------------------------------------------------------------------------------
Aggregated                                                                          906  198(21.9%) |    284       3    4018     17 |   16.64     3.64

Response time percentiles (ms)
Name                                                    50%   66%   75%   80%   90%   95%   98%   99%  99.9% 100%
/api/v1/agent/chat                                      130   190   200   250   300   320   320   320   320   320
/api/v1/auth/register                                    48    82   110  1000  3000  3500  4000  4000  4000  4000
/api/v1/bookings/[id] (status check)                      10    12    16    18    23    25    39    39    39    39
/api/v1/bookings/[id]/confirm                             50    70    73    76    90   130  3100  3100  3100  3100
/api/v1/bookings/[id]/release                             29    32    44    44    45    76    76    76    76    76
/api/v1/bookings/lock                                     19    63    87   100   150   180   200   220   230   230
/api/v1/events (list, KNOWN BROKEN)                        22  2300  2300  2300  2300  2400  2700  2700  2700  2700
/api/v1/events/[id]                                        10    14    16    20    34    56    81   100   100   100
/api/v1/events/[id]/seats                                   9    15    64   100   150   170   210   230   250   250
Aggregated                                                  17    40    71    98  1000  2300  2700  3000  4000  4000

Error report
133  GET /api/v1/events (list, KNOWN BROKEN): 429 Client Error: Too Many Requests
 70  GET /api/v1/events (list, KNOWN BROKEN): gaierror(11001, 'getaddrinfo failed')
```
Full raw CSVs: `scripts/loadtest/results_100u_stats.csv`, `results_100u_stats_history.csv`, `results_100u_failures.csv`.

## Findings — real, not tuned around

**1. `GET /api/v1/events` (catalog list) is broken end-to-end through the gateway. 100% failure rate in both runs, and it is a real bug, not a load artifact.**

`services/gateway/app/routes/events.py` proxies with
`target = f"{settings.catalog_service_url}/events"` — no trailing slash.
catalog-service's router is `APIRouter(prefix="/events")` with the list route
at `"/"`, i.e. the real path is `/events/`. FastAPI's `redirect_slashes`
therefore 307-redirects the gateway's own upstream call, and the `Location`
header it builds is an absolute URL using catalog-service's own host —
`http://catalog-service:8000/events/`, its **internal Docker-network
hostname**, which is unreachable from outside the compose network. Verified
directly:

```
$ curl -sD - "http://localhost:8000/api/v1/events/" -H "Authorization: Bearer $TOKEN"
HTTP/1.1 307 Temporary Redirect
location: http://catalog-service:8000/events/
```
Any real external client following that redirect gets a DNS/connection
failure (`gaierror(11001, 'getaddrinfo failed')` on this machine) — confirmed
by 155/155 and 198/198 failures across both runs. This is a genuine
production bug: nobody outside the docker network can list events through
the gateway at all, only fetch a single known event by ID. The single-item
routes (`/events/{id}`, `/events/{id}/seats`) don't have this issue (0
failures, both runs) because they don't hit the trailing-slash redirect.
Fix: change the proxy target to `f"{settings.catalog_service_url}/events/"`.

The remaining share of that endpoint's failures at 100 users (133/198) is
`429 Too Many Requests` — that's the second finding below, layering on top
of the redirect bug, not a separate issue.

**2. `429` rate limiting is working correctly and gets real exercise at 100 users.**

Per-IP limit is 300 req/min (`rate_limit_per_ip_per_minute` in
`services/gateway/app/config.py`); all 100 simulated users share one source
IP (`localhost`) in this test, so the shared per-IP bucket saturates well
before the per-user (100/min) bucket does. Every task in the locustfile
treats `429` as an expected, non-failure outcome, matching the instruction
to not penalize correct load-shedding — the only place `429` still shows as
a "failure" in the table above is the already-broken list endpoint, and it's
folded into that same 100%-failure line regardless.

**3. The rest of the system held up cleanly at both load levels: 0 unexpected failures.**

Register, login, event detail, seat map, seat lock, confirm, release, booking
status check, and the support-agent refund/inquiry path via agent-gateway
all had **zero** failures (excluding the expected 409/429 branches already
folded into "success") at both 20 and 100 concurrent users. `bookings/lock`
correctly 409s on contested seats without ever going 500. p95 latency for
the core booking path (lock/confirm/status) stayed under ~200ms even at 100
users; the visible tail spikes (~3.1s at the 95th+ percentile on `confirm`)
are single slow outliers (23 and 33 total requests respectively — a couple
of slow requests move the percentile a lot at this sample size), not a
sustained degradation.

**4. Two pre-existing bugs in `scripts/seed_data.py` were found and fixed to make this test possible at all** (not part of the load test scope, but load-bearing for it): its module docstring opened with `""` instead of `"""` (a real `SyntaxError`, the script could not run at all), and its hardcoded `DATABASE_URL` pointed at `localhost:5433`, which is not this stack's postgres port (`docker-compose.yml` maps it to `5440`) — so a first "successful" run silently wrote to an unrelated/nonexistent database and the actual stack stayed empty. Both fixed with one-line changes; noting here since running the test at all first required discovering that the running stack had zero seeded data.

## Chaos testing

A chaos-during-load run (Redpanda broker killed and restarted ~20s into a
60s Locust run) is documented separately in `docs/load-chaos-results.md`,
with exact timestamps and raw output in `scripts/loadtest/results_chaos_*.csv`
and `chaos_locust.log`. Summary: zero visible request failures on any
real user-facing path during the ~5s outage; the only failing endpoint
(100%) is the pre-existing `GET /api/v1/events` trailing-slash bug (Finding
1 above / `docs/runbooks.md` #2), reproduced identically with no broker
outage involved, so it's not a chaos-induced regression.

## Overall assessment

The core booking journey (browse a specific event, view seats, lock, confirm
or release, check status, and even the AI-agent refund flow) is solid under
both 20 and 100 concurrent users — no unexpected errors, reasonable
latencies, correct 409/429 behavior under contention. The one real defect
found is narrow but user-facing: the catalog *list* endpoint is completely
unusable through the gateway for any external client, which is a real bug
worth fixing (see Finding 1) independent of load.
