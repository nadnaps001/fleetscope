# Data included with the online demo

This folder contains a small export of an evaluated FleetScope run. It lets the
website work on GitHub Pages without a Python server or a live taxi-data feed.

The export includes:

- Saved forecasts and recorded pickup counts for 407 eligible test hours.
- Official TLC polygons for the 262 modeled NYC taxi zones.
- Past hourly counts needed by each zone's history chart.
- Evaluation scores, data-quality information, and source checksums.

The export contains zone-level counts, not individual trip records. Values come
from the same run used in the results report. No example numbers were invented.

`data/manifest.json` lists checksums for every exported file. The website build
checks these files before publishing. To create a new export after evaluating a
new run, use `scripts/export_demo.py --output PATH_TO_A_NEW_EMPTY_FOLDER`, review
the results, and replace the snapshot intentionally.

Data source: [NYC Taxi & Limousine Commission](https://www.nyc.gov/site/tlc/about/tlc-trip-record-data.page).
The public data is subject to its source terms; it is not original data collected
by this project.
