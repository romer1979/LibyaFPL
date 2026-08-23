# -*- coding: utf-8 -*-
"""
Read-only inspection of the current database state.

Answers: has the new-season reset already run, and is anything currently
stored under a team name that is NOT in this season's roster?

That second question is the important one. Rows can end up holding last
season's team names two different ways:
  a) new_season_reset.py was never run, so last season's rows are still there
  b) the reset WAS run, but the app then saved a finished GW while the code
     still carried last season's TEAMS_FPL_IDS — writing last season's team
     names, with this season's points, into the fresh tables

Case (b) looks identical to (a) from the dashboard but needs a different fix:
the rows are new, so there is nothing worth archiving — they just need deleting.

Writes nothing. Safe to run any time.

Usage on Render shell:
    python inspect_db_state.py
"""

from sqlalchemy import text
from app import app, db
from models import TeamLeagueMatches, TeamLeagueStandings

LEAGUES = ('cities', 'arab', 'libyan')

SEASON_TABLES = [
    'standings_history',
    'fixture_results',
    'team_league_standings',
    'team_league_matches',
    'the100_qualified',
    'the100_eliminations',
    'the100_championship',
    'the100_gw_rankings',
]


def rosters():
    from core.cities_league import TEAMS_FPL_IDS as C
    from core.arab_league import TEAMS_FPL_IDS as A
    from core.libyan_league import TEAMS_FPL_IDS as L
    return {'cities': set(C), 'arab': set(A), 'libyan': set(L)}


def scalar(sql, **p):
    return db.session.execute(text(sql), p).scalar()


def main():
    with app.app_context():
        print("=" * 78)
        print("  1. ARCHIVE TABLES (did new_season_reset.py run?)")
        print("=" * 78)
        archives = db.session.execute(text(
            "SELECT tablename FROM pg_tables WHERE schemaname='public' "
            "AND (" + " OR ".join(f"tablename LIKE '{t}\\_%'" for t in SEASON_TABLES) + ") "
            "ORDER BY tablename"
        )).fetchall()
        if archives:
            for (t,) in archives:
                print(f"    {t:45s} {scalar(f'SELECT COUNT(*) FROM {t}'):>7} rows")
            print("\n    -> reset HAS run at least once.")
        else:
            print("    none found -> new_season_reset.py has NOT run yet.")

        print()
        print("=" * 78)
        print("  2. LIVE TABLE ROW COUNTS")
        print("=" * 78)
        for t in SEASON_TABLES:
            if scalar("SELECT to_regclass(:t)", t=f"public.{t}"):
                print(f"    {t:45s} {scalar(f'SELECT COUNT(*) FROM {t}'):>7} rows")

        print()
        print("=" * 78)
        print("  3. TEAM-LEAGUE DATA vs THIS SEASON'S ROSTER")
        print("=" * 78)
        roster = rosters()
        stale_found = False
        for lg in LEAGUES:
            print(f"\n  --- {lg} ---")
            gws = sorted({r[0] for r in db.session.query(TeamLeagueMatches.gameweek)
                          .filter_by(league_type=lg).distinct().all()})
            sgws = sorted({r[0] for r in db.session.query(TeamLeagueStandings.gameweek)
                           .filter_by(league_type=lg).distinct().all()})
            print(f"    matches saved for GWs  : {gws or 'none'}")
            print(f"    standings saved for GWs: {sgws or 'none'}")

            names = set()
            for m in TeamLeagueMatches.query.filter_by(league_type=lg).all():
                names.add(m.team1_name)
                names.add(m.team2_name)
            for s in TeamLeagueStandings.query.filter_by(league_type=lg).all():
                names.add(s.team_name)

            if not names:
                print("    (no stored team names)")
                continue

            unknown = sorted(n for n in names if n not in roster[lg])
            known = sorted(n for n in names if n in roster[lg])
            print(f"    stored team names: {len(names)}  "
                  f"({len(known)} in roster, {len(unknown)} NOT in roster)")
            if unknown:
                stale_found = True
                print(f"    !! NOT in this season's roster: {unknown}")
                # show a sample row so the points make the origin obvious
                for m in TeamLeagueMatches.query.filter_by(league_type=lg).all():
                    if m.team1_name in unknown or m.team2_name in unknown:
                        print(f"       e.g. GW{m.gameweek}: {m.team1_name} {m.team1_points}"
                              f" - {m.team2_points} {m.team2_name}")
                        break

        print()
        print("=" * 78)
        print("  VERDICT")
        print("=" * 78)
        if stale_found:
            print("  Rows exist under team names that are not in this season's roster.")
            print("  The dashboard guard ignores them, but they should be deleted:")
            print("      python clear_team_league_data.py            # dry run")
            print("      python clear_team_league_data.py --apply    # delete")
        else:
            print("  Clean. Every stored team name belongs to this season's roster.")


if __name__ == '__main__':
    main()
