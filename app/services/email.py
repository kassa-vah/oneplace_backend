"""
Email service (Brevo, per your stack). Deliberately a thin, safe stub
right now — receipts must only ever be triggered after real payment
verification (spec #70), and tax/deductibility language must never be
invented by the backend (spec #71). Wire in the actual Brevo API call
once the org has approved real receipt copy; until then this logs what
would have been sent so the rest of the donation flow can be built and
tested without accidentally emailing anyone.
"""
import logging

logger = logging.getLogger("one_place.email")


class EmailService:
    def __init__(self, app=None):
        self.enabled = False
        if app is not None:
            self.init_app(app)

    def init_app(self, app):
        # Flip on once BREVO_API_KEY (or similar) is configured and a
        # real send_transactional_email() implementation replaces the
        # logger.info() calls below.
        self.enabled = bool(app.config.get("BREVO_API_KEY"))

    def send_donation_receipt(self, donor, donation):
        if not donor.email_receipts:
            return
        logger.info(
            "[email stub] would send donation receipt for donation=%s to=%s amount=%s %s",
            donation.id, donor.email, donation.amount, donation.currency,
        )

    def send_refund_confirmation(self, donor, donation):
        logger.info(
            "[email stub] would send refund confirmation for donation=%s to=%s",
            donation.id, donor.email,
        )

    def send_subscription_confirmation(self, donor, subscription):
        logger.info(
            "[email stub] would send subscription confirmation for subscription=%s to=%s",
            subscription.id, donor.email,
        )

    def send_subscription_cancellation(self, donor, subscription):
        logger.info(
            "[email stub] would send subscription cancellation for subscription=%s to=%s",
            subscription.id, donor.email,
        )

    def send_admin_invitation(self, email: str, invite_link: str):
        logger.info("[email stub] would send admin invitation to=%s link=%s", email, invite_link)

    def send_admin_promotion(self, email: str, role: str):
        logger.info("[email stub] would send congratulatory promotion email to=%s role=%s", email, role)

    def send_contact_ack(self, message):
        logger.info("[email stub] would send contact ack to=%s", message.email)


email_service = EmailService()
