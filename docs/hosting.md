# How the hosted demo works

FleetScope is hosted on GitHub Pages. The public website displays saved results
from the evaluated forecasting model. It does not train a model or run a Python
server when someone opens the page.

## Why use a saved replay?

The goal is to let a reviewer explore the project quickly. Predictions have
already been computed for the historical test period, so the browser can load
them directly. The map, time slider, comparisons, and zone history still work.

The Python data pipeline and FastAPI backend remain in the repository for anyone
who wants to reproduce the experiment or inspect the API locally.

## What gets published?

`scripts/build_site.py` creates the `_site/` folder from:

- The dashboard code in `src/fleetscope/web/`.
- The saved zone-level data in `demo/data/`.
- The repository details in `demo/project.json`.

The build verifies the saved data's checksums first. Raw trip files, local caches,
credentials, and Python environments are not part of the website.

## How updates reach the website

1. Push a change to the `main` branch.
2. GitHub Actions installs the locked dependencies and runs the tests.
3. It checks the code and builds the static website.
4. If those checks pass, it publishes the new website to GitHub Pages.

Pull requests run the checks without publishing. The workflow is in
[`.github/workflows/pages.yml`](../.github/workflows/pages.yml).

## Preview changes locally

```bash
python scripts/build_site.py
python -m http.server 8080 --directory _site --bind 127.0.0.1
```

Open `http://127.0.0.1:8080`.

## Publish results from a new model run

After reproducing and reviewing a new experiment, export it to an empty folder:

```bash
uv run --frozen --extra dev python scripts/export_demo.py --output artifacts/new-demo-data
```

Compare the export with the results report, then intentionally replace
`demo/data/` with the reviewed snapshot. Update the results and date labels to
match the new experiment. The export does not modify the trained model or its
evaluation scores.

See [GitHub's Pages documentation](https://docs.github.com/en/pages/getting-started-with-github-pages/what-is-github-pages)
for details about static hosting.
