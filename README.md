# Ano + ScorPred

Two personal projects that run together on one Windows laptop.

| Folder | What it is |
|---|---|
| [`ano/`](ano/) | **Ano**: a desktop assistant (PySide6/QML) with a supervisor, a live wallpaper status feed, email reports, background Claude Code jobs, and paper/practice trading bots (Alpaca paper stocks, OANDA practice forex). Practice accounts only. |
| [`scorpred/`](scorpred/) | **ScorPred**: a Flask + React sports-prediction app for NBA, soccer and NFL: game models, player-prop parlays (Ladder, Easy, Medium, Fly, 5-leg), matchup stories, and a pick tracker that grades every emailed pick and records closing prices. |

Each folder has its own README with setup and commands.

## What is not in this repo

Secrets and personal data never leave the laptop: `.env` files and API keys, databases (pick tracker, memory, trading ledgers), caches, ScorPred's `data/` and `persistent/` folders, virtual environments, and Ano's release builds. Copy `scorpred/.env.example` to `.env` and add your own keys to run ScorPred.

## Saving changes

`sync.ps1` copies the latest source from the working folders, scrubs private details, refuses to push anything that looks like a key, then commits and pushes:

```powershell
& "$HOME\Documents\ano-suite\sync.ps1" "what changed"
```

## Disclaimer

Research and paper trading only. Nothing here is financial or betting advice; picks are estimates, not guarantees.
