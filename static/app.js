// Frontend logic: talks to the FastAPI backend with fetch().
// We always use textContent (not innerHTML) for AI text, so it can't inject HTML.

const $ = (id) => document.getElementById(id);
const LETTERS = "ABCD";

let currentNote = null;

// ---------- Views ----------

// Show one section (upload / note / weak) and hide the others.
function showView(name) {
  for (const view of ["upload", "note", "weak"]) {
    $(`${view}-view`).hidden = view !== name;
  }
}

function showUpload() {
  currentNote = null;
  showView("upload");
  highlightActive(null);
}

function showNote(note) {
  currentNote = note;
  showView("note");

  $("note-subject").textContent = note.subject || "";
  $("note-topic").textContent = note.topic || "Untitled";
  $("note-date").textContent = new Date(note.created_at).toLocaleString();

  const list = $("note-notes");
  list.innerHTML = "";
  for (const text of note.notes) {
    const li = document.createElement("li");
    li.textContent = text;
    list.appendChild(li);
  }

  showAccuracy(note.accuracy);
  renderQuiz(note.mcqs);
  highlightActive(note.id);
}

// accuracy is {attempts, correct, accuracy} or null if never tried.
function showAccuracy(acc) {
  const el = $("note-accuracy");
  el.hidden = !acc;
  if (acc) {
    el.textContent = `Your accuracy on this note: ${acc.accuracy}% (${acc.correct}/${acc.attempts} answers)`;
  }
}

// ---------- Saved notes sidebar ----------

async function loadNotesList() {
  const res = await fetch("/notes");
  const notes = await res.json();
  const ul = $("notes-list");
  ul.innerHTML = "";

  if (notes.length === 0) {
    const li = document.createElement("li");
    li.className = "s";
    li.textContent = "No notes yet.";
    ul.appendChild(li);
    return;
  }

  for (const n of notes) {
    const li = document.createElement("li");
    li.dataset.id = n.id;

    const title = document.createElement("div");
    title.className = "t";
    title.textContent = n.topic || "Untitled";

    const sub = document.createElement("div");
    sub.className = "s";
    sub.textContent = `${n.subject || ""} · ${new Date(n.created_at).toLocaleDateString()}`;

    li.append(title, sub);
    li.addEventListener("click", () => openNote(n.id));
    ul.appendChild(li);
  }
  if (currentNote) highlightActive(currentNote.id);
}

function highlightActive(id) {
  for (const li of $("notes-list").children) {
    li.classList.toggle("active", li.dataset.id == id);
  }
}

async function openNote(id) {
  const res = await fetch(`/notes/${id}`);
  if (!res.ok) return alert("Could not load that note.");
  showNote(await res.json());
}

// ---------- Upload ----------

$("upload-form").addEventListener("submit", async (event) => {
  event.preventDefault(); // stop the browser from reloading the page

  const form = new FormData();
  form.append("file", $("file").files[0]);
  form.append("subject", $("subject").value);

  const status = $("status");
  const button = $("submit-btn");
  status.className = "";
  status.textContent = "Generating... this can take a minute.";
  button.disabled = true;

  try {
    const res = await fetch("/generate", { method: "POST", body: form });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Something went wrong.");

    status.textContent = "";
    $("upload-form").reset();
    showNote(data);
    loadNotesList();
  } catch (err) {
    status.className = "error";
    status.textContent = err.message;
  } finally {
    button.disabled = false;
  }
});

// ---------- Quiz ----------

function renderQuiz(mcqs) {
  const quiz = $("quiz");
  quiz.innerHTML = "";
  let answered = 0;
  let score = 0;

  const updateScore = () => {
    $("score").textContent = `Score: ${score} / ${mcqs.length}`;
    $("retry-btn").hidden = answered < mcqs.length;
  };

  mcqs.forEach((q, i) => {
    const box = document.createElement("div");
    box.className = "question";

    const p = document.createElement("p");
    p.textContent = `Q${i + 1}. ${q.question}`;
    box.appendChild(p);

    const buttons = q.options.map((option, j) => {
      const btn = document.createElement("button");
      btn.className = "option";
      btn.textContent = `${LETTERS[j]}) ${option}`;
      btn.addEventListener("click", () => {
        // Lock this question, then color the answers.
        buttons.forEach((b) => (b.disabled = true));
        buttons[q.answer].classList.add("correct");
        if (j === q.answer) {
          score++;
        } else {
          btn.classList.add("wrong");
        }
        answered++;
        saveAttempt(i, j);

        const exp = document.createElement("p");
        exp.className = "explanation";
        exp.textContent = q.explanation || "";
        box.appendChild(exp);

        updateScore();
      });
      box.appendChild(btn);
      return btn;
    });

    quiz.appendChild(box);
  });

  updateScore();
}

// Send one answered question to the backend. The server works out
// whether it was correct, so we only send which option was clicked.
async function saveAttempt(questionIndex, chosen) {
  const noteId = currentNote.id;
  try {
    await fetch("/attempts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ note_id: noteId, question_index: questionIndex, chosen }),
    });
    // Refresh the accuracy line (only if the user is still on this note).
    const note = await (await fetch(`/notes/${noteId}`)).json();
    if (currentNote && currentNote.id === noteId) showAccuracy(note.accuracy);
  } catch (err) {
    console.error("Could not save attempt:", err);
  }
}

// ---------- Weak topics ----------

// Same thresholds as the explanation: score is the smoothed error rate (0..1).
function level(score) {
  if (score > 0.5) return "weak";
  if (score >= 0.3) return "medium";
  return "strong";
}

async function showWeak() {
  currentNote = null;
  showView("weak");
  highlightActive(null);
  await fillSubjectFilter();
  await loadWeakness();
}

// Rebuild the dropdown from all subjects that have attempts, keeping the selection.
async function fillSubjectFilter() {
  const select = $("subject-filter");
  const selected = select.value;
  const rows = await (await fetch("/weakness")).json();
  const subjects = [...new Set(rows.map((r) => r.subject))].sort();

  select.innerHTML = "";
  for (const value of ["", ...subjects]) {
    const opt = document.createElement("option");
    opt.value = value;
    opt.textContent = value || "All subjects";
    select.appendChild(opt);
  }
  select.value = subjects.includes(selected) ? selected : "";
}

async function loadWeakness() {
  const subject = $("subject-filter").value;
  const url = subject ? `/weakness?subject=${encodeURIComponent(subject)}` : "/weakness";
  const rows = await (await fetch(url)).json();

  const list = $("weak-list");
  list.innerHTML = "";
  if (rows.length === 0) {
    const p = document.createElement("p");
    p.className = "muted";
    p.textContent = "No quiz answers yet. Take a quiz to see your weak topics.";
    list.appendChild(p);
    return;
  }

  for (const r of rows) {
    const row = document.createElement("div");
    row.className = "weak-row";

    const name = document.createElement("div");
    name.className = "weak-name";
    name.textContent = r.subtopic || "General";
    const sub = document.createElement("small");
    sub.textContent = r.subject || "";
    name.appendChild(sub);

    const track = document.createElement("div");
    track.className = "bar-track";
    const bar = document.createElement("div");
    bar.className = `bar ${level(r.score)}`;
    bar.style.width = `${Math.max(r.accuracy, 2)}%`; // keep a sliver visible at 0%
    track.appendChild(bar);

    const stats = document.createElement("div");
    stats.className = "weak-stats";
    const pct = document.createElement("b");
    pct.textContent = `${r.accuracy}%`;
    stats.append(pct, ` · ${r.attempts} attempt${r.attempts === 1 ? "" : "s"}`);

    row.append(name, track, stats);
    list.appendChild(row);
  }
}

$("subject-filter").addEventListener("change", loadWeakness);
$("weak-btn").addEventListener("click", showWeak);

$("retry-btn").addEventListener("click", () => renderQuiz(currentNote.mcqs));
$("new-btn").addEventListener("click", showUpload);

// ---------- Start ----------
loadNotesList();
