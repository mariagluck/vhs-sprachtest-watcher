# VHS Sprachtest watcher

Watches the Berlin VHS course database for "Sprachtest zur Einbürgerung" dates and pushes an alert via [ntfy](https://ntfy.sh) when a place opens or a new date is listed.

Runs every 10 minutes on GitHub Actions (`NTFY_TOPIC` repo secret), or locally: `python3 watch.py --ntfy <topic>`.
