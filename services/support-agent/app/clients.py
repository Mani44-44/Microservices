"""
clients.py
~~~~~~~~~~
Thin httpx wrappers around the other TicketFlow services. Each raises on
failure (network error, non-2xx) — callers in tools.py decide how to turn
that into a graceful user-facing response.
"""
import httpx

from app.config import get_settings
from libs.security import internal_headers

settings = get_settings()


def _auth_headers(user_bearer_token: str) -> dict:
    return {"Authorization": f"Bearer {user_bearer_token}"}


async def get_booking(booking_id: str, user_bearer_token: str) -> dict:
    async with httpx.AsyncClient(timeout=settings.http_timeout_seconds, headers=internal_headers()) as client:
        resp = await client.get(
            f"{settings.booking_service_url}/bookings/{booking_id}",
            headers=_auth_headers(user_bearer_token),
        )
        resp.raise_for_status()
        return resp.json()


async def release_booking(booking_id: str, user_bearer_token: str) -> dict:
    async with httpx.AsyncClient(timeout=settings.http_timeout_seconds, headers=internal_headers()) as client:
        resp = await client.post(
            f"{settings.booking_service_url}/bookings/{booking_id}/release",
            headers=_auth_headers(user_bearer_token),
        )
        resp.raise_for_status()
        return resp.json()


async def get_event(event_id: str) -> dict:
    async with httpx.AsyncClient(timeout=settings.http_timeout_seconds, headers=internal_headers()) as client:
        resp = await client.get(f"{settings.catalog_service_url}/events/{event_id}")
        resp.raise_for_status()
        return resp.json()


async def get_payment_by_booking(booking_id: str) -> dict:
    async with httpx.AsyncClient(timeout=settings.http_timeout_seconds, headers=internal_headers()) as client:
        resp = await client.get(
            f"{settings.payment_service_url}/internal/payments/by-booking/{booking_id}"
        )
        resp.raise_for_status()
        return resp.json()


async def refund_payment(payment_id: str, amount: float, reason: str) -> dict:
    async with httpx.AsyncClient(timeout=settings.http_timeout_seconds, headers=internal_headers()) as client:
        resp = await client.post(
            f"{settings.payment_service_url}/payments/{payment_id}/refund",
            json={"amount": amount, "reason": reason},
        )
        resp.raise_for_status()
        return resp.json()


async def search_policy_docs(query: str, category: str = "support", top_k: int = 5) -> dict:
    async with httpx.AsyncClient(timeout=settings.http_timeout_seconds, headers=internal_headers()) as client:
        resp = await client.post(
            f"{settings.rag_service_url}/search",
            json={"query": query, "category": category, "top_k": top_k},
        )
        resp.raise_for_status()
        return resp.json()
