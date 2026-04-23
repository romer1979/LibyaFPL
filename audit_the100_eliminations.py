# -*- coding: utf-8 -*-
"""
Audit script for The 100 league elimination phase.

Context: core/the100.py:505 had a bug where, once a GW passed the 12h buffer,
`gw_points` was read as gross (FPL `entry_history.points`) instead of net
(subtracting `event_transfers_cost`). Eliminations are auto-saved AFTER the
24h buffer, so every stored The100EliminationResult row used the buggy gross
ranking. This script recomputes net GW points for all qualified managers
for each already-saved elimination GW and reports:
  - Stored `gw_points` values that are wrong (gross vs. net)
  - GWs where the set of 6 eliminated managers differs under net ranking

Read-only by default. Pass `--apply-points-fix` to update the `gw_points`
column for rows whose eliminated-set is unchanged (safe fix). A change in
the eliminated set is NOT auto-fixed — that requires human judgment.

Run from Render Shell:
    python audit_the100_eliminations.py
    python audit_the100_eliminations.py --apply-points-fix
"""

import sys
from app import app, db
from models import The100EliminationResult, The100QualifiedManager
from core.the100 import (
    calculate_live_points,
    calculate_projected_bonus,
    fetch_json,
    fetch_multiple_parallel,
    get_cookies,
    ELIMINATIONS_PER_GW,
)


def build_player_info(bootstrap):
    return {
        p['id']: {
            'name': p['web_name'],
            'team': p['team'],
            'position': p['element_type'],
        }
        for p in bootstrap['elements']
    }


def build_live_elements(live_data, fixtures):
    """Mirror of get_elimination_standings' live_elements construction."""
    bonus_points = calculate_projected_bonus(live_data, fixtures)
    live_elements = {}
    for elem in live_data['elements']:
        elem_id = elem['id']
        official_bonus = elem['stats'].get('bonus', 0)
        projected_bonus = bonus_points.get(elem_id, 0)
        actual_bonus = official_bonus if official_bonus > 0 else projected_bonus
        base_points = elem['stats']['total_points'] - official_bonus
        live_elements[elem_id] = {
            'total_points': base_points + actual_bonus,
            'minutes': elem['stats']['minutes'],
            'bonus': actual_bonus,
        }
    return live_elements


def recompute_gw(gw, qualified_for_gw, cookies):
    """
    Recompute net GW points for each qualified manager in `qualified_for_gw`.
    Returns dict entry_id -> {'net': int, 'gross': int, 'hit': int, 'manager_name': str}.
    """
    fixtures = fetch_json(f"https://fantasy.premierleague.com/api/fixtures/?event={gw}", cookies) or []
    live_data = fetch_json(f"https://fantasy.premierleague.com/api/event/{gw}/live/", cookies)
    bootstrap = fetch_json("https://fantasy.premierleague.com/api/bootstrap-static/", cookies)
    if not (live_data and bootstrap):
        raise RuntimeError(f"Failed to fetch live/bootstrap for GW{gw}")

    player_info = build_player_info(bootstrap)
    live_elements = build_live_elements(live_data, fixtures)

    pick_urls = [
        f"https://fantasy.premierleague.com/api/entry/{m.entry_id}/event/{gw}/picks/"
        for m in qualified_for_gw
    ]
    all_picks = fetch_multiple_parallel(pick_urls, cookies)

    results = {}
    for m in qualified_for_gw:
        entry_id = m.entry_id
        url = f"https://fantasy.premierleague.com/api/entry/{entry_id}/event/{gw}/picks/"
        picks_data = all_picks.get(url)
        if not picks_data:
            print(f"    WARN: no picks for entry {entry_id} in GW{gw}")
            continue
        gross = picks_data.get('entry_history', {}).get('points', 0)
        hit = picks_data.get('entry_history', {}).get('event_transfers_cost', 0)
        net = calculate_live_points(picks_data, live_elements, player_info, fixtures)
        results[entry_id] = {
            'net': net,
            'gross': gross,
            'hit': hit,
            'manager_name': m.manager_name,
            'team_name': m.team_name,
        }
    return results


def audit():
    apply_fix = '--apply-points-fix' in sys.argv

    with app.app_context():
        # Saved elimination GWs
        saved_gws = sorted({r.gameweek for r in The100EliminationResult.query.all()})
        if not saved_gws:
            print("No elimination results in DB. Nothing to audit.")
            return
        print(f"Saved elimination GWs: {saved_gws}\n")

        # All qualified managers (for ranking we need everyone still alive at each GW)
        all_qualified = The100QualifiedManager.query.all()
        print(f"Qualified managers in DB: {len(all_qualified)}\n")

        cookies = get_cookies()

        per_gw_points_diffs = []   # rows where stored gw_points != recomputed net
        per_gw_set_diffs = []      # GWs where the eliminated set changes

        for gw in saved_gws:
            print(f"--- GW{gw} ---")
            # Exclude anyone already eliminated in a prior GW
            qualified_for_gw = [
                m for m in all_qualified
                if (m.eliminated_gw is None or m.eliminated_gw >= gw)
            ]
            print(f"  Managers alive at start of GW{gw}: {len(qualified_for_gw)}")

            recomputed = recompute_gw(gw, qualified_for_gw, cookies)

            # Rank by net desc; ties broken deterministically by entry_id for stability
            ranked = sorted(
                recomputed.items(),
                key=lambda kv: (-kv[1]['net'], kv[0]),
            )
            # Bottom ELIMINATIONS_PER_GW should have been eliminated this GW
            should_be_eliminated = {eid for eid, _ in ranked[-ELIMINATIONS_PER_GW:]}

            stored_rows = The100EliminationResult.query.filter_by(gameweek=gw).all()
            stored_ids = {r.entry_id for r in stored_rows}

            # Point-value diffs within stored rows
            for r in stored_rows:
                rec = recomputed.get(r.entry_id)
                if not rec:
                    continue
                if r.gw_points != rec['net']:
                    per_gw_points_diffs.append({
                        'gw': gw,
                        'entry_id': r.entry_id,
                        'name': r.manager_name,
                        'stored': r.gw_points,
                        'recomputed_net': rec['net'],
                        'gross': rec['gross'],
                        'hit': rec['hit'],
                    })

            # Set diffs
            missing_from_stored = should_be_eliminated - stored_ids  # should have been elim'd but weren't
            wrongly_eliminated = stored_ids - should_be_eliminated   # were elim'd but shouldn't have been
            if missing_from_stored or wrongly_eliminated:
                per_gw_set_diffs.append({
                    'gw': gw,
                    'should': should_be_eliminated,
                    'stored': stored_ids,
                    'missing': missing_from_stored,
                    'wrong': wrongly_eliminated,
                })
                print(f"  *** ELIMINATED-SET MISMATCH ***")
                for eid in wrongly_eliminated:
                    rec = recomputed.get(eid, {})
                    print(f"    Stored as eliminated but net-rank says safe: "
                          f"entry={eid} net={rec.get('net')} "
                          f"name={rec.get('manager_name')}")
                for eid in missing_from_stored:
                    rec = recomputed.get(eid, {})
                    print(f"    Should have been eliminated but wasn't:    "
                          f"entry={eid} net={rec.get('net')} "
                          f"name={rec.get('manager_name')}")
            else:
                print("  Eliminated set: OK")

        # Final report
        print("\n" + "=" * 60)
        print("  SUMMARY")
        print("=" * 60)
        print(f"\nStored gw_points values that disagree with net recomputation: "
              f"{len(per_gw_points_diffs)}")
        for d in per_gw_points_diffs:
            print(f"  GW{d['gw']} entry={d['entry_id']} {d['name']:20s} "
                  f"stored={d['stored']:>4} gross={d['gross']:>4} hit={d['hit']:>3} "
                  f"recomputed_net={d['recomputed_net']:>4}")

        print(f"\nGWs where the eliminated set would change under net ranking: "
              f"{len(per_gw_set_diffs)}")
        if per_gw_set_diffs:
            print("  >>> These require manual decisions. NOT auto-fixing. <<<")
            for d in per_gw_set_diffs:
                print(f"    GW{d['gw']}: wrongly_eliminated={sorted(d['wrong'])} "
                      f"missed={sorted(d['missing'])}")

        if not apply_fix:
            print("\nRead-only run. To update gw_points values for GWs whose "
                  "eliminated set is unchanged, rerun with --apply-points-fix")
            return

        # Apply points-only fix, but ONLY for GWs whose eliminated set is unchanged
        bad_gws = {d['gw'] for d in per_gw_set_diffs}
        fixable = [d for d in per_gw_points_diffs if d['gw'] not in bad_gws]
        skipped = [d for d in per_gw_points_diffs if d['gw'] in bad_gws]

        print(f"\nApplying points fix for {len(fixable)} rows "
              f"(skipping {len(skipped)} in set-diff GWs).")
        if skipped:
            print("  Skipped rows (set-diff GWs need manual resolution first):")
            for d in skipped:
                print(f"    GW{d['gw']} entry={d['entry_id']} "
                      f"stored={d['stored']} -> net={d['recomputed_net']}")

        confirm = input("\nProceed with points-only update? (yes/no): ")
        if confirm.strip().lower() != 'yes':
            print("Aborted. No changes made.")
            return

        for d in fixable:
            row = The100EliminationResult.query.filter_by(
                gameweek=d['gw'], entry_id=d['entry_id']
            ).first()
            if row:
                row.gw_points = d['recomputed_net']
        db.session.commit()
        print(f"Updated {len(fixable)} rows.")


if __name__ == '__main__':
    audit()
