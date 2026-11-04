"""Read saved research evidence. This module never authorizes or sends orders."""
import json
from pathlib import Path


def evidence_status(workspace=None):
    workspace=Path(workspace or Path.home()/'Documents/AnoWorkspace')
    lines=['Best research model: no automatic strategy switch or order was submitted by this command.']
    for prefix,label,key in (('ICT-research-','ICT','best_in_sample'),('Session-research-','Session strategies','best')):
        paths=sorted(workspace.glob(prefix+'*.json'),reverse=True)
        try:
            path=paths[0]
            if path.stat().st_size>2_000_000:raise ValueError()
            report=json.loads(path.read_text(encoding='utf-8'))
            best=report.get(key) or {};oos=(best.get('oos') or {}).get('both') or {}
            verdict='PASSED in the saved simulation report' if report.get('passed') is True else 'NOT PASSED'
            lines.append(f'{label}: {best.get("strategy",label)}; out-of-sample gate {verdict}; '
                f'{oos.get("trades","unknown")} simulated trades, average net R {oos.get("average_r","unknown")}, '
                f'profit factor {oos.get("profit_factor","unknown")}. Evidence: {path.name}.')
        except (IndexError,OSError,ValueError,TypeError,AttributeError):
            lines.append(label+': comparable research evidence unavailable; no winner inferred.')
    lines.append('These reports cover their tested variants, not proof that all configured research models were validated. '
                 'A saved backtest result is not a broker-fill record. Automatic performance-based best-model selection is not implemented. '
                 'Execution permission is configured separately: use trading bot readiness for the actual enabled adapters. '
                 'Named experimental paper adapters require explicit enablement and shared risk checks; '
                 'saved simulations never automatically grant execution permission. Live trading stays disabled.')
    return '\n'.join(lines)
