import asyncio
import hashlib
import hmac
import json
import random
import uuid
from dataclasses import dataclass, field
from enum import Enum

import structlog

log = structlog.get_logger()


class PaymentOutcome(str, Enum):
    SUCCESS = "SUCCEEDED"
    DECLINED = "FAILED"
    TIMEOUT_SUCCESS = "TIMEOUT_SUCCESS"  # network timeout then eventual success


@dataclass
class PaymentResult:
    outcome: PaymentOutcome
    provider_payment_id: str
    error_message: str | None = field(default=None)


class MockPaymentProvider:
    """
    Simulates a real payment gateway with three possible outcomes:
    * SUCCESS       – payment accepted immediately (default 80 %)
    * DECLINED      – card declined by issuer          (default 10 %)
    * TIMEOUT_SUCCESS – delayed acceptance             (remaining 10 %)
    """

    def __init__(
        self,
        success_rate: float = 0.80,
        decline_rate: float = 0.10,
        timeout_delay: float = 3.0,
    ) -> None:
        self.success_rate = success_rate
        self.decline_rate = decline_rate
        self.timeout_delay = timeout_delay

    async def process_payment(
        self, amount: float, payment_method: str
    ) -> PaymentResult:
        """Simulate payment processing with probabilistic outcomes."""
        r = random.random()
        provider_id = f"mock_pay_{uuid.uuid4().hex[:16]}"

        if r < self.success_rate:
            log.info(
                "mock_payment_success",
                provider_id=provider_id,
                amount=amount,
                payment_method=payment_method,
            )
            return PaymentResult(
                outcome=PaymentOutcome.SUCCESS,
                provider_payment_id=provider_id,
            )

        elif r < self.success_rate + self.decline_rate:
            log.warning(
                "mock_payment_declined",
                provider_id=provider_id,
                amount=amount,
                payment_method=payment_method,
            )
            return PaymentResult(
                outcome=PaymentOutcome.DECLINED,
                provider_payment_id=provider_id,
                error_message="Card declined by issuer",
            )

        else:
            # Simulate a network timeout that eventually resolves successfully
            log.info(
                "mock_payment_timeout",
                provider_id=provider_id,
                amount=amount,
                payment_method=payment_method,
                delay=self.timeout_delay,
            )
            await asyncio.sleep(self.timeout_delay)
            log.info(
                "mock_payment_timeout_resolved",
                provider_id=provider_id,
                outcome=PaymentOutcome.TIMEOUT_SUCCESS,
            )
            return PaymentResult(
                outcome=PaymentOutcome.TIMEOUT_SUCCESS,
                provider_payment_id=provider_id,
            )

    # ------------------------------------------------------------------
    # Webhook helpers
    # ------------------------------------------------------------------

    def generate_webhook_signature(self, payload: dict, secret: str) -> str:
        """Return HMAC-SHA256 hex digest of the canonicalised payload."""
        body = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hmac.new(
            secret.encode("utf-8"),
            body.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def verify_webhook_signature(
        self, payload: dict, signature: str, secret: str
    ) -> bool:
        """Constant-time comparison to prevent timing attacks."""
        expected = self.generate_webhook_signature(payload, secret)
        return hmac.compare_digest(expected, signature)
