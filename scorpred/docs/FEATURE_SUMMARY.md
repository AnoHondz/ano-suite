# ScorPred Feature Summary

Last updated: 2026-04-12

## Overview
ScorPred is a Flask-based soccer and NBA prediction platform with:
- Multi-league soccer coverage
- Unified ScorPred prediction outputs
- Result tracking and model performance analytics
- Daily prediction pages and top-pick surfacing

## Core Product Areas

### Soccer
- Team selection, matchup analysis, and prediction pages
- 3-way outcomes: home win, draw, away win
- Grouped upcoming predictions across supported leagues
- High-confidence top picks grouped by league

Primary routes:
- `/soccer`
- `/matchup`
- `/prediction`
- `/today-soccer-predictions`
- `/top-picks-today`

### NBA
- Live scoreboard, matchup, player, standings, and prediction pages
- Daily NBA predictions page for today's/next available games
- ScorPred engine used as the prediction source in the NBA prediction flow

Primary routes:
- `/nba/`
- `/nba/matchup`
- `/nba/player`
- `/nba/prediction`
- `/nba/today-predictions`
- `/nba/standings`

### Tracking and Performance
- Predictions are stored in `cache/prediction_tracking.json`
- Soccer records include `league_id` and `league_name`
- Duplicate prevention uses sport, date, teams, and soccer league id
- Model dashboard includes:
  - Overall accuracy
  - By confidence
  - By sport
  - Soccer accuracy by league
  - Recent, pending, and completed predictions

Primary routes:
- `/model-performance`
- `/update-prediction-results`

## Prediction Engines

### Soccer
- Poisson-based outcome model with draw support
- Weighs form, H2H, home/away, injuries, and expected-goal style signals

### NBA
- ScorPred weighted score model with these factors:
  - Form (40%)
  - Attack (15%)
  - Defense (15%)
  - H2H (10%)
  - Venue (8%)
  - Opponent quality (7%)
  - Squad (5%)

Outputs include:
- Team score out of 10
- Win probabilities
- Confidence tier
- Best pick
- Key edges
- Matchup reading

## API/Utility Endpoints
- `/api/football/leagues`
- `/api/football/teams`
- `/api/football/squad`
- `/api/football/team-form`
- `/api/player-stats`
- `/chat`
- `/chat/clear`
- `/health`

## Testing
Recommended regression set for current prediction/tracking behavior:

```bash
pytest tests/test_routes.py tests/test_tracking_and_updater.py -q
```
