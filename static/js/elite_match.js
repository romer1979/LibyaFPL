const $ = id => document.getElementById(id);
const labels = {minutes:'نقاط المشاركة', goals_scored:'أهداف', assists:'تمريرات حاسمة', clean_sheets:'شباك نظيفة',
    goals_conceded:'أهداف مستقبلة', own_goals:'أهداف عكسية', penalties_saved:'صد ركلات جزاء', penalties_missed:'ركلات جزاء ضائعة',
    yellow_cards:'بطاقات صفراء', red_cards:'بطاقات حمراء', saves:'تصديات', bonus:'بونص', defensive_contribution:'مساهمات دفاعية',
    defensive_contributions:'مساهمات دفاعية', adjustment:'تصحيح نقاط FPL'};
let data=null, busy=false, timer;
function text(tag,value,cls=''){const el=document.createElement(tag);el.textContent=value;el.className=cls;return el;}
function signed(n){return n>0?'+'+n:String(n);}
function timeLabel(value){return new Date(value).toLocaleString('en-GB');}
function detail(player){
    $('player-title').textContent=player.name;$('player-points').replaceChildren();
    $('player-points').append(text('p',`${player.minutes} دقيقة · ${player.raw} نقطة × ${player.weight} = ${player.points}`));
    Object.values(player.parts).filter(part=>part.points!==0).forEach(part=>{
        $('player-points').append(text('div',`${labels[part.identifier]||part.identifier.replaceAll('_',' ')}: ${signed(part.points)} · ${part.fixture_label||'تحديث المباراة'}${part.provisional?' · تقديري':''}`,'match-detail-row'));
    });$('player-detail').showModal();
}
function card(player,other,ownWeight=player.weight){
    const b=text('button','','player'+(ownWeight!==(other?.weight||0)&&player.weight>0?' unique':'')+(player.fixture_live?' fixture-live':''));b.type='button';b.onclick=()=>detail(player);
    b.setAttribute('aria-label',`${player.name}: ${player.points} نقطة محتسبة${player.fixture_live?' · مباراته جارية':''}`);
    b.append(text('span',player.clubName,'shirt'),text('strong',player.name),text('small',`${player.raw} × ${player.weight} = ${player.points}`,'match-player-total'),text('small',player.minutes+' دقيقة'));
    if(player.captain||player.vice)b.append(text('span',player.captain?'C':'V','captain-badge'));
    if(player.fixture_live){const badge=text('span','LIVE','live-badge');badge.lang='en';badge.title='مباراته جارية؛ لا يؤكد وجوده على أرض الملعب';b.append(badge);}
    return b;
}
function render(){
    const expanded = new Set([...$('pitches').querySelectorAll('details[open]')].map(el=>el.dataset.entry));
    const firstRender = !$('pitches').childElementCount;
    $('content').hidden=false;$('scoreboard').replaceChildren();$('pitches').replaceChildren();
    data.teams.forEach((team,i)=>{
        const title=text('div','');title.append(text('h2',team.name),text('strong',team.score),text('p','خصم انتقالات: '+team.hits));
        if(i===1){const gap=text('div','','gap');gap.append(text('p','GW '+data.gameweek),text('b',data.gap===0?'تعادل':Math.abs(data.gap)+' نقطة لصالح '+data.teams[data.gap>0?0:1].name),text('p',data.settled?'نتيجة معتمدة':'قراءة مباشرة · بونص تقديري'));$('scoreboard').append(gap);}
        $('scoreboard').append(title);
        const group=text('section','','city-team');
        const other=Object.fromEntries(data.teams[1-i].players.map(p=>[p.id,p]));
        const own=Object.fromEntries(team.players.map(p=>[p.id,p]));
        if(team.managers)group.append(text('h2',team.name,'city-title'));
        (team.managers||[team]).forEach((manager,index)=>{
            const panel=text('section','','team-panel'), pitch=text('div','','pitch'), bench=text('div','','bench');
            let chip=manager.chip?({'3xc':'Triple Captain',bboost:'Bench Boost',freehit:'Free Hit',wildcard:'Wildcard'}[manager.chip]||manager.chip):'دون شريحة';
            if(team.managers&&['3xc','bboost'].includes(manager.chip))chip+=' · الزيادة غير محتسبة';
            panel.append(text('h3',manager.name),text('p',chip,'hint'));
            const makeCard=p=>card(p,other[p.id],own[p.id]?.weight||0);
            [1,2,3,4].forEach(pos=>{const row=text('div','','position-row');manager.players.filter(p=>p.active&&p.position===pos).forEach(p=>row.append(makeCard(p)));pitch.append(row);});
            manager.players.filter(p=>!p.active).forEach(p=>bench.append(makeCard(p)));
            panel.append(pitch,text('p',team.managers?'الدكة · نقاطها غير محتسبة إلا عند التبديل التلقائي':'الدكة · الأرقام المحتسبة تشمل تأثير الشريحة والتبديلات','bench-title'),bench);
            manager.substitutions.forEach(sub=>panel.append(text('p',`تبديل تلقائي: ${manager.players.find(p=>p.id===sub.element_out)?.name} ← ${manager.players.find(p=>p.id===sub.element_in)?.name}`,'hint')));
            if(team.managers){
                const fold=text('details','','manager-card');fold.dataset.entry=String(manager.entry);
                fold.open=expanded.has(String(manager.entry))||(firstRender&&index===0);
                const heading=text('summary','','manager-overview');
                heading.append(text('strong',manager.name),text('span',`${manager.score} نقطة · خصم ${manager.hits}`));
                fold.append(heading,panel);group.append(fold);
            }else $('pitches').append(panel);
        });
        if(team.managers)$('pitches').append(group);
    });
    $('coverage').textContent=`بدأ الرصد ${timeLabel(data.tracking_since)} عند النتيجة ${data.baseline.join(' – ')}. لا تُنسب النقاط السابقة إلى أحداث مؤرخة. آخر ${data.history_limit} دفعة كحد أقصى؛ التغييرات ضمن كل دفعة قد تكون متزامنة.`;
    $('timeline').replaceChildren();
    if(!data.updates.length)$('timeline').append(text('p','لم يُرصد تغيير مؤثر منذ بداية التتبع.'));
    data.updates.forEach(batch=>{
        const box=text('article','','match-update');
        box.append(text('p',`${timeLabel(batch.detected_at)} · النتيجة بعد التحديث ${batch.scores.join(' – ')} · الفارق ${signed(batch.gap_before)} ← ${signed(batch.gap_after)} (من منظور ${data.teams[0].name})`));
        if(batch.coverage_gap)box.append(text('p','فجوة في الرصد: هذه التغييرات مجمّعة منذ '+timeLabel(batch.previous_at)+'.','coverage-gap'));
        batch.events.forEach(event=>{
            const row=text('div','','match-event');row.append(text('h3',event.player));
            if(event.parts.length)row.append(text('p',event.parts.map(p=>`${p.label} ${signed(p.delta)}${p.provisional?' (تقديري / تصحيح بونص)':''} · ${p.fixture_label||'تحديث المباراة'}`).join('، ')));
            else if(!event.lineup_change)row.append(text('p','تحديث نقاط / خصم انتقالات'));
            if(event.lineup_change)row.append(text('p',event.lineup_reasons?.join('، ')||'تغيّر اللاعب المحتسب أو مضاعف الكابتن / النائب.'));
            row.append(text('small',`${data.teams[0].name}: ${signed(event.delta[0])} · ${data.teams[1].name}: ${signed(event.delta[1])}`));
            (event.manager_effects||[]).forEach(effect=>row.append(text('small',`${data.teams[effect.side].name} · ${effect.manager}: ${signed(effect.delta)}`)));
            row.append(text('p',`أثر التحديث: ${Math.abs(event.gap_delta)} نقطة في صالح ${data.teams[event.gap_delta>0?0:1].name}`,'effect'));box.append(row);
        });$('timeline').append(box);
    });
}
async function refresh(){
    if(busy)return;busy=true;clearTimeout(timer);$('refresh').disabled=true;
    try{
        const response=await fetch(document.body.dataset.api), result=await response.json();
        if(!response.ok)throw Error(result.error||'تعذر تحديث المواجهة.');
        data=result;render();$('status').textContent='آخر رصد ناجح: '+timeLabel(data.observed_at);
    }catch(error){$('status').textContent=error.message+(data?' · المعروض آخر قراءة ناجحة: '+timeLabel(data.observed_at):'');}
    finally{busy=false;$('refresh').disabled=false;timer=setTimeout(()=>document.hidden?schedule():refresh(),60000);}
}
function schedule(){clearTimeout(timer);timer=setTimeout(()=>document.hidden?schedule():refresh(),60000);}
$('refresh').onclick=refresh;
document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh();});
refresh();
