"""FastAPI application entry point."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import asdict
import json
import logging
import os
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


async def broadcast_snapshot(snapshot: GameSnapshot) -> None:
    if not active_websockets:
        return

    payload = json.dumps(asdict(snapshot))
    dead_websockets: set[WebSocket] = set()

    for websocket in active_websockets:
        try:
            await websocket.send_text(payload)
        except Exception:
            dead_websockets.add(websocket)

    active_websockets.difference_update(dead_websockets)


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

    facebook_token = os.getenv(
        "FACEBOOK_ACCESS_TOKEN",
        "",
    ).strip()

    if (
        fb_cfg.get("enabled", True)
        and facebook_token
        and fb_cfg.get("live_video_id", "").strip()
    ):
        facebook_adapter = FacebookAdapter(
            access_token=facebook_token,
            live_video_id=fb_cfg["live_video_id"],
            chat_queue=chat_queue,
            poll_interval_ms=int(
                fb_cfg.get(
                    "poll_interval_ms",
                    500,
                )
            ),
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

    engine.start()

    # --------------------------------------------------------------
    # Broadcast / telemetry loop
    # --------------------------------------------------------------

    async def broadcast_loop() -> None:
        last_persisted_state = None

        while True:
            if engine.state == GameState.STOPPED:
                break

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
                persist_leaderboard(snapshot)
                last_persisted_state = snapshot.state

            if snapshot.state not in (
                GameState.RESULT.value,
                GameState.FINISHED.value,
            ):
                last_persisted_state = None

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

    return JSONResponse(
        asdict(engine.get_snapshot())
    )


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

    if engine.state != GameState.WAITING_FOR_START:
        raise HTTPException(
            status_code=400,
            detail=(
                "Cannot start while game is "
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