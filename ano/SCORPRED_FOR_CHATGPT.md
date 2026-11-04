I want a hard, honest critique of my sports-picks system. It is called ScorPred, and my desktop assistant "Ano" drives it. Below is the full logic, from fetching the day's games to the picks and parlays that get emailed to me. Every number below is the real value in the code.

Please critique:
1. Statistical soundness. Where am I fooling myself? Look for leakage, selection bias, double counting, bad priors, false independence, and overfitting.
2. Each engine on its own: is the method right for the job, and what would a professional sports modeller do instead?
3. The pick rules and thresholds: which are arbitrary, too loose or too strict, and what would you set them to, and why?
4. The parlay construction: is "most likely to hit inside a payout band" a sensible objective?
5. Tracking and evaluation: am I measuring the right things to know whether this has an edge? What should I add (closing-line value, Brier/log-loss by market, and so on)?
6. Priorities: give me a ranked list of the 10 changes with the biggest expected impact, and say for each whether it is quick or a big job.

Be direct. If something is pointless or harmful, say so.

---

## 0. Context and constraints

- It is paper research only. No bets are placed automatically, and the emails go only to me.
- Sports covered: NBA, soccer and NFL. The soccer dashboard covers the Premier League, Bundesliga and La Liga. Soccer props and parlays also scan Serie A, Ligue 1, the Champions League and the Europa League.
- Data sources:
  - ESPN public JSON (free): schedules, scoreboards, box scores, rosters, injuries, and ESPN's DraftKings odds/props board.
  - The Odds API (free plan): I cap myself at 450 credits a month across everything, with DraftKings and FanDuel as the books.
  - Fantasy Premier League: EPL availability and tackles.
  - nflverse: NFL data.
  - football-data.co.uk: historical results for training the soccer model.
- Budget rule: because of the credit cap, only the top games get priced each morning (NBA 2, soccer 2, NFL 1). Parlay Mode has its own 150-credit share. By default, game lines come from ESPN's free DraftKings feed, which is one book only.
- Stack: a Flask app running locally at 127.0.0.1:5001, with an APScheduler background scheduler. Ano is a separate Python desktop assistant that reads ScorPred over loopback.

---

## 1. Daily timeline (US Eastern)

| When | Job |
|---|---|
| Every 9 min | Injury feeds refresh (ESPN NBA/NFL, FPL) |
| Every 25 min | NBA schedule refresh |
| Every 60 min | Soccer fixtures + predictions refresh (MatchBrain refresh cycle per league) |
| Every 30 min | Prediction tracking: record every published pregame pick (revised until kickoff), grade finished ones |
| Every 10 min | Closing-line capture for tracked game picks starting within 25 min |
| Hourly | Best-props board rebuilt (with sportsbook pricing deferred, so it spends no credits) |
| 02:30 daily | Recalibration learner (see §9) |
| **06:00** | **Morning email** (§2) |
| **12:00, retry 15:00** | **Midday player-parlay email** (§6). It is sent once a day, because DraftKings often posts props after 6 AM |
| On demand | Ano chat commands can trigger any report (§10) |

---

## 2. The morning email pipeline (services/daily_digest.py)

Order of work, which matters because the sportsbook prices expire after 5 minutes:

1. **Game-line parlays are built first** (§7), because they take minutes.
2. **Player parlays (Low/Medium/Fly/Ladder) and Matchup stories** for the NBA and soccer (§5, §6).
3. **Yesterday's recap**: every pick emailed yesterday is graded from ESPN finals (§8).
4. **Collect the games** for a 72-hour lookahead, day by day:
   - Call `/api/dashboard/{sport}` for each date (and for each league in soccer).
   - A card is eligible only if it starts within the window, is still pregame, has data that is still "fresh", has a real side (not "Unavailable"), has an action of BET, CONSIDER or SKIP, has both team names, and has a match id.
   - If a partition is still warming up, it gets one retry. Expired partitions are re-fetched once. The code never loops to fill a quota.
   - Rank within each sport by (BET before CONSIDER before SKIP), then data quality (Strong, Partial, Limited), then confidence, then kickoff. Take up to 10, request a score forecast for each, and keep the first 5 that return a fresh forecast.
5. **Morning "Top picks" (single player props at real book lines)**:
   - NBA and NFL require a fresh injury feed, or the sport's props are dropped. Players on the injury list, or with roster status other than active, are removed. Soccer props must have a "verified" and fresh roster check.
   - Only the first N selected games are priced (NBA 2, soccer 2, NFL 1).
   - For each player prop, look at every fresh quote that has both over and under prices:
     - `fair_over = (1/over) / (1/over + 1/under)`: the book's no-vig chance.
     - The model's chance at that exact line comes from the prop model's probability ladder (§4), with the market's shrink applied.
     - For each side, `edge = model_chance − fair_chance`. The side qualifies if **edge ≥ 0.04 AND model_chance ≥ 0.52**. The side and line with the biggest edge win.
     - There is no fallback line: if no book line clears the bar, the player gets no pick.
   - Sort by edge, keep one pick per player and up to 5 per sport. The email shows the top 5 across all sports.
6. Before sending, everything is re-checked. Any game that has started or gone stale is dropped. Any prop whose book quote has expired is dropped. Just before SMTP submission, the code checks again and aborts if anything has expired.
7. A delivery journal for each recipient and day prevents a double send. Everything sent is recorded in the pick tracker.

Email layout: yesterday's recap, then Matchup stories, then Top picks, then game-line parlays, then player parlays, then a "Games" line per sport (the side and %, or "(no bet)" for SKIP).

---

## 3. Game-level engines (one per sport)

### 3a. Soccer: Elo + Dixon-Coles + fitted logistic layer (services/soccer_model.py, soccer_ratings.py)

- **Data**: finished league results for this season and the two before, from ESPN team schedules (deduplicated by fixture id). At least 60 results are needed. Ratings are rebuilt every 6 hours.
- **Elo**: goal-difference Elo with K = `params.elo_k` and home advantage = `params.elo_home`. Those are fitted values stored in the model artifact; the class defaults are K 20 and home +60.
  - Margin multiplier: 1 for a 0–1 goal margin, 1.5 for 2, and (11+m)/8 for 3 or more.
  - New team: 1400. Between seasons, ratings regress to the mean, keeping 80% of each team's distance from it.
- **Dixon-Coles**: each team gets an attack and a defence rating.
  - Home goals ~ Poisson(exp(base + home + att_h + def_a)), and away goals likewise.
  - A rho correction is applied to 0-0, 1-0, 0-1 and 1-1, with rho bounded to ±0.3.
  - Fitting: time-decay weights with a half-life of 365 days (or the artifact value), using a 730-day window and a ridge penalty of 4.0 toward a prior. Promoted teams get a prior of (attack −0.25, defence +0.20). The optimiser is L-BFGS-B. At least 30 matches are needed.
- **Combiner**: a multinomial logistic layer whose coefficients are stored in the artifact and were fitted offline with a walk-forward on football-data.co.uk. It has 4 features:
  1. Elo difference / 400
  2. log(DC P(home) / P(away))
  3. logit(DC P(draw))
  4. log(λ·μ)
- Then a **recalibration** layer (§9) is applied if it has been switched on.
- Output: H/D/A probabilities, expected goals, the most likely score from the DC grid, and confidence. Confidence is "High" at ≥60% on the top side, "Medium" at ≥48%, otherwise "Low".
- **Fallback**: if ratings are unavailable, an older weighted rules engine is used (scorpred_engine.py).
  - It scores each team 0–10 as: Form 39%, Offense 14%, Defense 14%, H2H 9%, Home/Away 8%, Opponent strength 7%, Match context (rest) 5%, Squad/injuries 4%.
  - The last 5 games are weighted 40/25/15/10/10.
  - P(A) = logistic(0.5 × score gap). Draw % = clamp(26 − 3.5×|gap|, 8, 32), and the remainder is split between A and B.
- **Hard rule**: no soccer model has beaten bookmaker prices in walk-forward testing, so **any soccer BET is downgraded to CONSIDER**.

### 3b. NBA: shrunk margin rating priced against the market (services/nba_pregame_model.py)

- **Team rating**:
  - Built from recency-weighted, venue-neutral scoring margin. Each margin is capped at ±25, and home court of 2.0 is removed first.
  - The weight half-life is 60 games. Last season counts at 0.25 weight.
  - The rating is shrunk toward league average using 8 pseudo-games.
- **Projected home margin** = rating_home − rating_away + 2.0 (home court) + 1.5 × (away team on a back-to-back − home team on a back-to-back) + a lineup adjustment.
- **Lineup adjustment**: each rotation player (10+ minutes) is valued by Hollinger Game Score above a replacement rate.
  - Value = 0.5 × (GmSc − 0.28 × min), shrunk with 5 pseudo-games and capped at 8 points.
  - The adjustment is value × (chance he plays tonight − share of the rated games he played).
  - Chance he plays tonight, from ESPN status: out 0, doubtful 0.25, questionable 0.5, day-to-day 0.6, probable 0.9.
  - The team's total adjustment is capped at ±12.
- **Win probability** = Φ(margin / σ), where σ = sqrt(11.5² + each team's rating uncertainty²).
- **Totals**: points scored and allowed use a 10-game half-life, shrunk with 8 pseudo-games toward the league level implied by both teams' recent games (default 114).
- Head-to-head is shown but not used. It made log-loss worse in testing.
- Backtest: log-loss of 0.605 on 2,636 games (2024-25 and 2025-26), against 0.669 for the old form engine.
- **Market**: a no-vig moneyline from ESPN's odds (or a spread converted through Φ(−spread/11.5)).
- **Action rules**:
  - **BET**: edge ≥ 4pp, at least 15 games' worth of rating weight, and at least 8 current-season games.
  - **CONSIDER**: edge ≥ 2pp and at least 8 games.
  - **Early-season guard**: with fewer than 8 current-season games, an edge over 10pp is treated as implausible and the game is a SKIP ("the price knows something").
  - **No market**: CONSIDER only if the model is at 65% or more with at least 15 games. Otherwise SKIP.
- Recalibration (§9) is applied to the home probability before deciding.

### 3c. NFL: descriptive scenario model (services/nfl_service.py)

- It needs at least 3 completed current-season games for each team, and uses up to the last 8 (finished more than 24 hours ago).
- Scenarios: every (home game, away game) pair gives an expected home score of (home's points scored + away's points conceded)/2, and the mirror for the away team. Home/draw/away shares are counted across all pairs.
- There is no home field, opponent strength, injury, weather or odds adjustment. The output is labelled "scenario share, not win probability".
- It goes through the Decision Engine with data_quality 45 and no odds, so it can never be a BET.
- A frozen, pre-registered Elo + logistic NFL research forecast also exists (nfl_pregame_forecast.py), with provenance and holdout checks. It is research only and is not used for picks.

---

## 4. Player-prop models

### 4a. Rule model for count stats (services/prop_model.py), used for NBA and soccer

For each player and market:
- **Rate per minute**: recency-weighted with a 10-game half-life, shrunk toward the league per-minute rate for his position, with prior minutes of 120 for the NBA and 270 for soccer.
- **Projected minutes**: weighted average of the last 5 games.
- **Opponent factor**: what this opponent allowed per game in this market relative to the league mean, shrunk with 8 pseudo-games and capped at ±15%.
- **Mean** μ = rate × minutes × opponent factor.
- **Distribution**: negative binomial. Its overdispersion α blends the player's own observed dispersion (last 15 in-role games, scaled to projected minutes) with a market default, with the default counting as 10 games of evidence. Poisson is used if α ≈ 0.
- Games below 70% of projected minutes are treated as a different role. The model needs a minimum number of games in role: 5 for the NBA, 4 for soccer.
- **Internal "line"**: floor(median of the last 15 in-role games) + 0.5. This is only a reference, not a book line.
- **Side**: whichever side the model favours more than the player's smoothed record, (hits + 1)/(n + 2).
- **Probability ladder**: P(over k) for every k, so any half-point book line can be priced.
- **Published chance** = 0.5 + shrink × (model − 0.5).
  - NBA shrink is 0.66, fitted on the first half of 2025-26. Picks said 77% and hit 68%; after the shrink, the second half hit 69%.
  - Soccer shrink is 1.0. On the 2025 EPL, picks said 76–77% and hit 79%.
- Thresholds before a market can be picked:
  - NBA minimum lines: points 9.5, rebounds 3.5, assists 2.5, threes 1.5, PRA 14.5. NBA players need 20+ projected minutes.
  - Soccer: lines of 0.5 or more. Goals and assists are picked on the over side only. Saves are never picked (unstable in testing). Soccer players need 60+ projected minutes.
- Players must be on the **current, verified ESPN roster** (fresh within 10 minutes). Players on the injury list, or whose status is not active, are excluded. A player appearing on both rosters is ambiguous and is dropped.

### 4b. NBA ML model (services/nba_props_ml.py)

- LightGBM predicts each stat's mean, priced as a negative binomial.
- Features: shot volume (FGA, 3PA, FTA), minutes stability, rest and back-to-backs, teammates and opponents out (ESPN injury report), team and opponent pace, what the opponent allows to the player's position (G/F/C), and the pregame spread and total.
- It only replaces the rule model in markets where it beat the rule model in walk-forward tests. All of the rule model's guards still apply.

### 4c. Soccer extras

- A soccer player model plus "model picks" add context on top of the rule model.
- EPL tackles come from FPL.
- For leagues other than the EPL, there is no injury feed.

---

## 5. Matchup engine and context factors

### 5a. Defense-vs-position and attack-by-position (services/matchup_engine.py)

- For every team in the league: over its last 15 games, what opposing players at each position produced against it ("allowed"), and what its own players at each position produced ("produced").
- **Position groups**:
  - NBA box scores only say G/F/C. Each game, the starting guard with the most assists counts as that game's PG, and the other guards are G. Only starters are counted, plus bench guards as G.
  - Soccer: winger, striker, attacking mid, central mid, full-back and centre-back, from ESPN positions, counting players with 45+ minutes.
- **Factor** = 1 + 0.2 × (team allowed ÷ league average − 1). The 0.2 strength was picked by replay over 3 seasons; 0.5 overshot. Each team's average needs at least 5 samples.
- **Note text**: written only when the team is at least 8% away from average AND in the league's top or bottom quarter (band = max(3, teams/4)).
- **"Story"**: the attack's strength at a position plus the defence's weakness there.
  - Both must point the same way.
  - Together they must move the numbers by at least 15% (`STORY_MIN = 0.15`).
  - At least one of them must be in its league's top or bottom quarter.
  - Each team is judged against its own domestic league, so cross-league games stay fair.
- Profiles are cached for 6 hours (NBA) or 12 hours (soccer). Box scores are cached on disk permanently.

### 5b. Context factors (services/context_factors.py)

Each factor was kept only after a replay of cached box scores improved accuracy in each of the last 3 seasons (about 97,000 NBA player-stat predictions and 5,481 soccer matches):
- **Recency**: last 5 games ×2, games 6–10 ×1.5, games 11–15 ×1. Hits are rescaled so the total weight equals the number of games.
- **Teammate out**: if a star teammate is out tonight (star = 6+ of the last 10 games at 28+ min and 17+ pts), compare the player's average without him and with him over the last 30 games (needs at least 3 without and 5 with). Factor = 1 + 0.2 × (ratio − 1).
- **Back-to-back**: ×0.97 if the team played within 1.3 days.
- **Blowout**: if the spread is 10 or more and the player is a starter (6+ starts in the last 10), factor = max(0.85, 1 − 0.02 × (spread − 8)).
- **Soccer team goals**: use the team's own scoring AND the opponent's goals conceded, blended with a Poisson shots-on-target model.
  - λ = conversion × (team's SOT per game + opponent's SOT allowed per game) / 2, using the last 10 games.
  - conversion = goals ÷ SOT across these games (default 0.3 if fewer than 100 SOT).
- **Dropped because they made predictions worse**: pace, and stronger matchup or teammate strengths.

How they are applied to a prop: every one of the player's last 15 values is multiplied by (matchup factor × teammate factor × b2b × blowout), then the recency-weighted hits at the line are counted.

---

## 6. Player parlays: Ladder / Low / Medium / Fly (services/tiered_parlays.py)

This uses only ESPN's free DraftKings board (no credits), for the NBA and soccer.

**Candidate legs (NBA)**:
- Every DraftKings main-line over/under plus every "X+" alternate, for each game on today's slate (up to 15 games).
- The player needs 8+ of his last 15 games available, and 18+ average minutes over the last 5. He must not be on the injury list.
- `hits, n` = the record at that exact line over the last 15 games. `adjusted` = recency-weighted hits after the context factors in §5b.
- **Leg chance** = `with_market(adjusted, n, price) = (adjusted + 30/price) / (n + 30)`. In words, the book's implied chance (1/price, **including vig**) counts as 30 games of evidence. That is then averaged 50/50 with the ScorPred model's chance at that line, when the model covers the prop.
- If several books or duplicates offer the same (player, stat, side, line), the best price is kept.
- **Story flag**: a leg is a "story" pick when the matchup story (§5a) points the same way as the leg AND the adjusted record agrees (adjusted × 2 ≥ n).

**Candidate legs (soccer)**:
- Player shots, SOT, assists, fouls, saves and goals. The player needs 45+ average minutes over the last 5 and 8+ games. Records come from the domestic league plus this competition.
- Game legs:
  - **BTTS** from both teams' last 10 games (20 samples).
  - **Match goals over/under** at DraftKings' main line.
  - **Team total goals**: the sample is the team's own goals scored (last 15) plus the opponent's goals conceded (last 15). The team's own record AND the opponent's record must each back the side at 50% or more. The leg's chance blends the record (n games), the SOT Poisson model (10 games of weight) and the book (30 games of weight).
- A soccer line with no price never enters a parlay. There is an "unpriced" mode that emails record-only picks at 62% or more.

**Building the tiers**:
- **Fair legs only**: chance × price ≥ 1.0 (`MIN_VALUE`).

| Tier | Total payout band | Leg price range | Legs |
|---|---|---|---|
| Ladder (my $10→$20 challenge) | 1.85–2.3× | 1.15–2.30 | 1–3, all from different games |
| Low | 1.5–2.0× | 1.10–1.45 | 2–4 |
| Medium | 2.0–5.0× | 1.20–2.30 | 2–4 |
| Fly | 5.0–10.0× | 1.40–3.20 | 2–4 |

- Each tier's pool: the legs in its price range, ranked by chance × price, top 16. If enough legs are flagged as "story" picks, the tier tries a pool of story legs only first.
- **Search**: brute force over the combinations. Rules: one leg per player, and at most 2 legs from the same game (the Ladder allows 1 per game).
- **Objective**: maximise **hit probability** (the product of leg chances, **assuming independence**), then fewer legs, then EV. The combined odds must land inside the tier's band.
- Ladder bets: up to 3 separate ~2× bets with no game shared between them.
- **"Matchup edges" list**: story legs where chance − 1/price ≥ 0.03. The strongest story per player is kept, with a maximum of 6.
- Every leg gets plain-English reasons, generated from the same numbers that picked it ("X has had 20+ points in 12 of his last 15…", "We make it 64%; the price implies 58%").

---

## 7. Game-line parlays: "Parlay Mode" (parlay_engine.py)

- Slates: the NBA daily, the NFL on game days, and soccer daily across the top 5 leagues plus the UCL and UEL.
- Odds: ESPN's free scoreboard, one book (usually DraftKings), with `min_books = 1`. The Odds API (multiple books, credits) can be switched on instead.
- For each outcome (moneyline, spread, total):
  - `consensus` = the no-vig chance, averaged across books (with ESPN that means one book).
  - `prob` = 0.5 × model + 0.5 × consensus for **moneylines covered by a ScorPred model**. Otherwise it is the consensus alone.
  - `ev` = prob × best price − 1.
- **Picks**: score = prob + 1.5 × ev. Keep one per game and the top 5.
  - First pass: prob ≥ 0.55, price ≥ 1.25, ev ≥ −0.02.
  - Second pass (relaxed): prob ≥ 0.50.
  - Last resort: prob ≥ 0.50 and ev ≥ −0.06.
- **Parlay**: the 2–4 picks with the highest combined hit probability (independence again) whose odds are at least 2.0×.
- An all-sports parlay is built from the top 8 legs by probability.

---

## 8. Tracking and grading (services/pick_tracker.py and the model tracker)

- Every emailed pick and parlay is stored in SQLite: day, source (morning, midday or on-request), sport, kind, tier, odds, hit probability, and each leg's price, chance and grading facts.
- Grading uses ESPN finals and box scores (player stats, BTTS, goals, team totals, moneyline, spread, totals).
  - Games that are never finalised are voided after 3 days.
  - Pushes are recorded.
- Profit is "$1 per play" at the price when the pick was built. Records are kept by sport and by tier (Low/Medium/Fly/Ladder).
- Outputs: the morning recap (yesterday leg by leg plus the last 7 days), a Sunday weekly scorecard, and a chat query from Ano covering 1 to 90 days.
- The separate game-pick tracker (model_tracker) records every published soccer and NBA pregame pick, updates it until kickoff, grades it, and captures closing lines for game picks.
- **There is no closing-line-value tracking for the emailed player props.**

---

## 9. Self-adjusting layers

### 9a. Decision Engine (services/decision_engine.py)

This is used for soccer cards, the NFL, and to wrap NBA cards; on NBA cards the NBA model's own action overrides it.
- Probabilities are normalised to 100. **If they are missing, it falls back to 34/32/34.**
- `confidence`:
  - A numeric confidence is used as given.
  - A text label maps to high 80, medium 65 or low 45. If the probabilities are meaningful (top side over 40%), the side's own probability is used instead, but never more than 10 below the label value.
- `edge` = model probability − 1/odds (the odds still include vig). `EV` = model probability ÷ implied − 1.
- `data_quality`: Strong 85, Moderate 70, Partial 65, Limited 45. It is capped at 45 if the raw probabilities were missing.
- `risk_score` = max(0, (65 − confidence)/100) + max(0, (70 − data_quality)/100) + max(0, (15 − spread between top and bottom probability)/100). HIGH if ≥ 0.45, MEDIUM if ≥ 0.2, otherwise LOW.
- **Action**:
  - SKIP if the risk is above the maximum allowed.
  - With no market: CONSIDER if confidence ≥ the CONSIDER threshold, otherwise SKIP.
  - BET if confidence ≥ the BET threshold, edge ≥ 0.02 and risk is not HIGH.
  - CONSIDER if confidence ≥ the CONSIDER threshold and edge ≥ −0.01.
  - Otherwise SKIP.
- **Adaptive thresholds**: the base is BET 64 / CONSIDER 54 / maximum risk MEDIUM.
  - Trust under 55: BET threshold +4 to +5, maximum risk LOW. Trust over 75: BET threshold −2.
  - Calibration error over 12: BET threshold +3. Calibration score under 65: +2.
  - Recent accuracy (last 30) over 60%: BET threshold −1. Under 45%: +2.
  - Drift detected: BET threshold +2, +4 or +6 by severity, and the CONSIDER threshold rises by half that. Medium or high drift sets maximum risk to LOW.
  - The thresholds are clamped: BET 60–85, CONSIDER 50–75.
- **Trust guard**: trust under 55 moves BET down to CONSIDER, and CONSIDER down to SKIP.
- **Drift guard**: BET becomes CONSIDER (or SKIP at high drift unless confidence ≥ the BET threshold + 6). CONSIDER becomes SKIP at high drift.
- **Feature feedback**: plus or minus 3 confidence points from "feature attribution". Note: those "features" (form_score, h2h_score and so on) are actually derived from the output probabilities, not from real inputs. It is switched off when the soccer ratings model is active.
- **Grade**: A/B/C/D from 0.5 × confidence + 0.3 × data quality + 0.2 × (1 − risk) × 100 + 0.2 × edge × 100. Cutoffs are 82, 72 and 60.

### 9b. Calibration and trust (calibration_engine.py)

- Completed picks are bucketed by confidence (50–60, 60–70, … 90–100), and each bucket's predicted value is compared with its actual hit rate.
- calibration_score = 100 − mean absolute error. It needs at least 20 completed picks.
- Trust combines calibration and accuracy. Below 20 picks it is penalised: 0.3 × accuracy + 0.1 × maturity.

### 9c. Drift (drift_engine.py)

- Compares a short window (the last 20 graded picks) with a long one (the last 100), and needs at least 15 picks.
- Hard triggers:
  - The short-window win rate is more than 10 points below the long-window rate.
  - Calibration error has risen by more than 0.08.
  - The short-window win rate is under 45%.
- The drift score is smoothed: 0.7 × previous + 0.3 × new.

### 9d. Recalibration (services/recalibration.py)

- Two parameters per sport, so it cannot chase individual results:
  - Soccer: temperature T plus a draw offset.
  - NBA: logit(p)/T plus a home offset.
- Fitted on raw probabilities from graded pregame picks of the current engine version. It needs at least 200 picks, uses a 70/30 time split, and a ridge toward the identity map.
- It switches on only if the held-out log-loss improves by 0.005 or more, with a bootstrap interval above zero. Once on, it is refitted on every pick.

---

## 10. Ano (desktop assistant) layer

- Ano talks to ScorPred only over loopback (127.0.0.1:5001). It verifies the server's identity and refuses an unexpected service on that port.
- **Read-only chat tools**: game insights per sport and date, NBA/soccer/NFL prop reports, "check player X over Y line", the odds-credit status, and the pick record for the last N days.
- **Typed commands only** for anything that launches or emails: "nba picks", "player parlays", "morning email", and so on. The model cannot choose to send email. Email goes only to my address.
- Every command writes a job receipt. If a job was interrupted, it is marked "uncertain" and never replayed automatically.
- Background: Ano can dispatch work to a headless coding agent (full auto, with hard rules: paper/practice only, no payments, email only to me).

---

## 11. Things I already suspect. Please confirm, refute or rank them.

1. **The vig in the market prior.** `with_market` uses 1/price (vig included) as a 30-game prior. The no-vig fair chance would be lower. That pushes leg chances up toward break-even, so more legs pass `chance × price ≥ 1.0`.
2. **No margin of safety.** `MIN_VALUE = 1.0` means "zero EV by my own estimate". I choose the best of hundreds of lines (the winner's curse), so the real EV of the chosen legs is probably negative.
3. **The parlay objective** maximises hit probability inside a band, not EV. That favours the shortest prices, where the vig is often worst.
4. **The independence assumption**, with up to 2 legs from the same game. Same-game legs (a player's points and his team's total, for example) are correlated, and books price same-game parlays differently.
5. **Game-line parlays with one book**: consensus equals that book's own no-vig line, so spreads and totals have zero model input and EV of roughly −vig. Ranking by prob + 1.5 × EV then mostly picks favourites, and the fallbacks accept EV down to −6%.
6. **The decision engine is hand-tuned**: confidence is basically the side probability, the risk and grade formulas are arbitrary, the feature feedback uses fake features, and the drift and trust layers run on a small soccer win rate. Is any of this worth keeping?
7. **The NFL model** (pairs of recent scores, no home field) is very weak.
8. **The matchup application**: multiplying the last 15 raw values by one factor and then counting hits at the line is crude next to shifting a fitted distribution.
9. **Evaluation**: no CLV for props, small samples per tier, and a "record" view that can mislead. What is the minimum honest evaluation setup?
10. **Prices fresh for 5 minutes** in a 6 AM email, while lines move all day. Does the email-time price even matter?
