# -*- coding: utf-8 -*-
"""
Option C replay: recompute all eliminations from GW23 onwards using
correct net (post-hit) GW points.

Context: core/the100.py:505 had a bug where, after a GW's 12h post-finish
buffer, stored gw_points were gross (pre-hit) instead of net. Eliminations
auto-save after the 24h buffer, so every stored elimination from GW20-32
used gross rankings. The audit showed GW20-22 were unaffected (no close
calls on the cut line); GW23, 26, 27, 29 had the wrong managers eliminated;
the rest had right managers but wrong point values.

This script replays the cascade from GW23 forward:
  - Walks GW23 -> max_saved_gw, recomputes net points for each alive
    manager, picks bottom 6 by net rank
  - Alive pool evolves as managers are eliminated in the replay, so
    a different-from-DB GW23 outcome ripples into GW24+
  - GW20-22 are preserved as-is
  - Tie-break on net points is entry_id ascending (deterministic but
    arbitrary -- production had no defined tie-break either)

Dry-run by default. --apply to modify the DB.

Run from Render Shell:
    python replay_the100_eliminations.py
    python replay_the100_eliminations.py --apply
"""

import sys
from app import app, db
from models import (
    The100EliminationResult,
    The100QualifiedManager,
    save_the100_elimination,
    save_the100_gameweek_ranking,
)
from audit_the100_eliminations import recompute_gw
from core.the100 import (
    get_cookies,
    fetch_json,
    ELIMINATIONS_PER_GW,
    ELIMINATION_END_GW,
)

HISTORY_START_GW = 20  # first GW of elimination phase (for ranking history)
REPLAY_START_GW = 23   # first GW where audit found a set mismatch


def main():
    apply_mode = '--apply' in sys.argv

    with app.app_context():
        cookies = get_cookies()

        # Replay end = max of (highest saved elim GW, highest FPL-finished elim GW).
        # Including a finished GW that isn't saved yet (e.g. auto-save still within
        # its 24h post-finish buffer) lets us close it out in one pass.
        max_saved_gw = db.session.query(
            db.func.max(The100EliminationResult.gameweek)
        ).scalar() or 0

        bootstrap = fetch_json("https://fantasy.premierleague.com/api/bootstrap-static/", cookies)
        if not bootstrap:
            print("Failed to fetch bootstrap; aborting.")
            return
        finished_gws_in_range = [
            e['id'] for e in bootstrap['events']
            if REPLAY_START_GW <= e['id'] <= ELIMINATION_END_GW
            and e.get('finished') and e.get('data_checked')
        ]
        max_finished_gw = max(finished_gws_in_range) if finished_gws_in_range else 0

        replay_end_gw = max(max_saved_gw, max_finished_gw)
        if replay_end_gw < REPLAY_START_GW:
            print("Nothing to replay (no finished or saved elim GWs >= 23).")
            return

        print(f"Max saved elim GW:    {max_saved_gw}")
        print(f"Max finished elim GW: {max_finished_gw} (finished AND data_checked)")
        print(f"Replaying GW{REPLAY_START_GW}..GW{replay_end_gw}\n")

        # Load all qualified managers and current DB state
        all_qualified = The100QualifiedManager.query.all()
        qualified_by_id = {m.entry_id: m for m in all_qualified}
        print(f"Qualified managers: {len(all_qualified)}")

        current_elims = The100EliminationResult.query.filter(
            The100EliminationResult.gameweek.between(REPLAY_START_GW, replay_end_gw)
        ).all()
        current_by_gw = {}
        for r in current_elims:
            current_by_gw.setdefault(r.gameweek, set()).add(r.entry_id)

        old_eliminated_by_gw = {
            m.entry_id: m.eliminated_gw
            for m in all_qualified
            if m.eliminated_gw is not None and m.eliminated_gw >= REPLAY_START_GW
        }

        # Pre-replay pass for GW20-22: these are "locked" (audit confirmed correct).
        # We recompute rankings only so the history view has data for them; we do
        # NOT change their eliminations.
        full_rankings = {}  # gw -> list of {entry_id, manager_name, team_name, gw_points, gw_rank, was_eliminated}
        alive = {m.entry_id: m for m in all_qualified}

        def build_ranking_rows(gw, recomputed, eliminated_ids):
            ranked = sorted(recomputed.items(), key=lambda kv: (-kv[1]['net'], kv[0]))
            rows = []
            for rank, (eid, data) in enumerate(ranked, start=1):
                rows.append({
                    'entry_id': eid,
                    'manager_name': data['manager_name'],
                    'team_name': data['team_name'],
                    'gw_points': data['net'],
                    'gw_rank': rank,
                    'was_eliminated': eid in eliminated_ids,
                })
            return rows, ranked

        pre_gws = [gw for gw in range(HISTORY_START_GW, REPLAY_START_GW) if gw <= replay_end_gw]
        for gw in pre_gws:
            print(f"--- GW{gw} ranking (pre-replay, preserving DB eliminations) ---")
            alive_list = list(alive.values())
            recomputed = recompute_gw(gw, alive_list, cookies)
            missing = {m.entry_id for m in alive_list} - set(recomputed.keys())
            if missing:
                # For pre-replay GWs we can tolerate a missing manager (audit said
                # GW20 had one). Exclude them from ranking rather than aborting.
                print(f"  WARN: could not fetch picks for {len(missing)} managers "
                      f"(excluding from ranking): {sorted(missing)}")
            # Truth for "was eliminated this GW" = current DB state
            eliminated_ids = current_by_gw.get(gw, set())
            rows, _ = build_ranking_rows(gw, recomputed, eliminated_ids)
            full_rankings[gw] = rows
            # Remove the DB-eliminated from alive (matches the truth we're preserving)
            for eid in eliminated_ids:
                alive.pop(eid, None)
            print(f"  Ranked {len(rows)} managers; {len(eliminated_ids)} eliminated this GW; "
                  f"alive after GW{gw}: {len(alive)}\n")

        # Safety check: after pre-replay, alive pool size should match what replay expects
        locked_eliminated = {
            m.entry_id for m in all_qualified
            if m.eliminated_gw is not None and m.eliminated_gw < REPLAY_START_GW
        }
        expected_alive = len(all_qualified) - len(locked_eliminated)
        if len(alive) != expected_alive:
            print(f"WARN: after pre-replay, alive={len(alive)} but expected {expected_alive} "
                  f"based on DB locked_eliminated. Proceeding with DB state for replay.")
            alive = {m.entry_id: m for m in all_qualified if m.entry_id not in locked_eliminated}

        # Replay (this updates alive as managers are eliminated)
        replayed_elims = {}       # gw -> list of elim dicts (ready for save_the100_elimination)
        new_eliminated_by_gw = {} # entry_id -> gw (replayed)

        for gw in range(REPLAY_START_GW, replay_end_gw + 1):
            print(f"--- GW{gw} replay ---")
            alive_list = list(alive.values())
            recomputed = recompute_gw(gw, alive_list, cookies)

            # Bail cleanly if API gave incomplete data
            missing = {m.entry_id for m in alive_list} - set(recomputed.keys())
            if missing:
                print(f"  ABORT: could not fetch picks for {len(missing)} managers: "
                      f"{sorted(missing)}")
                print("  Fix API fetch issue and rerun. No changes applied.")
                return

            # Rank: net desc, tie-break entry_id asc
            ranked = sorted(
                recomputed.items(),
                key=lambda kv: (-kv[1]['net'], kv[0])
            )
            total_alive = len(ranked)
            eliminated_this_gw = ranked[-ELIMINATIONS_PER_GW:]
            elim_ids_this_gw = {eid for eid, _ in eliminated_this_gw}

            # Full ranking rows for history
            full_rankings[gw], _ = build_ranking_rows(gw, recomputed, elim_ids_this_gw)

            # Build elim dicts; gw_rank = rank within GW ranking (matches production)
            replayed_elims[gw] = []
            for idx, (eid, data) in enumerate(eliminated_this_gw):
                rank_in_gw = total_alive - ELIMINATIONS_PER_GW + idx + 1
                replayed_elims[gw].append({
                    'entry_id': eid,
                    'manager_name': data['manager_name'],
                    'team_name': data['team_name'],
                    'gw_points': data['net'],
                    'gw_rank': rank_in_gw,
                })
                new_eliminated_by_gw[eid] = gw
                del alive[eid]

            # Per-GW diff
            new_ids = {e['entry_id'] for e in replayed_elims[gw]}
            old_ids = current_by_gw.get(gw, set())
            added = new_ids - old_ids
            removed = old_ids - new_ids
            for e in replayed_elims[gw]:
                flag = "NEW" if e['entry_id'] in added else "same"
                name = e['manager_name'][:28]
                print(f"  {name:28s} (entry={e['entry_id']:>8d}) "
                      f"net={e['gw_points']:>3d} rank={e['gw_rank']:>3d}  [{flag}]")
            if added or removed:
                print(f"  Added vs DB: {sorted(added)}")
                print(f"  Removed vs DB: {sorted(removed)}")
            print(f"  Alive after GW{gw}: {len(alive)}\n")

        # Overall change summary across the whole replay
        previously = set(old_eliminated_by_gw.keys())
        now = set(new_eliminated_by_gw.keys())
        reinstated = previously - now
        newly_out = now - previously
        moved = {
            eid: (old_eliminated_by_gw[eid], new_eliminated_by_gw[eid])
            for eid in previously & now
            if old_eliminated_by_gw[eid] != new_eliminated_by_gw[eid]
        }
        unchanged = (previously & now) - set(moved.keys())

        print("=" * 60)
        print("  REPLAY SUMMARY (replay vs current DB)")
        print("=" * 60)
        print(f"Reinstated (were eliminated, now alive):  {len(reinstated)}")
        for eid in sorted(reinstated):
            m = qualified_by_id.get(eid)
            old_gw = old_eliminated_by_gw[eid]
            print(f"  {m.manager_name if m else '?':30s} (entry={eid}) "
                  f"was eliminated GW{old_gw} -> now alive")
        print(f"\nNewly eliminated (were alive, now out):   {len(newly_out)}")
        for eid in sorted(newly_out):
            m = qualified_by_id.get(eid)
            new_gw = new_eliminated_by_gw[eid]
            print(f"  {m.manager_name if m else '?':30s} (entry={eid}) "
                  f"was alive -> now eliminated GW{new_gw}")
        print(f"\nShifted to different GW:                  {len(moved)}")
        for eid, (old_gw, new_gw) in sorted(moved.items(), key=lambda kv: kv[0]):
            m = qualified_by_id.get(eid)
            print(f"  {m.manager_name if m else '?':30s} (entry={eid}): "
                  f"GW{old_gw} -> GW{new_gw}")
        print(f"\nSame-GW elimination (unchanged):          {len(unchanged)}")

        if not apply_mode:
            print("\nDry run. Rerun with --apply to write changes.")
            return

        # Apply
        print("\n>>> APPLY MODE <<<")
        print("This will:")
        print(f"  1. Delete all The100EliminationResult rows GW{REPLAY_START_GW}-{replay_end_gw}")
        print(f"  2. Reset eliminated_gw=NULL for managers currently out in GW{REPLAY_START_GW}-{replay_end_gw}")
        print(f"  3. Save replayed eliminations for GW{REPLAY_START_GW}-{replay_end_gw}")
        print(f"\n  {len(reinstated)} managers will be reinstated")
        print(f"  {len(newly_out)} managers will be newly eliminated")
        print(f"  {len(moved)} managers will shift elimination GW")
        confirm = input("\nType 'yes' to proceed: ")
        if confirm.strip().lower() != 'yes':
            print("Aborted. No changes made.")
            return

        # 1. Delete old elim rows
        deleted = The100EliminationResult.query.filter(
            The100EliminationResult.gameweek.between(REPLAY_START_GW, replay_end_gw)
        ).delete(synchronize_session=False)
        print(f"Deleted {deleted} elimination rows")

        # 2. Reset eliminated_gw for managers currently marked out in replay range
        reset_count = The100QualifiedManager.query.filter(
            The100QualifiedManager.eliminated_gw.between(REPLAY_START_GW, replay_end_gw)
        ).update({'eliminated_gw': None, 'final_rank': None}, synchronize_session=False)
        db.session.commit()
        print(f"Reset eliminated_gw on {reset_count} managers")

        # 3. Save replayed eliminations (this also sets eliminated_gw on the manager)
        for gw in sorted(replayed_elims.keys()):
            ok = save_the100_elimination(gw, replayed_elims[gw])
            marker = "OK" if ok else "FAIL"
            print(f"  GW{gw}: saved {len(replayed_elims[gw])} eliminations [{marker}]")

        # 4. Save full per-GW rankings for the history view
        for gw in sorted(full_rankings.keys()):
            ok = save_the100_gameweek_ranking(gw, full_rankings[gw])
            marker = "OK" if ok else "FAIL"
            print(f"  GW{gw}: saved full ranking ({len(full_rankings[gw])} rows) [{marker}]")

        print("\nReplay complete. The100EliminationResult, The100QualifiedManager, "
              "and The100GameweekRanking are now consistent.")


if __name__ == '__main__':
    main()
