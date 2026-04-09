// auction.js — Live auction real-time client

let currentTeamId = null;
let currentTeamName = "";

async function startDraft() {
    const resp = await fetch(`/pool/${POOL_ID}/draft/start`, { method: "POST" });
    if (resp.ok) location.reload();
    else alert("Failed to start auction");
}

function nominateTeam(teamId, teamName) {
    currentTeamId = teamId;
    currentTeamName = teamName;
    document.getElementById("current-team-display").innerHTML =
        `<div class="team-name" style="font-size:24px;">${teamName}</div>`;
    document.getElementById("bid-amount").focus();
    document.getElementById("high-bid").textContent = "No bids yet";
}

async function placeBid() {
    if (!currentTeamId) {
        alert("Select a team first");
        return;
    }

    const amount = parseFloat(document.getElementById("bid-amount").value);
    if (!amount || amount <= 0) {
        alert("Enter a valid bid amount");
        return;
    }

    const resp = await fetch(`/pool/${POOL_ID}/auction/bid`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ nba_team_id: currentTeamId, bid_amount: amount })
    });

    const data = await resp.json();
    if (resp.ok) {
        document.getElementById("high-bid").textContent = `High bid: $${amount}`;
        document.getElementById("bid-amount").value = "";
    } else {
        alert(data.error || "Bid failed");
    }
}

// Supabase Realtime for live bid updates
if (typeof SUPABASE_URL !== "undefined" && SUPABASE_URL) {
    const { createClient } = supabase;
    const sb = createClient(SUPABASE_URL, SUPABASE_KEY);

    sb.channel("auction-" + POOL_ID)
        .on("postgres_changes", {
            event: "INSERT",
            schema: "public",
            table: "auction_bids",
            filter: `pool_id=eq.${POOL_ID}`
        }, (payload) => {
            const bid = payload.new;
            if (bid.nba_team_id === currentTeamId) {
                document.getElementById("high-bid").textContent = `High bid: $${bid.bid_amount}`;
            }
        })
        .subscribe();
}
