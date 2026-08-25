import click
from flask.cli import with_appcontext

from app.extensions import db
from app.models.cause import Cause, CauseStatus
from app.models.beneficiary import Beneficiary
from app.models.admin import AdminUser, AdminRole, AdminStatus


@click.command("seed-causes")
@with_appcontext
def seed_causes():
    """Milestone #5 — seed a couple of sample causes (and one beneficiary) for local dev."""
    beneficiary = Beneficiary.query.filter_by(name="Sarafina Children's Home").first()
    if beneficiary is None:
        beneficiary = Beneficiary(
            name="Sarafina Children's Home",
            description="A children's home supporting orphaned and vulnerable children.",
        )
        db.session.add(beneficiary)
        db.session.flush()

    samples = [
        {
            "title": "Sarafina Children's Home",
            "slug": "sarafina-childrens-home",
            "description": "Support daily meals and schooling for children at Sarafina.",
            "status": CauseStatus.PUBLISHED,
            "featured": True,
            "beneficiary_id": beneficiary.id,
        },
        {
            "title": "Widows Flour Program",
            "slug": "widows-flour-program",
            "description": "Monthly flour and staple food support for widows in the community.",
            "status": CauseStatus.PUBLISHED,
        },
    ]

    created = 0
    for data in samples:
        if Cause.query.filter_by(slug=data["slug"]).first() is not None:
            continue
        db.session.add(Cause(**data))
        created += 1

    db.session.commit()
    click.echo(f"Seeded {created} cause(s).")


@click.command("bootstrap-superadmin")
@click.option("--firebase-uid", required=True, help="Firebase UID to promote to superadmin")
@click.option("--email", required=True, help="Email on record for this account")
@with_appcontext
def bootstrap_superadmin(firebase_uid, email):
    """
    Creates (or promotes) the very first superadmin directly, bypassing
    the invitation flow — there has to be a way in before any admins
    exist (spec #72). Intended to be run once, manually, from a trusted
    shell — not exposed as an HTTP endpoint.
    """
    admin = AdminUser.query.filter_by(firebase_uid=firebase_uid).first()
    if admin is None:
        admin = AdminUser(firebase_uid=firebase_uid, email=email.lower().strip())
        db.session.add(admin)

    admin.role = AdminRole.SUPERADMIN
    admin.status = AdminStatus.ACTIVE
    db.session.commit()

    click.echo(f"{email} is now an active superadmin.")


def register_cli(app):
    app.cli.add_command(seed_causes)
    app.cli.add_command(bootstrap_superadmin)
