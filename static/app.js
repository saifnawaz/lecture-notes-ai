// Frontend logic: talks to the FastAPI backend with fetch().
// We always use textContent (not innerHTML) for AI text, so it can't inject HTML.

const $ = (id) => document.getElementById(id);
const LETTERS = "ABCD";

let currentNote = null;

// ---------- Views ----------

function showUpload() {
  currentNote = null;
  $("upload-view").hidden = false;
  $("note-view").hidden = true;
  highlightActive(null);
}

function showNote(note) {
  currentNote = note;
  $("upload-view").hidden = true;
  $("note-view").hidden = false;

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

  renderQuiz(note.mcqs);
  highlightActive(note.id);
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

$("retry-btn").addEventListener("click", () => renderQuiz(currentNote.mcqs));
$("new-btn").addEventListener("click", showUpload);

// ---------- Start ----------
loadNotesList();
