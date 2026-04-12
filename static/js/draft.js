// draft.js — Live draft real-time client using Supabase Realtime

async function startDraft() {
    const resp = await fetch(`/pool/${POOL_ID}/draft/start`, { method: "POST" });
    if (resp.ok) {
        location.reload();
    } else {
        const data = await resp.json();
        alert(data.error || "Failed to start draft");
    }
}

async function pickTeam(teamId, league, teamName) {
    if (!confirm(`Pick ${teamName} (${league.toUpperCase()})?`)) return;

    const resp = await fetch(`/pool/${POOL_ID}/draft/pick`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ team_id: teamId, league: league })
    });

    const data = await resp.json();
    if (resp.ok) {
        const btn = document.querySelector(`[data-team-id="${teamId}"][data-league="${league}"]`);
        if (btn) btn.remove();
        appendPick(data.pick_order, league, teamName);
    } else {
        alert(data.error || "Failed to make pick");
    }
}

function appendPick(pickOrder, league, teamName) {
    const log = document.getElementById("draft-log");
    const entry = document.createElement("div");
    entry.className = "pick-entry";
    entry.innerHTML = `<span class="pick-num">#${pickOrder}</span> <span class="pick-league badge badge-${league}">${league.toUpperCase()}</span> <span class="pick-team">${teamName}</span>`;
    log.prepend(entry);
}

// Supabase Realtime subscription for live updates
if (typeof SUPABASE_URL !== "undefined" && SUPABASE_URL) {
    const { createClient } = supabase;
    const sb = createClient(SUPABASE_URL, SUPABASE_KEY);

    sb.channel("draft-" + POOL_ID)
        .on("postgres_changes", {
            event: "INSERT",
            schema: "public",
            table: "draft_picks",
            filter: `pool_id=eq.${POOL_ID}`
        }, (payload) => {
            location.reload();
        })
        .subscribe();
}
