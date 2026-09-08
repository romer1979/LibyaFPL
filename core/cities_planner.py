"""Read-only next-gameweek planning for the Cities three-manager competition."""
from concurrent.futures import ThreadPoolExecutor

from flask import Blueprint, jsonify, render_template, request

from config import FPL_BASE_URL
from core.cities_league import CITIES_H2H_LEAGUE_ID, TEAMS_FPL_IDS
from core import libyan_league, arab_league
from core.elite_planner import event_window, squad, finances, free_transfers, chip_availability
from core.fpl_api import fetch_data, get_bootstrap_data, get_fixtures, FPLApiError

cities_planner = Blueprint('cities_planner', __name__)


def league_config(league):
    return {
        'cities': (TEAMS_FPL_IDS, CITIES_H2H_LEAGUE_ID, 'دوري المدن', 'CITIES LEAGUE'),
        'libyan': (libyan_league.TEAMS_FPL_IDS, libyan_league.LIBYAN_H2H_LEAGUE_ID, 'الدوري الليبي', 'LIBYAN LEAGUE'),
        'arab': (arab_league.TEAMS_FPL_IDS, arab_league.ARAB_H2H_LEAGUE_ID, 'البطولة العربية', 'ARAB CHAMPIONSHIP'),
    }[league]


def city_opponent(city, gw, league='cities'):
    roster, league_id, _, _ = league_config(league)
    lookup = {}
    for name, entries in roster.items():
        if len(entries) != 3 or len(set(entries)) != 3:
            raise ValueError('قائمة الفريق يجب أن تحتوي على 3 مديرين مختلفين.')
        for entry in entries:
            if entry in lookup:
                raise ValueError('يوجد مدير مسجل في أكثر من فريق.')
            lookup[entry] = name
    matches, page = [], 1
    while True:
        data = fetch_data(f'{FPL_BASE_URL}/leagues-h2h-matches/league/{league_id}/?event={gw}&page={page}')
        for match in data['results']:
            if match.get('event', gw) != gw:
                continue
            first, second = lookup.get(match.get('entry_1_entry')), lookup.get(match.get('entry_2_entry'))
            if city in (first, second):
                matches.append(second if first == city else first)
        if not data.get('has_next', data.get('next')):
            break
        page += 1
    if len(matches) != 1:
        raise ValueError('لا توجد مواجهة واحدة منشورة لهذا الفريق في الجولة القادمة.')
    if matches[0] is None or matches[0] == city:
        raise ValueError('الخصم غير متاح أو ممثل الفريق غير مربوط بالقائمة الصحيحة.')
    return matches[0]


def manager_snapshot(entry, published, upcoming, players):
    settings = squad(entry, published, players)
    settings['free_transfers'] = free_transfers(entry, upcoming)
    finance = finances(entry, settings, players)
    chips = chip_availability(entry, upcoming)
    try:
        profile = fetch_data(f'{FPL_BASE_URL}/entry/{entry}/')
        name = ' '.join(filter(None, [profile.get('player_first_name'), profile.get('player_last_name')]))
    except (FPLApiError, TypeError, AttributeError):
        name = ''
    return {'id': entry, 'name': name or f'FPL {entry}', 'squad': settings.pop('ids'),
            'settings': settings, 'finances': finance,
            'chips': {key: chips[key] for key in ('wildcard', 'freehit')}}


@cities_planner.route('/league/cities/planner', defaults={'league': 'cities'})
@cities_planner.route('/league/libyan/planner', defaults={'league': 'libyan'})
@cities_planner.route('/league/arab/planner', defaults={'league': 'arab'})
def page(league):
    _, _, title, subtitle = league_config(league)
    return render_template('cities_planner.html', league=league, league_title=title, league_subtitle=subtitle)


@cities_planner.route('/api/cities/planner', defaults={'league': 'cities'})
@cities_planner.route('/api/libyan/planner', defaults={'league': 'libyan'})
@cities_planner.route('/api/arab/planner', defaults={'league': 'arab'})
def api(league):
    try:
        roster, _, _, _ = league_config(league)
        bootstrap = get_bootstrap_data()
        upcoming, published = event_window(bootstrap['events'])
        if not upcoming:
            return jsonify(error='لا توجد جولة قادمة في الموسم الحالي.'), 409
        base = {'cities': list(roster), 'gameweek': upcoming['id'], 'deadline': upcoming['deadline_time']}
        city = request.args.get('city')
        if city is None:
            return jsonify(base)
        if city not in roster:
            return jsonify(error='اختر فريقاً مسجلاً في الدوري.'), 400
        if not published:
            return jsonify(error='تتاح المقارنة بعد نشر تشكيلات الجولة الأولى.'), 409
        opponent = city_opponent(city, upcoming['id'], league)
        clubs = {t['id']: t['short_name'] for t in bootstrap['teams']}
        fixtures = {t: [] for t in clubs}
        for fixture in get_fixtures(upcoming['id']):
            home, away = fixture['team_h'], fixture['team_a']
            fixtures[home].append(clubs[away] + ' (H)')
            fixtures[away].append(clubs[home] + ' (A)')
        players = {p['id']: {'id': p['id'], 'name': p['web_name'], 'position': p['element_type'],
                   'club': p['team'], 'clubName': clubs[p['team']], 'cost': p['now_cost'],
                   'status': p['status'], 'news': p.get('news', ''), 'fixtures': fixtures[p['team']]}
                   for p in bootstrap['elements']}
        entries = roster[city] + roster[opponent]
        # Fetch only the six managers in this fixture, never the whole league.
        with ThreadPoolExecutor(max_workers=6) as pool:
            snapshots = list(pool.map(lambda entry: manager_snapshot(entry, published['id'], upcoming['id'], players), entries))
        return jsonify(**base, published_gameweek=published['id'], players=players,
                       teams=[{'name': city, 'managers': snapshots[:3]}, {'name': opponent, 'managers': snapshots[3:]}])
    except ValueError as exc:
        return jsonify(error=str(exc)), 409
    except (FPLApiError, KeyError, TypeError):
        return jsonify(error='تعذر تحميل التشكيلات الست كاملة. أعد المحاولة قبل المقارنة.'), 502
