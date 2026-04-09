def compute_salaries(players, cap=50000):
    """
    Compute salary values for NBA players based on regular season stats.

    Args:
        players: list of dicts with keys: id, name, ppg, rpg, apg
        cap: the salary cap amount (default $50,000)

    Returns:
        dict mapping player id -> salary value
    """
    if not players:
        return {}

    # Composite score: weighted sum of stats
    composites = []
    for p in players:
        score = p["ppg"] * 1.0 + p["rpg"] * 0.7 + p["apg"] * 0.8
        composites.append((p["id"], score))

    composites.sort(key=lambda x: x[1], reverse=True)

    max_score = composites[0][1] if composites else 1
    min_salary = cap * 0.01  # Floor: 1% of cap ($500 at $50K cap)
    max_salary = cap * 0.20  # Ceiling: 20% of cap ($10K at $50K cap)

    salaries = {}
    for player_id, score in composites:
        ratio = score / max_score if max_score > 0 else 0
        salary = min_salary + (max_salary - min_salary) * ratio
        salaries[player_id] = round(salary)

    return salaries
