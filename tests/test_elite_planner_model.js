const assert = require('node:assert/strict');
const r = require('../static/js/elite_planner_model.js');
const ids = Array.from({length:15},(_,i)=>i+1);
const pos = [1,2,2,2,3,3,3,3,4,4,4,1,3,2,2];
const players = Object.fromEntries(ids.map((id,i)=>[id,{position:pos[i]}]));
const p = r.create(ids,{captain:5,vice:6});
assert.equal(r.weights(p)[5],2); assert.equal(r.weights(p)[13],0);
p.chip='3xc'; assert.equal(r.weights(p)[5],3);assert.equal(r.weights(p)[6],1);
p.chip='bboost';assert.equal(r.weights(p)[5],2);assert.equal(r.weights(p)[13],1);
assert.equal(r.canSwap(p,1,12,players),false); // Cannot remove the third defender.
assert.equal(r.canSwap(p,0,12,players),false);
assert.equal(r.swap(p,0,11,players),true);
assert.equal(r.swap(p,4,12,players),true);assert.equal(p.captain,13);
assert.equal(r.swap(p,5,13,players),true);assert.equal(p.vice,14);
const other=r.create(ids,{captain:6,vice:5});
other.chip='3xc';const weights=r.weights(other);assert.equal(weights[6],3);assert.equal(weights[5],1);
assert.notDeepEqual(p.ids,other.ids); // Independent lineups.
assert.equal(r.create(ids,{captain:5,vice:5}).vice!==5,true);
console.log('Planner rules: substitutions, formations, C/V, TC, BB and independent drafts passed.');
