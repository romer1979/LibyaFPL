# -*- coding: utf-8 -*-
"""
Audit (and optionally rebuild) team-league standings for cities/arab/libyan.

Standings are DERIVED data: for every gameweek,

    league_points[gw]    = league_points[gw-1]    + (3 win / 1 draw / 0 loss)
    total_fpl_points[gw] = total_fpl_points[gw-1] + that team's GW points

both taken from the saved rows in team_league_matches. So the standings
table can always be recomputed from the matches table, and any drift
between the two is a bug.

This script walks that chain from GW1 and reports where stored standings
stop agreeing with what the matches imply. Typical causes:
  - a gameweek's matches were never saved, so the next GW built on a stale
    base and every later GW inherited the error
  - standings were written by an older/buggier code path
  - rows were written under a different roster (e.g. a season rollover)

Reads rosters and league IDs from core/*_league.py — never hardcodes them,
so it always audits against the CURRENT season.

    python audit_standings.py                 # audit all three leagues
    python audit_standings.py cities          # one league
    python audit_standings.py --verbose       # per-team detail for bad GWs
    python audit_standings.py --fix           # rewrite standings from matches
    python audit_standings.py cities --fix    # ...for one league

--fix rebuilds ONLY the standings table, from the matches table. It never
touches matches. If the matches themselves are wrong, fix those first with
force_save_gw36.py --gw N, then rerun this.
"""

import sys
from app import app, db
from models import TeamLeagueMatches, TeamLeagueStandings

LEAGUES = ('cities', 'arab', 'libyan')


def get_roster(league):
    if league == 'cities':
        from core.cities_league import TEAMS_FPL_IDS
    elif league == 'arab':
        from core.arab_league import TEAMS_FPL_IDS
    elif league == 'libyan':
        from core.libyan_league import TEAMS_FPL_IDS
    else:
        raise SystemExit(f"Unknown league: {league}")
    return set(TEAMS_FPL_IDS)


def check_matches(gw, matches, roster):
    """Return list of problem strings for one GW's match set."""
    problems = []
    n_teams = len(roster)
    expected_matches = n_teams // 2

    if len(matches) != expected_matches:
        problems.append(f"{len(matches)} matches (expected {expected_matches})")

    seen = []
    for m in matches:
        seen += [m.team1_name, m.team2_name]

    unknown = sorted({t for t in seen if t not in roster})
    if unknown:
        problems.append(f"teams not in roster: {unknown}")

    dupes = sorted({t for t in seen if seen.count(t) > 1})
    if dupes:
        problems.append(f"teams appearing twice: {dupes}")

    missing = sorted(roster - set(seen))
    if missing:
        problems.append(f"teams with no fixture: {missing}")

    return problems


def audit_league(league, verbose=False):
    """Walk the GW chain.

    Returns (expected_by_gw, findings, blocking) where `blocking` lists
    reasons --fix must not run: a missing GW or a malformed match set means
    the derived chain itself is wrong, so rewriting standings from it would
    replace bad numbers with different bad numbers.
    """
    roster = get_roster(league)

    matches_by_gw = {}
    for m in TeamLeagueMatches.query.filter_by(league_type=league).all():
        matches_by_gw.setdefault(m.gameweek, []).append(m)

    standings_by_gw = {}
    for s in TeamLeagueStandings.query.filter_by(league_type=league).all():
        standings_by_gw.setdefault(s.gameweek, {})[s.team_name] = s

    match_gws = sorted(matches_by_gw)
    stand_gws = sorted(standings_by_gw)

    print(f"\n{'=' * 78}\n  {league.upper()}\n{'=' * 78}")
    print(f"  teams in roster        : {len(roster)}")
    print(f"  matches saved for GWs  : {match_gws or 'none'}")
    print(f"  standings saved for GWs: {stand_gws or 'none'}")

    if not match_gws:
        print("  nothing to audit (no matches saved yet)")
        return {}, [], []

    blocking = []

    # Gaps in the match chain break every later GW's cumulative total.
    gaps = [g for g in range(1, max(match_gws)) if g not in matches_by_gw]
    if gaps:
        blocking.append(f"missing match GWs {gaps}")
        print(f"  !! MISSING match GWs   : {gaps}")
        print("     Every GW after a gap has a wrong cumulative total, and the")
        print("     chain below cannot be trusted until they are restored:")
        for g in gaps:
            print(f"       python force_save_gw36.py {league} --gw {g} --apply")

    orphan_standings = [g for g in stand_gws if g not in matches_by_gw]
    if orphan_standings:
        print(f"  !! standings with no matches: {orphan_standings}")

    # Validate each GW's match set
    for gw in match_gws:
        probs = check_matches(gw, matches_by_gw[gw], roster)
        if probs:
            blocking.append(f"GW{gw} matches: {'; '.join(probs)}")
            print(f"  !! GW{gw} matches: {'; '.join(probs)}")

    # Walk the chain from zero
    cum_lp = {t: 0 for t in roster}
    cum_fpl = {t: 0 for t in roster}
    expected_by_gw = {}
    findings = []

    for gw in match_gws:
        for m in matches_by_gw[gw]:
            t1, t2 = m.team1_name, m.team2_name
            p1 = m.team1_points or 0
            p2 = m.team2_points or 0
            if t1 not in cum_lp or t2 not in cum_lp:
                continue  # already reported as unknown team
            if p1 > p2:
                cum_lp[t1] += 3
            elif p2 > p1:
                cum_lp[t2] += 3
            else:
                cum_lp[t1] += 1
                cum_lp[t2] += 1
            cum_fpl[t1] += p1
            cum_fpl[t2] += p2

        expected_by_gw[gw] = (dict(cum_lp), dict(cum_fpl))

        stored = standings_by_gw.get(gw)
        if stored is None:
            findings.append((gw, 'missing', []))
            continue

        bad = []
        for team in sorted(roster):
            row = stored.get(team)
            if row is None:
                bad.append((team, None, None, cum_lp[team], cum_fpl[team]))
                continue
            if (row.league_points or 0) != cum_lp[team] or \
               (row.total_fpl_points or 0) != cum_fpl[team]:
                bad.append((team, row.league_points, row.total_fpl_points,
                            cum_lp[team], cum_fpl[team]))
        if bad:
            findings.append((gw, 'mismatch', bad))

    # Report
    print()
    if not findings and not blocking:
        print("  Standings are consistent with saved matches at every GW.")
    else:
        for gw, kind, bad in findings:
            if kind == 'missing':
                print(f"  GW{gw}: matches saved but NO standings row set")
                continue
            print(f"  GW{gw}: {len(bad)} team(s) disagree with the match-derived total")
            rows = bad if verbose else bad[:5]
            for team, s_lp, s_fpl, e_lp, e_fpl in rows:
                if s_lp is None:
                    print(f"      {team[:24]:24s}  NO ROW           -> "
                          f"lp={e_lp:>3} fpl={e_fpl:>5}")
                else:
                    print(f"      {team[:24]:24s}  stored lp={s_lp:>3} fpl={s_fpl:>5}"
                          f"  ->  should be lp={e_lp:>3} fpl={e_fpl:>5}")
            if not verbose and len(bad) > 5:
                print(f"      ... and {len(bad) - 5} more (use --verbose)")

    # Show what the current table would look like
    last_gw = match_gws[-1]
    exp_lp, exp_fpl = expected_by_gw[last_gw]
    label = "Standings after GW%d derived from matches" % last_gw
    if blocking:
        label += "  [UNRELIABLE — see problems above]"
    print(f"\n  {label}:")
    order = sorted(roster, key=lambda t: (-exp_lp[t], -exp_fpl[t]))
    for i, t in enumerate(order, 1):
        print(f"    {i:>2}. {t[:24]:24s}  {exp_lp[t]:>3} pts   fpl={exp_fpl[t]:>5}")

    return expected_by_gw, findings, blocking


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    fix = '--fix' in sys.argv
    verbose = '--verbose' in sys.argv
    leagues = [a for a in args if a in LEAGUES] or list(LEAGUES)

    print(f"Auditing standings for: {', '.join(leagues)}"
          f"   [{'FIX' if fix else 'read-only'}]")

    with app.app_context():
        plans = {}
        any_problem = False
        for lg in leagues:
            expected, findings, blocking = audit_league(lg, verbose)
            if findings or blocking:
                any_problem = True
            plans[lg] = (expected, findings, blocking)

        if not fix:
            print(f"\n{'=' * 78}")
            if any_problem:
                print("  Problems found. Rerun with --fix to rewrite standings from matches.")
                print("  If the MATCHES are wrong (not just standings), fix those first:")
                print("      python force_save_gw36.py <league> --gw <N> --apply")
            else:
                print("  All audited leagues are consistent.")
            print("=" * 78)
            return

        # A league with blocking problems is excluded even if it has findings:
        # its derived chain is wrong, so "fixing" would just write different
        # wrong numbers. Check this before looking at findings.
        blocked = {lg for lg, p in plans.items() if p[2]}
        if blocked:
            print(f"\n{'=' * 78}")
            print(f"  REFUSING to fix: {', '.join(sorted(blocked))}")
            for lg in sorted(blocked):
                for reason in plans[lg][2]:
                    print(f"    {lg}: {reason}")
            print("  The match rows these standings derive from are incomplete or")
            print("  malformed, so rebuilding would replace bad numbers with different")
            print("  bad ones. Repair the matches first, then rerun --fix.")
            print("=" * 78)

        to_write = {lg: p for lg, p in plans.items() if p[1] and lg not in blocked}
        if not to_write:
            print("\nNothing left to fix.")
            return

        total = sum(len(p[1]) for p in to_write.values())
        print(f"\n{'=' * 78}")
        print(f"  Will rewrite standings for {total} gameweek(s) across "
              f"{len(to_write)} league(s): {', '.join(sorted(to_write))}")
        print("  (matches are NOT touched)")
        print("=" * 78)
        confirm = input("Type 'yes' to rewrite: ")
        if confirm.strip().lower() != 'yes':
            print("Aborted. No changes.")
            return

        for lg, (expected, findings, _) in to_write.items():
            for gw, _kind, _bad in findings:
                exp_lp, exp_fpl = expected[gw]
                TeamLeagueStandings.query.filter_by(
                    league_type=lg, gameweek=gw
                ).delete(synchronize_session=False)
                for team, lp in exp_lp.items():
                    db.session.add(TeamLeagueStandings(
                        league_type=lg,
                        gameweek=gw,
                        team_name=team,
                        league_points=lp,
                        total_fpl_points=exp_fpl[team],
                    ))
                print(f"  {lg} GW{gw}: rewrote {len(exp_lp)} rows")
        db.session.commit()
        print("\nDone. Rerun without --fix to verify.")


if __name__ == '__main__':
    main()
