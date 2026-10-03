/**
 * Multiplatform LIVE Trivia - Host Dashboard & Overlay
 */
(() => {
  const isOverlayMode =
    window.location.pathname.includes("/overlay") ||
    new URLSearchParams(window.location.search).get("overlay") === "true";

  if (isOverlayMode) {
    document.body.classList.add("overlay-mode");
  }

  const stateBadge = document.getElementById("state-badge");
  const questionCount = document.getElementById("question-count");
  const questionBox = document.getElementById("question-box");
  const answerReveal = document.getElementById("answer-reveal");
  const timerBar = document.getElementById("timer-bar");
  const timerDigits = document.getElementById("timer-digits");
  const leaderboardList = document.getElementById("leaderboard-list");
  const leaderboardTitle = document.getElementById("leaderboard-title");
  const btnStart = document.getElementById("btn-start");
  const btnStop = document.getElementById("btn-stop");
  const tiktokStatus = document.getElementById("tiktok-status");
  const fbStatus = document.getElementById("fb-status");
  const queueStats = document.getElementById("queue-stats");

  const QUESTION_WINDOW_SEC = 10.0;

  let currentSnapshot = null;
  let countdownTimerId = null;
  let countdownStateKey = null;
  let lastLeaderboardKey = null;
  let socket = null;
  let reconnectInterval = 1000;

  function connectWebSocket() {
    const protocol =
      window.location.protocol === "https:"
        ? "wss:"
        : "ws:";

    const wsUrl =
      `${protocol}//${window.location.host}/ws`;

    socket = new WebSocket(wsUrl);

    socket.onopen = () => {
      reconnectInterval = 1000;
    };

    socket.onmessage = (event) => {
      try {
        const snapshot = JSON.parse(event.data);
        renderSnapshot(snapshot);
      } catch (err) {
        console.error(
          "Error parsing WebSocket snapshot:",
          err
        );
      }
    };

    socket.onclose = () => {
      updateStateBadge(
        "DISCONNECTED",
        "stopped"
      );

      setTimeout(
        connectWebSocket,
        Math.min(
          reconnectInterval *= 1.5,
          10000
        )
      );
    };

    socket.onerror = () => {
      socket.close();
    };
  }

  function renderSnapshot(snap) {
    currentSnapshot = snap;

    questionCount.textContent =
      `Q ${snap.question_number}/${snap.total_questions}`;

    updateStateBadge(snap.state);

    if (snap.question) {
      questionBox.textContent =
        snap.question;
    } else if (
      snap.state === "WAITING_FOR_START"
    ) {
      questionBox.textContent =
        "Waiting for host to trigger START...";
    } else if (
      snap.state === "FINISHED"
    ) {
      questionBox.textContent =
        "Game Complete!";
    }

    if (
      snap.state === "RESULT" &&
      snap.correct_answer
    ) {
      answerReveal.style.display = "block";
      answerReveal.textContent =
        `Correct Answer: ${snap.correct_answer}`;
    } else {
      answerReveal.style.display = "none";
    }

    if (btnStart) {
      btnStart.disabled =
        snap.state !== "WAITING_FOR_START";
    }

    if (btnStop) {
      btnStop.disabled =
        snap.state === "STOPPED" ||
        snap.state === "FINISHED";
    }

    syncCountdown(snap);
    renderLeaderboard(snap);

    if (!isOverlayMode && snap.platforms) {
      updatePlatformTelemetry(
        snap.platforms
      );

      if (queueStats) {
        queueStats.textContent =
          `Queue: ${snap.queue_size}/${snap.queue_capacity} ` +
          `(Dropped: ${snap.dropped_messages})`;
      }
    }
  }

  function syncCountdown(snap) {
    clearInterval(countdownTimerId);

    if (
      snap.state !== "ACTIVE" ||
      snap.time_remaining == null
    ) {
      timerBar.style.width =
        snap.state === "ACTIVE"
          ? "100%"
          : "0%";

      timerDigits.textContent =
        snap.state === "ACTIVE"
          ? "10"
          : "0";

      return;
    }

    const startTime = Date.now();

    const initialRemaining =
      Math.max(
        0,
        Number(snap.time_remaining)
      );

    const updateClock = () => {
      const elapsed =
        (Date.now() - startTime) / 1000;

      const remaining =
        Math.max(
          0,
          initialRemaining - elapsed
        );

      timerDigits.textContent =
        Math.ceil(remaining);

      const percent =
        Math.min(
          100,
          Math.max(
            0,
            (remaining / QUESTION_WINDOW_SEC) *
              100
          )
        );

      timerBar.style.width =
        `${percent}%`;

      timerBar.classList.remove(
        "warning",
        "critical"
      );

      if (remaining <= 3) {
        timerBar.classList.add(
          "critical"
        );
      } else if (remaining <= 5) {
        timerBar.classList.add(
          "warning"
        );
      }

      if (remaining <= 0) {
        clearInterval(
          countdownTimerId
        );
      }
    };

    updateClock();

    countdownTimerId =
      setInterval(updateClock, 100);
  }


  function renderLeaderboard(snap) {
    if (!leaderboardList) {
      return;
    }

    const isFinal = snap.state === "FINISHED";
    const maxEntries = isFinal ? 3 : 5;

    const title = isFinal
      ? "🏆 Final Winners (Top 3)"
      : "Top 5 Players";

    if (
      leaderboardTitle &&
      leaderboardTitle.textContent !== title
    ) {
      leaderboardTitle.textContent = title;
    }

    const players = (snap.leaderboard || [])
      .slice(0, maxEntries);

    // Build a stable key so unchanged data doesn't
    // cause the leaderboard DOM to be rebuilt.
    const leaderboardKey = JSON.stringify({
      isFinal,
      players: players.map((player, index) => ({
        rank: index + 1,
        platform: player.platform || "LIVE",
        name:
          player.display_name ||
          player.username ||
          "Anonymous",
        score: player.score
      }))
    });

    if (leaderboardKey === lastLeaderboardKey) {
      return;
    }

    lastLeaderboardKey = leaderboardKey;

    const fragment = document.createDocumentFragment();

    if (players.length === 0) {
      const empty = document.createElement("div");
      empty.style.color = "var(--text-muted)";
      empty.style.fontSize = "0.85rem";
      empty.style.padding = "6px 0";
      empty.textContent = "No scores recorded yet.";
      fragment.appendChild(empty);
    } else {
      players.forEach((player, index) => {
        const row = document.createElement("div");
        row.className = "leaderboard-row";

        const platformClass =
          (player.platform || "")
            .toLowerCase()
            .includes("tiktok")
            ? "tiktok"
            : "facebook";

        const playerInfo = document.createElement("div");
        playerInfo.className = "player-info";

        const rank = document.createElement("span");
        rank.className = "player-rank";
        rank.textContent = `#${index + 1}`;

        const platform = document.createElement("span");
        platform.className = `platform-pill ${platformClass}`;
        platform.textContent = player.platform || "LIVE";

        const name = document.createElement("span");
        name.className = "player-name";
        name.textContent =
          player.display_name ||
          player.username ||
          "Anonymous";

        const score = document.createElement("span");
        score.className = "player-score";
        score.textContent = `${player.score} pts`;

        playerInfo.append(rank, platform, name);
        row.append(playerInfo, score);
        fragment.appendChild(row);
      });
    }

    // Replace the list only when its displayed data changes.
    leaderboardList.replaceChildren(fragment);
  }



  function updatePlatformTelemetry(platforms) {
    if (
      tiktokStatus &&
      platforms.tiktok
    ) {
      setDotStatus(
        tiktokStatus,
        platforms.tiktok
      );
    }

    if (
      fbStatus &&
      platforms.facebook
    ) {
      setDotStatus(
        fbStatus,
        platforms.facebook
      );
    }
  }

  function setDotStatus(
    container,
    stateStr
  ) {
    const dot =
      container.querySelector(
        ".status-dot"
      );

    const label =
      container.querySelector(
        ".status-text"
      );

    dot.className =
      "status-dot";

    const state =
      (stateStr || "")
        .toUpperCase();

    if (state === "CONNECTED") {
      dot.classList.add(
        "connected"
      );
    } else if (
      state === "CONNECTING"
    ) {
      dot.classList.add(
        "connecting"
      );
    } else if (
      state === "DISCONNECTED" ||
      state === "ENDED"
    ) {
      dot.classList.add(
        "disconnected"
      );
    }

    label.textContent =
      state;
  }

  function updateStateBadge(
    state,
    forceClass
  ) {
    if (!stateBadge) {
      return;
    }

    stateBadge.textContent =
      state;

    stateBadge.className =
      "state-badge";

    if (forceClass) {
      stateBadge.classList.add(
        forceClass
      );

      return;
    }

    const normalized =
      (state || "")
        .toLowerCase();

    if (normalized === "active") {
      stateBadge.classList.add(
        "active"
      );
    } else if (
      normalized.includes("waiting")
    ) {
      stateBadge.classList.add(
        "waiting"
      );
    } else if (
      normalized.includes("draining")
    ) {
      stateBadge.classList.add(
        "draining"
      );
    } else if (
      normalized.includes("result")
    ) {
      stateBadge.classList.add(
        "result"
      );
    } else if (
      normalized.includes("stop")
    ) {
      stateBadge.classList.add(
        "stopped"
      );
    }
  }

  async function triggerStart() {
    if (
      !currentSnapshot ||
      currentSnapshot.state !==
        "WAITING_FOR_START"
    ) {
      return;
    }

    try {
      const response =
        await fetch(
          "/game/start",
          {
            method: "POST",
          }
        );

      if (!response.ok) {
        console.error(
          "START request failed:",
          response.status
        );
      }
    } catch (error) {
      console.error(
        "Failed to start game:",
        error
      );
    }
  }

  async function triggerStop() {
    try {
      const response =
        await fetch(
          "/game/stop",
          {
            method: "POST",
          }
        );

      if (!response.ok) {
        console.error(
          "STOP request failed:",
          response.status
        );
      }
    } catch (error) {
      console.error(
        "Failed to stop game:",
        error
      );
    }
  }

  if (btnStart) {
    btnStart.addEventListener(
      "click",
      triggerStart
    );
  }

  if (btnStop) {
    btnStop.addEventListener(
      "click",
      triggerStop
    );
  }

  window.addEventListener(
    "keydown",
    (event) => {
      if (isOverlayMode) {
        return;
      }

      if (
        event.code === "Space" ||
        event.key === " "
      ) {
        const tag =
          document.activeElement
            ? document.activeElement.tagName
            : "";

        if (
          tag === "INPUT" ||
          tag === "TEXTAREA"
        ) {
          return;
        }

        event.preventDefault();
        triggerStart();
      }
    }
  );

  function escapeHtml(value) {
    return String(value).replace(
      /[&<>'"]/g,
      (tag) =>
        ({
          "&": "&amp;",
          "<": "&lt;",
          ">": "&gt;",
          "'": "&#39;",
          '"': "&quot;"
        }[tag] || tag)
    );
  }

  connectWebSocket();
})();