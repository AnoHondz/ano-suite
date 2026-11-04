import importlib.util
from pathlib import Path

import numpy as np

_SPEC = importlib.util.spec_from_file_location(
    "evaluate_against_market", Path(__file__).resolve().parents[1] / "scripts" / "evaluate_against_market.py")
market = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(market)


def test_dataset_names_match_football_data_names_including_mis_encoded_accents():
    assert market._similarity("Atl�tico Madrid", "Ath Madrid") == 1.0
    assert market._similarity("Atlético Madrid", "Ath Madrid") == 1.0
    assert market._similarity("Borussia M�nchengladbach", "M'gladbach") == 1.0
    assert market._similarity("Brighton & Hove Albion", "Brighton") == 1.0
    assert market._similarity("Arsenal", "Arsenal") == 1.0
    assert market._similarity("Arsenal", "Aston Villa") < 0.6


def test_margin_is_removed_proportionally():
    probabilities = market._devig(np.array([[2.0, 3.4, 3.8]]))
    assert np.isclose(probabilities.sum(), 1.0)
    assert probabilities[0, 0] > probabilities[0, 1] > probabilities[0, 2]


def test_season_codes_cover_the_test_window():
    from datetime import date
    assert market._season_codes(date(2025, 5, 23), date(2026, 4, 19)) == ["2425", "2526"]
