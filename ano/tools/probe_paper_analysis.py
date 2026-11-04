"""Real Toolbox dispatch and rendered synthetic chart. Never uses a broker or model."""
import json
import math
import os
from pathlib import Path
import time

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from PySide6.QtGui import QFontDatabase, QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer
from ano.plugins.paper_trading import PaperTrading
from ano.plugins.workshop import Workshop
from ano.tools import Toolbox


def main():
    app = QGuiApplication.instance() or QGuiApplication([])
    QFontDatabase.addApplicationFont(str(Path('ano/assets/fonts/MPLUSRounded1c-Regular.ttf').resolve()))
    root = Path('.runtime') / ('paper-analysis-fixture-' + str(time.time_ns()))
    root.mkdir()
    start, rows = 1780000000, []
    for i in range(70):
        o, c = 100+math.sin(i*.45)*4, 100+math.sin((i+1)*.45)*4
        rows.append(dict(t=start+i*60, o=round(o,2), h=round(max(o,c)+.5+(i%3)*.07,2),
                         l=round(min(o,c)-.5-(i%4)*.04,2), c=round(c,2)))
    plugin = PaperTrading(Workshop(root,None), clock=lambda:start+70*60)
    box = Toolbox(lambda:[root], lambda:True, plugins=[plugin])
    text, _, failed = box.run('analyze_paper_candles',dict(symbol='SYNTHETIC:INDEX',
        source='Synthetic deterministic fixture; no real market feed', candles=rows))
    assert not failed, text
    result = json.loads(text)
    renderer = QSvgRenderer(result['chart'])
    assert renderer.isValid()
    image = QImage(1000,480,QImage.Format_ARGB32)
    image.fill(0xffffffff)
    painter = QPainter(image)
    renderer.render(painter)
    painter.end()
    preview = root/'preview.png'
    assert image.save(str(preview))
    result.update(preview=str(preview.resolve()), passed=True,
        scope='Real Toolbox dispatch with synthetic data. No feed, monitoring or paper execution.',
        visual_check='Pending inspection')
    Path('.runtime/paper-analysis-verification.json').write_text(json.dumps(result,indent=2))
    print(preview.resolve())


if __name__ == '__main__': main()
