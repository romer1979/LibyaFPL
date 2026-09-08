/* Pure planning rules, shared by the UI and regression tests. */
(function(root) {
    const rules = {
        create(ids, settings = {}, finance = {}, players = {}) {
            const plan = {ids: [...ids], original: [...ids], captain: settings.captain, vice: settings.vice, chip: null,
                bank: Number.isInteger(finance.bank) && finance.bank >= 0 ? finance.bank : null,
                // Derived from the public history by the server, which returns
                // null rather than a guess when it cannot verify itself. Still
                // editable: it is a starting point, not a claim.
                freeTransfers: Number.isInteger(finance.free_transfers) && finance.free_transfers >= 0
                    ? finance.free_transfers : null,
                sales: {}, undo: []};
            ids.forEach(id => { plan.sales[id] = finance.sales?.[id]?.value ?? players[id]?.cost ?? 0; });
            rules.normalize(plan); return plan;
        },
        balance(p, players, ids = p.ids) {
            if (p.bank === null) return null;
            return p.bank + p.original.filter(id => !ids.includes(id)).reduce((n,id) => n+p.sales[id],0)
                - ids.filter(id => !p.original.includes(id)).reduce((n,id) => n+players[id].cost,0);
        },
        // How many transfers this plan may make without a points hit.
        // Infinity under Wildcard or Free Hit, which lift the limit entirely;
        // null when the free-transfer count could not be established.
        allowance(p) {
            if (['wildcard','freehit'].includes(p.chip)) return Infinity;
            return p.freeTransfers === null ? null : p.freeTransfers;
        },
        used(p) {
            return p.ids.filter(id => !p.original.includes(id)).length;
        },
        hits(p) {
            const allowed = rules.allowance(p);
            if (allowed === null) return null;
            if (allowed === Infinity) return 0;
            return 4 * Math.max(0, rules.used(p) - allowed);
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
            // Every substitution involves the bench. Two starters swapping
            // changes nothing — order inside the XI does not affect scoring,
            // only bench order does (it sets auto-sub priority) — and allowing
            // it was what let a substitution be started from the pitch.
            if (a < 11 && b < 11) return false;
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
        scenario(own, opponent, points) {
            const hit = p => rules.used(p) === 0 ? 0 : rules.hits(p);
            const a = hit(own), b = hit(opponent);
            if (a === null || b === null) return null;
            const mine = rules.weights(own), theirs = rules.weights(opponent);
            return [...new Set([...own.ids, ...opponent.ids])].reduce((gap, id) =>
                gap + ((mine[id] || 0) - (theirs[id] || 0)) * (points[id] ?? 0), b - a);
        },
        weights(p) {
            return Object.fromEntries(p.ids.map((id,i) => [id, i >= 11 && p.chip !== 'bboost' ? 0 : id === p.captain ? (p.chip === '3xc' ? 3 : 2) : 1]));
        }
    };
    if (typeof module !== 'undefined' && module.exports) module.exports = rules;
    else root.PlannerRules = rules;
})(globalThis);
