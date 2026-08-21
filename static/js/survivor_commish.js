// survivor_commish.js — Commissioner panel for the survivor status board.
//
// Expects globals from the page's inline <script> (see
// _survivor_commish.html): POOL_ID, COMMISH_DATA — {members, teams,
// games_by_week, current_week}. All actions POST to the Task 9
// commissioner endpoints and reload the page on success, matching the
// reload-on-success convention already used by draft.js/auction.js.

function commishShowError(elId, message) {
    const el = document.getElementById(elId);
    if (!el) return;
    el.textContent = message;
    el.hidden = false;
}

function commishClearError(elId) {
    const el = document.getElementById(elId);
    if (!el) return;
    el.hidden = true;
    el.textContent = "";
}

// --- Assign Pick: team dropdown is derived client-side from COMMISH_DATA so
// picking a member/week never needs a round trip -- it's already the whole
// week's games, filtered to teams that member hasn't used yet. ---
function commishUpdateTeamOptions() {
    const memberSel = document.getElementById("commish-assign-member");
    const weekInput = document.getElementById("commish-assign-week");
    const teamSel = document.getElementById("commish-assign-team");
    if (!memberSel || !weekInput || !teamSel) return;
    teamSel.innerHTML = "";

    const member = (COMMISH_DATA.members || []).find((m) => m.member_id === memberSel.value);
    const usedTeams = new Set(member ? member.used_teams : []);
    const games = (COMMISH_DATA.games_by_week || {})[weekInput.value] || [];

    games.forEach((g) => {
        g.team_refs.forEach((ref) => {
            if (usedTeams.has(ref)) return;
            const team = (COMMISH_DATA.teams || {})[ref];
            if (!team) return;
            const opt = document.createElement("option");
            opt.value = JSON.stringify({ team_ref: ref, espn_game_id: g.espn_game_id });
            opt.textContent = team.abbreviation || team.name || ref;
            teamSel.appendChild(opt);
        });
    });

    if (!teamSel.options.length) {
        const opt = document.createElement("option");
        opt.value = "";
        opt.textContent = "No unused teams playing that week";
        teamSel.appendChild(opt);
    }
}

async function commishAssignPick() {
    commishClearError("commish-assign-error");
    const memberSel = document.getElementById("commish-assign-member");
    const weekInput = document.getElementById("commish-assign-week");
    const teamSel = document.getElementById("commish-assign-team");
    const noteInput = document.getElementById("commish-assign-note");
    if (!memberSel.value || !teamSel.value) {
        commishShowError("commish-assign-error", "Pick a member and a team");
        return;
    }
    let choice;
    try {
        choice = JSON.parse(teamSel.value);
    } catch (e) {
        commishShowError("commish-assign-error", "Pick a team");
        return;
    }

    const resp = await fetch(`/pool/${POOL_ID}/survivor/assign-pick`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            member_id: memberSel.value,
            week: parseInt(weekInput.value, 10),
            team_ref: choice.team_ref,
            espn_game_id: choice.espn_game_id,
            note: noteInput.value || null,
        }),
    });
    if (resp.ok) {
        location.reload();
    } else {
        const body = await resp.json().catch(() => ({}));
        commishShowError("commish-assign-error", body.error || "Failed to assign pick");
    }
}

async function commishRecordBuyback() {
    commishClearError("commish-buyback-error");
    const memberSel = document.getElementById("commish-buyback-member");
    const weekInput = document.getElementById("commish-buyback-week");
    const kindSel = document.getElementById("commish-buyback-kind");
    const feeInput = document.getElementById("commish-buyback-fee");

    const payload = {
        member_id: memberSel.value,
        week: parseInt(weekInput.value, 10),
        kind: kindSel.value,
    };
    if (feeInput.value !== "") payload.fee = Number(feeInput.value);

    const resp = await fetch(`/pool/${POOL_ID}/survivor/buyback-for`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
    });
    if (resp.ok) {
        location.reload();
    } else {
        const body = await resp.json().catch(() => ({}));
        commishShowError("commish-buyback-error", body.error || "Failed to record buyback");
    }
}

// --- Eliminate / Reinstate toggle: button label + target status flip based
// on the selected member's CURRENT status (from COMMISH_DATA via the
// option's data-status attribute). ---
function commishSyncStatusButton() {
    const sel = document.getElementById("commish-status-member");
    const btn = document.getElementById("commish-status-btn");
    const weekInput = document.getElementById("commish-status-week");
    if (!sel || !btn) return;
    const opt = sel.options[sel.selectedIndex];
    const status = opt ? opt.dataset.status : "active";
    if (status === "eliminated") {
        btn.textContent = "Reinstate";
        btn.classList.remove("btn-danger");
        btn.classList.add("btn-primary");
        if (weekInput) weekInput.disabled = true;
    } else {
        btn.textContent = "Eliminate";
        btn.classList.remove("btn-primary");
        btn.classList.add("btn-danger");
        if (weekInput) weekInput.disabled = false;
    }
}

async function commishToggleStatus() {
    commishClearError("commish-status-error");
    const sel = document.getElementById("commish-status-member");
    const weekInput = document.getElementById("commish-status-week");
    const opt = sel.options[sel.selectedIndex];
    const currentStatus = opt ? opt.dataset.status : "active";
    const targetStatus = currentStatus === "eliminated" ? "active" : "eliminated";

    const payload = { member_id: sel.value, status: targetStatus };
    if (targetStatus === "eliminated" && weekInput.value) {
        payload.eliminated_week = parseInt(weekInput.value, 10);
    }

    const resp = await fetch(`/pool/${POOL_ID}/survivor/set-status`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
    });
    if (resp.ok) {
        location.reload();
    } else {
        const body = await resp.json().catch(() => ({}));
        commishShowError("commish-status-error", body.error || "Failed to update status");
    }
}

async function commishResolveNow() {
    commishClearError("commish-resolve-error");
    const weekInput = document.getElementById("commish-resolve-week");
    const resp = await fetch(`/pool/${POOL_ID}/survivor/resolve`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ week: parseInt(weekInput.value, 10) }),
    });
    if (resp.ok) {
        location.reload();
    } else {
        const body = await resp.json().catch(() => ({}));
        commishShowError("commish-resolve-error", body.error || "Failed to resolve week");
    }
}

async function commishSettleSeason() {
    commishClearError("commish-settle-error");
    if (!confirm("Settle the season? This marks the pool complete and records the winner(s).")) return;
    const resp = await fetch(`/pool/${POOL_ID}/survivor/settle`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
    });
    if (resp.ok) {
        location.reload();
    } else {
        const body = await resp.json().catch(() => ({}));
        commishShowError("commish-settle-error", body.error || "Failed to settle season");
    }
}

document.addEventListener("DOMContentLoaded", function () {
    commishUpdateTeamOptions();
    commishSyncStatusButton();
});
