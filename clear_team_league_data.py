# -*- coding: utf-8 -*-
"""
Delete team-league rows (matches + standings) for cities/arab/libyan.

Use when rows were written under the wrong roster — typically because the
app saved a finished GW while the code still carried last season's
TEAMS_FPL_IDS, putting last season's team names into freshly-reset tables.
Those rows are worthless (wrong team groupings), so they are deleted
outright rather than archived.

By default only deletes rows whose team names are NOT in this season's
roster, leaving valid rows alone. Pass --all to delete every row for the
selected leagues regardless of name — use that when a GW was saved with
correct-looking names but the wrong manager groupings behind them.

DRY-RUN by default. --apply prompts for typed confirmation.

Usage on Render shell:
    python clear_team_league_data.py                    # dry run, all 3 leagues
    python clear_team_league_data.py libyan             # one league
    python clear_team_league_data.py --all              # every row, not just stale
    python clear_team_league_data.py --apply            # delete stale rows
    python clear_team_league_data.py --all --apply      # delete everything
"""

import sys
from app import app, db
from models import TeamLeagueMatches, TeamLeagueStandings

LEAGUES = ('cities', 'arab', 'libyan')


def rosters():
    from core.cities_league import TEAMS_FPL_IDS as C
    from core.arab_league import TEAMS_FPL_IDS as A
    from core.libyan_league import TEAMS_FPL_IDS as L
    return {'cities': set(C), 'arab': set(A), 'libyan': set(L)}


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    apply_mode = '--apply' in sys.argv
    delete_all = '--all' in sys.argv
    leagues = [a for a in args if a in LEAGUES] or list(LEAGUES)

    mode = 'APPLY' if apply_mode else 'DRY-RUN'
    scope = 'ALL rows' if delete_all else 'rows with team names outside this season\'s roster'
    print(f"Clearing team-league data for: {', '.join(leagues)}   [{mode}]")
    print(f"Scope: {scope}\n")

    with app.app_context():
        roster = rosters()
        plan = []  # (league, match_ids, standing_ids)

        for lg in leagues:
            matches = TeamLeagueMatches.query.filter_by(league_type=lg).all()
            standings = TeamLeagueStandings.query.filter_by(league_type=lg).all()

            if delete_all:
                m_del = matches
                s_del = standings
            else:
                valid = roster[lg]
                m_del = [m for m in matches
                         if m.team1_name not in valid or m.team2_name not in valid]
                s_del = [s for s in standings if s.team_name not in valid]

            print(f"  --- {lg} ---")
            print(f"    matches  : {len(matches):>4} total, {len(m_del):>4} to delete")
            print(f"    standings: {len(standings):>4} total, {len(s_del):>4} to delete")
            if m_del:
                gws = sorted({m.gameweek for m in m_del})
                names = sorted({n for m in m_del for n in (m.team1_name, m.team2_name)})
                print(f"    GWs affected: {gws}")
                print(f"    team names  : {names}")
            plan.append((lg, m_del, s_del))
            print()

        total_m = sum(len(p[1]) for p in plan)
        total_s = sum(len(p[2]) for p in plan)
        print("=" * 70)
        print(f"  Total: {total_m} match row(s), {total_s} standings row(s) to delete")
        print("=" * 70)

        if total_m == 0 and total_s == 0:
            print("\n  Nothing to delete.")
            return

        if not apply_mode:
            print("\n  Dry run. Rerun with --apply to delete.")
            return

        confirm = input(f"\nType 'yes' to permanently delete {total_m + total_s} row(s): ")
        if confirm.strip().lower() != 'yes':
            print("Aborted. No changes.")
            return

        for lg, m_del, s_del in plan:
            for m in m_del:
                db.session.delete(m)
            for s in s_del:
                db.session.delete(s)
            print(f"  {lg}: deleted {len(m_del)} matches, {len(s_del)} standings")
        db.session.commit()

        print("\nVerification:")
        for lg in leagues:
            m = TeamLeagueMatches.query.filter_by(league_type=lg).count()
            s = TeamLeagueStandings.query.filter_by(league_type=lg).count()
            print(f"  {lg:8s} matches={m:>4}  standings={s:>4}")
        print("\nDone.")


if __name__ == '__main__':
    main()
