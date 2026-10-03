"""FastAPI web application factory."""
from __future__ import annotations

import asyncio
from dataclasses import asdict
import json
from pathlib import Path
from typing import Set

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.core.engine import TriviaEngine
from app.models import (
    GameCommand,
    GameCommandType,
)


def create_app(
    engine: TriviaEngine,
    config: dict,
) -> FastAPI:
    app = FastAPI(
        title="Multiplatform LIVE Trivia"
    )

    static_dir = Path("web")

    if static_dir.is_dir():
        app.mount(
            "/static",
            StaticFiles(
                directory=str(static_dir)
            ),
            name="static",
        )

    sockets: Set[WebSocket] = set()

    async def broadcast_loop():
        while engine.state.value != "STOPPED":
            snapshot = engine.get_snapshot()

            payload = json.dumps(
                asdict(snapshot)
            )

            dead = set()

            for ws in sockets:
                try:
                    await ws.send_text(
                        payload
                    )
                except Exception:
                    dead.add(ws)

            sockets.difference_update(dead)

            await asyncio.sleep(0.1)

    @app.get("/api/status")
    async def get_status():
        return JSONResponse(
            asdict(engine.get_snapshot())
        )

    @app.post("/api/control")
    async def post_control(
        payload: dict[str, str]
    ):
        action = (
            payload.get("action", "")
            .upper()
        )

        if action == "START":
            engine.handle_command(
                GameCommand(
                    command=GameCommandType.START
                )
            )

            return JSONResponse(
                {
                    "status": "ok",
                    "action": "START",
                }
            )

        if action == "STOP":
            engine.handle_command(
                GameCommand(
                    command=GameCommandType.STOP
                )
            )

            return JSONResponse(
                {
                    "status": "ok",
                    "action": "STOP",
                }
            )

        return JSONResponse(
            {
                "status": "error",
                "message":
                    f"Unknown action '{action}'",
            },
            status_code=400,
        )

    @app.websocket("/ws")
    async def websocket_endpoint(
        websocket: WebSocket,
    ):
        await websocket.accept()

        sockets.add(websocket)

        await websocket.send_text(
            json.dumps(
                asdict(
                    engine.get_snapshot()
                )
            )
        )

        try:
            while True:
                await websocket.receive_text()

        except WebSocketDisconnect:
            sockets.discard(websocket)

        except Exception:
            sockets.discard(websocket)

    @app.on_event("startup")
    async def startup():
        engine.start()
        app.state.broadcast_task = (
            asyncio.create_task(
                broadcast_loop()
            )
        )

    @app.on_event("shutdown")
    async def shutdown():
        task = getattr(
            app.state,
            "broadcast_task",
            None,
        )

        if task is not None:
            task.cancel()
            await asyncio.gather(
                task,
                return_exceptions=True,
            )

        await engine.stop()

    return app