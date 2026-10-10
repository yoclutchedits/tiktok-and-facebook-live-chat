/**
 * Multiplatform LIVE Trivia - Host Dashboard & OBS Overlay
 */
(() => {
  const isOverlayMode =
    window.location.pathname.includes("/overlay") ||
    new URLSearchParams(window.location.search).get("overlay") === "true";

  if (isOverlayMode) {
    document.body.classList.add("overlay-mode");
  }

  // --------------------------------------------------------------------------
  // DOM elements
  // --------------------------------------------------------------------------

  const stateBadge =
    document.getElementById("state-badge");

  const questionCount =
    document.getElementById("question-count");

  const questionBox =
    document.getElementById("question-box");

  const optionsBox =
    document.getElementById("options-box");

  const answerReveal =
    document.getElementById("answer-reveal");

  const timerBar =
    document.getElementById("timer-bar");

  const timerDigits =
    document.getElementById("timer-digits");

  // Facebook extra timer
  const facebookGraceWrapper =
    document.getElementById("facebook-grace-wrapper");

  const facebookGraceBar =
    document.getElementById("facebook-grace-bar");

  const facebookGraceDigits =
    document.getElementById("facebook-grace-digits");

  // Separate platform leaderboards
  const tiktokLeaderboardList =
    document.getElementById("tiktok-leaderboard-list");

  const facebookLeaderboardList =
    document.getElementById("facebook-leaderboard-list");

  const btnStart =
    document.getElementById("btn-start");

  const btnStop =
    document.getElementById("btn-stop");

  const tiktokStatus =
    document.getElementById("tiktok-status");

  const fbStatus =
    document.getElementById("fb-status");

  const queueStats =
    document.getElementById("queue-stats");

  const timerWrapper = 
    document.querySelector(".timer-wrapper");

  // --------------------------------------------------------------------------
  // Timer
  // --------------------------------------------------------------------------

  const QUESTION_WINDOW_SEC = 15.0;

  let currentSnapshot = null;
  let lastTimerSecond = null;
  let timerFinishPlayed = false;
  let lastTimerState = null;

  // --------------------------------------------------------------------------
  // Sound effects
  //
  // Required files:
  //
  // web/sfx/tick.mp3
  // web/sfx/end.mp3
  // --------------------------------------------------------------------------

  const timerTickSound =
    new Audio("/static/sfx/tick.mp3");

  const timerEndSound =
    new Audio("/static/sfx/end.mp3");

  timerTickSound.volume = 0.6;
  timerEndSound.volume = 0.8;

  // --------------------------------------------------------------------------
  // Other state
  // --------------------------------------------------------------------------

  let socket = null;
  let reconnectInterval = 1000;

  // --------------------------------------------------------------------------
  // WebSocket
  // --------------------------------------------------------------------------

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

  // --------------------------------------------------------------------------
  // Snapshot rendering
  // --------------------------------------------------------------------------

  function renderSnapshot(snap) {
    currentSnapshot = snap;

    const isWelcome = snap.question_type === "welcome";

    if (questionCount) {
      questionCount.textContent =
        `Q ${snap.question_number}/${snap.total_questions}`;

      questionCount.style.visibility =
        isWelcome ? "hidden" : "visible";
    }

    updateStateBadge(snap.state);

    if (timerWrapper) {
      timerWrapper.style.display = isWelcome ? "none" : "";
    }

    if (facebookGraceWrapper && isWelcome) {
      facebookGraceWrapper.hidden = true;
    }

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

    renderOptions(snap);

    // ------------------------------------------------------------------------
    // Answer reveal
    // ------------------------------------------------------------------------

    if (
      snap.state === "RESULT" &&
      snap.correct_answer
    ) {
      answerReveal.style.display =
        "block";

      let answerText =
        `Correct Answer: ${snap.correct_answer}`;

      if (
        snap.options &&
        snap.options[snap.correct_answer]
      ) {
        answerText +=
          `. ${snap.options[snap.correct_answer]}`;
      }

      answerText += " — Press SPACE to continue";

      answerReveal.textContent =
        answerText;

    } else {
      answerReveal.style.display =
        "none";
    }

    // ------------------------------------------------------------------------
    // Buttons
    // ------------------------------------------------------------------------

    if (btnStart) {
      btnStart.disabled =
        snap.state !== "WAITING_FOR_START";
    }

    if (btnStop) {
      btnStop.disabled =
        snap.state === "STOPPED" ||
        snap.state === "FINISHED";
    }

    // ------------------------------------------------------------------------
    // Countdown timers
    // ------------------------------------------------------------------------

    syncCountdown(snap);
    syncFacebookGraceCountdown(snap);

    // ------------------------------------------------------------------------
    // Leaderboards
    // ------------------------------------------------------------------------

    renderLeaderboard(snap);

    // ------------------------------------------------------------------------
    // Platform telemetry
    // ------------------------------------------------------------------------

    if (
      !isOverlayMode &&
      snap.platforms
    ) {
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

  // --------------------------------------------------------------------------
  // MCQ options
  // --------------------------------------------------------------------------

  function renderOptions(snap) {
    if (!optionsBox) {
      return;
    }

    optionsBox.replaceChildren();

    if (
      !snap.options ||
      typeof snap.options !== "object" ||
      Object.keys(snap.options).length === 0
    ) {
      optionsBox.style.display =
        "none";

      return;
    }

    optionsBox.style.display =
      "grid";

    for (
      const [label, text]
      of Object.entries(snap.options)
    ) {
      const option =
        document.createElement("div");

      option.className =
        "mcq-option";

      const optionLabel =
        document.createElement("span");

      optionLabel.className =
        "mcq-option-label";

      optionLabel.textContent =
        label;

      const optionText =
        document.createElement("span");

      optionText.className =
        "mcq-option-text";

      optionText.textContent =
        text;

      option.append(
        optionLabel,
        optionText
      );

      optionsBox.appendChild(
        option
      );
    }
  }

  // --------------------------------------------------------------------------
  // Original question countdown
  //
  // The backend remains the source of truth.
  // --------------------------------------------------------------------------

  function syncCountdown(snap) {
    const state =
      snap.state;

    // ------------------------------------------------------------------------
    // Question is no longer active
    // ------------------------------------------------------------------------

    if (
      state !== "ACTIVE" ||
      snap.time_remaining == null
    ) {
      timerTickSound.pause();
      timerTickSound.currentTime = 0;

      timerBar.style.width =
        "0%";

      timerDigits.textContent =
        "0";

      // Play the finish sound when the active round finishes.
      if (
        lastTimerState === "ACTIVE" &&
        (
          state === "DRAINING" ||
          state === "RESULT"
        ) &&
        !timerFinishPlayed
      ) {
        timerFinishPlayed =
          true;

        timerEndSound.currentTime =
          0;

        timerEndSound
          .play()
          .catch(() => {});
      }

      lastTimerState =
        state;

      lastTimerSecond =
        null;

      return;
    }

    // ------------------------------------------------------------------------
    // Active question
    // ------------------------------------------------------------------------

    lastTimerState =
      "ACTIVE";

    const remaining =
      Math.max(
        0,
        Number(snap.time_remaining)
      );

    const displayedSecond =
      Math.ceil(remaining);

    timerDigits.textContent =
      String(displayedSecond);

    const percent =
      Math.min(
        100,
        Math.max(
          0,
          (remaining / QUESTION_WINDOW_SEC) * 100
        )
      );

    timerBar.style.width =
      `${percent}%`;

    // ------------------------------------------------------------------------
    // Timer colours
    // ------------------------------------------------------------------------

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

    // ------------------------------------------------------------------------
    // Tick sound
    // ------------------------------------------------------------------------

    if (
      displayedSecond !== lastTimerSecond &&
      displayedSecond > 0
    ) {
      lastTimerSecond =
        displayedSecond;

      timerTickSound.currentTime =
        0;

      timerTickSound
        .play()
        .catch(() => {});
    }

    // Allow the finish sound on the next round.
    if (displayedSecond > 0) {
      timerFinishPlayed =
        false;
    }
  }

  // --------------------------------------------------------------------------
  // Facebook extra countdown
  //
  // Appears after the original timer expires.
  // Its duration comes from config.json via the backend.
  // --------------------------------------------------------------------------

  function syncFacebookGraceCountdown(snap) {
    if (
      !facebookGraceWrapper ||
      !facebookGraceBar ||
      !facebookGraceDigits
    ) {
      return;
    }

    const duration = Math.max(
      0,
      Number(snap.facebook_grace_duration_sec || 0)
    );

    const remaining = Math.max(
      0,
      Number(snap.facebook_grace_time_remaining ?? 0)
    );

    const showTimer =
      snap.state === "DRAINING" &&
      duration > 0 &&
      snap.facebook_grace_time_remaining != null;

    facebookGraceWrapper.hidden =
      !showTimer;

    if (!showTimer) {
      facebookGraceBar.style.width =
        "0%";

      facebookGraceDigits.textContent =
        "0";

      facebookGraceBar.classList.remove(
        "warning",
        "critical"
      );

      return;
    }

    facebookGraceDigits.textContent =
      String(Math.ceil(remaining));

    const percent =
      Math.min(
        100,
        Math.max(
          0,
          (remaining / duration) * 100
        )
      );

    facebookGraceBar.style.width =
      `${percent}%`;

    facebookGraceBar.classList.remove(
      "warning",
      "critical"
    );

    if (remaining <= 1) {
      facebookGraceBar.classList.add(
        "critical"
      );
    } else if (remaining <= 2) {
      facebookGraceBar.classList.add(
        "warning"
      );
    }
  }

  // --------------------------------------------------------------------------
  // Leaderboards
  // --------------------------------------------------------------------------

  function renderLeaderboard(snap) {
    const maxEntries = 5;

    const players =
      snap.leaderboard || [];

    const tiktokPlayers =
      players
        .filter(
          (player) =>
            String(player.platform).toLowerCase() === "tiktok"
        )
        .slice(0, maxEntries);

    const facebookPlayers =
      players
        .filter(
          (player) =>
            String(player.platform).toLowerCase() === "facebook"
        )
        .slice(0, maxEntries);

    renderPlatformLeaderboard(
      tiktokLeaderboardList,
      tiktokPlayers
    );

    renderPlatformLeaderboard(
      facebookLeaderboardList,
      facebookPlayers
    );
  }

  function renderPlatformLeaderboard(
    container,
    players
  ) {
    if (!container) {
      return;
    }

    const leaderboardKey =
      JSON.stringify(
        players.map((player) => ({
          name:
            player.display_name ||
            player.username ||
            "Anonymous",
          score: player.score
        }))
      );

    const previousKey =
      container.dataset.leaderboardKey || "";

    if (
      previousKey === leaderboardKey
    ) {
      return;
    }

    container.dataset.leaderboardKey =
      leaderboardKey;

    const fragment =
      document.createDocumentFragment();

    if (players.length === 0) {
      const empty =
        document.createElement("div");

      empty.style.color =
        "var(--text-muted)";

      empty.style.fontSize =
        "0.85rem";

      empty.style.padding =
        "6px 0";

      empty.textContent =
        "No scores yet.";

      fragment.appendChild(
        empty
      );

    } else {
      players.forEach(
        (player, index) => {
          const row =
            document.createElement("div");

          row.className =
            "leaderboard-row";

          const playerInfo =
            document.createElement("div");

          playerInfo.className =
            "player-info";

          const rank =
            document.createElement("span");

          rank.className =
            "player-rank";

          rank.textContent =
            `#${index + 1}`;

          const name =
            document.createElement("span");

          name.className =
            "player-name";

          name.textContent =
            player.display_name ||
            player.username ||
            "Anonymous";

          const score =
            document.createElement("span");

          score.className =
            "player-score";

          score.textContent =
            `${player.score} pts`;

          playerInfo.append(
            rank,
            name
          );

          row.append(
            playerInfo,
            score
          );

          fragment.appendChild(
            row
          );
        }
      );
    }

    container.replaceChildren(
      fragment
    );
  }

  // --------------------------------------------------------------------------
  // Platform telemetry
  // --------------------------------------------------------------------------

  function updatePlatformTelemetry(
    platforms
  ) {
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

    if (!dot || !label) {
      return;
    }

    dot.className =
      "status-dot";

    const state =
      (stateStr || "")
        .toUpperCase();

    if (state === "CONNECTED") {
      dot.classList.add(
        "connected"
      );

    } else if (state === "CONNECTING") {
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

  // --------------------------------------------------------------------------
  // State badge
  // --------------------------------------------------------------------------

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

    } else if (normalized.includes("waiting")) {
      stateBadge.classList.add(
        "waiting"
      );

    } else if (normalized.includes("draining")) {
      stateBadge.classList.add(
        "draining"
      );

    } else if (normalized.includes("result")) {
      stateBadge.classList.add(
        "result"
      );

    } else if (normalized.includes("stop")) {
      stateBadge.classList.add(
        "stopped"
      );
    }
  }

  // --------------------------------------------------------------------------
  // START / ADVANCE
  // --------------------------------------------------------------------------

  async function triggerStart() {
    if (
      !currentSnapshot ||
      ![
        "WAITING_FOR_START",
        "RESULT"
      ].includes(currentSnapshot.state)
    ) {
      return;
    }

    // ------------------------------------------------------------------------
    // Unlock browser audio using the user interaction.
    // ------------------------------------------------------------------------

    timerTickSound
      .play()
      .then(() => {
        timerTickSound.pause();
        timerTickSound.currentTime =
          0;
      })
      .catch(() => {});

    timerEndSound
      .play()
      .then(() => {
        timerEndSound.pause();
        timerEndSound.currentTime =
          0;
      })
      .catch(() => {});

    try {
      const response =
        await fetch(
          "/game/start",
          {
            method: "POST"
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
        "Failed to start or advance game:",
        error
      );
    }
  }

  // --------------------------------------------------------------------------
  // STOP
  // --------------------------------------------------------------------------

  async function triggerStop() {
    try {
      const response =
        await fetch(
          "/game/stop",
          {
            method: "POST"
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

  // --------------------------------------------------------------------------
  // Button listeners
  // --------------------------------------------------------------------------

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

  // --------------------------------------------------------------------------
  // Spacebar: START a round or advance from RESULT
  // --------------------------------------------------------------------------

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
          tag === "TEXTAREA" ||
          tag === "BUTTON" ||
          event.repeat
        ) {
          return;
        }

        if (
          !currentSnapshot ||
          ![
            "WAITING_FOR_START",
            "RESULT"
          ].includes(currentSnapshot.state)
        ) {
          return;
        }

        event.preventDefault();

        triggerStart();
      }
    }
  );

  // --------------------------------------------------------------------------
  // HTML escaping helper
  // --------------------------------------------------------------------------

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

  // --------------------------------------------------------------------------
  // Start WebSocket connection
  // --------------------------------------------------------------------------

  connectWebSocket();

})();