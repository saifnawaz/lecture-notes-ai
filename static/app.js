// Frontend: a small single-page app. The part of the URL after "#" decides the page:
//   #/                        home (subject cards)
//   #/s/3                     subject 3, Overview tab
//   #/s/3/quiz                subject 3, Quiz tab
//   #/s/3/pdf/12/flashcards   PDF 12 inside subject 3, Flashcards tab
//   #/s/3/explain/Sets        AI explanation of the topic "Sets" in subject 3
//   #/usage                   Gemini token usage per feature
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

// Line icons (paths from Lucide, ISC licence), drawn with stroke 1.5 by the CSS.
const ICONS = {
  chevron: '<path d="m9 18 6-6-6-6"/>',
  check: '<path d="M20 6 9 17l-5-5"/>',
  x: '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
  plus: '<path d="M5 12h14"/><path d="M12 5v14"/>',
  upload: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m17 8-5-5-5 5"/><path d="M12 3v12"/>',
  trash: '<path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/>',
  file: '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M16 13H8"/><path d="M16 17H8"/>',
  refresh: '<path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/>',
  back: '<path d="m15 18-6-6 6-6"/>',
};

function icon(name) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("class", "icon");
  svg.setAttribute("aria-hidden", "true");
  svg.innerHTML = ICONS[name]; // fixed strings above, never user text
  return svg;
}

// Buttons: primary = solid black with ">" chevron, secondary = white outline.
// Pass href to get a link that looks like a button.
function btn(kind, label, attrs = {}, iconName = kind === "primary" ? "chevron" : null) {
  const cls = `btn btn-${kind}${attrs.small ? " btn-sm" : ""}`;
  const { small, href, ...rest } = attrs;
  const parts = iconName === "chevron" ? [label, icon("chevron")] : [iconName ? icon(iconName) : null, label];
  return href ? el("a", { ...rest, href, class: cls }, parts) : el("button", { type: "button", ...rest, class: cls }, parts);
}

// A horizontal band of the page, split from the next band by a full-width line.
function section(cls, ...children) {
  return el("section", { class: `section ${cls || ""}` }, children);
}

function label(text) {
  return el("span", { class: "label" }, text);
}

// Our pixel motif: a 4x3 block of squares (k = black, g = grey, l = light, . = empty).
function pixels(pattern = "k..g.kl.l.gk") {
  const map = { k: "k", g: "g", l: "l", ".": "" };
  return el("span", { class: "pixels", "aria-hidden": "true" }, [...pattern].map((c) => el("i", { class: map[c] })));
}

// A big mono number with a small label above it.
function stat(value, text, cls = "") {
  return el("div", { class: `stat ${cls}` }, label(text), el("span", { class: "num" }, value));
}

function accuracyText(accuracy) {
  return accuracy === null || accuracy === undefined ? "–" : `${accuracy}%`;
}

// Bar color depends only on how full it is.
function barColor(pct) {
  if (pct < 25) return "low";
  if (pct > 75) return "high";
  return "mid";
}

function bar(pct) {
  return el("div", { class: "bar-track" },
    pct === null || pct === undefined ? null : el("div", { class: `bar ${barColor(pct)}`, style: `width: ${pct}%` }));
}

// Tab bar of bordered boxes: items are [key, label, href, count?].
function tabs(items, active) {
  const nav = el("nav", { class: "tabs" },
    items.map(([key, text, href, count]) => el("a", { href, class: key === active ? "active" : "" },
      text, count === undefined ? null : el("span", { class: "num" }, count))));
  // On narrow screens the bar scrolls sideways: keep the active tab in view.
  requestAnimationFrame(() => {
    const a = nav.querySelector(".active");
    if (a && nav.scrollWidth > nav.clientWidth) nav.scrollLeft = a.offsetLeft - 24;
  });
  return nav;
}

// Only one keyboard handler is active at a time (the current quiz or revision session).
let keyHandler = null;
document.addEventListener("keydown", (event) => {
  if (!keyHandler || ["INPUT", "SELECT", "TEXTAREA"].includes(event.target.tagName)) return;
  keyHandler(event);
});

// ---------- Breadcrumb: Home > Subject > PDF ----------

function setBreadcrumb(items) {
  const crumbs = items.length ? [{ label: "Subjects", href: "#/" }, ...items] : [];
  const nav = document.getElementById("breadcrumb");
  nav.replaceChildren(...crumbs.flatMap((c, i) => {
    const last = i === crumbs.length - 1;
    const node = last ? el("span", { class: "current" }, c.label) : el("a", { href: c.href }, c.label);
    return i === 0 ? [node] : [el("span", { class: "sep" }, icon("chevron")), node];
  }));
}

// ---------- Router ----------

async function route() {
  keyHandler = null;
  const parts = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean).map(decodeURIComponent);
  try {
    if (parts[0] === "s" && parts[2] === "pdf") {
      await showPdf(Number(parts[1]), Number(parts[3]), parts[4] || "notes");
    } else if (parts[0] === "usage") {
      await showUsage();
    } else if (parts[0] === "s") {
      await showSubject(Number(parts[1]), parts[2] || "overview", parts[3]);
    } else {
      await showHome();
    }
  } catch (err) {
    setBreadcrumb([]);
    app.replaceChildren(section("",
      label("Error"),
      el("p", { class: "error lede" }, err.message),
      el("div", { class: "btn-row spaced" }, btn("secondary", "Back to subjects", { href: "#/" }, "back"))));
  }
}

window.addEventListener("hashchange", route);

// ---------- Home: subject cards ----------

async function showHome() {
  setBreadcrumb([]);
  const [subjects, today] = await Promise.all([api("/subjects"), api("/today")]);
  app.replaceChildren(
    section("hero",
      el("div", { class: "hero-text" },
        label("Lecture revision"),
        el("div", { class: "hero-title" },
          el("h1", { class: "display" },
            el("span", { class: "line" }, "Read it once."),
            el("span", { class: "line" }, "Fix what slips.")),
          pixels()),
        el("p", { class: "lede" },
          "Upload a lecture PDF. Get notes, MCQs and flashcards. See what you keep getting wrong.")),
      todayBox(subjects, today)),
    section("subjects",
      label("Subjects"),
      el("div", { class: "section-head" },
        el("h2", { class: "h2" }, "Your subjects"),
        el("span", { class: "muted num" }, plural(subjects.length, "subject"))),
      el("div", { class: "grid-lined subject-grid" },
        subjects.map(subjectCard),
        newSubjectCard()),
      subjects.length ? null : el("p", { class: "muted spaced" }, "Create your first subject, then upload PDFs into it.")),
  );
}

// "Today" box: cards due across all subjects, weakest topic, score on the last quiz day.
function todayBox(subjects, t) {
  const target = [...subjects].sort((a, b) => b.due_today - a.due_today)[0];
  const w = t.weakest;
  const last = t.last_score;
  return el("aside", { class: "today" },
    el("div", { class: "today-head" },
      el("span", { class: "label" }, "Today"),
      el("span", { class: "label num" }, new Date().toLocaleDateString(undefined, { day: "numeric", month: "short" }))),
    el("div", { class: "today-row" },
      el("span", {}, "Cards due"),
      el("span", { class: "num big" }, t.due)),
    el("div", { class: "today-row" },
      el("span", {}, "Weakest topic"),
      w
        ? el("a", { class: "today-topic", href: w.subject_id ? explainHref({ id: w.subject_id }, w.subtopic) : "#/" },
            el("b", {}, w.subtopic || "General"), el("span", { class: "num muted" }, `${w.accuracy}%`))
        : el("span", { class: "muted" }, "No quiz answers yet")),
    el("div", { class: "today-row" },
      el("span", {}, "Last score"),
      last
        ? el("span", { class: "today-score" },
            el("span", { class: "num big" }, `${last.accuracy}%`),
            el("small", { class: "muted num" }, `${last.correct}/${last.attempts}`))
        : el("span", { class: "muted" }, "–")),
    t.due && target
      ? btn("primary", "Start revision", { href: `#/s/${target.id}/revision` })
      : el("p", { class: "muted today-empty" }, "Nothing due today."));
}

function subjectCard(s) {
  return el("a", { class: "subject-card", href: `#/s/${s.id}` },
    el("div", { class: "subject-card-top" },
      el("h3", { class: "h3" }, s.name),
      icon("chevron")),
    el("dl", { class: "mini-stats" },
      el("div", {}, el("dt", {}, "PDFs"), el("dd", { class: "num" }, s.pdf_count)),
      el("div", {}, el("dt", {}, "Due"), el("dd", { class: "num" }, s.due_today)),
      el("div", {}, el("dt", {}, "Score"), el("dd", { class: "num" }, accuracyText(s.accuracy)))),
    bar(s.accuracy));
}

// "New subject" cell that turns into a small form.
function newSubjectCard() {
  const input = el("input", { type: "text", placeholder: "Subject name", maxlength: 100, required: true });
  const error = el("p", { class: "error" });
  const button = el("button", { type: "button", class: "subject-card add" }, icon("plus"), "New subject");
  const form = el("form", { class: "subject-card new-subject", hidden: true },
    label("New subject"),
    input,
    el("div", { class: "btn-row" },
      btn("primary", "Create", { type: "submit", small: true }),
      btn("secondary", "Cancel", { small: true, onclick: () => toggle(false) })),
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

  const items = SUBJECT_TABS.map(([key, text]) => [
    key, text, `#/s/${id}/${key}`, key === "revision" && subject.due_today ? subject.due_today : undefined,
  ]);
  const body = el("div", { class: "tab-body" });
  app.replaceChildren(
    section("page-head",
      el("div", { class: "page-head-grid" },
        el("div", {}, label("Subject"), el("h1", { class: "h1" }, subject.name)),
        el("div", { class: "grid-lined stats-3" },
          stat(accuracyText(subject.accuracy), "Score"),
          stat(subject.due_today, "Due"),
          stat(subject.pdf_count, subject.pdf_count === 1 ? "PDF" : "PDFs"))),
      tabs(items, tab === "explain" ? "progress" : tab)),
    section("body", body));

  const renderers = {
    overview: renderOverview,
    pdfs: renderPdfs,
    quiz: renderSubjectQuiz,
    revision: renderRevisionTab,
    progress: renderProgress,
    explain: renderExplain,
  };
  await (renderers[tab] || renderOverview)(body, subject, extra);
}

function explainHref(s, topic) {
  return `#/s/${s.id}/explain/${encodeURIComponent(topic)}`;
}

function practiceHref(s, topic) {
  return `#/s/${s.id}/quiz/${encodeURIComponent(topic)}`;
}

// One topic row: name, bar, score, and Explain / Practice buttons.
function topicRow(s, t) {
  const name = t.subtopic || "General";
  return el("div", { class: "topic-row" },
    el("div", { class: "topic-name" }, el("b", {}, name),
      el("small", { class: "muted num" }, `${t.correct}/${t.attempts} correct`)),
    bar(t.accuracy),
    el("span", { class: "num topic-pct" }, `${t.accuracy}%`),
    el("div", { class: "btn-row" },
      btn("secondary", "Explain", { href: explainHref(s, name), small: true }),
      btn("secondary", "Practice", { href: practiceHref(s, name), small: true })));
}

function renderOverview(body, s) {
  const status = el("p", { class: "status" });
  const practiceButton = btn("secondary", "Practice weak topics");
  // Gemini writes new questions for the weakest topics; answers count toward progress.
  practiceButton.addEventListener("click", async () => {
    practiceButton.disabled = true;
    status.className = "status";
    status.textContent = "Writing new questions for your weak topics. This can take a minute.";
    try {
      const questions = await api(`/subjects/${s.id}/practice`, { method: "POST" });
      runQuiz(body, questions, { title: "Practice: weak topics", backHref: `#/s/${s.id}` });
    } catch (err) {
      status.className = "status error";
      status.textContent = err.message;
      practiceButton.disabled = false;
    }
  });

  body.append(el("div", { class: "split" },
    el("div", {},
      label("Weakest topics"),
      s.weakest.length
        ? el("div", { class: "list-lined" }, s.weakest.map((t) => topicRow(s, t)))
        : el("p", { class: "muted" }, "Take a quiz to find your weak topics.")),
    el("aside", { class: "card next-step" },
      label("Next step"),
      el("p", {}, s.due_today
        ? `${plural(s.due_today, "card")} due today.`
        : s.pdf_count ? "No cards due. Practise the topics you miss most." : "Upload a lecture PDF to start."),
      el("div", { class: "btn-col" },
        s.due_today ? btn("primary", "Start Daily Revision", { href: `#/s/${s.id}/revision` }) : null,
        s.pdf_count ? practiceButton : btn("primary", "Upload a PDF", { href: `#/s/${s.id}/pdfs` })),
      status)));
}

// ---------- Subject: Explain a topic ----------

// The first visit asks Gemini; after that the saved explanation comes back instantly.
// "Regenerate" asks again and replaces it.
async function renderExplain(body, s, topic) {
  const status = el("p", { class: "status" });
  const content = el("div", { class: "explain" });
  const regen = btn("secondary", "Regenerate", { small: true, onclick: () => load(true) }, "refresh");

  async function load(regenerate) {
    regen.disabled = true;
    status.className = "status";
    status.textContent = regenerate ? "Writing a new explanation." : "Writing an explanation. This takes a few seconds.";
    try {
      const path = `/subjects/${s.id}/topics/${encodeURIComponent(topic)}/explain`;
      const e = await api(regenerate ? `${path}?regenerate=true` : path, { method: "POST" });
      status.textContent = "";
      content.replaceChildren(
        el("div", { class: "explain-text" }, e.explanation.map((text) => el("p", {}, text))),
        el("div", { class: "card worked" },
          label("Worked example"),
          el("p", { class: "problem" }, e.example.problem),
          el("p", { class: "solution" }, e.example.solution)),
        el("div", { class: "key-points" },
          label("Remember"),
          el("ol", { class: "list-lined" }, e.key_points.map((text, i) =>
            el("li", {}, el("span", { class: "num" }, String(i + 1).padStart(2, "0")), el("span", {}, text))))),
        el("p", { class: "muted saved" }, `Saved ${new Date(e.created_at).toLocaleString()}`));
    } catch (err) {
      status.className = "status error";
      status.textContent = err.message;
    } finally {
      regen.disabled = false;
    }
  }

  body.append(
    el("div", { class: "section-head explain-head" },
      el("div", {}, label("Explain"), el("h2", { class: "h2" }, topic)),
      el("div", { class: "btn-row" },
        regen,
        btn("primary", "Practice this topic", { href: practiceHref(s, topic), small: true }))),
    status,
    content);
  await load(false);
}

// ---------- Subject: PDFs tab ----------

async function renderPdfs(body, s) {
  const notes = await api(`/notes?subject=${encodeURIComponent(s.name)}`);
  body.append(el("div", { class: "split" },
    el("div", {},
      el("div", { class: "section-head" },
        el("h2", { class: "h2" }, "Lecture PDFs"),
        el("span", { class: "muted num" }, plural(notes.length, "PDF"))),
      notes.length
        ? el("div", { class: "list-lined" }, notes.map((n) => el("a", { class: "pdf-row", href: `#/s/${s.id}/pdf/${n.id}` },
            icon("file"),
            el("div", { class: "pdf-row-main" },
              el("b", {}, n.topic || "Untitled"),
              el("small", { class: "muted" },
                [n.filename, new Date(n.created_at).toLocaleDateString()].filter(Boolean).join(" · "))),
            el("span", { class: "num pdf-row-counts" }, `${plural(n.mcq_count, "MCQ")} · ${plural(n.card_count, "card")}`),
            icon("chevron"))))
        : el("p", { class: "muted" }, "No PDFs yet. Upload one to get notes, a quiz and flashcards.")),
    uploadForm(s)));
}

// Upload a PDF into this subject. If the same file is already here, ask first.
function uploadForm(s) {
  const file = el("input", { type: "file", accept: ".pdf,application/pdf", required: true });
  const button = btn("primary", "Upload and generate", { type: "submit" });
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

  const form = el("form", { class: "upload-form" }, file, button);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    button.disabled = true;
    status.className = "status";
    status.textContent = "Reading the PDF and writing notes. This can take a minute.";
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
  return el("aside", { class: "card upload-box" },
    label("Upload"),
    el("h3", { class: "h3" }, "Add a lecture"),
    el("p", { class: "muted" }, "One PDF at a time. You get notes, MCQs and flashcards for it."),
    form, status);
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
  body.append(el("div", { class: "split" },
    el("div", { class: "start-box" },
      label("Quiz"),
      el("h2", { class: "h2" }, "Mixed quiz"),
      el("p", { class: "lede" }, `${QUIZ_SIZE} questions from every PDF in ${s.name}. Topics you miss come up more often.`),
      btn("primary", "Start quiz", { onclick: start })),
    keysCard([["1-4", "pick an answer"], ["Enter", "next question"]])));
}

// Small bordered table of keyboard shortcuts.
function keysCard(rows) {
  return el("aside", { class: "card keys" },
    label("Keyboard"),
    el("dl", {}, rows.map(([k, text]) => el("div", {}, el("dt", { class: "num" }, k), el("dd", {}, text)))));
}

// Session header: title, "03 / 10" and a thin progress line.
function sessionHead(title, done, total, countText) {
  return el("div", { class: "session-head" },
    el("div", { class: "session-head-row" },
      el("span", { class: "label" }, title),
      el("span", { class: "num session-count" }, countText)),
    el("div", { class: "session-progress" }, el("div", { style: `width: ${total ? (100 * done) / total : 0}%` })));
}

function pad2(n) {
  return String(n).padStart(2, "0");
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

    const explanation = el("div", { class: "explanation" });
    const next = btn("primary", index === questions.length - 1 ? "See results" : "Next question",
      { onclick: () => { index++; show(); } });
    const footer = el("div", { class: "session-foot", hidden: true }, explanation, next);
    const mark = (ok) => el("span", { class: `mark ${ok ? "ok" : "no"}` }, icon(ok ? "check" : "x"));
    const options = q.options.map((text, j) =>
      el("button", { type: "button", class: "option", onclick: () => choose(j) },
        el("span", { class: "option-key num" }, LETTERS[j]),
        el("span", { class: "option-text" }, text)));

    function choose(j) {
      if (answered) return;
      answered = true;
      const correct = j === q.answer;
      options.forEach((b) => (b.disabled = true));
      options[j].classList.add("selected");
      options[j].append(mark(correct));
      if (!correct) {
        options[q.answer].classList.add("correct");
        options[q.answer].append(mark(true));
      }
      explanation.className = `explanation ${correct ? "ok" : "no"}`;
      explanation.replaceChildren(label(correct ? "Correct" : "Not quite"),
        q.explanation ? el("p", {}, q.explanation) : null);
      footer.hidden = false;
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

    const title = opts.title || "Quiz";
    container.replaceChildren(el("div", { class: "session" },
      sessionHead(q.practice ? `${title} · new question` : title,
        index, questions.length, `${pad2(index + 1)} / ${pad2(questions.length)}`),
      el("p", { class: "question-text" }, q.question),
      el("div", { class: "options" }, options),
      footer));
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
    goodLabel: "Correct",
    bad: wrong.length,
    badLabel: "Wrong",
    mistakesTitle: "Wrong answers",
    mistakes: wrong.map((r) => el("li", {},
      el("p", { class: "m-question" }, r.q.question),
      el("p", { class: "m-answer" }, el("span", { class: "mark ok" }, icon("check")), r.q.options[r.q.answer]),
      el("p", { class: "m-yours" }, el("span", { class: "mark no" }, icon("x")), r.q.options[r.chosen]),
      r.q.explanation ? el("p", { class: "m-note" }, r.q.explanation) : null,
      whyButton(r.q, r.chosen))),
    topics: wrong.map((r) => r.q.subtopic),
    onPractice: () => runQuiz(container, wrong.map((r) => r.q), { ...opts, practice: true, title: "Practice mistakes" }),
    backHref: opts.backHref,
  });
}

// "Why?" under a wrong answer: Gemini explains the mistake (saved, so asking again is instant).
function whyButton(q, chosen) {
  const text = el("p", { class: "m-why", hidden: true });
  const button = btn("secondary", "Why was mine wrong?", { small: true });
  button.addEventListener("click", async () => {
    button.disabled = true;
    text.hidden = false;
    text.className = "m-why";
    text.textContent = "Working it out...";
    try {
      const r = await postJson("/why", { note_id: q.note_id, question_index: q.question_index, chosen });
      text.textContent = r.why;
      button.hidden = true;
    } catch (err) {
      text.className = "m-why error";
      text.textContent = err.message;
      button.textContent = "Try again";
      button.disabled = false;
    }
  });
  return el("div", { class: "m-why-box" }, button, text);
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

  container.replaceChildren(el("div", { class: "report" },
    el("div", { class: "report-head" },
      el("div", {}, label("Session report"), el("h2", { class: "h2" }, "Session complete")),
      pixels("..gkl.k.gk..")),
    el("div", { class: "grid-lined stats-3" },
      stat(r.good, r.goodLabel, "good"),
      stat(r.bad, r.badLabel, r.bad ? "bad" : ""),
      stat(`${pct}%`, "Score")),
    bar(pct),
    el("div", { class: "split report-body" },
      el("div", {},
        label(r.mistakesTitle),
        r.bad
          ? el("ul", { class: "mistakes list-lined" }, r.mistakes)
          : el("p", { class: "lede" }, "No mistakes this round.")),
      el("aside", {},
        topics.length
          ? [label("Topics to revise"), el("div", { class: "tags" },
              topics.map((t) => el("a", { class: "tag", href: `${r.backHref}/explain/${encodeURIComponent(t)}` }, t)))]
          : null,
        el("div", { class: "btn-col" },
          r.bad ? btn("primary", "Practice mistakes again", { onclick: r.onPractice }) : null,
          btn("secondary", "Back to subject", { href: r.backHref }, "back"))))));
}

// ---------- Subject: Daily Revision tab ----------

const FORGOT = 1;      // "Forgot"     -> SM-2 quality 1
const REMEMBERED = 4;  // "Remembered" -> SM-2 quality 4

async function renderRevisionTab(body, s) {
  const cards = await api(`/cards/due?subject=${encodeURIComponent(s.name)}`);
  runRevision(body, cards, { backHref: `#/s/${s.id}` });
}

function days(n) {
  return plural(n, "day");
}

// Flashcard session. "Forgot" cards come back at the end of the same session.
// Those repeats (and practice rounds) are practice only: SM-2 already scheduled the
// card on its first answer, so they are not sent to the server again.
function runRevision(container, cards, opts) {
  if (!cards.length) {
    container.replaceChildren(el("div", { class: "empty" },
      label(opts.practice ? "Practice" : "Daily Revision"),
      el("h2", { class: "h2" }, "All done for today."),
      el("p", { class: "muted" }, "No cards are due in this subject. New ones come back on their review day."),
      el("div", { class: "btn-row spaced" }, btn("secondary", "Back to subject", { href: opts.backHref }, "back"))));
    return;
  }
  const queue = cards.map((card) => ({ card, repeat: Boolean(opts.practice), nextDays: null }));
  const firstAnswer = new Map(); // card id -> {card, remembered}; only the first showing counts
  let flipped = false;
  let busy = false;

  const front = el("p", { class: "fc-front" });
  const back = el("p", { class: "fc-back" });
  const backBox = el("div", { class: "fc-back-box", hidden: true }, label("Answer"), back);
  const hint = el("p", { class: "fc-hint" }, "Click the card to flip", el("span", { class: "keys-hint" }, " · or press ", el("span", { class: "num" }, "Space")));
  const cardBox = el("div", { class: "flashcard", onclick: flip },
    el("div", { class: "fc-front-box" }, label("Question"), front), backBox, hint);
  const buttons = el("div", { class: "review-buttons", hidden: true },
    btn("secondary", "Forgot", { onclick: () => answer(FORGOT) }, null),
    btn("primary", "Remembered", { onclick: () => answer(REMEMBERED) }));
  buttons.firstChild.append(el("span", { class: "kbd" }, "1"));
  buttons.lastChild.insertBefore(el("span", { class: "kbd" }, "2"), buttons.lastChild.lastChild);
  const head = el("div");
  const feedback = el("p", { class: "feedback", hidden: true });

  container.replaceChildren(el("div", { class: "session revision" }, head, cardBox, buttons, feedback));

  function render() {
    if (!queue.length) return finishRevision(container, [...firstAnswer.values()], opts);
    head.replaceChildren(sessionHead(opts.practice ? "Practice mistakes" : "Daily Revision",
      firstAnswer.size, cards.length, `${queue.length} left`));
    flipped = false;
    front.textContent = queue[0].card.front;
    back.textContent = queue[0].card.back;
    backBox.hidden = true;
    hint.hidden = false;
    cardBox.classList.remove("flipped");
    buttons.hidden = true;
  }

  function flip() {
    if (flipped || !queue.length) return;
    flipped = true;
    backBox.hidden = false;
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

  // Keys: Space flips, 1 = Forgot, 2 = Remembered.
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
        el("small", {}, `${r.correct} of ${r.attempts} correct`)),
      el("a", { class: "button secondary small", href: explainHref(s, r.subtopic || "General") }, "Explain"));
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

// ---------- Usage: Gemini tokens per feature ----------

async function showUsage() {
  setBreadcrumb([{ label: "Usage" }]);
  const rows = await api("/usage");
  const n = (value) => Number(value || 0).toLocaleString();
  const sum = (key) => rows.reduce((total, r) => total + r[key], 0);
  app.replaceChildren(
    el("h2", { class: "page-title" }, "Gemini token usage"),
    el("div", { class: "stat-row" },
      stat(n(sum("tokens_today")), "tokens today"),
      stat(n(sum("tokens")), "tokens in total"),
      stat(n(sum("calls")), "calls in total")),
    rows.length
      ? el("table", { class: "usage" },
          el("thead", {}, el("tr", {},
            ["Feature", "Calls today", "Tokens today", "Calls", "Tokens", "Avg / call",
              "Avg prompt", "Avg output", "Avg thinking"].map((h) => el("th", {}, h)))),
          el("tbody", {}, rows.map((r) => el("tr", {},
            el("td", {}, r.feature),
            [r.calls_today, r.tokens_today, r.calls, r.tokens, r.avg_total,
              r.avg_prompt, r.avg_output, r.avg_thinking].map((v) => el("td", {}, n(v)))))))
      : el("p", { class: "muted" }, "No Gemini calls yet."),
    el("p", { class: "muted" }, "Every call counts, including retries. Answers served from a cache use no tokens."),
  );
}

// ---------- Start ----------
route();
