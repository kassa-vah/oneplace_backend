# One Place — Backend

Flask backend for the One Place donation platform.

## The permission model, in plain terms

- **Making a one-time donation never requires an account.** Anyone can
  donate. A donor can ask to be shown anonymously on the cause page
  (`is_anonymous`) — that only affects public display, One Place still
  has their real record internally.
- **Starting a subscription (recurring giving) requires registration.**
  You must be signed in via Firebase to create one, because you need a
  way to come back later and manage/cancel it.
- **Registering an account is not the same as becoming an admin.**
  Signing in with Firebase just makes you a donor. Asking to become an
  admin is a separate, explicit step, and it stays `pending` until a
  superadmin approves it.
- **Admins manage causes/beneficiaries/donations day to day.** They can
  create, edit, publish, and archive causes; they cannot approve or
  suspend other admins.
- **Superadmins are admins with one extra power**: approving, suspending,
  and inviting other admins. That's the entire difference between the
  two roles.

## Structure

```
app/
  config.py             # env-driven config, no secrets in code
  extensions.py          # db, migrate, cors singletons
  error_handlers.py      # safe error responses (no stack traces to clients)
  cli.py                  # flask seed-causes / flask bootstrap-superadmin
  models/
    __init__.py
    admin.py               # AdminUser, AdminInvitation, AuditLog
    cause.py                 # Cause, CauseStatus, slugify()
    beneficiary.py            # Beneficiary, BeneficiaryStatus
    donation.py                 # Donor, Donation, DonationStatus, PaymentTransaction, PaymentWebhookEvent
    subscription.py               # Subscription, SubscriptionStatus
    content.py                      # ContactMessage
  routes/                      # ONE blueprint per file — see below
    __init__.py
    causes.py               # public GET + admin CRUD/publish/archive
    beneficiaries.py          # public GET + admin CRUD/archive
    donations.py                 # public create (no auth) + webhook stub + admin list/export/refund
    subscriptions.py                # create (requires auth) + donor self-service + webhook stub + admin list/cancel
    admins.py                          # superadmin-only: approve/suspend/invite OTHER admins
    auth.py                              # self-service: /me, request-access, accept invite
    content.py                             # public contact form + admin inbox
    metrics.py                               # admin dashboard financial metrics
  services/
    firebase.py            # Firebase Admin SDK init + token verification
    payments.py               # PaymentProvider interface + TestPaymentProvider
    email.py                     # EmailService (Brevo) — logging stub for now
  utils/
    decorators.py          # require_firebase_auth / require_admin / require_superadmin
    security.py                # invitation token hashing, idempotency key helpers
    pagination.py                # shared {items, pagination} envelope
migrations/                # Alembic migration history
run.py                      # entrypoint
```

Each route file defines exactly **one Flask `Blueprint`**, with public,
donor-self-service, and admin routes all living in that same blueprint —
told apart by path (`/api/causes` vs `/api/admin/causes`) and by the
`@require_admin` / `@require_superadmin` decorator, not by extra
`Blueprint` objects per audience. That's the earlier "admin_causes vs
causes" split, cut down: 9 blueprints total now (one per file above,
plus `health`), not 14.

`admins.py` and `auth.py` are the one split that's real, not accidental:
`admins.py` is what a **superadmin does to someone else's** admin
account; `auth.py` is what **any signed-in user does to their own**
account (including asking to become an admin). Same underlying
`AdminUser` model, two very different audiences.

## Local setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# fill in DATABASE_URL and FIREBASE_CREDENTIALS_PATH
```

You'll need:
- A Postgres database (Supabase or Render both work — put the connection
  string in `DATABASE_URL`)
- A Firebase service account JSON, referenced by `FIREBASE_CREDENTIALS_PATH`
  (keep this file out of git — it's already in `.gitignore`)

## Running it

```bash
export FLASK_APP=run.py

flask db upgrade          # apply migrations
flask seed-causes         # optional: sample beneficiary + two causes

flask run                 # http://localhost:5000
```

## Getting your first admin in

```bash
flask bootstrap-superadmin --firebase-uid <uid> --email you@example.com
```

`bootstrap-superadmin` is a CLI command, not an HTTP endpoint, on
purpose — there's no public "become an admin" route. From then on:

- **Self-request flow**: a signed-in user calls `POST /api/auth/request-access`
  → creates a `pending` AdminUser → a superadmin calls
  `POST /api/admin/admins/<id>/approve`
- **Invitation flow**: a superadmin calls `POST /api/admin/invitations` (returns
  a one-time plaintext token) → the invitee calls
  `POST /api/auth/invitations/accept` with that token while signed into Firebase

## API surface

### Public — no auth required
```
GET    /health
GET    /api/causes                            published causes only
GET    /api/causes/<slug>
GET    /api/beneficiaries                      active beneficiaries only
GET    /api/beneficiaries/<id>
POST   /api/donations                          one-time donation — no account needed
POST   /api/donations/webhook/<provider>         idempotent webhook stub
POST   /api/contact                              submit a contact message
```

### Requires Firebase sign-in, but NOT admin
```
GET    /api/auth/me                             check your own status
POST   /api/auth/request-access                   ask to become an admin
POST   /api/auth/invitations/accept                 redeem an admin invite
POST   /api/subscriptions                             start recurring giving — registration required
GET    /api/subscriptions/me                            your own subscriptions
POST   /api/subscriptions/me/<id>/cancel                  cancel your own subscription
```

### Requires an active AdminUser
```
GET    /api/admin/causes            POST/PATCH create/edit, POST publish|archive on /<id>
GET    /api/admin/beneficiaries      POST/PATCH create/edit, POST archive on /<id>
GET    /api/admin/donations           ?status=&cause_id=
GET    /api/admin/donations/export     CSV
POST   /api/admin/donations/<id>/refund
GET    /api/admin/subscriptions
POST   /api/admin/subscriptions/<id>/cancel   admin-initiated (audited — different from donor's own cancel)
GET    /api/admin/metrics/overview             computed from Donation records, never cached
GET    /api/admin/contact-messages
POST   /api/admin/contact-messages/<id>/resolve
```

### Requires an active superadmin
```
GET    /api/admin/admins             POST approve|suspend on /<id>
GET    /api/admin/invitations          POST /revoke on /<id>
POST   /api/admin/invitations
```

Every create/update/publish/archive/refund/approve/suspend/invite writes
an `AuditLog` row. A donor cancelling their *own* subscription does not —
that's not an administrative action.

## What's deliberately still a stub

- **Payments**: only `TestPaymentProvider` exists (`app/services/payments.py`).
  It "succeeds" every payment instantly so the donation/subscription
  flow, idempotency, and the state machine can be exercised end-to-end
  before any real money moves. Real Stripe/Paystack/M-Pesa integration
  is next.
- **Email**: `EmailService` (`app/services/email.py`) logs what it
  would send instead of calling Brevo. Set `BREVO_API_KEY` and replace
  the logging calls with real Brevo calls when ready.
- **Refunds**: tracked as fields on `Donation` (`refund_reason`,
  `refund_reference`, `refunded_at`) rather than a separate `Refund`
  model, since only full refunds exist today.

## Milestone checklist (spec #101, items 1–18)

- [x] 1–14 — Flask/DB/migrations/seed/public causes/Firebase/admin+superadmin
      auth/cause CRUD/archive-hides-from-public
- [x] 15. Donation records can be created in a controlled/test state
- [x] 16. Duplicate financial records are prevented (idempotency_key on
      `Donation`, provider+event_id uniqueness on `PaymentWebhookEvent`,
      provider+provider_transaction_id uniqueness on `PaymentTransaction`)
- [x] 17. Audit actions are recorded
- [ ] 18. Tests pass — still no automated `tests/` suite; everything has
      been smoke-tested manually via a test client, not checked-in pytest

## Next steps

1. Write an actual `tests/` suite (pytest), including the financial edge
   cases in spec #99
2. Real payment provider integration, one at a time (Paystack, then M-Pesa)
3. Real Brevo integration in `EmailService`
4. PII/audit-log retention policy (spec #77) — currently unlimited
