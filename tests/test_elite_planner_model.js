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
const market=Object.fromEntries(ids.map((id,i)=>[id,{position:pos[i],club:id,cost:70,status:'a'}]));
market[16]={position:3,club:16,cost:75,status:'a'};
market[17]={position:3,club:17,cost:90,status:'a'};
const f=r.create(ids,{captain:5,vice:6},{bank:10,sales:{5:{value:72}}},market);
f.freeTransfers=0;
assert.equal(r.transfer(f,4,16,market),true);
assert.equal(r.balance(f,market),7);assert.equal(f.captain,16);assert.equal(r.hits(f),4);
assert.equal(r.transfer(f,5,17,market),false); // Unaffordable.
f.chip='wildcard';assert.equal(r.hits(f),0);
f.chip='freehit';assert.equal(r.hits(f),0);
f.chip=null;assert.equal(r.hits(f),4);
assert.equal(r.transfer(f,4,5,market),true); // Revert a draft transfer, not a real repurchase.
assert.equal(r.balance(f,market),10);assert.equal(r.hits(f),0);
Object.assign(f,f.undo.pop());assert.equal(f.captain,16);assert.equal(r.balance(f,market),7);
const unknown=r.create(ids,{}, {},market);assert.equal(r.transfer(unknown,4,16,market),false);
f.bank=0;f.sales[5]=50;assert.equal(r.balance(f,market),-25);
console.log('Finance rules: budgets, unknown funds, sale proceeds, net transfers, undo and WC/FH passed.');
