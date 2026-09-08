/* Six independent drafts; only their Cities scoring contributions are combined. */
const $ = id => document.getElementById(id);
const positions = {1:'حارس',2:'دفاع',3:'وسط',4:'هجوم'};
let data = null, plans = [], selected = null, sequence = 0, assumptions = {};
const expanded = new Set(), financeExpanded = new Set();
function text(tag, value, className='') {
    const el=document.createElement(tag); el.textContent=value; el.className=className; return el;
}
function button(label, action) {const el=text('button',label);el.type='button';el.onclick=action;return el;}
function money(value) {return value===null?'غير معروف':'£'+(value/10).toFixed(1)+'m';}
function draft(team, index) {
    const m=data.teams[team].managers[index];
    return PlannerRules.create(m.squad,m.settings,m.finances,data.players);
}
function inputField(label,value,min,max,step,onchange) {
    const wrap=text('label',label), input=document.createElement('input');
    input.type='number';input.min=min;input.max=max;input.step=step;input.dir='ltr';
    input.value=value===null?'':String(value);input.placeholder='غير معروف';
    input.onchange=()=>{
        if(!input.validity.valid)return;
        onchange(input.value===''?null:Number(input.value));
    };
    wrap.append(input);return wrap;
}
function finance(team,index) {
    const plan=plans[team][index], m=data.teams[team].managers[index], key=m.id;
    const box=text('div','','finance-box'), detail=document.createElement('details');
    detail.open=financeExpanded.has(key);
    detail.ontoggle=()=>{if(!detail.isConnected)return;detail.open?financeExpanded.add(key):financeExpanded.delete(key);};
    detail.append(text('summary','الميزانية وأسعار البيع'));
    detail.append(text('p',`رصيد منشور من GW ${m.settings.snapshot_gameweek}: ${money(m.finances.bank)}. أسعار البيع تقديرية وقابلة للتصحيح.`, 'hint'));
    const fields=text('div','','finance-fields');
    fields.append(inputField('رصيد البداية (£m)',plan.bank===null?null:plan.bank/10,0,1000,.1,v=>{plan.bank=v===null?null:Math.round(v*10);render();}),
        inputField('انتقالات مجانية',plan.freeTransfers,0,5,1,v=>{plan.freeTransfers=v;render();}));
    detail.append(fields);
    const prices=text('div','','sale-prices');
    plan.original.forEach(id=>{
        const source=m.finances.sales[id]?.source==='transfer_history'?'سجل انتقالات':'تقدير سوق';
        prices.append(inputField(data.players[id].name+' · '+source,plan.sales[id]/10,0,data.players[id].cost/10,.1,v=>{
            if(v===null)return;plan.sales[id]=Math.round(v*10);render();
        }));
    });detail.append(prices);box.append(detail);
    const balance=PlannerRules.balance(plan,data.players);
    if(balance!==null&&balance<0)box.append(text('p','الميزانية سالبة: صحح الافتراضات أو تراجع عن انتقال.','budget-error'));
    if(plan.chip)box.append(text('p',plan.chip==='freehit'?'Free Hit: خطة لهذه الجولة فقط. تبقى التشكيلة الأصلية محفوظة لإعادة الضبط.':'Wildcard: انتقالات بلا خصم؛ لا يمتد هذا المخطط إلى جولات مستقبلية.','hint'));
    if(PlannerRules.used(plan)) {
        box.append(text('p','خروج: '+plan.original.filter(id=>!plan.ids.includes(id)).map(id=>data.players[id].name).join('، ')),
            text('p','دخول: '+plan.ids.filter(id=>!plan.original.includes(id)).map(id=>data.players[id].name).join('، ')));
    }
    return box;
}
function playerCard(team,index,id,slot,totals) {
    const player=data.players[id], plan=plans[team][index];
    const el=button('',()=>openEditor(team,index,slot));
    const weight=CitiesRules.weights(plan)[id];
    el.className='player'+(weight>0&&(totals[team][id]||0)!==(totals[1-team][id]||0)?' unique':'');
    el.setAttribute('aria-label',`${player.name}، ${positions[player.position]}، ${slot<11?'أساسي':'بديل'}`);
    el.append(text('span',player.clubName,'shirt'),text('strong',player.name),text('small',player.fixtures.join(' · ')||'BLANK'));
    if(id===plan.captain||id===plan.vice)el.append(text('span',id===plan.captain?'C':'V','captain-badge'));
    if(player.status!=='a'){const warning=text('small','شكوك / غير جاهز','availability');warning.title=player.news;el.append(warning);}
    el.append(text('small','×'+weight));
    if(slot>=11)el.append(text('small','بديل '+(slot-10)));
    return el;
}
function managerCard(team,index,totals) {
    const manager=data.teams[team].managers[index], plan=plans[team][index];
    const card=document.createElement('details');card.className='manager-card';card.dataset.manager=manager.id;
    card.open=expanded.has(manager.id);
    card.ontoggle=()=>{if(!card.isConnected)return;card.open?expanded.add(manager.id):expanded.delete(manager.id);};
    const summary=document.createElement('summary'), overview=text('div','','manager-overview');
    const allowance=PlannerRules.allowance(plan), used=PlannerRules.used(plan), hit=used===0?0:PlannerRules.hits(plan);
    overview.append(text('strong',manager.name),text('small',`الكابتن: ${data.players[plan.captain].name} · الرصيد ${money(PlannerRules.balance(plan,data.players))}`),
        text('small',`GW ${manager.settings.snapshot_gameweek} · ${used} انتقال · مجاني: ${allowance===Infinity?'غير محدود':allowance??'غير معروف'} · خصم ${hit??'غير معروف'}`));
    summary.append(overview);card.append(summary);
    const body=text('div','','manager-body'), leaders=text('div','','leaders');
    [['captain','الكابتن · C'],['vice','النائب · V']].forEach(([role,label])=>{
        const wrapper=text('label',label), select=document.createElement('select');
        select.setAttribute('aria-label',label+' '+manager.name);
        plan.ids.slice(0,11).forEach(id=>select.add(new Option(data.players[id].name,id)));
        select.value=plan[role];select.onchange=()=>{
            const old=plan[role], other=role==='captain'?'vice':'captain';plan[role]=Number(select.value);
            if(plan[other]===plan[role])plan[other]=old;render();
        };wrapper.append(select);leaders.append(wrapper);
    });body.append(leaders);
    const chips=text('div','','chip-controls');
    [['wildcard','Wildcard'],['freehit','Free Hit']].forEach(([chip,label])=>{
        const status=manager.chips[chip], labels={available:'متاحة',used:'مستخدمة',unknown:'غير مؤكدة',blocked:'غير متاحة لهذه الجولة'};
        const b=button(label+' · '+(plan.chip===chip?'مفعّلة':labels[status]||labels.unknown),()=>{
            if(status!=='available')return;plan.chip=plan.chip===chip?null:chip;render();
        });b.disabled=status!=='available';b.className='chip'+(plan.chip===chip?' active':'');b.setAttribute('aria-pressed',String(plan.chip===chip));chips.append(b);
    });body.append(chips,finance(team,index));
    const actions=text('div','','manager-actions');
    actions.append(button('إعادة ضبط المدير',()=>{plans[team][index]=draft(team,index);render();}));
    if(plan.undo.length)actions.append(button('تراجع عن آخر انتقال',()=>{Object.assign(plan,plan.undo.pop());render();}));
    body.append(actions,text('p','اضغط أي لاعب لفتح التبديلات والانتقالات.','hint'));
    const pitch=text('div','','pitch'), bench=text('div','','bench');
    [1,2,3,4].forEach(pos=>{
        const row=text('div','','position-row');plan.ids.slice(0,11).forEach((id,slot)=>{if(data.players[id].position===pos)row.append(playerCard(team,index,id,slot,totals));});pitch.append(row);
    });plan.ids.slice(11).forEach((id,i)=>bench.append(playerCard(team,index,id,i+11,totals)));
    body.append(pitch,text('p','دكة البدلاء · لا تُحسب إلا عند التبديل','bench-title'),bench);card.append(body);return card;
}
function render() {
    const totals=plans.map(CitiesRules.aggregate);$('city-teams').replaceChildren();
    data.teams.forEach((team,t)=>{
        const section=text('section','','city-team'), heading=text('div','','city-title'), title=text('div','');
        title.append(text('small',t===0?'فريقك · خطة مشتركة':'الخصم · خطة مفترضة'),text('h2',team.name));
        heading.append(title,button('إعادة ضبط الفريق',()=>{plans[t]=team.managers.map((_,i)=>draft(t,i));render();}));
        section.append(heading);team.managers.forEach((_,i)=>section.append(managerCard(t,i,totals)));$('city-teams').append(section);
    });
    $('own-column').textContent=data.teams[0].name;$('other-column').textContent=data.teams[1].name;
    $('shared').textContent=Object.keys(totals[0]).filter(id=>totals[0][id]>0&&totals[0][id]===totals[1][id]).length+' لاعبين بنفس المساهمة';
    $('hit-summary').replaceChildren();data.teams.forEach((team,i)=>$('hit-summary').append(text('span',team.name+' · خصم انتقالات الفريق: '+(CitiesRules.hits(plans[i])??'غير معروف — راجع المديرين'))));
    const diffs=CitiesRules.differences(plans);$('comparison-rows').replaceChildren();$('scenario-fields').replaceChildren();
    diffs.forEach(row=>{
        const player=data.players[row.id], tr=document.createElement('tr'), name=text('td',player.name);
        name.append(text('small',player.fixtures.join(' / ')||'BLANK'));
        tr.append(name,text('td','×'+row.own),text('td','×'+row.opponent),text('td',(row.delta>0?'+':'')+row.delta+'×',row.delta>0?'positive':'negative'));$('comparison-rows').append(tr);
        const field=inputField(`${player.name} (${row.delta>0?'+':''}${row.delta}×)`,assumptions[row.id]??null,-20,100,1,v=>{assumptions[row.id]=v??0;renderScenario();});
        field.querySelector('input').oninput=field.querySelector('input').onchange;
        $('scenario-fields').append(field);
    });
    if(!diffs.length){const row=document.createElement('tr'), cell=text('td','المساهمات متطابقة؛ قد يحسم خصم الانتقالات الفارق.');cell.colSpan=4;row.append(cell);$('comparison-rows').append(row);}
    renderScenario();
}
function renderScenario() {
    const gap=CitiesRules.scenario(plans,assumptions);
    const overBudget=plans.some(team=>team.some(plan=>{const bank=PlannerRules.balance(plan,data.players);return bank!==null&&bank<0;}));
    $('scenario-result').textContent=gap===null?'أكمل عدد الانتقالات المجانية للمديرين الذين أجريت لهم انتقالات لحساب الفارق.':
        (overBudget?'خطة تتجاوز الميزانية · ':'')+'حسب افتراضاتك: '+(gap===0?'تعادل':Math.abs(gap)+' نقطة لصالح '+data.teams[gap>0?0:1].name);
}
function candidates() {
    const plan=plans[selected.team][selected.manager], outgoing=data.players[plan.ids[selected.slot]];
    return Object.values(data.players).filter(p=>p.position===outgoing.position&&!plan.ids.includes(p.id)&&!['u','n'].includes(p.status)&&
        plan.ids.filter((id,i)=>i!==selected.slot&&data.players[id].club===p.club).length<3);
}
function transferOptions() {
    if(!selected)return;
    const query=$('search').value.trim().toLowerCase(), club=$('club-filter').value;
    const matches=candidates().filter(p=>(!club||String(p.club)===club)&&`${p.name} ${p.clubName}`.toLowerCase().includes(query));
    $('picker-count').textContent=`عرض ${Math.min(40,matches.length)} من ${matches.length} — استخدم البحث أو النادي للتصفية`;
    $('transfer-options').replaceChildren();
    matches.slice(0,40).forEach(player=>{
        const {team,manager,slot}=selected, plan=plans[team][manager], ids=[...plan.ids];ids[slot]=player.id;
        const balance=PlannerRules.balance(plan,data.players,ids), before=CitiesRules.hits(plans[team]);
        const hypothetical={...plan,ids}, updated=plans[team].map((p,i)=>i===manager?hypothetical:p), after=CitiesRules.hits(updated);
        const hitChange=before===null||after===null?'خصم الفريق غير معروف':'تغيّر خصم الفريق '+(after-before>0?'+':'')+(after-before);
        const b=button(`${player.name} · ${player.clubName} · ${money(player.cost)} · ${balance===null?'أدخل الميزانية':balance<0?'يتجاوز الميزانية':'المتبقي '+money(balance)} · ${hitChange}`,()=>{
            const old=data.players[plan.ids[slot]].name;
            if(!PlannerRules.transfer(plan,slot,player.id,data.players))return;
            selected=null;$('editor').close();render();
            $('message').textContent=`${data.teams[team].name}: ${old} ← ${player.name}. تم تحديث مساهمات الفريق وخصم الانتقالات.`;
        });b.disabled=balance===null||balance<0;$('transfer-options').append(b);
    });
    if(!matches.length)$('transfer-options').append(text('p','لا توجد نتائج متاحة.'));
}
function openEditor(team,manager,slot) {
    selected={team,manager,slot};const plan=plans[team][manager], player=data.players[plan.ids[slot]];
    $('edit-title').textContent=player.name+' · '+data.teams[team].managers[manager].name;
    $('edit-budget').textContent='الرصيد المتاح '+money(PlannerRules.balance(plan,data.players));
    $('sub-options').replaceChildren();plan.ids.forEach((id,index)=>{
        if(!PlannerRules.canSwap(plan,slot,index,data.players))return;
        $('sub-options').append(button(data.players[id].name+' · '+(index<11?'أساسي':'بديل'),()=>{
            if(!PlannerRules.swap(plan,slot,index,data.players))return;
            selected=null;$('editor').close();render();$('message').textContent='تم التبديل وتحديث المقارنة. انتقلت شارة الكابتن أو النائب إلى الداخل إن لزم.';
        }));
    });
    $('club-filter').replaceChildren(new Option('كل الأندية',''));
    [...new Map(candidates().map(p=>[p.club,p.clubName]))].sort((a,b)=>a[1].localeCompare(b[1])).forEach(([id,name])=>$('club-filter').add(new Option(name,id)));
    $('search').value='';transferOptions();$('editor').showModal();
}
async function load(city='') {
    const current=++sequence;data=null;selected=null;$('matchup').hidden=true;$('refresh').disabled=true;
    $('message').textContent=city?'جاري تحميل فريقين و6 تشكيلات…':'جاري تحميل الجولة القادمة…';
    try {
        const response=await fetch('/api/cities/planner'+(city?'?city='+encodeURIComponent(city):'')), result=await response.json();
        if(current!==sequence)return;
        if(!response.ok)throw Error(result.error||'تعذر تحميل البيانات.');
        $('city').replaceChildren(new Option('اختر فريقك',''));result.cities.forEach(name=>$('city').add(new Option(name,name)));$('city').value=city;$('city').disabled=false;
        $('gameweek').textContent='GW '+result.gameweek;$('deadline').textContent='الموعد النهائي: '+new Date(result.deadline).toLocaleString('en-GB');
        if(!city){$('message').textContent='اختر الفريق لعرض مواجهته القادمة.';return;}
        data=result;plans=result.teams.map((team,t)=>team.managers.map((_,i)=>draft(t,i)));assumptions={};expanded.clear();financeExpanded.clear();
        render();$('matchup').hidden=false;$('message').textContent='افتح أي مدير لتعديل خطته. تحديث البيانات أو تغيير الفريق يمسح الخطط المؤقتة.';
    }catch(error){if(current===sequence)$('message').textContent=error.message;}
    finally {if(current===sequence)$('refresh').disabled=false;}
}
$('city').onchange=()=>load($('city').value);
$('refresh').onclick=()=>load($('city').value);
$('search').oninput=transferOptions;$('club-filter').onchange=transferOptions;
$('clear-scenario').onclick=()=>{assumptions={};render();};
load();
