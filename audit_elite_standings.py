# -*- coding: utf-8 -*-
"""
Audit (and optionally repair) Elite league standings.

Elite differs from the team leagues: FPL runs the H2H league itself, so its
standings endpoint is ground truth. Every manager has a `total` (cumulative
league points) plus matches_won / matches_drawn / matches_lost.

This compares three independent views and shows where they disagree:

  A. FPL's own numbers, checked for internal consistency (total == 3W + 1D)
  B. Cumulative league points recomputed from FPL's per-GW H2H match results
  C. What StandingsHistory holds, at each saved gameweek

(B) is the important one for diagnosing the dashboard, because the dashboard
builds its table as `prev_cumulative_from_DB + this_GW_delta`. If the DB row
for the previous GW is wrong — most often zeroed by the backfill path, which
saves league_points=0 — then every manager's displayed total is wrong even
though FPL's numbers are fine.

    python audit_elite_standings.py              # audit
    python audit_elite_standings.py --verbose    # list every manager
    python audit_elite_standings.py --fix        # rewrite league_points in DB

--fix rewrites StandingsHistory.league_points for every saved GW using the
match-derived cumulative totals (B). It touches nothing else.
"""

import sys
from app import app, db
from models import StandingsHistory
from config import LEAGUE_ID, EXCLUDED_PLAYERS, KNOCKOUT_START_GW
from core.fpl_api import FPL_BASE_URL, fetch_data


def get_league_standings_all():
    """All members of the H2H league, following pagination."""
    members, page, name = {}, 1, None
    while True:
        d = fetch_data(f"{FPL_BASE_URL}/leagues-h2h/{LEAGUE_ID}/standings/?page_standings={page}")
        if not d:
            break
        name = d.get('league', {}).get('name', name)
        st = d.get('standings', {}) or {}
        for r in st.get('results', []):
            members[r['entry']] = r
        if not st.get('has_next'):
            break
        page += 1
    return members, name


def get_matches(gw):
    d = fetch_data(f"{FPL_BASE_URL}/leagues-h2h-matches/league/{LEAGUE_ID}/?event={gw}")
    return (d or {}).get('results', [])


def current_gameweek():
    boot = fetch_data(f"{FPL_BASE_URL}/bootstrap-static/")
    events = boot.get('events', []) if boot else []
    cur = next((e['id'] for e in events if e.get('is_current')), None)
    if cur:
        return cur, events
    finished = [e for e in events if e.get('finished')]
    return (max(finished, key=lambda e: e['id'])['id'] if finished else 1), events


def main():
    fix = '--fix' in sys.argv
    verbose = '--verbose' in sys.argv

    with app.app_context():
        print("=" * 78)
        print(f"  ELITE LEAGUE AUDIT   (league {LEAGUE_ID})")
        print("=" * 78)

        members, league_name = get_league_standings_all()
        if not members:
            print("  Could not fetch league standings from FPL. Aborting.")
            return
        cur_gw, events = current_gameweek()
        finished = [e['id'] for e in events if e.get('finished')]
        print(f"  league        : {league_name}")
        print(f"  members       : {len(members)}")
        print(f"  current GW    : {cur_gw}   finished GWs: {finished or 'none'}")
        if EXCLUDED_PLAYERS:
            print(f"  excluded      : {EXCLUDED_PLAYERS}")

        # ---- A. FPL internal consistency -------------------------------
        print(f"\n{'-' * 78}\n  A. FPL's own numbers (total should equal 3*W + 1*D)\n{'-' * 78}")
        bad_fpl = []
        for eid, r in members.items():
            w, d_, l = r.get('matches_won', 0), r.get('matches_drawn', 0), r.get('matches_lost', 0)
            expect = 3 * w + d_
            if int(r.get('total', 0) or 0) != expect:
                bad_fpl.append((r.get('player_name', ''), r.get('total'), expect, w, d_, l))
        if bad_fpl:
            print(f"  !! {len(bad_fpl)} manager(s) where FPL's own total != 3W+1D")
            print("     (normal during the knockout phase, when FPL stops adding to the")
            print("      round-robin total — otherwise unexpected)")
            for n, t, e, w, d_, l in bad_fpl[:8]:
                print(f"       {n[:26]:26s} total={t:>3} but 3*{w}+{d_} = {e}")
        else:
            print("  OK: FPL's totals are self-consistent.")

        # ---- B. Recompute cumulative LP from per-GW matches -------------
        print(f"\n{'-' * 78}\n  B. Cumulative league points recomputed from H2H matches\n{'-' * 78}")
        played = [g for g in sorted(finished) if g < cur_gw] + \
                 ([cur_gw] if cur_gw in finished else [])
        played = sorted(set(played))
        if not played:
            print("  No finished gameweeks yet — nothing to recompute.")
            return

        cum = {eid: 0 for eid in members}
        cum_by_gw = {}
        no_match_gws = []
        for gw in played:
            ms = get_matches(gw)
            if not ms:
                no_match_gws.append(gw)
                cum_by_gw[gw] = dict(cum)
                continue
            for m in ms:
                e1, e2 = m.get('entry_1_entry'), m.get('entry_2_entry')
                p1 = m.get('entry_1_points', 0) or 0
                p2 = m.get('entry_2_points', 0) or 0
                # Knockout rounds don't add to the round-robin total
                if gw >= KNOCKOUT_START_GW:
                    continue
                if e1 in cum:
                    cum[e1] += 3 if p1 > p2 else (1 if p1 == p2 else 0)
                if e2 in cum:
                    cum[e2] += 3 if p2 > p1 else (1 if p1 == p2 else 0)
            cum_by_gw[gw] = dict(cum)
        if no_match_gws:
            print(f"  !! no H2H matches returned for GW(s) {no_match_gws} — "
                  f"totals after them are understated")

        mismatch_b = []
        for eid, r in members.items():
            fpl_total = int(r.get('total', 0) or 0)
            if cum[eid] != fpl_total:
                mismatch_b.append((r.get('player_name', ''), fpl_total, cum[eid]))
        if mismatch_b:
            print(f"  !! {len(mismatch_b)} manager(s) where recomputed != FPL total")
            for n, f, c in (mismatch_b if verbose else mismatch_b[:8]):
                print(f"       {n[:26]:26s} FPL={f:>3}  recomputed={c:>3}  ({c - f:+d})")
            if not verbose and len(mismatch_b) > 8:
                print(f"       ... and {len(mismatch_b) - 8} more (--verbose)")
        else:
            print(f"  OK: recomputed totals match FPL for all {len(members)} managers.")

        # ---- C. StandingsHistory vs recomputed --------------------------
        print(f"\n{'-' * 78}\n  C. StandingsHistory (what the dashboard builds on)\n{'-' * 78}")
        saved_gws = sorted({r[0] for r in db.session.query(
            StandingsHistory.gameweek).distinct().all()})
        print(f"  saved GWs: {saved_gws or 'none'}")
        if not saved_gws:
            print("  Nothing saved yet — the dashboard falls back to FPL's `total`,")
            print("  which section B shows is correct. No action needed.")
            return

        missing = [g for g in played if g not in saved_gws]
        if missing:
            print(f"  !! finished GWs with NO saved standings: {missing}")

        problems = {}
        for gw in saved_gws:
            if gw not in cum_by_gw:
                continue
            rows = StandingsHistory.query.filter_by(gameweek=gw).all()
            bad = []
            zeros = 0
            for row in rows:
                want = cum_by_gw[gw].get(row.entry_id)
                if want is None:
                    continue
                have = row.league_points or 0
                if have != want:
                    bad.append((row.player_name, have, want))
                if have == 0:
                    zeros += 1
            if bad:
                problems[gw] = bad
                allzero = " — ALL ROWS ZERO (backfill wrote league_points=0)" \
                    if zeros == len(rows) and rows else ""
                print(f"  !! GW{gw}: {len(bad)}/{len(rows)} rows wrong{allzero}")
                for n, h, w in (bad if verbose else bad[:5]):
                    print(f"       {str(n)[:26]:26s} stored={h:>3}  should be {w:>3}")
                if not verbose and len(bad) > 5:
                    print(f"       ... and {len(bad) - 5} more (--verbose)")

        if not problems:
            print("  OK: every saved GW matches the match-derived cumulative totals.")

        # ---- Verdict ----------------------------------------------------
        print(f"\n{'=' * 78}")
        latest = max(saved_gws)
        if problems:
            print("  The dashboard reads the PREVIOUS GW's league_points as its base,")
            print("  so wrong rows here shift every manager's displayed total.")
            if latest in problems:
                print(f"  GW{latest} is wrong and is the base for GW{latest + 1} — "
                      f"this is what you are seeing on the page.")
            if not fix:
                print("\n  Rerun with --fix to rewrite league_points from FPL's match results.")
        else:
            print("  Standings history is consistent with FPL. If the page still looks")
            print("  wrong, the issue is in display, not stored data — say so and we'll")
            print("  look at the dashboard's live projection instead.")
        print("=" * 78)

        if not fix or not problems:
            return

        if mismatch_b or no_match_gws:
            print("\n  REFUSING to fix: the recomputed totals themselves disagree with")
            print("  FPL (section B), so writing them would not be an improvement.")
            return

        total_rows = sum(len(v) for v in problems.values())
        confirm = input(f"\nType 'yes' to rewrite league_points on {total_rows} row(s) "
                        f"across GWs {sorted(problems)}: ")
        if confirm.strip().lower() != 'yes':
            print("Aborted. No changes.")
            return

        for gw in sorted(problems):
            n = 0
            for row in StandingsHistory.query.filter_by(gameweek=gw).all():
                want = cum_by_gw[gw].get(row.entry_id)
                if want is not None and (row.league_points or 0) != want:
                    row.league_points = want
                    n += 1
            print(f"  GW{gw}: updated {n} row(s)")
        db.session.commit()
        print("\nDone. Rerun without --fix to verify, then reload the dashboard.")


if __name__ == '__main__':
    main()
