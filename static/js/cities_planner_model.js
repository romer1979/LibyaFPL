/* Cities totals count starters once and captains twice, regardless of TC/BB. */
(function(root) {
    const rules = typeof module !== 'undefined' && module.exports ? require('./elite_planner_model.js') : root.PlannerRules;
    const model = {
        weights(plan) {
            return Object.fromEntries(plan.ids.map((id, index) => [id, index >= 11 ? 0 : id === plan.captain ? 2 : 1]));
        },
        aggregate(plans) {
            const total = {};
            plans.forEach(plan => Object.entries(model.weights(plan)).forEach(([id, weight]) => {
                total[id] = (total[id] || 0) + weight;
            }));
            return total;
        },
        hits(plans) {
            const hits = plans.map(plan => rules.used(plan) === 0 ? 0 : rules.hits(plan));
            return hits.some(value => value === null) ? null : hits.reduce((a,b) => a+b, 0);
        },
        differences(teams) {
            const weights = teams.map(model.aggregate);
            return [...new Set([...Object.keys(weights[0]), ...Object.keys(weights[1])])]
                .map(id => ({id: Number(id), own: weights[0][id] || 0, opponent: weights[1][id] || 0,
                    delta: (weights[0][id] || 0) - (weights[1][id] || 0)}))
                .filter(row => row.delta !== 0).sort((a,b) => Math.abs(b.delta)-Math.abs(a.delta) || a.id-b.id);
        },
        scenario(teams, points) {
            const hits = teams.map(model.hits);
            if (hits.includes(null)) return null;
            return model.differences(teams).reduce((gap,row) => gap+row.delta*(points[row.id] ?? 0), hits[1]-hits[0]);
        }
    };
    if (typeof module !== 'undefined' && module.exports) module.exports = model;
    else root.CitiesRules = model;
})(globalThis);
