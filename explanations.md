# Explanations

Lapse: notes on how it was built.

Short notes on each important step of turning the CLI into a web app.

## 1. Ignore the database file
Added `*.db` to `.gitignore`. Saved notes will live in a SQLite file (`notes.db`)
on your computer only. Like `.env` and PDFs, it should never go into git.

## 2. Move Gemini logic into `study_ai.py`
All the Gemini code (prompt, upload, retry + fallback models) now lives in
`study_ai.py`. `main.py` just imports it, so the CLI and the web server share
one copy of the logic. Two changes:
- Instead of `sys.exit()` when everything fails, it raises `GenerationError`.
  A web server must not quit on one failed request; it should send an error back.
- The prompt now also asks for a `subject` (e.g. "Discrete Mathematics"), which we save in the database.

## 3. New packages in `requirements.txt`
- `fastapi`: the web framework that defines our URLs (routes) like `/generate`.
- `uvicorn`: the server program that actually runs the FastAPI app.
- `python-multipart`: lets FastAPI read file uploads from a form.

## 4. SQLite storage in `db.py`
SQLite is a database stored in one file (`notes.db`), built into Python, so there's no server to install.
- `init_db()` creates the `notes` table if it doesn't exist yet.
- `save_note()` inserts a row. Lists (notes, mcqs) are stored as JSON text,
  because a SQLite column holds plain text, not Python lists.
- `list_notes()` / `get_note()` read rows back (and turn the JSON text back into lists).
- The `?` placeholders in SQL keep user data separate from the query (prevents SQL injection).

## 5. FastAPI backend in `app.py`
- `@app.post("/generate")` is a route: when the browser POSTs a PDF to `/generate`, this function runs.
  It checks the file is a PDF (max 20 MB), writes a temporary copy (Gemini needs a file on disk),
  calls `generate()`, deletes the temp file in `finally` (runs even on errors), saves to SQLite and returns the JSON.
- `GET /notes` lists saved notes, `GET /notes/{id}` returns one (404 if missing).
- `HTTPException(400, ...)` sends an error message back to the browser instead of crashing.
- `lifespan` runs `init_db()` once when the server starts.
- The route is a plain `def` (not `async def`), so FastAPI runs the slow Gemini call
  in a background thread and the server stays responsive.
- Try it: run `uvicorn app:app --reload` and open http://127.0.0.1:8000/docs for an auto-generated test page.

## 6. Frontend page (`static/index.html`)
Plain HTML: a sidebar of saved notes, an upload form, and a notes + quiz section
(hidden until a note is opened). `app.mount("/", StaticFiles(...))` in `app.py` makes FastAPI
serve the `static/` folder, so http://127.0.0.1:8000/ shows the page. It's added last so it
doesn't hide the API routes.

## 7. Styling (`static/style.css`)
Colors are defined once as CSS variables (`--accent`, `--correct`, ...) at the top and reused.
The page uses a two-column grid (sidebar + main) that becomes one column on small screens.
Quiz options get `.correct` (green) or `.wrong` (red) classes after you click.

## 8. Frontend logic (`static/app.js`)
- `fetch()` is how the browser talks to our FastAPI routes (`/generate`, `/notes`, `/notes/{id}`).
- Upload: the form's `submit` event builds a `FormData` (file + subject), POSTs it,
  shows "Generating..." and disables the button until the answer arrives.
- Sidebar: `loadNotesList()` fills the list of saved notes; clicking one opens it.
- Quiz: each option is a button. On click, the question locks, the right answer turns green,
  a wrong pick turns red, the explanation appears and the score updates. "Retry quiz" redraws it.
- AI text is inserted with `textContent` (not `innerHTML`), so it is always shown as plain text
  and can never run as HTML or script.

## 9. README
`README.md` explains setup (venv, install, `.env`) and how to run the web app and the CLI.

---

# Phase 2: quiz tracking and weak topics

## 10. `attempts` table and weakness math in `db.py`
- New table `attempts`: one row each time you answer a quiz question
  (which note, which question, its subtopic/subject, what you chose, right or wrong).
  SQLite has no true/false type, so `correct` is stored as 1 or 0.
- `weakness()` groups attempts by subject + subtopic with SQL `GROUP BY`, counts attempts
  and wrong answers, then adds accuracy % and a weakness score. Sorted weakest first.
- `note_accuracy()` gives your accuracy on one note (or `None` if you never tried its quiz).
- `init_db()` uses `CREATE TABLE IF NOT EXISTS`, so your existing `notes.db` just gets the new table.

### Why the score is `(wrong + 1) / (attempts + 2)`
The simple error rate is `wrong / attempts`. With very few answers it gives extreme results:
- 1 question, got it right → 0% wrong → "you've mastered this topic". Too confident!
- 1 question, got it wrong → 100% wrong → "this is your worst topic ever". Also too harsh.

Adding +1 to wrong and +2 to attempts is like pretending you already answered
**two extra questions: one right and one wrong**. So every topic starts at 50% ("we don't know yet"):

| Answers           | Plain error rate | Smoothed score       |
|-------------------|------------------|----------------------|
| 1 right           | 0%               | (0+1)/(1+2) = 33%    |
| 1 wrong           | 100%             | (1+1)/(1+2) = 67%    |
| 10 right          | 0%               | (0+1)/(10+2) = 8%    |
| 10 wrong          | 100%             | (10+1)/(10+2) = 92%  |

With few answers the score stays near the middle. With many answers the two
pretend answers hardly matter and the score gets close to your real error rate.
So a topic only shows up as "very weak" or "very strong" once there is real evidence.
(This trick is called *Laplace smoothing*.)

## 11. API routes: `POST /attempts`, `GET /weakness`
- `Attempt` is a Pydantic model: it describes the JSON the browser must send
  (`note_id`, `question_index`, `chosen`). FastAPI checks the types automatically and returns 422 if they're wrong.
- The server looks up the saved note to fill in subtopic, subject and whether the answer was correct.
  We never trust the browser to say "I was right": it could be buggy or edited.
- `GET /weakness?subject=Math` filters by subject; without `?subject=` you get all subjects.
- `GET /notes/{id}` now also returns `accuracy` (or `null` if you haven't tried that quiz).

## 12. Tests (`tests/test_attempts.py`)
Run them with `pytest` (from the project folder, venv active).
- A *fixture* is setup code pytest runs before a test. `client` points `db.DB_PATH` to a
  temporary file, so tests never touch your real `notes.db`, and gives a `TestClient`
  that calls the API without starting a real server.
- Tests cover the score math (0 attempts → 0.5, 1 right → 1/3, ...), right/wrong answers,
  bad input (404/400), weakest-first ordering, the subject filter and per-note accuracy.
- `pytest.approx` compares decimals safely (1/3 can't be stored exactly).
- `pytest.ini` tells pytest where the tests are and lets them import `app`/`db`.

## 13. Frontend saves every answer
When you click an option, `saveAttempt()` sends `{note_id, question_index, chosen}` to
`POST /attempts` as JSON. It runs in the background (`async`), so the quiz doesn't wait for it.
If saving fails, the quiz still works; the error only shows in the browser console (F12).

## 14. "Weak topics" page
- New sidebar button opens a section listing every subtopic, weakest first (the server already sorts).
- Each row: subtopic name + subject, a bar whose width is your accuracy %, and "accuracy · attempts".
- Bar color comes from the weakness score: red if > 0.5, yellow if 0.3–0.5, green if < 0.3.
  Thanks to smoothing, a topic answered right only once is yellow, not green; it turns green after a few right answers.
- The subject dropdown is filled from your attempts; changing it calls `/weakness?subject=...`.
- `showView()` is a small helper that shows one section and hides the other two.

## 15. Accuracy on each note page
`GET /notes/{id}` returns `accuracy`, and the note page shows it as a small badge,
e.g. "Your accuracy on this note: 60% (3/5 answers)". It's hidden if you never took that quiz.
After each answer is saved, the page re-fetches the note so the badge updates live.
It counts every answer, including retries.

## 16. README
Added the new routes, a "Weak topics" section and how to run the tests (`pytest`).

---

# Phase 3: flashcards with spaced repetition

## 17. Shuffling quiz options (`shuffle_options` in `study_ai.py`)
Gemini likes to put the correct answer in the same slot (often A), so you could learn
"pick A" instead of the material. Before a new note is saved, each question's options are shuffled
and `answer` is moved to wherever the correct option ended up.
- We shuffle a list of positions (`order`), not the options directly. `order[new] = old`,
  so the new answer index is `order.index(old_answer)`.
- It returns new dicts and doesn't change the input, which makes it easy to test.
- Notes saved before this change keep their old order.

## 18. `cards` table (`db.py`)
One row per flashcard: `front`, `back`, `subtopic`, `subject`, the note it came from,
and the SM-2 state: `ease` (starts 2.5), `interval_days` (0), `repetitions` (0),
`due_date` and `last_reviewed`.
- New cards are due **today**, so you can study them right away.
- `due_date` is stored as `"YYYY-MM-DD"` text. In that format, comparing text is the same as
  comparing dates (`"2026-01-09" < "2026-01-10"`), so `WHERE due_date <= today` just works.
- Cards with an empty front or back are skipped, in case the model sends a broken one.

## 19. Getting flashcards from Gemini (`study_ai.py`)
- The main prompt now also asks for 8-10 flashcards `{front, back, subtopic}`. `save_note()`
  saves them along with the note.
- Notes saved before this change have no cards. The note page shows a **Generate flashcards**
  button, which calls `POST /notes/{id}/flashcards`. That sends the saved notes text (not the PDF)
  to Gemini with a smaller prompt. It refuses if the note already has cards, so you can't get duplicates.
- The retry + fallback loop moved into `ask_with_fallback()`, so both prompts share it.
- In `FLASHCARD_PROMPT` the JSON braces are doubled (`{{ }}`) because `.format()` uses
  single braces for its `{topic}`, `{notes}` placeholders.

## 20. SM-2 in simple words (`srs.py`)
**The idea:** you forget things over time, but every time you successfully remember something,
you forget it more slowly. So review a card *just before* you'd forget it: soon after you first learn it,
then less and less often. Easy cards get pushed far away; hard cards keep coming back.

Each card remembers three numbers:
- **repetitions**: how many times in a row you've remembered it.
- **interval**: how many days until the next review.
- **ease**: how easy the card is for you, starting at 2.5. It is the number the interval
  gets multiplied by each time.

After you see the answer, you rate yourself:

| Button | Quality | Meaning                      |
|--------|---------|------------------------------|
| Again  | 1       | I forgot it                  |
| Hard   | 3       | Remembered, but it was hard  |
| Good   | 4       | Remembered normally          |
| Easy   | 5       | Remembered instantly         |

**Step 1: the new interval**
- Forgot (quality below 3): start over. repetitions = 0, see it again **tomorrow**.
- Remembered: repetitions goes up by 1, then
  - 1st time in a row → 1 day
  - 2nd time in a row → 6 days
  - after that → old interval × ease (rounded)

**Step 2: adjust ease**
`ease = ease + 0.1 - (5 - q) * (0.08 + (5 - q) * 0.02)`, which works out to:

| Button | Ease change |
|--------|-------------|
| Again  | −0.54       |
| Hard   | −0.14       |
| Good   | no change   |
| Easy   | +0.10       |

Ease never goes below **1.3**. Without that floor, a card you keep failing could end up with
a tiny ease and its interval would hardly grow, so you'd be stuck seeing it almost every day forever.

**Step 3:** `due_date = today + interval`.

**Example**: a new card, pressing Good every time (ease stays 2.5):

| Review | Interval        | Next review in |
|--------|-----------------|----------------|
| 1      | 1               | 1 day          |
| 2      | 6               | 6 days         |
| 3      | 6 × 2.5 = 15    | 15 days        |
| 4      | 15 × 2.5 = 37.5 | 38 days        |
| 5      | 38 × 2.5 = 95   | 95 days        |

Five reviews cover about five months. Press Again at any point and the card goes back to 1 day,
and its ease drops, so it will also grow more slowly from then on.

Small details:
- The interval is multiplied by the ease *before* the ease is updated.
- Rounding is normal rounding (37.5 → 38). Python's `round()` uses "banker's rounding",
  where `round(12.5)` is 12, so we use `floor(x + 0.5)` instead.
- `sm2()` takes numbers and returns numbers. It doesn't touch the database, so the tests can check
  the math directly. It also accepts a `today` so tests can use a fixed date.

## 21. Flashcard API routes (`app.py`)
- `GET /cards/due?subject=...` returns cards due today or earlier (overdue cards included), oldest first.
- `POST /cards/{id}/review` with `{"quality": 4}` runs `sm2()` on the card's saved state,
  stores the result and returns the updated card. `Field(ge=0, le=5)` makes FastAPI
  reject other numbers with a 422 error.
- `GET /notes/{id}` now also returns `card_count`.

## 22. Review page (frontend)
- The sidebar has a **Review** button with a badge showing how many cards are due. It updates after every
  review and after new cards are made.
- The page shows one card at a time. Click it (or press Space) to flip, then rate it
  with the four buttons (or keys 1-4). The next card appears and a line tells you when you'll
  see the previous one again ("Next review of that card in 6 days").
- A subject dropdown lets you review one subject at a time.
- A card you press Again on is due tomorrow, so it won't come back in the same session.

## 23. Tests
- `tests/test_shuffle.py`: the correct option text is still at `answer` after shuffling,
  the input isn't changed, and the answer really does move around.
- `tests/test_srs.py`: each button on a new and a mature card, Good five times gives 1, 6, 15, 38, 95,
  Again resets progress, and ease never drops below 1.3.
- `tests/test_cards.py`: the API routes. Gemini is replaced by a fake function (`monkeypatch`),
  so the "Generate flashcards" test runs without an API key or internet.

---

# Fixes and UI changes

## 24. Upload crash: Gemini sometimes sends `[ {...} ]`
**The bug:** we asked for a JSON object, but Gemini sometimes wrapped it in a list. `json.loads` worked
fine, but then `data.get("mcqs")` crashed (lists have no `.get`). That error wasn't caught, so FastAPI
answered with a 500 "Internal Server Error".

**The fix, in three layers:**
1. `unwrap()` in `study_ai.py`: if the answer is a list holding exactly one dict, use that dict.
2. `validate_notes()` checks the shape: `topic` is text, `notes`/`mcqs`/`flashcards` are lists,
   and every MCQ has a question, at least 2 options and an `answer` index that exists.
   If not, it raises `BadResponse`, and `ask_with_fallback()` retries, exactly like broken JSON.
   (The flashcard-only prompt has its own smaller check, `validate_flashcards()`.)
3. If every model and retry fails, `GenerationError` becomes a **502** with a readable message.
   `app.py` also catches *any other* unexpected error and returns a 502 with a generic message
   (the real error is still printed in the server log), so the browser never gets a bare 500.

On the page, `readJson()` reads the response as text first and then tries to parse it. So even if
the server sends something that isn't JSON, the upload page shows a clear message instead of a
confusing "Unexpected token" error.

Tests (`tests/test_study_ai.py`) use a `FakeClient` that pretends to be Gemini and returns
whatever answers we give it, so we can test a list-wrapped answer, a wrong shape followed by
a good one (retry works), and always-bad answers (ends in `GenerationError`) without internet.

## 25. One subject, many spellings
"discrete" and "Discrete Mathematics" were saved as separate subjects because they're different text.
- **Upload form:** the subject is now a dropdown of existing subjects (from `GET /subjects`), plus
  "Let the AI guess" and "+ New subject...", which shows a text box. Picking from a list avoids typos.
- **Case-insensitive:** `canonical_subject()` runs when a note is saved. If the subject matches an existing
  one ignoring case, the existing spelling is used ("DISCRETE mathematics" → "Discrete Mathematics").
  The `?subject=` filters use SQL's `COLLATE NOCASE`, which compares text ignoring upper/lower case.
- **Merging:** different names like "discrete" vs "Discrete Mathematics" can't be matched automatically,
  so `rename_subject.py` does it by hand:
  `python rename_subject.py "discrete" "Discrete Mathematics"`.
  It updates the `notes`, `attempts` and `cards` tables, because each stores its own copy of the subject.

## 26. Page names
"Weak topics" is now **Progress** and "Review" is now **Daily Revision**. That's the names
people see in the sidebar and headings. Since the page now shows mastery, the API route changed too:
`GET /weakness` → `GET /progress`.

## 27. Progress page: mastery instead of weakness
The old bar showed accuracy, but its color came from a separate weakness score, which was confusing:
two bars of the same length could have different colors. Now there is one number:

`mastery = (correct + 1) / (attempts + 2)`. Higher = better.

It's the same smoothing trick as before (section 10), just turned around: pretend you already answered
one question right and one wrong. A topic you got right once shows 67%, not 100%. Ten right in a row
shows 92%.

- The bar's **length is the mastery**, and its **color depends only on the length**:
  under 25% red, 25-75% yellow, over 75% green. So same length = same color, always.
- Each row: topic, subject, bar, mastery %, "X of Y correct". Lowest mastery first,
  so what needs work is at the top. If two topics tie, the one with more attempts comes first.
- The legend and the weak / so-so / strong labels are gone. The colors speak for themselves.

## 28. Daily Revision: two buttons
- **Forgot** sends SM-2 quality 1 and **Remembered** sends quality 4. `srs.py` is unchanged.
  It still works with any quality from 0 to 5, so going back to four buttons later only means changing the page.
- Keys: Space flips, 1 = Forgot, 2 = Remembered.
- **Forgotten cards come back at the end of the session.** The first "Forgot" is sent to the server
  (the card's ease drops and it's due tomorrow). The card then goes to the back of the queue. That second
  showing is **practice only** and is not sent again. Original SM-2 works this way too: repeats inside a
  session don't change the schedule. Otherwise forgetting a card twice in one sitting would punish its ease twice.
- "X left" counts what's still in the queue, including cards coming back. After each answer the page shows
  "next time: in X days".
- The page is just a big centered card and two buttons. The subject filter that used to be here is gone,
  because daily revision means everything that's due.

## 29. Deleting a note
- `DELETE /notes/{id}` removes the note **and** everything that belongs to it: its quiz attempts
  (so they disappear from Progress) and its flashcards (so they leave Daily Revision).
  It returns 404 if the note doesn't exist.
- `db.delete_note()` deletes from `attempts` and `cards` first, then `notes`, all inside one
  `with connect()` block. That block is one *transaction*: if anything fails halfway, SQLite undoes all of it,
  so you can never end up with flashcards pointing at a note that's gone.
- Why not let the database cascade automatically (`ON DELETE CASCADE`)? SQLite only does that when
  foreign keys are switched on for every connection, and the existing tables were created without it.
  Deleting from each table by hand is simple and easy to read.
- On the note page, **Delete** asks with `confirm()` first, since this can't be undone.
  Afterwards the app goes back to the upload page and refreshes the sidebar, the subject dropdown
  and the due-card badge.
- Tests (`tests/test_delete.py`) check the delete, the cascade, that other notes are untouched,
  and the 404 cases.

---

# Restructure around subjects

## 30. A `subjects` table
Until now a subject was just text on each note, so a subject couldn't exist without a PDF.
"New subject" needs that, so there's now a small `subjects` table (`id`, `name`).
- `name` is `UNIQUE COLLATE NOCASE`: "math" and "Math" can't both exist.
- Notes, attempts and cards still keep their own `subject` text, so all the existing filters
  (`?subject=`) keep working. The table is the list of subjects; the text links rows to it.
- **Migrating your existing `notes.db`** happens in `migrate()`, which runs on every start and is safe to repeat:
  1. Adds the new note columns `filename`, `pdf_path`, `pdf_hash` with `ALTER TABLE`
     (SQLite has no "add column if missing", so we check `PRAGMA table_info` first).
  2. Notes with an empty subject go to "Uncategorized".
  3. Every subject used by a note gets a row in `subjects`.
  4. Rows are rewritten to the registered spelling ("math" → "Math").
- Deleting a subject's last PDF keeps the subject (it shows 0 PDFs).
- `rename_subject.py` now also renames (or merges) the row in `subjects`.
- **A SQLite gotcha**: when comparing two columns, SQLite uses the collation of the *left* one.
  `notes.subject = subjects.name` compares case-sensitively, while `subjects.name = notes.subject`
  ignores case. That's why the queries say `COLLATE NOCASE` explicitly.

## 31. Navigation (`static/app.js`)
The page is now a small *single-page app*: one HTML page whose content is drawn by JavaScript.
The part of the URL after `#` says what to show:

| URL                      | Page                               |
|--------------------------|------------------------------------|
| `#/`                     | Home: subject cards                |
| `#/s/3/quiz`             | Subject 3, Quiz tab                |
| `#/s/3/quiz/Sets`        | Subject 3, quiz on the topic "Sets" (the Practice button) |
| `#/s/3/pdf/12/notes`     | PDF 12 in subject 3, Notes tab     |

Changing the `#` part doesn't reload the page. It fires a `hashchange` event, and `route()`
draws the right page. So the browser's Back button works, and you can bookmark a tab.
- The breadcrumb (Home › Subject › PDF) is rebuilt on every page.
- `el("div", {class: "x"}, "text", child)` is a tiny helper that builds elements. It always adds
  text as text (never HTML), so AI text still can't inject anything.
- Only one keyboard handler is active at a time (`keyHandler`), so the keys of an old quiz
  stop working when you leave the page.

## 32. Saved PDFs and the duplicate check
- An uploaded PDF is saved as `uploads/<sha256>.pdf`. `uploads/` is in `.gitignore`.
- **SHA-256** turns the file's bytes into a 64-character fingerprint. The same file always
  gives the same fingerprint, even if you rename it, and different files practically never collide.
- Before generating, the server checks whether that fingerprint already exists *in this subject*.
  If so, it answers **409 Conflict** instead of calling Gemini. The page asks
  "Generate notes from it again anyway?", and if you say yes it sends the upload again with `force=true`.
- Two notes from the same file share one file on disk, so deleting a PDF only removes the file
  when no other note uses it. If generation fails, the new file is removed again.

## 33. Generate more MCQs / flashcards
- `POST /notes/{id}/mcqs` and `POST /notes/{id}/flashcards` ask Gemini for new items.
  They use the saved PDF when there is one. Older notes (or a missing file) fall back to the saved notes text.
- The prompt lists the existing questions/fronts and says "do NOT repeat these". Models don't
  always listen, so the server also removes repeats itself: `normalize()` lowercases the text and drops
  punctuation and extra spaces, and `only_new()` keeps only items whose normalized text is new.
  "What is a SET?" and "what is a set" count as the same.
- New MCQs are **added to the end** of the note's list. Old questions keep their position, which
  matters because saved quiz attempts point at a question by its index.
- New MCQs get their options shuffled, like the first ones.
- The old "Generate flashcards" rule (only if the note has none) is gone: you can always add more.

## 34. Subject quiz: weak topics more often (`quiz.py`)
The Quiz tab mixes MCQs from every PDF in the subject. Picking them is a *weighted* random choice:
- Each question's weight is `1 - mastery` of its topic. A topic at 20% mastery has weight 0.8,
  a topic at 90% has weight 0.1, and an untried topic counts as 50% (weight 0.5).
- Because mastery is smoothed (section 27), it's never exactly 100%, so even your best topics
  still appear sometimes, just less often.
- To pick n *different* questions with weights, each question gets the random key
  `random() ** (1 / weight)` and the n biggest keys win. A big weight pushes the key toward 1,
  so weak questions usually win, but there's still luck involved. (This is the
  Efraimidis–Spirakis method: it's short and gives the right probabilities.)
- `?subtopic=Sets` restricts the quiz to one topic. That's what the **Practice** buttons on the
  Overview use.
- The tests check the weighting statistically: over 500 quizzes, weak questions show up more than
  3 times as often as strong ones, and strong ones still show up.

## 35. End-of-session report
After a quiz or a Daily Revision session, the question/card and buttons disappear and a report appears:
- Score: correct/wrong (quiz) or remembered/forgotten (revision), and accuracy %.
- Every wrong question with the correct answer (and what you chose), or every forgotten card with its answer.
- **Topics to revise**: the topics where you made a mistake in this session.
- **Practice mistakes again** starts a new round with only those questions/cards. Practice rounds
  are **not saved**: no quiz attempts, no SM-2 updates. You just saw the answers, so getting them
  right a minute later says little about whether you've learned them, and it would make your progress
  look better than it is.
- In Daily Revision, a card's result is its **first** answer in the session. If you forgot it and then
  remembered it when it came back, it still counts as forgotten.

## 36. Also fixed: broken characters
Some earlier edits saved `static/app.js` and `static/index.html` in the Windows "cp1252" encoding
instead of UTF-8, so characters like `·` showed up as `�` in the browser. The new files are UTF-8.

## 37. Progress shows raw accuracy again
Section 27 put the smoothed mastery on screen, so one right answer showed 67% instead of 100%.
That was confusing. Now the two numbers have separate jobs:

- **What you see** (bar length, bar color, the % text) is raw accuracy: `correct / attempts`, rounded.
  0 of 3 → 0% (red), 1 of 2 → 50% (yellow), 2 of 2 → 100% (green).
  Colors are the same as before: under 25% red, 25-75% yellow, over 75% green.
- **The order** still uses the smoothed `(correct + 1) / (attempts + 2)`, lowest first, so a topic
  with 0 of 1 right isn't treated as worse than one with 0 of 10. The quiz picker (section 34) also uses it.
- `GET /progress` and the weakest topics in `GET /subjects/{id}` return `accuracy` instead of `mastery`.
  The smoothed value isn't sent to the page at all, so it can't show up by accident.
- Home cards, session reports and the note page already showed raw accuracy, so they didn't change.

## 38. Empty uploads and clearer Gemini errors
An upload was once saved as `e3b0c442...b855.pdf`. That is the SHA-256 of zero bytes, so the server got an empty file.
- `POST /generate` reads the upload **once** into `content`. The same bytes are hashed (duplicate check),
  saved to `uploads/` and sent to Gemini (`generate(client, path, content)` uploads them from memory).
- A 0-byte upload is rejected with **400 "The uploaded file is empty"** before anything is saved or sent.
- After saving, the file size on disk is checked against the upload size. If they differ, the file is removed
  and the request fails with a 500 instead of continuing with a broken PDF.
- `study_ai.py` now prints the real Gemini error (code, status and message), not just the code.
- A 404 can mean "model not found" **or** "file not found". If the message is about the file
  (file, document, mime, pdf, page), trying the next model won't help, so it stops with a clear error.
  Model errors still move on to the next fallback model.

# Phase 4: AI tutor

## 39. Explain a topic
- `POST /subjects/{id}/topics/{topic}/explain` sends the subject's notes to Gemini and gets back short paragraphs,
  one worked example and exactly 3 key points, in simple English (`EXPLAIN_PROMPT` in `study_ai.py`).
- The result is saved in the `explanations` table (one row per subject + topic, any case), so the next click is instant.
  **Regenerate** (`?regenerate=true`) asks again and replaces the saved row.
- "Explain" buttons are on every Progress row and on the Overview's weakest topics; they open `#/s/{id}/explain/{topic}`.

## 40. Targeted practice
- `POST /subjects/{id}/practice` ranks topics by smoothed mastery (untried = 0.5), takes the 3 weakest and
  `plan_practice()` in `quiz.py` splits the questions in proportion to `1 - mastery`: every topic gets at least one, the weakest the most.
- The prompt lists the existing question stems of those topics so Gemini doesn't repeat them; repeats and off-topic answers are dropped anyway.
- New questions are saved as normal MCQs with `"practice": true` on the note that already has most questions on that topic,
  so answering them counts toward progress. The quiz shows an "AI practice" tag.

## 41. Why was I wrong?
- In the quiz report, each wrong answer has a **Why?** button. `POST /why` sends the question, all options, your choice
  and the correct answer, and gets back 3-4 sentences (`WHY_PROMPT`).
- Saved in the `why_wrong` table per (note, question, chosen option), so the same mistake never calls Gemini twice.
  The rows are deleted with their note.

## 42. Shared rules
- All prompts live in `study_ai.py`; each answer is checked by a `validate_*` function, and a bad shape is retried like before.
- `call_ai()` in `app.py` turns any Gemini failure into a 502 with a readable message; the page shows a loading text
  and that message. Tests fake Gemini (`tests/test_tutor.py`), including the caches and Regenerate.
- UI text is English: the flashcard buttons are now **Forgot** and **Remembered**.

# Token usage

## 43. Measuring first: `ai_usage` and the Usage page
- After every Gemini call (retries included), `study_ai.log_usage` reads `response.usage_metadata` and saves prompt,
  output, thinking and total tokens with the feature and model in the `ai_usage` table. A logging error never breaks a request.
- `GET /usage` and the **Usage** page (header link) show tokens per feature today and in total, and the average per call.
- `python measure_tokens.py lecture.pdf` runs every feature once on a real PDF with a throwaway database and prints the averages.
- Baseline (one real run on a 520 KB lecture PDF, gemini-3.6-flash, total ≈ 45,400 tokens):

| feature | avg total | prompt | output | thinking |
|---|---:|---:|---:|---:|
| generate | 13,767 | 8,801 | 2,354 | 2,612 |
| more_mcqs | 12,594 | 8,870 | 820 | 2,904 |
| flashcards | 11,666 | 8,770 | 726 | 2,170 |
| explain | 2,255 | 669 | 478 | 1,108 |
| practice | 4,024 | 815 | 1,152 | 2,057 |
| why_wrong | 1,075 | 182 | 83 | 810 |

## 44. Extract the PDF text once
- On upload, `extract_text()` (pypdf) reads the text once and stores it in `notes.source_text`. Spaces, blank lines,
  repeated page headers and page numbers are removed. Generate more, explain and practice use this text.
- If there are fewer than 100 characters per page (a scanned PDF) or pypdf fails, `source_text` is `""` and the PDF
  itself is sent, like before. Older notes get their text extracted from the saved PDF the first time it's needed.
- Measured: this PDF's text costs about as many tokens as the PDF did (~8,700), because it is dense with symbols.
  The win is that we can now send *part* of it (sections 45-46) and skip re-uploading the file.

## 45. Send only what's needed
- `context.py` splits the text into ~600-character chunks and scores chunks and note bullets by shared word stems
  with the topic name (weight 3) and its question stems (weight 1). Explain and practice get only the best matches
  (at least half the top score), within a fixed budget, instead of the whole subject.
- Generate more MCQs/flashcards send at most 9,000 characters: the chunks the existing items cover *least*
  (`least_covered`). New questions should come from new material anyway.
- Duplicate avoidance sends only question stems / card fronts; Why? sends only the question, options, choice and answer.

## 46. Right model and limits per task
- `TASKS` in `study_ai.py` sets, per feature, the lite model first or not, `max_output_tokens`, and the thinking level.
  Why? and flashcards use `GEMINI_LITE_MODEL` (default gemini-3.5-flash-lite) with `MINIMAL` thinking; the rest use `LOW`.
- A model that rejects the thinking setting is asked again without it (same model). Bad JSON now logs Gemini's finish reason,
  so a too-small `max_output_tokens` shows up as `MAX_TOKENS`.
- Prompts were shortened: each rule once and one-line JSON shapes (`MCQ`, `CARD`) shared by all prompts.

## 47. Cache by hash
- `cached_ask()` keys the answer by `sha256(feature + input)` in the `ai_cache` table. Generate (the text, or the
  scanned PDF's hash), explain and why_wrong use it, so an identical request never calls Gemini twice.
  The `explanations` and `why_wrong` tables stay as they were; Regenerate skips the lookup and stores the new answer.
- Generate more and practice aren't cached: their input lists every existing item, so a repeat means "give me different ones".

## 48. Before / after
Average tokens per call, one real run of `measure_tokens.py` on the same 16-page PDF, gemini-3.6-flash, empty cache:

| feature | before | after | change |
|---|---:|---:|---:|
| generate | 13,767 | 10,582 | −23% |
| more_mcqs | 12,594 | 4,281 | −66% |
| flashcards | 11,666 | 4,068 | −65% |
| explain | 2,255 | 1,356 | −40% |
| practice | 4,024 | 2,421 | −40% |
| why_wrong | 1,075 | 232 | −78% |

A whole run went from about 45,400 to 22,900 tokens (−49%). Most of the drop is thinking (2,000-2,900 → 0 tokens per call)
and the smaller text for generate more. Cached repeats of generate, explain and why_wrong now cost 0.

## 49. First generate: text or the PDF itself
- `read_pdf()` reads the PDF once: its text, pages, characters per page and whether any page has an embedded image.
- `text_for_first_generate()` sends the **PDF** if it has embedded images (diagrams, picture slides) or under
  500 characters of text per page on average, because extracted text would miss too much. Otherwise it sends the text.
- The chosen path is printed: "First generate: sending the PDF, it has embedded images." or "...sending extracted text (1543 characters per page, no images)."
- Either way the extracted text (if at least 100 characters per page) is stored, so later features still don't resend the PDF.

# UI

## 50. Monochrome design system
- `static/style.css` starts with variables (colours, fonts, spacing, 1px line) and shared classes: `.btn-primary`, `.btn-secondary`, `.card`, `.tabs`, `.label`, `.grid-lined`, `.list-lined`, `.bar`.
- Colour is used only for progress bars and right/wrong answers (muted red, amber, green). Everything else is black lines on off-white, sharp corners, no shadows.
- Fonts: Archivo expanded for headings, Instrument Sans for text, IBM Plex Mono for numbers.
- `GET /today` feeds the home "Today" box; the last score is the accuracy on the most recent day with quiz answers.

# Deployment

## 51. One db layer for SQLite and Postgres
- `DATABASE_URL` set = Postgres (psycopg with a small connection pool), otherwise the `notes.db` SQLite file.
- All SQL uses `?` and portable syntax (`LOWER(a) = LOWER(b)`, `ON CONFLICT`); `Conn` in `db.py` swaps `?` for `%s` and gets new ids with `RETURNING id`.
- Only the schema differs (`BIGSERIAL`, case-insensitive unique indexes). Date grouping moved to Python so it works on both.
- Tests always use SQLite; `tests/test_postgres.py` runs only when `DATABASE_URL` is set.

## 52. Guests and per-user data
- First visit: an httpOnly `lapse_uid` cookie with a random uuid. That id is the `user_id`.
- Shared: subjects, notes, MCQs, cards, explanations. Per user: `attempts.user_id`, `card_state` (each user's SM-2 schedule; a card without a row is new, due today) and `ai_quota`.
- The admin (passcode) is user `owner`, which also owns all progress from before guests existed. A login later just maps an account to a `user_id`.

## 53. Admin passcode and AI limits
- `ADMIN_PASSCODE` guards upload, generate more, delete, new/rename subject and Regenerate (`X-Admin-Passcode` header). Without it: local = admin, production = nobody.
- `study_ai.before_call` runs right before a real Gemini request, never for a cached one. `app.spend_ai_call` counts one per action and refuses after 10 per guest or 200 in total per UTC day (429).

## 54. No disk writes in production
- With Postgres, `/generate` keeps the PDF in memory: Gemini gets the bytes, the database gets the text. Nothing goes to `uploads/`.
- `api/index.py` refuses to start without `DATABASE_URL` instead of creating a SQLite file on Vercel's read-only disk.
