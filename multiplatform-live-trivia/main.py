"""FastAPI application entry point."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import asdict
import json
import logging
from pathlib import Path

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
from app.services.question_loader import load_questions

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

                snapshot.platforms = build_platform_snapshot(
                    tiktok_adapter,
                    facebook_adapter,
                )

                snapshot.dropped_messages = total_dropped_messages(
                    tiktok_adapter,
                    facebook_adapter,
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

        for task in adapter_tasks:
            if not task.done():
                task.cancel()

        if adapter_tasks:
            await asyncio.gather(
                *adapter_tasks,
                return_exceptions=True,
            )

        if tiktok_adapter is not None:
            await tiktok_adapter.stop()

        if facebook_adapter is not None:
            await facebook_adapter.stop()

        await engine.stop()


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