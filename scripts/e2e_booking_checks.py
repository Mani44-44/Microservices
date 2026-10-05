"""
End-to-end booking correctness checks against a running stack.

Drives the real public edges (API Gateway :8000, Agent Gateway :8007) and,
for verification only, a couple of mesh-internal endpoints using the dev
internal secret. Run after `docker compose up -d` and `scripts/seed_data.py`:

    python scripts/e2e_booking_checks.py            # fast checks (~30s)
    python scripts/e2e_booking_checks.py --expiry   # + seat-hold expiry (~6 min)

Each check prints PASS/FAIL; exit code is the number of failures.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import uuid

import httpx

GATEWAY = os.environ.get("GATEWAY_URL", "http://localhost:8000")
AGENT_GATEWAY = os.environ.get("AGENT_GATEWAY_URL", "http://localhost:8007")
PAYMENT = os.environ.get("PAYMENT_SERVICE_URL", "http://localhost:8004")
ANALYTICS = os.environ.get("ANALYTICS_SERVICE_URL", "http://localhost:8006")
INTERNAL = {"X-Internal-Secret": os.environ.get("INTERNAL_SHARED_SECRET", "dev-internal-secret-change-me")}
SEAT_LOCK_TTL = int(os.environ.get("SEAT_LOCK_TTL_SECONDS", "300"))

http = httpx.Client(timeout=60.0)
failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(name)
    return ok


def register() -> dict:
    email = f"e2e-{uuid.uuid4().hex[:10]}@example.com"
    resp = http.post(
        f"{GATEWAY}/api/v1/auth/register",
        json={"email": email, "password": "correct-horse-battery", "full_name": "E2E User"},
    )
    resp.raise_for_status()
    token = resp.json()["access_token"]
    return {"email": email, "headers": {"Authorization": f"Bearer {token}"}}


def lock(user: dict, event_id: str, seat_ids: list[str]) -> httpx.Response:
    return http.post(
        f"{GATEWAY}/api/v1/bookings/lock",
        json={"event_id": event_id, "seat_ids": seat_ids},
        headers=user["headers"],
    )


def confirm(user: dict, booking_id: str) -> httpx.Response:
    return http.post(
        f"{GATEWAY}/api/v1/bookings/{booking_id}/confirm",
        json={"payment_method": "tok_visa"},
        headers=user["headers"],
    )


def get_booking(user: dict, booking_id: str) -> dict:
    return http.get(f"{GATEWAY}/api/v1/bookings/{booking_id}", headers=user["headers"]).json()


def seat_map(user: dict, event_id: str) -> dict[str, dict]:
    resp = http.get(f"{GATEWAY}/api/v1/events/{event_id}/seats", headers=user["headers"])
    resp.raise_for_status()
    return {s["id"]: s for sec in resp.json()["sections"] for s in sec["seats"]}


def error_code(resp: httpx.Response) -> str | None:
    try:
        return resp.json()["error"]["code"]
    except Exception:
        return None


def pick_events(user: dict) -> dict[str, dict]:
    resp = http.get(f"{GATEWAY}/api/v1/events/", params={"size": 50}, headers=user["headers"])
    resp.raise_for_status()
    return {e["title"]: e for e in resp.json()["items"]}


def buy_some_seat(user: dict, event_id: str, available: list[str]) -> tuple[str, str]:
    """Lock+confirm seats until one payment succeeds (the mock provider
    declines ~10%). Returns (booking_id, seat_id)."""
    for seat_id in available:
        resp = lock(user, event_id, [seat_id])
        if resp.status_code != 201:
            continue
        booking_id = resp.json()["id"]
        if confirm(user, booking_id).json().get("status") == "CONFIRMED":
            return booking_id, seat_id
    raise RuntimeError("could not confirm any booking")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expiry", action="store_true", help="also wait out a seat hold (~TTL+60s)")
    args = parser.parse_args()

    alice, bob = register(), register()
    events = pick_events(alice)
    # >7 days out on the seed data -> full refund window
    event = next(e for t, e in events.items() if t.startswith("Taylor Swift"))
    other_venue_event = next(e for e in events.values() if e["venue_id"] != event["venue_id"])
    seats = seat_map(alice, event["id"])
    available = [sid for sid, s in seats.items() if s["status"] == "available"]

    # 1. A confirmed seat cannot be sold again.
    booking_id, seat_id = buy_some_seat(alice, event["id"], available)
    available = [s for s in available if s != seat_id]
    check("confirm succeeds and booking is CONFIRMED", get_booking(alice, booking_id)["status"] == "CONFIRMED")
    resp = lock(bob, event["id"], [seat_id])
    check(
        "re-locking a sold seat is rejected with SEAT_ALREADY_BOOKED",
        resp.status_code == 409 and error_code(resp) == "SEAT_ALREADY_BOOKED",
        f"{resp.status_code} {resp.text[:200]}",
    )
    check("seat map shows the sold seat as booked", seat_map(bob, event["id"])[seat_id]["status"] == "booked")

    # 2. Pricing fails closed.
    resp = lock(bob, event["id"], [str(uuid.uuid4())])
    check(
        "invented seat id is rejected with SEAT_NOT_FOUND",
        resp.status_code == 422 and error_code(resp) == "SEAT_NOT_FOUND",
        f"{resp.status_code} {resp.text[:200]}",
    )
    foreign_seat = next(iter(seat_map(bob, other_venue_event["id"])))
    resp = lock(bob, event["id"], [foreign_seat])
    check(
        "seat from another venue is rejected with SEAT_NOT_FOUND",
        resp.status_code == 422 and error_code(resp) == "SEAT_NOT_FOUND",
        f"{resp.status_code} {resp.text[:200]}",
    )
    resp = lock(bob, event["id"], [available[0]])
    priced = resp.status_code == 201 and float(resp.json()["total_amount"]) > 0
    check("a real seat is priced from the catalog (non-zero)", priced, resp.text[:200])
    if resp.status_code == 201:
        http.post(f"{GATEWAY}/api/v1/bookings/{resp.json()['id']}/release", headers=bob["headers"])

    # 3. Standard error envelope from user-service (through the gateway).
    resp = http.post(f"{GATEWAY}/api/v1/auth/login", json={"email": alice["email"], "password": "wrong-password"})
    check(
        "failed login returns {error: {code, message}} at top level",
        resp.status_code == 401 and error_code(resp) == "INVALID_CREDENTIALS",
        resp.text[:200],
    )

    # 4. Refund via the support agent cancels the booking and frees the seat.
    resp = http.post(
        f"{AGENT_GATEWAY}/api/v1/agent/chat",
        json={"message": f"I want a refund for booking {booking_id} please"},
        headers=alice["headers"],
    )
    check("agent chat routes the refund request", resp.status_code == 200 and resp.json().get("intent") == "REFUND_REQUEST", resp.text[:300])
    print(f"       agent replied: {resp.json().get('response', '')[:160]!r}")
    check("refunded booking is CANCELLED", get_booking(alice, booking_id)["status"] == "CANCELLED")
    payment = http.get(f"{PAYMENT}/internal/payments/by-booking/{booking_id}", headers=INTERNAL).json()
    check("payment is REFUNDED", payment.get("status") == "REFUNDED", str(payment))
    resp = lock(bob, event["id"], [seat_id])
    check("refunded seat can be booked again", resp.status_code == 201, f"{resp.status_code} {resp.text[:200]}")
    if resp.status_code == 201:
        http.post(f"{GATEWAY}/api/v1/bookings/{resp.json()['id']}/release", headers=bob["headers"])

    # 5. Domain events flow through the outbox -> Redpanda -> analytics.
    time.sleep(8)
    stats = http.get(f"{ANALYTICS}/stats", headers=INTERNAL)
    body = stats.json() if stats.status_code == 200 else {}
    check("analytics consumed booking events", stats.status_code == 200 and "BOOKING_CONFIRMED" in str(body), str(body)[:300])

    # 6. Abandoned holds expire, persist EXPIRED, and free the seat.
    if args.expiry:
        carol = register()
        resp = lock(carol, event["id"], [available[1]])
        held_id = resp.json()["id"]
        wait = SEAT_LOCK_TTL + 60
        print(f"       waiting {wait}s for the hold on booking {held_id} to expire ...")
        time.sleep(wait)
        check("abandoned booking is persisted as EXPIRED", get_booking(carol, held_id)["status"] == "EXPIRED")
        resp = lock(bob, event["id"], [available[1]])
        check("expired seat can be locked by someone else", resp.status_code == 201, resp.text[:200])

    print(f"\n{'ALL CHECKS PASSED' if not failures else f'{len(failures)} FAILED: ' + ', '.join(failures)}")
    return len(failures)


if __name__ == "__main__":
    sys.exit(main())
