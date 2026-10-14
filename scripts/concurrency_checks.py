"""
Concurrency checks against a running stack (real PostgreSQL + Redis).

The unit tests run on SQLite, where SELECT ... FOR UPDATE is a no-op, so the
row locks that stop double charges can only be verified here:

    python scripts/concurrency_checks.py
    python scripts/concurrency_checks.py --payment-url http://localhost:9004   # another payment-service

Each check fires N truly concurrent requests and asserts exactly one wins.
Exit code is the number of failed checks.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from collections import Counter

import httpx

GATEWAY = os.environ.get("GATEWAY_URL", "http://localhost:8000")
INTERNAL = {"X-Internal-Secret": os.environ.get("INTERNAL_SHARED_SECRET", "dev-internal-secret-change-me")}

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))
    if not ok:
        failures.append(name)


async def register(c: httpx.AsyncClient) -> dict:
    r = await c.post(
        f"{GATEWAY}/api/v1/auth/register",
        json={"email": f"conc-{uuid.uuid4().hex[:10]}@example.com", "password": "correct-horse-battery", "full_name": "C"},
    )
    r.raise_for_status()
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def available_seat(c: httpx.AsyncClient, headers: dict) -> tuple[str, str]:
    events = (await c.get(f"{GATEWAY}/api/v1/events/", params={"size": 50}, headers=headers)).json()["items"]
    for ev in events:
        seats = (await c.get(f"{GATEWAY}/api/v1/events/{ev['id']}/seats", headers=headers)).json()
        for sec in seats["sections"]:
            for s in sec["seats"]:
                if s["status"] == "available":
                    return ev["id"], s["id"]
    raise RuntimeError("no available seat; re-seed")


async def seat_race(c: httpx.AsyncClient, n: int) -> None:
    users = await asyncio.gather(*(register(c) for _ in range(n)))
    event_id, seat_id = await available_seat(c, users[0])
    resps = await asyncio.gather(
        *(c.post(f"{GATEWAY}/api/v1/bookings/lock", json={"event_id": event_id, "seat_ids": [seat_id]}, headers=h) for h in users)
    )
    codes = Counter(r.status_code for r in resps)
    check(f"{n} users lock the same seat at once -> exactly one 201", codes[201] == 1 and codes[409] == n - 1, str(dict(codes)))
    winner = next(r for r in resps if r.status_code == 201)
    await c.post(f"{GATEWAY}/api/v1/bookings/{winner.json()['id']}/release", headers=users[resps.index(winner)])


async def double_confirm(c: httpx.AsyncClient, n: int, payment_url: str) -> None:
    for _attempt in range(5):  # the mock provider declines ~10%; retry on a fresh seat
        headers = await register(c)
        event_id, seat_id = await available_seat(c, headers)
        lock = await c.post(f"{GATEWAY}/api/v1/bookings/lock", json={"event_id": event_id, "seat_ids": [seat_id]}, headers=headers)
        booking_id = lock.json()["id"]
        resps = await asyncio.gather(
            *(c.post(f"{GATEWAY}/api/v1/bookings/{booking_id}/confirm", json={"payment_method": "tok_visa"}, headers=headers) for _ in range(n))
        )
        statuses = Counter(r.json().get("status") or r.json().get("error", {}).get("code") for r in resps)
        final = (await c.get(f"{GATEWAY}/api/v1/bookings/{booking_id}", headers=headers)).json()["status"]
        if final == "CONFIRMED":
            break
    payment = (await c.get(f"{payment_url}/internal/payments/by-booking/{booking_id}", headers=INTERNAL)).json()
    check(
        f"{n} parallel confirms of one booking -> CONFIRMED once, one SUCCEEDED payment",
        final == "CONFIRMED" and payment.get("status") == "SUCCEEDED" and all(r.status_code in (200, 409) for r in resps),
        f"responses={dict(statuses)} payment={payment.get('status')}",
    )


async def payment_double_charge(c: httpx.AsyncClient, n: int, payment_url: str) -> None:
    intent = await c.post(
        f"{payment_url}/payments/create-intent",
        json={"booking_id": str(uuid.uuid4()), "amount": "10.00", "idempotency_key": str(uuid.uuid4())},
        headers=INTERNAL,
    )
    pid = intent.json()["id"]
    resps = await asyncio.gather(
        *(c.post(f"{payment_url}/payments/{pid}/confirm", json={"payment_method_details": "tok_visa"}, headers=INTERNAL) for _ in range(n))
    )
    codes = Counter(r.status_code for r in resps)
    check(
        f"{n} parallel confirms of one payment intent -> provider charged exactly once",
        codes[200] == 1 and codes[409] == n - 1,
        f"{dict(codes)} (each 200 = one charge)",
    )


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("-n", type=int, default=10, help="concurrent requests per check")
    parser.add_argument("--payment-url", default=os.environ.get("PAYMENT_SERVICE_URL", "http://localhost:8004"))
    parser.add_argument("--only", choices=["seat", "booking", "payment"])
    args = parser.parse_args()

    limits = httpx.Limits(max_connections=200, max_keepalive_connections=0)
    async with httpx.AsyncClient(timeout=60.0, limits=limits) as c:
        if args.only in (None, "seat"):
            await seat_race(c, max(args.n, 20))
        if args.only in (None, "booking"):
            await double_confirm(c, args.n, args.payment_url)
        if args.only in (None, "payment"):
            await payment_double_charge(c, args.n, args.payment_url)

    print(f"\n{'ALL CHECKS PASSED' if not failures else f'{len(failures)} FAILED'}")
    return len(failures)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
