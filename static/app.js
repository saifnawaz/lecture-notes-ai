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
    subjects.length ? null : el("p", { class: "muted" }, "Create your first subject, then upload PDFs into it."),
  );
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

  const renderers = { overview: renderOverview };
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
            el("small", {}, `${Math.round(t.mastery * 100)}% mastery · ${t.correct} of ${t.attempts} correct`)),
          el("a", { class: "button secondary", href: `#/s/${s.id}/quiz/${encodeURIComponent(t.subtopic)}` },
            "Practice"))))
      : el("p", { class: "muted" }, "Take a quiz to find your weak topics."),
  );
}

// ---------- Start ----------
route();
