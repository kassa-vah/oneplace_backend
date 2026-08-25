from datetime import datetime, timezone

from flask import Blueprint, jsonify
from sqlalchemy import func

from app.extensions import db
from app.utils.decorators import require_admin
from app.models.donation import Donation, DonationStatus
from app.models.subscription import Subscription, SubscriptionStatus
from app.models.cause import Cause

metrics_bp = Blueprint("metrics", __name__, url_prefix="/api/admin/metrics")


@metrics_bp.get("/overview")
@require_admin
def overview():
    """
    Financial metrics computed from Donation records at request time —
    never from a cached counter (spec #66/#97/#98). The admin dashboard
    is expected to consume this, not recompute totals client-side from
    a raw donation list.
    """
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    total_raised = (
        db.session.query(func.coalesce(func.sum(Donation.amount), 0))
        .filter(Donation.status == DonationStatus.SUCCESSFUL)
        .scalar()
    )

    monthly_raised = (
        db.session.query(func.coalesce(func.sum(Donation.amount), 0))
        .filter(
            Donation.status == DonationStatus.SUCCESSFUL,
            Donation.created_at >= month_start,
        )
        .scalar()
    )

    active_recurring_total = (
        db.session.query(func.coalesce(func.sum(Subscription.amount), 0))
        .filter(Subscription.status == SubscriptionStatus.ACTIVE)
        .scalar()
    )

    total_donation_count = Donation.query.filter_by(status=DonationStatus.SUCCESSFUL).count()

    top_causes_query = (
        db.session.query(
            Cause.id,
            Cause.title,
            func.coalesce(func.sum(Donation.amount), 0).label("raised"),
        )
        .join(Donation, Donation.cause_id == Cause.id)
        .filter(Donation.status == DonationStatus.SUCCESSFUL)
        .group_by(Cause.id, Cause.title)
        .order_by(func.sum(Donation.amount).desc())
        .limit(5)
        .all()
    )

    return jsonify(
        {
            "total_raised": str(total_raised),
            "monthly_raised": str(monthly_raised),
            "active_recurring_monthly_total": str(active_recurring_total),
            "successful_donation_count": total_donation_count,
            "top_causes": [
                {"cause_id": row.id, "title": row.title, "raised": str(row.raised)}
                for row in top_causes_query
            ],
        }
    )
