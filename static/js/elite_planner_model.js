/* Pure planning rules, shared by the UI and regression tests. */
(function(root) {
    const rules = {
        create(ids, settings = {}, finance = {}, players = {}) {
            const plan = {ids: [...ids], original: [...ids], captain: settings.captain, vice: settings.vice, chip: null,
                bank: Number.isInteger(finance.bank) && finance.bank >= 0 ? finance.bank : null,
                freeTransfers: null, sales: {}, undo: []};
            ids.forEach(id => { plan.sales[id] = finance.sales?.[id]?.value ?? players[id]?.cost ?? 0; });
            rules.normalize(plan); return plan;
        },
        balance(p, players, ids = p.ids) {
            if (p.bank === null) return null;
            return p.bank + p.original.filter(id => !ids.includes(id)).reduce((n,id) => n+p.sales[id],0)
                - ids.filter(id => !p.original.includes(id)).reduce((n,id) => n+players[id].cost,0);
        },
        hits(p) {
            if (['wildcard','freehit'].includes(p.chip)) return 0;
            if (p.freeTransfers === null) return null;
            return 4 * Math.max(0, p.ids.filter(id => !p.original.includes(id)).length - p.freeTransfers);
        },
        transfer(p, index, id, players) {
            const incoming = players[id], outgoing = players[p.ids[index]];
            if (!incoming || !outgoing || incoming.position !== outgoing.position || p.ids.includes(id) ||
                ['u','n'].includes(incoming.status) || p.ids.filter((x,i) => i !== index && players[x].club === incoming.club).length >= 3) return false;
            const ids = [...p.ids]; ids[index] = id;
            const balance = rules.balance(p, players, ids);
            if (balance === null || balance < 0) return false;
            p.undo.push({ids:[...p.ids],captain:p.captain,vice:p.vice});
            const old = p.ids[index]; p.ids = ids;
            if (p.captain === old) p.captain = id;
            if (p.vice === old) p.vice = id;
            return true;
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
