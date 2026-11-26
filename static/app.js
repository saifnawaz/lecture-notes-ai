// Frontend: a small single-page app. The part of the URL after "#" decides the page:
//   #/                        home (subject cards)
//   #/s/3                     subject 3, Overview tab
//   #/s/3/quiz                subject 3, Quiz tab
//   #/s/3/pdf/12/flashcards   PDF 12 inside subject 3, Flashcards tab
// AI text is always inserted as text (never as HTML), so it can't inject anything.

const app = document.getElementById("app");
const LETTERS = "ABCD";

// ---------- Small helpers ----------

// el("div", { class: "x", onclick: fn }, "text", childNode, [more, children])
function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (key === "class") node.className = value;
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : String(child));
  }
  return node;
}

// Read a JSON response, or throw an Error with a readable message.
// Works even if the server sent plain text (e.g. "Internal Server Error").
async function readJson(res) {
  const text = await res.text();
  let data = null;
  try {
    data = JSON.parse(text);
  } catch {
    // not JSON
  }
  if (!res.ok) {
    const detail = data && typeof data.detail === "string" ? data.detail : "";
    throw new Error(detail || `Server error (${res.status}). Please try again.`);
  }
  return data;
}

async function api(path, options) {
  return readJson(await fetch(path, options));
}

function postJson(path, body) {
  return api(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

function plural(n, word) {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

// A big number with a small label under it.
function stat(value, label, cls = "") {
  return el("div", { class: `stat ${cls}` }, el("b", {}, value), el("small", {}, label));
}

function accuracyText(accuracy) {
  return accuracy === null || accuracy === undefined ? "–" : `${accuracy}%`;
}

// Tab bar: items are [key, label, href].
function tabs(items, active) {
  return el("nav", { class: "tabs" },
    items.map(([key, label, href]) => el("a", { href, class: key === active ? "active" : "" }, label)));
}

// Only one keyboard handler is active at a time (the current quiz or revision session).
let keyHandler = null;
document.addEventListener("keydown", (event) => {
  if (!keyHandler || ["INPUT", "SELECT", "TEXTAREA"].includes(event.target.tagName)) return;
  keyHandler(event);
});

// ---------- Breadcrumb: Home > Subject > PDF ----------

function setBreadcrumb(items) {
  const crumbs = [{ label: "Home", href: "#/" }, ...items];
  const nav = document.getElementById("breadcrumb");
  nav.replaceChildren(...crumbs.flatMap((c, i) => {
    const last = i === crumbs.length - 1;
    const node = last ? el("span", { class: "current" }, c.label) : el("a", { href: c.href }, c.label);
    return i === 0 ? [node] : [el("span", { class: "sep" }, "›"), node];
  }));
}

// ---------- Router ----------

async function route() {
  keyHandler = null;
  const parts = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean).map(decodeURIComponent);
  try {
    if (parts[0] === "s" && parts[2] === "pdf") {
      await showPdf(Number(parts[1]), Number(parts[3]), parts[4] || "notes");
    } else if (parts[0] === "s") {
      await showSubject(Number(parts[1]), parts[2] || "overview", parts[3]);
    } else {
      await showHome();
    }
  } catch (err) {
    setBreadcrumb([]);
    app.replaceChildren(el("div", { class: "card" },
      el("p", { class: "error" }, err.message),
      el("a", { href: "#/" }, "Back to home")));
  }
}

window.addEventListener("hashchange", route);

// ---------- Home: subject cards ----------

async function showHome() {
  setBreadcrumb([]);
  const subjects = await api("/subjects");
  app.replaceChildren(
    el("h2", { class: "page-title" }, "Subjects"),
    el("div", { class: "subject-grid" },
      subjects.map((s) => el("a", { class: "subject-card", href: `#/s/${s.id}` },
        el("h3", {}, s.name),
        el("div", { class: "stats" },
          stat(s.pdf_count, s.pdf_count === 1 ? "PDF" : "PDFs"),
          stat(s.due_today, "due today", s.due_today ? "highlight" : ""),
          stat(accuracyText(s.accuracy), "accuracy")))),
      newSubjectCard()),
  );
  if (!subjects.length) {
    app.append(el("p", { class: "muted" }, "Create your first subject, then upload PDFs into it."));
  }
}

// "+ New subject" card that turns into a small form.
function newSubjectCard() {
  const input = el("input", { type: "text", placeholder: "Subject name", maxlength: 100, required: true });
  const error = el("p", { class: "error" });
  const button = el("button", { class: "subject-card add" }, "+ New subject");
  const form = el("form", { class: "subject-card new-subject", hidden: true },
    input,
    el("div", { class: "row" },
      el("button", { type: "submit" }, "Create"),
      el("button", { type: "button", class: "secondary", onclick: () => toggle(false) }, "Cancel")),
    error);

  function toggle(open) {
    button.hidden = open;
    form.hidden = !open;
    if (open) input.focus();
  }
  button.addEventListener("click", () => toggle(true));
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const subject = await postJson("/subjects", { name: input.value.trim() });
      location.hash = `#/s/${subject.id}/pdfs`; // straight to uploading
    } catch (err) {
      error.textContent = err.message;
    }
  });
  return [button, form];
}

// ---------- Subject page ----------

const SUBJECT_TABS = [
  ["overview", "Overview"],
  ["pdfs", "PDFs"],
  ["quiz", "Quiz"],
  ["revision", "Daily Revision"],
  ["progress", "Progress"],
];

async function showSubject(id, tab, extra) {
  const subject = await api(`/subjects/${id}`);
  setBreadcrumb([{ label: subject.name, href: `#/s/${id}` }]);

  const items = SUBJECT_TABS.map(([key, label]) => [
    key,
    key === "revision" && subject.due_today ? `${label} (${subject.due_today})` : label,
    `#/s/${id}/${key}`,
  ]);
  const body = el("div", { class: "tab-body" });
  app.replaceChildren(el("h2", { class: "page-title" }, subject.name), tabs(items, tab), body);

  const renderers = {
    overview: renderOverview,
    pdfs: renderPdfs,
    quiz: renderSubjectQuiz,
    revision: renderRevisionTab,
    progress: renderProgress,
  };
  await (renderers[tab] || renderOverview)(body, subject, extra);
}

function renderOverview(body, s) {
  body.append(
    el("div", { class: "stat-row" },
      stat(accuracyText(s.accuracy), "quiz accuracy"),
      stat(s.due_today, "cards due today", s.due_today ? "highlight" : ""),
      stat(s.pdf_count, s.pdf_count === 1 ? "PDF" : "PDFs")),
    s.due_today
      ? el("a", { class: "button", href: `#/s/${s.id}/revision` }, `Start Daily Revision (${s.due_today})`)
      : null,
    el("h3", { class: "section-title" }, "Weakest topics"),
    s.weakest.length
      ? el("div", { class: "weak-list" }, s.weakest.map((t) => el("div", { class: "weak-item" },
          el("div", {},
            el("b", {}, t.subtopic),
            el("small", {}, `${t.accuracy}% accuracy · ${t.correct} of ${t.attempts} correct`)),
          el("a", { class: "button secondary", href: `#/s/${s.id}/quiz/${encodeURIComponent(t.subtopic)}` },
            "Practice"))))
      : el("p", { class: "muted" }, "Take a quiz to find your weak topics."),
  );
}

// ---------- Subject: PDFs tab ----------

async function renderPdfs(body, s) {
  const notes = await api(`/notes?subject=${encodeURIComponent(s.name)}`);
  body.append(
    uploadForm(s),
    notes.length
      ? el("div", { class: "pdf-list" }, notes.map((n) => el("a", { class: "pdf-row", href: `#/s/${s.id}/pdf/${n.id}` },
          el("div", {},
            el("b", {}, n.topic || "Untitled"),
            el("small", {}, [n.filename, new Date(n.created_at).toLocaleDateString()].filter(Boolean).join(" · "))),
          el("small", { class: "muted" }, `${plural(n.mcq_count, "MCQ")} · ${plural(n.card_count, "card")}`))))
      : el("p", { class: "muted" }, "No PDFs yet. Upload one to get notes, a quiz and flashcards."),
  );
}

// Upload a PDF into this subject. If the same file is already here, ask first.
function uploadForm(s) {
  const file = el("input", { type: "file", accept: ".pdf,application/pdf", required: true });
  const button = el("button", { type: "submit" }, "Upload PDF");
  const status = el("p", { class: "status" });

  async function send(force) {
    const form = new FormData();
    form.append("file", file.files[0]);
    form.append("subject", s.name); // subject is set automatically
    if (force) form.append("force", "true");
    const res = await fetch("/generate", { method: "POST", body: form });
    if (res.status === 409) {
      const data = await res.json();
      if (!confirm(`${data.detail}\n\nGenerate notes from it again anyway?`)) return null;
      return send(true);
    }
    return readJson(res);
  }

  const form = el("form", { class: "upload-row" }, file, button);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    button.disabled = true;
    status.className = "status";
    status.textContent = "Generating... this can take a minute.";
    try {
      const note = await send(false);
      if (note) location.hash = `#/s/${s.id}/pdf/${note.id}`;
      else status.textContent = "";
    } catch (err) {
      status.className = "status error";
      // A network failure (server down) gives a TypeError with a vague message.
      status.textContent = err instanceof TypeError ? "Could not reach the server." : err.message;
    } finally {
      button.disabled = false;
    }
  });
  return el("div", { class: "upload-box" }, form, status);
}

// ---------- Subject: Quiz tab ----------

const QUIZ_SIZE = 10;

// Mixed quiz from all PDFs. With a topic (from a "Practice" button) it starts right away.
async function renderSubjectQuiz(body, s, topic) {
  async function start() {
    let url = `/subjects/${s.id}/quiz?n=${QUIZ_SIZE}`;
    if (topic) url += `&subtopic=${encodeURIComponent(topic)}`;
    const questions = await api(url);
    runQuiz(body, questions, { title: topic ? `Practice: ${topic}` : "Mixed quiz", backHref: `#/s/${s.id}` });
  }
  if (topic) return start();
  body.append(el("div", { class: "start-box" },
    el("p", {}, `${QUIZ_SIZE} questions mixed from all PDFs in ${s.name}. Weak topics come up more often.`),
    el("button", { onclick: start }, "Start quiz")));
}

// ---------- Quiz session (used by the subject quiz and the PDF quiz) ----------

// questions: [{question, options, answer, explanation, subtopic, note_id, question_index}]
// opts: {title, backHref, practice}. Practice rounds don't save attempts.
function runQuiz(container, questions, opts) {
  if (!questions.length) {
    container.replaceChildren(el("p", { class: "muted" }, "No questions yet. Upload a PDF or generate more MCQs."));
    return;
  }
  const results = []; // {q, chosen, correct}
  let index = 0;

  function show() {
    if (index >= questions.length) return finishQuiz(container, results, opts);
    const q = questions[index];
    let answered = false;

    const explanation = el("p", { class: "explanation", hidden: true });
    const next = el("button", { class: "next", hidden: true, onclick: () => { index++; show(); } },
      index === questions.length - 1 ? "See results" : "Next");
    const options = q.options.map((text, j) =>
      el("button", { class: "option", onclick: () => choose(j) }, `${LETTERS[j]}) ${text}`));

    function choose(j) {
      if (answered) return;
      answered = true;
      const correct = j === q.answer;
      options.forEach((b) => (b.disabled = true));
      options[q.answer].classList.add("correct");
      if (!correct) options[j].classList.add("wrong");
      explanation.textContent = q.explanation || "";
      explanation.hidden = !q.explanation;
      next.hidden = false;
      results.push({ q, chosen: j, correct });
      if (!opts.practice) saveAttempt(q, j);
    }

    // Keys: 1-4 pick an option, Enter or Space goes to the next question.
    keyHandler = (event) => {
      const n = Number(event.key);
      if (!answered && n >= 1 && n <= q.options.length) choose(n - 1);
      else if (answered && (event.key === "Enter" || event.code === "Space")) {
        event.preventDefault();
        next.click();
      }
    };

    container.replaceChildren(el("div", { class: "session" },
      el("div", { class: "session-head" },
        el("span", {}, opts.title || "Quiz"),
        el("span", { class: "left-count" }, `${index + 1} / ${questions.length}`)),
      el("p", { class: "question-text" }, q.question),
      el("div", { class: "options" }, options),
      explanation,
      next));
  }
  show();
}

// The server decides if the answer was right, so we only send which option was clicked.
async function saveAttempt(q, chosen) {
  try {
    await postJson("/attempts", { note_id: q.note_id, question_index: q.question_index, chosen });
  } catch (err) {
    console.error("Could not save attempt:", err);
  }
}

function finishQuiz(container, results, opts) {
  const wrong = results.filter((r) => !r.correct);
  showReport(container, {
    good: results.length - wrong.length,
    goodLabel: "correct",
    bad: wrong.length,
    badLabel: "wrong",
    mistakesTitle: "Wrong answers",
    mistakes: wrong.map((r) => el("li", {},
      el("p", { class: "m-question" }, r.q.question),
      el("p", { class: "m-answer" }, "Correct: ", el("b", {}, r.q.options[r.q.answer])),
      el("p", { class: "m-yours" }, `You chose: ${r.q.options[r.chosen]}`),
      r.q.explanation ? el("p", { class: "m-note" }, r.q.explanation) : null)),
    topics: wrong.map((r) => r.q.subtopic),
    onPractice: () => runQuiz(container, wrong.map((r) => r.q), { ...opts, practice: true, title: "Practice mistakes" }),
    backHref: opts.backHref,
  });
}

// ---------- End-of-session report (quiz and Daily Revision) ----------

function unique(list) {
  const seen = new Map(); // lowercase -> first spelling
  for (const item of list) {
    if (item && !seen.has(item.toLowerCase())) seen.set(item.toLowerCase(), item);
  }
  return [...seen.values()];
}

// r: {good, goodLabel, bad, badLabel, mistakesTitle, mistakes: [li], topics, onPractice, backHref}
function showReport(container, r) {
  keyHandler = null;
  const total = r.good + r.bad;
  const pct = total ? Math.round((100 * r.good) / total) : 0;
  const topics = unique(r.topics);

  container.replaceChildren(el("div", { class: "session report" },
    el("h3", {}, "Session complete"),
    el("div", { class: "stat-row" },
      stat(r.good, r.goodLabel, "good"),
      stat(r.bad, r.badLabel, r.bad ? "bad" : ""),
      stat(`${pct}%`, "accuracy")),
    r.bad
      ? [el("h4", {}, r.mistakesTitle), el("ul", { class: "mistakes" }, r.mistakes)]
      : el("p", { class: "done" }, "No mistakes. Well done!"),
    topics.length
      ? [el("h4", {}, "Topics to revise"), el("div", { class: "chips" }, topics.map((t) => el("span", { class: "chip" }, t)))]
      : null,
    el("div", { class: "report-actions" },
      r.bad ? el("button", { onclick: r.onPractice }, "Practice mistakes again") : null,
      el("a", { class: "button secondary", href: r.backHref }, "Back to subject"))));
}

// ---------- Subject: Daily Revision tab ----------

const FORGOT = 1;      // "Bhool gaya" -> SM-2 quality 1
const REMEMBERED = 4;  // "Yaad tha"   -> SM-2 quality 4

async function renderRevisionTab(body, s) {
  const cards = await api(`/cards/due?subject=${encodeURIComponent(s.name)}`);
  runRevision(body, cards, { backHref: `#/s/${s.id}` });
}

function days(n) {
  return plural(n, "day");
}

// Flashcard session. "Bhool gaya" cards come back at the end of the same session.
// Those repeats (and practice rounds) are practice only: SM-2 already scheduled the
// card on its first answer, so they are not sent to the server again.
function runRevision(container, cards, opts) {
  if (!cards.length) {
    container.replaceChildren(el("p", { class: "done" }, "All done for today."),
      el("p", { class: "muted" }, "No cards are due in this subject."));
    return;
  }
  const queue = cards.map((card) => ({ card, repeat: Boolean(opts.practice), nextDays: null }));
  const firstAnswer = new Map(); // card id -> {card, remembered}; only the first showing counts
  let flipped = false;
  let busy = false;

  const front = el("p", { class: "fc-front" });
  const back = el("p", { class: "fc-back", hidden: true });
  const hint = el("p", { class: "fc-hint" }, "tap to flip · Space");
  const cardBox = el("div", { class: "flashcard", onclick: flip }, front, back, hint);
  const buttons = el("div", { class: "review-buttons", hidden: true },
    el("button", { class: "forgot", onclick: () => answer(FORGOT) }, "Bhool gaya ", el("small", {}, "1")),
    el("button", { class: "remembered", onclick: () => answer(REMEMBERED) }, "Yaad tha ", el("small", {}, "2")));
  const left = el("span", { class: "left-count" });
  const feedback = el("p", { class: "feedback", hidden: true });

  container.replaceChildren(el("div", { class: "session revision" },
    el("div", { class: "session-head" }, el("span", {}, opts.practice ? "Practice mistakes" : "Daily Revision"), left),
    cardBox, buttons, feedback));

  function render() {
    if (!queue.length) return finishRevision(container, [...firstAnswer.values()], opts);
    left.textContent = `${queue.length} left`;
    flipped = false;
    front.textContent = queue[0].card.front;
    back.textContent = queue[0].card.back;
    back.hidden = true;
    hint.hidden = false;
    cardBox.classList.remove("flipped");
    buttons.hidden = true;
  }

  function flip() {
    if (flipped || !queue.length) return;
    flipped = true;
    back.hidden = false;
    hint.hidden = true;
    cardBox.classList.add("flipped");
    buttons.hidden = false;
  }

  async function answer(quality) {
    if (!flipped || busy) return;
    busy = true;
    const item = queue.shift();
    try {
      if (!item.repeat) {
        const updated = await postJson(`/cards/${item.card.id}/review`, { quality });
        item.nextDays = updated.interval_days;
      }
      if (!firstAnswer.has(item.card.id)) {
        firstAnswer.set(item.card.id, { card: item.card, remembered: quality === REMEMBERED });
      }
      let text = item.nextDays === null ? "practice only" : `next time: in ${days(item.nextDays)}`;
      if (quality === FORGOT) {
        queue.push({ ...item, repeat: true }); // see it again at the end of this session
        text += " · coming back in this session";
      }
      feedback.className = "feedback";
      feedback.textContent = text;
    } catch (err) {
      queue.unshift(item); // keep the card so nothing is lost
      feedback.className = "feedback error";
      feedback.textContent = err.message;
    }
    feedback.hidden = false;
    busy = false;
    render();
  }

  // Keys: Space flips, 1 = Bhool gaya, 2 = Yaad tha.
  keyHandler = (event) => {
    if (event.code === "Space") {
      event.preventDefault();
      flip();
    } else if (event.key === "1") answer(FORGOT);
    else if (event.key === "2") answer(REMEMBERED);
  };
  render();
}

// results: one entry per card, from its first showing in this session.
function finishRevision(container, results, opts) {
  const forgotten = results.filter((r) => !r.remembered);
  showReport(container, {
    good: results.length - forgotten.length,
    goodLabel: "remembered",
    bad: forgotten.length,
    badLabel: "forgotten",
    mistakesTitle: "Forgotten cards",
    mistakes: forgotten.map((r) => el("li", {},
      el("p", { class: "m-question" }, r.card.front),
      el("p", { class: "m-answer" }, r.card.back))),
    topics: forgotten.map((r) => r.card.subtopic),
    onPractice: () => runRevision(container, forgotten.map((r) => r.card), { ...opts, practice: true }),
    backHref: opts.backHref,
  });
}

// ---------- Subject: Progress tab ----------

// Bar color depends only on how full it is.
function barColor(pct) {
  if (pct < 25) return "low";
  if (pct > 75) return "high";
  return "mid";
}

async function renderProgress(body, s) {
  const rows = await api(`/progress?subject=${encodeURIComponent(s.name)}`); // sorted weakest first by the server
  if (!rows.length) {
    body.append(el("p", { class: "muted" }, "No quiz answers yet. Take a quiz to see your progress."));
    return;
  }
  body.append(el("div", { class: "progress-list" }, rows.map((r) => {
    const pct = r.accuracy; // raw correct / attempts, already rounded
    return el("div", { class: "progress-row" },
      el("div", { class: "progress-name" }, r.subtopic || "General"),
      el("div", { class: "bar-track" }, el("div", { class: `bar ${barColor(pct)}`, style: `width: ${pct}%` })),
      el("div", { class: "progress-stats" },
        el("b", {}, `${pct}%`),
        el("small", {}, `${r.correct} of ${r.attempts} correct`)));
  })));
}

// ---------- PDF page: Notes | Quiz | Flashcards ----------

const PDF_TABS = [["notes", "Notes"], ["quiz", "Quiz"], ["flashcards", "Flashcards"]];
let flashMessage = ""; // shown once on the next PDF page render (after "Generate more")

async function showPdf(sid, nid, tab) {
  const [subject, note] = await Promise.all([api(`/subjects/${sid}`), api(`/notes/${nid}`)]);
  setBreadcrumb([{ label: subject.name, href: `#/s/${sid}` }, { label: note.topic || "Untitled" }]);

  const status = el("p", { class: "status" }, flashMessage);
  flashMessage = "";
  const buttons = [];

  // "Generate more" buttons: show progress, then redraw the page with the result.
  function generateButton(label, path, describe) {
    const button = el("button", { class: "secondary" }, label);
    button.addEventListener("click", async () => {
      buttons.forEach((b) => (b.disabled = true));
      status.className = "status";
      status.textContent = "Generating... this can take a minute.";
      try {
        flashMessage = describe(await api(path, { method: "POST" }));
        await route();
      } catch (err) {
        status.className = "status error";
        status.textContent = err.message;
        buttons.forEach((b) => (b.disabled = false));
      }
    });
    buttons.push(button);
    return button;
  }

  const deleteButton = el("button", { class: "danger" }, "Delete PDF");
  deleteButton.addEventListener("click", async () => {
    if (!confirm("Delete this PDF? Its notes, quiz history and flashcards will also be deleted.")) return;
    try {
      await api(`/notes/${nid}`, { method: "DELETE" });
      location.hash = `#/s/${sid}/pdfs`;
    } catch (err) {
      status.className = "status error";
      status.textContent = err.message;
    }
  });
  buttons.push(deleteButton);

  const body = el("div", { class: "tab-body" });
  app.replaceChildren(
    el("div", { class: "pdf-head" },
      el("div", {},
        el("h2", { class: "page-title" }, note.topic || "Untitled"),
        el("p", { class: "muted" },
          [note.filename, new Date(note.created_at).toLocaleDateString()].filter(Boolean).join(" · "))),
      el("div", { class: "actions" },
        generateButton("Generate more MCQs", `/notes/${nid}/mcqs`, (r) =>
          r.added ? `Added ${plural(r.added, "new MCQ")}.` : "No new MCQs this time (all were repeats). Try again."),
        generateButton("Generate more flashcards", `/notes/${nid}/flashcards`, (r) =>
          r.added ? `Added ${plural(r.added, "new flashcard")}.` : "No new flashcards this time (all were repeats). Try again."),
        deleteButton)),
    status,
    tabs(PDF_TABS.map(([key, label]) => {
      const count = key === "quiz" ? ` (${note.mcqs.length})` : key === "flashcards" ? ` (${note.card_count})` : "";
      return [key, label + count, `#/s/${sid}/pdf/${nid}/${key}`];
    }), tab),
    body,
  );

  if (tab === "quiz") {
    const questions = note.mcqs.map((q, i) => ({
      ...q, note_id: nid, question_index: i, subtopic: q.subtopic || note.topic,
    }));
    runQuiz(body, questions, { title: note.topic, backHref: `#/s/${sid}` });
  } else if (tab === "flashcards") {
    await renderNoteCards(body, nid);
  } else {
    renderNotes(body, note);
  }
}

function renderNotes(body, note) {
  const acc = note.accuracy;
  body.append(
    acc ? el("p", { class: "accuracy" }, `Your quiz accuracy: ${acc.accuracy}% (${acc.correct} of ${acc.attempts})`) : null,
    el("ul", { class: "notes" }, note.notes.map((text) => el("li", {}, text))),
  );
}

async function renderNoteCards(body, nid) {
  const cards = await api(`/notes/${nid}/cards`);
  if (!cards.length) {
    body.append(el("p", { class: "muted" }, "No flashcards yet. Click \"Generate more flashcards\"."));
    return;
  }
  // Local "YYYY-MM-DD" (toISOString would give the UTC date, which can be a day off).
  const now = new Date();
  const today = [now.getFullYear(), now.getMonth() + 1, now.getDate()]
    .map((n) => String(n).padStart(2, "0")).join("-");
  body.append(el("div", { class: "card-list" }, cards.map((c) => el("div", { class: "card-item" },
    el("b", {}, c.front),
    el("p", {}, c.back),
    el("small", {}, c.due_date <= today
      ? "due now"
      : `next review: ${new Date(`${c.due_date}T00:00`).toLocaleDateString()}`)))));
}

// ---------- Start ----------
route();
