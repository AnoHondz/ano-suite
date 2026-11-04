"""One explicitly selected, budget-guarded GPT-4.1 synthetic streaming probe.

Uses the user's existing key locally. Sends only the constant public prompt below,
never app history, rules, files, tool schemas or diagnostics. No retries.
"""
from pathlib import Path
import json
import sys
import time
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    if sys.argv[1:] != ['--run-paid-public-probe']:
        raise SystemExit('Explicit --run-paid-public-probe flag required; no API request sent.')
    from openai import OpenAI
    from ano.credentials import load_openai_key
    from ano.paid import GuardedAPI, SpendLedger, MODEL
    from ano.paths import data_directory
    from ano.streaming import PartialText
    key = load_openai_key()
    if not key:
        raise SystemExit('No existing OpenAI key. No request sent.')
    started, first, raw, shown = time.monotonic(), [], [], []
    throttled = PartialText(lambda text: shown.append((time.monotonic(), text)))
    def partial(text):
        if not first:
            first.append(time.monotonic())
        raw.append(text)
        throttled(text)
    client = OpenAI(api_key=key, base_url='https://api.openai.com/v1', timeout=45, max_retries=0)
    guard = GuardedAPI(client, SpendLedger(data_directory() / 'paid-usage.sqlite3'), partial=partial)
    prompt = 'Write one short paragraph, about 25 words, explaining why rounding individual amounts can change their total. Use no tools.'
    try:
        result = guard.create(model=MODEL, messages=[{'role':'user','content':prompt}], max_completion_tokens=100)
        final = result.choices[0].message.content
        report = {'scope':'One real GPT-4.1 streamed completion through the existing spending guard, public synthetic prompt only. Not native frontend verification or broad reasoning evidence.',
                  'model':MODEL, 'passed':bool(final and raw and final == raw[-1]),
                  'seconds':round(time.monotonic()-started,3),
                  'first_text_seconds':round(first[0]-started,3) if first else None,
                  'raw_text_updates':len(raw), 'throttled_updates':len(shown),
                  'throttle_intervals_valid':all(b[0]-a[0]>=.1 for a,b in zip(shown,shown[1:])),
                  'api_calls':guard.calls,'estimated_usd':guard.spent,
                  'final_text':final,'usage_received':result.usage is not None,'overall_goal_complete':False}
    except Exception as error:
        body = getattr(error, 'body', None)
        details = body.get('error', body) if isinstance(body, dict) else {}
        report = {'scope':'Budget-guarded public synthetic streaming probe', 'passed':False,
                  'error_type':type(error).__name__,'api_calls':guard.calls,'reserved_or_estimated_usd':guard.spent,
                  'api_param':details.get('param'),'api_code':details.get('code'),
                  'api_message':str(details.get('message',''))[:500].replace(key,'[redacted]'),
                  'overall_goal_complete':False}
    finally:
        guard.close()
    out = Path(__file__).resolve().parents[1] / '.runtime/openai-stream-live-verification.json'
    if out.exists():
        previous = out.with_name('openai-stream-prior-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '.json')
        with previous.open('x', encoding='utf-8') as archive:
            archive.write(out.read_text(encoding='utf-8'))
    out.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))


if __name__ == '__main__':
    main()
