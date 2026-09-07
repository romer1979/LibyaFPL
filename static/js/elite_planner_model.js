/* Pure planning rules, shared by the UI and regression tests. */
(function(root) {
    const rules = {
        create(ids, settings = {}) {
            const plan = {ids: [...ids], captain: settings.captain, vice: settings.vice, chip: null};
            rules.normalize(plan); return plan;
        },
        normalize(p) {
            const starters = p.ids.slice(0, 11);
            if (!starters.includes(p.captain)) p.captain = starters[0];
            if (!starters.includes(p.vice) || p.vice === p.captain) p.vice = starters.find(id => id !== p.captain);
        },
        canSwap(p, a, b, players) {
            if (a === b) return false;
            const pa = players[p.ids[a]].position, pb = players[p.ids[b]].position;
            if ((pa === 1 || pb === 1) && pa !== pb) return false;
            const ids = [...p.ids]; [ids[a], ids[b]] = [ids[b], ids[a]];
            const c = [0,0,0,0,0]; ids.slice(0,11).forEach(id => c[players[id].position]++);
            return c[1] === 1 && c[2] >= 3 && c[2] <= 5 && c[3] >= 2 && c[3] <= 5 && c[4] >= 1 && c[4] <= 3;
        },
        swap(p, a, b, players) {
            if (!rules.canSwap(p,a,b,players)) return false;
            const outgoing = a < 11 && b >= 11 ? p.ids[a] : b < 11 && a >= 11 ? p.ids[b] : null;
            const incoming = outgoing === p.ids[a] ? p.ids[b] : p.ids[a];
            [p.ids[a],p.ids[b]] = [p.ids[b],p.ids[a]];
            if (p.captain === outgoing) p.captain = incoming;
            if (p.vice === outgoing) p.vice = incoming;
            rules.normalize(p); return true;
        },
        weights(p) {
            return Object.fromEntries(p.ids.map((id,i) => [id, i >= 11 && p.chip !== 'bboost' ? 0 : id === p.captain ? (p.chip === '3xc' ? 3 : 2) : 1]));
        }
    };
    if (typeof module !== 'undefined' && module.exports) module.exports = rules;
    else root.PlannerRules = rules;
})(globalThis);
