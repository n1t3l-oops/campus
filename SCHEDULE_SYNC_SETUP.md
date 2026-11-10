# Dnevnik.ru schedule sync

Campus reads the public `schedule.json` file from this repository. The local Windows script collects the previous, current and next weeks from the authorized Dnevnik.ru session and publishes the result through GitHub CLI.

## One-time setup on the Windows PC

1. Close any running Dnevnik schedule checker so it does not lock the Edge profile.
2. In `C:\Users\yay\Desktop\dnevnik_test`, replace the old checker with this repository version:
   ```bat
   curl -L https://raw.githubusercontent.com/n1t3l-oops/campus/main/check_dnevnik.py -o check_dnevnik.py
   ```
3. Install GitHub CLI if it is not installed:
   ```bat
   winget install --id GitHub.cli -e
   ```
4. Open a new CMD window and authorize the GitHub account that can write to `n1t3l-oops/campus`:
   ```bat
   gh auth login
   gh auth status
   ```
5. Run one manual test:
   ```bat
   cd /d C:\Users\yay\Desktop\dnevnik_test
   .venv\Scripts\python.exe check_dnevnik.py
   ```
6. Check `monitor.log` and the public `schedule.json`. The script keeps the existing persistent Edge profile at `dnevnik_profile`; do not delete it.

The existing Task Scheduler task can continue to run `check_dnevnik.py` every 30 minutes after the manual test succeeds. Keep the PC powered on and connected to the internet.

## Status mapping

- `dLP`: normal scheduled lesson (blue).
- `dLC`: cancelled lesson (red).
- `dLO`: changed lesson (green).

Each lesson is stored separately by its Dnevnik.ru lesson ID. Lessons sharing the same date and pair are not merged.
