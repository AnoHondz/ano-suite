"""Public synthetic shape fixture through Ano's image tool and a loopback vision model.

No private image, paid service, app action or model-default change is involved.
"""
from pathlib import Path
import json
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ano.tools import Toolbox
from ano.tool_media import ToolImage


def main():
    from PIL import Image, ImageDraw
    from openai import OpenAI
    root = Path(__file__).resolve().parents[1] / '.runtime'
    fixture = root / 'vision-public-shapes-20261027.png'
    if not fixture.exists():
        image = Image.new('RGB', (600, 240), 'white')
        draw = ImageDraw.Draw(image)
        draw.ellipse((35, 55, 165, 185), fill='red')
        draw.rectangle((235, 55, 365, 185), fill='blue')
        draw.polygon(((500, 45), (425, 185), (575, 185)), fill='green')
        with fixture.open('xb') as stream:
            image.save(stream, format='PNG')
    output, _, failed = Toolbox(lambda: [root]).run('view_image_file', {'path': str(fixture)})
    assert not failed and isinstance(output, ToolImage)
    # Dedicated loopback-only call. Check the app and other probes are idle first.
    client = OpenAI(base_url='http://127.0.0.1:11434/v1', api_key='local-unused', max_retries=0, timeout=300)
    started = time.monotonic()
    result = client.chat.completions.create(model='ano-local:latest', temperature=0, max_tokens=150,
        messages=[{'role': 'user', 'content': output.gpt() + [{'type': 'text', 'text':
            'Describe only the three shapes, from left to right. Give each color and shape. Be brief.'}]}])
    text = result.choices[0].message.content or ''
    expected = ('red', 'circle', 'blue', 'square', 'green', 'triangle')
    positions = [text.lower().find(word) for word in expected]
    report = {'scope': 'One public synthetic fixture passed through actual Ano image tool into local vision inference. Not a native Ano command, not general visual accuracy.',
              'model': 'ano-local:latest', 'seconds': round(time.monotonic()-started, 3),
              'response': text, 'expected_terms_in_order': all(x >= 0 for x in positions) and positions == sorted(positions),
              'fixture': str(fixture), 'overall_goal_complete': False}
    (root / 'local-image-view-probe.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
