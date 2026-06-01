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

// --- Mini-auction (post-snake) ---
// Bid placement, commissioner seeds/deletes, force settle, countdown, realtime.
(function () {
    function fmtError(prefix) {
        return async (resp) => {
            const data = await resp.json().catch(() => ({}));
            alert(data.error || prefix);
        };
    }

    // Convert a datetime-local input ("2026-06-11T16:00") to a UTC ISO string
    // by treating the input as local time. Browsers' Date() parses naive
    // datetime strings as local, .toISOString() emits UTC — exactly the round
    // trip we want when sending to the server.
    function localToUtcIso(value) {
        if (!value) return null;
        const d = new Date(value);
        if (isNaN(d.getTime())) return null;
        return d.toISOString();
    }

    // Reverse of the above: UTC ISO → "YYYY-MM-DDTHH:MM" in local time,
    // for prefilling the datetime-local input from server state.
    function utcIsoToLocalInput(iso) {
        if (!iso) return "";
        const d = new Date(iso);
        if (isNaN(d.getTime())) return "";
        const pad = (n) => String(n).padStart(2, "0");
        return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
    }

    window.startAuction = async function () {
        const closesAtInput = document.getElementById("start-auction-closes-at");
        const incrementInput = document.getElementById("start-auction-increment");
        const errorEl = document.getElementById("start-auction-error");
        if (errorEl) { errorEl.hidden = true; errorEl.textContent = ""; }
        const closes_at = localToUtcIso(closesAtInput && closesAtInput.value);
        const bid_increment = parseInt((incrementInput && incrementInput.value) || "25", 10);
        if (!closes_at) {
            if (errorEl) { errorEl.textContent = "Enter a deadline"; errorEl.hidden = false; }
            return;
        }
        const resp = await fetch(`/pool/${POOL_ID}/auction/start`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ closes_at, bid_increment }),
        });
        if (resp.ok) {
            location.reload();
        } else {
            const data = await resp.json().catch(() => ({}));
            if (errorEl) { errorEl.textContent = data.error || "Failed to start auction"; errorEl.hidden = false; }
        }
    };

    window.placeBid = async function (teamRef) {
        const input = document.getElementById(`bid-input-${teamRef}`);
        if (!input) return;
        const amount = parseInt(input.value, 10);
        if (!amount || amount <= 0) { alert("Enter a valid bid amount"); return; }
        const resp = await fetch(`/pool/${POOL_ID}/auction/bid`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ team_ref: teamRef, amount }),
        });
        if (resp.ok) {
            // Realtime will reload the page when the INSERT lands on this client.
            // Optimistic UI: bump the input's min to current+increment so it
            // doesn't look stuck while waiting for the realtime echo.
            input.value = amount + (parseInt(input.step, 10) || 25);
        } else {
            const data = await resp.json().catch(() => ({}));
            alert(data.error || "Failed to place bid");
        }
    };

    window.seedBid = async function () {
        const member_id = document.getElementById("seed-bid-member").value;
        const team_ref = document.getElementById("seed-bid-team").value;
        const amount = parseInt(document.getElementById("seed-bid-amount").value, 10);
        const errorEl = document.getElementById("seed-bid-error");
        if (errorEl) { errorEl.hidden = true; errorEl.textContent = ""; }
        if (!member_id || !team_ref || !amount) {
            if (errorEl) { errorEl.textContent = "All fields required"; errorEl.hidden = false; }
            return;
        }
        const resp = await fetch(`/pool/${POOL_ID}/auction/seed`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ member_id, team_ref, amount }),
        });
        if (resp.ok) {
            location.reload();
        } else {
            const data = await resp.json().catch(() => ({}));
            if (errorEl) { errorEl.textContent = data.error || "Failed to seed bid"; errorEl.hidden = false; }
        }
    };

    window.deleteBid = async function (bidId) {
        if (!confirm("Delete this bid?")) return;
        const resp = await fetch(`/pool/${POOL_ID}/auction/delete-bid`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ bid_id: bidId }),
        });
        if (resp.ok) {
            location.reload();
        } else {
            await fmtError("Failed to delete bid")(resp);
        }
    };

    window.forceSettle = async function () {
        if (!confirm("Force-settle now? Each team goes to its highest current bidder, pool moves to complete.")) return;
        const resp = await fetch(`/pool/${POOL_ID}/auction/force-settle`, { method: "POST" });
        if (resp.ok) {
            location.reload();
        } else {
            await fmtError("Failed to settle")(resp);
        }
    };

    window.updateClosesAt = async function () {
        const input = document.getElementById("auction-closes-at-input");
        const closes_at = localToUtcIso(input && input.value);
        if (!closes_at) { alert("Enter a valid date/time"); return; }
        const resp = await fetch(`/pool/${POOL_ID}/auction/update-closes-at`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ closes_at }),
        });
        if (resp.ok) {
            location.reload();
        } else {
            await fmtError("Failed to update deadline")(resp);
        }
    };

    // Prefill the commissioner's "edit closes_at" input with the current
    // deadline rendered in the viewer's local timezone.
    document.addEventListener("DOMContentLoaded", () => {
        const editInput = document.getElementById("auction-closes-at-input");
        const countdownEl = document.getElementById("auction-countdown");
        if (editInput && countdownEl && countdownEl.dataset.closesAt) {
            editInput.value = utcIsoToLocalInput(countdownEl.dataset.closesAt);
        }
        // Display the human-readable local closes_at next to "Closes".
        const display = document.getElementById("auction-closes-at-display");
        if (display && countdownEl && countdownEl.dataset.closesAt) {
            const d = new Date(countdownEl.dataset.closesAt);
            if (!isNaN(d.getTime())) {
                display.textContent = d.toLocaleString();
            }
        }
    });

    // Live countdown to the deadline. Updates once per second; switches to
    // "Closed" + locks the bid inputs when the deadline passes.
    function updateCountdown() {
        const countdownEl = document.getElementById("auction-countdown");
        if (!countdownEl) return;
        const closesAtRaw = countdownEl.dataset.closesAt;
        if (!closesAtRaw) return;
        const closesAt = new Date(closesAtRaw).getTime();
        const remainingMs = closesAt - Date.now();
        const textEl = document.getElementById("auction-countdown-text");
        if (!textEl) return;
        if (remainingMs <= 0) {
            textEl.textContent = "Closed";
            document.querySelectorAll(".auction-card-bid-btn").forEach((b) => { b.disabled = true; });
            document.querySelectorAll(".auction-card-bid-input").forEach((i) => { i.disabled = true; });
            return;
        }
        const days = Math.floor(remainingMs / 86400000);
        const hours = Math.floor((remainingMs % 86400000) / 3600000);
        const mins = Math.floor((remainingMs % 3600000) / 60000);
        const secs = Math.floor((remainingMs % 60000) / 1000);
        let text = "";
        if (days > 0) text = `in ${days}d ${hours}h ${mins}m`;
        else if (hours > 0) text = `in ${hours}h ${mins}m ${secs}s`;
        else text = `in ${mins}m ${secs}s`;
        textEl.textContent = text;
    }
    setInterval(updateCountdown, 1000);
    document.addEventListener("DOMContentLoaded", updateCountdown);

    // Realtime subscription for auction_bids on top of the existing
    // draft_picks one. Any INSERT or DELETE triggers a reload so the
    // grid shows fresh high bids + bidder names.
    if (typeof SUPABASE_URL !== "undefined" && SUPABASE_URL && typeof supabase !== "undefined") {
        const { createClient } = supabase;
        const sb = createClient(SUPABASE_URL, SUPABASE_KEY);
        const channel = sb.channel("auction-" + POOL_ID)
            .on("postgres_changes", {
                event: "*",
                schema: "public",
                table: "auction_bids",
                filter: `pool_id=eq.${POOL_ID}`,
            }, (payload) => {
                console.log("[auction-realtime] change:", payload.eventType, payload);
                location.reload();
            })
            .subscribe((status) => {
                console.log("[auction-realtime] subscription status:", status);
            });
        window.__auctionChannel = channel;
    }
})();
