# Multiplatform LIVE Trivia Bot

Local Python application for one shared trivia game receiving comments from TikTok LIVE and Facebook LIVE.

## Current implementation stage

Phase 1 is the project foundation:
- project structure
- configuration files
- shared data models
- initial model tests

The implementation follows the supplied specification as the source of truth.

## Run tests

```bash
python -m pytest -q
```

## Install dependencies

```bash
python -m pip install -r requirements.txt
```

## Config notes

- `game.resume_leaderboard` (default `true`): scores saved in `data/leaderboard.json` are restored on restart. Delete that file, or set this to `false`, to begin a fresh game.
- `game.max_text_attempts` (default `1`): attempts per viewer on non-multiple-choice questions. Multiple-choice questions only count messages that are a valid option (A/B/C/D or the option text), so chat never uses up an attempt.
- `timing.facebook_answer_grace_sec` only applies when Facebook is enabled **and** `facebook.session_id` is set; otherwise it is ignored.
