import resend
import config

resend.api_key = config.RESEND_API_KEY
FROM_EMAIL = "Playoff Pools <noreply@playoffpools.com>"


def send_pool_invite(to_email, pool_name, invite_code):
    return resend.Emails.send({
        "from": FROM_EMAIL,
        "to": [to_email],
        "subject": f"You're invited to join {pool_name}!",
        "html": f"""
        <h2>You've been invited to a playoff pool!</h2>
        <p>Join <strong>{pool_name}</strong> and compete with friends.</p>
        <p><a href="{config.APP_URL}/join/{invite_code}"
               style="background:#7c6ef0;color:#fff;padding:12px 24px;border-radius:6px;text-decoration:none;display:inline-block;">
            Join Pool
        </a></p>
        """
    })


def send_draft_turn_notification(to_email, pool_name, pool_id):
    return resend.Emails.send({
        "from": FROM_EMAIL,
        "to": [to_email],
        "subject": f"It's your turn to pick in {pool_name}",
        "html": f"""
        <h2>Your turn!</h2>
        <p>It's your turn to make a pick in <strong>{pool_name}</strong>.</p>
        <p><a href="{config.APP_URL}/pool/{pool_id}/draft"
               style="background:#7c6ef0;color:#fff;padding:12px 24px;border-radius:6px;text-decoration:none;display:inline-block;">
            Make Your Pick
        </a></p>
        """
    })


def send_auction_alert(to_email, pool_name, pool_id, team_name):
    return resend.Emails.send({
        "from": FROM_EMAIL,
        "to": [to_email],
        "subject": f"New team up for bidding: {team_name}",
        "html": f"""
        <h2>{team_name} is up for bidding!</h2>
        <p>A new team is available in <strong>{pool_name}</strong>.</p>
        <p><a href="{config.APP_URL}/pool/{pool_id}/draft"
               style="background:#7c6ef0;color:#fff;padding:12px 24px;border-radius:6px;text-decoration:none;display:inline-block;">
            Place Your Bid
        </a></p>
        """
    })


def send_deadline_reminder(to_email, pool_name, pool_id, hours_left):
    return resend.Emails.send({
        "from": FROM_EMAIL,
        "to": [to_email],
        "subject": f"Reminder: {hours_left}h left for your pick in {pool_name}",
        "html": f"""
        <h2>Don't forget!</h2>
        <p>You have <strong>{hours_left} hours</strong> left to make your pick in <strong>{pool_name}</strong>.</p>
        <p><a href="{config.APP_URL}/pool/{pool_id}/draft"
               style="background:#7c6ef0;color:#fff;padding:12px 24px;border-radius:6px;text-decoration:none;display:inline-block;">
            Go Now
        </a></p>
        """
    })
