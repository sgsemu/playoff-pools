def match_outcomes(game):
    """Per-team (team_id, "win"|"draw"|"loss") outcomes for one game_results row.

    A completed match with is_draw is a draw for both sides. Otherwise the
    winner is taken from winner_team_id when present — this is required for
    penalty-shootout / extra-time results, where the stored home/away scores
    are the (tied) regulation score and can't reveal the winner. Falls back to
    score comparison for ordinary decisive games with no winner recorded.
    """
    home, away = game["home_team_id"], game["away_team_id"]
    if game.get("is_draw"):
        return [(home, "draw"), (away, "draw")]
    winner = game.get("winner_team_id")
    if winner is None:
        winner = home if game["home_score"] > game["away_score"] else away
    loser = away if winner == home else home
    return [(winner, "win"), (loser, "loss")]


def calculate_team_scores(config, team_wins, member_teams, series_wins):
    """
    Calculate scores for draft/auction pool members based on their teams' performance.

    Args:
        config: scoring_config dict from pool
        team_wins: dict mapping team_id -> total playoff wins
        member_teams: dict mapping member_id -> list of team_ids
        series_wins: dict mapping round_number -> list of team_ids that won that round's series

    Returns:
        dict mapping member_id -> total score
    """
    scores = {}
    scoring_type = config["type"]

    for member_id, teams in member_teams.items():
        total = 0

        if scoring_type == "per_win":
            ppw = config.get("points_per_win", 1)
            for team_id in teams:
                total += team_wins.get(team_id, 0) * ppw

        elif scoring_type == "per_round":
            round_points = {
                1: config.get("round_1", 2),
                2: config.get("round_2", 4),
                3: config.get("round_3", 6),
                4: config.get("round_4", 10),
            }
            for rnd, winning_teams in series_wins.items():
                for team_id in teams:
                    if team_id in winning_teams:
                        total += round_points.get(rnd, 0)

        elif scoring_type == "combo":
            ppw = config.get("points_per_win", 1)
            for team_id in teams:
                total += team_wins.get(team_id, 0) * ppw

            round_bonuses = {
                1: config.get("round_1_bonus", 2),
                2: config.get("round_2_bonus", 4),
                3: config.get("round_3_bonus", 6),
                4: config.get("round_4_bonus", 10),
            }
            for rnd, winning_teams in series_wins.items():
                for team_id in teams:
                    if team_id in winning_teams:
                        total += round_bonuses.get(rnd, 0)

        scores[member_id] = total

    return scores


def calculate_salary_cap_scores(config, member_players, player_stats):
    """
    Calculate scores for salary cap pool members based on their players' stats.

    Args:
        config: scoring_config dict from pool
        member_players: dict mapping member_id -> list of player_ids
        player_stats: dict mapping player_id -> {"points": N, "rebounds": N, "assists": N}

    Returns:
        dict mapping member_id -> total score
    """
    pts_mult = float(config.get("stat_points", 1))
    reb_mult = float(config.get("stat_rebounds", 0))
    ast_mult = float(config.get("stat_assists", 0))

    scores = {}
    for member_id, player_ids in member_players.items():
        total = 0.0
        for pid in player_ids:
            stats = player_stats.get(pid, {"points": 0, "rebounds": 0, "assists": 0})
            total += stats["points"] * pts_mult
            total += stats["rebounds"] * reb_mult
            total += stats["assists"] * ast_mult
        scores[member_id] = total

    return scores


def calculate_stage_weighted_scores(stages, team_results, member_teams, group_winners):
    """Score teams by per-match result weighted by stage.

    stages: list of {key, win_points, [draw_points], [group_winner_bonus]}.
    team_results: {team_ext_id: [(stage_key, "win"|"draw"|"loss"), ...]}.
    member_teams: {member_id: [team_ext_id, ...]}.
    group_winners: set of team_ext_ids that finished 1st in their group.
    """
    scores = {}
    for member_id, teams in member_teams.items():
        scores[member_id] = sum(
            stage_points_for_team(stages, team_results.get(team, []), team in group_winners)
            for team in teams
        )
    return scores


def stage_points_for_team(stages, results, is_group_winner=False):
    """Points a single team earned under stage-weighted scoring.

    stages: list of {key, win_points, [draw_points], [group_winner_bonus]}.
    results: list of (stage_key, "win"|"draw"|"loss").
    Adds the group-winner bonus when the team finished 1st in its group.
    """
    by_key = {s["key"]: s for s in stages}
    total = 0
    for stage_key, outcome in results:
        s = by_key.get(stage_key, {})
        if outcome == "win":
            total += s.get("win_points", 0)
        elif outcome == "draw":
            total += s.get("draw_points", 0)
    if is_group_winner:
        total += by_key.get("group", {}).get("group_winner_bonus", 0)
    return total
