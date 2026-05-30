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

async function pickTeam(teamRef, teamName, logoUrl) {
    if (!confirm(`Pick ${teamName}?`)) return;
    const resp = await fetch(`/pool/${POOL_ID}/draft/pick`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ team_ref: teamRef })
    });
    const data = await resp.json();
    if (resp.ok) {
        showPickToast(teamName, data.next_message || "Pick saved.");
    } else {
        alert(data.error || "Failed to make pick");
    }
}

function showPickToast(teamName, nextMessage) {
    const toast = document.getElementById("pick-toast");
    if (!toast) { location.reload(); return; }
    document.getElementById("pick-toast-team").textContent = teamName;
    document.getElementById("pick-toast-message").textContent = nextMessage;
    toast.classList.remove("hidden");
    // Safety auto-advance if the picker walks away from their phone.
    setTimeout(function () { location.reload(); }, 20000);
}

function dismissPickToast() {
    location.reload();
}

function appendPick(pickOrder, teamName, logoUrl) {
    const log = document.getElementById("draft-log");
    const entry = document.createElement("div");
    entry.className = "pick-entry";
    // Build with textContent so a manager display name can't inject markup.
    const num = document.createElement("span");
    num.className = "pick-num";
    num.textContent = `#${pickOrder}`;
    const mgr = document.createElement("span");
    mgr.className = "pick-member";
    mgr.textContent = typeof CURRENT_USER_NAME !== "undefined" ? CURRENT_USER_NAME : "";
    const team = document.createElement("span");
    team.className = "pick-team";
    team.textContent = teamName;
    if (logoUrl) {
        const logo = document.createElement("img");
        logo.className = "pick-logo";
        logo.src = logoUrl;
        logo.alt = "";
        logo.onerror = function () { this.style.display = "none"; };
        entry.append(num, mgr, logo, team);
    } else {
        entry.append(num, mgr, team);
    }
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

// --- Pre-draft queue ---
// Add/remove via ★ on team cards or × in the queue panel. Reorder via drag
// or ↑↓ buttons. Toggle reloads so the queue panel re-renders cleanly;
// reorder just shuffles DOM + persists (no reload).
(function () {
    // Log the initial state so a missing VIEWER_MEMBER_ID or INITIAL_QUEUE
    // is obvious in DevTools. Function definitions below are unconditional
    // so an inline onclick="toggleQueue(...)" never hits ReferenceError.
    console.log("[queue] init", {
        VIEWER_MEMBER_ID: typeof VIEWER_MEMBER_ID !== "undefined" ? VIEWER_MEMBER_ID : "(undeclared)",
        INITIAL_QUEUE: typeof INITIAL_QUEUE !== "undefined" ? INITIAL_QUEUE : "(undeclared)",
    });

    function viewerMemberId() {
        return (typeof VIEWER_MEMBER_ID !== "undefined") ? VIEWER_MEMBER_ID : null;
    }

    let currentQueue = (typeof INITIAL_QUEUE !== "undefined" && Array.isArray(INITIAL_QUEUE))
        ? INITIAL_QUEUE.slice() : [];

    async function persistQueue() {
        try {
            const resp = await fetch(`/pool/${POOL_ID}/queue`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ team_refs: currentQueue }),
            });
            if (!resp.ok) {
                const data = await resp.json().catch(() => ({}));
                alert(data.error || "Failed to update queue");
                return false;
            }
            return true;
        } catch (e) {
            alert("Network error updating queue");
            return false;
        }
    }

    function buildQueueRowFromCard(wrap, teamRef) {
        // Build a queue <li> from a team card's DOM, so adding feels instant
        // without a server round-trip + reload to re-render the panel.
        const card = wrap.querySelector(".team-card");
        const logoImg = card ? card.querySelector(".team-card-logo") : null;
        const teamFull = card ? card.querySelector(".team-full") : null;
        const teamSeed = card ? card.querySelector(".team-seed") : null;
        const teamNameText = teamFull ? teamFull.textContent.trim() : teamRef;
        const logoUrl = (logoImg && logoImg.src) ? logoImg.src : "";

        const li = document.createElement("li");
        li.className = "queue-item";
        li.dataset.teamRef = teamRef;
        li.draggable = true;

        const rank = document.createElement("span");
        rank.className = "queue-rank";
        rank.textContent = "?";

        const handle = document.createElement("span");
        handle.className = "queue-handle";
        handle.setAttribute("aria-hidden", "true");
        handle.textContent = "⋮⋮";

        li.append(rank, handle);

        if (logoUrl) {
            const logo = document.createElement("img");
            logo.className = "queue-logo";
            logo.src = logoUrl;
            logo.alt = "";
            logo.onerror = function () { this.style.display = "none"; };
            li.appendChild(logo);
        }

        const nameSpan = document.createElement("span");
        nameSpan.className = "queue-team-name";
        nameSpan.textContent = teamNameText;
        // Treat a single A-Z seed-text as a group letter (WC); skip for numeric seeds.
        const seedText = teamSeed ? teamSeed.textContent.trim() : "";
        if (seedText.length === 1 && /[A-Z]/.test(seedText)) {
            const grp = document.createElement("span");
            grp.className = "queue-group";
            grp.textContent = ` · Grp ${seedText}`;
            nameSpan.appendChild(grp);
        }
        li.appendChild(nameSpan);

        const actions = document.createElement("span");
        actions.className = "queue-actions";

        const up = document.createElement("button");
        up.type = "button"; up.className = "queue-btn";
        up.setAttribute("aria-label", "Move up"); up.textContent = "↑";
        up.onclick = () => moveQueueUp(teamRef);

        const down = document.createElement("button");
        down.type = "button"; down.className = "queue-btn";
        down.setAttribute("aria-label", "Move down"); down.textContent = "↓";
        down.onclick = () => moveQueueDown(teamRef);

        actions.append(up, down);

        if (typeof CAN_PICK !== "undefined" && CAN_PICK) {
            const pick = document.createElement("button");
            pick.type = "button"; pick.className = "queue-btn queue-btn-pick";
            pick.textContent = "Pick";
            pick.onclick = () => pickTeam(teamRef, teamNameText, logoUrl);
            actions.appendChild(pick);
        }

        const remove = document.createElement("button");
        remove.type = "button"; remove.className = "queue-btn queue-btn-remove";
        remove.setAttribute("aria-label", "Remove"); remove.textContent = "×";
        remove.onclick = () => toggleQueue(teamRef);
        actions.appendChild(remove);

        li.appendChild(actions);
        return li;
    }

    function updateQueueEmptyPlaceholder() {
        const list = document.getElementById("queue-list");
        if (!list) return;
        const existing = list.querySelector(".queue-empty");
        const hasItems = list.querySelector(".queue-item") !== null;
        if (hasItems && existing) existing.remove();
        if (!hasItems && !existing) {
            const li = document.createElement("li");
            li.className = "queue-empty";
            li.textContent = "No teams queued yet. Tap ★ on any team below to add it.";
            list.appendChild(li);
        }
    }

    window.toggleQueue = function (teamRef) {
        if (!viewerMemberId()) {
            alert("Can't find your pool membership in this page's state. Reload and try again.");
            return;
        }
        const idx = currentQueue.indexOf(teamRef);
        const adding = idx === -1;
        const list = document.getElementById("queue-list");
        const wrap = document.querySelector(`.team-card-wrap[data-team-ref="${teamRef}"]`);

        // Optimistic local state + DOM update so the click feels instant.
        if (adding) {
            currentQueue.push(teamRef);
        } else {
            currentQueue.splice(idx, 1);
        }
        if (wrap) wrap.classList.toggle("team-card-queued", adding);

        if (list) {
            if (adding) {
                if (wrap) list.appendChild(buildQueueRowFromCard(wrap, teamRef));
            } else {
                const row = list.querySelector(`.queue-item[data-team-ref="${teamRef}"]`);
                if (row) row.remove();
            }
            renumberQueue();
            updateQueueEmptyPlaceholder();
        }

        // Persist in background. On failure, revert via a hard reload so the
        // user sees server-true state instead of a phantom local change.
        persistQueue().then((ok) => { if (!ok) location.reload(); });
    };

    function renumberQueue() {
        const list = document.getElementById("queue-list");
        if (!list) return;
        list.querySelectorAll(".queue-item").forEach((el, i) => {
            const rank = el.querySelector(".queue-rank");
            if (rank) rank.textContent = String(i + 1);
        });
    }

    function reorderQueueDom() {
        const list = document.getElementById("queue-list");
        if (!list) return;
        const byRef = {};
        list.querySelectorAll(".queue-item").forEach((el) => {
            byRef[el.dataset.teamRef] = el;
        });
        currentQueue.forEach((ref) => {
            const el = byRef[ref];
            if (el) list.appendChild(el);
        });
        renumberQueue();
    }

    function moveTo(teamRef, delta) {
        const idx = currentQueue.indexOf(teamRef);
        if (idx === -1) return;
        const newIdx = idx + delta;
        if (newIdx < 0 || newIdx >= currentQueue.length) return;
        const [item] = currentQueue.splice(idx, 1);
        currentQueue.splice(newIdx, 0, item);
        reorderQueueDom();
        persistQueue();
    }

    window.moveQueueUp = (teamRef) => moveTo(teamRef, -1);
    window.moveQueueDown = (teamRef) => moveTo(teamRef, +1);

    const list = document.getElementById("queue-list");
    if (list) {
        let dragged = null;
        list.addEventListener("dragstart", (e) => {
            const item = e.target.closest(".queue-item");
            if (!item || item.classList.contains("queue-item-taken")) return;
            dragged = item;
            item.classList.add("dragging");
            e.dataTransfer.effectAllowed = "move";
        });
        list.addEventListener("dragover", (e) => {
            e.preventDefault();
            if (!dragged) return;
            const target = e.target.closest(".queue-item");
            if (!target || target === dragged) return;
            const rect = target.getBoundingClientRect();
            const after = e.clientY > rect.top + rect.height / 2;
            target.parentNode.insertBefore(dragged, after ? target.nextSibling : target);
        });
        list.addEventListener("dragend", () => {
            if (!dragged) return;
            dragged.classList.remove("dragging");
            dragged = null;
            currentQueue = Array.from(list.querySelectorAll(".queue-item"))
                .map((el) => el.dataset.teamRef);
            renumberQueue();
            persistQueue();
        });
    }
})();

// Supabase Realtime subscription for live updates.
// Verbose logging so a failing subscription is obvious in DevTools.
(function () {
    if (typeof SUPABASE_URL === "undefined" || !SUPABASE_URL) {
        console.warn("[draft-realtime] SUPABASE_URL missing — realtime disabled.");
        return;
    }
    if (typeof supabase === "undefined") {
        console.error("[draft-realtime] supabase-js global not loaded — CDN script tag missing or blocked.");
        return;
    }
    const { createClient } = supabase;
    const sb = createClient(SUPABASE_URL, SUPABASE_KEY);
    const channelName = "draft-" + POOL_ID;
    console.log("[draft-realtime] subscribing to channel", channelName, "for pool", POOL_ID);

    const channel = sb.channel(channelName)
        .on("postgres_changes", {
            event: "INSERT",
            schema: "public",
            table: "draft_picks",
            filter: `pool_id=eq.${POOL_ID}`,
        }, (payload) => {
            console.log("[draft-realtime] INSERT received, reloading:", payload);
            location.reload();
        })
        .subscribe((status, err) => {
            console.log("[draft-realtime] subscription status:", status, err || "");
            if (status === "CHANNEL_ERROR" || status === "TIMED_OUT") {
                console.error("[draft-realtime] subscription failed — check Supabase Realtime is enabled on `draft_picks` and that the anon key can read it.");
            }
        });

    // Expose for manual inspection from the console.
    window.__draftChannel = channel;
    window.__draftSb = sb;
})();
