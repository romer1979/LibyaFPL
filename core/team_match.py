"""Three-manager match centres using each competition's actual representatives."""
from datetime import datetime
from functools import lru_cache
from hashlib import sha256

from flask import Blueprint, jsonify, render_template, request, url_for
from sqlalchemy.exc import SQLAlchemyError

from config import FPL_BASE_URL
from models import db
from core.cities_planner import league_config
from core.elite_match import (MatchObservation, MatchUpdate, context, league_matches, record, stamp, log)
from core.elite_match_scoring import lineup
from core.fpl_api import get_bootstrap_data, get_entry_picks, fetch_data, FPLApiError

team_match = Blueprint('team_match', __name__)
LEAGUES = ('cities', 'libyan', 'arab')


def fixtures_for(league, gw):
    roster, league_id, _, _ = league_config(league)
    lookup = {}
    for name, entries in roster.items():
        if len(entries) != 3 or len(set(entries)) != 3:
            raise ValueError('قائمة الفريق يجب أن تحتوي على 3 مديرين مختلفين.')
        for entry in entries:
            if entry in lookup:
                raise ValueError('يوجد مدير مسجل في أكثر من فريق.')
            lookup[entry] = name
    result = []
    for match in league_matches(gw, league_id):
        first, second = lookup.get(match.get('entry_1_entry')), lookup.get(match.get('entry_2_entry'))
        if not first or not second or first == second:
            continue
        result.append(dict(match, team_names=[first, second], manager_ids=[roster[first], roster[second]]))
    return result


def match_key(league, match, season, gw):
    # A roster replacement must start a new baseline, not masquerade as a score event.
    roster = ':'.join(str(entry) for group in match['manager_ids'] for entry in group)
    signature = sha256(roster.encode()).hexdigest()[:12]
    return f"team:{league}:{league_config(league)[1]}:{season}:{gw}:{match['entry_1_entry']}:{match['entry_2_entry']}:{signature}"


@lru_cache(maxsize=1024)
def manager_name(entry, season):
    profile = fetch_data(f'{FPL_BASE_URL}/entry/{entry}/')
    return ' '.join(filter(None, [profile.get('player_first_name'), profile.get('player_last_name')])) or f'FPL {entry}'


def aggregate(managers, name):
    players = {}
    for manager in managers:
        for player in manager['players']:
            pid = player['id']
            if pid not in players:
                players[pid] = dict(player, points=0, weight=0, active=False, captain=False, vice=False)
            players[pid]['points'] += player['points']
            players[pid]['weight'] += player['weight']
            players[pid]['active'] |= player['active']
    return {'name': name, 'managers': managers, 'players': list(players.values()),
            'score': sum(m['score'] for m in managers), 'hits': sum(m['hits'] for m in managers)}


def snapshot_team(league, match, ctx):
    teams = []
    for name, entries in zip(match['team_names'], match['manager_ids']):
        managers = []
        for entry in entries:
            manager = lineup(get_entry_picks(entry, ctx['gw']), ctx['players'], ctx['scores'],
                             ctx['fixtures'], ctx['settled'], team_rules=True)
            try:
                display_name = manager_name(entry, ctx['season'])
            except (FPLApiError, TypeError, AttributeError):
                display_name = f'FPL {entry}'
            manager.update(entry=entry, name=display_name)
            managers.append(manager)
        teams.append(aggregate(managers, name))
    return match_key(league, match, ctx['season'], ctx['gw']), {
        'team_league': league, 'gameweek': ctx['gw'], 'settled': ctx['settled'],
        'teams': teams, 'observed_at': stamp(ctx['observed']), 'gap': teams[0]['score'] - teams[1]['score']}


def manager_effects(events, before, after):
    for event in events:
        effects = []
        for side in (0, 1):
            old = {m['entry']: m for m in before['teams'][side]['managers']}
            for manager in after['teams'][side]['managers']:
                prior = old[manager['entry']]
                if 'id' in event:
                    a = next((p['points'] for p in prior['players'] if p['id'] == event['id']), 0)
                    b = next((p['points'] for p in manager['players'] if p['id'] == event['id']), 0)
                    delta = b - a
                else:
                    delta = prior['hits'] - manager['hits']
                if delta:
                    effects.append({'side': side, 'manager': manager['name'], 'delta': delta})
        event['manager_effects'] = effects


@team_match.route('/league/cities/match/<int:gw>', defaults={'league': 'cities'})
@team_match.route('/league/libyan/match/<int:gw>', defaults={'league': 'libyan'})
@team_match.route('/league/arab/match/<int:gw>', defaults={'league': 'arab'})
def page(league, gw):
    _, _, title, subtitle = league_config(league)
    return render_template('elite_match.html', league=league, league_title=title, league_subtitle=subtitle,
                           api_url=url_for('team_match.api', league=league, gw=gw,
                                           first=request.args.get('first', ''), second=request.args.get('second', '')))


@team_match.route('/api/cities/match/<int:gw>', defaults={'league': 'cities'})
@team_match.route('/api/libyan/match/<int:gw>', defaults={'league': 'libyan'})
@team_match.route('/api/arab/match/<int:gw>', defaults={'league': 'arab'})
def api(league, gw):
    try:
        names = {request.args.get('first'), request.args.get('second')}
        matches = [m for m in fixtures_for(league, gw) if set(m['team_names']) == names]
        if len(matches) != 1:
            return jsonify(error='هذه المواجهة غير متاحة في جدول الدوري لهذه الجولة.'), 404
        bootstrap = get_bootstrap_data()
        season = min(bootstrap['events'], key=lambda e:e['id'])['deadline_time'][:10]
        key = match_key(league, matches[0], season, gw)
        row = db.session.get(MatchObservation, key, populate_existing=True)
        if row is None or (datetime.utcnow() - row.observed_at).total_seconds() >= 55:
            ctx = context(gw, bootstrap)
            key, state = snapshot_team(league, matches[0], ctx)
            record(key, state, ctx['observed'])
            row = db.session.get(MatchObservation, key, populate_existing=True)
        batches = MatchUpdate.query.filter_by(match_key=key).order_by(MatchUpdate.version.desc()).limit(200).all()
        return jsonify(**row.state, tracking_since=stamp(row.started_at), baseline=row.baseline,
                       updates=[b.payload for b in batches], history_limit=200)
    except ValueError as exc:
        return jsonify(error=str(exc)), 409
    except (FPLApiError, KeyError, TypeError):
        db.session.rollback()
        return jsonify(error='تعذر تحميل التشكيلات الست كاملة. المعروض آخر قراءة ناجحة؛ أعد المحاولة.'), 502
    except SQLAlchemyError:
        db.session.rollback()
        log.exception('Team match storage unavailable')
        return jsonify(error='تعذر حفظ قراءة المواجهة الآن. أعد المحاولة.'), 503
