"""
Comprehensive tests for the Payment Service.

All tests are async and use the in-memory SQLite DB defined in conftest.py.
The MockPaymentProvider is patched where needed to produce deterministic
outcomes instead of random ones.
"""
import asyncio
import os
import tempfile
import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database import Base, get_db
from libs.security import internal_headers
from app.main import app
from app.mock_provider import MockPaymentProvider, PaymentOutcome, PaymentResult
from tests.conftest import unique_idempotency_key

pytestmark = pytest.mark.asyncio


# ===========================================================================
# Helpers
# ===========================================================================


def _payment_intent_body(**overrides) -> dict:
    defaults = {
        "booking_id": str(uuid.uuid4()),
        "amount": "99.99",
        "currency": "USD",
        "idempotency_key": unique_idempotency_key(),
    }
    defaults.update(overrides)
    return defaults


# ===========================================================================
# Health check
# ===========================================================================


async def test_health(client):
    resp = await client.get("/payments/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"


# ===========================================================================
# Create payment intent
# ===========================================================================


async def test_create_payment_intent_success(client):
    body = _payment_intent_body()
    resp = await client.post("/payments/create-intent", json=body)
    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "PENDING"
    assert data["amount"] == "99.99"
    assert data["currency"] == "USD"
    assert data["booking_id"] == body["booking_id"]
    assert data["idempotency_key"] == body["idempotency_key"]
    assert "id" in data


async def test_create_payment_intent_idempotency(client):
    """Same idempotency_key must return the same payment record."""
    body = _payment_intent_body()

    resp1 = await client.post("/payments/create-intent", json=body)
    assert resp1.status_code == 201

    resp2 = await client.post("/payments/create-intent", json=body)
    # Second call still returns 201 with the identical record
    assert resp2.status_code == 201

    assert resp1.json()["id"] == resp2.json()["id"]


async def test_create_payment_intent_different_keys_different_records(client):
    """Two different idempotency keys create two distinct records."""
    booking_id = str(uuid.uuid4())
    body1 = _payment_intent_body(booking_id=booking_id, idempotency_key=unique_idempotency_key())
    body2 = _payment_intent_body(booking_id=booking_id, idempotency_key=unique_idempotency_key())

    resp1 = await client.post("/payments/create-intent", json=body1)
    resp2 = await client.post("/payments/create-intent", json=body2)

    assert resp1.status_code == 201
    assert resp2.status_code == 201
    assert resp1.json()["id"] != resp2.json()["id"]


# ===========================================================================
# Confirm payment
# ===========================================================================


async def test_confirm_payment_success(client):
    """Provider returns SUCCESS → payment transitions to SUCCEEDED."""
    body = _payment_intent_body()
    create_resp = await client.post("/payments/create-intent", json=body)
    payment_id = create_resp.json()["id"]

    mock_result = PaymentResult(
        outcome=PaymentOutcome.SUCCESS,
        provider_payment_id="mock_pay_abc123",
    )

    with patch(
        "app.routes.payments._provider.process_payment",
        new=AsyncMock(return_value=mock_result),
    ):
        resp = await client.post(
            f"/payments/{payment_id}/confirm",
            json={"payment_method_details": "tok_visa_success"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "SUCCEEDED"
    assert data["provider_payment_id"] == "mock_pay_abc123"
    assert data["error_message"] is None


async def test_confirm_payment_declined(client):
    """Provider returns DECLINED → payment transitions to FAILED."""
    body = _payment_intent_body()
    create_resp = await client.post("/payments/create-intent", json=body)
    payment_id = create_resp.json()["id"]

    mock_result = PaymentResult(
        outcome=PaymentOutcome.DECLINED,
        provider_payment_id="mock_pay_declined_xyz",
        error_message="Card declined by issuer",
    )

    with patch(
        "app.routes.payments._provider.process_payment",
        new=AsyncMock(return_value=mock_result),
    ):
        resp = await client.post(
            f"/payments/{payment_id}/confirm",
            json={"payment_method_details": "tok_visa_decline"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "FAILED"
    assert data["error_message"] == "Card declined by issuer"


async def test_confirm_payment_timeout_success(client):
    """
    Provider returns TIMEOUT_SUCCESS (simulated with instant mock).
    The payment must end up as SUCCEEDED regardless.
    """
    body = _payment_intent_body()
    create_resp = await client.post("/payments/create-intent", json=body)
    payment_id = create_resp.json()["id"]

    # We patch the sleep inside the provider so the test doesn't take 3 s
    mock_result = PaymentResult(
        outcome=PaymentOutcome.TIMEOUT_SUCCESS,
        provider_payment_id="mock_pay_timeout_ok",
    )

    with patch(
        "app.routes.payments._provider.process_payment",
        new=AsyncMock(return_value=mock_result),
    ):
        resp = await client.post(
            f"/payments/{payment_id}/confirm",
            json={"payment_method_details": "tok_timeout"},
        )

    assert resp.status_code == 200
    data = resp.json()
    # TIMEOUT_SUCCESS should resolve to SUCCEEDED
    assert data["status"] == "SUCCEEDED"
    assert data["provider_payment_id"] == "mock_pay_timeout_ok"


async def test_confirm_payment_not_found(client):
    fake_id = str(uuid.uuid4())
    resp = await client.post(
        f"/payments/{fake_id}/confirm",
        json={"payment_method_details": "tok_anything"},
    )
    assert resp.status_code == 404


async def test_confirm_payment_already_confirmed(client):
    """Confirming an already SUCCEEDED payment must return 409."""
    body = _payment_intent_body()
    create_resp = await client.post("/payments/create-intent", json=body)
    payment_id = create_resp.json()["id"]

    mock_result = PaymentResult(
        outcome=PaymentOutcome.SUCCESS,
        provider_payment_id="mock_pay_already",
    )

    with patch(
        "app.routes.payments._provider.process_payment",
        new=AsyncMock(return_value=mock_result),
    ):
        await client.post(
            f"/payments/{payment_id}/confirm",
            json={"payment_method_details": "tok_visa"},
        )

    # Second confirm on the same payment
    resp2 = await client.post(
        f"/payments/{payment_id}/confirm",
        json={"payment_method_details": "tok_visa"},
    )
    assert resp2.status_code == 409


# ===========================================================================
# Webhook
# ===========================================================================


def _make_webhook_payload(payment_id: str, status: str = "SUCCEEDED") -> dict:
    from decimal import Decimal
    return {
        "payment_id": payment_id,
        "event": "payment.updated",
        "amount": "99.99",
        "status": status,
        "timestamp": "2026-01-01T00:00:00Z",
    }


def _sign(payload: dict) -> str:
    provider = MockPaymentProvider()
    from app.config import get_settings

    secret = get_settings().webhook_secret
    return provider.generate_webhook_signature(payload, secret)


async def test_webhook_valid_signature(client):
    """Valid HMAC signature → webhook accepted and payment status updated."""
    body = _payment_intent_body()
    create_resp = await client.post("/payments/create-intent", json=body)
    payment_id = create_resp.json()["id"]

    payload = _make_webhook_payload(payment_id, status="SUCCEEDED")
    sig = _sign(payload)

    resp = await client.post(
        "/payments/webhook",
        json=payload,
        headers={"X-Webhook-Signature": sig},
    )
    assert resp.status_code == 200
    assert resp.json()["received"] is True


async def test_webhook_invalid_signature(client):
    """Tampered / wrong signature must return 401."""
    payload = _make_webhook_payload(str(uuid.uuid4()))

    resp = await client.post(
        "/payments/webhook",
        json=payload,
        headers={"X-Webhook-Signature": "bad_signature_value"},
    )
    assert resp.status_code == 401


async def test_webhook_missing_signature(client):
    """No signature header at all must return 401."""
    payload = _make_webhook_payload(str(uuid.uuid4()))
    resp = await client.post("/payments/webhook", json=payload)
    assert resp.status_code == 401


async def test_webhook_updates_payment_status(client):
    """Webhook should update payment status in the database."""
    body = _payment_intent_body()
    create_resp = await client.post("/payments/create-intent", json=body)
    payment_id = create_resp.json()["id"]

    payload = _make_webhook_payload(payment_id, status="REFUNDED")
    sig = _sign(payload)

    await client.post(
        "/payments/webhook",
        json=payload,
        headers={"X-Webhook-Signature": sig},
    )

    # Fetch the payment to confirm status was updated
    get_resp = await client.get(f"/payments/{payment_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["status"] == "REFUNDED"


# ===========================================================================
# Refund
# ===========================================================================


async def _create_succeeded_payment(client) -> str:
    """Helper: create and confirm a payment, return its UUID string."""
    body = _payment_intent_body()
    create_resp = await client.post("/payments/create-intent", json=body)
    payment_id = create_resp.json()["id"]

    mock_result = PaymentResult(
        outcome=PaymentOutcome.SUCCESS,
        provider_payment_id="mock_pay_for_refund",
    )
    with patch(
        "app.routes.payments._provider.process_payment",
        new=AsyncMock(return_value=mock_result),
    ):
        await client.post(
            f"/payments/{payment_id}/confirm",
            json={"payment_method_details": "tok_visa"},
        )
    return payment_id


async def test_refund_success(client):
    """Full refund on a SUCCEEDED payment creates a Refund and marks payment REFUNDED."""
    payment_id = await _create_succeeded_payment(client)

    resp = await client.post(
        f"/payments/{payment_id}/refund",
        json={"amount": "99.99", "reason": "Customer request"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "SUCCEEDED"
    assert data["payment_id"] == payment_id
    assert data["amount"] == "99.99"
    assert data["reason"] == "Customer request"

    # Payment must now be REFUNDED
    get_resp = await client.get(f"/payments/{payment_id}")
    assert get_resp.json()["status"] == "REFUNDED"


async def test_refund_partial(client):
    """Partial refund (less than original amount) must also succeed."""
    payment_id = await _create_succeeded_payment(client)

    resp = await client.post(
        f"/payments/{payment_id}/refund",
        json={"amount": "10.00", "reason": "Partial"},
    )
    assert resp.status_code == 201
    assert resp.json()["amount"] == "10.00"


async def test_refund_exceeds_amount(client):
    """Refund exceeding the original amount must return 422."""
    payment_id = await _create_succeeded_payment(client)

    resp = await client.post(
        f"/payments/{payment_id}/refund",
        json={"amount": "999.00", "reason": "Too much"},
    )
    assert resp.status_code == 422


async def test_refund_not_found_payment(client):
    """Refund on non-existent payment returns 404."""
    fake_id = str(uuid.uuid4())
    resp = await client.post(
        f"/payments/{fake_id}/refund",
        json={"amount": "10.00", "reason": "test"},
    )
    assert resp.status_code == 404


async def test_refund_pending_payment(client):
    """Refunding a PENDING (not yet confirmed) payment returns 409."""
    body = _payment_intent_body()
    create_resp = await client.post("/payments/create-intent", json=body)
    payment_id = create_resp.json()["id"]

    resp = await client.post(
        f"/payments/{payment_id}/refund",
        json={"amount": "99.99", "reason": "early refund"},
    )
    assert resp.status_code == 409


# ===========================================================================
# Get payment
# ===========================================================================


async def test_get_payment(client):
    body = _payment_intent_body()
    create_resp = await client.post("/payments/create-intent", json=body)
    payment_id = create_resp.json()["id"]

    resp = await client.get(f"/payments/{payment_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == payment_id
    assert data["status"] == "PENDING"


async def test_get_payment_not_found(client):
    resp = await client.get(f"/payments/{uuid.uuid4()}")
    assert resp.status_code == 404


# ===========================================================================
# Race condition / concurrent idempotency
# ===========================================================================


@pytest_asyncio.fixture()
async def race_client():
    """
    A client backed by a real file-based SQLite DB (genuinely independent
    connections), not the shared in-memory/StaticPool engine used elsewhere.
    Needed so concurrent requests actually interleave at the DB level instead
    of fighting over one shared physical connection.
    """
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async def _override_get_db():
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test", headers=internal_headers()
    ) as ac:
        yield ac
    app.dependency_overrides.clear()
    await engine.dispose()
    os.remove(path)


async def test_race_condition_idempotency(race_client):
    """
    Ten concurrent requests with the same idempotency_key should only ever
    produce ONE payment record.  The first writer wins; subsequent requests
    hit the UNIQUE constraint, and create_payment_intent's IntegrityError
    handler re-selects and returns the existing record.
    """
    idempotency_key = unique_idempotency_key()
    booking_id = str(uuid.uuid4())
    body = _payment_intent_body(
        booking_id=booking_id,
        idempotency_key=idempotency_key,
    )

    async def _create():
        return await race_client.post("/payments/create-intent", json=body)

    responses = await asyncio.gather(*[_create() for _ in range(10)])

    # All must succeed
    for r in responses:
        assert r.status_code == 201

    # All must return the same payment ID
    ids = {r.json()["id"] for r in responses}
    assert len(ids) == 1, f"Expected 1 unique payment, got {len(ids)}: {ids}"


# ===========================================================================
# Mock provider unit tests (direct, no HTTP)
# ===========================================================================


async def test_mock_provider_always_success():
    """Deterministic success when success_rate=1.0."""
    provider = MockPaymentProvider(success_rate=1.0, decline_rate=0.0, timeout_delay=0)
    result = await provider.process_payment(amount=10.0, payment_method="tok")
    assert result.outcome == PaymentOutcome.SUCCESS
    assert result.provider_payment_id.startswith("mock_pay_")


async def test_mock_provider_always_declined():
    """Deterministic decline when success_rate=0.0, decline_rate=1.0."""
    provider = MockPaymentProvider(success_rate=0.0, decline_rate=1.0, timeout_delay=0)
    result = await provider.process_payment(amount=10.0, payment_method="tok")
    assert result.outcome == PaymentOutcome.DECLINED
    assert result.error_message is not None


async def test_mock_provider_webhook_signature_round_trip():
    """generate + verify must return True for the same payload/secret."""
    provider = MockPaymentProvider()
    payload = {"payment_id": "abc", "status": "SUCCEEDED", "amount": "10.00"}
    secret = "test-secret"
    sig = provider.generate_webhook_signature(payload, secret)
    assert provider.verify_webhook_signature(payload, sig, secret) is True


async def test_mock_provider_webhook_signature_tampered():
    """Tampered payload must fail verification."""
    provider = MockPaymentProvider()
    payload = {"payment_id": "abc", "status": "SUCCEEDED"}
    secret = "test-secret"
    sig = provider.generate_webhook_signature(payload, secret)

    tampered = {"payment_id": "abc", "status": "REFUNDED"}
    assert provider.verify_webhook_signature(tampered, sig, secret) is False
