# Scoring predictors: methods, evidence and limits

Scoring forecasts are separate from ScorPred's decision engine. Their expected
scores, score distributions and uncertainty ranges do not rewrite BET/CONSIDER/SKIP.
These are experimental implementations of established statistical methods, not
a claim to reproduce a research paper, achieve state-of-the-art performance, or
make profitable bets. The scoring panel links the relevant research and displays
its own chronological test results.

## Methods and primary references

| Sport | Implementation | Research foundation and scope |
|---|---|---|
| Soccer | Two L2-regularized Poisson regressions predict home/away scoring rates from rolling pregame form. The low-score correlation is fitted on calibration games. | [Dixon & Coles (1997)](https://doi.org/10.1111/1467-9876.00065) motivates Poisson score modeling and the low-score correction. This implementation uses rolling features, rather than their full attack/defense team-parameter likelihood. Current trained coverage is the Premier League. |
| NBA | Two Ridge regressions predict team points. Calibration residuals determine score uncertainty; integer score probabilities use one-point bins and exclude tied final scores. | [Hoerl & Kennard (1970)](https://doi.org/10.1080/00401706.1970.10488634) provides Ridge regression. [Sill (2010)](https://www.sloansportsconference.com/research-papers/improved-nba-adjusted-using-regularization-and-out-of-sample-testing) supports regularization and testing on future NBA games. ScorPred uses team score features, not Sill's player adjusted-plus-minus model. |
| NFL | Two Ridge regressions use the 19 pregame nflverse features, including prior scoring/conceding, approximate EPA/play, CPOE, turnovers, rest and neutral venue. | Ridge regression as above; [nflfastR's analysis](https://nflfastr.com/articles/beginners_guide) motivates examining the predictive value of prior efficiency. It does not validate ScorPred's feature coefficients or score forecasts. |

All sports use separate-score split-conformal absolute-residual ranges, targeting
80% marginal coverage, following [Angelopoulos & Bates](https://arxiv.org/abs/2107.07511).
These ranges target each team's score, not simultaneous coverage for both teams.
Sports results have temporal dependence and season shifts: exchangeability is not
established, so theoretical nominal coverage is **not guaranteed**. The displayed
coverage is measured on later held-out games. The time-series concern is discussed
in [Stocker et al.](https://arxiv.org/abs/2511.13608).

NBA score pairs use a discretized independent-normal approximation. Marginal and
total uncertainty are estimated from calibration residuals; correlated joint score
probabilities and overtime transitions are not fully modeled. NFL does not pretend
that a continuous point mean is a probability for an attainable exact final score.
Soccer/NBA distribution probabilities remain model-based estimates; outcome
probability calibration has not been separately established by the score-error test.

## Data and leakage controls

Soccer reads local Football-Data `data/raw/E0_*.csv` files, validates dates against
their labeled season (including the delayed 2019/20 season), and deduplicates game
identities. `E0_2018.csv` was excluded because its dates actually describe 2025/26.
The original file is preserved. This scoring audit does not retroactively certify
other historical datasets used elsewhere in the app.

NBA imports monthly ESPN public scoreboards into `cache/score_models/nba_raw`.
Completed regular-season/playoff NBA-team games are used; All-Star, exhibition,
preseason and foreign-team games are excluded. Source responses are fingerprinted.
This public endpoint has no service-level guarantee. Current form containing
preseason games receives an explicit descriptive-estimate label rather than a
claim that the regular-season research evaluation applies to it.

NFL uses the synchronized local nflverse store described in [NFLVERSE.md](NFLVERSE.md).

Soccer and NBA features use up to six prior completed same-season games per team,
with a minimum of three and a 24-hour lag. NFL uses its shared pregame feature
builder. Current-game outcomes, closing prices, recorded game-day weather and
unversioned availability reports never enter these feature sets. Historical sources
can contain later corrections, so these are retrospective data vintages.

Data is split chronologically 60/20/20, keeping dates together. Imputation,
standardization and regression coefficients are fitted only on the earliest window.
The middle window determines residual ranges/dispersion and soccer low-score
correlation. The final window is evaluated without tuning or refitting. Fixed
regularization strengths are 0.1 for Poisson and 20 for Ridge.

## Reproduce and refresh

From the repository root, after installing the base requirements and seeding
nflverse:

```powershell
.\.venv\Scripts\python -m scripts.train_scoring_models --sync-nba
```

For a single sport, use `--sports soccer`, `--sports nba` or `--sports nfl`.
`--sync-nba` caches complete historical months and refreshes the current month;
omit it to train from existing local downloads. Scheduled data import/training is
a separate maintenance task; no external scheduler was deployed by this change.

Artifacts are small JSON coefficient bundles in `cache/score_models/<sport>.json`.
Reports are `data/validation/scoring_<sport>.json`. They contain date windows,
source/data/schema fingerprints, score MAE, a training-average baseline, total-score
error and actual interval coverage. Web inference checks the report's artifact
hash, exact feature order/dimensions, finite parameters, calibration cutoff and a
30-day artifact-age limit. No pickle is loaded and no training runs in web requests.
Generated cache artifacts need to be trained on each host or copied with their
matching reports; they are not included in Git by default.

Missing/incompatible research artifacts are clearly labeled. With sufficient real
scores the app can show an explicitly descriptive distribution; without sufficient
scoring data it returns an unavailable state instead of invented league averages.
Invalid team IDs/totals receive 400; insufficient scoring records receive 422.
Injuries, starting lineups, weather and bookmaker prices are not model inputs.

## Local evaluation on 2026-10-16

The exact current results are in the machine-readable reports. MAE below is the
average absolute error for one team's score, averaged across home/away teams.

| Sport | Later test games | Score MAE | Training-average baseline MAE |
|---|---:|---:|---:|
| Premier League soccer | 480 | 0.90 goals | 0.94 goals |
| NBA | 1,169 | 9.85 points | 10.49 points |
| NFL | 196 | 7.47 points | 7.82 points |

The NBA report excludes non-NBA teams. A source/data change requires regenerating
its evaluation; it must not silently reuse earlier metrics.
These are score errors, not win-prediction accuracy percentages or returns.
Prospective logging, rolling-window evaluation across multiple seasons, possession
and player impact data for NBA, versioned injuries/quarterbacks for NFL, and
competition-specific soccer validation remain necessary before stronger claims.
