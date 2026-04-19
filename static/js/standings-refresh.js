(function () {
    const host = document.querySelector('[data-live-pulse-pool]');
    if (!host) return;
    const poolId = host.dataset.livePulsePool;

    function expandedMemberIds() {
        return Array.from(document.querySelectorAll('.teams-detail'))
            .filter(r => r.style.display && r.style.display !== 'none')
            .map(r => r.dataset.memberId)
            .filter(Boolean);
    }

    async function refresh() {
        try {
            const prev = new Set(expandedMemberIds());
            const r = await fetch(`/pool/${poolId}/standings.partial`, { cache: 'no-store' });
            if (!r.ok) return;
            const html = await r.text();
            const wrapper = document.getElementById('standings-wrapper');
            if (!wrapper) return;
            wrapper.innerHTML = html;
            prev.forEach(mid => {
                const row = wrapper.querySelector(`.teams-detail[data-member-id="${mid}"]`);
                if (!row) return;
                row.style.display = 'table-row';
                const prevRow = row.previousElementSibling;
                const arrow = prevRow && prevRow.querySelector('.expand-icon');
                if (arrow) arrow.textContent = '▾';
            });
        } catch (e) { /* keep last render */ }
    }
    setInterval(refresh, 60000);
})();
