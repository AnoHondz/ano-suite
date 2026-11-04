"""Page-context and chat-assistant helpers."""

from __future__ import annotations

from datetime import datetime


def now_stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def football_data_source(api_client) -> str:
    api_key = str(getattr(api_client, "API_KEY", "") or "").strip()
    if not api_key:
        return "ESPN fallback (missing API_FOOTBALL_KEY / RAPIDAPI_KEY)"
    if getattr(api_client, "_USING_DIRECT_APISPORTS", False):
        return "API-Football (api-sports.io direct)"
    if getattr(api_client, "RAPIDAPI_OK", False):
        return "API-Football via RapidAPI"
    forbidden = getattr(api_client, "_FORBIDDEN_ENDPOINTS", set()) or set()
    if forbidden:
        return "ESPN fallback (RapidAPI 403/plan block)"
    return "ESPN public football fallback"


def page_context(api_client, data_source: str | None = None, **kwargs) -> dict:
    context = {
        "data_source": data_source or football_data_source(api_client),
        "last_updated": now_stamp(),
        "is_stale": kwargs.pop("is_stale", False),
    }
    context.update(kwargs)
    return context


def _context_aware_fallback(message: str, page_ctx: dict) -> tuple[str, list[str]]:
    """Return (reply, suggestions) using rich page context."""
    lower = (message or "").strip().lower()
    kind = page_ctx.get("page_kind", "")

    if kind in ("soccer_prediction", "nba_prediction"):
        team_a = page_ctx.get("team_a", "")
        team_b = page_ctx.get("team_b", "")
        matchup = f"{team_a} vs {team_b}" if team_a and team_b else "this matchup"
        winner = page_ctx.get("winner_pick", "")
        prob = page_ctx.get("winner_probability")
        confidence = page_ctx.get("confidence", "")
        totals = page_ctx.get("totals_pick", "")
        reasoning = page_ctx.get("reasoning", "")

        prob_str = f"{prob:.1f}%" if prob is not None else ""
        reply_parts = [f"For {matchup}, the model picks {winner}" if winner else f"Prediction for {matchup}"]
        if prob_str:
            reply_parts.append(f"with {prob_str} win probability")
        if confidence:
            reply_parts.append(f"({confidence} confidence)")
        reply = " ".join(reply_parts) + "."
        if reasoning:
            reply += f" {reasoning}"
        if totals:
            reply += f" Totals pick: {totals}."

        if kind == "nba_prediction" and ("spread" in lower or "market" in lower or "totals" in lower):
            reply = (
                f"For {matchup}: winner market predicts outright result, "
                f"spread is margin-based (must win/lose by threshold), "
                f"totals is combined points over/under a line. "
                f"Model pick: {winner} ({prob_str})."
            )
            if totals:
                reply += f" Totals: {totals}."

        suggestions = [
            f"What drives the {confidence.lower()} confidence?" if confidence else "What factors drive confidence?",
            "How does the model use form data?",
            "What is the totals pick based on?",
        ]
        return reply, suggestions

    if kind == "result_detail":
        team_a = page_ctx.get("team_a", "")
        team_b = page_ctx.get("team_b", "")
        winner_leg = page_ctx.get("winner_leg", "Pending")
        totals_leg = page_ctx.get("totals_leg", "Pending")
        overall = page_ctx.get("overall_result", "")
        score = page_ctx.get("final_score", "")
        actual_winner = page_ctx.get("actual_winner", "")

        hit_str = "graded hit" if winner_leg == "Hit" else "graded miss" if winner_leg == "Miss" else "pending"
        ou_str = "graded hit" if totals_leg == "Hit" else "graded miss" if totals_leg == "Miss" else "pending"
        reply = (
            f"Result for {team_a} vs {team_b}: winner leg {hit_str}, totals leg {ou_str}. "
            f"Overall: {overall or 'Pending'}."
        )
        if score:
            reply += f" Final score: {score}."
        if actual_winner:
            reply += f" {actual_winner} won."
        suggestions = ["How is the overall result calculated?", "Why did the winner leg miss?"]
        return reply, suggestions

    if kind == "model_performance":
        accuracy = page_ctx.get("overall_accuracy")
        wins = page_ctx.get("wins")
        losses = page_ctx.get("losses")
        grading = page_ctx.get("grading_logic", "")
        tracked = (wins or 0) + (losses or 0)

        acc_str = f"{accuracy:.1f}%" if accuracy is not None else "N/A"
        wl_str = f"{wins} wins and {losses} losses" if wins is not None and losses is not None else "tracked results"
        sample_note = "This is still an early live sample." if tracked < 8 else "This reflects live tracked results."
        reply = f"Live tracked accuracy: {acc_str} ({wl_str}). {sample_note}"
        if grading:
            reply += f" {grading}"
        if "winner leg" not in reply:
            reply += " Grading separates winner leg, totals leg, and overall verdict."
        suggestions = ["How is accuracy calculated?", "What counts as a hit?"]
        return reply, suggestions

    return "", []


def fallback_chat_reply(
    message: str,
    *,
    team_a: dict | None = None,
    team_b: dict | None = None,
    page_ctx: dict | None = None,
) -> tuple[str, list[str]]:
    """Return (reply, suggestions) for fallback (no API key) mode."""
    if page_ctx:
        reply, suggestions = _context_aware_fallback(message, page_ctx)
        if reply:
            return reply, suggestions

    lower = (message or "").strip().lower()
    matchup = f"{team_a['name']} vs {team_b['name']}" if team_a and team_b else "your selected matchup"

    if any(kw in lower for kw in ("scoreline", "correct score", "exact score", "score distribution", "score line")):
        return (
            f"The Scoreline Predictor gives you a full probability distribution of every possible "
            f"result for {matchup}. It uses a Dixon-Coles corrected Poisson model (soccer) or "
            f"Normal distribution (NBA) blended with ML win probabilities. "
            f"You'll get the most likely scorelines, a heat-map grid, over/under probabilities "
            f"for each line, and BTTS odds (soccer only).",
            ["What is the most likely score?", "Show me O/U probabilities", "What does BTTS mean?"],
        )
    if "props" in lower:
        return (
            f"Use the Props page to generate player lines for {matchup}. Pick a player, "
            "choose markets, and the app will build a bet slip from live stats.",
            ["How are props calculated?"],
        )
    if "prediction" in lower or "winner" in lower:
        return (
            f"The Prediction page combines form, matchup context, and probability modeling "
            f"to judge {matchup} and explain whether the edge looks worth trusting.",
            ["What model factors matter most?"],
        )
    if "player" in lower:
        return (
            "The Player page compares squad members side by side and can generate prop "
            "ideas from their season profile and opponent context.",
            ["How are player props generated?"],
        )
    if "nba" in lower:
        return (
            "The NBA section has its own home, matchup, player, prediction, and standings views under /nba.",
            ["Show me NBA predictions"],
        )
    return (
        "Ask about matchup analysis, player props, prediction logic, injuries, or where "
        "to find a specific football or NBA view.",
        ["How does prediction work?", "Where are player props?"],
    )


def chat_reply(
    message: str,
    *,
    history: list[dict] | None = None,
    anthropic_module=None,
    api_key: str = "",
    team_a: dict | None = None,
    team_b: dict | None = None,
    page_ctx: dict | None = None,
    logger=None,
) -> dict:
    # This helper is deliberately deterministic. Legacy keys and injected SDKs
    # cannot bypass Ano's explicit paid-task gate or its spending ledger.
    reply, suggestions = fallback_chat_reply(message, team_a=team_a, team_b=team_b, page_ctx=page_ctx)
    return {"reply": reply, "suggestions": suggestions, "mode": "fallback", "intent": None,
            "cost_policy": "free_deterministic_no_paid_fallback"}
