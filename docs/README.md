# docs/

This directory is the GitHub Pages output folder.

`docs/index.html` is **generated automatically** by `scripts/build_html.py`
and deployed via the CI workflow in `.github/workflows/pages.yml`.

To build locally:
```bash
PYTHONPATH=src python scripts/scan.py    # fetch live data → data/charts/
python scripts/build_html.py             # generate docs/index.html
open docs/index.html                     # preview in browser
```

