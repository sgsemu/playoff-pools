from services.salary_generator import compute_salaries


def test_compute_salaries_ranks_by_composite():
    players = [
        {"id": 1, "name": "Star", "ppg": 30.0, "rpg": 8.0, "apg": 6.0},
        {"id": 2, "name": "Role Player", "ppg": 10.0, "rpg": 3.0, "apg": 2.0},
        {"id": 3, "name": "Mid", "ppg": 18.0, "rpg": 5.0, "apg": 4.0},
    ]
    salaries = compute_salaries(players, cap=50000)
    assert salaries[1] > salaries[3] > salaries[2]


def test_compute_salaries_all_positive():
    players = [
        {"id": 1, "name": "A", "ppg": 25.0, "rpg": 5.0, "apg": 5.0},
        {"id": 2, "name": "B", "ppg": 5.0, "rpg": 2.0, "apg": 1.0},
    ]
    salaries = compute_salaries(players, cap=50000)
    assert all(v > 0 for v in salaries.values())


def test_top_player_under_half_cap():
    players = [
        {"id": 1, "name": "Best", "ppg": 35.0, "rpg": 10.0, "apg": 8.0},
        {"id": 2, "name": "Worst", "ppg": 2.0, "rpg": 1.0, "apg": 0.5},
    ]
    salaries = compute_salaries(players, cap=50000)
    # No single player should cost more than 20% of the cap (so 5 min-players can fill a roster)
    assert salaries[1] <= 50000 * 0.20
