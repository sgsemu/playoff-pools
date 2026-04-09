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
