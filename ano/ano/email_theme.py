"""Ano premium email v2. Keep the standalone ScorPred copy byte-identical.

Inline styles and presentation tables keep essential layout readable in mail
clients that remove style blocks. No scripts, remote fonts, tracking or assets.
Only titles/labels are raw text; content is trusted, escaped report markup.
"""
from datetime import datetime
from html import escape
import re
from zoneinfo import ZoneInfo

THEME_VERSION = 'ano-premium-v2'


def style_content(content):
    # Preserve source text, forecast distinctions and table semantics.
    content = re.sub(r'<h2(?:\s[^>]*)?>', '<h2 style="font:700 20px Arial,sans-serif;color:#142f37;margin:30px 0 14px;padding-bottom:12px;border-bottom:2px solid #d5b777">', content)
    content = re.sub(r'<h3(?:\s[^>]*)?>', '<h3 style="font:700 16px Arial,sans-serif;color:#1b4c4e;margin:24px 0 12px">', content)
    content = re.sub(r'<table(?:\s[^>]*)?>', '<table cellpadding="0" cellspacing="0" style="border-collapse:collapse;width:100%;table-layout:fixed;font-size:13px;line-height:1.6">', content)
    content = re.sub(r'<td(?:\s[^>]*)?>', lambda m: '<td'+(' colspan="'+re.search(r'colspan=[\"\x27]?(\d+)',m[0])[1]+'"' if re.search(r'colspan=[\"\x27]?(\d+)',m[0]) else '')+' style="padding:12px 8px;border-bottom:1px solid #e1e7e3;vertical-align:top;overflow-wrap:anywhere;word-break:break-word">',content)
    content = re.sub(r'<th(?:\s[^>]*)?>', '<th style="padding:12px 8px;background:#eaf0eb;color:#264a43;text-align:left;vertical-align:top;overflow-wrap:anywhere">', content)
    return content


def premium_frame(title, content, *, token='', when=None, subtitle='Sports intelligence', metrics=(), reply_help=None):
    when = when or datetime.now(ZoneInfo('America/New_York'))
    stamp = when.astimezone(ZoneInfo('America/New_York')).strftime('%a, %b %d, %Y · %I:%M %p ET')
    metric_cells=''.join('<td width="'+str(100//len(metrics))+'%" style="padding:15px 10px;border:1px solid #dfe5dc;vertical-align:top">'
                        '<div style="font-size:10px;letter-spacing:1px;text-transform:uppercase;color:#607368">'+escape(str(label))+'</div>'
                        '<div style="font:700 21px Arial,sans-serif;color:#183f38;margin-top:5px">'+escape(str(value))+'</div></td>'
                        for label,value in metrics)
    metric_table=('<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:20px 0;table-layout:fixed"><tr>'+metric_cells+'</tr></table>') if metrics else ''
    commands=('Reply with <b>picks</b>, <b>nba props</b>, <b>soccer picks</b>, <b>nfl picks</b>, '
              '<b>trading status</b>, <b>status</b> or <b>help</b> on the first line. Leave the subject unchanged. '
              'Only replies authenticated from your own account are accepted. Your laptop must be on and online.'
              if token else 'Enable email commands in Ano to request fresh reports from your phone. Your laptop must be on and online.')
    if reply_help is not None:
        commands=escape(reply_help)
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="light"><title>ANO · {escape(subtitle)}</title>
<style>@media(max-width:520px){{.mail-pad{{padding:22px 16px!important}}.mail-title{{font-size:26px!important}}.mail-shell{{border-radius:0!important}}}}</style></head>
<body data-ano-theme="{THEME_VERSION}" style="margin:0;padding:0;background:#e8ece7;color:#243e39;font:15px Arial,sans-serif;line-height:1.7;-webkit-text-size-adjust:100%">
<div style="display:none;max-height:0;overflow:hidden;opacity:0">Your Ano sports briefing — forecasts, evidence and availability in one place.</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#e8ece7"><tr><td align="center" style="padding:24px 0">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" class="mail-shell" style="max-width:720px;background:#fafbf7;border:1px solid #d8e0d7;border-radius:18px;overflow:hidden">
<tr><td style="height:5px;background:#c2a062;font-size:1px;line-height:5px">&nbsp;</td></tr>
<tr><td class="mail-pad" style="padding:30px 34px;background:#102f2d;color:#fff">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td width="58" style="vertical-align:middle"><img src="cid:ano-logo" alt="Ano" width="48" height="48" style="display:block;border:0;border-radius:12px"></td>
<td style="vertical-align:middle;color:#fff"><span style="font:bold 27px Arial,sans-serif;letter-spacing:5px">ANO</span><br><span style="font-size:11px;letter-spacing:2px;color:#d7c28f;text-transform:uppercase">Personal intelligence</span></td>
<td align="right" style="font-size:10px;letter-spacing:1px;color:#d7c28f">SPORTS<br>DESK</td></tr></table>
<p style="font-size:11px;letter-spacing:2px;color:#d7c28f;text-transform:uppercase;margin:28px 0 10px">{escape(subtitle)}</p>
<h1 class="mail-title" style="font:700 32px Georgia,serif;line-height:1.25;margin:0;color:#fff">Hey boss, {escape(title)}</h1>
<p style="font-size:12px;color:#bfd1c6;margin:16px 0 0">{escape(stamp)}</p></td></tr>
<tr><td class="mail-pad" style="padding:26px 34px 32px">{metric_table}
<div style="font-size:11px;letter-spacing:1px;text-transform:uppercase;color:#7a663a;border-bottom:1px solid #dfe5dc;padding-bottom:14px">ScorPred research · Evidence before action</div>
{style_content(content)}
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-top:30px;background:#edf1e8;border:1px solid #d8e1cf;border-radius:12px"><tr><td style="padding:20px">
<div style="font-size:11px;letter-spacing:1px;text-transform:uppercase;color:#6c6c42">Your assistant, wherever you are</div><h3 style="font-size:16px;margin:6px 0;color:#173f36">Talk to me from your phone</h3><p style="font-size:13px;margin:8px 0 0">{commands}</p></td></tr></table>
<p style="font-size:11px;line-height:1.7;color:#68786e;margin:24px 0 0">Forecasts are estimates, not guarantees. Historical and model prop targets are not verified sportsbook offers. No bets placed. Generated locally by Ano using ScorPred.</p>
</td></tr><tr><td align="center" style="background:#102f2d;padding:17px;color:#d7c28f;font-size:10px;letter-spacing:2px">ANO &nbsp; / &nbsp; YOUR PERSONAL ASSISTANT</td></tr>
</table></td></tr></table></body></html>'''
