# League Match Centres

Open **تفاصيل المواجهة** beside any Elite fixture score. It opens a separate tab
at `/league/elite/match/<gameweek>/<entry_1>/<entry_2>`.

The page uses FPL's public live points explanations, not an LLM. It shows each
player's points, applied multiplier, playing minutes and points breakdown. The
timeline retains only changes to the difference between the two managers.
Categories include appearance/60-minute points, goals, assists, clean sheets,
goals conceded, saves, penalties, cards, own goals, defensive contributions,
bonus and corrections. Unknown future categories retain their FPL identifiers
and point values rather than silently disappearing.

## Three-manager leagues

Cities, Libyan and Arab Championship fixtures now have the same match-detail
link on desktop and mobile. Routes are `/league/<league>/match/<gameweek>`
with `first` and `second` team-name query parameters. The API checks the actual
H2H schedule and maps its representatives to the configured three-manager
rosters; any member can be the representative.

Each side has three expandable pitches. Player dialogs show individual points;
the scoreboard sums all three managers after transfer deductions. Differential
highlighting compares total counted multipliers across both teams. One Palmer
against three Palmers is not neutral: a five-point goal changes the gap by ten
points (assuming ordinary, non-captain appearances). Equal exposure cancels out.
Timeline rows include each affected manager's contribution.

Team rules cap captaincy at two, ignore Triple Captain's extra multiplier, and
exclude Bench Boost bench points. On Bench Boost weeks, normal substitutions
are reconstructed even after settlement because FPL's boosted multipliers and
empty substitution list do not represent these leagues' scoring rules.
Wildcard and Free Hit use the actual published squad, with no invented transfers.

Cities retains its rounded gold-accented dark cards, Libyan its flat gold/grey
ledger, and Arab its white/navy treatment. The same frontend still supports the
original Elite view without changing Elite's chip rules.

## Collection and storage

The web app starts a daemon collector in each process. A database lease lets
one process collect at a time, normally about once per minute. Each cycle
collects the last published gameweek and the previous one if still unchecked.
It covers all four leagues, sharing each gameweek's live scores and limiting
parallel match work to four. The lease is renewed while matches are processed.
Match-page requests also collect the requested fixture. Set
`ELITE_MATCH_COLLECTOR=0` to disable the daemon during tests or local scripts.

`db.create_all()` adds three tables on app startup:

- `elite_match_observations`: latest snapshot, baseline and version per fixture.
- `elite_match_updates`: observed changes, atomically recorded with the snapshot.
- `elite_match_collector_lease`: coordination across web processes.

These existing table names are shared by all four leagues. Team match keys
include league, season, gameweek, representatives and a roster fingerprint,
so a roster replacement starts a fresh baseline. Incomplete squads never save
a partial six-manager score.

No changes are written to FPL or to the standings tables. Use the configured
PostgreSQL database in production so history survives web-service restarts.
Do not run Gunicorn with `--preload`: the existing start commands initialize
the app and collector inside each worker.

## Interpretation

- Times mean **detected at**, not real match event times. A poll may combine
  multiple events. Rows in a batch are not a claimed chronological sequence.
- The first snapshot is a baseline, never a fabricated event history. Previously
  earned points are shown in the score but not attributed to invented times.
- Server sleep, outages and API failures can cause gaps. Gaps exceeding three
  minutes are labeled. Continuous tracking requires the web host to stay awake.
- The API shows the latest 200 batches; older stored history is retained.
- Bonus is provisional until FPL marks the gameweek checked. It is calculated
  per real fixture from BPS, including double gameweeks and tied ranks.
- Clean sheets and scoring thresholds come from the points explanation. A
  defender substituted before a later goal keeps whatever FPL awards them.
- During play, substitutes/captain fallback wait until all the absent player's
  fixtures finish. A pending higher-priority bench player reserves their place.
  Final FPL multipliers and automatic substitutions are authoritative after
  `finished` and `data_checked` are both true.
- This deliberately avoids the dashboard's earlier speculative substitutions;
  interim scores can differ. The match page explains that distinction.

## Validation

Run `ELITE_MATCH_COLLECTOR=0 python3 -m unittest discover -s tests -p 'test*.py'`
and `node --check static/js/elite_match.js`.
