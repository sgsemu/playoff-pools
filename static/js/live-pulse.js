(function () {
    const host = document.querySelector('[data-live-pulse-pool]');
    if (!host) return;
    const poolId = host.dataset.livePulsePool;

    async function update() {
        try {
            const r = await fetch(`/pool/${poolId}/scores/live.json`, { cache: 'no-store' });
            if (!r.ok) return;
            const { live } = await r.json();
            const set = new Set();
            for (const g of live) {
                set.add(`${g.league}:${g.home_abbr}`);
                set.add(`${g.league}:${g.away_abbr}`);
            }
            document.querySelectorAll('.member-team-row[data-abbr]').forEach(row => {
                const key = `${row.dataset.league}:${row.dataset.abbr}`;
                row.classList.toggle('team-live', set.has(key));
            });
        } catch (e) { /* keep last state */ }
    }
    update();
    setInterval(update, 45000);
})();
