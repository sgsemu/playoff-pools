// survivor.js — Weekly pick screen: optimistic save + stale-while-revalidate.
//
// Expects globals from the page's inline <script> (see _survivor_pick.html):
//   POOL_ID, CURRENT_WEEK, INITIAL_PICK_DATA (server-rendered snapshot, same
//   shape as GET /pool/<pool_id>/survivor/pick.json).

function spickCacheKey() {
    return `survivor:${POOL_ID}:${CURRENT_WEEK}`;
}

function spickReadCache() {
    try {
        const raw = localStorage.getItem(spickCacheKey());
        return raw ? JSON.parse(raw) : null;
    } catch (e) {
        return null;
    }
}

function spickWriteCache(data) {
    try {
        localStorage.setItem(spickCacheKey(), JSON.stringify(data));
    } catch (e) { /* localStorage unavailable (private mode, quota) -- skip caching */ }
}

function spickTeamButton(teamRef) {
    return document.querySelector(`.spick-team[data-team-ref="${teamRef}"]`);
}

function spickFmtSpread(point) {
    if (point === null || point === undefined) return "";
    const n = Number(point);
    const sign = n > 0 ? "+" : "";
    return ` (${sign}${n.toFixed(1)})`;
}

// Re-paints the board (selection/used/locked state + the savebar) from a
// board-data object -- used both to paint instantly from the localStorage
// cache on load and to reconcile once the fresh server copy lands.
function spickRenderBoard(data) {
    if (!data || !Array.isArray(data.games)) return;

    const lockbar = document.getElementById("spick-lockbar");
    if (lockbar) lockbar.dataset.weekLockAt = JSON.stringify(data.week_lock_at || null);

    data.games.forEach((g) => {
        ["home", "away"].forEach((side) => {
            const team = g[side];
            if (!team) return;
            const el = spickTeamButton(team.team_ref);
            if (!el) return;
            const isUsed = team.used_week !== null && team.used_week !== undefined;
            el.classList.toggle("spick-sel", !!team.selected);
            el.classList.toggle("spick-used", isUsed);
            el.classList.toggle("spick-locked", !!data.locked && !isUsed);
            el.disabled = isUsed || !!data.locked;
        });
    });

    const statusEl = document.getElementById("spick-save-status");
    if (!statusEl) return;
    if (data.current_pick) {
        statusEl.className = "spick-saved";
        statusEl.innerHTML = `✓ Current pick: <b>${data.current_pick.nickname}</b>${spickFmtSpread(data.current_pick.spread)} — saved`;
    } else {
        statusEl.className = "";
        statusEl.textContent = "No pick yet this week";
    }

    if (data.locked && lockbar) {
        const countdownEl = document.getElementById("spick-countdown");
        if (countdownEl) countdownEl.textContent = "Locked";
    }
}

// Mutates a cached board-data object in place to reflect a just-confirmed
// pick (single-select across the whole week), then writes it back.
function spickUpdateCacheAfterPick(teamRef) {
    const data = spickReadCache() || (typeof INITIAL_PICK_DATA !== "undefined" ? INITIAL_PICK_DATA : null);
    if (!data || !Array.isArray(data.games)) return;
    let newCurrent = null;
    data.games.forEach((g) => {
        ["home", "away"].forEach((side) => {
            const team = g[side];
            if (!team) return;
            team.selected = team.team_ref === teamRef;
            if (team.selected) newCurrent = team;
        });
    });
    data.current_pick = newCurrent;
    spickWriteCache(data);
}

function spickShowError(message) {
    const savebar = document.getElementById("spick-savebar");
    if (!savebar) return;
    const err = document.createElement("div");
    err.className = "spick-error";
    err.textContent = message;
    savebar.appendChild(err);
    setTimeout(() => err.remove(), 4000);
}

// Optimistic pick: mark selected + "unsaved" immediately, POST in the
// background, flip to "saved" on 200 or revert (selection + savebar text)
// on 409/400/network error.
async function pickTeam(teamRef, espnGameId, week) {
    const el = spickTeamButton(teamRef);
    if (!el || el.disabled) return;

    const statusEl = document.getElementById("spick-save-status");
    const prevSelected = document.querySelector(".spick-team.spick-sel");
    const prevSelectedRef = prevSelected ? prevSelected.dataset.teamRef : null;
    const prevStatusHtml = statusEl ? statusEl.innerHTML : "";
    const prevStatusClass = statusEl ? statusEl.className : "";

    document.querySelectorAll(".spick-team.spick-sel").forEach((t) => t.classList.remove("spick-sel"));
    el.classList.add("spick-sel");
    if (statusEl) {
        const nameEl = el.querySelector(".spick-tname");
        const spreadEl = el.querySelector(".spick-spread");
        statusEl.className = "spick-unsaved";
        statusEl.innerHTML = `Current pick: <b>${nameEl ? nameEl.textContent.trim() : ""}</b>${spreadEl ? " (" + spreadEl.textContent.trim() + ")" : ""} — unsaved`;
    }

    const revert = (message) => {
        el.classList.remove("spick-sel");
        if (prevSelectedRef) {
            const prevEl = spickTeamButton(prevSelectedRef);
            if (prevEl) prevEl.classList.add("spick-sel");
        }
        if (statusEl) {
            statusEl.className = prevStatusClass;
            statusEl.innerHTML = prevStatusHtml;
        }
        spickShowError(message);
    };

    try {
        const resp = await fetch(`/pool/${POOL_ID}/survivor/pick`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ week: week, team_ref: teamRef, espn_game_id: espnGameId }),
        });
        const body = await resp.json().catch(() => ({}));
        if (resp.ok) {
            if (statusEl) {
                const nameEl = el.querySelector(".spick-tname");
                const spreadEl = el.querySelector(".spick-spread");
                statusEl.className = "spick-saved";
                statusEl.innerHTML = `✓ Current pick: <b>${nameEl ? nameEl.textContent.trim() : ""}</b>${spreadEl ? " (" + spreadEl.textContent.trim() + ")" : ""} — saved`;
            }
            // Reflect the pick in the Player Results grid immediately, on the
            // viewer's own row (always visible to themselves) — so the top pick
            // section and the grid below stay in sync without a reload.
            const ownCell = document.getElementById("sb-own-w" + week);
            if (ownCell) {
                ownCell.className = "sb-cell sb-pending";
                ownCell.title = "Your pick";
                ownCell.textContent = el.dataset.abbr || "";
            }
            spickUpdateCacheAfterPick(teamRef);
        } else {
            revert(body.error || "Failed to save pick");
        }
    } catch (e) {
        revert("Network error saving pick");
    }
}
window.pickTeam = pickTeam;

// --- Lock countdown ---
function spickUpdateCountdown() {
    const lockbar = document.getElementById("spick-lockbar");
    const countdownEl = document.getElementById("spick-countdown");
    if (!lockbar || !countdownEl) return;
    let lockAtRaw = null;
    try { lockAtRaw = JSON.parse(lockbar.dataset.weekLockAt || "null"); } catch (e) { /* ignore */ }
    if (!lockAtRaw) return;
    const lockAt = new Date(lockAtRaw).getTime();
    const remaining = lockAt - Date.now();
    if (remaining <= 0) {
        countdownEl.textContent = "Locked";
        document.querySelectorAll(".spick-team").forEach((el) => {
            if (!el.classList.contains("spick-used")) {
                el.classList.add("spick-locked");
                el.disabled = true;
            }
        });
        return;
    }
    const days = Math.floor(remaining / 86400000);
    const hours = Math.floor((remaining % 86400000) / 3600000);
    const mins = Math.floor((remaining % 3600000) / 60000);
    let left;
    if (days > 0) left = `${days}d ${hours}h left`;
    else if (hours > 0) left = `${hours}h ${mins}m left`;
    else left = `${mins}m left`;
    countdownEl.textContent = `Locks Sun 1:00 PM ET · ${left}`;
}

// --- Stale-while-revalidate ---
// Paint instantly from whatever's in localStorage (survives reloads/bfcache
// and reflects picks made in another tab), then fetch the canonical state
// from the server and reconcile + refresh the cache.
(function () {
    if (typeof CURRENT_WEEK === "undefined" || CURRENT_WEEK === null) return;

    const cached = spickReadCache();
    if (cached) {
        spickRenderBoard(cached);
    } else if (typeof INITIAL_PICK_DATA !== "undefined" && INITIAL_PICK_DATA) {
        spickWriteCache(INITIAL_PICK_DATA);
    }

    fetch(`/pool/${POOL_ID}/survivor/pick.json`, { cache: "no-store" })
        .then((r) => (r.ok ? r.json() : null))
        .then((fresh) => {
            if (!fresh) return;
            spickWriteCache(fresh);
            spickRenderBoard(fresh);
        })
        .catch(() => { /* keep last render */ });

    setInterval(spickUpdateCountdown, 30000);
    document.addEventListener("DOMContentLoaded", spickUpdateCountdown);
    spickUpdateCountdown();
})();
