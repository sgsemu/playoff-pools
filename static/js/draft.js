// draft.js — Live draft real-time client using Supabase Realtime

async function assignPick() {
    const memberId = document.getElementById("assign-member").value;
    const teamRef = document.getElementById("assign-team").value;
    const errorEl = document.getElementById("assign-error");
    if (errorEl) { errorEl.hidden = true; errorEl.textContent = ""; }
    const resp = await fetch(`/pool/${POOL_ID}/draft/assign`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ member_id: memberId, team_ref: teamRef }),
    });
    if (resp.ok) {
        location.reload();
    } else {
        const data = await resp.json().catch(() => ({}));
        if (errorEl) { errorEl.textContent = data.error || "Failed to assign"; errorEl.hidden = false; }
    }
}

async function removePick(pickId) {
    if (!confirm("Remove this team from the member's roster?")) return;
    const resp = await fetch(`/pool/${POOL_ID}/draft/remove-pick`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pick_id: pickId }),
    });
    if (resp.ok) {
        location.reload();
    } else {
        const data = await resp.json().catch(() => ({}));
        alert(data.error || "Failed to remove pick");
    }
}

async function finalizeDraft() {
    if (!confirm("Finalize the draft? No more picks, assigns, or removals after this.")) return;
    const resp = await fetch(`/pool/${POOL_ID}/draft/finalize`, { method: "POST" });
    if (resp.ok) {
        location.reload();
    } else {
        const data = await resp.json().catch(() => ({}));
        alert(data.error || "Failed to finalize");
    }
}

async function undoLastPick() {
    if (!confirm("Undo the most recent pick? The member whose pick is undone will be up again.")) return;
    const resp = await fetch(`/pool/${POOL_ID}/draft/undo`, { method: "POST" });
    if (resp.ok) {
        location.reload();
    } else {
        const data = await resp.json().catch(() => ({}));
        alert(data.error || "Failed to undo pick");
    }
}

async function startDraft() {
    const resp = await fetch(`/pool/${POOL_ID}/draft/start`, { method: "POST" });
    if (resp.ok) {
        location.reload();
    } else {
        const data = await resp.json();
        alert(data.error || "Failed to start draft");
    }
}

async function pickTeam(teamRef, teamName) {
    if (!confirm(`Pick ${teamName}?`)) return;
    const resp = await fetch(`/pool/${POOL_ID}/draft/pick`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ team_ref: teamRef })
    });
    const data = await resp.json();
    if (resp.ok) {
        const btn = document.querySelector(`[data-team-ref="${teamRef}"]`);
        if (btn) btn.remove();
        appendPick(data.pick_order, teamName);
    } else {
        alert(data.error || "Failed to make pick");
    }
}

function appendPick(pickOrder, teamName) {
    const log = document.getElementById("draft-log");
    const entry = document.createElement("div");
    entry.className = "pick-entry";
    entry.innerHTML = `<span class="pick-num">#${pickOrder}</span> <span class="pick-team">${teamName}</span>`;
    log.prepend(entry);
}

// --- Manual draft order (pre-start, creator only) ---
(function initDraftOrder() {
    const list = document.getElementById("draft-order-list");
    if (!list || list.dataset.editable !== "true") return;

    const errorEl = document.getElementById("draft-order-error");
    let dragged = null;
    let snapshot = null;

    function currentIds() {
        return Array.from(list.querySelectorAll(".draft-order-item"))
            .map((el) => el.dataset.memberId);
    }

    function renumber() {
        list.querySelectorAll(".draft-order-item").forEach((el, i) => {
            const num = el.querySelector(".draft-order-num");
            if (num) num.textContent = String(i + 1);
        });
    }

    function showError(msg) {
        if (!errorEl) return;
        errorEl.textContent = msg;
        errorEl.hidden = false;
    }

    function clearError() {
        if (!errorEl) return;
        errorEl.textContent = "";
        errorEl.hidden = true;
    }

    function restoreSnapshot() {
        if (!snapshot) return;
        snapshot.forEach((el) => list.appendChild(el));
        renumber();
    }

    async function persist(memberIds) {
        try {
            const resp = await fetch(`/pool/${POOL_ID}/draft/order`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ member_ids: memberIds }),
            });
            if (!resp.ok) {
                const data = await resp.json().catch(() => ({}));
                showError(data.error || "Failed to save draft order");
                restoreSnapshot();
                return;
            }
            clearError();
        } catch (e) {
            showError("Network error saving draft order");
            restoreSnapshot();
        }
    }

    list.addEventListener("dragstart", (e) => {
        const item = e.target.closest(".draft-order-item");
        if (!item) return;
        dragged = item;
        snapshot = Array.from(list.querySelectorAll(".draft-order-item"));
        item.classList.add("dragging");
        e.dataTransfer.effectAllowed = "move";
    });

    list.addEventListener("dragover", (e) => {
        e.preventDefault();
        if (!dragged) return;
        const target = e.target.closest(".draft-order-item");
        if (!target || target === dragged) return;
        const rect = target.getBoundingClientRect();
        const after = e.clientY > rect.top + rect.height / 2;
        target.parentNode.insertBefore(dragged, after ? target.nextSibling : target);
    });

    list.addEventListener("dragend", () => {
        if (!dragged) return;
        dragged.classList.remove("dragging");
        dragged = null;
        renumber();
        persist(currentIds());
    });
})();

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
