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
@click.option("--email", required=True, help="Email on record for this account")
@click.option(
    "--firebase-uid",
    required=False,
    help="Use an existing Firebase account's UID. Omit this if you want the command to create the Firebase account for you (requires --password).",
)
@click.option(
    "--password",
    required=False,
    help="Create a brand-new Firebase account with this password. Only needed the first time — for seeding the very first superadmin before any account exists.",
)
@with_appcontext
def bootstrap_superadmin(email, firebase_uid, password):
    """
    Creates (or promotes) the very first superadmin directly, bypassing
    the invitation flow — there has to be a way in before any admins
    exist. Intended to be run once, manually, from a trusted shell —
    not exposed as an HTTP endpoint, and there is no public "become an
    admin" route anywhere in this app.

    This never stores a password in this app's own database — Firebase
    Authentication remains the sole authentication authority (spec
    #89). If --password is given, it's used only to create the actual
    Firebase account via the Admin SDK; only the resulting Firebase UID
    is ever persisted here.
    """
    from firebase_admin import auth as firebase_auth

    if not firebase_uid:
        try:
            user_record = firebase_auth.get_user_by_email(email)
            firebase_uid = user_record.uid
            click.echo(f"Found an existing Firebase account for {email}.")
        except firebase_auth.UserNotFoundError:
            if not password:
                raise click.UsageError(
                    "No Firebase account exists yet for this email, and no --password "
                    "was given to create one. Pass --password to create the account, "
                    "or --firebase-uid if the account already exists under a UID you "
                    "already have."
                )
            user_record = firebase_auth.create_user(email=email, password=password, email_verified=True)
            firebase_uid = user_record.uid
            click.echo(f"Created a new Firebase account for {email}.")

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
