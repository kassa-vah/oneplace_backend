import logging
import os

import sib_api_v3_sdk
from sib_api_v3_sdk.rest import ApiException

logger = logging.getLogger("one_place")


# ── Shared brand shell (no credentials needed — pure HTML) ────────────────────

def _wrap(body_html: str) -> str:
    """
    Wraps arbitrary inner HTML in the One Place, Inc. branded email shell.
    Fonts, colours and spacing mirror the site's globals.css
    (Playfair Display / DM Sans, navy #0d0d0c + gold #e8570a).
    """
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <!--[if mso]><xml><o:OfficeDocumentSettings><o:PixelsPerInch>96</o:PixelsPerInch></o:OfficeDocumentSettings></xml><![endif]-->
  <style>
    /* Google Fonts – Playfair Display + DM Sans */
    @import url('https://fonts.googleapis.com/css2?family=Playfair+Display:ital,wght@0,400;0,600;0,700;0,900;1,400;1,600&family=DM+Sans:wght@300;400;500;700&display=swap');

    /* Reset */
    body, table, td {{ margin:0; padding:0; }}
    img {{ border:0; display:block; }}
    a {{ color:inherit; text-decoration:none; }}
  </style>
</head>
<body style="
  margin:0; padding:0;
  background-color:#f6f2ea;
  font-family:'DM Sans', Arial, sans-serif;
  color:#0d0d0c;
  -webkit-text-size-adjust:100%;
">

<!-- Outer table -->
<table width="100%" cellpadding="0" cellspacing="0" border="0"
       style="background:#f6f2ea; padding:40px 0;">
  <tr>
    <td align="center">

      <!-- Card -->
      <table width="560" cellpadding="0" cellspacing="0" border="0"
             style="
               max-width:560px; width:100%;
               background:#ffffff;
               border:1px solid #e6dcc8;
               border-radius:20px;
               overflow:hidden;
             ">

        <!-- ── Header band ── -->
        <tr>
          <td style="
            background:linear-gradient(135deg, #fdf0e6 0%, #f9d9bc 100%);
            padding:32px 48px 28px;
            border-bottom:1px solid #f0c79a;
            text-align:center;
          ">
            <!-- Brand name in display font -->
            <div style="
              font-family:'Playfair Display', Georgia, serif;
              font-size:28px;
              font-weight:700;
              letter-spacing:0.01em;
              color:#0d0d0c;
              line-height:1.1;
            ">One Place, Inc.</div>
            <div style="
              font-family:'DM Sans', Arial, sans-serif;
              font-size:11px;
              font-weight:500;
              letter-spacing:0.12em;
              text-transform:uppercase;
              color:#e8570a;
              margin-top:4px;
            ">Admin Portal</div>
          </td>
        </tr>

        <!-- ── Body ── -->
        <tr>
          <td style="padding:40px 48px;">
            {body_html}
          </td>
        </tr>

        <!-- ── Footer ── -->
        <tr>
          <td style="
            background:#fbf8f3;
            padding:20px 48px;
            border-top:1px solid #e6dcc8;
            text-align:center;
          ">
            <p style="
              font-family:'DM Sans', Arial, sans-serif;
              font-size:11px;
              color:#6b6560;
              margin:0;
              letter-spacing:0.04em;
            ">
              One Place, Inc. Admin Portal &mdash; please do not reply to this email.
            </p>
          </td>
        </tr>

      </table>
      <!-- /Card -->

    </td>
  </tr>
</table>
<!-- /Outer table -->

</body>
</html>"""


# ── Shared style snippets ─────────────────────────────────────────────────────

_H2 = (
    'font-family:\'Playfair Display\', Georgia, serif;'
    'font-size:30px; font-weight:700; color:#0d0d0c;'
    'margin:0 0 16px; line-height:1.15;'
)
_P = (
    'font-family:\'DM Sans\', Arial, sans-serif;'
    'font-size:15px; line-height:1.7; color:#6b6560; margin:0 0 12px;'
)
_P_SMALL = (
    'font-family:\'DM Sans\', Arial, sans-serif;'
    'font-size:13px; line-height:1.6; color:#8a8478; margin:0;'
)
_BTN = (
    'display:inline-block;'
    'background:linear-gradient(135deg, #e8570a 0%, #f4874a 100%);'
    'color:#ffffff;'
    'font-family:\'DM Sans\', Arial, sans-serif;'
    'font-size:14px; font-weight:500; letter-spacing:0.04em;'
    'padding:13px 32px; border-radius:999px;'
    'text-decoration:none;'
)
_DIVIDER = '<div style="height:1px;background:#e6dcc8;margin:24px 0;"></div>'


def _tag_pill(text: str) -> str:
    return (
        f'<span style="'
        f'display:inline-block; font-family:\'DM Sans\',Arial,sans-serif;'
        f'font-size:11px; font-weight:500; letter-spacing:0.1em;'
        f'text-transform:uppercase; color:#e8570a;'
        f'background:#fdf0e6; border:1px solid #f4874a;'
        f'padding:5px 14px; border-radius:999px; margin-bottom:16px;'
        f'">{text}</span>'
    )


class EmailService:
    """
    Brevo-backed transactional email sender, wired as a Flask extension
    (init_app pattern, same as db/migrate/cors in app/__init__.py) so
    credentials are read from app.config once at startup rather than
    read fresh from os.environ on every send.

    Usage:
        from app.services.email import email_service
        email_service.send_otp_email(to_email=..., to_name=..., otp=...)
    """

    def __init__(self):
        self._configuration = None
        self._api_instance = None
        self._sender_email = "noreply@example.com"
        self._sender_name = "One Place Admin"
        self._initialized = False

    def init_app(self, app) -> None:
        api_key = app.config.get("BREVO_API_KEY") or os.environ.get("BREVO_API_KEY", "")

        self._configuration = sib_api_v3_sdk.Configuration()
        self._configuration.api_key["api-key"] = api_key
        self._api_instance = sib_api_v3_sdk.TransactionalEmailsApi(
            sib_api_v3_sdk.ApiClient(self._configuration)
        )
        self._sender_email = (
            app.config.get("BREVO_SENDER_EMAIL")
            or os.environ.get("BREVO_SENDER_EMAIL", "noreply@example.com")
        )
        self._sender_name = (
            app.config.get("BREVO_SENDER_NAME")
            or os.environ.get("BREVO_SENDER_NAME", "One Place Admin")
        )
        self._initialized = True

        if not api_key:
            logger.warning(
                "BREVO_API_KEY is not set — email sending is disabled until it is configured."
            )

    # ── Internal helper ────────────────────────────────────────────────────

    def _send(self, *, to_email: str, to_name: str, subject: str, html_content: str) -> bool:
        if not self._initialized or not self._configuration.api_key.get("api-key"):
            logger.error("Email service not configured (BREVO_API_KEY missing) — cannot send to %s", to_email)
            return False

        send_smtp_email = sib_api_v3_sdk.SendSmtpEmail(
            sender      ={"name": self._sender_name, "email": self._sender_email},
            to          =[{"email": to_email, "name": to_name}],
            subject     =subject,
            html_content=html_content,
        )
        try:
            self._api_instance.send_transac_email(send_smtp_email)
            logger.info("Email sent — to=%s subject=%r", to_email, subject)
            return True
        except ApiException as exc:
            logger.error("Brevo API error sending to %s: %s", to_email, exc)
            return False
        except Exception as exc:
            logger.error("Unexpected error sending email to %s: %s", to_email, exc)
            return False

    # ── Public methods ─────────────────────────────────────────────────────

    def send_otp_email(self, *, to_email: str, to_name: str, otp: str) -> bool:
        """Send a 6-digit sign-in verification code to an active admin."""
        body = f"""
        {_tag_pill("Verification Code")}
        <h2 style="{_H2}">Your sign-in code</h2>
        <p style="{_P}">Hi {to_name},</p>
        <p style="{_P}">
          Use the code below to finish signing in to the admin portal.
          It expires in <strong style="color:#0d0d0c;">10 minutes</strong>.
        </p>

        <!-- OTP block -->
        <div style="
          background:linear-gradient(135deg,#e8570a 0%,#b8430a 100%);
          border:1px solid #b8430a;
          border-radius:14px;
          padding:32px 24px;
          text-align:center;
          margin:28px 0;
        ">
          <span style="
            font-family:'Playfair Display', Georgia, serif;
            font-size:52px;
            font-weight:600;
            letter-spacing:16px;
            color:#ffffff;
            line-height:1;
          ">{otp}</span>
          <p style="
            font-family:'DM Sans', Arial, sans-serif;
            font-size:12px;
            color:rgba(255,255,255,0.8);
            letter-spacing:0.08em;
            text-transform:uppercase;
            margin:10px 0 0;
          ">Verification code &bull; 10 min</p>
        </div>

        {_DIVIDER}
        <p style="{_P_SMALL}">
          If you did not request this code, you can safely ignore this email —
          your account remains secure without it.
        </p>
        """
        return self._send(
            to_email    =to_email,
            to_name     =to_name,
            subject     ="Your Admin Sign-In Code — One Place, Inc.",
            html_content=_wrap(body),
        )

    def send_admin_promotion(self, to_email: str, role: str) -> bool:
        """
        Tell a newly promoted admin they now have dashboard access. This
        is the only "you're now an admin" email — there is no invitation
        step anymore. A superadmin promotes an already-registered account
        directly (see app/routes/admins.py: promote_registration), so the
        recipient already has a working login; this just tells them their
        access level changed and what to expect next time they sign in.
        """
        display_name = to_email.split("@", 1)[0].replace(".", " ").replace("_", " ").title()
        body = f"""
        {_tag_pill("Account Promoted")}
        <h2 style="{_H2}">You&rsquo;re now an admin, {display_name}</h2>
        <p style="{_P}">
          Your account has been promoted to
          <strong style="color:#0d0d0c;">{role}</strong> on the One Place, Inc.
          admin portal. You can sign in with the same account you already use.
        </p>

        <!-- Highlight strip -->
        <div style="
          background:linear-gradient(135deg,#fdf0e6 0%,#f9d9bc 100%);
          border-left:4px solid #e8570a;
          border-radius:0 10px 10px 0;
          padding:18px 24px;
          margin:24px 0;
        ">
          <p style="
            font-family:'Playfair Display',Georgia,serif;
            font-size:20px; font-weight:700; color:#0d0d0c;
            margin:0 0 6px; line-height:1.3;
          ">One more step at sign-in.</p>
          <p style="
            font-family:'DM Sans',Arial,sans-serif;
            font-size:14px; color:#6b6560; margin:0; line-height:1.6;
          ">
            Each time you sign in, a one-time verification code will be sent to
            this email address to confirm it&rsquo;s you before the dashboard
            unlocks.
          </p>
        </div>

        {_DIVIDER}
        <p style="{_P_SMALL}">
          If you weren&rsquo;t expecting this, please contact your superadmin
          immediately.
        </p>
        """
        return self._send(
            to_email    =to_email,
            to_name     =display_name,
            subject     ="Your Account Was Promoted — One Place, Inc.",
            html_content=_wrap(body),
        )

    def send_admin_approved_email(self, *, to_email: str, to_name: str) -> bool:
        """Tell a reactivated admin their account is active again."""
        body = f"""
        {_tag_pill("Account Approved")}
        <h2 style="{_H2}">You&rsquo;re in, {to_name}</h2>
        <p style="{_P}">
          Your admin account has been approved and is now active.
        </p>

        <!-- Highlight strip -->
        <div style="
          background:linear-gradient(135deg,#fdf0e6 0%,#f9d9bc 100%);
          border-left:4px solid #e8570a;
          border-radius:0 10px 10px 0;
          padding:18px 24px;
          margin:24px 0;
        ">
          <p style="
            font-family:'Playfair Display',Georgia,serif;
            font-size:20px; font-weight:700; color:#0d0d0c;
            margin:0 0 6px; line-height:1.3;
          ">Welcome to the admin portal.</p>
          <p style="
            font-family:'DM Sans',Arial,sans-serif;
            font-size:14px; color:#6b6560; margin:0; line-height:1.6;
          ">
            Each time you sign in, a one-time verification code will be sent to
            this email address to confirm it&rsquo;s you.
          </p>
        </div>

        {_DIVIDER}
        <p style="{_P_SMALL}">
          If you weren&rsquo;t expecting this, please contact your superadmin
          immediately.
        </p>
        """
        return self._send(
            to_email    =to_email,
            to_name     =to_name,
            subject     ="Your Admin Account Has Been Approved — One Place, Inc.",
            html_content=_wrap(body),
        )

    def send_admin_suspended_email(self, *, to_email: str, to_name: str) -> bool:
        """Notify an admin that their account access has been suspended."""
        body = f"""
        {_tag_pill("Account Update")}
        <h2 style="{_H2}">Your admin access has changed</h2>
        <p style="{_P}">Hi {to_name},</p>
        <p style="{_P}">
          Your admin account for One Place, Inc. has been suspended and you
          will no longer be able to sign in to the admin portal.
        </p>

        <div style="
          background:#f6f2ea;
          border:1px solid #e6dcc8;
          border-radius:10px;
          padding:18px 24px;
          margin:24px 0;
        ">
          <p style="
            font-family:'DM Sans',Arial,sans-serif;
            font-size:14px; color:#6b6560;
            margin:0; line-height:1.7;
          ">
            If you believe this is a mistake, please contact your organisation&rsquo;s
            superadmin directly for further assistance.
          </p>
        </div>

        {_DIVIDER}
        <p style="{_P_SMALL}">
          This is an automated message — please do not reply.
        </p>
        """
        return self._send(
            to_email    =to_email,
            to_name     =to_name,
            subject     ="Your Admin Account Status Has Changed — One Place, Inc.",
            html_content=_wrap(body),
        )

    def send_password_reset_email(self, *, to_email: str, to_name: str, reset_link: str) -> bool:
        """
        Send a branded password-reset link email. Not currently wired to
        a route — Firebase Auth handles password reset natively. This
        exists for the case where you generate a Firebase password-reset
        action link server-side (via the Firebase Admin SDK) and want to
        deliver it through this branded template instead of Firebase's
        default plain email.

        The recipient's address is partially masked in the body — only
        the first two characters of the local part and domain name are
        shown, the rest replaced with asterisks, e.g.
        jane.doe@example.com becomes ja***@ex*****.com
        """
        def _mask(addr: str) -> str:
            local, _, domain = addr.partition("@")
            def _star(s: str) -> str:
                if len(s) <= 2:
                    return s[0] + "*" * (len(s) - 1)
                return s[:2] + "*" * (len(s) - 2)
            dot_pos  = domain.rfind(".")
            dom_name = domain[:dot_pos]  if dot_pos != -1 else domain
            dom_tld  = domain[dot_pos:]  if dot_pos != -1 else ""
            return f"{_star(local)}@{_star(dom_name)}{dom_tld}"

        masked = _mask(to_email)

        body = f"""
        {_tag_pill("Password Reset")}
        <h2 style="{_H2}">Reset your password</h2>
        <p style="{_P}">Hi {to_name},</p>
        <p style="{_P}">
          We received a password-reset request for the account associated with
          <strong style="color:#0d0d0c;">{masked}</strong>.
          Click the button below to choose a new password.
          The link expires in <strong style="color:#0d0d0c;">1 hour</strong>.
        </p>

        <div style="text-align:center; margin:32px 0;">
          <a href="{reset_link}" style="{_BTN}">
            Reset Password &rarr;
          </a>
        </div>

        {_DIVIDER}
        <p style="{_P_SMALL}">
          If you did not request a password reset, please ignore this email —
          your current password has <strong>not</strong> been changed.
        </p>
        """
        return self._send(
            to_email    =to_email,
            to_name     =to_name,
            subject     ="Reset Your Password — One Place, Inc.",
            html_content=_wrap(body),
        )


email_service = EmailService()