# Prediction tracking and learning

## What is recorded

- **When:** every 30 minutes, the `prediction_tracking` scheduler job records every published pregame pick for today and tomorrow, covering the Premier League, Bundesliga, La Liga and the NBA.
- **Revisions:** a pick can be revised until kickoff and is frozen after it.
- **Grading:** the same job grades games that started at least 110 minutes ago.
- **What each pick stores:**

| Field | Meaning |
|---|---|
| `prob_a`, `prob_draw`, `prob_b` | Published probabilities (after any learned correction) |
| `raw_probs` | The model's own probabilities, before correction; learning fits these |
| `predicted_score` | Expected goals or points, plus the most likely exact score for soccer |
| `market_at_pick`, `closing_market` | Margin-free prices when picked and just before kickoff (NBA always; soccer needs `ODDS_API_KEY`) |
| `engine` | Code fingerprint and model version (`soccer-ratings-v1`, `nba-pregame-margin-v2`) |
| `final_score`, `is_correct`, `score_grade` | Result, pick outcome, and score error (per team, total, exact-score hit) |

**Where to see it:** Insights → Track record shows:
- accuracy with 95% ranges;
- log loss and calibration;
- closing line value;
- score-forecast error;
- the 30 most recent verified pregame picks, predicted against the actual result;
- learning status.

Only picks first recorded before a known kickoff count.

## How the system learns

1. **Ratings learn from every result.** Soccer refits Elo and Dixon-Coles from league results every six hours; the NBA ratings use every completed game.
2. **Published probabilities are checked against graded picks once a day** (`recalibration` job, and the nightly `daily_refresh.py`). The check fits a two-parameter correction per sport:
   - soccer: a temperature and a draw offset;
   - NBA: a temperature and a home offset.

   The correction is only switched on when all of these hold:
   - at least 200 graded pregame picks from the current engine version;
   - it is fitted on the oldest 70% of those picks;
   - it lowers log loss on the newest 30% by at least 0.005;
   - the bootstrap interval for that improvement lies above zero.

   It resets when the engine version changes. Decisions are saved in `persistent/recalibration.json`.

The older `learn_from_mistakes` adjustments still run nightly, but they affect only the legacy ScorMastermind pages.

## Saving picks

Signed-in users can save a pick from the match analysis page. Saved picks (`/my-bets`) show Won or Lost once the tracked result for that match is graded.
