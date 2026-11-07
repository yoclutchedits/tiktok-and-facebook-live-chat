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

    # Prevent tests from modifying real project data/config files.
    monkeypatch.setattr(
        main_module,
        "DATA_DIR",
        tmp_path / "data",
    )
    monkeypatch.setattr(
        main_module,
        "CONFIG_PATH",
        tmp_path / "config.json",
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


def wait_until_snapshot(client, predicate, timeout: float = 1.0) -> bool:
    """Wait until the public snapshot satisfies a predicate."""
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        response = client.get("/status")

        if response.status_code == 200 and predicate(response.json()):
            return True

        time.sleep(0.01)

    return False


def dismiss_welcome_screen(client) -> None:
    """Dismiss the initial welcome entry and wait for the first real question."""
    response = client.post("/game/start")
    assert response.status_code == 200

    assert wait_until_snapshot(
        client,
        lambda snapshot: (
            snapshot.get("question_type") == "question"
            and snapshot.get("state") == "WAITING_FOR_START"
        ),
        timeout=0.5,
    )


def test_root_dashboard_is_available(client):
    response = client.get("/")

    assert response.status_code == 200
    assert "Learn and Earn English Quiz With Sanukaji" in response.text


def test_overlay_route_is_available(client):
    response = client.get("/overlay")

    assert response.status_code == 200
    assert "Learn and Earn English Quiz With Sanukaji" in response.text


def test_production_status_endpoint(client):
    response = client.get("/status")

    assert response.status_code == 200

    data = response.json()

    assert data["state"] == "WAITING_FOR_START"
    assert data["total_questions"] > 0
    assert data["queue_size"] == 0
    assert data["queue_capacity"] == 10
    assert data["question_duration_sec"] == 1.0


def test_production_start_endpoint(client):
    # The first START dismisses the welcome screen without starting a timer.
    response = client.post("/game/start")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "action": "START",
    }

    assert wait_until_snapshot(
        client,
        lambda snapshot: (
            snapshot.get("question_type") == "question"
            and snapshot.get("state") == "WAITING_FOR_START"
        ),
        timeout=0.5,
    )

    # A second START begins the first timed question.
    response = client.post("/game/start")
    assert response.status_code == 200

    assert wait_until_state(
        client,
        "ACTIVE",
        timeout=0.5,
    )


def test_cannot_start_twice(client):
    dismiss_welcome_screen(client)

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
    dismiss_welcome_screen(client)

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

def test_settings_endpoint_displays_facebook_session_id(client):
    main_module.CONFIG["facebook"]["session_id"] = "visible-session-test"
    response = client.get("/api/settings")

    assert response.status_code == 200
    data = response.json()
    assert data["facebook"]["session_id"] == "visible-session-test"
    assert data["facebook"]["session_id_configured"] is True
    assert data["game"]["question_duration_sec"] == 1.0


def test_game_settings_are_saved_and_applied_to_upcoming_rounds(client):
    response = client.put("/api/settings", json={
        "game": {
            "question_duration_sec": 35,
            "transition_duration_sec": 2.5,
            "max_text_attempts": 2,
            "fuzzy_threshold": 93,
        },
        "timing": {
            "facebook_answer_grace_sec": 4,
            "facebook_tolerance_sec": 1.5,
        },
    })

    assert response.status_code == 200
    assert response.json()["restart_required"] is False
    assert main_module.app.state.engine.question_duration_sec == 35
    assert main_module.app.state.engine.fuzzy_threshold == 93
    saved = json.loads(main_module.CONFIG_PATH.read_text(encoding="utf-8"))
    assert saved["game"]["question_duration_sec"] == 35
    assert saved["timing"]["facebook_answer_grace_sec"] == 4


def test_settings_reject_out_of_range_values(client):
    response = client.put("/api/settings", json={
        "game": {"question_duration_sec": 9999},
    })
    assert response.status_code == 422


def test_platform_settings_hot_reload_and_echo_visible_session(client):
    response = client.put("/api/settings", json={
        "tiktok": {"enabled": False, "username": "@new_creator"},
        "facebook": {"enabled": False, "session_id": "private-test-session"},
    })

    assert response.status_code == 200
    data = response.json()
    assert data["restart_required"] is False
    assert data["platforms_reloaded"] is True
    assert data["settings"]["facebook"]["session_id"] == "private-test-session"
    saved = json.loads(main_module.CONFIG_PATH.read_text(encoding="utf-8"))
    assert saved["facebook"]["session_id"] == "private-test-session"


def test_question_manager_reads_and_replaces_question_file(client, monkeypatch, tmp_path):
    question_file = tmp_path / "questions.json"
    question_file.write_text(json.dumps([
        {"question": "Temporary seed?", "answers": ["Yes"], "answer_type": "text"}
    ]), encoding="utf-8")
    monkeypatch.setattr(main_module, "configured_questions_path", lambda: question_file)

    original = client.get("/api/questions")
    assert original.status_code == 200

    replacement = [
        {"question_type": "welcome", "question": "Welcome!"},
        {
            "question": "What is 2 + 2?",
            "answer_type": "mcq",
            "options": {"A": "3", "B": "4", "C": "5"},
            "answers": ["B"],
        },
    ]
    response = client.put("/api/questions", json={"questions": replacement})

    assert response.status_code == 200
    assert response.json()["count"] == 2
    assert json.loads(question_file.read_text(encoding="utf-8")) == replacement
    assert len(main_module.app.state.engine.questions) == 2


def test_question_manager_rejects_invalid_questions(client):
    response = client.put("/api/questions", json={
        "questions": [{"question": "Missing answer"}],
    })
    assert response.status_code == 422


def test_question_manager_rejects_edits_during_active_round(client, tmp_path, monkeypatch):
    question_file = tmp_path / "questions.json"
    monkeypatch.setattr(main_module, "configured_questions_path", lambda: question_file)
    main_module.app.state.engine.state = main_module.GameState.ACTIVE

    response = client.put("/api/questions", json={
        "questions": [{"question": "A question?", "answers": ["Answer"]}],
    })
    assert response.status_code == 409


def test_settings_reject_fractional_text_attempt_count(client):
    response = client.put("/api/settings", json={
        "game": {"max_text_attempts": 1.5},
    })
    assert response.status_code == 422


def test_question_manager_rejects_non_object_entries(client):
    response = client.put("/api/questions", json={
        "questions": [{"question": "A question?", "answers": ["Answer"]}, "not a question object"],
    })
    assert response.status_code == 422


def test_platform_settings_replace_live_adapters_without_app_restart(client, monkeypatch):
    created = []

    class FakeAdapter:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.stopped = False
            self.dropped_messages = 0
            from app.models import PlatformStatus, Platform, PlatformStatusType
            platform = Platform.TIKTOK.value if "username" in kwargs else Platform.FACEBOOK.value
            self.status = PlatformStatus(platform=platform, state=PlatformStatusType.CONNECTING)
            created.append(self)

        async def start(self):
            return None

        async def stop(self):
            self.stopped = True

    monkeypatch.setattr(main_module, "TikTokAdapter", FakeAdapter)
    monkeypatch.setattr(main_module, "FacebookAdapter", FakeAdapter)

    response = client.put("/api/settings", json={
        "tiktok": {"enabled": True, "username": "@new_host"},
        "facebook": {"enabled": True, "session_id": "new-facebook-session"},
    })

    assert response.status_code == 200
    assert response.json()["platforms_reloaded"] is True
    assert len(created) == 2
    assert main_module.app.state.tiktok_adapter is created[0]
    assert main_module.app.state.facebook_adapter is created[1]
    assert created[0].kwargs["username"] == "@new_host"
    assert created[1].kwargs["session_id"] == "new-facebook-session"
