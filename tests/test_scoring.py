from services.scoring import calculate_team_scores, calculate_salary_cap_scores, calculate_stage_weighted_scores


def test_per_win_scoring():
    config = {"type": "per_win", "points_per_win": 2}
    # team 1 won 3 games, team 2 won 1
    team_wins = {1: 3, 2: 1}
    # member A owns team 1, member B owns team 2
    member_teams = {"member-a": [1], "member-b": [2]}

    scores = calculate_team_scores(config, team_wins, member_teams, series_wins={})
    assert scores["member-a"] == 6
    assert scores["member-b"] == 2


def test_per_round_scoring():
    config = {"type": "per_round", "round_1": 2, "round_2": 4, "round_3": 6, "round_4": 10}
    team_wins = {}
    series_wins = {1: [1, 2], 2: [1]}  # team 1 won round 1 and 2, team 2 won round 1 (hypothetical)
    # Actually: series_wins maps round -> list of team_ids that won that round's series
    member_teams = {"member-a": [1], "member-b": [2]}

    scores = calculate_team_scores(config, team_wins, member_teams, series_wins)
    assert scores["member-a"] == 6  # 2 (round 1) + 4 (round 2)
    assert scores["member-b"] == 2  # 2 (round 1)


def test_combo_scoring():
    config = {
        "type": "combo",
        "points_per_win": 1,
        "round_1_bonus": 2, "round_2_bonus": 4, "round_3_bonus": 6, "round_4_bonus": 10
    }
    team_wins = {1: 4}  # 4 wins
    series_wins = {1: [1]}  # team 1 won round 1
    member_teams = {"member-a": [1]}

    scores = calculate_team_scores(config, team_wins, member_teams, series_wins)
    assert scores["member-a"] == 6  # 4 wins * 1 + round 1 bonus 2


def test_salary_cap_scoring_points_only():
    config = {"type": "salary_cap", "stat_points": 1.0, "stat_rebounds": 0, "stat_assists": 0}
    # member A has player 101 and 102
    member_players = {"member-a": [101, 102]}
    player_stats = {
        101: {"points": 120, "rebounds": 40, "assists": 30},
        102: {"points": 80, "rebounds": 20, "assists": 15}
    }

    scores = calculate_salary_cap_scores(config, member_players, player_stats)
    assert scores["member-a"] == 200.0  # 120 + 80


def test_salary_cap_scoring_with_multipliers():
    config = {"type": "salary_cap", "stat_points": 1.0, "stat_rebounds": 1.2, "stat_assists": 1.5}
    member_players = {"member-a": [101]}
    player_stats = {101: {"points": 100, "rebounds": 50, "assists": 40}}

    scores = calculate_salary_cap_scores(config, member_players, player_stats)
    # 100*1.0 + 50*1.2 + 40*1.5 = 100 + 60 + 60 = 220
    assert scores["member-a"] == 220.0


_WC_STAGES = [
    {"key": "group", "win_points": 3, "draw_points": 1, "group_winner_bonus": 2},
    {"key": "r32", "win_points": 3}, {"key": "r16", "win_points": 3},
    {"key": "qf", "win_points": 3}, {"key": "sf", "win_points": 4},
    {"key": "final", "win_points": 5}, {"key": "third_place", "win_points": 3},
]


def test_stage_weighted_group_and_knockout_and_bonus():
    # member m1 holds team 203. Results: group win, group draw, R32 win, SF win.
    # Team 203 also won its group (bonus +2).
    # 3 + 1 + 3 + 4 + 2 = 13
    team_results = {203: [("group", "win"), ("group", "draw"), ("r32", "win"), ("sf", "win")]}
    member_teams = {"m1": [203]}
    scores = calculate_stage_weighted_scores(_WC_STAGES, team_results, member_teams, group_winners={203})
    assert scores["m1"] == 13


def test_stage_weighted_no_bonus_when_not_group_winner():
    team_results = {203: [("group", "win")]}
    scores = calculate_stage_weighted_scores(_WC_STAGES, team_results, {"m1": [203]}, group_winners=set())
    assert scores["m1"] == 3
