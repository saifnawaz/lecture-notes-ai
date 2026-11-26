// Frontend logic: talks to the FastAPI backend with fetch().
// We always use textContent (not innerHTML) for AI text, so it can't inject HTML.

const $ = (id) => document.getElementById(id);
const LETTERS = "ABCD";

let currentNote = null;

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

// ---------- Views ----------

// Show one section (upload / note / progress / review) and hide the others.
function showView(name) {
  for (const view of ["upload", "note", "progress", "review"]) {
    $(`${view}-view`).hidden = view !== name;
  }
}

function showUpload() {
  currentNote = null;
  showView("upload");
  highlightActive(null);
  loadSubjectOptions();
}

// ---------- Upload subject picker ----------

const NEW_SUBJECT = "__new__";

// Fill the dropdown: "Let the AI guess", existing subjects, "+ New subject...".
async function loadSubjectOptions() {
  const select = $("subject-select");
  const selected = select.value;
  const subjects = await (await fetch("/subjects")).json();

  select.innerHTML = "";
  const options = [["", "Let the AI guess"], ...subjects.map((s) => [s, s]), [NEW_SUBJECT, "+ New subject..."]];
  for (const [value, label] of options) {
    const opt = document.createElement("option");
    opt.value = value;
    opt.textContent = label;
    select.appendChild(opt);
  }
  if ([...select.options].some((o) => o.value === selected)) select.value = selected;
  toggleNewSubject();
}

function toggleNewSubject() {
  const isNew = $("subject-select").value === NEW_SUBJECT;
  $("new-subject-label").hidden = !isNew;
  $("new-subject").required = isNew;
  if (isNew) $("new-subject").focus();
}

// If the typed name matches an existing subject (any case), use the existing one.
function chosenSubject() {
  const value = $("subject-select").value;
  if (value !== NEW_SUBJECT) return value;
  const typed = $("new-subject").value.trim();
  const match = [...$("subject-select").options].find(
    (o) => o.value !== NEW_SUBJECT && o.value && o.value.toLowerCase() === typed.toLowerCase()
  );
  return match ? match.value : typed;
}

$("subject-select").addEventListener("change", toggleNewSubject);

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
  showCardInfo(note.card_count);
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

// Flashcard line on the note page: count, or a button for older notes without cards.
function showCardInfo(count) {
  $("card-info").textContent = count ? `${count} flashcards from this note` : "No flashcards yet.";
  $("gen-cards-btn").hidden = count > 0;
}

$("gen-cards-btn").addEventListener("click", async () => {
  const noteId = currentNote.id;
  const button = $("gen-cards-btn");
  button.disabled = true;
  $("card-info").textContent = "Making flashcards...";
  try {
    const data = await readJson(await fetch(`/notes/${noteId}/flashcards`, { method: "POST" }));
    if (currentNote && currentNote.id === noteId) showCardInfo(data.card_count);
    loadDueCount();
  } catch (err) {
    $("card-info").textContent = err.message;
  } finally {
    button.disabled = false;
  }
});

// ---------- Delete note ----------

$("delete-btn").addEventListener("click", async () => {
  if (!currentNote) return;
  if (!confirm("Delete this note? Its quiz history and flashcards will also be deleted.")) return;

  const button = $("delete-btn");
  button.disabled = true;
  try {
    await readJson(await fetch(`/notes/${currentNote.id}`, { method: "DELETE" }));
    showUpload(); // also refreshes the subject dropdown
    loadNotesList();
    loadDueCount(); // its flashcards are gone
  } catch (err) {
    alert(err.message);
  } finally {
    button.disabled = false;
  }
});

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
    sub.textContent = `${n.subject || ""} Â· ${new Date(n.created_at).toLocaleDateString()}`;

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
  form.append("subject", chosenSubject());

  const status = $("status");
  const button = $("submit-btn");
  status.className = "";
  status.textContent = "Generating... this can take a minute.";
  button.disabled = true;

  try {
    const res = await fetch("/generate", { method: "POST", body: form });
    const data = await readJson(res);

    status.textContent = "";
    $("upload-form").reset();
    showNote(data);
    loadNotesList();
    loadSubjectOptions();
    loadDueCount();
  } catch (err) {
    status.className = "error";
    // A network failure (server down) gives a TypeError with a vague message.
    status.textContent = err instanceof TypeError ? "Could not reach the server." : err.message;
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

// ---------- Progress page ----------

// Bar color depends only on how full it is.
function barColor(pct) {
  if (pct < 25) return "low";
  if (pct > 75) return "high";
  return "mid";
}

async function showProgress() {
  currentNote = null;
  showView("progress");
  highlightActive(null);
  await fillSubjectFilter();
  await loadProgress();
}

// Rebuild the dropdown from all subjects that have attempts, keeping the selection.
async function fillSubjectFilter() {
  const select = $("subject-filter");
  const selected = select.value;
  const rows = await (await fetch("/progress")).json();
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

async function loadProgress() {
  const subject = $("subject-filter").value;
  const url = subject ? `/progress?subject=${encodeURIComponent(subject)}` : "/progress";
  const rows = await (await fetch(url)).json(); // already sorted, lowest mastery first

  const list = $("progress-list");
  list.innerHTML = "";
  if (rows.length === 0) {
    const p = document.createElement("p");
    p.className = "muted";
    p.textContent = "No quiz answers yet. Take a quiz to see your progress.";
    list.appendChild(p);
    return;
  }

  for (const r of rows) {
    const pct = Math.round(r.mastery * 100);

    const row = document.createElement("div");
    row.className = "progress-row";

    const name = document.createElement("div");
    name.className = "progress-name";
    name.textContent = r.subtopic || "General";
    const sub = document.createElement("small");
    sub.textContent = r.subject || "";
    name.appendChild(sub);

    const track = document.createElement("div");
    track.className = "bar-track";
    const bar = document.createElement("div");
    bar.className = `bar ${barColor(pct)}`;
    bar.style.width = `${pct}%`;
    track.appendChild(bar);

    const stats = document.createElement("div");
    stats.className = "progress-stats";
    const big = document.createElement("b");
    big.textContent = `${pct}%`;
    const small = document.createElement("small");
    small.textContent = `${r.correct} of ${r.attempts} correct`;
    stats.append(big, small);

    row.append(name, track, stats);
    list.appendChild(row);
  }
}

$("subject-filter").addEventListener("change", loadProgress);
$("progress-btn").addEventListener("click", showProgress);

// ---------- Daily Revision (flashcards) ----------

const FORGOT = 1;      // "Bhool gaya" -> SM-2 quality 1
const REMEMBERED = 4;  // "Yaad tha"   -> SM-2 quality 4

// Session queue. Each item is {card, repeat, nextDays}.
// repeat = true means a "Bhool gaya" card coming back at the end of the session.
// Repeats are practice only: SM-2 already scheduled the card on the first answer,
// so they are not sent to the server again.
let queue = [];
let flipped = false;
let busy = false;

// Due count in the nav.
async function loadDueCount() {
  const cards = await (await fetch("/cards/due")).json();
  const badge = $("due-badge");
  badge.textContent = cards.length;
  badge.hidden = cards.length === 0;
  return cards;
}

async function showReview() {
  currentNote = null;
  showView("review");
  highlightActive(null);
  document.activeElement.blur(); // so Space flips the card instead of re-clicking a button
  $("review-feedback").hidden = true;

  const cards = await loadDueCount();
  queue = cards.map((card) => ({ card, repeat: false, nextDays: null }));
  renderCard();
}

function renderCard() {
  const left = queue.length;
  $("review-count").textContent = left ? `${left} left` : "";
  $("review-done").hidden = left > 0;
  $("flashcard").hidden = left === 0;
  $("review-buttons").hidden = true;
  if (left === 0) return;

  flipped = false;
  $("fc-front").textContent = queue[0].card.front;
  $("fc-back").textContent = queue[0].card.back;
  $("fc-back").hidden = true;
  $("fc-hint").hidden = false;
  $("flashcard").classList.remove("flipped");
}

function flipCard() {
  if (flipped || queue.length === 0) return;
  flipped = true;
  $("fc-back").hidden = false;
  $("fc-hint").hidden = true;
  $("flashcard").classList.add("flipped");
  $("review-buttons").hidden = false;
}

function days(n) {
  return `${n} day${n === 1 ? "" : "s"}`;
}

async function answerCard(quality) {
  if (!flipped || busy) return;
  busy = true;
  const item = queue.shift();
  const feedback = $("review-feedback");
  feedback.className = "feedback";

  try {
    if (!item.repeat) {
      const res = await fetch(`/cards/${item.card.id}/review`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ quality }),
      });
      item.nextDays = (await readJson(res)).interval_days;
      loadDueCount();
    }
    let text = `next time: in ${days(item.nextDays)}`;
    if (quality === FORGOT) {
      queue.push({ ...item, repeat: true }); // see it again at the end of this session
      text += " · coming back in this session";
    }
    feedback.textContent = text;
  } catch (err) {
    queue.unshift(item); // keep the card so nothing is lost
    feedback.className = "feedback error";
    feedback.textContent = err.message;
  }
  feedback.hidden = false;
  busy = false;
  renderCard();
}

$("flashcard").addEventListener("click", flipCard);
for (const btn of $("review-buttons").querySelectorAll("button")) {
  btn.addEventListener("click", () => {
    btn.blur();
    answerCard(Number(btn.dataset.quality));
  });
}

// Keyboard: Space flips, 1 = Bhool gaya, 2 = Yaad tha.
document.addEventListener("keydown", (event) => {
  if ($("review-view").hidden || ["INPUT", "SELECT", "TEXTAREA"].includes(event.target.tagName)) return;
  if (event.code === "Space") {
    event.preventDefault();
    flipCard();
  } else if (event.key === "1") {
    answerCard(FORGOT);
  } else if (event.key === "2") {
    answerCard(REMEMBERED);
  }
});

$("review-btn").addEventListener("click", showReview);

$("retry-btn").addEventListener("click", () => renderQuiz(currentNote.mcqs));
$("new-btn").addEventListener("click", showUpload);

// ---------- Start ----------
loadNotesList();
loadSubjectOptions();
loadDueCount();
