/* Public snapshots only; both hypothetical plans stay in browser memory. */
const $ = id => document.getElementById(id);
let data = null, plans = {}, selected = null, requestNumber = 0;
const positions = {1:'حارس',2:'دفاع',3:'وسط',4:'هجوم'};
function text(tag,value,className='') { const e=document.createElement(tag); e.textContent=value; e.className=className; return e; }
function button(label,action) { const b=text('button',label); b.type='button'; b.onclick=action; return b; }
function selectPlayer(side,index) {
    if(selected && selected.side===side) {
        if(selected.index===index) selected=null;
        else if(PlannerRules.swap(plans[side],selected.index,index,data.players)) {
            selected=null; $('message').textContent='تم التبديل. انتقلت شارة الكابتن أو النائب إلى اللاعب الداخل إن لزم.';
        } else { $('message').textContent='تبديل غير مسموح: حافظ على حارس واحد و3 مدافعين و2 وسط ومهاجم على الأقل.'; return; }
    } else selected={side,index};
    render();
}
function renderControls(side) {
    const p=plans[side], area=$(side+'-controls'); area.replaceChildren();
    const leaders=text('div','','leaders');
    [['captain','الكابتن · C'],['vice','نائب الكابتن · V']].forEach(([role,label])=>{
        const wrapper=text('label',label), select=document.createElement('select');
        select.setAttribute('aria-label',label+' '+$(side==='my'?'my-name':'opponent-name').textContent);
        p.ids.slice(0,11).forEach(id=>select.add(new Option(data.players[id].name,id)));
        select.value=p[role]; select.onchange=()=>{
            const other=role==='captain'?'vice':'captain', old=p[role]; p[role]=Number(select.value);
            if(p[other]===p[role])p[other]=old;
            render();
        }; wrapper.append(select); leaders.append(wrapper);
    }); area.append(leaders);
    const chips=text('div','','chip-controls'), availability=(side==='my'?data.chips:data.opponent_chips)||{};
    const labels={available:'متاحة',used:'مستخدمة',unknown:'غير مؤكدة',blocked:'غير متاحة لهذه الجولة'};
    [['3xc','Triple Captain'],['bboost','Bench Boost'],['wildcard','Wildcard'],['freehit','Free Hit']].forEach(([id,label])=>{
        const status=availability[id]||'unknown', locked=['wildcard','freehit'].includes(id);
        const b=button(label+' · '+(p.chip===id?'مفعّلة':labels[status])+(locked?' · عرض فقط':''),()=>{
            if(status!=='available'||locked)return;
            p.chip=p.chip===id?null:id; render();
        });
        b.disabled=status!=='available'||locked; b.className='chip'+(p.chip===id?' active':'');
        b.setAttribute('aria-pressed',String(p.chip===id)); chips.append(b);
    }); area.append(chips);
    const bar=$(side+'-selection'); bar.replaceChildren();
    if(selected?.side===side) {
        bar.append(text('span',data.players[p.ids[selected.index]].name+' — اختر لاعباً مضيئاً للتبديل'),button('تجربة انتقال',openEditor),button('إلغاء',()=>{selected=null;render();}));
    } else bar.append(text('span','اختر لاعباً من الملعب أو الدكة لبدء التبديل.'));
}
function playerCard(id,index,side,weights,other) {
    const p=data.players[id], chosen=selected?.side===side && selected.index===index;
    const target=selected?.side===side && PlannerRules.canSwap(plans[side],selected.index,index,data.players);
    const el=button('',()=>selectPlayer(side,index));
    el.className='player'+(weights[id] !== (other[id]||0)?' unique':'')+(chosen?' selected':'')+(target?' swap-target':'');
    el.setAttribute('aria-pressed',String(chosen)); el.setAttribute('aria-label',`${p.name}، ${positions[p.position]}، ${index<11?'أساسي':'بديل'}${target?'، متاح للتبديل':''}`);
    el.append(text('span',p.clubName,'shirt'),text('strong',p.name),text('small',p.fixtures.join(' · ')||'BLANK'));
    const plan=plans[side];
    if(id===plan.captain||id===plan.vice)el.append(text('span',id===plan.captain?'C':'V','captain-badge'));
    el.append(text('small','×'+weights[id],'weight'));
    if(p.status!=='a'){const warning=text('small','غير جاهز / شكوك','availability');warning.title=p.news;el.append(warning);}
    if(index>=11)el.append(text('small',positions[p.position]+' · '+(index-10)));
    return el;
}
function render() {
    const weights={my:PlannerRules.weights(plans.my),opponent:PlannerRules.weights(plans.opponent)};
    ['my','opponent'].forEach(side=>{
        renderControls(side);
        const other=side==='my'?'opponent':'my', ids=plans[side].ids, pitch=$(side+'-pitch'), bench=$(side+'-bench');
        pitch.replaceChildren();bench.replaceChildren();
        [1,2,3,4].forEach(pos=>{const row=text('div','','position-row');ids.slice(0,11).forEach((id,i)=>{if(data.players[id].position===pos)row.append(playerCard(id,i,side,weights[side],weights[other]));});pitch.append(row);});
        ids.slice(11).forEach((id,i)=>bench.append(playerCard(id,i+11,side,weights[side],weights[other])));
        const diffs=$(side+'-diffs');diffs.replaceChildren();
        ids.filter(id=>weights[side][id]>(weights[other][id]||0)).forEach(id=>{
            diffs.append(text('span',`${data.players[id].name} · +${weights[side][id]-(weights[other][id]||0)}× (${weights[side][id]} مقابل ${weights[other][id]||0}) · ${data.players[id].fixtures.join(' / ')||'BLANK'}`));
        });
        if(!diffs.childNodes.length)diffs.append(text('span','لا توجد أفضلية في المساهمة'));
    });
    const shared=plans.my.ids.filter(id=>weights.my[id]>0 && weights.my[id]===weights.opponent[id]).length;
    $('shared-count').textContent=shared+' لاعبين بنفس المساهمة · المضاعفات وليست نقاطاً متوقعة';
}
function canTransfer(id) {
    if(!selected)return false;
    const p=data.players[id], ids=plans[selected.side].ids;
    return p.position===data.players[ids[selected.index]].position&&!ids.includes(id)&&!['u','n'].includes(p.status)&&ids.filter((x,i)=>i!==selected.index&&data.players[x].club===p.club).length<3;
}
function transferOptions() {
    $('transfer-options').replaceChildren();if(!data||!selected)return;
    const query=$('search').value.trim().toLocaleLowerCase();
    const options=Object.values(data.players).filter(p=>canTransfer(p.id)&&`${p.name} ${p.clubName}`.toLocaleLowerCase().includes(query)).slice(0,40);
    options.forEach(p=>$('transfer-options').append(button(`${p.name} · ${p.clubName} · £${(p.cost/10).toFixed(1)}`,()=>{
        if(!canTransfer(p.id))return;
        const plan=plans[selected.side], old=plan.ids[selected.index];plan.ids[selected.index]=p.id;
        if(plan.captain===old)plan.captain=p.id;if(plan.vice===old)plan.vice=p.id;
        selected=null;$('editor').close();render();
    })));
    if(!options.length)$('transfer-options').append(text('p','لا توجد نتائج متاحة بنفس المركز وحد 3 لاعبين من النادي.'));
}
function openEditor() {
    $('edit-title').textContent=data.players[plans[selected.side].ids[selected.index]].name;
    $('search').value='';transferOptions();$('editor').showModal();
}
// A Free Hit squad reverts, so the server steps back to the last standing one.
// That can leave the two sides on different gameweeks, and it can leave either
// behind the latest published week — both worth saying rather than showing a
// single number that is no longer true for anyone.
function publishedLabel(d) {
    const mine=d.squad_gameweek, theirs=d.opponent_squad_gameweek, latest=d.published_gameweek;
    if(mine===theirs) return 'GW '+mine+(mine===latest?'':' (تم تخطي Free Hit في GW '+latest+')');
    return 'خطتك من GW '+mine+' وخطة خصمك من GW '+theirs+' (تم تخطي Free Hit)';
}
async function load(entry='') {
    const sequence=++requestNumber; data=null; selected=null; $('matchup').hidden=true; $('message').textContent='جاري تحميل البيانات…'; $('retry').disabled=true;
    try {
        const response=await fetch('/api/elite/planner'+(entry?'?entry='+encodeURIComponent(entry):'')); const result=await response.json();
        if(sequence!==requestNumber)return;
        if(!response.ok)throw Error(result.error || 'تعذر تحميل البيانات.');
        const old=entry; $('manager').replaceChildren(new Option('اختر مديرك','')); result.managers.forEach(m=>$('manager').add(new Option(`${m.name} — ${m.team}`,m.id))); $('manager').value=old; $('manager').disabled=false;
        $('gameweek').textContent='GW '+result.gameweek;
        $('deadline').textContent='الموعد النهائي: '+new Date(result.deadline).toLocaleString('en-GB');
        if(!entry){$('message').textContent='اختر مديرك لعرض المواجهة القادمة.';return;}
        data=result; plans={my: PlannerRules.create(data.squad,data.settings), opponent: PlannerRules.create(data.opponent_squad,data.opponent_settings)}; $('published').textContent=publishedLabel(data);
        $('my-name').textContent=data.managers.find(m=>String(m.id)===entry).name; $('opponent-name').textContent=data.opponent.name;
        render(); $('matchup').hidden=false; $('message').textContent='خطتك مؤقتة؛ تحديث الصفحة أو تغيير المدير يعيد التشكيلة المنشورة.';
    } catch(error) {if(sequence===requestNumber)$('message').textContent=error.message;}
    finally {if(sequence===requestNumber)$('retry').disabled=false;}
}
$('manager').addEventListener('change',()=>load($('manager').value));
$('retry').addEventListener('click',()=>load($('manager').value));
['my','opponent'].forEach(side=>$(side==='my'?'reset':'opponent-reset').addEventListener('click',()=>{
    plans[side]=PlannerRules.create(side==='my'?data.squad:data.opponent_squad,side==='my'?data.settings:data.opponent_settings);
    selected=null;render();$('message').textContent='تمت استعادة التشكيلة المنشورة وإلغاء الشريحة التجريبية لهذا الفريق.';
}));
$('search').addEventListener('input',transferOptions);
load();
