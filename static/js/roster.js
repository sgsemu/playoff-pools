// roster.js — Salary cap roster picker

async function pickPlayer(playerId) {
    const resp = await fetch(`/pool/${POOL_ID}/roster/pick`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ nba_player_id: playerId })
    });

    const data = await resp.json();
    if (resp.ok) {
        location.reload();
    } else {
        alert(data.error || "Failed to add player");
    }
}

async function removePlayer(rosterId) {
    if (!confirm("Remove this player?")) return;

    const resp = await fetch(`/pool/${POOL_ID}/roster/remove`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ roster_id: rosterId })
    });

    if (resp.ok) location.reload();
    else alert("Failed to remove player");
}

function filterPlayers() {
    const search = document.getElementById("name-search").value.toLowerCase();

    document.querySelectorAll(".player-row").forEach(row => {
        const name = row.dataset.name || "";
        const team = row.dataset.team || "";
        const match = !search || name.includes(search) || team.includes(search);
        row.style.display = match ? "" : "none";
    });
}
