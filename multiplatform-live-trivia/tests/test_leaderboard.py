"""Tests for app.core.leaderboard."""
from app.core.leaderboard import LeaderboardManager


def test_leaderboard_scoring_and_tiebreaking():
    lb = LeaderboardManager()

    # Alice scores at t=10.0
    lb.record_score("tiktok", "101", "Alice", timestamp=10.0)
    # Bob scores at t=12.0
    lb.record_score("facebook", "202", "Bob", timestamp=12.0)
    # Alice scores again at t=15.0 (total = 2)
    lb.record_score("tiktok", "101", "Alice", timestamp=15.0)

    board = lb.get_leaderboard()
    assert len(board) == 2
    assert board[0]["display_name"] == "Alice"
    assert board[0]["score"] == 2
    assert board[1]["display_name"] == "Bob"
    assert board[1]["score"] == 1


def test_leaderboard_tiebreak_earlier_timestamp():
    lb = LeaderboardManager()

    # Both Alice and Bob have 1 point, but Bob reached it earlier
    lb.record_score("facebook", "202", "Bob", timestamp=5.0)
    lb.record_score("tiktok", "101", "Alice", timestamp=10.0)

    board = lb.get_leaderboard()
    assert board[0]["display_name"] == "Bob"
    assert board[1]["display_name"] == "Alice"

def test_restore_keeps_scores_and_saved_ranking():
    saved = [
        {"platform": "tiktok", "platform_user_id": "1", "display_name": "Ann", "score": 3},
        {"platform": "facebook", "platform_user_id": "2", "display_name": "Bo", "score": 3},
        {"platform": "tiktok", "platform_user_id": "3", "display_name": "Cy", "score": 1},
        {"bad": "entry"},
    ]
    lb = LeaderboardManager()
    assert lb.restore(saved) == 3

    board = lb.get_leaderboard()
    assert [p["display_name"] for p in board] == ["Ann", "Bo", "Cy"]

    # A new point after the restart sorts after restored players on a tie
    lb.record_score("tiktok", "3", "Cy", timestamp=500.0)
    lb.record_score("tiktok", "3", "Cy", timestamp=501.0)
    lb.record_score("tiktok", "3", "Cy", timestamp=502.0)
    board = lb.get_leaderboard()
    assert board[0]["display_name"] == "Cy" and board[0]["score"] == 4
