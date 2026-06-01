"""Vercel Cron job: settles any auction pool whose closes_at has passed.

For each pool in `draft_status='auction'` with `auction_closes_at <= NOW()`:
- Writes the highest bidder per team as a `draft_picks` row.
- Transitions pool to `complete`.
- Recomputes standings.
- Teams with zero bids stay unassigned (commissioner uses the Assign form
  post-settle to hand them out).

Triggered every 15 min by vercel.json so a deadline can never sit more
than ~15 min unsettled.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

from datetime import datetime, timezone
from flask import Flask, jsonify
from services.supabase_client import get_service_client
from routes.auction import settle_pool

app = Flask(__name__)


@app.route("/api/cron/settle-auctions", methods=["GET"])
def settle_auctions():
    sb = get_service_client()
    now_iso = datetime.now(timezone.utc).isoformat()
    pools = sb.table("pools").select("*").eq("draft_status", "auction").execute().data
    settled = []
    for pool in pools:
        closes = pool.get("auction_closes_at")
        if closes and closes <= now_iso:
            result = settle_pool(sb, pool)
            settled.append({"pool_id": pool["id"], **result})
    return jsonify({"checked_at": now_iso, "settled": settled})
