from datetime import UTC, datetime
from unittest.mock import Mock

from services.cloud_refresh import refresh_nfl, DATASETS


def test_cloud_refresh_only_requests_serving_inputs_and_previous_season():
    syncer = Mock(return_value=[])
    refresh_nfl(now=datetime(2026, 10, 25, tzinfo=UTC), syncer=syncer)
    syncer.assert_called_once_with([2025, 2026], DATASETS, refresh=True, keep_going=True)
    assert not {"pbp", "participation", "ftn_charting"}.intersection(DATASETS)


def test_january_keeps_nfl_season_year_instead_of_calendar_year():
    syncer = Mock(return_value=[])
    refresh_nfl(now=datetime(2027, 1, 15, tzinfo=UTC), syncer=syncer)
    assert syncer.call_args.args[0] == [2025, 2026]
