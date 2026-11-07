# Multiplatform LIVE Trivia

A local Python application for one shared trivia game receiving comments from TikTok LIVE and Facebook LIVE. The browser dashboard includes live game controls, separate platform leaderboards, an OBS overlay, editable settings, and a question manager.

## Run the application

### Windows (one-click desktop launcher)

1. Extract the ZIP to a permanent folder (for example `Documents\Multiplatform Live Trivia`).
2. Double-click `Create Desktop Shortcut.ps1` and approve the script if Windows asks. This creates **Multiplatform Live Trivia** on your Desktop.
3. Double-click the Desktop shortcut whenever you want to start the app. The launcher creates a local virtual environment, installs dependencies, starts the server, and opens the dashboard in your browser. Keep the terminal window open while streaming.

You can also double-click `Start Trivia.bat` directly from the project folder. The scripts are Windows launchers; they are included in the ZIP even if you build/develop on Arch Linux.

### Arch Linux / other Python environments

```bash
python -m pip install -r requirements.txt
python main.py
```

Open the host dashboard at <http://127.0.0.1:8000>.

The OBS browser source remains <http://127.0.0.1:8000/overlay>.

## Dashboard settings

Open **Settings** in the dashboard.

- **General:** configure the question timer, transition delay, Facebook extra answer time, Facebook timestamp tolerance, text-answer attempts, and fuzzy matching threshold. Changes apply to upcoming rounds.
- **Platforms:** enable/disable TikTok and Facebook, edit the TikTok username, and view/edit/clear the Facebook session ID. Platform connections automatically stop and reconnect when these settings are saved; you do not need to restart the app. The session ID is sensitive, so keep the dashboard local and never expose port 8000 to the public internet.
- **Questions:** add, edit, delete, and reorder welcome screens, MCQ questions, and text/number/numbered-text/Roman numeral questions. MCQs support multiple correct options. Save the question list to write changes to `data/questions.json`.

Question lists can only be saved while the game is in `WAITING_FOR_START`, so question order is not changed during an active round or while results are being displayed. Question entries are validated using the same loader as startup.

## Configuration and data

- `config.json`: settings saved from the dashboard, including platform connection settings.
- `data/questions.json`: ordered question list.
- `data/leaderboard.json`: saved leaderboard data.
- `data/leaderboard.txt`: readable top-five leaderboard.

Keep the project folder private because platform credentials are stored in local configuration files. Do not upload `config.json` or share an archive containing real session credentials publicly. Keep `web.host` set to `127.0.0.1` unless you intentionally add network-level protection; the dashboard settings are designed for local use, not public internet exposure.

## Tests

```bash
python -m pytest -q
```

The test suite covers the engine, platform adapters, question validation, dashboard routes, settings persistence, and question management.

## Build and test notes

The Windows launcher scripts can be packaged while developing on Arch Linux; run the `.ps1`/`.bat` files on the target Windows computer, not on Arch. Python 3.12 is recommended on Windows. The automated suite was run in the Arch build environment with TikTokLive import stubs because that environment could not reach PyPI; install the listed requirements on Windows before the first run.


For the Windows-first setup walkthrough, see `multiplatform_live_trivia_guide.txt`.
