# NBA prop research integration

Open http://127.0.0.1:5001/nba/props/research while the local app is running.
Choose a date, load games, then load research. The screen supports points,
rebounds and assists. Missing critical evidence produces WAIT. Complete inputs
without validated book-line calibration produce PASS.

## Connected flow

- Existing ESPN schedules, injuries, roster checks, box histories and count models.
- Baseline context collection also runs without a promoted learned model.
- Exact oriented team and player/market joins; ambiguous identities are withheld.
- Standard and alternative NBA lines, paired over/under decimal prices, all matched
  lines, explicit integer-line push mass, no-vig comparisons and expected return.
- Distinct calibration keys for each market, alternatives and integer settlement.
- Append-only inputs, model source/parameter bundles, raw odds receipts, decision
  reports, analyst evidence and result snapshots in the research SQLite store.
- Source receipt times remain distinct from historical game dates. Capturing old
  data today never establishes that it was available before an earlier game.
- Hourly paper grading and hourly Daft Previews checks at 08:00–12:00 Eastern.
  The app must remain running. Caption candidates require review; they never
  increase statistical probabilities.
- Deterministic explanations and optional, explicitly requested local inference.
  The default local writer is the installed `qwen3:0.6b` on port 11434. Set
  `SCORPRED_LOCAL_LLM_URL` / `SCORPRED_LOCAL_LLM_MODEL` for another local model.
  Remote hosts, cloud model names, redirects and proxy forwarding are rejected.
  There is no paid fallback. The small local model selects fact IDs; only original
  recorded sentences are rendered. Invalid selections fall back to deterministic
  explanations. Generated prose cannot introduce claims or change saved decisions.
- Separate market summaries and recorded NO QUALIFYING PARLAY attempts. No
  parlay position is opened without actual combination pricing and joint validation.

## Endpoints

The bet365 line checker accepts the player's line and both American prices from
the user, without contacting a book or consuming provider credits. It requires
fresh saved pregame inputs (under five minutes), exact roster/player matching,
and all injury/environment freshness gates. A complete starting five is optional
when the player has three stable recent appearances for their current team.
Unvalidated leans are explicitly labelled; the validated decision remains PASS.
Manual scenarios are stored separately and never used as verified book-line
calibration samples. No bet is placed.

- `POST /api/nba/props/research/bet365-line` with session CSRF token
- `GET /api/nba/props/research/readiness`
- `GET /api/nba/props/research/games?date=YYYY-MM-DD` uses the schedule only,
  without waiting for unrelated game-winner calculations or their release gates.
- `GET /api/nba/props/research/results`
- `GET /api/nba/props/research/parlays?legs=2&target=2`
- `GET /api/nba/props/research/snapshots/<id>/explanation`
- `POST` the same explanation URL with `mode: deterministic | local_ai` and
  the session CSRF token. The UI supplies the token automatically.

## Requirements still preventing the full objective

This is an integrated research foundation, not a completed or validated betting
system. At the live verification the provider reported 499/500 credits used;
the app's separate conservative monthly comparison cap was also reached. Neither
limit was raised. The user confirmed no combined-parlay-price source exists.

Actual current quotes, sufficient lineup evidence and genuine later outcomes
are still necessary. Tracking-derived potential assists, rebound chances, travel,
usage and confirmed minutes restrictions are not invented when unavailable.
Observed shot/count rates are descriptive baselines, not tracking opportunities.

No actual combined-price adapter or empirically validated joint model is configured.
Parlays therefore remain NO QUALIFYING PARLAY. Same-game, repeated-player and
known shared-team dependencies are excluded; individual probabilities are not
multiplied. There are no parlay simulated returns to report.

Automatic results are forecast-only. The explicit grading function supports the
documented overtime/DNP-void contract only when supplied; unattended runs do not
assume a sportsbook has adopted it. Last observed pregame prices are labelled
as observations, not verified closing prices. Model validation needs genuine
forward samples and is not established by unit tests.

## Provider references

Alternative market names follow the provider's
[NBA market list](https://the-odds-api.com/sports-odds-data/betting-markets.html).
Provider quota headers and free schedule discovery are described in the
[v4 documentation](https://the-odds-api.com/liveapi/guides/v4/).
Local thinking control uses Ollama's documented
[compatibility endpoint](https://docs.ollama.com/api/openai-compatibility).
