"""Player score tracking and leaderboard manager."""
from __future__ import annotations

from typing import Dict, List, Optional

from app.models import Player


class LeaderboardManager:
    def __init__(self) -> None:
        self.players: Dict[str, Player] = {}

    def record_score(
        self,
        platform: str,
        user_id: str,
        display_name: str,
        timestamp: float,
        points: int = 1,
    ) -> Player:
        key = f"{platform}:{user_id}"
        
        if key not in self.players:
            self.players[key] = Player(
                platform=platform,
                platform_user_id=user_id,
                display_name=display_name,
                score=0,
                score_reached_at=timestamp,
            )

        player = self.players[key]
        player.score += points
        player.score_reached_at = timestamp
        player.display_name = display_name  # Update display name if changed
        player.score_history.append(timestamp)
        return player

    def get_leaderboard(self, limit: Optional[int] = None) -> List[Dict]:
        # Sort rule: highest score first; if tied, earlier score_reached_at timestamp wins
        sorted_players = sorted(
            self.players.values(),
            key=lambda p: (-p.score, p.score_reached_at),
        )

        if limit is not None:
            sorted_players = sorted_players[:limit]

        return [
            {
                "player_key": p.player_key,
                "platform": p.platform,
                "platform_user_id": p.platform_user_id,
                "display_name": p.display_name,
                "score": p.score,
                "score_reached_at": p.score_reached_at,
            }
            for p in sorted_players
        ]

    def restore(self, entries: List[Dict]) -> int:
        """Restore players from a previously saved leaderboard list.

        ``score_reached_at`` is a monotonic clock value that is meaningless
        after a restart, so restored players get negative values that keep
        their saved ranking and always sort before anyone who scores later.
        Returns the number of players restored.
        """
        restored = 0
        total = len(entries)

        for rank, entry in enumerate(entries):
            try:
                platform = str(entry["platform"])
                user_id = str(entry["platform_user_id"])
                score = int(entry["score"])
                display_name = str(
                    entry.get("display_name") or user_id
                )
            except (KeyError, TypeError, ValueError):
                continue

            if score <= 0:
                continue

            key = f"{platform}:{user_id}"
            self.players[key] = Player(
                platform=platform,
                platform_user_id=user_id,
                display_name=display_name,
                score=score,
                score_reached_at=float(rank - total - 1),
            )
            restored += 1

        return restored

    def reset(self) -> None:
        self.players.clear()