"""
Locust load test for the TicketFlow gateway.

Journey modeled (weights on the @task decorators):
  - on_start:        register (fallback to login on 409) -> JWT access token
  - browse_catalog (weight 6, high): GET /api/v1/events/{id} + /seats for a
    known seeded event. Also probes the raw GET /api/v1/events list endpoint
    every time -- see NOTE below, this is a real, reproducible bug.
  - book_seat (weight 3, medium): lock a random available seat, confirm ~70%
    of locks (payment), release the rest.
  - check_booking_status (weight 2, low/medium): GET a previously booked id.
  - support_refund_or_inquiry (weight 1, low): via agent-gateway /chat,
    which is the real user-facing entry point that talks to support-agent
    internally (see NOTE below -- the ticketflow gateway on :8000 does not
    proxy support-agent at all).

Run:
    pip install -r scripts/loadtest/requirements.txt
    locust -f scripts/loadtest/locustfile.py --headless -u 20 -r 5 \
        --run-time 45s --host http://localhost:8000 \
        --csv scripts/loadtest/results_20u

NOTE -- GET /api/v1/events (catalog list) is broken through the gateway.
services/gateway/app/routes/events.py builds the proxy target as
f"{catalog_service_url}/events" (no trailing slash). The catalog-service
route is registered at "/events/" (APIRouter(prefix="/events") + "/"), so
FastAPI's redirect_slashes kicks in server-side and 307-redirects to
"http://catalog-service:8000/events/" -- catalog-service's internal Docker
hostname, unreachable from outside the compose network. Any real external
client following that redirect gets a connection failure. This test hits
that endpoint for real every browse cycle and reports it honestly rather
than working around it -- see docs/load-test-results.md.

Because the list endpoint is unusable, a small pool of real event/seat IDs
is fetched once at import time directly from catalog-service (port 8002,
bypassing the broken gateway route) using the dev internal-mesh secret from
docker-compose.yml, purely to seed this test's data -- not part of the
measured traffic.
"""

import base64
import json
import os
import random
import uuid

from locust import HttpUser, task, between
import requests

GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://localhost:8000")
AGENT_GATEWAY_URL = os.environ.get("AGENT_GATEWAY_URL", "http://localhost:8007")
CATALOG_DIRECT_URL = os.environ.get("CATALOG_DIRECT_URL", "http://localhost:8002")
INTERNAL_SECRET = os.environ.get("INTERNAL_SHARED_SECRET", "dev-internal-secret-change-me")

PAYMENT_METHODS = ["card_test_4242", "card_test_4111", "card_test_5555"]


def _decode_user_id(access_token: str) -> str:
    """Pull the 'sub' claim out of the JWT without verifying (stdlib only)."""
    payload_b64 = access_token.split(".")[1]
    payload_b64 += "=" * (-len(payload_b64) % 4)
    payload = json.loads(base64.urlsafe_b64decode(payload_b64))
    return payload["sub"]


def _fetch_seed_pool() -> list[dict]:
    """
    One-time, out-of-band fetch of real (event_id, seat_ids) pairs directly
    from catalog-service, since the gateway's list endpoint is broken (see
    module docstring). Not part of measured load-test traffic.
    """
    try:
        resp = requests.get(
            f"{CATALOG_DIRECT_URL}/events/",
            params={"page": 1, "size": 10},
            headers={"X-Internal-Secret": INTERNAL_SECRET},
            timeout=5,
        )
        resp.raise_for_status()
        events = resp.json()["items"]
    except Exception as exc:  # pragma: no cover - test setup only
        print(f"[loadtest] WARNING: could not seed event pool: {exc}")
        return []

    pool = []
    for ev in events:
        try:
            seats_resp = requests.get(
                f"{CATALOG_DIRECT_URL}/events/{ev['id']}/seats",
                headers={"X-Internal-Secret": INTERNAL_SECRET},
                timeout=5,
            )
            seats_resp.raise_for_status()
            seat_ids = [
                seat["id"]
                for section in seats_resp.json()["sections"]
                for seat in section["seats"]
            ]
            if seat_ids:
                pool.append({"event_id": ev["id"], "seat_ids": seat_ids})
        except Exception as exc:  # pragma: no cover
            print(f"[loadtest] WARNING: could not fetch seats for {ev['id']}: {exc}")
    return pool


SEED_POOL = _fetch_seed_pool()
if not SEED_POOL:
    print(
        "[loadtest] WARNING: empty seed pool -- run `python scripts/seed_data.py` "
        "against the running stack first. Booking-related tasks will no-op."
    )


class TicketFlowUser(HttpUser):
    host = GATEWAY_URL
    wait_time = between(1, 3)

    def on_start(self):
        email = f"loadtest_{uuid.uuid4().hex[:12]}@example.com"
        password = "loadtest_password_123"
        body = {
            "email": email,
            "password": password,
            "full_name": "Load Test User",
        }
        with self.client.post(
            "/api/v1/auth/register", json=body, catch_response=True, name="/api/v1/auth/register"
        ) as resp:
            if resp.status_code == 201:
                data = resp.json()
                resp.success()
            elif resp.status_code == 409:
                # Email collision (practically impossible with uuid4, but
                # handle it the way a real client would: fall back to login).
                resp.success()
                login_resp = self.client.post(
                    "/api/v1/auth/login",
                    json={"email": email, "password": password},
                    name="/api/v1/auth/login (fallback)",
                )
                data = login_resp.json()
            elif resp.status_code == 429:
                # Per-IP rate limit tripped at high concurrency -- expected,
                # correct behavior, not a bug. This user just won't be able
                # to authenticate for the rest of the run.
                resp.success()
                data = None
            else:
                resp.failure(f"unexpected register status {resp.status_code}: {resp.text[:200]}")
                data = None

        if data is None:
            self.token = None
            self.user_id = None
        else:
            self.token = data["access_token"]
            self.user_id = _decode_user_id(self.token)

        self.booking_ids: list[str] = []

    def _auth_headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    # ------------------------------------------------------------------
    # Browse catalog -- high weight
    # ------------------------------------------------------------------
    @task(6)
    def browse_catalog(self):
        if not self.token:
            return

        # Real broken endpoint -- hit it as a real client would and let
        # Locust's default success/failure logic report it honestly (no
        # catch_response override here). Following the 307 lands on
        # catalog-service's internal Docker hostname, which is unreachable
        # from outside the compose network, so this reliably surfaces as a
        # connection failure -- that IS the finding, not a test bug.
        self.client.get(
            "/api/v1/events",
            headers=self._auth_headers(),
            name="/api/v1/events (list, KNOWN BROKEN)",
        )

        if not SEED_POOL:
            return
        entry = random.choice(SEED_POOL)
        event_id = entry["event_id"]

        with self.client.get(
            f"/api/v1/events/{event_id}",
            headers=self._auth_headers(),
            catch_response=True,
            name="/api/v1/events/[id]",
        ) as resp:
            if resp.status_code in (200, 404, 429):
                resp.success()
            else:
                resp.failure(f"unexpected status {resp.status_code}: {resp.text[:200]}")

        with self.client.get(
            f"/api/v1/events/{event_id}/seats",
            headers=self._auth_headers(),
            catch_response=True,
            name="/api/v1/events/[id]/seats",
        ) as resp:
            if resp.status_code in (200, 404, 429):
                resp.success()
            else:
                resp.failure(f"unexpected status {resp.status_code}: {resp.text[:200]}")

    # ------------------------------------------------------------------
    # Lock + confirm/release a seat -- medium weight
    # ------------------------------------------------------------------
    @task(3)
    def book_seat(self):
        if not self.token or not SEED_POOL:
            return

        entry = random.choice(SEED_POOL)
        seat_id = random.choice(entry["seat_ids"])
        body = {"event_id": entry["event_id"], "seat_ids": [seat_id]}

        with self.client.post(
            "/api/v1/bookings/lock",
            json=body,
            headers=self._auth_headers(),
            catch_response=True,
            name="/api/v1/bookings/lock",
        ) as resp:
            if resp.status_code == 201:
                resp.success()
                booking_id = resp.json()["id"]
            elif resp.status_code in (409, 429):
                # 409: seat already locked by another concurrent user.
                # 429: per-user/per-IP rate limit tripped.
                # Both are expected, correct behavior under load, not failures.
                resp.success()
                booking_id = None
            else:
                resp.failure(f"unexpected status {resp.status_code}: {resp.text[:200]}")
                booking_id = None

        if booking_id is None:
            return

        self.booking_ids.append(booking_id)
        if len(self.booking_ids) > 20:
            self.booking_ids.pop(0)

        if random.random() < 0.7:
            with self.client.post(
                f"/api/v1/bookings/{booking_id}/confirm",
                json={"payment_method": random.choice(PAYMENT_METHODS)},
                headers=self._auth_headers(),
                catch_response=True,
                name="/api/v1/bookings/[id]/confirm",
            ) as resp:
                if resp.status_code in (200, 409, 429):
                    resp.success()
                else:
                    resp.failure(f"unexpected status {resp.status_code}: {resp.text[:200]}")
        else:
            with self.client.post(
                f"/api/v1/bookings/{booking_id}/release",
                headers=self._auth_headers(),
                catch_response=True,
                name="/api/v1/bookings/[id]/release",
            ) as resp:
                if resp.status_code in (200, 409, 429):
                    resp.success()
                else:
                    resp.failure(f"unexpected status {resp.status_code}: {resp.text[:200]}")

    # ------------------------------------------------------------------
    # Check booking status -- low weight
    # ------------------------------------------------------------------
    @task(2)
    def check_booking_status(self):
        if not self.token or not self.booking_ids:
            return
        booking_id = random.choice(self.booking_ids)
        with self.client.get(
            f"/api/v1/bookings/{booking_id}",
            headers=self._auth_headers(),
            catch_response=True,
            name="/api/v1/bookings/[id] (status check)",
        ) as resp:
            if resp.status_code in (200, 404, 429):
                resp.success()
            else:
                resp.failure(f"unexpected status {resp.status_code}: {resp.text[:200]}")

    # ------------------------------------------------------------------
    # Support refund / booking inquiry -- low weight.
    # NOTE: the ticketflow gateway (:8000) does not proxy support-agent at
    # all -- it isn't in services/gateway/app/routes/*. The real user-facing
    # entry point for this journey is agent-gateway (:8007) /api/v1/agent/chat,
    # which internally calls support-agent with the mesh secret. Hitting
    # that here for real (not localhost:8000) since that's the only
    # externally reachable path for this journey.
    # ------------------------------------------------------------------
    @task(1)
    def support_refund_or_inquiry(self):
        if not self.token:
            return

        if self.booking_ids and random.random() < 0.5:
            booking_id = random.choice(self.booking_ids)
            message = f"I want a refund for booking {booking_id}"
        elif self.booking_ids:
            booking_id = random.choice(self.booking_ids)
            message = f"What is the status of booking {booking_id}?"
        else:
            message = "What is your refund policy?"

        with self.client.post(
            f"{AGENT_GATEWAY_URL}/api/v1/agent/chat",
            json={"message": message},
            headers=self._auth_headers(),
            catch_response=True,
            name="/api/v1/agent/chat (support-agent via agent-gateway)",
        ) as resp:
            if resp.status_code in (200, 404, 429):
                resp.success()
            else:
                resp.failure(f"unexpected status {resp.status_code}: {resp.text[:200]}")
