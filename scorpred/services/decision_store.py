"""Short-lived canonical cards for addressable match analysis."""
import decision_ui
from services import cache_service
from services.prediction_lifecycle import freshness_state, is_pregame, sanitize_decision


def publish(cards, sport):
    decisions = [sanitize_decision(decision_ui.card_to_decision(sanitize_decision(dict(card, sport=sport)))) for card in cards]
    for decision in decisions:
        if decision["matchId"]:
            key = cache_service.make_key("decision", sport, decision["matchId"])
            if cache_service.get_json(key, ttl=300) != decision:
                cache_service.set_json(key, decision, ttl=300)
    return decisions


def get(sport, match_id, *, allow_stale=False):
    cached = cache_service.get_json(cache_service.make_key("decision", sport, match_id), ttl=300)
    # A presentation TTL must not suppress a refreshed NBA/soccer source.
    # A miss makes match, props and injury routes rebuild from the provider.
    if cached is not None and sport in {"nba", "soccer"}:
        source = cached.get("dataFreshness") or cached.get("data_freshness")
        refreshable = sport == "nba" or is_pregame(cached.get("matchDate"), cached.get("eventStatus"))
        missing_soccer = (sport == "soccer" and cached.get("side") == "Unavailable"
                          and not ((cached.get("dataQuality") or {}).get("state")=="waiting"
                                   and (cached.get("researchForecast") or {}).get("winProbability") is not None))
        if source and (freshness_state(source) != "fresh" or missing_soccer) and not allow_stale and refreshable:
            return None
    return sanitize_decision(cached) if cached is not None else None
