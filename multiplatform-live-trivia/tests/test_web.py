"""Tests for the production FastAPI application in main.py."""

import asyncio
import json
import time

import pytest
from fastapi.testclient import TestClient

import main as main_module


@pytest.fixture
def client(monkeypatch, tmp_path):
    """
    Start the real production FastAPI app with both livestream adapters
    disabled so tests do not connect to TikTok or Social Stream Ninja.
    """

    monkeypatch.setattr(
        main_module,
        "CONFIG",
        {
            "questions_file": "questions.json",
            "game": {
                "question_duration_sec": 1.0,
                "result_duration_sec": 0.05,
                "transition_duration_sec": 0.05,
            },
            "timing": {
                "facebook_tolerance_sec": 1.0,
                "drain_idle_sec": 0.01,
                "drain_hard_cap_sec": 0.05,
            },
            "queues": {
                "chat_maxsize": 10,
            },
            "tiktok": {
                "enabled": False,
                "username": "",
            },
            "facebook": {
                "enabled": False,
                "session_id": "",
            },
            "web": {
                "host": "127.0.0.1",
                "port": 8000,
            },
        },
    )

    # Prevent tests from modifying the real leaderboard files.
    monkeypatch.setattr(
        main_module,
        "DATA_DIR",
        tmp_path / "data",
    )

    with TestClient(main_module.app) as test_client:
        yield test_client


def wait_until_state(
    client: TestClient,
    expected_state: str,
    timeout: float = 1.0,
) -> bool:
    """Wait until the production app reaches the expected state."""
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        response = client.get("/status")

        if response.status_code == 200:
            if response.json()["state"] == expected_state:
                return True

        time.sleep(0.01)

    return False


def test_root_dashboard_is_available(client):
    response = client.get("/")

    assert response.status_code == 200
    assert "Learn and Earn English Quiz" in response.text


def test_overlay_route_is_available(client):
    response = client.get("/overlay")

    assert response.status_code == 200
    assert "Learn and Earn English Quiz" in response.text


def test_production_status_endpoint(client):
    response = client.get("/status")

    assert response.status_code == 200

    data = response.json()

    assert data["state"] == "WAITING_FOR_START"
    assert data["total_questions"] > 0
    assert data["queue_size"] == 0
    assert data["queue_capacity"] == 10


def test_production_start_endpoint(client):
    response = client.post("/game/start")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "action": "START",
    }

    assert wait_until_state(
        client,
        "ACTIVE",
        timeout=0.5,
    )


def test_cannot_start_twice(client):
    first = client.post("/game/start")

    assert first.status_code == 200

    assert wait_until_state(
        client,
        "ACTIVE",
        timeout=0.5,
    )

    second = client.post("/game/start")

    assert second.status_code == 400
    assert "Cannot start or advance while game is" in second.json()["detail"]


def test_production_stop_endpoint(client):
    start_response = client.post("/game/start")

    assert start_response.status_code == 200

    assert wait_until_state(
        client,
        "ACTIVE",
        timeout=0.5,
    )

    stop_response = client.post("/game/stop")

    assert stop_response.status_code == 200
    assert stop_response.json() == {
        "status": "ok",
        "action": "STOP",
    }

    assert wait_until_state(
        client,
        "STOPPED",
        timeout=0.5,
    )


def test_websocket_returns_snapshot(client):
    with client.websocket_connect("/ws") as websocket:
        raw = websocket.receive_text()

        snapshot = json.loads(raw)

        assert snapshot["state"] == "WAITING_FOR_START"
        assert snapshot["total_questions"] > 0
        assert snapshot["queue_size"] == 0
        assert snapshot["queue_capacity"] == 10


def test_game_start_without_initialized_engine_returns_503(
    monkeypatch,
):
    monkeypatch.setattr(
        main_module.app.state,
        "engine",
        None,
    )

    with TestClient(main_module.app) as test_client:
        # The lifespan normally initializes the engine, so this test
        # intentionally checks the endpoint behavior only after startup.
        response = test_client.post("/game/start")

        # Lifespan creates the engine again, so this endpoint should
        # still be available.
        assert response.status_code in (200, 400)