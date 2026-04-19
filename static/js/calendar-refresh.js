(function () {
    const host = document.querySelector('[data-live-pulse-pool]');
    if (!host) return;
    const poolId = host.dataset.livePulsePool;

    async function refresh() {
        try {
            const active = document.querySelector('.calendar')?.dataset.activeDate;
            const r = await fetch(`/pool/${poolId}/calendar.partial`, { cache: 'no-store' });
            if (!r.ok) return;
            const html = await r.text();
            const wrapper = document.getElementById('calendar-wrapper');
            if (!wrapper) return;
            wrapper.innerHTML = html;
            // Preserve the user's selected day if it still exists.
            if (active) {
                const tab = wrapper.querySelector(`.day-tab[data-date="${active}"]`);
                if (tab) tab.click();
            }
        } catch (e) { /* keep last render */ }
    }
    setInterval(refresh, 45000);
})();
