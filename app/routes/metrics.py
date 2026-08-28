# ============================================================
# FILE BELONGS AT:  app/routes/metrics.py
# ============================================================
from datetime import datetime, timezone

from flask import Blueprint, jsonify
from sqlalchemy import func

from app.extensions import db
from app.utils.decorators import require_admin
from app.models.donation import Donation, DonationStatus
from app.models.cause import Cause

metrics_bp = Blueprint("metrics", __name__, url_prefix="/api/admin/metrics")


@metrics_bp.get("/overview")
@require_admin
def overview():
    """
    Totals computed from admin-entered Donation records at request
    time — never from a cached counter. No recurring-revenue figure
    here anymore: SwipeSimple handles monthly giving entirely outside
    this backend, so there's nothing here to compute it from.
    """
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    total_raised = (
        db.session.query(func.coalesce(func.sum(Donation.amount), 0))
        .filter(Donation.status == DonationStatus.RECORDED)
        .scalar()
    )

    monthly_raised = (
        db.session.query(func.coalesce(func.sum(Donation.amount), 0))
        .filter(
            Donation.status == DonationStatus.RECORDED,
            Donation.created_at >= month_start,
        )
        .scalar()
    )

    donation_count = Donation.query.filter_by(status=DonationStatus.RECORDED).count()

    top_causes_query = (
        db.session.query(
            Cause.id,
            Cause.title,
            func.coalesce(func.sum(Donation.amount), 0).label("raised"),
        )
        .join(Donation, Donation.cause_id == Cause.id)
        .filter(Donation.status == DonationStatus.RECORDED)
        .group_by(Cause.id, Cause.title)
        .order_by(func.sum(Donation.amount).desc())
        .limit(5)
        .all()
    )

    return jsonify(
        {
            "total_raised": str(total_raised),
            "monthly_raised": str(monthly_raised),
            "recorded_donation_count": donation_count,
            "top_causes": [
                {"cause_id": row.id, "title": row.title, "raised": str(row.raised)}
                for row in top_causes_query
            ],
        }
    )