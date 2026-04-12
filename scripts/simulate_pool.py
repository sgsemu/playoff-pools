"""
End-to-end simulation: 10 users create and draft in a pool, then simulate games.

Usage: python scripts/simulate_pool.py
"""
import sys
import os
import uuid
import random
import time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

from services.supabase_client import get_service_client
import bcrypt


def log(msg):
    print(f"  → {msg}")


def create_test_users(sb, n=10):
    """Create n test users, return list of user dicts."""
    print(f"\n1. CREATING {n} TEST USERS")
    users = []
    for i in range(1, n + 1):
        email = f"simuser{i}_{uuid.uuid4().hex[:6]}@test.com"
        pw_hash = bcrypt.hashpw(b"testpass123", bcrypt.gensalt()).decode()
        result = sb.table("users").insert({
            "email": email,
            "password_hash": pw_hash,
            "display_name": f"Player {i}",
        }).execute()
        user = result.data[0]
        users.append(user)
        log(f"Created {user['display_name']} ({email})")
    return users


def create_pool(sb, creator):
    """Create a test pool."""
    print(f"\n2. CREATING POOL")
    import secrets
    pool = sb.table("pools").insert({
        "creator_id": creator["id"],
        "name": "Simulation Pool",
        "league": "nba",
        "type": "draft",
        "invite_code": secrets.token_urlsafe(8),
        "buy_in": "$50",
        "payout_description": "1st: 70%, 2nd: 30%",
        "scoring_config": {"type": "per_win", "points_per_win": 1},
        "auction_config": {},
        "draft_mode": "live",
        "draft_status": "active",
        "timer_seconds": 60,
        "season_year": 2026,
    }).execute().data[0]

    # Add creator as member
    sb.table("pool_members").insert({
        "pool_id": pool["id"],
        "user_id": creator["id"],
        "role": "creator",
    }).execute()
    log(f"Pool created: {pool['name']} (id: {pool['id'][:8]}...)")
    return pool


def join_pool(sb, pool, users):
    """Have all users join the pool."""
    print(f"\n3. JOINING POOL ({len(users)} users)")
    members = []
    for user in users:
        # Check if already a member (creator)
        existing = sb.table("pool_members").select("id").eq(
            "pool_id", pool["id"]
        ).eq("user_id", user["id"]).execute().data
        if existing:
            members.append(existing[0])
            log(f"{user['display_name']} already in pool (creator)")
            continue

        result = sb.table("pool_members").insert({
            "pool_id": pool["id"],
            "user_id": user["id"],
            "role": "member",
        }).execute()
        members.append(result.data[0])
        log(f"{user['display_name']} joined")

    # Re-fetch all members with IDs
    all_members = sb.table("pool_members").select("*").eq(
        "pool_id", pool["id"]
    ).order("joined_at").execute().data
    return all_members


def run_draft(sb, pool, members):
    """Snake draft: each member picks teams from NBA + NHL."""
    print(f"\n4. RUNNING SNAKE DRAFT")

    # Get available teams
    nba_teams = sb.table("nba_teams").select("*").order("seed").execute().data
    nhl_teams = sb.table("nhl_teams").select("*").order("seed").execute().data

    all_teams = []
    for t in nba_teams:
        all_teams.append({"id": t["id"], "name": t["name"], "abbr": t["abbreviation"], "league": "nba"})
    for t in nhl_teams:
        all_teams.append({"id": t["id"], "name": t["name"], "abbr": t["abbreviation"], "league": "nhl"})

    random.shuffle(all_teams)  # Randomize available order for variety

    num_members = len(members)
    total_teams = len(all_teams)
    num_rounds = total_teams // num_members

    log(f"{total_teams} teams available, {num_members} members, {num_rounds} rounds")

    pick_order = 0
    picks_made = []

    for rnd in range(1, num_rounds + 1):
        # Snake: odd rounds go forward, even rounds go backward
        order = list(range(num_members)) if rnd % 2 == 1 else list(range(num_members - 1, -1, -1))

        for idx in order:
            if not all_teams:
                break
            member = members[idx]
            team = all_teams.pop(0)
            pick_order += 1

            sb.table("draft_picks").insert({
                "pool_id": pool["id"],
                "member_id": member["id"],
                "nba_team_id": team["id"],
                "team_id": team["id"],
                "league": team["league"],
                "pick_order": pick_order,
                "round": rnd,
            }).execute()

            picks_made.append({
                "member_id": member["id"],
                "team_id": team["id"],
                "league": team["league"],
                "team_name": team["name"],
            })
            log(f"Round {rnd}, Pick #{pick_order}: Member {idx+1} → {team['abbr']} ({team['league'].upper()})")

    log(f"\nDraft complete! {len(picks_made)} picks made")
    return picks_made


def simulate_games(sb, n_games=10):
    """Simulate playoff game results for both NBA and NHL."""
    print(f"\n5. SIMULATING {n_games} GAMES")

    nba_teams = sb.table("nba_teams").select("id, abbreviation").execute().data
    nhl_teams = sb.table("nhl_teams").select("id, abbreviation").execute().data

    if not nba_teams or not nhl_teams:
        log("No teams found — run seed_all_teams.py first")
        return 0

    games_created = 0
    for i in range(n_games):
        # Alternate between NBA and NHL
        if i % 2 == 0:
            league = "nba"
            teams = random.sample(nba_teams, 2)
            teams_table = "nba_teams"
        else:
            league = "nhl"
            teams = random.sample(nhl_teams, 2)
            teams_table = "nhl_teams"

        home = teams[0]
        away = teams[1]
        # Random score
        home_score = random.randint(85, 130) if league == "nba" else random.randint(1, 6)
        away_score = random.randint(85, 130) if league == "nba" else random.randint(1, 6)
        # Avoid ties in NHL
        if league == "nhl" and home_score == away_score:
            home_score += 1

        game_id = f"sim_{uuid.uuid4().hex[:10]}"

        sb.table("game_results").insert({
            "espn_game_id": game_id,
            "home_team_id": home["id"],
            "away_team_id": away["id"],
            "home_score": home_score,
            "away_score": away_score,
            "round": 1,
            "league": league,
            "game_date": "2026-04-12",
        }).execute()

        winner = home if home_score > away_score else away
        loser = away if home_score > away_score else home

        # Update team records
        try:
            team_data = sb.table(teams_table).select("playoff_wins").eq("id", winner["id"]).execute().data[0]
            sb.table(teams_table).update({"playoff_wins": team_data["playoff_wins"] + 1}).eq("id", winner["id"]).execute()
            team_data = sb.table(teams_table).select("playoff_losses").eq("id", loser["id"]).execute().data[0]
            sb.table(teams_table).update({"playoff_losses": team_data["playoff_losses"] + 1}).eq("id", loser["id"]).execute()
        except Exception as e:
            log(f"  Warning updating records: {e}")

        log(f"Game {i+1}: {league.upper()} — {home['abbreviation']} {home_score} vs {away['abbreviation']} {away_score} → Winner: {winner['abbreviation']}")
        games_created += 1

    log(f"{games_created} games simulated")
    return games_created


def calculate_standings(sb, pool):
    """Recalculate standings for the pool."""
    print(f"\n6. CALCULATING STANDINGS")

    from services.scoring import calculate_team_scores

    scoring_config = pool["scoring_config"]

    # Get all game results
    games = sb.table("game_results").select("*").execute().data
    team_wins = {}
    for g in games:
        winner_id = g["home_team_id"] if g["home_score"] > g["away_score"] else g["away_team_id"]
        team_wins[winner_id] = team_wins.get(winner_id, 0) + 1

    # Get member teams from draft picks
    picks = sb.table("draft_picks").select("*").eq("pool_id", pool["id"]).execute().data
    member_teams = {}
    for p in picks:
        tid = p.get("team_id") or p.get("nba_team_id")
        member_teams.setdefault(p["member_id"], []).append(tid)

    # Calculate scores
    scores = calculate_team_scores(scoring_config, team_wins, member_teams, {})

    # Get member display names
    members = sb.table("pool_members").select("id, user_id").eq("pool_id", pool["id"]).execute().data
    member_names = {}
    for m in members:
        user = sb.table("users").select("display_name").eq("id", m["user_id"]).execute().data
        member_names[m["id"]] = user[0]["display_name"] if user else "Unknown"

    # Sort by score
    sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)

    log("STANDINGS:")
    log(f"{'Rank':<6}{'Player':<20}{'Points':<10}{'Teams'}")
    log("-" * 60)
    for rank, (member_id, total) in enumerate(sorted_scores, 1):
        team_ids = member_teams.get(member_id, [])
        team_names = []
        for tid in team_ids:
            wins = team_wins.get(tid, 0)
            if wins > 0:
                team_names.append(f"{tid}({wins}W)")
        teams_str = ", ".join(team_names) if team_names else "no wins yet"
        name = member_names.get(member_id, "?")
        log(f"#{rank:<5}{name:<20}{total:<10}{teams_str}")

        # Upsert standings
        sb.table("pool_standings").upsert({
            "pool_id": pool["id"],
            "member_id": member_id,
            "rank": rank,
            "total_points": total,
            "points_breakdown": {},
        }, on_conflict="pool_id,member_id").execute()

        # Update denormalized total on pool_members
        sb.table("pool_members").update({"total_points": total}).eq("id", member_id).execute()

    return sorted_scores


def cleanup(sb, pool, users):
    """Remove all simulation data."""
    print(f"\n7. CLEANUP")
    pool_id = pool["id"]

    sb.table("draft_picks").delete().eq("pool_id", pool_id).execute()
    sb.table("auction_bids").delete().eq("pool_id", pool_id).execute()
    sb.table("pool_standings").delete().eq("pool_id", pool_id).execute()
    sb.table("pool_members").delete().eq("pool_id", pool_id).execute()
    sb.table("pools").delete().eq("id", pool_id).execute()

    # Delete simulated game results
    sb.table("game_results").delete().like("espn_game_id", "sim_%").execute()

    # Reset team records
    for table in ["nba_teams", "nhl_teams"]:
        teams = sb.table(table).select("id").execute().data
        for t in teams:
            sb.table(table).update({"playoff_wins": 0, "playoff_losses": 0}).eq("id", t["id"]).execute()

    # Delete test users
    for u in users:
        sb.table("users").delete().eq("id", u["id"]).execute()

    log("All simulation data cleaned up")


if __name__ == "__main__":
    sb = get_service_client()

    print("=" * 60)
    print("  PLAYOFF POOLS — FULL SIMULATION")
    print("=" * 60)

    try:
        # Step 1: Create users
        users = create_test_users(sb, 10)

        # Step 2: Create pool
        pool = create_pool(sb, users[0])

        # Step 3: Join pool
        members = join_pool(sb, pool, users)

        # Step 4: Draft
        picks = run_draft(sb, pool, members)

        # Step 5: Simulate games
        simulate_games(sb, n_games=12)

        # Step 6: Calculate standings
        standings = calculate_standings(sb, pool)

        # Simulate more games and recalculate
        print(f"\n--- SIMULATING MORE GAMES ---")
        simulate_games(sb, n_games=8)
        standings2 = calculate_standings(sb, pool)

        print(f"\n{'=' * 60}")
        print(f"  SIMULATION COMPLETE — ALL STEPS PASSED")
        print(f"{'=' * 60}")

    finally:
        # Always cleanup
        try:
            cleanup(sb, pool, users)
        except Exception as e:
            print(f"Cleanup error (non-fatal): {e}")
