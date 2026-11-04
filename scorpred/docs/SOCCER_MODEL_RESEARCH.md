# Soccer model research: ratings models versus the rules engine and the market

Produced by `scripts/soccer_model_research.py`; full numbers in
[`data/validation/soccer_model_research.json`](../data/validation/soccer_model_research.json).

## Summary

- The rules engine that the soccer dashboard published scored worse than the league-average outcome split. Its log loss was 1.069, against 1.073 for the train-prior baseline in the model validation audit. It never picked a draw, and on average it gave the away side slightly more chance than the home side.
- A fitted ratings model (Elo plus Dixon-Coles, combined by a logistic layer) cuts log loss to 0.988. Accuracy rises from 47.6% to 52.7%. It is better in every one of the five seasons and five leagues. It is now the published soccer model.
- No model beats the bookmakers. The pre-weekend market average scores 0.971 and the closing price 0.969. Every betting strategy built on a model lost money. Adding the ratings to the market price (the anchored model) did not improve on the price either: the log-loss difference interval includes zero.
- Soccer picks are therefore never labelled BET. CONSIDER is the strongest soccer call until a model shows an edge, measured by closing line value on tracked picks.

## Method

- **Data:** football-data.co.uk results, shots and market-average prices for the Premier League, La Liga, Serie A, Bundesliga and Ligue 1, seasons 2019-20 to 2025-26 (12,458 matches).
- **Walk-forward:** every probability uses only matches played before the day it predicts.
  - Elo updates after each match day.
  - Dixon-Coles refits every week.
  - Logistic layers refit at the start of each season on all earlier seasons.
- **Evaluation:** 2021-22 to 2025-26, 8,907 matches with complete prices.
- **Tuning:** 2019-20 is the warm-up season. Hyperparameters were chosen by log loss on 2020-21 only:
  - Dixon-Coles decay half-life and ridge: 180d/ridge 2 1.0047, 180d/ridge 6 1.0028, 365d/ridge 2 1.0038, **365d/ridge 6 1.0023**, 730d/ridge 2 1.0046, 730d/ridge 6 1.0031.
  - Elo K-factor: **K 15 1.0067**, K 25 1.0081, K 35 1.0101.
- **Rules engine replay:** `scorpred_predict` with:
  - same-season last-5 form;
  - last-5 head-to-head;
  - a league table built from results to date;
  - no injuries, as the dashboard calls it.
- **Uncertainty:** 95% intervals from a weekly-block bootstrap with 2,000 resamples. A negative log-loss gap means the model is better than the reference.

## Probability quality

| Model | Log loss | Brier | Accuracy | Draw picks | vs pre-weekend market (95% CI) |
|---|---:|---:|---:|---:|---|
| Market, closing | 0.9686 | 0.1920 | 54.3% | 19 | — |
| Market, pre-weekend | 0.9708 | 0.1925 | 53.8% | 8 | — |
| Market, recalibrated | 0.9704 | 0.1924 | 53.7% | 8 | — |
| Market + ratings (anchored) | 0.9709 | 0.1925 | 53.8% | 11 | +0.0002 [-0.0008, +0.0012] |
| Ratings + shots on target | 0.9857 | 0.1958 | 52.7% | 0 | +0.0149 [+0.0122, +0.0178] |
| **Ratings model (published)** | 0.9880 | 0.1963 | 52.7% | 1 | +0.0173 [+0.0146, +0.0203] |
| Dixon-Coles alone | 0.9896 | 0.1967 | 52.6% | 29 | +0.0188 [+0.0158, +0.0220] |
| Elo alone | 0.9912 | 0.1969 | 52.4% | 0 | +0.0205 [+0.0175, +0.0236] |
| Rules engine (previously published) | 1.0693 | 0.2148 | 47.6% | 0 | +0.0985 [+0.0909, +0.1067] |

The published model leaves out shots on target because the live results feed (ESPN team schedules) does not carry them. Keeping them would improve log loss by about 0.002.

| Season | Market (pre-weekend) | Ratings model | Rules engine |
|---|---:|---:|---:|
| 2021-22 | 0.9773 | 0.9930 | 1.0795 |
| 2022-23 | 0.9779 | 0.9938 | 1.0648 |
| 2023-24 | 0.9556 | 0.9744 | 1.0624 |
| 2024-25 | 0.9640 | 0.9830 | 1.0560 |
| 2025-26 | 0.9784 | 0.9956 | 1.0834 |

| League | Market (pre-weekend) | Ratings model | Rules engine |
|---|---:|---:|---:|
| Premier League | 0.9594 | 0.9776 | 1.0496 |
| La Liga | 0.9668 | 0.9829 | 1.0838 |
| Serie A | 0.9715 | 0.9862 | 1.0715 |
| Bundesliga | 0.9750 | 0.9949 | 1.0733 |
| Ligue 1 | 0.9834 | 1.0016 | 1.0690 |

## Betting at pre-weekend prices

Flat one-unit stakes at market-average prices. Closing line value is the backed outcome's margin-free closing probability over its pre-weekend probability, minus one.

| Model | Strategy | Bets | ROI (95% CI) | Closing line value (95% CI) | Beat the close |
|---|---|---:|---|---|---:|
| Rules engine (previously published) | Every top pick | 8,907 | -5.2% [-7.5%, -3.0%] | — | — |
| Rules engine (previously published) | Positive expected value | 8,840 | -11.1% [-14.7%, -7.4%] | -0.4% [-0.6%, -0.2%] | 47% |
| Rules engine (previously published) | Expected value > 5% | 8,333 | -11.5% [-15.0%, -7.7%] | -0.4% [-0.7%, -0.2%] | 47% |
| Ratings model (published) | Every top pick | 8,907 | -3.3% [-5.1%, -1.5%] | — | — |
| Ratings model (published) | Positive expected value | 7,333 | -12.3% [-15.7%, -9.1%] | -0.6% [-0.8%, -0.4%] | 47% |
| Ratings model (published) | Expected value > 5% | 5,139 | -13.6% [-17.9%, -9.5%] | -0.6% [-0.9%, -0.3%] | 46% |
| Ratings + shots on target | Positive expected value | 7,334 | -11.5% [-14.8%, -8.2%] | -0.1% [-0.3%, +0.1%] | 50% |
| Ratings + shots on target | Expected value > 5% | 4,946 | -12.5% [-17.1%, -7.5%] | -0.1% [-0.4%, +0.2%] | 49% |
| Market + ratings (anchored) | Positive expected value | 4,432 | -6.1% [-11.4%, -1.1%] | +0.7% [+0.4%, +1.0%] | 60% |
| Market + ratings (anchored) | Expected value > 5% | 1,139 | -12.4% [-23.9%, -1.0%] | +0.2% [-0.5%, +0.9%] | 56% |

The anchored model's small positive closing line value is the only sign of information beyond the price. It is smaller than the bookmakers' margin, so it did not turn into profit. It is the place to look for an edge: lineups and injuries known before the market moves would feed it.

## Deployment

- **Code:**
  - `services/soccer_ratings.py` holds the rating code shared by research and production.
  - `services/soccer_model.py` rebuilds Elo and Dixon-Coles per league from ESPN team schedules (this season and the two before it), caches them for six hours, and applies the logistic layer.
- **Coefficients:** `data/models/soccer_ratings_v1.json`, plain JSON. It is fitted on all seasons; the walk-forward numbers above come from season-by-season refits.
- **Fallback:** when ratings are unavailable or a club is not in the current league list, the rules engine's probabilities are kept and the log says so.
- **Tracking:** every tracked pick records the engine fingerprint, so live results for the ratings model are reported separately from the rules engine's.

## Known limits

- Production Elo starts two seasons back, while the research Elo ran from 2019-20. Early-window ratings will be slightly noisier.
- Clubs new to a league start from a fixed below-average prior (`NEW_TEAM_PRIOR`), not from their previous league.
- Results and odds are retrospective files (Class B in the validation audit's terms). The tracked pregame record is the prospective test.
- A Dixon-Coles fitting bug was found and fixed during this work. Very strong clubs could drive the low-score correction negative, which froze the fit; Serie A was the clear case. The tables above come from the fixed code.

## Player availability backtest

Produced by `scripts/player_availability_backtest.py`; numbers in
[`data/validation/player_availability_backtest.json`](../data/validation/player_availability_backtest.json).

**Question:** does adjusting for who plays make Premier League forecasts better?

**Method:**
- Player values come from each player's season-to-date Fantasy Premier League totals before the match (gameweek history from github.com/vaastav/Fantasy-Premier-League, MIT licence).
- They go through the production formula (`soccer_player_value.player_value`), and the adjustment is the one the injury panel uses.
- Availability is what actually happened, which is more than any pre-match injury list knows, so this is an upper bound on what the information can add:
  - **lineup**: every player who started, plus regulars who did not start.
  - **absences**: only regulars who did not play at all.
- The adjustment scale was chosen on 2022-23 (380 matches); results are for 2023-24 to 2025-26 (1,140 matches).

| Forecast | Log loss | Change from adding availability (95% CI) |
|---|---:|---|
| Ratings model | 0.9795 | — |
| Ratings model + lineup (scale 0.75) | 0.9779 | -0.0016 [-0.0073, +0.0041] |
| Ratings model + absences (scale 1.25) | 0.9800 | +0.0005 [-0.0055, +0.0066] |
| Pre-weekend market | 0.9650 | — |
| Pre-weekend market + lineup | 0.9652 | +0.0002 [-0.0052, +0.0057] |
| Pre-weekend market + absences | 0.9682 | +0.0032 [-0.0026, +0.0093] |
| Closing market | 0.9597 | — |

**Result:** no measurable improvement, even with lineups known.
- The tuning season suggested one (0.994 to 0.986 with absences), but it did not hold in later seasons.
- The closing market, which does know the lineups, stays clearly better.
- The player values (xG and xA above replacement, on/off expected goals conceded, shrunk, season-to-date) are therefore not yet good enough to change forecasts. They are not used by the published model.
- The injury panel still shows them, labelled as a guide to who matters rather than a better forecast.

**Likely reasons, and what to try next:**
- **Early-season values are near zero.** Season-to-date stats are thin, so values are heavily shrunk; adding last season's totals would help.
- **The ratings remember players longer than their usual share does.** "Usual share" is season-to-date, while the ratings use a 365-day half-life, so the two windows don't line up.
- **Defensive value from on/off splits is noisy.**
- **Any change must be checked against the same held-out seasons** without retuning on them; they have now been looked at, so a fresh season is the clean test.
