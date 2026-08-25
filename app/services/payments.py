"""
Payment provider abstraction (spec #93). Donation business logic talks
to this interface, never to a specific provider's SDK directly, so
Stripe/Paystack/M-Pesa can be added later without touching route or
model code.

Only a TestPaymentProvider exists right now, on purpose (spec #102 —
foundation and donation records in a *controlled test state* come
before any real provider integration). It simulates an instantly
successful payment so the donation flow, idempotency, and webhook
plumbing can all be built and tested end-to-end before real money is
involved.
"""
from __future__ import annotations

import uuid
from abc import ABC, abstractmethod


class PaymentInitiationResult:
    def __init__(self, provider_transaction_id: str, status: str, provider_reference: str | None = None):
        self.provider_transaction_id = provider_transaction_id
        self.status = status
        self.provider_reference = provider_reference


class PaymentProvider(ABC):
    name: str = "unset"

    @abstractmethod
    def initiate_payment(self, *, amount, currency: str, donation_id: str) -> PaymentInitiationResult:
        """Start a payment for a donation. Returns provider-side
        identifiers and an initial status. Real providers will usually
        return 'pending' here and confirm via webhook instead."""
        raise NotImplementedError


class TestPaymentProvider(PaymentProvider):
    """
    Local/dev/test-only provider. Every payment 'succeeds' immediately
    with a synthetic transaction ID. Never select this provider in a
    production environment — it exists purely so milestone #101 item 15
    ('donation records can be created in a controlled/test state') is
    achievable before Stripe/Paystack/M-Pesa are wired in.
    """

    name = "test"

    def initiate_payment(self, *, amount, currency: str, donation_id: str) -> PaymentInitiationResult:
        return PaymentInitiationResult(
            provider_transaction_id=f"test_{uuid.uuid4().hex}",
            status="successful",
            provider_reference=f"test-ref-{donation_id}",
        )


_PROVIDERS: dict[str, PaymentProvider] = {
    "test": TestPaymentProvider(),
}


def get_payment_provider(name: str) -> PaymentProvider:
    provider = _PROVIDERS.get(name)
    if provider is None:
        raise ValueError(
            f"Unknown or not-yet-integrated payment provider '{name}'. "
            f"Available: {list(_PROVIDERS.keys())}"
        )
    return provider
