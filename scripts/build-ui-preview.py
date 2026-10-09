"""Build an offline synthetic-data preview without model calls or customer data."""
from pathlib import Path

root = Path(__file__).resolve().parent.parent
html = (root / 'frontend/index.html').read_text()
html = html.replace('<link rel="stylesheet" href="/assets/styles.css">', '<style>' + (root / 'frontend/styles.css').read_text() + '</style>')
html = html.replace('<script src="/assets/app.js" defer></script>', '')
script = (root / 'frontend/tests/browser-fixture.js').read_text() + '\n' + (root / 'frontend/app.js').read_text()
html = html.replace('</body>', '<script>' + script + '</script></body>')
out = root / 'data/preview/index.html'
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(html)
print(out)
