"""Elite match centre with shared, persisted observations and a single collector lease."""
import logging
import os
import threading
import time
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from flask import Blueprint, jsonify, render_template
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from config import FPL_BASE_URL, LEAGUE_ID
from models import db
from core.fpl_api import fetch_data, get_bootstrap_data, get_fixtures, get_live_data, get_entry_picks, FPLApiError
from core.elite_match_scoring import player_scores, lineup, changes

match_centre = Blueprint('elite_match', __name__)
log = logging.getLogger(__name__)


class MatchObservation(db.Model):
    __tablename__ = 'elite_match_observations'
    key = db.Column(db.String(140), primary_key=True)
    version = db.Column(db.Integer, nullable=False, default=1)
    observed_at = db.Column(db.DateTime, nullable=False)
    started_at = db.Column(db.DateTime, nullable=False)
    baseline = db.Column(db.JSON, nullable=False)
    state = db.Column(db.JSON, nullable=False)


class MatchUpdate(db.Model):
    __tablename__ = 'elite_match_updates'
    id = db.Column(db.Integer, primary_key=True)
    match_key = db.Column(db.String(140), nullable=False, index=True)
    version = db.Column(db.Integer, nullable=False)
    payload = db.Column(db.JSON, nullable=False)
    __table_args__ = (db.UniqueConstraint('match_key', 'version'),)


class CollectorLease(db.Model):
    __tablename__ = 'elite_match_collector_lease'
    id = db.Column(db.Integer, primary_key=True)
    expires_at = db.Column(db.DateTime, nullable=False)
    owner = db.Column(db.String(36), nullable=False)


def stamp(dt):
    return dt.replace(tzinfo=timezone.utc).isoformat()


def league_matches(gw):
    results, page = [], 1
    while True:
        response = fetch_data(f'{FPL_BASE_URL}/leagues-h2h-matches/league/{LEAGUE_ID}/?event={gw}&page={page}')
        results.extend(m for m in response['results'] if m.get('event', gw) == gw)
        if not response.get('has_next', response.get('next')):
            return results
        page += 1


def context(gw, bootstrap=None):
    observed = datetime.utcnow()
    bootstrap = bootstrap or get_bootstrap_data()
    event = next((e for e in bootstrap['events'] if e['id'] == gw), None)
    if event is None:
        raise ValueError('الجولة غير متاحة.')
    if datetime.fromisoformat(event['deadline_time'].replace('Z', '+00:00')) > datetime.now(timezone.utc):
        raise ValueError('تتاح التشكيلات بعد الموعد النهائي للجولة.')
    fixtures = get_fixtures(gw)
    elements = get_live_data(gw)['elements']
    if not elements:
        raise ValueError('لم تنشر بيانات النقاط بعد.')
    clubs = {t['id']: t['short_name'] for t in bootstrap['teams']}
    players = {p['id']: {'id': p['id'], 'name': p['web_name'], 'position': p['element_type'],
                        'club': p['team'], 'clubName': clubs[p['team']]} for p in bootstrap['elements']}
    settled = bool(event.get('finished') and event.get('data_checked'))
    scores = player_scores(elements, fixtures, settled)
    fixture_names = {f['id']: clubs[f['team_h']] + ' / ' + clubs[f['team_a']] for f in fixtures}
    for score in scores.values():
        for part in score['parts'].values():
            part['fixture_label'] = fixture_names.get(part['fixture'], 'تحديث الجولة')
    return {'observed': observed, 'season': min(bootstrap['events'], key=lambda e:e['id'])['deadline_time'][:10],
            'gw': gw, 'fixtures': fixtures, 'players': players,
            'scores': scores, 'settled': settled}


def snapshot(match, ctx):
    teams = []
    for side in (1, 2):
        entry = match[f'entry_{side}_entry']
        if not isinstance(entry, int) or entry <= 0:
            raise ValueError('المواجهة دون خصم فردي متاح.')
        result = lineup(get_entry_picks(entry, ctx['gw']), ctx['players'], ctx['scores'], ctx['fixtures'], ctx['settled'])
        result.update(entry=entry, name=match.get(f'entry_{side}_player_name') or match.get(f'entry_{side}_name') or f'FPL {entry}')
        teams.append(result)
    key = f"{LEAGUE_ID}:{ctx['season']}:{ctx['gw']}:{teams[0]['entry']}:{teams[1]['entry']}"
    return key, {'gameweek': ctx['gw'], 'teams': teams, 'settled': ctx['settled'],
                 'observed_at': stamp(ctx['observed']), 'gap': teams[0]['score'] - teams[1]['score']}


def record(key, state, observed):
    """Atomic compare-and-swap prevents duplicate batches across Gunicorn workers."""
    for _ in range(3):
        row = db.session.get(MatchObservation, key, populate_existing=True)
        if row is None:
            row = MatchObservation(key=key, version=1, observed_at=observed, started_at=observed,
                                   baseline=[t['score'] for t in state['teams']], state=state)
            db.session.add(row)
            try:
                db.session.commit()
                return
            except IntegrityError:
                db.session.rollback()
                continue
        if row.observed_at >= observed:
            return
        version = row.version
        events = changes(row.state, state)
        payload = {'detected_at': stamp(observed), 'previous_at': stamp(row.observed_at),
                   'gap_before': row.state['gap'], 'gap_after': state['gap'],
                   'scores': [t['score'] for t in state['teams']], 'events': events,
                   'coverage_gap': (observed - row.observed_at).total_seconds() > 180}
        updated = db.session.query(MatchObservation).filter_by(key=key, version=version).update(
            {'state': state, 'observed_at': observed, 'version': version + 1}, synchronize_session=False)
        if not updated:
            db.session.rollback()
            continue
        if events or payload['coverage_gap']:
            db.session.add(MatchUpdate(match_key=key, version=version + 1, payload=payload))
        db.session.commit()
        return


@match_centre.route('/league/elite/match/<int:gw>/<int:first>/<int:second>')
def page(gw, first, second):
    return render_template('elite_match.html', api_url=f'/api/elite/match/{gw}/{first}/{second}')


@match_centre.route('/api/elite/match/<int:gw>/<int:first>/<int:second>')
def api(gw, first, second):
    try:
        matches = [m for m in league_matches(gw) if {m.get('entry_1_entry'), m.get('entry_2_entry')} == {first, second}]
        if len(matches) != 1:
            return jsonify(error='هذه المواجهة غير موجودة في جدول دوري النخبة لهذه الجولة.'), 404
        bootstrap = get_bootstrap_data()
        season = min(bootstrap['events'], key=lambda e:e['id'])['deadline_time'][:10]
        match = matches[0]
        key = f"{LEAGUE_ID}:{season}:{gw}:{match['entry_1_entry']}:{match['entry_2_entry']}"
        row = db.session.get(MatchObservation, key, populate_existing=True)
        # All visitors share the collector's recent reading, avoiding API fan-out.
        if row is None or (datetime.utcnow() - row.observed_at).total_seconds() >= 55:
            ctx = context(gw, bootstrap)
            key, state = snapshot(match, ctx)
            record(key, state, ctx['observed'])
            row = db.session.get(MatchObservation, key, populate_existing=True)
        batches = MatchUpdate.query.filter_by(match_key=key).order_by(MatchUpdate.version.desc()).limit(200).all()
        return jsonify(**row.state, tracking_since=stamp(row.started_at), baseline=row.baseline,
                       updates=[batch.payload for batch in batches], history_limit=200)
    except ValueError as exc:
        return jsonify(error=str(exc)), 409
    except (FPLApiError, KeyError, TypeError):
        db.session.rollback()
        return jsonify(error='تعذر تحديث بيانات FPL. ستبقى آخر قراءة ظاهرة؛ أعد المحاولة.'), 502
    except SQLAlchemyError:
        db.session.rollback()
        log.exception('Elite match storage unavailable')
        return jsonify(error='تعذر حفظ قراءة المواجهة الآن. أعد المحاولة.'), 503


def acquire_lease():
    now = datetime.utcnow()
    owner = str(uuid4())
    updated = db.session.query(CollectorLease).filter(CollectorLease.id == 1, CollectorLease.expires_at <= now).update(
        {'expires_at': now + timedelta(minutes=5), 'owner': owner}, synchronize_session=False)
    if updated:
        db.session.commit()
        return owner
    if db.session.get(CollectorLease, 1) is None:
        db.session.add(CollectorLease(id=1, expires_at=now + timedelta(minutes=5), owner=owner))
        try:
            db.session.commit()
            return owner
        except IntegrityError:
            db.session.rollback()
    db.session.rollback()
    return False


def collect(app):
    bootstrap = get_bootstrap_data()
    now = datetime.now(timezone.utc)
    # Keep revisiting the last published GW to pick up late official corrections.
    published = sorted([e for e in bootstrap['events'] if datetime.fromisoformat(e['deadline_time'].replace('Z', '+00:00')) <= now], key=lambda e:e['id'])
    for event in sorted(published, key=lambda e:e['id'])[-2:]:
        if event.get('data_checked') and event != published[-1]:
            continue
        ctx = context(event['id'], bootstrap)
        matches = [m for m in league_matches(event['id']) if (m.get('entry_1_entry') or 0) > 0 and (m.get('entry_2_entry') or 0) > 0]
        def save(match):
            with app.app_context():
                try:
                    key, state = snapshot(match, ctx)
                    record(key, state, ctx['observed'])
                except Exception:
                    db.session.rollback()
                    log.exception('Elite match observation failed')
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(save, matches))


def start_collector(app):
    """No paid service or API key: run with the web app, coordinated via its DB.

    Host sleep/restarts can leave gaps; the UI exposes observation times and gaps.
    ELITE_MATCH_COLLECTOR=0 disables collection (useful in tests/local tooling).
    """
    if os.environ.get('ELITE_MATCH_COLLECTOR', '1') == '0':
        return
    def run():
        while True:
            with app.app_context():
                try:
                    owner = acquire_lease()
                    if owner:
                        collect(app)
                        db.session.query(CollectorLease).filter_by(id=1, owner=owner).update({'expires_at': datetime.utcnow() + timedelta(seconds=60)})
                        db.session.commit()
                except Exception:
                    db.session.rollback()
                    log.exception('Elite match collector cycle failed')
            time.sleep(30)
    threading.Thread(target=run, name='elite-match-collector', daemon=True).start()
