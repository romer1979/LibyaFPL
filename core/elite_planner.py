"""Read-only public squad snapshots for the Elite matchup planner."""
from datetime import datetime, timezone
from flask import Blueprint, jsonify, render_template, request
from config import LEAGUE_ID, FPL_BASE_URL, EXCLUDED_PLAYERS
from core.fpl_api import fetch_data, get_bootstrap_data, get_fixtures, get_entry_picks, FPLApiError

planner = Blueprint('elite_planner', __name__)


def event_window(events, now=None):
    now = now or datetime.now(timezone.utc)
    ordered = sorted(events, key=lambda e: e['id'])
    future = [e for e in ordered if datetime.fromisoformat(e['deadline_time'].replace('Z', '+00:00')) > now]
    past = [e for e in ordered if datetime.fromisoformat(e['deadline_time'].replace('Z', '+00:00')) <= now]
    return (future[0] if future else None), (past[-1] if past else None)


def members():
    result, page = [], 1
    while True:
        data = fetch_data(f'{FPL_BASE_URL}/leagues-h2h/{LEAGUE_ID}/standings/?page_standings={page}')['standings']
        result.extend({'id': x['entry'], 'name': x['player_name'], 'team': x.get('entry_name', '')}
                      for x in data['results'] if x.get('entry') and x['player_name'] not in EXCLUDED_PLAYERS)
        if not data.get('has_next'):
            return result
        page += 1


def opponent_for(entry, gw):
    matches, page = [], 1
    while True:
        data = fetch_data(f'{FPL_BASE_URL}/leagues-h2h-matches/league/{LEAGUE_ID}/?event={gw}&page={page}')
        matches.extend(m for m in data['results'] if entry in (m.get('entry_1_entry'), m.get('entry_2_entry')))
        if not data.get('has_next', data.get('next')):
            break
        page += 1
    if not matches:
        raise ValueError('لم يُنشر خصمك لهذه الجولة بعد، أو لا توجد لك مواجهة.')
    if len(matches) != 1:
        raise ValueError('توجد أكثر من مواجهة لهذه الجولة؛ لا يمكن اختيار خصم واحد تلقائياً.')
    m = matches[0]
    side = '2' if m.get('entry_1_entry') == entry else '1'
    opponent = m.get(f'entry_{side}_entry')
    if not opponent or opponent <= 0:
        raise ValueError('هذه الجولة دون خصم فردي متاح للمقارنة.')
    return {'id': opponent, 'name': m.get(f'entry_{side}_player_name') or m.get(f'entry_{side}_name') or str(opponent)}


FREE_HIT = 'freehit'


def squad(entry, gw, players, earliest=1):
    """The latest published squad at or before `gw` that is not a Free Hit.

    A Free Hit squad expires at the next deadline and the manager reverts to
    the team they had before it, so planning from one would show fifteen
    players they will not own.

    The walk back is a loop rather than a single step because the chip sets
    split at GW20: Free Hit can be played in GW19 and again in GW20, and one
    step would land on the second one.

    Wildcard is deliberately NOT skipped — that squad is permanent.
    """
    for source in range(gw, earliest - 1, -1):
        data = get_entry_picks(entry, source)
        if data.get('active_chip') == FREE_HIT:
            continue
        gw = source
        break
    else:
        raise ValueError('تعذر استعادة التشكيلة الأصلية قبل Free Hit.')
    picks = sorted(data.get('picks', []), key=lambda p: p['position'])
    ids = [p['element'] for p in picks]
    if len(ids) != 15 or len(set(ids)) != 15 or any(i not in players for i in ids):
        raise ValueError('التشكيلة المنشورة غير مكتملة. أعد المحاولة بعد نشر بيانات الجولة.')
    return {'ids': ids,
            'snapshot_gameweek': gw,
            'bank': data.get('entry_history', {}).get('bank'),
            'captain': next((p['element'] for p in picks[:11] if p.get('is_captain')), ids[0]),
            'vice': next((p['element'] for p in picks[:11] if p.get('is_vice_captain')), ids[1])}


def finances(entry, snapshot, players):
    """Best-effort estimates in integer tenths; no authenticated endpoints."""
    latest = {}
    try:
        chips = fetch_data(f'{FPL_BASE_URL}/entry/{entry}/history/')['chips']
        temporary = {c['event'] for c in chips if c['name'] == 'freehit'}
        transfers = fetch_data(f'{FPL_BASE_URL}/entry/{entry}/transfers/')
        for t in sorted(transfers, key=lambda t: (t['event'], t['time'])):
            if t['event'] <= snapshot['snapshot_gameweek'] and t['event'] not in temporary:
                latest.pop(t['element_out'], None)
                latest[t['element_in']] = t['element_in_cost']
    except (FPLApiError, KeyError, TypeError, ValueError):
        latest = {}
    sales = {}
    for pid in snapshot['ids']:
        current = players[pid]['cost']
        purchase = latest.get(pid)
        if not isinstance(purchase, int) or purchase <= 0:
            purchase = None
        sales[pid] = {
            'value': current if purchase is None else min(current, purchase + max(0, current - purchase) // 2),
            'source': 'market_estimate' if purchase is None else 'transfer_history',
            'purchase': purchase,
        }
    return {'bank': snapshot['bank'], 'sales': sales,
            'snapshot_gameweek': snapshot['snapshot_gameweek']}


def chip_availability(entry, gw):
    """Infer remaining chips from public history, never from last week's multiplier.

    2025/26 and 2026/27 have separate GW1-19 and GW20-38 chip sets.
    Unpublished activations cannot be observed through the public API.
    """
    names = ('3xc', 'bboost', 'wildcard', 'freehit')
    try:
        history = fetch_data(f'{FPL_BASE_URL}/entry/{entry}/history/')['chips']
        if not isinstance(history, list):
            raise TypeError('Invalid chip history')
        start = 1 if gw <= 19 else 20
        used = {c['name'] for c in history if start <= c['event'] <= gw}
        return {name: ('used' if name in used else
                       'blocked' if name == 'freehit' and (gw == 1 or any(
                           c['name'] == name and c['event'] == gw - 1 for c in history))
                       else 'available') for name in names}
    except (FPLApiError, KeyError, TypeError, ValueError):
        return {name: 'unknown' for name in names}


@planner.route('/league/elite/planner')
def page():
    return render_template('elite_planner.html')


@planner.route('/api/elite/planner')
def api():
    try:
        bootstrap = get_bootstrap_data()
        upcoming, published = event_window(bootstrap['events'])
        if not upcoming:
            return jsonify(error='لا توجد جولة قادمة في الموسم الحالي.'), 409
        managers = members()
        base = {'gameweek': upcoming['id'], 'deadline': upcoming['deadline_time'], 'managers': managers}
        raw = request.args.get('entry')
        if raw is None:
            return jsonify(base)
        if not raw.isdecimal() or not any(m['id'] == int(raw) for m in managers):
            return jsonify(error='اختر مديراً من دوري النخبة.'), 400
        if not published:
            return jsonify(error='لم تُنشر أي تشكيلات بعد. تتاح المقارنة بعد أول موعد نهائي للموسم.'), 409
        entry = int(raw)
        opponent = opponent_for(entry, upcoming['id'])
        teams = {t['id']: t['short_name'] for t in bootstrap['teams']}
        fixtures = {t: [] for t in teams}
        for f in get_fixtures(upcoming['id']):
            fixtures[f['team_h']].append(teams[f['team_a']] + ' (H)')
            fixtures[f['team_a']].append(teams[f['team_h']] + ' (A)')
        players = {p['id']: {'id': p['id'], 'name': p['web_name'], 'position': p['element_type'],
                   'club': p['team'], 'clubName': teams[p['team']], 'cost': p['now_cost'],
                   'status': p['status'], 'news': p.get('news', ''), 'fixtures': fixtures[p['team']]}
                   for p in bootstrap['elements']}
        own = squad(entry, published['id'], players)
        other = squad(opponent['id'], published['id'], players)
        own_finance = finances(entry, own, players)
        other_finance = finances(opponent['id'], other, players)
        return jsonify(**base, published_gameweek=published['id'], opponent=opponent,
                       squad=own.pop('ids'), opponent_squad=other.pop('ids'), players=players,
                       settings=own, opponent_settings=other,
                       finances=own_finance, opponent_finances=other_finance,
                       chips=chip_availability(entry, upcoming['id']),
                       opponent_chips=chip_availability(opponent['id'], upcoming['id']))
    except ValueError as exc:
        return jsonify(error=str(exc)), 409
    except (FPLApiError, KeyError, TypeError):
        return jsonify(error='تعذر تحميل بيانات FPL الآن. حاول مرة أخرى.'), 502
