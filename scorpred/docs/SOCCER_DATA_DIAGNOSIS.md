# Soccer data diagnosis

The supported UI leagues are Premier League (39), Bundesliga (78) and LaLiga (140), using current start year 2026. The preserved classifier has reproducible historical skill over a train-prior baseline, but league-specific fitness and the actual runtime blend remain unverified. Some original feature defects were subsequently corrected in source files outside this audit; those corrections require validation rather than a claim of completed repair.

## League coverage

League assignments below come from explicit provider league IDs, not team-name guesses or unstable season-type codes. Cached prior-season counts are compared to double round-robin capacity; they are not independent provider-universe reconciliation.

| League | 2025-start completed /capacity | 2025 draws | 2026-start cached | Current completed /scheduled | Current draws | Teams |
|---|---:|---:|---:|---:|---:|---:|
| Premier League | 374 /380 (98.42%) | 103 /374 (27.54%) | 100 | 70 /30 | 20 /70 (28.57%) | 20 |
| Bundesliga | 300 /306 (98.04%) | 74 /300 (24.67%) | 81 | 54 /27 | 11 /54 (20.37%) | 18 |
| LaLiga | 374 /380 (98.42%) | 91 /374 (24.33%) | 119 | 88 /31 | 25 /88 (28.41%) | 20 |

Each previous-season cache is six fixtures below capacity. Current-season cached slices are unequal and should not be interpreted as full schedule completeness. Overall: 1,348 distinct events, 1,260 completed and 88 scheduled, with no conflicting final scores or completed events missing scores. Selected daily provider checks returned 32 soccer events, all already cached and consistent in date/home/away fields.

Observed current-season home/away results and goal rates are below. A team's conceding contribution is the opponent's scoring total. These small cached league slices describe results, not causal home advantage or complete-season forecasts; missing games can change the estimates.

| League | Home /draw /away results | Mean home /away goals |
|---|---|---|
| Premier League | 28 /20 /22 | 1.614 /1.286 |
| Bundesliga | 28 /11 /15 | 2.278 /1.407 |
| LaLiga | 38 /25 /25 | 1.716 /1.352 |

The corresponding previous-season home/away wins are 159/112, 131/95 and 184/99, respectively; all goal/result denominators and means are exported in `soccer_league_coverage.json`. Congestion/rest can be derived from prior fixture dates, but missing fixture history and the absence of immutable receipts limit historical availability claims.

## Training data and identity

The primary classifier's source CSV has 7,888 matches, 130 teams, dates August 8, 2021–April 19, 2026, zero duplicate fixtures and zero inconsistent score/result labels. Every row lacks league ID, season ID and source receipt lineage. Therefore there is **no measurable per-league training denominator or per-league test result**, and no defensible conclusion that each supported league has enough suitable historical examples. There are zero current 2026–27 season rows. The newest source fixture is 184 days before the initial audit date.

The live team directory has 58 clubs; 51 exact names occur in the saved Elo state (87.93%). Seven receive neutral fallback: Coventry City, Hull City, SC Paderborn 07, SV Elversberg, Deportivo, Málaga and Racing Santander. No additional matches appeared after simple name normalization. This is a coverage/identity-crosswalk question, not proven spelling corruption. Promoted/new clubs need explicit provider-ID mapping and an appropriate documented prior.

Legacy raw `E0_2018.csv` is byte-identical to `E0_2025.csv`, both containing 319 matches from 2025–26. The 2019–24 files each have 380 rows, 2025 is partial at 319/380, and 2026 is absent. The legacy processed concat contains 2,918 rows /2,599 unique fixtures (319 duplicate excess). This is a confirmed season-label and concatenation defect. The separate current classifier dataset has no such duplicates; the newer scoring trainer explicitly excludes the mislabeled file. `data/matches.csv` has anonymous feature rows and cannot supply fixture-identity coverage evidence.

## Existing feature quality

The classifier artifact declares 60 columns. Historical goal-scoring/conceding form, points, five/ten-match trend, venue, opponent, H2H, rest and reconstructed Elo provide 47 varying inputs. Five market, five xG and three referee columns are constant or entirely imputed; their presence does not prove a learned signal. The older processed feature CSV has 47 columns and is not the current artifact's complete schema.

At the original snapshot, `/prediction` supplied only five recent matches to an ML feature builder that also computes ten-match windows. Six long-window fields collapsed to five-match values, and four trend fields collapsed to zero. An isolated replay changing only these ten fields altered 39/1,518 argmax selections (2.57%), with mean absolute class-probability change 1.26 percentage points and maximum 12.88 points. Log loss was nearly unchanged and Brier slightly improved in that sensitivity; the evidence establishes changed semantics, **not uniformly worse accuracy**. The original runtime rest definition also used today's date rather than selected kickoff.

At 18:20:20 UTC, `app.py` and `scormastermind.py` changed outside the audit edits. Current disk code supplies separate ten-match ML contexts and selected fixture target dates. These original defects are therefore marked **corrected on disk, runtime validation pending**. The running app predates these source changes and its loaded version has not been established. No model artifact changed, and the audit made no production fix.

Shots, shots on target, possession and corners may be absent from extracted form. The legacy comparison display's averaging helper substitutes zero for missing values; missing-data frequency across all live fixtures was not measured, so no invented percentage is supplied. Current xG feed coverage, suspensions, starting lineups and kickoff-specific squad availability are Not verified. The 31 roster files /932 players have zero cached injury records, which does not establish healthy squads.

Player-prop mappings have confirmed semantic issues: `clearances` reads `tackles.blocks`; `aerial_duels_won` reads all `duels.won`; `passes_completed` reads `passes.total` without proof it means completed; and `chances_created` duplicates `passes.key`. Touches in the opponent box is explicitly unsupported. Credentialed fixture-stat coverage was not verified. Correctness requires original stat definitions and null for unsupported quantities, not richer model families.

## Three-way prediction evaluation

Without fitting or changing weights, the audit replayed the exact existing artifact and source hashes. Chronological train/calibration/test sizes are 4,540/1,528/1,518. The test runs May 23, 2025–April 19, 2026, with only 29 test games from the prior start-year season and 1,489 from the latest. This is not broad multi-season consistency evidence.

| Existing classifier /baseline | Accuracy | Log loss | Mean-class Brier | Top-label ECE |
|---|---:|---:|---:|---:|
| Calibrated forest | 52.04% | 1.002599 | 0.199535 | 0.021563 |
| Train class prior | 43.68% | 1.072552 | 0.216319 | 0.005972 |

Exploratory paired week-bootstrap log-loss difference is -0.069953, 95% interval [-0.088420,-0.051082], 33 week clusters. This supports improvement over this simple prior on an already-used reconstructed test window; it does not validate current live inputs or the blended recommendation system. Saved classifier metrics reproduce within 0.000000393. Sum-class Brier is 0.598604; comparisons must use the same normalization.

Actual classes are 663 home /383 draw /472 away. Argmax predicts 917 home /zero draw /601 away, giving zero draw recall. However mean draw probability is 24.88% versus actual 25.23%, and draw class ECE is 0.015201. Zero hard draw selections is a decision/discrimination limitation; it does not by itself establish broken probability calibration. Classwise and league-level reliability still need independent validation. Confidence transformations and BET/LEAN/SKIP categories must not be interpreted as calibrated class probabilities.

## Freshness and permitted data

Soccer optional football-data.org/API-Football credentials were absent in the audit process; their availability inside the running service is Unknown. ESPN public fallback was verified on bounded dates. Later app health reported last successful soccer refresh at October 20 17:56:43 UTC, 539 minutes before the final check, and two consecutive failures. No durable provider receipt history supports a stronger reliability claim.

Free collection must respect source terms. Official [football-data.org policy](https://docs.football-data.org/general/v4/policies.html) specifies authenticated free-tier rate limits and treats null as valid unknown information; verify supported competition entitlements before enabling additional calls. [Openfootball](https://github.com/openfootball/football.json) offers a public-domain results cross-check, subject to validating the needed season and identity coverage. The currently published [football-data.co.uk conditions](https://football-data.co.uk/data.php) restrict use to private individuals and exclude automated commercial/data-training uses. No new files were downloaded from it; resolving permitted use is a prerequisite to recommending automated training ingestion. More predictors or paid feeds are not the next priority.
