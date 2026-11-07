/** Dashboard settings and question manager. Kept separate from overlay/game rendering. */
(() => {
  const byId = (id) => document.getElementById(id);
  const settingsView = byId("settings-view");
  const dashboardView = byId("dashboard-view");
  if (!settingsView || !dashboardView) return;

  const mainTabs = [...document.querySelectorAll(".nav-tab")];
  const settingTabs = [...document.querySelectorAll(".settings-tab")];
  const settingPanels = [...document.querySelectorAll(".settings-panel")];
  const questionList = byId("question-list");
  const questionEditor = byId("question-editor");
  const questionForm = byId("question-form");

  let questionDraft = [];
  let editingQuestionIndex = null;
  let settingsLoaded = false;

  function showMessage(container, message, isError = false) {
    let node = container.querySelector(".inline-message");
    if (!node) {
      node = document.createElement("p");
      node.className = "inline-message";
      node.setAttribute("role", "status");
      container.appendChild(node);
    }
    node.textContent = message;
    node.classList.toggle("error-message", isError);
  }

  async function requestJson(url, options = {}) {
    const response = await fetch(url, {
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      ...options,
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(data.detail || `Request failed (${response.status}).`);
    }
    return data;
  }

  function switchView(viewId) {
    const showSettings = viewId === "settings-view";
    dashboardView.hidden = showSettings;
    settingsView.hidden = !showSettings;
    mainTabs.forEach((tab) => {
      tab.classList.toggle("active", tab.dataset.view === viewId);
    });
    if (showSettings && !settingsLoaded) {
      loadSettings();
      loadQuestions();
      settingsLoaded = true;
    }
  }

  mainTabs.forEach((tab) => {
    tab.addEventListener("click", () => switchView(tab.dataset.view));
  });

  function switchSettingsPanel(panelId) {
    settingPanels.forEach((panel) => {
      panel.hidden = panel.id !== panelId;
    });
    settingTabs.forEach((tab) => {
      tab.classList.toggle("active", tab.dataset.panel === panelId);
    });
    if (panelId === "questions-settings" && questionDraft.length === 0) {
      loadQuestions();
    }
  }

  settingTabs.forEach((tab) => {
    tab.addEventListener("click", () => switchSettingsPanel(tab.dataset.panel));
  });

  async function loadSettings() {
    try {
      const settings = await requestJson("/api/settings");
      byId("setting-question-duration").value = settings.game.question_duration_sec;
      byId("setting-transition-duration").value = settings.game.transition_duration_sec;
      byId("setting-facebook-grace").value = settings.timing.facebook_answer_grace_sec;
      byId("setting-facebook-tolerance").value = settings.timing.facebook_tolerance_sec;
      byId("setting-text-attempts").value = settings.game.max_text_attempts;
      byId("setting-fuzzy-threshold").value = settings.game.fuzzy_threshold;

      byId("setting-tiktok-enabled").checked = settings.tiktok.enabled;
      byId("setting-tiktok-username").value = settings.tiktok.username;
      byId("setting-facebook-enabled").checked = settings.facebook.enabled;
      byId("setting-facebook-session").value = settings.facebook.session_id || "";
      byId("facebook-session-status").textContent = settings.facebook.session_id_configured
        ? "Saved Facebook session ID is displayed in the field above."
        : "No Facebook session ID is saved yet.";
    } catch (error) {
      showMessage(byId("general-settings"), error.message, true);
    }
  }

  byId("general-settings-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    if (!form.reportValidity()) return;
    const payload = {
      game: {
        question_duration_sec: Number(byId("setting-question-duration").value),
        transition_duration_sec: Number(byId("setting-transition-duration").value),
        max_text_attempts: Number(byId("setting-text-attempts").value),
        fuzzy_threshold: Number(byId("setting-fuzzy-threshold").value),
      },
      timing: {
        facebook_answer_grace_sec: Number(byId("setting-facebook-grace").value),
        facebook_tolerance_sec: Number(byId("setting-facebook-tolerance").value),
      },
    };
    const button = form.querySelector('button[type="submit"]');
    button.disabled = true;
    try {
      const result = await requestJson("/api/settings", {
        method: "PUT", body: JSON.stringify(payload),
      });
      showMessage(byId("general-settings"), result.message || "Game settings saved.");
    } catch (error) {
      showMessage(byId("general-settings"), error.message, true);
    } finally {
      button.disabled = false;
    }
  });

  byId("platform-settings-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const sessionId = byId("setting-facebook-session").value;
    const clearSession = byId("setting-facebook-clear").checked;
    if (sessionId.trim() && clearSession) {
      showMessage(byId("platform-settings"), "Enter a new session ID or choose to remove the saved one, not both.", true);
      return;
    }
    const payload = {
      tiktok: {
        enabled: byId("setting-tiktok-enabled").checked,
        username: byId("setting-tiktok-username").value.trim(),
      },
      facebook: {
        enabled: byId("setting-facebook-enabled").checked,
        session_id: sessionId,
        clear_session_id: clearSession,
      },
    };
    const button = form.querySelector('button[type="submit"]');
    button.disabled = true;
    try {
      const result = await requestJson("/api/settings", {
        method: "PUT", body: JSON.stringify(payload),
      });
      byId("setting-facebook-session").value = result.settings.facebook.session_id || "";
      byId("setting-facebook-clear").checked = false;
      byId("facebook-session-status").textContent = result.settings.facebook.session_id_configured
        ? "Saved Facebook session ID is displayed in the field above."
        : "No Facebook session ID is saved yet.";
      showMessage(byId("platform-settings"), result.message || "Platform settings saved.");
    } catch (error) {
      showMessage(byId("platform-settings"), error.message, true);
    } finally {
      button.disabled = false;
    }
  });

  // ------------------------------------------------------------------------
  // Question list and editor
  // ------------------------------------------------------------------------

  async function loadQuestions() {
    try {
      const result = await requestJson("/api/questions");
      questionDraft = Array.isArray(result.questions) ? result.questions.map((item) => ({ ...item })) : [];
      renderQuestionList();
      byId("question-save-status").textContent = `${questionDraft.length} question entries loaded from data/questions.json.`;
    } catch (error) {
      showMessage(byId("questions-settings"), error.message, true);
    }
  }

  function questionSummary(question) {
    if (question.question_type === "welcome") return "Welcome screen · no answer or timer";
    if (question.answer_type === "mcq" || question.options) return "Multiple choice";
    return `${question.answer_type || "text"} answer`;
  }

  function renderQuestionList() {
    questionList.replaceChildren();
    if (questionDraft.length === 0) {
      const empty = document.createElement("p");
      empty.className = "field-help";
      empty.textContent = "No questions yet. Add a question to begin.";
      questionList.appendChild(empty);
      return;
    }

    questionDraft.forEach((question, index) => {
      const item = document.createElement("article");
      item.className = "question-item";

      const number = document.createElement("span");
      number.className = "question-item-number";
      number.textContent = String(index + 1).padStart(2, "0");

      const content = document.createElement("div");
      content.className = "question-item-content";
      const title = document.createElement("div");
      title.className = "question-item-title";
      title.textContent = question.question || "Untitled question";
      const meta = document.createElement("div");
      meta.className = "question-item-meta";
      meta.textContent = questionSummary(question);
      content.append(title, meta);

      const actions = document.createElement("div");
      actions.className = "question-item-actions";
      const up = makeActionButton("↑", "Move up", () => moveQuestion(index, -1));
      const down = makeActionButton("↓", "Move down", () => moveQuestion(index, 1));
      up.disabled = index === 0;
      down.disabled = index === questionDraft.length - 1;
      const edit = makeActionButton("Edit", "Edit question", () => openQuestionEditor(index));
      const remove = makeActionButton("Delete", "Delete question", () => deleteQuestion(index));
      remove.classList.add("delete-question");
      actions.append(up, down, edit, remove);
      item.append(number, content, actions);
      questionList.appendChild(item);
    });
  }

  function makeActionButton(label, title, callback) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = label;
    button.title = title;
    button.addEventListener("click", callback);
    return button;
  }

  function moveQuestion(index, direction) {
    const target = index + direction;
    if (target < 0 || target >= questionDraft.length) return;
    [questionDraft[index], questionDraft[target]] = [questionDraft[target], questionDraft[index]];
    renderQuestionList();
    byId("question-save-status").textContent = "Order changed locally. Save the question list to apply it.";
  }

  function deleteQuestion(index) {
    const removed = questionDraft.splice(index, 1)[0];
    renderQuestionList();
    byId("question-save-status").textContent = `Removed “${removed.question}” locally. Save the question list to apply it.`;
    if (editingQuestionIndex === index) closeQuestionEditor();
  }

  function normalizeMcqCorrectAnswers(question) {
    const options = question.options || {};
    return (question.answers || []).map((answer) => {
      const found = Object.entries(options).find(([label, text]) =>
        String(answer).trim().toLowerCase() === label.toLowerCase() ||
        String(answer).trim().toLowerCase() === String(text).trim().toLowerCase()
      );
      return found ? found[0] : String(answer).trim().toUpperCase();
    });
  }

  function openQuestionEditor(index = null) {
    editingQuestionIndex = index;
    const question = index === null ? null : questionDraft[index];
    byId("question-editor-title").textContent = question ? "Edit question" : "Add a question";
    byId("question-type").value = question?.question_type || "question";
    byId("question-text").value = question?.question || "";
    const type = question?.answer_type || (question?.options ? "mcq" : "text");
    byId("question-answer-type").value = type;
    byId("question-answers").value = (question?.answers || []).join("\n");
    ["a", "b", "c", "d"].forEach((letter) => {
      byId(`option-${letter}`).value = question?.options?.[letter.toUpperCase()] || "";
    });
    const correct = new Set(question?.options ? normalizeMcqCorrectAnswers(question) : []);
    document.querySelectorAll('input[name="correct-option"]').forEach((input) => {
      input.checked = correct.has(input.value);
    });
    updateQuestionEditorFields();
    questionEditor.hidden = false;
    questionEditor.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function closeQuestionEditor() {
    questionEditor.hidden = true;
    editingQuestionIndex = null;
    questionForm.reset();
    updateQuestionEditorFields();
  }

  function updateQuestionEditorFields() {
    const isWelcome = byId("question-type").value === "welcome";
    const isMcq = byId("question-answer-type").value === "mcq";
    byId("answer-type-group").hidden = isWelcome;
    byId("mcq-editor-fields").hidden = isWelcome || !isMcq;
    byId("text-answer-fields").hidden = isWelcome || isMcq;
    byId("question-answers").required = !isWelcome && !isMcq;
  }

  byId("add-question-button").addEventListener("click", () => openQuestionEditor());
  byId("cancel-question-edit").addEventListener("click", closeQuestionEditor);
  byId("question-type").addEventListener("change", updateQuestionEditorFields);
  byId("question-answer-type").addEventListener("change", updateQuestionEditorFields);

  questionForm.addEventListener("submit", (event) => {
    event.preventDefault();
    if (!questionForm.reportValidity()) return;
    const questionText = byId("question-text").value.trim();
    if (!questionText) return;

    let item;
    if (byId("question-type").value === "welcome") {
      item = { question_type: "welcome", question: questionText };
    } else if (byId("question-answer-type").value === "mcq") {
      const options = {};
      ["A", "B", "C", "D"].forEach((label) => {
        const value = byId(`option-${label.toLowerCase()}`).value.trim();
        if (value) options[label] = value;
      });
      const answers = [...document.querySelectorAll('input[name="correct-option"]:checked')].map((input) => input.value);
      if (Object.keys(options).length < 2) {
        showMessage(questionEditor, "Enter at least two answer options for an MCQ.", true);
        return;
      }
      if (answers.length === 0) {
        showMessage(questionEditor, "Select at least one correct answer.", true);
        return;
      }
      if (answers.some((answer) => !options[answer])) {
        showMessage(questionEditor, "A selected correct answer must have an option value.", true);
        return;
      }
      item = { question: questionText, answer_type: "mcq", options, answers };
    } else {
      const answers = byId("question-answers").value.split(/\r?\n/).map((value) => value.trim()).filter(Boolean);
      if (answers.length === 0) {
        showMessage(questionEditor, "Enter at least one accepted answer.", true);
        return;
      }
      item = {
        question: questionText,
        answer_type: byId("question-answer-type").value,
        answers,
      };
    }

    if (editingQuestionIndex === null) questionDraft.push(item);
    else questionDraft[editingQuestionIndex] = item;
    closeQuestionEditor();
    renderQuestionList();
    byId("question-save-status").textContent = "Unsaved changes. Click Save question list to write them to data/questions.json.";
  });

  byId("save-questions-button").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    try {
      const result = await requestJson("/api/questions", {
        method: "PUT", body: JSON.stringify({ questions: questionDraft }),
      });
      questionDraft = result.questions;
      renderQuestionList();
      byId("question-save-status").textContent = `${result.count} question entries saved to data/questions.json.`;
      showMessage(byId("questions-settings"), "Question list saved successfully.");
    } catch (error) {
      showMessage(byId("questions-settings"), error.message, true);
    } finally {
      button.disabled = false;
    }
  });

  updateQuestionEditorFields();
})();
