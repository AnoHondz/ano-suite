"""Premium digest renderer contracts, no SMTP calls."""
from services.ano_email import brand
from services import daily_digest


def test_legacy_report_evidence_is_preserved_inside_premium_shell():
    html=brand('<html><body><h1>ScorPred daily sports digest</h1><h2>NBA</h2>'
               '<table><tr><td colspan="3">SKIP — no bet recommended. Source unavailable.</td></tr></table></body></html>',
               generated_at='2026-10-28T18:59:00+00:00')
    assert 'ano-premium-v2' in html
    assert 'ScorPred daily sports digest</h1>' not in html
    assert 'SKIP — no bet recommended. Source unavailable.' in html
    assert 'colspan="3"' in html
    assert 'Oct 28, 2026' in html
    assert html.count('cid:ano-logo')==1


def test_actual_digest_renderer_uses_shared_theme_and_real_counts():
    report=dict(generated_at='2026-10-28T18:59:00+00:00',window_end='2026-10-31T18:59:00+00:00',
                matches={s:[] for s in daily_digest.SPORTS},props={s:[] for s in daily_digest.SPORTS},
                notes=['No current roster-verified predictions.'],match_count=0)
    html=daily_digest.render(report)
    assert 'ano-premium-v2' in html
    # Short morning layout: top picks and one line per game, with real zero counts.
    assert 'Top picks' in html and 'No picks today' in html
    assert 'No qualified game forecasts.' in html
    assert 'Picks' in html and 'Games' in html
