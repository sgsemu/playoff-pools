def compute_salaries(players, cap=50000):
    """
    Compute salary values for NBA players based on PPG.
    Higher PPG = higher salary.

    Args:
        players: list of dicts with keys: id, name, ppg
        cap: the salary cap amount (default $50,000)

    Returns:
        dict mapping player id -> salary value
    """
    if not players:
        return {}

    # Sort by PPG descending
    sorted_players = sorted(players, key=lambda p: p.get("ppg", 0), reverse=True)

    max_ppg = sorted_players[0].get("ppg", 1) if sorted_players else 1
    if max_ppg == 0:
        max_ppg = 1

    min_salary = cap * 0.01   # Floor: 1% of cap ($500 at $50K cap)
    max_salary = cap * 0.20   # Ceiling: 20% of cap ($10K at $50K cap)

    salaries = {}
    for p in sorted_players:
        ppg = p.get("ppg", 0)
        ratio = ppg / max_ppg
        salary = min_salary + (max_salary - min_salary) * ratio
        salaries[p["id"]] = round(salary)

    return salaries
