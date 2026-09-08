# -*- coding: utf-8 -*-
"""
Fantasy Premier League Multi-League App
"""

from flask import Flask, render_template, jsonify, request
import os
import sys
import threading
import hmac
from functools import wraps
import requests as http_requests
from datetime import datetime

# Load environment variables from .env file (for local development)
from dotenv import load_dotenv
load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import LEAGUE_ID, ARABIC
from core.dashboard import get_dashboard
from core.stats import get_league_stats, get_manager_history
from core.the100 import get_the100_standings, get_the100_stats
from core.cities_league import get_cities_league_data
from core.libyan_league import get_libyan_league_data
from core.arab_league import get_arab_league_data
from core.elite_planner import planner
from core.cities_planner import cities_planner
from core.elite_match import match_centre, start_collector
from core.team_match import team_match
from models import db, save_standings, calculate_rank_change, StandingsHistory, FixtureResult

app = Flask(__name__)
app.register_blueprint(planner)
app.register_blueprint(cities_planner)
app.register_blueprint(match_centre)
app.register_blueprint(team_match)

# Database configuration
database_url = os.environ.get('DATABASE_URL', 'sqlite:///elite_league.db')
# Fix for Render PostgreSQL URL (postgres:// -> postgresql://)
if database_url.startswith('postgres://'):
    database_url = database_url.replace('postgres://', 'postgresql://', 1)

app.config['SQLALCHEMY_DATABASE_URI'] = database_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'elite-league-secret-key-2024')

# Initialize database
db.init_app(app)

# Create tables on first request
with app.app_context():
    db.create_all()

start_collector(app)


@app.route('/')
def home():
    """Home page showing all leagues - simple links only"""
    return render_template('home.html')


def admin_required(view):
    """Gate a state-changing admin route behind ADMIN_TOKEN.

    These routes write permanent records — process-elimination in particular
    stamps eliminated_gw onto manager rows, which cannot be undone from the
    UI — so they must not be reachable by anyone who guesses the URL.

    Fails closed: with ADMIN_TOKEN unset the route is disabled outright,
    rather than silently open.
    """
    @wraps(view)
    def wrapped(*args, **kwargs):
        expected = os.environ.get('ADMIN_TOKEN', '')
        if not expected:
            return jsonify({
                'status': 'disabled',
                'message': 'ADMIN_TOKEN is not configured; admin routes are disabled.'
            }), 503
        supplied = request.args.get('token', '') or request.headers.get('X-Admin-Token', '')
        if not hmac.compare_digest(supplied, expected):
            return jsonify({'status': 'forbidden', 'message': 'Invalid or missing token.'}), 403
        return view(*args, **kwargs)
    return wrapped


# Guard to prevent concurrent elite backfill.
# A lock rather than a bool flag: gunicorn runs with --threads, so a plain
# "if flag: return / flag = True" is a check-then-set race two threads can
# both pass, producing duplicate FPL fetches and duplicate DB writes.
_elite_backfill_lock = threading.Lock()


def backfill_elite_standings(current_gw):
    """
    Backfill missing elite league standings and fixture results for previous GWs.
    Fetches data from the FPL API for any GW not yet saved in the database.
    """
    # Non-blocking: if another thread is already backfilling, this request just
    # skips it rather than queueing behind a job that may take ~30s.
    if not _elite_backfill_lock.acquire(blocking=False):
        return

    try:
        from core.fpl_api import (
            get_bootstrap_data, get_league_standings, get_league_matches,
            get_multiple_entry_data, get_multiple_entry_picks, build_player_info
        )
        from config import LEAGUE_ID, EXCLUDED_PLAYERS, KNOCKOUT_START_GW, get_chip_arabic

        # Find which GWs have standings saved
        saved_standings_gws = db.session.query(
            StandingsHistory.gameweek
        ).distinct().all()
        saved_standings_set = {gw[0] for gw in saved_standings_gws}

        # Find which GWs have fixture results saved
        saved_fixtures_gws = db.session.query(
            FixtureResult.gameweek
        ).distinct().all()
        saved_fixtures_set = {gw[0] for gw in saved_fixtures_gws}

        # Determine which finished GWs need work
        bootstrap = get_bootstrap_data()
        events = bootstrap.get('events', [])
        finished_gws = [e['id'] for e in events if e.get('finished') and e.get('data_checked')]

        # GWs missing standings entirely
        missing_standings = sorted([gw for gw in finished_gws if gw not in saved_standings_set and gw < current_gw])
        # GWs that have standings but missing fixtures
        missing_fixtures_only = sorted([gw for gw in finished_gws if gw in saved_standings_set and gw not in saved_fixtures_set and gw < current_gw])

        # GWs that have standings but result/opponent are missing (saved by old code)
        standings_missing_results = []
        for gw in finished_gws:
            if gw in saved_standings_set and gw not in missing_fixtures_only and gw < current_gw:
                has_blank = StandingsHistory.query.filter_by(gameweek=gw).filter(
                    db.or_(StandingsHistory.result.is_(None), StandingsHistory.result == '-', StandingsHistory.result == '')
                ).first()
                if has_blank:
                    standings_missing_results.append(gw)

        # Detect GWs where all league_points are 0 (backfilled with no LP)
        zero_lp_gws = []
        for gw in finished_gws:
            if gw in saved_standings_set and gw not in missing_standings and gw < current_gw:
                has_nonzero = StandingsHistory.query.filter_by(gameweek=gw).filter(
                    StandingsHistory.league_points > 0
                ).first()
                if not has_nonzero:
                    zero_lp_gws.append(gw)

        # Detect league_points that are stale rather than zero. A GW saved
        # while it was still live keeps whatever the result was at that moment;
        # once FPL moves on, current_gameweek advances and nothing ever re-saves
        # that GW with its final result. Such rows have plausible non-zero
        # values, so neither the "missing" nor the "all zero" check above sees
        # them.
        #
        # FPL's own `total` is the cumulative league points after the last
        # finished GW, so it is a free cross-check. get_league_standings is
        # cached, so this costs no extra request.
        #
        # The comparison has to be made against the last FINISHED gameweek, not
        # the last one before `current_gw`. FPL leaves a gameweek marked current
        # after it finishes — GW3 was finished, data_checked AND is_current at
        # the same time — so keying off `gw < current_gw` compared GW2's rows to
        # a total that already included GW3, and the old `current_gw not in
        # finished_gws` guard existed to suppress the false alarm that caused.
        # It also switched the whole safety net off for exactly the stretch when
        # a freshly settled gameweek most needs checking.
        stale_lp_gws = []
        settled_gws = [gw for gw in finished_gws if gw in saved_standings_set]
        if settled_gws:
            last_settled = max(settled_gws)
            try:
                fpl_totals = {
                    e.get('entry'): int(e.get('total', 0) or 0)
                    for e in get_league_standings(LEAGUE_ID)['standings']['results']
                    if e.get('player_name') not in EXCLUDED_PLAYERS
                }
                for row in StandingsHistory.query.filter_by(gameweek=last_settled).all():
                    want = fpl_totals.get(row.entry_id)
                    if want is not None and (row.league_points or 0) != want:
                        # One wrong row means the chain is untrustworthy; every
                        # settled GW gets recomputed from its match results.
                        stale_lp_gws = settled_gws
                        print(f"[elite] GW{last_settled} league_points disagree with FPL "
                              f"totals (e.g. {row.player_name}: saved "
                              f"{row.league_points}, FPL {want}) — recomputing "
                              f"GWs {settled_gws}")
                        break
            except Exception as e:
                print(f"[elite] Could not cross-check league_points against FPL: {e}")

        zero_lp_gws = sorted(set(zero_lp_gws) | set(stale_lp_gws))

        missing_gws = missing_standings
        all_gws_to_process = sorted(set(missing_standings + missing_fixtures_only + standings_missing_results))

        if not all_gws_to_process and not zero_lp_gws:
            return

        if missing_standings:
            print(f"[elite] Backfilling missing standings+fixtures for GWs: {missing_standings}")
        if missing_fixtures_only:
            print(f"[elite] Backfilling missing fixtures only for GWs: {missing_fixtures_only}")
        if standings_missing_results:
            print(f"[elite] Fixing missing result/opponent in standings for GWs: {standings_missing_results}")
        if zero_lp_gws:
            print(f"[elite] Fixing zero league_points for GWs: {zero_lp_gws}")

        print(f"[elite] Backfilling missing GWs: {missing_gws}")

        # Get league standings (current cumulative data)
        league_data = get_league_standings(LEAGUE_ID)
        teams_league = league_data['standings']['results']

        # Build entry info map
        entry_info = {}
        entry_ids = []
        for entry in teams_league:
            name = entry.get('player_name')
            if name in EXCLUDED_PLAYERS:
                continue
            eid = entry.get('entry')
            entry_ids.append(eid)
            entry_info[eid] = {
                'player_name': name,
                'entry_name': entry.get('entry_name', ''),
                'total_h2h': int(entry.get('total', 0) or 0),
                'points_for': entry.get('points_for', 0),
            }

        # Get overall ranks
        all_entry_data = get_multiple_entry_data(entry_ids)
        player_info_map = build_player_info(bootstrap)
        elements = bootstrap.get('elements', [])

        for gw in all_gws_to_process:
            needs_standings = gw in missing_standings
            needs_fixtures = gw in missing_fixtures_only or gw in standings_missing_results or needs_standings
            needs_result_fix = gw in standings_missing_results or gw in missing_fixtures_only
            label = "standings+fixtures" if needs_standings else ("fixtures+results" if needs_result_fix else "fixtures only")
            print(f"[elite] Backfilling GW{gw} ({label})...")

            try:
                # Fetch H2H matches for this GW (needed for both cases)
                matches_data = get_league_matches(LEAGUE_ID, gw)
                matches = matches_data.get('results', [])

                # Save standings if needed
                if needs_standings:
                    all_picks = get_multiple_entry_picks(entry_ids, gw)

                    standings_data = []
                    for eid in entry_ids:
                        info = entry_info.get(eid, {})
                        picks_data = all_picks.get(eid, {})
                        e_data = all_entry_data.get(eid, {})

                        gw_points = picks_data.get('entry_history', {}).get('points', 0) if picks_data else 0
                        overall_rank = e_data.get('summary_overall_rank')

                        captain = None
                        if picks_data:
                            captain_id = next((p['element'] for p in picks_data.get('picks', []) if p.get('is_captain')), None)
                            if captain_id:
                                capt = next((pl for pl in elements if pl.get('id') == captain_id), None)
                                captain = capt.get('web_name') if capt else None

                        chip_raw = picks_data.get('active_chip') if picks_data else None
                        chip = get_chip_arabic(chip_raw)

                        result = '-'
                        opponent = '-'
                        for match in matches:
                            if match.get('entry_1_entry') == eid:
                                opp_id = match.get('entry_2_entry')
                                opp_info = entry_info.get(opp_id, {})
                                opponent = opp_info.get('player_name', '-')
                                p1 = match.get('entry_1_points', 0)
                                p2 = match.get('entry_2_points', 0)
                                result = 'W' if p1 > p2 else ('L' if p2 > p1 else 'D')
                                break
                            elif match.get('entry_2_entry') == eid:
                                opp_id = match.get('entry_1_entry')
                                opp_info = entry_info.get(opp_id, {})
                                opponent = opp_info.get('player_name', '-')
                                p1 = match.get('entry_1_points', 0)
                                p2 = match.get('entry_2_points', 0)
                                result = 'W' if p2 > p1 else ('L' if p1 > p2 else 'D')
                                break

                        standings_data.append({
                            'entry_id': eid,
                            'player_name': info.get('player_name', ''),
                            'team_name': info.get('entry_name', ''),
                            'projected_league_points': 0,
                            'current_gw_points': gw_points,
                            'total_points': info.get('points_for', 0),
                            'overall_rank': overall_rank,
                            'result': result,
                            'opponent': opponent,
                            'captain': captain or '-',
                            'chip': chip,
                        })

                    standings_data.sort(key=lambda x: (
                        -(3 if x['result'] == 'W' else (1 if x['result'] == 'D' else 0)),
                        -x['current_gw_points']
                    ))
                    for i, team in enumerate(standings_data, 1):
                        team['rank'] = i
                        team['projected_league_points'] = 0

                    save_standings(gw, standings_data)

                # Save fixture results if needed
                if needs_fixtures:
                    fixture_count = 0
                    for match in matches:
                        entry_1 = match.get('entry_1_entry')
                        entry_2 = match.get('entry_2_entry')
                        name_1 = entry_info.get(entry_1, {}).get('player_name', '')
                        name_2 = entry_info.get(entry_2, {}).get('player_name', '')

                        if not name_1 or not name_2:
                            continue

                        p1 = match.get('entry_1_points', 0)
                        p2 = match.get('entry_2_points', 0)
                        winner = 1 if p1 > p2 else (2 if p2 > p1 else 0)

                        existing = FixtureResult.query.filter_by(
                            gameweek=gw, entry_1_id=entry_1, entry_2_id=entry_2
                        ).first()

                        if not existing:
                            fixture = FixtureResult(
                                gameweek=gw,
                                entry_1_id=entry_1,
                                entry_1_name=name_1,
                                entry_1_points=p1,
                                entry_2_id=entry_2,
                                entry_2_name=name_2,
                                entry_2_points=p2,
                                winner=winner
                            )
                            db.session.add(fixture)
                            fixture_count += 1

                    db.session.commit()
                    print(f"[elite] Backfilled GW{gw}: {fixture_count} fixtures saved")

                # Update existing standings with result/opponent from H2H matches
                if needs_result_fix and not needs_standings:
                    updated_count = 0
                    for match in matches:
                        entry_1 = match.get('entry_1_entry')
                        entry_2 = match.get('entry_2_entry')
                        name_1 = entry_info.get(entry_1, {}).get('player_name', '-')
                        name_2 = entry_info.get(entry_2, {}).get('player_name', '-')
                        p1 = match.get('entry_1_points', 0)
                        p2 = match.get('entry_2_points', 0)

                        if p1 > p2:
                            r1, r2 = 'W', 'L'
                        elif p2 > p1:
                            r1, r2 = 'L', 'W'
                        else:
                            r1, r2 = 'D', 'D'

                        # Update entry 1
                        s1 = StandingsHistory.query.filter_by(gameweek=gw, entry_id=entry_1).first()
                        if s1:
                            s1.result = r1
                            s1.opponent = name_2
                            updated_count += 1

                        # Update entry 2
                        s2 = StandingsHistory.query.filter_by(gameweek=gw, entry_id=entry_2).first()
                        if s2:
                            s2.result = r2
                            s2.opponent = name_1
                            updated_count += 1

                    db.session.commit()
                    print(f"[elite] Fixed result/opponent for GW{gw}: {updated_count} standings updated")

            except Exception as e:
                print(f"[elite] Error backfilling GW{gw}: {e}")
                db.session.rollback()
                continue

        # Fix league points for GWs that have 0 (backfilled or newly created)
        gws_needing_lp = sorted(set(missing_standings + zero_lp_gws))
        if gws_needing_lp:
            print(f"[elite] Calculating cumulative league points for GWs: {gws_needing_lp}")
            cumulative_lp = {eid: 0 for eid in entry_ids}
            max_gw_needed = max(gws_needing_lp)

            for gw in sorted(finished_gws):
                if gw > max_gw_needed:
                    break
                try:
                    gw_matches = get_league_matches(LEAGUE_ID, gw)
                    for match in gw_matches.get('results', []):
                        e1 = match.get('entry_1_entry')
                        e2 = match.get('entry_2_entry')
                        p1 = match.get('entry_1_points', 0)
                        p2 = match.get('entry_2_points', 0)
                        # Knockout rounds don't add to the round-robin total —
                        # FPL freezes `total` there, so accumulating would make
                        # this diverge from the numbers we cross-check against.
                        if gw >= KNOCKOUT_START_GW:
                            continue
                        if e1 in cumulative_lp:
                            cumulative_lp[e1] += 3 if p1 > p2 else (1 if p1 == p2 else 0)
                        if e2 in cumulative_lp:
                            cumulative_lp[e2] += 3 if p2 > p1 else (1 if p1 == p2 else 0)
                except Exception as e:
                    print(f"[elite] Error fetching GW{gw} matches for LP calc: {e}")
                    continue

                # Update DB records for this GW if it needs LP fix
                if gw in gws_needing_lp:
                    try:
                        for eid in entry_ids:
                            s = StandingsHistory.query.filter_by(gameweek=gw, entry_id=eid).first()
                            if s:
                                s.league_points = cumulative_lp.get(eid, 0)
                        db.session.commit()
                        print(f"[elite] Updated league points for GW{gw}: {dict(list(cumulative_lp.items())[:3])}...")
                    except Exception as e:
                        print(f"[elite] Error updating LP for GW{gw}: {e}")
                        db.session.rollback()

    except Exception as e:
        print(f"[elite] Backfill error: {e}")
    finally:
        _elite_backfill_lock.release()


def _standings_agree_with_fpl(gameweek, standings):
    """
    Acceptance test run immediately before persisting a settled gameweek:
    do the cumulative totals we are about to write match FPL's own table?

    `gw_settled` comes from bootstrap's finished + data_checked, which is FPL
    saying the PLAYER scores are final. Their head-to-head job is a separate
    piece of machinery, and the finished-gameweek branch reads its output
    (`entry_1_points` / `entry_2_points`) to decide who won. Nothing promises
    the two land in the same instant. If the H2H side were still empty when
    data_checked flipped, every fixture would read 0-0, every match would look
    like a draw, and a full set of plausible-but-wrong rows would be written.

    So rather than assume an ordering, check the result. FPL's `total` is the
    cumulative league points through the last finished gameweek, which is this
    one; if what we computed disagrees with it, their H2H numbers and ours are
    not describing the same gameweek yet, and we skip the write. The next page
    view tries again. This is what keeps "FPL is late" from ever meaning
    "the database is wrong" — it can only ever mean "the database is behind".

    get_league_standings is cached, so this costs no extra request.
    """
    from config import LEAGUE_ID, EXCLUDED_PLAYERS, KNOCKOUT_START_GW
    from core.fpl_api import get_league_standings

    # During the FPL-run knockout phase the round-robin total is frozen on both
    # sides and the comparison stops being meaningful, so it isn't made.
    if gameweek >= KNOCKOUT_START_GW:
        return True

    try:
        fpl_totals = {
            e.get('entry'): int(e.get('total', 0) or 0)
            for e in get_league_standings(LEAGUE_ID)['standings']['results']
            if e.get('player_name') not in EXCLUDED_PLAYERS
        }
    except Exception as e:
        # Can't verify, so don't write. Being a gameweek behind is recoverable;
        # a corrupted base row silently shifts every gameweek after it.
        print(f"[elite] GW{gameweek} not saved: could not reach FPL to verify ({e})")
        return False

    if not fpl_totals:
        print(f"[elite] GW{gameweek} not saved: FPL returned no standings to verify against")
        return False

    for team in standings:
        want = fpl_totals.get(team.get('entry_id'))
        if want is None:
            continue
        got = team.get('projected_league_points', 0)
        if got != want:
            print(f"[elite] GW{gameweek} not saved: computed totals disagree with FPL "
                  f"(e.g. {team.get('player_name')}: computed {got}, FPL {want}). "
                  f"FPL's head-to-head data is probably still catching up; "
                  f"will retry on the next request.")
            return False

    return True


@app.route('/league/elite')
def elite_dashboard():
    """Elite League dashboard page"""
    data = get_dashboard()

    # Calculate rank changes from database
    if data.get('success') and data.get('standings'):
        gameweek = data.get('gameweek', 1)

        # Backfill any missing previous GW standings and fixture results
        try:
            backfill_elite_standings(gameweek)
        except Exception as e:
            print(f"[elite] Backfill failed: {e}")

        # rank_change is already computed by DashboardData.get_dashboard_data()
        # as `base_rank - i` (movement caused by THIS GW's H2H deltas, using
        # current tiebreakers consistently). That value is correct for live
        # play even when the saved StandingsHistory.rank column is stale.
        # Previously this block overrode it with `prev_saved_rank - current_rank`
        # which broke arrows whenever the saved rank lagged the actual standings.

        # Persist only once FPL has settled the gameweek.
        #
        # This used to be `gw_finished or is_live`, which wrote on EVERY page
        # view from the first Saturday kickoff onwards — provisional bonus and
        # all. Whatever the last visitor happened to load during that window
        # was then frozen forever, because once FPL advances the gameweek
        # nothing ever re-saves the old one. That is what put GW2 out by
        # 18 rows, and because the dashboard builds each week as
        # `previous GW from the DB + this week's delta`, one frozen week
        # shifted every week after it.
        #
        # `gw_settled` is FPL's finished + data_checked for this gameweek,
        # unaffected by the 12h display buffer. Waiting for it means the app
        # is immune to how long FPL takes: if their H2H job runs three days
        # late, the row is simply written three days late — it is never
        # written wrong. Live scores keep displaying throughout, they just
        # aren't recorded until they stop moving.
        #
        # Settled data doesn't change, so re-saving on later views is a no-op
        # and the upsert needs no first-write-wins guard.
        if data.get('gw_settled') and _standings_agree_with_fpl(gameweek, data['standings']):
            save_standings(gameweek, data['standings'])

            # Also save fixture results for current GW
            if data.get('fixtures'):
                for fix in data['fixtures']:
                    entry_1 = fix.get('entry_1')
                    entry_2 = fix.get('entry_2')
                    if not entry_1 or not entry_2:
                        continue
                    p1 = fix.get('team_1_points', 0)
                    p2 = fix.get('team_2_points', 0)
                    winner = fix.get('winner', 0)

                    existing = FixtureResult.query.filter_by(
                        gameweek=gameweek, entry_1_id=entry_1, entry_2_id=entry_2
                    ).first()

                    if existing:
                        existing.entry_1_points = p1
                        existing.entry_2_points = p2
                        existing.winner = winner
                    else:
                        fixture = FixtureResult(
                            gameweek=gameweek,
                            entry_1_id=entry_1,
                            entry_1_name=fix.get('team_1_name', ''),
                            entry_1_points=p1,
                            entry_2_id=entry_2,
                            entry_2_name=fix.get('team_2_name', ''),
                            entry_2_points=p2,
                            winner=winner
                        )
                        db.session.add(fixture)

                try:
                    db.session.commit()
                except Exception as e:
                    db.session.rollback()
                    print(f"Error saving elite fixtures: {e}")

    return render_template('dashboard.html', data=data, ar=ARABIC)


@app.route('/league/elite/history')
def elite_history():
    """Elite League history page"""
    from core.elite_history import get_elite_history_data
    data = get_elite_history_data()
    if not data:
        return "Error loading history", 500
    return render_template('elite_history.html', **data)


@app.route('/league/elite/stats')
def elite_stats():
    """Elite League statistics page"""
    data = get_league_stats()
    return render_template('stats.html', data=data, ar=ARABIC)


@app.route('/league/the100')
def the100_dashboard():
    """The 100 League dashboard"""
    data = get_the100_standings()
    # Record any settled elimination GW that hasn't been processed yet, so the
    # phase advances on its own rather than depending on someone remembering
    # to hit an admin URL every week.
    try:
        gw = data.get('gameweek')
        if gw:
            auto_process_the100_eliminations(gw)
    except Exception as e:
        print(f"[the100] auto-elimination skipped: {e}")
    return render_template('the100_dashboard.html', data=data)


@app.route('/league/the100/stats')
def the100_stats():
    """The 100 League statistics page"""
    data = get_the100_stats()
    return render_template('the100_stats.html', data=data)


@app.route('/league/the100/history')
def the100_history():
    """The 100 League elimination-phase history: per-GW ranking + who was cut."""
    from models import get_the100_history
    history = get_the100_history()
    gameweeks = sorted(history.keys())
    return render_template('the100_history.html', history=history, gameweeks=gameweeks)


@app.route('/league/cities')
def cities_dashboard():
    """Cities League dashboard - Team H2H"""
    data = get_cities_league_data()
    return render_template('cities_dashboard.html', data=data)


@app.route('/league/libyan')
def libyan_dashboard():
    """Libyan League dashboard - Team H2H"""
    data = get_libyan_league_data()
    return render_template('libyan_dashboard.html', data=data)


@app.route('/league/arab')
def arab_dashboard():
    """Arab Championship dashboard - Team H2H"""
    data = get_arab_league_data()
    return render_template('arab_dashboard.html', data=data)


@app.route('/league/cities/history')
def cities_history():
    """Cities League history page"""
    from core.team_league_history import get_league_history_data
    data = get_league_history_data('cities')
    if not data:
        return "Error loading history", 500
    return render_template('team_league_history.html', **data)


@app.route('/league/libyan/history')
def libyan_history():
    """Libyan League history page"""
    from core.team_league_history import get_league_history_data
    data = get_league_history_data('libyan')
    if not data:
        return "Error loading history", 500
    return render_template('team_league_history.html', **data)


@app.route('/league/arab/history')
def arab_history():
    """Arab League history page"""
    from core.team_league_history import get_league_history_data
    data = get_league_history_data('arab')
    if not data:
        return "Error loading history", 500
    return render_template('team_league_history.html', **data)


@app.route('/api/comparison')
def comparison_data():
    """API endpoint for manager comparison data"""
    data = get_manager_history()
    return jsonify(data)


@app.route('/api/dashboard')
def api_dashboard():
    """API endpoint for AJAX updates"""
    data = get_dashboard()
    data['timestamp'] = datetime.now().strftime('%H:%M:%S')
    return jsonify(data)


@app.errorhandler(404)
def page_not_found(e):
    return render_template('home.html', elite_standings=[], error='Page not found'), 404


@app.errorhandler(500)
def server_error(e):
    return render_template('home.html', elite_standings=[], error='Server error'), 500


@app.route('/admin/the100/init-qualified')
@admin_required
def init_the100_qualified():
    """Initialize the 100 qualified managers after GW19 - run once"""
    from models import The100QualifiedManager, save_the100_qualified_managers
    from core.the100 import get_qualification_standings, WINNER_ENTRY_ID, THE100_LEAGUE_ID
    
    # Check if already initialized
    existing = The100QualifiedManager.query.first()
    if existing:
        count = The100QualifiedManager.query.count()
        return jsonify({
            'status': 'already_exists',
            'message': f'Qualified managers already initialized ({count} managers)'
        })
    
    # Fetch qualification standings
    qual_standings = get_qualification_standings(THE100_LEAGUE_ID)
    
    if not qual_standings:
        return jsonify({'status': 'error', 'message': 'Could not fetch qualification standings'})
    
    # Determine qualified managers (top 99 + winner)
    qualified = []
    winner_in_top_99 = False
    
    # Check if winner is in top 99
    for row in qual_standings:
        if row.get('entry') == WINNER_ENTRY_ID and row.get('rank', 0) <= 99:
            winner_in_top_99 = True
            break
    
    # Build qualified list
    count_non_winner = 0
    for row in qual_standings:
        entry_id = row.get('entry')
        rank = row.get('rank', 0)
        is_winner = (entry_id == WINNER_ENTRY_ID)
        
        if winner_in_top_99:
            if rank <= 100:
                qualified.append({
                    'entry_id': entry_id,
                    'manager_name': row.get('player_name', ''),
                    'team_name': row.get('entry_name', ''),
                    'qualification_rank': rank,
                    'qualification_total': row.get('total', 0),
                    'is_winner': is_winner
                })
        else:
            if is_winner:
                qualified.append({
                    'entry_id': entry_id,
                    'manager_name': row.get('player_name', ''),
                    'team_name': row.get('entry_name', ''),
                    'qualification_rank': rank,
                    'qualification_total': row.get('total', 0),
                    'is_winner': True
                })
            elif count_non_winner < 99:
                qualified.append({
                    'entry_id': entry_id,
                    'manager_name': row.get('player_name', ''),
                    'team_name': row.get('entry_name', ''),
                    'qualification_rank': rank,
                    'qualification_total': row.get('total', 0),
                    'is_winner': False
                })
                count_non_winner += 1
        
        if len(qualified) >= 100:
            break
    
    # Save to database
    success = save_the100_qualified_managers(qualified)
    
    if success:
        return jsonify({
            'status': 'success',
            'message': f'Initialized {len(qualified)} qualified managers',
            'qualified_count': len(qualified),
            'winner_in_top_99': winner_in_top_99
        })
    else:
        return jsonify({'status': 'error', 'message': 'Failed to save to database'})


_the100_elim_lock = threading.Lock()


def run_the100_elimination(gameweek, force=False):
    """Eliminate the bottom N for one gameweek. Returns a (payload, status) pair.

    Elimination is irreversible — it stamps eliminated_gw onto manager rows —
    so by default this refuses to run until the gameweek is genuinely settled.
    get_elimination_standings() already reports that as gw_finished_for_save
    (all fixtures done plus the post-finish buffer); it was previously computed
    and ignored, which meant running this mid-gameweek would permanently
    eliminate whoever happened to be bottom at that moment.

    force=True overrides the settled check, for the rare case where the buffer
    logic is wrong and the organiser has confirmed the result by hand.
    """
    from models import (
        The100QualifiedManager, The100EliminationResult,
        save_the100_elimination
    )
    from core.the100 import (
        get_elimination_standings, ELIMINATION_START_GW,
        ELIMINATION_END_GW, ELIMINATIONS_PER_GW
    )

    if gameweek < ELIMINATION_START_GW or gameweek > ELIMINATION_END_GW:
        return {
            'status': 'error',
            'message': f'Invalid gameweek. Elimination phase is GW{ELIMINATION_START_GW}-{ELIMINATION_END_GW}'
        }, 400

    existing_count = The100EliminationResult.query.filter_by(gameweek=gameweek).count()
    if existing_count:
        return {
            'status': 'already_processed',
            'message': f'GW{gameweek} elimination already processed ({existing_count} eliminated)'
        }, 200

    remaining = The100QualifiedManager.query.filter(
        The100QualifiedManager.eliminated_gw.is_(None)
    ).order_by(The100QualifiedManager.qualification_rank).all()

    if not remaining:
        return {'status': 'error', 'message': 'No remaining managers found'}, 400

    qualified = [{
        'entry_id': m.entry_id,
        'manager_name': m.manager_name,
        'team_name': m.team_name,
        'qualification_rank': m.qualification_rank,
        'qualification_total': m.qualification_total,
        'is_winner': m.is_winner
    } for m in remaining]

    elim_data = get_elimination_standings(gameweek, qualified)

    if not elim_data or not elim_data.get('standings'):
        return {'status': 'error', 'message': 'Could not fetch elimination standings'}, 502

    if not force and not elim_data.get('gw_finished_for_save'):
        return {
            'status': 'not_finished',
            'message': (f'GW{gameweek} is not settled yet (fixtures still running, or '
                        f'inside the post-finish buffer). Refusing to eliminate on '
                        f'provisional scores. Re-run once it settles, or pass force=1 '
                        f'if you have verified the result.')
        }, 409

    standings = elim_data['standings']

    if len(standings) <= ELIMINATIONS_PER_GW:
        return {
            'status': 'error',
            'message': (f'Only {len(standings)} manager(s) remain; refusing to '
                        f'eliminate {ELIMINATIONS_PER_GW}.')
        }, 400

    # Bottom N. standings is sorted by (-gw_points, qualification_rank), so a
    # tie on points at the cut line is broken by the better qualification rank.
    eliminated = standings[-ELIMINATIONS_PER_GW:]

    eliminated_list = [{
        'entry_id': m['entry_id'],
        'manager_name': m['manager_name'],
        'team_name': m['team_name'],
        'gw_points': m['live_gw_points'],
        'gw_rank': m['live_rank']
    } for m in eliminated]

    if not save_the100_elimination(gameweek, eliminated_list):
        return {'status': 'error', 'message': 'Failed to save eliminations'}, 500

    print(f"[the100] GW{gameweek} elimination processed: "
          f"{[m['manager_name'] for m in eliminated_list]}")
    return {
        'status': 'success',
        'message': f'Processed GW{gameweek} elimination',
        'eliminated': [m['manager_name'] for m in eliminated_list],
        'remaining_count': len(qualified) - ELIMINATIONS_PER_GW
    }, 200


@app.route('/admin/the100/process-elimination/<int:gameweek>')
@admin_required
def process_the100_elimination(gameweek):
    """Manually process elimination for a specific gameweek."""
    force = request.args.get('force', '') in ('1', 'true', 'yes')
    payload, status = run_the100_elimination(gameweek, force=force)
    return jsonify(payload), status


def auto_process_the100_eliminations(current_gw):
    """Process any settled elimination gameweek that hasn't been recorded yet.

    Called on The 100 page loads so the organiser doesn't have to remember to
    hit an admin URL fourteen weeks running. Missing even one week breaks the
    arithmetic the bracket depends on: 14 gameweeks x 6 leaves exactly 16, and
    generate_the100_bracket() requires exactly 16.

    Never forces — a gameweek that isn't settled is simply left for later.
    """
    from core.the100 import ELIMINATION_START_GW, ELIMINATION_END_GW

    if current_gw < ELIMINATION_START_GW:
        return
    if not _the100_elim_lock.acquire(blocking=False):
        return
    try:
        last = min(current_gw, ELIMINATION_END_GW)
        for gw in range(ELIMINATION_START_GW, last + 1):
            payload, _ = run_the100_elimination(gw)
            if payload.get('status') == 'not_finished':
                break  # later gameweeks can't be settled either
    except Exception as e:
        print(f"[the100] auto-elimination error: {e}")
        db.session.rollback()
    finally:
        _the100_elim_lock.release()


@app.route('/api/the100')
def api_the100():
    """API endpoint for The 100 data"""
    data = get_the100_standings()
    data['timestamp'] = datetime.now().strftime('%H:%M:%S')
    return jsonify(data)


@app.route('/admin/social-posts')
def admin_social_posts():
    """Admin page for generating social media posts"""
    return render_template('admin_social_posts.html')


@app.route('/api/generate-post', methods=['POST'])
def api_generate_post():
    """Generate a social media post for a league using OpenAI"""
    api_key = os.environ.get('OPEN_AI_KEY')
    if not api_key:
        return jsonify({'error': 'OpenAI API key not configured'})

    data = request.get_json()
    league = data.get('league', 'elite')
    post_format = data.get('format', 'twitter')

    try:
        summary = _gather_league_summary(league)
        if not summary:
            return jsonify({'error': 'Failed to fetch league data'})

        post = _call_openai(api_key, summary, post_format, league)
        return jsonify({'post': post, 'league': league, 'format': post_format})
    except Exception as e:
        print(f"[social-post] Error: {e}")
        return jsonify({'error': f'Error generating post: {str(e)}'})


def _gather_league_summary(league):
    """Gather and format league data with focus on competitive stakes"""

    if league == 'elite':
        data = get_dashboard()
        if not data.get('success'):
            return None
        gw = data.get('gameweek', '?')
        standings = data.get('standings', [])
        total = len(standings)

        # Get additional stats
        elite_stats = get_league_stats()
        ps = elite_stats.get('points_stats', {}) if elite_stats and elite_stats.get('success') else {}

        # --- Points stats ---
        stats_text = ""
        if ps:
            stats_text += f"GW STATS: Average {ps.get('avg')} pts, Highest {ps.get('max')} pts, Lowest {ps.get('min')} pts\n"
            max_mgrs = ', '.join(ps.get('max_managers', []))
            min_mgrs = ', '.join(ps.get('min_managers', []))
            stats_text += f"  Star of GW: {max_mgrs} with {ps.get('max')} pts\n"
            stats_text += f"  Worst performer: {min_mgrs} with {ps.get('min')} pts\n"
            if ps.get('lucky_manager'):
                stats_text += f"  Lucky manager (won with lowest score): {ps['lucky_manager']} with {ps['lucky_points']} pts\n"
            if ps.get('unlucky_manager'):
                stats_text += f"  Unlucky manager (lost/drew with highest score): {ps['unlucky_manager']} with {ps['unlucky_points']} pts\n"

        # --- Captain analysis ---
        captain_text = ""
        if elite_stats and elite_stats.get('success'):
            caps = elite_stats.get('captain_stats', [])
            if caps:
                top_cap = caps[0]
                captain_text = f"CAPTAINS: Most popular: {top_cap['name']} ({top_cap['count']}/{total} managers)\n"
                other_caps = caps[1:4]
                if other_caps:
                    others = ', '.join(f"{c['name']} ({c['count']})" for c in other_caps)
                    captain_text += f"  Others chose: {others}\n"

        # --- Chips used with match results ---
        chips_text = ""
        if elite_stats and elite_stats.get('success'):
            chips = elite_stats.get('chips_used', [])
            if chips:
                chip_details = []
                for c in chips:
                    mgr_name = c['manager']
                    chip_name = c.get('chip_ar', c.get('chip', ''))
                    # Find their result
                    mgr_result = '-'
                    for s in standings:
                        if s['player_name'] == mgr_name:
                            mgr_result = s.get('result', '-')
                            break
                    result_ar = {'W': 'فاز', 'L': 'خسر', 'D': 'تعادل'}.get(mgr_result, '-')
                    chip_details.append(f"{mgr_name} ({chip_name} - {result_ar})")
                chips_text = f"CHIPS USED: {len(chips)} this GW: {', '.join(chip_details)}\n"

        # --- Effective ownership ---
        eo_text = ""
        if elite_stats and elite_stats.get('success'):
            eo = elite_stats.get('effective_ownership', [])[:6]
            if eo:
                eo_names = ', '.join(f"{p['name']} ({p['count']})" for p in eo)
                eo_text = f"MOST OWNED PLAYERS: {eo_names}\n"

        # --- H2H Fixtures with match stories ---
        fixtures_text = "ALL H2H FIXTURES:\n"
        draws_high = []
        for f in data.get('fixtures', []):
            w = f['team_1_name'] if f['winner'] == 1 else (f['team_2_name'] if f['winner'] == 2 else 'Draw')
            fixtures_text += f"- {f['team_1_name']} {f['team_1_points']} vs {f['team_2_points']} {f['team_2_name']} (Winner: {w}, Diff: {f['points_diff']})\n"
            # Track high-scoring draws
            if f['winner'] == 0 and f['team_1_points'] >= 70:
                draws_high.append(f"{f['team_1_name']} و {f['team_2_name']} ({f['team_1_points']} pts each)")

        unlucky_draws = ""
        if draws_high:
            unlucky_draws = f"UNLUCKY DRAWS (high scorers drew each other): {'; '.join(draws_high)}\n"

        # --- Standings zones ---
        # Early season (GW < 20): focus more on top positions
        # Late season (GW >= 20): focus on playoff/relegation zones
        playoff_zone = standings[:8] if len(standings) >= 8 else standings
        relegation_zone = standings[max(0, total-10):] if total > 10 else standings

        top_text = "TOP OF TABLE (Playoff Zone - Top 8):\n"
        for t in playoff_zone:
            top_text += f"  {t['rank']}. {t['player_name']} - {t['projected_league_points']} LP, GW: {t['current_gw_points']}, {t.get('result','-')}\n"

        bottom_text = "BOTTOM OF TABLE (Relegation Zone - below 18th):\n"
        for t in relegation_zone:
            tag = " << RELEGATION" if t['rank'] > 18 else ""
            bottom_text += f"  {t['rank']}. {t['player_name']} - {t['projected_league_points']} LP, GW: {t['current_gw_points']}, {t.get('result','-')}{tag}\n"

        gw_num = int(gw) if str(gw).isdigit() else 0
        stage = "EARLY SEASON" if gw_num < 15 else ("MID SEASON" if gw_num < 28 else "FINAL STRETCH")

        return (
            f"League: Elite League (دوري النخبة) - H2H, {total} managers\n"
            f"Gameweek: {gw} ({stage})\n\n"
            f"STAKES: Top 8 qualify for PLAYOFFS (GW36), Top 18 stay next season, Bottom {total - 18} RELEGATE\n\n"
            f"{stats_text}\n"
            f"{captain_text}\n"
            f"{chips_text}\n"
            f"{eo_text}\n"
            f"{fixtures_text}\n"
            f"{unlucky_draws}\n"
            f"{top_text}\n"
            f"{bottom_text}"
        )

    elif league == 'the100':
        data = get_the100_standings()
        stats = get_the100_stats()
        if not data or not data.get('standings'):
            return None
        gw = data.get('gameweek', '?')
        phase = data.get('phase', 'unknown')
        phase_info = data.get('phase_info', {})
        standings = data.get('standings', [])

        if phase == 'qualification':
            total = len(standings)
            # Around the 100th place cutoff
            around_cutoff = standings[max(0, min(95, total-5)):min(105, total)]
            cutoff_text = ""
            for t in around_cutoff:
                rank = t.get('live_rank', '')
                tag = " << OUTSIDE" if rank > 100 else ""
                cutoff_text += f"  {rank}. {t['manager_name']} - Total: {t.get('live_total', 0)}, GW: {t.get('live_gw_points', 0)}{tag}\n"

            top5 = ""
            for t in standings[:5]:
                top5 += f"  {t.get('live_rank')}. {t['manager_name']} - Total: {t.get('live_total', 0)}\n"

            return (
                f"League: The 100 (دوري المئة) - Qualification Phase\n"
                f"Gameweek: {gw}, Total managers: {total}\n\n"
                f"STAKES: Top 99 + defending champion qualify for elimination phase\n\n"
                f"TOP 5:\n{top5}\n"
                f"QUALIFICATION CUTOFF (around 100th place):\n{cutoff_text}"
            )

        elif phase == 'elimination':
            remaining = data.get('remaining_managers', len(standings))
            safe_count = data.get('safe_count', remaining - 6)
            total_eliminated = data.get('total_eliminated', 0)
            gws_remaining = data.get('gws_remaining', 0)

            # --- Star of the GW (highest scorer) with player breakdown ---
            star = standings[0] if standings else None
            star_text = ""
            if star:
                star_text = f"STAR OF THE GW: {star['manager_name']} with {star['live_gw_points']} points\n"
                star_text += f"  Captain: {star.get('captain', '-')}\n"
                if star.get('chip'):
                    star_text += f"  Chip used: {star['chip']}\n"
                # Top contributing players from their team
                top_players = sorted(
                    [p for p in star.get('players', []) if p.get('points') and p['points'] > 0 and p.get('is_starter')],
                    key=lambda x: -(x.get('points') or 0)
                )[:5]
                if top_players:
                    contrib = ', '.join(f"{p['name']} ({p['points']}pts)" for p in top_players)
                    star_text += f"  Key contributors: {contrib}\n"

            # --- Worst performer (lowest scorer) with player breakdown ---
            worst = standings[-1] if standings else None
            worst_text = ""
            if worst:
                worst_text = f"WORST PERFORMER (خازوق الجولة): {worst['manager_name']} with {worst['live_gw_points']} points\n"
                worst_text += f"  Captain: {worst.get('captain', '-')}\n"
                # Show bench/zero players
                zeros = [p for p in worst.get('players', []) if p.get('is_starter') and (p.get('points') or 0) <= 0]
                if zeros:
                    zero_names = ', '.join(p['name'] for p in zeros)
                    worst_text += f"  Players with 0 or less: {zero_names}\n"
                scorers = sorted(
                    [p for p in worst.get('players', []) if p.get('points') and p['points'] > 0 and p.get('is_starter')],
                    key=lambda x: -(x.get('points') or 0)
                )[:3]
                if scorers:
                    only_from = ', '.join(f"{p['name']} ({p['points']}pts)" for p in scorers)
                    worst_text += f"  Only points came from: {only_from}\n"

            # --- The 6 eliminated managers ---
            eliminated = standings[safe_count:] if safe_count < len(standings) else []
            elim_text = "THE 6 ELIMINATED THIS GW:\n"
            for t in eliminated:
                elim_text += f"  {t.get('live_rank')}. {t['manager_name']} - {t['live_gw_points']} pts (was qualification rank {t.get('qualification_rank', '?')})\n"

            # --- Tiebreaker situation (if any eliminated had same points as a safe manager) ---
            tiebreak_text = ""
            if safe_count > 0 and safe_count < len(standings):
                last_safe = standings[safe_count - 1]
                first_elim = standings[safe_count]
                if last_safe['live_gw_points'] == first_elim['live_gw_points']:
                    tiebreak_text = (
                        f"TIEBREAKER DRAMA: {last_safe['manager_name']} and {first_elim['manager_name']} "
                        f"both scored {last_safe['live_gw_points']} pts! "
                        f"{last_safe['manager_name']} survived because their qualification rank "
                        f"({last_safe.get('qualification_rank', '?')}) was better than "
                        f"{first_elim['manager_name']} ({first_elim.get('qualification_rank', '?')})\n"
                    )

            # --- The barely-safe managers ---
            barely_safe = standings[max(0, safe_count - 3):safe_count]
            barely_text = "BARELY SAFE (survived by slim margin):\n"
            for t in barely_safe:
                pts_above = t['live_gw_points'] - (eliminated[0]['live_gw_points'] if eliminated else 0)
                barely_text += f"  {t.get('live_rank')}. {t['manager_name']} - {t['live_gw_points']} pts (only +{pts_above} above elimination)\n"

            # --- Points stats ---
            stats_text = ""
            if stats and stats.get('success'):
                ps = stats.get('points_stats', {})
                stats_text = f"GW STATS: Average {ps.get('avg')} pts, Highest {ps.get('max')} pts, Lowest {ps.get('min')} pts\n"

                # Captain stats
                caps = stats.get('captain_stats', [])[:5]
                if caps:
                    caps_str = ', '.join(f"{c['name']} ({c['count']})" for c in caps)
                    stats_text += f"Most popular captains: {caps_str}\n"

                # Chips used
                chips = stats.get('chips_used', [])
                if chips:
                    chips_str = ', '.join(f"{c['manager']} used {c['chip_ar']}" for c in chips)
                    stats_text += f"Chips used this GW: {chips_str}\n"

            entered_gw = remaining + 6  # before this GW's elimination

            return (
                f"League: The 100 (دوري المئة) - Elimination Phase\n"
                f"Gameweek: {gw}\n"
                f"Entered this GW: {entered_gw} managers, After elimination: {remaining} managers remain\n"
                f"Total eliminated so far: {total_eliminated}, GWs remaining in elimination: {gws_remaining}\n"
                f"Bottom 6 each GW are eliminated. 16 survive for Championship knockout.\n\n"
                f"{star_text}\n"
                f"{worst_text}\n"
                f"{elim_text}\n"
                f"{tiebreak_text}\n"
                f"{barely_text}\n"
                f"{stats_text}"
            )

        else:  # championship
            bracket_text = ""
            for t in standings:
                bracket_text += f"  {t.get('live_rank', '?')}. {t['manager_name']} - GW pts: {t.get('live_gw_points', 0)}\n"
            return (
                f"League: The 100 (دوري المئة) - Championship Phase (Knockout)\n"
                f"Gameweek: {gw}\n\n"
                f"STAKES: Head-to-head knockout! Lose and you're out.\n\n"
                f"Bracket:\n{bracket_text}"
            )

    elif league in ('libyan', 'arab', 'cities'):
        league_info = {
            'cities': {
                'func': get_cities_league_data,
                'name': 'Cities League (دوري المدن)',
                'division': 'First Division (top tier)',
                'relegation_to': 'Libyan League (second division)',
            },
            'libyan': {
                'func': get_libyan_league_data,
                'name': 'Libyan League (الدوري الليبي)',
                'division': 'Second Division',
                'relegation_to': 'Arab League (third division)',
            },
            'arab': {
                'func': get_arab_league_data,
                'name': 'Arab League (الدوري العربي)',
                'division': 'Third Division (lowest tier)',
                'relegation_to': 'OUT OF THE COMPETITION for next season',
            },
        }
        info = league_info[league]
        data = info['func']()
        if not data or not data.get('standings'):
            return None
        gw = data.get('gameweek', '?')
        standings = data.get('standings', [])
        total = len(standings)
        relegation_line = total - 6  # Bottom 6 relegate

        # Title race (top 3)
        top_text = ""
        for t in standings[:3]:
            top_text += f"  {t.get('rank')}. {t['team_name']} - {t['league_points']} LP, GW: {t['live_gw_points']}, {t.get('result','')}\n"

        # Relegation zone (bottom 6 + 2 above)
        relegation_text = ""
        for t in standings[max(0, relegation_line - 2):]:
            tag = " << RELEGATION" if t.get('rank', 0) > relegation_line else ""
            relegation_text += f"  {t.get('rank')}. {t['team_name']} - {t['league_points']} LP, GW: {t['live_gw_points']}, {t.get('result','')}{tag}\n"

        # Key matches (involving relegation-zone teams)
        relegation_teams = set(t['team_name'] for t in standings[relegation_line:])
        key_matches = ""
        for m in data.get('matches', []):
            w = m['team_1'] if m['winner'] == 1 else (m['team_2'] if m['winner'] == 2 else 'Draw')
            is_key = m['team_1'] in relegation_teams or m['team_2'] in relegation_teams
            tag = " ** RELEGATION BATTLE" if is_key else ""
            key_matches += f"- {m['team_1']} {m['points_1']} vs {m['points_2']} {m['team_2']} (Winner: {w}){tag}\n"

        best = ""
        bt = data.get('best_team')
        bm = data.get('best_manager')
        if bt:
            best += f"Best team this GW: {bt['name']} ({bt['points']} pts)\n"
        if bm:
            best += f"Star player: {bm['name']} ({bm['points']} pts, team: {bm.get('team', '')})\n"

        return (
            f"League: {info['name']}\n"
            f"Division: {info['division']}\n"
            f"Gameweek: {gw}, Total teams: {total} (3 managers per team)\n\n"
            f"STAKES:\n"
            f"- Bottom 6 teams RELEGATE to {info['relegation_to']}\n\n"
            f"TITLE RACE (Top 3):\n{top_text}\n"
            f"RELEGATION BATTLE (bottom 6 + borderline):\n{relegation_text}\n"
            f"MATCHES:\n{key_matches}\n"
            f"{best}"
        )

    return None


def _call_openai(api_key, summary, post_format, league=''):
    """Call OpenAI API to generate the social media post"""
    if post_format == 'twitter':
        format_instruction = (
            "اكتب منشور تويتر باللغة العربية. "
            "يجب أن لا يتجاوز 280 حرف. مختصر وجذاب ومثير. "
            "ركز على أهم قصة واحدة أو اثنتين فقط."
        )
    elif league == 'elite':
        format_instruction = (
            "اكتب منشور انستقرام مفصل باللغة العربية مع ايموجي كعلامات للفقرات. "
            "التدرج: عنوان الجولة > متوسط النقاط > نجم الجولة > أسوأ مدرب > "
            "محظوظ الجولة (فاز بنقاط قليلة) > منحوس الجولة (خسر أو تعادل بنقاط عالية أو تعادل لأنهم يلعبون ضد بعض) > "
            "الكبتنة (من اختار من وكم واحد وهل نجحت أو فشلت) > "
            "الشيبات المستخدمة ونتائج أصحابها > "
            "أبرز مباريات الجولة خصوصاً المتصدرين والمنافسة على القمة > "
            "اختم بتشويق للجولة القادمة."
        )
    elif league == 'the100':
        format_instruction = (
            "اكتب منشور انستقرام مفصل باللغة العربية مع ايموجي كعلامات للفقرات. "
            "التدرج: عنوان الجولة والمرحلة > عدد المدربين قبل وبعد > متوسط النقاط > "
            "نجم الجولة مع ذكر اللاعبين اللي ساهمو > أسوأ أداء مع السبب > "
            "اذكر ال 6 المغادرين بالاسم > اي حالة تعادل بالنقاط وكيف تم الفصل > "
            "الناجين بأعجوبة. اختم بتشويق للجولة القادمة."
        )
    else:
        format_instruction = (
            "اكتب منشور انستقرام مفصل باللغة العربية مع ايموجي كعلامات للفقرات. "
            "التدرج: عنوان الجولة > متوسط النقاط > أفضل فريق > أسوأ فريق > "
            "أهم المباريات خصوصاً معارك الهبوط > الترتيب والمنافسة على القمة > "
            "اختم بتشويق للجولة القادمة."
        )

    response = http_requests.post(
        'https://api.openai.com/v1/chat/completions',
        headers={
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
        },
        json={
            'model': 'gpt-4o-mini',
            'messages': [
                {
                    'role': 'system',
                    'content': (
                        "أنت صحفي رياضي متخصص في فانتازي الدوري الإنجليزي. "
                        "تكتب منشورات سوشيال ميديا باللغة العربية بأسلوب عامي وحماسي.\n\n"
                        "أسلوب الكتابة:\n"
                        "- اكتب بأسلوب عامي ليبي/عربي طبيعي وليس فصحى رسمية\n"
                        "- كأنك تحكي لأصدقائك عن اللي صار بالجولة\n"
                        "- اذكر اسماء المدربين كما هي بالبيانات\n"
                        "- تكلم عن اللاعبين اللي جابو النقاط (مثلاً 'الفضل يعود للرباعي X و Y و Z')\n"
                        "- تكلم عن اللي جاب أقل نقاط وليش (دكة أصفار، كابتن فاشل، الخ)\n"
                        "- اذكر متوسط النقاط وقارنه\n"
                        "- اذا في تعادل بالنقاط وتم الفصل بينهم اشرح كيف (الترتيب في مرحلة التأهل)\n"
                        "- خلي المنشور يتدرج حسب نوع الدوري (التدرج موضح في التعليمات)\n\n"
                        "ملاحظات لدوري النخبة (H2H):\n"
                        "- محظوظ الجولة = فاز بأقل نقاط (خصمه جاب أقل منه)\n"
                        "- منحوس الجولة = خسر أو تعادل بنقاط عالية (خصمه جاب أكثر أو نفس النقاط)\n"
                        "- اذا لاعبين جابو نقاط عالية وتعادلو لأنهم يلعبو ضد بعض اذكرهم كمنحوسين\n"
                        "- تكلم عن الكبتنة: كم واحد اختار الكابتن الأول وهل نجح\n"
                        "- اذا في شيبات (وايلد كارد، فري هيت، بنش بوست، تربل كابتن) اذكر من لعبها وهل فاز أو خسر\n\n"
                        "قواعد مهمة جداً:\n"
                        "- لا تستخدم ** أو أي تنسيق ماركداون. اكتب نص عادي فقط.\n"
                        "- في دوري المئة مرحلة الإقصاء: خروج 6 مدربين كل جولة هو القانون وليس مفاجأة! "
                        "لا تقل 'من كان يتوقع خروج 6' أو 'دفعة واحدة'. الدراما هي في من هم هؤلاء الستة بالتحديد.\n"
                        "- لا تخترع معلومات. استخدم فقط البيانات المقدمة لك.\n"
                        "- لا تكرر نفس الجملة بصياغات مختلفة. كل جملة يجب أن تضيف معلومة جديدة."
                    )
                },
                {
                    'role': 'user',
                    'content': f"{summary}\n\n{format_instruction}"
                }
            ],
            'temperature': 0.8,
            'max_tokens': 1000,
        },
        timeout=30,
    )

    if response.status_code != 200:
        raise RuntimeError(f"OpenAI API error: {response.status_code} - {response.text[:200]}")

    result = response.json()
    return result['choices'][0]['message']['content'].strip()


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001))
    app.run(debug=False, host='0.0.0.0', port=port)
