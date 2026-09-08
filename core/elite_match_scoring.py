"""Deterministic FPL score attribution and gap-only updates; no language model."""

LABELS = {
    'goals_scored': 'أهداف', 'assists': 'تمريرات حاسمة', 'clean_sheets': 'شباك نظيفة',
    'goals_conceded': 'أهداف مستقبلة', 'own_goals': 'أهداف عكسية', 'penalties_saved': 'صد ركلات جزاء',
    'penalties_missed': 'ركلات جزاء ضائعة', 'yellow_cards': 'بطاقات صفراء', 'red_cards': 'بطاقات حمراء',
    'saves': 'تصديات', 'bonus': 'بونص', 'defensive_contribution': 'مساهمات دفاعية',
    'defensive_contributions': 'مساهمات دفاعية', 'adjustment': 'تصحيح نقاط FPL',
}


def player_scores(elements, fixtures, settled=False):
    """Use FPL's per-fixture points, including unknown future scoring categories.

    Bonus is projected per real fixture, never from a player's combined DGW BPS.
    Unexplained residual points are retained as an explicit correction component.
    """
    scores = {}
    for element in elements:
        parts = {}
        for fixture in element.get('explain', []):
            for stat in fixture['stats']:
                key = f"{fixture['fixture']}:{stat['identifier']}"
                parts[key] = dict(stat, fixture=fixture['fixture'], provisional=False)
        official = element['stats']['total_points']
        residual = official - sum(part['points'] for part in parts.values())
        if residual:
            parts['0:adjustment'] = {'identifier': 'adjustment', 'fixture': 0, 'points': residual, 'value': 0, 'provisional': False}
        scores[element['id']] = {'points': official, 'minutes': element['stats']['minutes'], 'parts': parts}
    if not settled:
        for fixture in fixtures:
            if not fixture.get('started'):
                continue
            bps = next((s for s in fixture.get('stats', []) if s['identifier'] == 'bps'), None)
            if not bps:
                continue
            values = {row['element']: row['value'] for row in bps['h'] + bps['a']
                      if row['element'] in scores and any(
                          part['identifier'] == 'minutes' and part['fixture'] == fixture['id'] and part['value'] > 0
                          for part in scores[row['element']]['parts'].values())}
            for pid, value in values.items():
                part_key = f"{fixture['id']}:bonus"
                score = scores[pid]
                if '0:adjustment' in score['parts']:
                    continue  # Incomplete explanation: retain the authoritative total.
                projected = max(0, 3 - sum(other > value for other in values.values()))
                previous = score['parts'].get(part_key, {}).get('points', 0)
                score['points'] += projected - previous
                score['parts'][part_key] = {'identifier': 'bonus', 'fixture': fixture['id'],
                    'points': projected, 'value': projected, 'provisional': True}
    return scores


def lineup(payload, players, scores, fixtures, settled=False, team_rules=False):
    picks = sorted(payload['picks'], key=lambda p: p['position'])
    ids = [p['element'] for p in picks]
    if len(ids) != 15 or len(set(ids)) != 15 or any(pid not in players or pid not in scores for pid in ids):
        raise ValueError('لم تكتمل بيانات التشكيلة أو النقاط بعد.')
    chip = payload.get('active_chip')
    captain = next((p['element'] for p in picks if p.get('is_captain')), None)
    vice = next((p['element'] for p in picks if p.get('is_vice_captain')), None)
    active, substitutions = ids[:11].copy(), []

    def done(pid):
        games = [f for f in fixtures if players[pid]['club'] in (f['team_h'], f['team_a'])]
        # A postponed/unplayed fixture is not proof of DNP until the GW settles.
        return settled or not games or all(f.get('finished') or f.get('finished_provisional') for f in games)

    if settled and not (team_rules and chip == 'bboost'):
        # Published FPL multipliers/auto-subs are authoritative after data_checked.
        weights = {p['element']: p['multiplier'] for p in picks}
        if team_rules:
            weights = {pid: min(2, multiplier) for pid, multiplier in weights.items()}
        substitutions = payload.get('automatic_subs', [])
        for sub in substitutions:
            if sub['element_out'] in active:
                active[active.index(sub['element_out'])] = sub['element_in']
    else:
        if chip != 'bboost' or team_rules:
            absent = [pid for pid in active if scores[pid]['minutes'] == 0 and done(pid)]
            used = set()
            for incoming in ids[11:]:
                if scores[incoming]['minutes'] == 0 and done(incoming):
                    continue
                for outgoing in absent:
                    if outgoing in used:
                        continue
                    ipos, opos = players[incoming]['position'], players[outgoing]['position']
                    if (ipos == 1) != (opos == 1):
                        continue
                    trial = [incoming if pid == outgoing else pid for pid in active]
                    counts = [sum(players[pid]['position'] == pos for pid in trial) for pos in (1, 2, 3, 4)]
                    if not (counts[0] == 1 and 3 <= counts[1] <= 5 and 2 <= counts[2] <= 5 and 1 <= counts[3] <= 3):
                        continue
                    used.add(outgoing)
                    # Reserve a higher-priority substitute until their fixtures finish.
                    if scores[incoming]['minutes'] > 0:
                        active = trial
                        substitutions.append({'element_in': incoming, 'element_out': outgoing})
                    break
        weights = {pid: int((chip == 'bboost' and not team_rules) or pid in active) for pid in ids}
        effective = captain
        if captain and scores[captain]['minutes'] == 0 and done(captain):
            effective = vice
        if effective and weights.get(effective) and scores[effective]['minutes'] > 0:
            weights[effective] = 3 if chip == '3xc' and not team_rules else 2
    hits = payload.get('entry_history', {}).get('event_transfers_cost', 0)
    rows = []
    for pid in ids:
        score = scores[pid]
        rows.append(dict(players[pid], raw=score['points'], minutes=score['minutes'], weight=weights[pid],
                         points=score['points'] * weights[pid], captain=pid == captain, vice=pid == vice,
                         active=pid in active, parts=score['parts']))
    return {'players': rows, 'score': sum(p['points'] for p in rows) - hits,
            'hits': hits, 'chip': chip, 'substitutions': substitutions}


def changes(before, after):
    """One observed batch; rows have no invented event order or match minute."""
    maps = [[{str(p['id']): p for p in side['players']} for side in state['teams']] for state in (before, after)]
    ids = sorted(set().union(*(side.keys() for state in maps for side in state)), key=int)
    events = []
    for pid in ids:
        old = [side.get(pid, {}) for side in maps[0]]
        new = [side.get(pid, {}) for side in maps[1]]
        delta = [new[i].get('points', 0) - old[i].get('points', 0) for i in (0, 1)]
        if delta[0] == delta[1]:
            continue
        prior = next((p for p in old if p), {})
        current = next((p for p in new if p), prior)
        parts = []
        for key in sorted(set(prior.get('parts', {})) | set(current.get('parts', {}))):
            a, b = prior.get('parts', {}).get(key, {}), current.get('parts', {}).get(key, {})
            diff = b.get('points', 0) - a.get('points', 0)
            if not diff:
                continue
            identifier = b.get('identifier', a.get('identifier'))
            label = LABELS.get(identifier, identifier.replace('_', ' '))
            if identifier == 'minutes':
                label = 'بلوغ 60 دقيقة' if b.get('value', 0) >= 60 and a.get('value', 0) < 60 else 'نقاط المشاركة / تصحيح الدقائق'
            parts.append({'label': label, 'delta': diff, 'fixture': b.get('fixture', a.get('fixture')),
                          'fixture_label': b.get('fixture_label', a.get('fixture_label', 'تحديث المباراة')),
                          'provisional': b.get('provisional', False) or a.get('provisional', False)})
        weight_changed = any(a.get('weight', 0) != b.get('weight', 0) for a, b in zip(old, new))
        reasons = []
        for a, b in zip(old, new):
            if a.get('weight', 0) == b.get('weight', 0):
                continue
            if b.get('vice') and b.get('weight', 0) > 1:
                reasons.append('انتقال مضاعف الكابتن إلى النائب')
            elif b.get('captain') and b.get('weight', 0) > 1:
                reasons.append('احتساب مضاعف الكابتن')
            elif b.get('active') and not a.get('active'):
                reasons.append('دخول بديل تلقائي')
            elif a.get('active') and not b.get('active'):
                reasons.append('خروج لاعب بالتبديل التلقائي')
            else:
                reasons.append('تحديث المضاعف المحتسب')
        events.append({'player': current['name'], 'id': int(pid), 'delta': delta, 'gap_delta': delta[0] - delta[1],
                       'parts': parts, 'lineup_change': weight_changed, 'lineup_reasons': list(dict.fromkeys(reasons))})
    hit_delta = [before['teams'][i]['hits'] - after['teams'][i]['hits'] for i in (0, 1)]
    if hit_delta[0] != hit_delta[1]:
        events.append({'player': 'خصم الانتقالات', 'delta': hit_delta, 'gap_delta': hit_delta[0] - hit_delta[1], 'parts': [], 'lineup_change': False})
    return events
