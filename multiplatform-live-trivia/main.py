"""FastAPI application entry point."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import asdict
import json
import logging
import math
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.core.engine import TriviaEngine
from app.integrations.facebook import FacebookAdapter
from app.integrations.tiktok import TikTokAdapter
from app.models import (
    GameCommand,
    GameCommandType,
    GameSnapshot,
    GameState,
)
from app.services.persistence import atomic_write_file, save_json_atomic
from app.services.question_loader import (
    load_questions,
    validate_and_parse_questions,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

logger = logging.getLogger("multiplatform-live-trivia")

BASE_DIR = Path(__file__).resolve().parent
WEB_DIR = BASE_DIR / "web"
DATA_DIR = BASE_DIR / "data"
CONFIG_PATH = BASE_DIR / "config.json"
ENV_PATH = BASE_DIR / ".env"

load_dotenv(ENV_PATH)

with CONFIG_PATH.open("r", encoding="utf-8") as fh:
    CONFIG = json.load(fh)


def build_platform_snapshot(
    tiktok_adapter: TikTokAdapter | None,
    facebook_adapter: FacebookAdapter | None,
) -> dict:
    platforms = {}

    if tiktok_adapter is not None:
        platforms["tiktok"] = tiktok_adapter.status.state.value

    if facebook_adapter is not None:
        platforms["facebook"] = (
            facebook_adapter.status.state.value
        )

    return platforms


def total_dropped_messages(
    tiktok_adapter: TikTokAdapter | None,
    facebook_adapter: FacebookAdapter | None,
) -> int:
    total = 0

    if tiktok_adapter is not None:
        total += tiktok_adapter.dropped_messages

    if facebook_adapter is not None:
        total += facebook_adapter.dropped_messages

    return total


active_websockets: set[WebSocket] = set()


async def _send_one(websocket: WebSocket, payload: str) -> bool:
    try:
        await asyncio.wait_for(websocket.send_text(payload), timeout=2.0)
        return True
    except Exception:
        return False


async def broadcast_snapshot(snapshot: GameSnapshot) -> None:
    if not active_websockets:
        return

    payload = json.dumps(asdict(snapshot))

    # Iterate over a copy: clients connect/disconnect while we await, and
    # mutating the live set mid-iteration raises RuntimeError. Send to all
    # clients concurrently so one slow client cannot stall the others.
    clients = list(active_websockets)
    results = await asyncio.gather(
        *(_send_one(ws, payload) for ws in clients)
    )

    for websocket, ok in zip(clients, results):
        if not ok:
            active_websockets.discard(websocket)


def load_saved_leaderboard() -> list[dict]:
    """Read the leaderboard saved by a previous run (empty list if none)."""
    path = DATA_DIR / "leaderboard.json"

    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return []
    except (OSError, ValueError):
        logger.warning(
            "Could not read %s; starting with an empty leaderboard.",
            path,
            exc_info=True,
        )
        return []

    return data if isinstance(data, list) else []


def persist_leaderboard(snapshot: GameSnapshot) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    save_json_atomic(
        DATA_DIR / "leaderboard.json",
        snapshot.leaderboard,
    )

    lines = [
        f"#{index + 1} "
        f"{player['display_name']}: "
        f"{player['score']} pts"
        for index, player
        in enumerate(snapshot.leaderboard[:5])
    ]

    atomic_write_file(
        DATA_DIR / "leaderboard.txt",
        "\n".join(lines),
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    queue_cfg = CONFIG.get("queues", {})

    chat_queue = asyncio.Queue(
        maxsize=int(
            queue_cfg.get("chat_maxsize", 10000)
        )
    )

    timing_cfg = CONFIG.get("timing", {})
    game_cfg = CONFIG.get("game", {})
    fb_cfg = CONFIG.get("facebook", {})
    tiktok_cfg = CONFIG.get("tiktok", {})

    # --------------------------------------------------------------
    # Questions
    # --------------------------------------------------------------

    questions_file = CONFIG.get(
        "questions_file",
        "questions.json",
    )

    questions_path = BASE_DIR / "data" / questions_file

    if not questions_path.exists():
        questions_path = BASE_DIR / questions_file

    if not questions_path.exists():
        raise RuntimeError(
            f"Questions file not found: {questions_path}"
        )

    questions = load_questions(questions_path)

    # --------------------------------------------------------------
    # Facebook is only active when enabled AND a session id is set.
    # Without it there is nothing to wait for, so no grace period.
    # --------------------------------------------------------------

    facebook_session_id = (
        fb_cfg.get("session_id", "")
        or ""
    ).strip()

    facebook_active = bool(
        fb_cfg.get("enabled", True)
        and facebook_session_id
    )

    if fb_cfg.get("enabled", True) and not facebook_session_id:
        logger.warning(
            "Facebook is enabled in config.json but session_id is "
            "empty; Facebook chat is OFF."
        )

    # --------------------------------------------------------------
    # Engine
    # --------------------------------------------------------------

    engine = TriviaEngine(
        questions=questions,
        chat_queue=chat_queue,
        question_duration_sec=float(
            game_cfg.get(
                "question_duration_sec",
                10.0,
            )
        ),
        facebook_answer_grace_sec=(
            float(
                timing_cfg.get(
                    "facebook_answer_grace_sec",
                    3.0,
                )
            )
            if facebook_active
            else 0.0
        ),
        max_text_attempts=int(
            game_cfg.get("max_text_attempts", 1)
        ),
        fuzzy_threshold=float(
            game_cfg.get("fuzzy_threshold", 90.0)
        ),
        result_duration_sec=float(
            game_cfg.get(
                "result_duration_sec",
                3.0,
            )
        ),
        transition_duration_sec=float(
            game_cfg.get(
                "transition_duration_sec",
                3.0,
            )
        ),
        fb_tolerance_sec=float(
            timing_cfg.get(
                "facebook_tolerance_sec",
                1.0,
            )
        ),
        drain_idle_sec=float(
            timing_cfg.get(
                "drain_idle_sec",
                0.05,
            )
        ),
        drain_hard_cap_sec=float(
            timing_cfg.get(
                "drain_hard_cap_sec",
                1.5,
            )
        ),
    )

    # Resume scores from the previous run instead of wiping them.
    # Set game.resume_leaderboard to false (or delete
    # data/leaderboard.json) to start a fresh game.
    if game_cfg.get("resume_leaderboard", True):
        restored = engine.leaderboard_mgr.restore(
            load_saved_leaderboard()
        )

        if restored:
            logger.info(
                "Restored %d player(s) from the saved leaderboard.",
                restored,
            )

    persist_leaderboard(engine.get_snapshot())

    app.state.engine = engine
    app.state.chat_queue = chat_queue

    # --------------------------------------------------------------
    # TikTok
    # --------------------------------------------------------------

    tiktok_adapter: TikTokAdapter | None = None

    if tiktok_cfg.get("enabled", True):
        tiktok_adapter = TikTokAdapter(
            username=tiktok_cfg.get("username", ""),
            chat_queue=chat_queue,
        )

    # --------------------------------------------------------------
    # Facebook
    # --------------------------------------------------------------

    facebook_adapter: FacebookAdapter | None = None

    if facebook_active:
        facebook_adapter = FacebookAdapter(
            session_id=facebook_session_id,
            chat_queue=chat_queue,
        )

    # --------------------------------------------------------------
    # Start components
    # --------------------------------------------------------------

    adapter_tasks: list[asyncio.Task] = []

    if tiktok_adapter is not None:
        adapter_tasks.append(
            asyncio.create_task(
                tiktok_adapter.start()
            )
        )

    if facebook_adapter is not None:
        adapter_tasks.append(
            asyncio.create_task(
                facebook_adapter.start()
            )
        )

    app.state.tiktok_adapter = tiktok_adapter
    app.state.facebook_adapter = facebook_adapter
    app.state.adapter_tasks = adapter_tasks
    app.state.platform_restart_lock = asyncio.Lock()

    engine.start()

    # --------------------------------------------------------------
    # Broadcast / telemetry loop
    # --------------------------------------------------------------

    async def broadcast_loop() -> None:
        last_persisted_state = None

        while True:
            # One bad tick (disk error, odd client) must never kill the
            # loop: it feeds every connected dashboard.
            try:
                snapshot = engine.get_snapshot()
                # Dashboard settings can replace adapters while the app is
                # running, so always read the current instances from app.state.
                current_tiktok = getattr(app.state, "tiktok_adapter", None)
                current_facebook = getattr(app.state, "facebook_adapter", None)

                snapshot.platforms = build_platform_snapshot(
                    current_tiktok,
                    current_facebook,
                )

                snapshot.dropped_messages = total_dropped_messages(
                    current_tiktok,
                    current_facebook,
                )

                await broadcast_snapshot(snapshot)

                if (
                    snapshot.state in (
                        GameState.RESULT.value,
                        GameState.FINISHED.value,
                    )
                    and snapshot.state != last_persisted_state
                ):
                    try:
                        persist_leaderboard(snapshot)
                        last_persisted_state = snapshot.state
                    except Exception:
                        logger.exception(
                            "Failed to save the leaderboard; "
                            "will retry."
                        )

                if snapshot.state not in (
                    GameState.RESULT.value,
                    GameState.FINISHED.value,
                ):
                    last_persisted_state = None

                if snapshot.state == GameState.STOPPED.value:
                    break

            except asyncio.CancelledError:
                raise

            except Exception:
                logger.exception("Broadcast loop tick failed.")

            await asyncio.sleep(0.1)

    broadcast_task = asyncio.create_task(
        broadcast_loop()
    )

    try:
        yield

    finally:
        # ----------------------------------------------------------
        # Shutdown
        # ----------------------------------------------------------

        broadcast_task.cancel()

        await asyncio.gather(
            broadcast_task,
            return_exceptions=True,
        )

        current_tasks = list(getattr(app.state, "adapter_tasks", adapter_tasks))
        current_tiktok = getattr(app.state, "tiktok_adapter", tiktok_adapter)
        current_facebook = getattr(app.state, "facebook_adapter", facebook_adapter)
        if current_tiktok is not None:
            await current_tiktok.stop()
        if current_facebook is not None:
            await current_facebook.stop()
        for task in current_tasks:
            if not task.done():
                task.cancel()
        if current_tasks:
            await asyncio.gather(*current_tasks, return_exceptions=True)

        await engine.stop()


def configured_questions_path() -> Path:
    """Resolve the configured question file without allowing path traversal."""
    filename = str(CONFIG.get("questions_file", "questions.json"))
    data_candidate = (BASE_DIR / "data" / filename).resolve()
    data_root = (BASE_DIR / "data").resolve()

    if data_candidate.is_relative_to(data_root) and data_candidate.exists():
        return data_candidate

    legacy_candidate = (BASE_DIR / filename).resolve()
    if legacy_candidate.parent == BASE_DIR and legacy_candidate.exists():
        return legacy_candidate

    if data_candidate.is_relative_to(data_root):
        return data_candidate

    raise HTTPException(
        status_code=400,
        detail="Invalid questions file path in config.json.",
    )


def public_settings() -> dict[str, Any]:
    """Return editable settings while never exposing platform secrets."""
    game = CONFIG.get("game", {})
    timing = CONFIG.get("timing", {})
    tiktok = CONFIG.get("tiktok", {})
    facebook = CONFIG.get("facebook", {})
    return {
        "game": {
            "question_duration_sec": float(game.get("question_duration_sec", 20.0)),
            "transition_duration_sec": float(game.get("transition_duration_sec", 3.0)),
            "max_text_attempts": int(game.get("max_text_attempts", 1)),
            "fuzzy_threshold": float(game.get("fuzzy_threshold", 90.0)),
        },
        "timing": {
            "facebook_answer_grace_sec": float(timing.get("facebook_answer_grace_sec", 0.0)),
            "facebook_tolerance_sec": float(timing.get("facebook_tolerance_sec", 1.0)),
        },
        "tiktok": {
            "enabled": bool(tiktok.get("enabled", True)),
            "username": str(tiktok.get("username", "")),
        },
        "facebook": {
            "enabled": bool(facebook.get("enabled", True)),
            # The user explicitly requested that this local dashboard display
            # the saved ID. Keep the dashboard bound to localhost.
            "session_id": str(facebook.get("session_id", "")),
            "session_id_configured": bool(str(facebook.get("session_id", "")).strip()),
        },
    }


def _number(value: Any, label: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool):
        raise HTTPException(status_code=422, detail=f"{label} must be a number.")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=422,
            detail=f"{label} must be a number.",
        ) from exc
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise HTTPException(
            status_code=422,
            detail=f"{label} must be between {minimum:g} and {maximum:g}.",
        )
    return result


def _apply_live_game_settings() -> None:
    """Apply timing/matching options to the engine for future rounds."""
    engine: TriviaEngine | None = getattr(app.state, "engine", None)
    if engine is None:
        return

    game = CONFIG.get("game", {})
    timing = CONFIG.get("timing", {})
    engine.question_duration_sec = float(game.get("question_duration_sec", 20.0))
    engine.transition_duration_sec = float(game.get("transition_duration_sec", 3.0))
    engine.max_text_attempts = max(1, int(game.get("max_text_attempts", 1)))
    engine.fuzzy_threshold = float(game.get("fuzzy_threshold", 90.0))
    facebook_active = getattr(app.state, "facebook_adapter", None) is not None
    engine.facebook_answer_grace_sec = (
        float(timing.get("facebook_answer_grace_sec", 0.0))
        if facebook_active
        else 0.0
    )
    engine.fb_tolerance_sec = float(timing.get("facebook_tolerance_sec", 1.0))


app = FastAPI(
    title="Multiplatform LIVE Trivia",
    lifespan=lifespan,
)

app.mount(
    "/static",
    StaticFiles(directory=str(WEB_DIR)),
    name="static",
)


@app.get("/")
async def get_index():
    return FileResponse(
        WEB_DIR / "index.html"
    )


@app.get("/overlay")
async def get_overlay():
    return FileResponse(
        WEB_DIR / "index.html"
    )


async def _restart_platform_adapters() -> None:
    """Reconnect enabled chat adapters using the latest in-memory config."""
    lock: asyncio.Lock = getattr(app.state, "platform_restart_lock", asyncio.Lock())
    async with lock:
        old_tiktok = getattr(app.state, "tiktok_adapter", None)
        old_facebook = getattr(app.state, "facebook_adapter", None)
        old_tasks = list(getattr(app.state, "adapter_tasks", []))

        # Stop adapters before cancelling their supervisor tasks.
        if old_tiktok is not None:
            await old_tiktok.stop()
        if old_facebook is not None:
            await old_facebook.stop()
        for task in old_tasks:
            if not task.done():
                task.cancel()
        if old_tasks:
            await asyncio.gather(*old_tasks, return_exceptions=True)

        queue = app.state.chat_queue
        tiktok_cfg = CONFIG.get("tiktok", {})
        facebook_cfg = CONFIG.get("facebook", {})
        tiktok_adapter = None
        facebook_adapter = None
        tasks: list[asyncio.Task] = []

        if tiktok_cfg.get("enabled", True) and str(tiktok_cfg.get("username", "")).strip():
            tiktok_adapter = TikTokAdapter(
                username=str(tiktok_cfg.get("username", "")),
                chat_queue=queue,
            )
            tasks.append(asyncio.create_task(tiktok_adapter.start()))

        session_id = str(facebook_cfg.get("session_id", "") or "").strip()
        if facebook_cfg.get("enabled", True) and session_id:
            facebook_adapter = FacebookAdapter(session_id=session_id, chat_queue=queue)
            tasks.append(asyncio.create_task(facebook_adapter.start()))

        app.state.tiktok_adapter = tiktok_adapter
        app.state.facebook_adapter = facebook_adapter
        app.state.adapter_tasks = tasks
        _apply_live_game_settings()
        logger.info("Livestream adapters reloaded from dashboard settings.")


@app.get("/api/settings")
async def get_settings():
    return public_settings()


@app.put("/api/settings")
async def update_settings(payload: dict[str, Any]):
    """Persist settings and hot-reload platform adapters when credentials change."""
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="Settings payload must be an object.")

    # Work on copies so an invalid request cannot partially mutate CONFIG.
    game = dict(CONFIG.get("game", {}))
    timing = dict(CONFIG.get("timing", {}))
    tiktok = dict(CONFIG.get("tiktok", {}))
    facebook = dict(CONFIG.get("facebook", {}))
    old_tiktok = dict(tiktok)
    old_facebook = dict(facebook)

    game_payload = payload.get("game", {})
    timing_payload = payload.get("timing", {})
    tiktok_payload = payload.get("tiktok", {})
    facebook_payload = payload.get("facebook", {})

    if not all(isinstance(section, dict) for section in (
        game_payload, timing_payload, tiktok_payload, facebook_payload
    )):
        raise HTTPException(status_code=422, detail="Each settings section must be an object.")

    text_attempts = _number(
        game_payload.get("max_text_attempts", game.get("max_text_attempts", 1)),
        "Text answer attempts", 1, 10,
    )
    if not text_attempts.is_integer():
        raise HTTPException(status_code=422, detail="Text answer attempts must be a whole number.")

    game.update({
        "question_duration_sec": _number(
            game_payload.get("question_duration_sec", game.get("question_duration_sec", 20)),
            "Question duration", 1, 600,
        ),
        "transition_duration_sec": _number(
            game_payload.get("transition_duration_sec", game.get("transition_duration_sec", 3)),
            "Transition delay", 0, 120,
        ),
        "max_text_attempts": int(text_attempts),
        "fuzzy_threshold": _number(
            game_payload.get("fuzzy_threshold", game.get("fuzzy_threshold", 90)),
            "Fuzzy matching threshold", 70, 100,
        ),
    })
    timing.update({
        "facebook_answer_grace_sec": _number(
            timing_payload.get("facebook_answer_grace_sec", timing.get("facebook_answer_grace_sec", 0)),
            "Facebook extra answer time", 0, 120,
        ),
        "facebook_tolerance_sec": _number(
            timing_payload.get("facebook_tolerance_sec", timing.get("facebook_tolerance_sec", 1)),
            "Facebook timestamp tolerance", 0, 30,
        ),
    })

    if "enabled" in tiktok_payload:
        if not isinstance(tiktok_payload["enabled"], bool):
            raise HTTPException(status_code=422, detail="TikTok enabled must be true or false.")
        tiktok["enabled"] = tiktok_payload["enabled"]
    if "username" in tiktok_payload:
        username = str(tiktok_payload["username"]).strip()
        if len(username) > 100:
            raise HTTPException(status_code=422, detail="TikTok username is too long.")
        tiktok["username"] = username

    if "enabled" in facebook_payload:
        if not isinstance(facebook_payload["enabled"], bool):
            raise HTTPException(status_code=422, detail="Facebook enabled must be true or false.")
        facebook["enabled"] = facebook_payload["enabled"]
    if "session_id" in facebook_payload:
        session_id = str(facebook_payload["session_id"] or "").strip()
        if len(session_id) > 10000:
            raise HTTPException(status_code=422, detail="Facebook session ID is too long.")
        if session_id:
            facebook["session_id"] = session_id
    if facebook_payload.get("clear_session_id") is True:
        facebook["session_id"] = ""

    previous = {key: CONFIG.get(key) for key in ("game", "timing", "tiktok", "facebook")}
    CONFIG["game"] = game
    CONFIG["timing"] = timing
    CONFIG["tiktok"] = tiktok
    CONFIG["facebook"] = facebook

    try:
        save_json_atomic(CONFIG_PATH, CONFIG)
    except OSError as exc:
        CONFIG.update(previous)
        logger.exception("Could not save dashboard settings.")
        raise HTTPException(status_code=500, detail="Could not save settings to config.json.") from exc

    _apply_live_game_settings()
    platforms_reloaded = tiktok != old_tiktok or facebook != old_facebook
    if platforms_reloaded:
        try:
            await _restart_platform_adapters()
        except Exception as exc:
            logger.exception("Could not reload livestream adapters after settings update.")
            raise HTTPException(
                status_code=500,
                detail="Settings were saved, but a platform could not be restarted. Check the application log.",
            ) from exc
    return {
        "status": "ok",
        "settings": public_settings(),
        "restart_required": False,
        "platforms_reloaded": platforms_reloaded,
        "message": (
            "Settings saved. Platform connections are restarting now."
            if platforms_reloaded
            else "Settings saved. Game settings apply to upcoming rounds."
        ),
    }


@app.get("/api/questions")
async def get_questions():
    path = configured_questions_path()
    try:
        with path.open("r", encoding="utf-8") as handle:
            questions = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        logger.exception("Could not read the configured questions file.")
        raise HTTPException(status_code=500, detail="Could not read questions.json.") from exc
    return {"questions": questions}


@app.put("/api/questions")
async def replace_questions(payload: dict[str, Any]):
    """Validate and replace the complete ordered question list."""
    raw_questions = payload.get("questions") if isinstance(payload, dict) else None
    if not isinstance(raw_questions, list) or any(
        not isinstance(item, dict) for item in raw_questions
    ):
        raise HTTPException(status_code=422, detail="Questions must be a list of question objects.")
    try:
        parsed_questions = validate_and_parse_questions(raw_questions)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    engine: TriviaEngine | None = getattr(app.state, "engine", None)
    if engine is not None and engine.state != GameState.WAITING_FOR_START:
        raise HTTPException(
            status_code=409,
            detail="Questions can only be changed while the game is waiting for the host to start the next question.",
        )
    if engine is not None and engine.current_question_index >= len(parsed_questions):
        raise HTTPException(
            status_code=422,
            detail="The new question list is shorter than the current game position. Stop and restart the app before replacing it.",
        )

    path = configured_questions_path()
    try:
        save_json_atomic(path, raw_questions)
    except OSError as exc:
        logger.exception("Could not save questions.")
        raise HTTPException(status_code=500, detail="Could not save questions.json.") from exc

    if engine is not None:
        engine.questions = parsed_questions

    return {"status": "ok", "count": len(parsed_questions), "questions": raw_questions}


@app.get("/status")
async def get_status():
    engine: TriviaEngine | None = getattr(
        app.state,
        "engine",
        None,
    )

    if engine is None:
        return JSONResponse(
            {"status": "starting"}
        )

    snapshot = engine.get_snapshot()
    snapshot.platforms = build_platform_snapshot(
        getattr(app.state, "tiktok_adapter", None),
        getattr(app.state, "facebook_adapter", None),
    )
    snapshot.dropped_messages = total_dropped_messages(
        getattr(app.state, "tiktok_adapter", None),
        getattr(app.state, "facebook_adapter", None),
    )

    return JSONResponse(asdict(snapshot))


@app.post("/game/start")
async def start_game():
    engine: TriviaEngine | None = getattr(
        app.state,
        "engine",
        None,
    )

    if engine is None:
        raise HTTPException(
            status_code=503,
            detail="Game engine is not initialized.",
        )

    if engine.state not in (
        GameState.WAITING_FOR_START,
        GameState.RESULT,
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                "Cannot start or advance while game is "
                f"in {engine.state.value} state."
            ),
        )

    engine.handle_command(
        GameCommand(
            command=GameCommandType.START
        )
    )

    return {
        "status": "ok",
        "action": "START",
    }


@app.post("/game/stop")
async def stop_game():
    engine: TriviaEngine | None = getattr(
        app.state,
        "engine",
        None,
    )

    if engine is None:
        raise HTTPException(
            status_code=503,
            detail="Game engine is not initialized.",
        )

    engine.handle_command(
        GameCommand(
            command=GameCommandType.STOP
        )
    )

    return {
        "status": "ok",
        "action": "STOP",
    }


@app.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
):
    await websocket.accept()
    active_websockets.add(websocket)

    engine: TriviaEngine | None = getattr(
        app.state,
        "engine",
        None,
    )

    if engine is not None:
        snapshot = engine.get_snapshot()

        await websocket.send_text(
            json.dumps(asdict(snapshot))
        )

    try:
        while True:
            await websocket.receive_text()

    except WebSocketDisconnect:
        active_websockets.discard(websocket)

    except Exception:
        active_websockets.discard(websocket)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host=CONFIG.get("web", {}).get(
            "host",
            "127.0.0.1",
        ),
        port=int(
            CONFIG.get("web", {}).get(
                "port",
                8000,
            )
        ),
        reload=False,
    )