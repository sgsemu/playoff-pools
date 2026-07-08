# scripts/backfill_game_winners.py
"""One-time backfill for game_results.winner_team_id (migration 009).

Existing rows were synced before the winner was captured, so penalty-shootout
and extra-time knockouts sit in the DB with a tied regulation score and no
recorded winner — mis-scoring the standings. This re-derives the winner from
ESPN for every completed, non-draw world_cup row that is missing one, writes it,
then recomputes standings for the affected pools.

Idempotent: only touches rows where winner_team_id IS NULL. Safe to re-run.

Run once (after applying migrations/009_game_winner.sql):
    python -m scripts.backfill_game_winners
"""
from services.supabase_client import get_service_client
from services.espn_api import fetch_competition_results
from routes.scores import recalculate_standings


def _winner_map(competition):
    """espn_game_id -> winner_team_id for every completed, decided game ESPN
    reports across the tournament window."""
    out = {}
    # Sweep the tournament date range; ESPN scoreboard is per-day. WC 2026 runs
    # mid-June through mid-July, so a wide window covers group + knockouts.
    from datetime import date, timedelta
    d = date(2026, 6, 11)
    end = date(2026, 7, 20)
    while d <= end:
        try:
            games = fetch_competition_results(competition, dates=d.strftime("%Y%m%d"))
        except Exception:
            games = []
        for g in games:
            if g["is_complete"] and not g["is_draw"] and g.get("winner_team_id"):
                out[g["espn_game_id"]] = g["winner_team_id"]
        d += timedelta(days=1)
    return out


def main():
    sb = get_service_client()
    comps = sb.table("competitions").select("*").eq("league", "world_cup").execute().data
    affected_comp_ids = set()

    for comp in comps:
        rows = sb.table("game_results").select(
            "id,espn_game_id,home_team_id,away_team_id,home_score,away_score,is_draw,winner_team_id"
        ).eq("competition_id", comp["id"]).execute().data
        missing = [r for r in rows if r.get("winner_team_id") is None and not r["is_draw"]]
        if not missing:
            continue

        wmap = _winner_map(comp)
        fixed = 0
        for r in missing:
            winner = wmap.get(r["espn_game_id"])
            if winner is None:
                # Fall back to score comparison for ordinary decisive games;
                # leave genuinely tied rows (shootouts ESPN no longer lists) alone.
                if r["home_score"] == r["away_score"]:
                    print(f"  ! {r['espn_game_id']}: tied {r['home_score']}-{r['away_score']}, "
                          f"no ESPN winner found — SKIPPED (needs manual set)")
                    continue
                winner = r["home_team_id"] if r["home_score"] > r["away_score"] else r["away_team_id"]
            sb.table("game_results").update({"winner_team_id": winner}).eq("id", r["id"]).execute()
            tag = "shootout/ET" if r["home_score"] == r["away_score"] else "decisive"
            print(f"  ✓ {r['espn_game_id']}: winner={winner} ({tag})")
            fixed += 1
        if fixed:
            affected_comp_ids.add(comp["id"])
        print(f"{comp['name']}: {fixed}/{len(missing)} rows backfilled")

    # Recompute standings for every pool touching an affected competition.
    if affected_comp_ids:
        links = sb.table("pool_competitions").select("pool_id,competition_id").in_(
            "competition_id", list(affected_comp_ids)
        ).execute().data
        pool_ids = sorted({l["pool_id"] for l in links})
        print(f"\nRecomputing standings for {len(pool_ids)} pool(s)...")
        for pid in pool_ids:
            try:
                recalculate_standings(pid)
                print(f"  ✓ pool {pid}")
            except Exception as e:
                print(f"  ! pool {pid}: {e}")
    else:
        print("\nNothing to backfill.")


if __name__ == "__main__":
    main()
