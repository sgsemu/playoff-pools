import smtplib
from email.message import EmailMessage

import config


def _send_email(to_email, subject, html):
    """Send one HTML email via SMTP (Gmail). No-ops (logs) if SMTP isn't
    configured, so callers never crash in dev/unconfigured envs."""
    if not config.SMTP_USER or not config.SMTP_PASSWORD:
        print(f"[email] SMTP not configured; skipping send to {to_email}: {subject}")
        return None
    msg = EmailMessage()
    msg["From"] = config.MAIL_FROM
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content("This message requires an HTML-capable email client.")
    msg.add_alternative(html, subtype="html")
    with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=15) as s:
        s.starttls()
        s.login(config.SMTP_USER, config.SMTP_PASSWORD)
        s.send_message(msg)
    return True


def send_pool_invite(to_email, pool_name, invite_code):
    subject = f"You're invited to join {pool_name}!"
    html = f"""
    <h2>You've been invited to a playoff pool!</h2>
    <p>Join <strong>{pool_name}</strong> and compete with friends.</p>
    <p><a href="{config.APP_URL}/join/{invite_code}"
           style="background:#7c6ef0;color:#fff;padding:12px 24px;border-radius:6px;text-decoration:none;display:inline-block;">
        Join Pool
    </a></p>
    """
    return _send_email(to_email, subject, html)


def send_draft_turn_notification(to_email, pool_name, pool_id):
    subject = f"It's your turn to pick in {pool_name}"
    html = f"""
    <h2>Your turn!</h2>
    <p>It's your turn to make a pick in <strong>{pool_name}</strong>.</p>
    <p><a href="{config.APP_URL}/pool/{pool_id}/draft"
           style="background:#7c6ef0;color:#fff;padding:12px 24px;border-radius:6px;text-decoration:none;display:inline-block;">
        Make Your Pick
    </a></p>
    """
    return _send_email(to_email, subject, html)


def send_auction_alert(to_email, pool_name, pool_id, team_name):
    subject = f"New team up for bidding: {team_name}"
    html = f"""
    <h2>{team_name} is up for bidding!</h2>
    <p>A new team is available in <strong>{pool_name}</strong>.</p>
    <p><a href="{config.APP_URL}/pool/{pool_id}/draft"
           style="background:#7c6ef0;color:#fff;padding:12px 24px;border-radius:6px;text-decoration:none;display:inline-block;">
        Place Your Bid
    </a></p>
    """
    return _send_email(to_email, subject, html)


def send_password_reset(to_email, reset_url):
    subject = "Reset your Playoff Pools password"
    html = f"""
    <h2>Reset your password</h2>
    <p>We received a request to reset your Playoff Pools password. Click the button below to choose a new one.</p>
    <p><a href="{reset_url}"
           style="background:#7c6ef0;color:#fff;padding:12px 24px;border-radius:6px;text-decoration:none;display:inline-block;">
        Reset Password
    </a></p>
    <p>This link expires in 1 hour. If you didn't request a password reset, you can safely ignore this email.</p>
    """
    return _send_email(to_email, subject, html)


def send_deadline_reminder(to_email, pool_name, pool_id, hours_left):
    subject = f"Reminder: {hours_left}h left for your pick in {pool_name}"
    html = f"""
    <h2>Don't forget!</h2>
    <p>You have <strong>{hours_left} hours</strong> left to make your pick in <strong>{pool_name}</strong>.</p>
    <p><a href="{config.APP_URL}/pool/{pool_id}/draft"
           style="background:#7c6ef0;color:#fff;padding:12px 24px;border-radius:6px;text-decoration:none;display:inline-block;">
        Go Now
    </a></p>
    """
    return _send_email(to_email, subject, html)
