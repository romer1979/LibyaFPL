/* Public snapshots only; both hypothetical plans stay in browser memory. */
const $ = id => document.getElementById(id);
let data = null, plans = {}, selected = null, requestNumber = 0;
const positions = {1:'حارس',2:'دفاع',3:'وسط',4:'هجوم'};
function text(tag,value,className='') { const e=document.createElement(tag); e.textContent=value; e.className=className; return e; }
function button(label,action) { const b=text('button',label); b.type='button'; b.onclick=action; return b; }

function money(value) { return value===null?'غير معروف':'£'+(value/10).toFixed(1)+'m'; }
function numberField(label,value,step,max,change) {
    const wrapper=text('label',label), input=document.createElement('input');
    input.type='number'; input.min='0'; input.max=String(max); input.step=String(step); input.dir='ltr';
    input.value=value===null?'':String(value); input.placeholder='غير معروف';
    input.onchange=()=>{if(!input.validity.valid)return;change(input.value===''?null:Number(input.value));};
    wrapper.append(input);return wrapper;
}
function renderFinance(side,area,expanded) {
    const plan=plans[side], finance=(side==='my'?data.finances:data.opponent_finances)||{};
    const box=text('div','','finance-box'), balance=PlannerRules.balance(plan,data.players), hits=PlannerRules.hits(plan);
    const total=plan.ids.filter(id=>!plan.original.includes(id)).length;
    box.append(text('strong','الرصيد المتبقي '+money(balance),balance!==null&&balance<0?'budget-error':''));
    const allowed=PlannerRules.allowance(plan);
    const allowedLabel=allowed===Infinity?'غير محدودة':allowed===null?'غير معروفة':allowed;
    box.append(text('strong',`الانتقالات المسموحة: ${allowedLabel}`,'allowance'+(allowed===Infinity?' unlimited':'')));
    box.append(text('p',`${total} انتقال صافٍ · خصم النقاط: ${hits===null?'حدد الانتقالات المجانية':hits}`));
    if(allowed===Infinity)box.append(text('p',`${plan.chip==='wildcard'?'Wildcard':'Free Hit'}: عدد الانتقالات غير محدود ولا خصم على أي منها.`));
    else if(allowed!==null&&total>allowed)box.append(text('p',`تجاوزت الانتقالات المجانية بـ ${total-allowed} — كل واحد إضافي يكلف 4 نقاط.`,'budget-error'));
    if(balance!==null&&balance<0)box.append(text('p','الخطة تتجاوز الميزانية: تراجع عن انتقال أو صحح الافتراضات.','budget-error'));
    if(plan.chip==='freehit')box.append(text('p','Free Hit: انتقالات لهذه الجولة فقط؛ التشكيلة الأصلية لا تتغير.'));
    if(plan.chip==='wildcard')box.append(text('p','Wildcard: انتقالات بلا خصم. هذه الأداة تخطط لجولة واحدة ولا تحفظ جولات مستقبلية.'));
    const detail=document.createElement('details');detail.open=Boolean(expanded);detail.append(text('summary','الميزانية وأسعار البيع · تعديل الافتراضات'));
    detail.append(text('p',`الرصيد المنشور: ${money(finance.bank??null)} · GW ${finance.snapshot_gameweek??data.published_gameweek}. الانتقالات المجانية محسوبة من السجل المنشور ومطابَقة مع خصومات FPL السابقة؛ أسعار البيع تقديرية.`, 'hint'));
    const fields=text('div','','finance-fields');
    fields.append(numberField('رصيد البداية (£m)',plan.bank===null?null:plan.bank/10,0.1,1000,v=>{plan.bank=v===null?null:Math.round(v*10);render();}),
        numberField('انتقالات مجانية',plan.freeTransfers,1,5,v=>{plan.freeTransfers=v;render();}));detail.append(fields);
    detail.append(text('p','المصدر: سجل انتقالات منشور إن توفر؛ وإلا سعر السوق كتقدير. صحح سعر البيع الحقيقي قبل الاعتماد على الميزانية.','hint'));
    const prices=text('div','','sale-prices');
    plan.original.forEach(id=>{
        const source=finance.sales?.[id]?.source==='transfer_history'?'سجل انتقالات':'تقدير سوق';
        prices.append(numberField(`${data.players[id].name} · ${source} · شراء الآن ${money(data.players[id].cost)}`,plan.sales[id]/10,0.1,data.players[id].cost/10,v=>{
            if(v===null)return;plan.sales[id]=Math.round(v*10);render();
        }));
    });detail.append(prices);box.append(detail);
    const sold=plan.original.filter(id=>!plan.ids.includes(id)), bought=plan.ids.filter(id=>!plan.original.includes(id));
    if(total)box.append(text('p','خروج: '+sold.map(id=>data.players[id].name+' '+money(plan.sales[id])).join('، ')),text('p','دخول: '+bought.map(id=>data.players[id].name+' '+money(data.players[id].cost)).join('، ')));
    if(plan.undo.length)box.append(button('تراجع عن آخر انتقال',()=>{Object.assign(plan,plan.undo.pop());selected=null;render();}));
    area.append(box);
}

// One gesture: tapping any player opens his panel, offering both a
// substitution and a transfer whichever end he is at. The only pairing that
// does not exist is starter with starter — order inside the XI carries no
// meaning — and canSwap enforces that, so neither end needs a special case.
//
// This replaces the old two-tap pitch swap. The panel is modal, so the pitch
// behind it is inert and a second tap out there could not land anyway; the
// substitution list does that job, in the same two taps, without having to
// find the right shirt.
function selectPlayer(side,index) {
    if(selected && selected.side===side && selected.index===index) { selected=null; render(); return; }
    selected={side,index}; render(); openEditor();
}
function renderControls(side) {
    const p=plans[side], area=$(side+'-controls'), expanded=area.querySelector('details')?.open; area.replaceChildren();
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
        const status=availability[id]||'unknown', locked=false;
        const b=button(label+' · '+(p.chip===id?'مفعّلة':labels[status])+(locked?' · عرض فقط':''),()=>{
            if(status!=='available'||locked)return;
            p.chip=p.chip===id?null:id; render();
        });
        b.disabled=status!=='available'||locked; b.className='chip'+(p.chip===id?' active':'');
        b.setAttribute('aria-pressed',String(p.chip===id)); chips.append(b);
    }); area.append(chips); renderFinance(side,area,expanded);
    const bar=$(side+'-selection'); bar.replaceChildren();
    if(selected?.side===side) {
        const name=data.players[p.ids[selected.index]].name;
        bar.append(text('span',name+(selected.index>=11?' — بديل':' — أساسي')),
            button('فتح البدائل',openEditor),button('إلغاء',()=>{selected=null;render();}));
    } else bar.append(text('span','اضغط أي لاعب للتبديل أو لتجربة انتقال.'));
}
function playerCard(id,index,side,weights,other) {
    const p=data.players[id], chosen=selected?.side===side && selected.index===index;

    const el=button('',()=>selectPlayer(side,index));
    el.className='player'+(weights[id] !== (other[id]||0)?' unique':'')+(chosen?' selected':'');
    el.setAttribute('aria-pressed',String(chosen)); el.setAttribute('aria-label',`${p.name}، ${positions[p.position]}، ${index<11?'أساسي':'بديل'}`);
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
// Substitution targets for whoever is selected, listed in the panel so a swap
// never depends on hunting the right shirt on the pitch.
//
// Offered from both ends: a starter can be swapped out for a bench player and
// a bench player brought in for a starter. The single forbidden pairing is
// starter with starter, and that is canSwap's job — filtering on it here means
// this list is right for either side without a separate rule.
function subOptions() {
    const box=$('picker-subs'), list=$('sub-options');
    list.replaceChildren();
    box.hidden=!selected;
    if(box.hidden)return;
    const side=selected.side, plan=plans[side];
    plan.ids.forEach((id,index)=>{
        if(!PlannerRules.canSwap(plan,selected.index,index,data.players))return;
        const p=data.players[id];
        // "Bench order" only when both ends are on the bench. Reading it off
        // the target alone labelled a substitution into the XI as a reorder
        // whenever the incoming player happened to be a substitute.
        const reorder=selected.index>=11 && index>=11;
        list.append(button(`${p.name} · ${p.clubName} · ${positions[p.position]}${reorder?' · ترتيب الدكة':''}`,()=>{
            if(!PlannerRules.swap(plan,selected.index,index,data.players))return;
            selected=null;$('editor').close();render();
            $('message').textContent='تم التبديل. انتقلت شارة الكابتن أو النائب إلى اللاعب الداخل إن لزم.';
        }));
    });
    if(!list.children.length)list.append(text('p','لا يوجد تبديل قانوني يحافظ على التشكيلة.','hint'));
}
function transferOptions() {
    $('transfer-options').replaceChildren();if(!data||!selected)return;
    const query=$('search').value.trim().toLocaleLowerCase();
    const club=$('club-filter').value;
    const matches=Object.values(data.players).filter(p=>canTransfer(p.id)
        &&(!club||String(p.club)===club)
        &&`${p.name} ${p.clubName}`.toLocaleLowerCase().includes(query));
    // The list was capped at 40 with no indication, so a common name could
    // silently hide the player you wanted. The cap stays — 200-odd buttons
    // help nobody — but the count now says when it is hiding something.
    const options=matches.slice(0,40);
    $('picker-count').textContent=matches.length>options.length
        ? `عرض ${options.length} من ${matches.length} — حدّد النادي أو ابحث للتضييق`
        : `${matches.length} لاعب متاح`;
    const plan=plans[selected.side];
    options.forEach(p=>{
        const ids=[...plan.ids];ids[selected.index]=p.id;
        const balance=PlannerRules.balance(plan,data.players,ids);
        const btn=button(`${p.name} · ${p.clubName} · ${money(p.cost)} · ${balance===null?'حدد الميزانية أولاً':balance<0?'يتجاوز الميزانية':'المتبقي '+money(balance)}`,()=>{
            if(!canTransfer(p.id)||!PlannerRules.transfer(plan,selected.index,p.id,data.players))return;
            selected=null;$('editor').close();render();
            $('message').textContent='تم الانتقال الافتراضي فقط — لم يتغير فريق FPL.';
        });
        btn.disabled=balance===null||balance<0;$('transfer-options').append(btn);
    });
    if(!options.length)$('transfer-options').append(text('p','لا توجد نتائج متاحة بنفس المركز وحد 3 لاعبين من النادي.'));
}
function openEditor() {
    const player=data.players[plans[selected.side].ids[selected.index]];
    $('edit-title').textContent=`${player.name} · ${positions[player.position]}`;
    $('subs-heading').textContent=selected.index>=11
        ? 'التبديل مع الأساسيين · أو ترتيب الدكة'
        : 'التبديل مع البدلاء';
    $('transfer-heading').textContent=`بدائل في مركز ${positions[player.position]}`;
    $('editor-finance').textContent='الرصيد المتاح: '+money(PlannerRules.balance(plans[selected.side],data.players))+' · عدّل أسعار البيع والميزانية من إعدادات الفريق عند الحاجة.';
    // Club list built from the players actually eligible for this slot, so it
    // never offers a club with nothing to show.
    const clubs=[...new Map(Object.values(data.players).filter(p=>canTransfer(p.id))
        .map(p=>[p.club,p.clubName])).entries()].sort((a,b)=>a[1].localeCompare(b[1],'ar'));
    $('club-filter').replaceChildren(new Option('كل الأندية',''));
    clubs.forEach(([id,name])=>$('club-filter').add(new Option(name,id)));
    $('search').value='';
    subOptions();transferOptions();$('editor').showModal();
}
// A Free Hit squad reverts, so the server steps back to the last standing one.
// That can leave the two sides on different gameweeks, and it can leave either
// behind the latest published week — both worth saying rather than showing a
// single number that is no longer true for anyone.
function publishedLabel(d) {
    const mine=d.settings?.snapshot_gameweek ?? d.published_gameweek;
    const theirs=d.opponent_settings?.snapshot_gameweek ?? d.published_gameweek;
    const latest=d.published_gameweek;
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
        data=result; plans={my: PlannerRules.create(data.squad,data.settings,data.finances,data.players), opponent: PlannerRules.create(data.opponent_squad,data.opponent_settings,data.opponent_finances,data.players)}; $('published').textContent=publishedLabel(data);
        $('my-name').textContent=data.managers.find(m=>String(m.id)===entry).name; $('opponent-name').textContent=data.opponent.name;
        render(); $('matchup').hidden=false; $('message').textContent='خطتك مؤقتة؛ تحديث الصفحة أو تغيير المدير يعيد التشكيلة المنشورة.';
    } catch(error) {if(sequence===requestNumber)$('message').textContent=error.message;}
    finally {if(sequence===requestNumber)$('retry').disabled=false;}
}
$('manager').addEventListener('change',()=>load($('manager').value));
$('retry').addEventListener('click',()=>load($('manager').value));
['my','opponent'].forEach(side=>$(side==='my'?'reset':'opponent-reset').addEventListener('click',()=>{
    plans[side]=PlannerRules.create(side==='my'?data.squad:data.opponent_squad,side==='my'?data.settings:data.opponent_settings,side==='my'?data.finances:data.opponent_finances,data.players);
    selected=null;render();$('message').textContent='تمت استعادة التشكيلة المنشورة وإلغاء الشريحة التجريبية لهذا الفريق.';
}));
$('search').addEventListener('input',transferOptions);
$('club-filter').addEventListener('change',transferOptions);
load();
