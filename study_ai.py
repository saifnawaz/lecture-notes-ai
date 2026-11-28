"""Gemini logic: upload a file and get notes + MCQs back as a dict.

Used by both main.py (CLI) and app.py (web server).
"""
import hashlib
import io
import json
import os
import random
import re
import time
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import errors, types
from pypdf import PdfReader

load_dotenv()

API_KEY = os.getenv("GEMINI_API_KEY")
MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
FALLBACKS = [m.strip() for m in os.getenv("GEMINI_FALLBACKS", "gemini-3.5-flash,gemini-3.5-flash-lite").split(",") if m.strip()]
RETRIES = 3
MIN_CHARS_PER_PAGE = 100  # less than this on average = probably a scanned PDF: no usable text at all
TEXT_ONLY_CHARS_PER_PAGE = 500  # first generate sends text only from this much per page, and no images
MAX_TEXT_CHARS = 120_000  # about 30k tokens; longer material is cut

LITE_MODEL = os.getenv("GEMINI_LITE_MODEL", "gemini-3.5-flash-lite")

# Per feature: use the lite model first?, the most output tokens it may write (the JSON
# with room to spare; a cut-off answer is retried), and how much the model may "think".
# MINIMAL is the lowest level; a model that doesn't accept a level is asked without one.
TASKS = {
    "generate": {"lite": False, "max_tokens": 8192, "thinking": "LOW"},
    "more_mcqs": {"lite": False, "max_tokens": 4096, "thinking": "LOW"},
    "practice": {"lite": False, "max_tokens": 6144, "thinking": "LOW"},
    "explain": {"lite": False, "max_tokens": 4096, "thinking": "LOW"},
    "flashcards": {"lite": True, "max_tokens": 3072, "thinking": "MINIMAL"},
    "why_wrong": {"lite": True, "max_tokens": 1024, "thinking": "MINIMAL"},
}

# Called after every Gemini answer as usage_hook(feature, model, usage_dict).
# app.py points it at db.log_usage; the command-line tool leaves it empty.
usage_hook = None
# Answer cache: cache_get(key) -> data or None, cache_put(key, feature, data). app.py points
# them at the ai_cache table; without them nothing is cached.
cache_get = None
cache_put = None
# Called as before_call(feature) right before a real Gemini request (never for a cached answer).
# app.py uses it for the demo's daily limits; it may raise to stop the call.
before_call = None

# Prompts are kept short: each rule once, and one-line JSON shapes.
MCQ = ('{"question": str, "options": [4 str], "answer": index 0-3 of the correct option, '
       '"subtopic": str, "explanation": one line on why it is correct}')
CARD = '{"front": question or term, "back": 1-2 sentence answer, "subtopic": str}'


def lit(text):
    """Escape { } so a JSON shape can sit inside a str.format() template."""
    return text.replace("{", "{{").replace("}", "}}")


PROMPT = f"""You are a study assistant for a university student. From the study material, return JSON:
{{"subject": course name, "topic": short title, "notes": [8-12 bullets], "mcqs": [exactly 5 MCQ], "flashcards": [8-10 card]}}
MCQ = {MCQ}
card = {CARD}
Simple English, keep formulas. Questions test understanding, not copied lines. Cards cover key definitions, formulas and ideas."""

FLASHCARD_PROMPT = """{source}
Return JSON {{"flashcards": [8-10 NEW card]}}, card = """ + lit(CARD) + """
Cover key definitions, formulas and ideas; keep formulas. Don't repeat or rephrase these:
{existing}
{notes}"""

MCQ_PROMPT = """{source}
Return JSON {{"mcqs": [5 NEW MCQ]}}, MCQ = """ + lit(MCQ) + """
Test understanding, not copied lines. Don't repeat or rephrase these:
{existing}
{notes}"""

EXPLAIN_PROMPT = """Explain "{subtopic}" ({subject}) to a university student, using the material below.
Clear, simple English: short sentences, everyday words, define every technical term.
Return JSON {{"explanation": [2-4 short paragraphs], "example": {{"problem": str, "solution": step-by-step str}}, "key_points": [exactly 3 str]}}
{notes}"""

PRACTICE_PROMPT = """Write {total} NEW MCQs for a university student who is weak in these topics (questions per topic):
{plan}
Return JSON {{"mcqs": [MCQ]}}, MCQ = """ + lit(MCQ) + """
"subtopic" is one of the topics above, spelled the same. Simple English; test understanding.
Don't repeat or rephrase these:
{existing}
{notes}"""

WHY_PROMPT = """A student got this MCQ wrong.
Question: {question}
{options}
Student's choice: {chosen}
Correct answer: {answer}
In 3-4 short sentences of clear, simple English, say why their choice is wrong and why the correct one is right.
JSON: {{"why": str}}"""


class GenerationError(Exception):
    """Raised when no model could produce a result."""


class BadResponse(ValueError):
    """The model answered, but not in the shape we asked for."""


def unwrap(data):
    """Gemini sometimes wraps the object in a list: [ {...} ]. Take the dict out."""
    if isinstance(data, list) and len(data) == 1 and isinstance(data[0], dict):
        return data[0]
    return data


def check_list(data, key):
    if not isinstance(data.get(key), list):
        raise BadResponse(f'"{key}" missing or not a list')


def validate_notes(data):
    """Raise BadResponse unless data looks like the main PROMPT's JSON."""
    if not isinstance(data, dict):
        raise BadResponse(f"expected an object, got {type(data).__name__}")
    if not isinstance(data.get("topic"), str):
        raise BadResponse('"topic" missing or not text')
    for key in ("notes", "mcqs", "flashcards"):
        check_list(data, key)
    check_mcqs(data["mcqs"])


def validate_mcqs(data):
    """Raise BadResponse unless data looks like MCQ_PROMPT's JSON."""
    if not isinstance(data, dict):
        raise BadResponse(f"expected an object, got {type(data).__name__}")
    check_list(data, "mcqs")
    check_mcqs(data["mcqs"])


def check_mcqs(mcqs):
    for q in mcqs:
        ok = (
            isinstance(q, dict)
            and isinstance(q.get("question"), str)
            and isinstance(q.get("options"), list)
            and len(q["options"]) >= 2
            and isinstance(q.get("answer"), int)
            and 0 <= q["answer"] < len(q["options"])
        )
        if not ok:
            raise BadResponse("an MCQ is missing question/options/answer")


def validate_flashcards(data):
    """Raise BadResponse unless data looks like FLASHCARD_PROMPT's JSON."""
    if not isinstance(data, dict):
        raise BadResponse(f"expected an object, got {type(data).__name__}")
    check_list(data, "flashcards")


def shuffle_options(mcqs, rng=random):
    """Shuffle each question's options and move "answer" to the new index.

    Models tend to put the correct answer in the same slot (often A),
    so we mix the options ourselves. Returns new dicts; the input is not changed.
    """
    shuffled = []
    for q in mcqs:
        order = list(range(len(q["options"])))
        rng.shuffle(order)  # order[new_index] = old_index
        shuffled.append({
            **q,
            "options": [q["options"][i] for i in order],
            "answer": order.index(q["answer"]),
        })
    return shuffled


def make_client():
    if not API_KEY:
        raise GenerationError("GEMINI_API_KEY missing. Put it in a .env file.")
    return genai.Client(api_key=API_KEY)


def list_models(client):
    return [
        m.name
        for m in client.models.list()
        if "generateContent" in (m.supported_actions or [])
    ]


def read_usage(response):
    """Token counts from a Gemini response (0 when the SDK doesn't report one)."""
    meta = getattr(response, "usage_metadata", None)

    def count(name):
        return getattr(meta, name, None) or 0
    return {
        "prompt_tokens": count("prompt_token_count"),
        "output_tokens": count("candidates_token_count"),
        "thinking_tokens": count("thoughts_token_count"),
        "total_tokens": count("total_token_count"),
    }


def log_usage(feature, model, response):
    """Hand the token counts to usage_hook. Logging must never break a request."""
    if usage_hook is None:
        return
    try:
        usage_hook(feature, model, read_usage(response))
    except Exception as e:
        print(f"  Couldn't log token usage: {e}")


def config_for(feature, thinking=True):
    """JSON output, plus the feature's output limit and thinking level from TASKS."""
    task = TASKS.get(feature, {})
    config = {"response_mime_type": "application/json", "max_output_tokens": task.get("max_tokens")}
    if thinking and task.get("thinking"):
        config["thinking_config"] = types.ThinkingConfig(thinking_level=task["thinking"])
    return types.GenerateContentConfig(**config)


def models_for(feature):
    """Small tasks try the lite model first; every task can fall back to the others."""
    models = ([LITE_MODEL] if TASKS.get(feature, {}).get("lite") else []) + [MODEL] + FALLBACKS
    return list(dict.fromkeys(models))  # drop repeats, keep order


def ask(client, model, contents, validate, feature="generate"):
    try:
        response = client.models.generate_content(model=model, contents=contents, config=config_for(feature))
    except errors.ClientError as e:
        if "thinking" not in str(e.message or e.details or "").lower():
            raise
        print(f"  {model} doesn't accept the thinking setting, asking without it.")
        response = client.models.generate_content(
            model=model, contents=contents, config=config_for(feature, thinking=False))
    log_usage(feature, model, response)  # before checking: a bad answer still cost tokens
    try:
        data = unwrap(json.loads(response.text))
    except json.JSONDecodeError:
        # MAX_TOKENS here means the answer was cut off: the feature's max_tokens is too small.
        candidates = getattr(response, "candidates", None) or [None]
        print(f"  Finish reason: {getattr(candidates[0], 'finish_reason', 'unknown')}")
        raise
    validate(data)  # raises BadResponse if the shape is wrong
    return data


def describe(e):
    """Readable text for a Gemini API error: code, status and the actual message."""
    return f"{e.code} {e.status or ''}: {e.message or e.details}".replace(" :", ":")


def is_file_error(e):
    """True if a ClientError is about the attached file, not the model.

    A 404 can mean "file not found" as well as "model not found", so we look at the message.
    Trying another model won't help with a bad file.
    """
    text = str(e.message or e.details or "").lower()
    return any(word in text for word in ("file", "document", "mime", "pdf", "page"))


def ask_with_fallback(client, contents, validate, feature="generate"):
    """Try the main model, then each fallback, with retries.

    A bad answer (broken JSON or wrong shape) is retried.
    A file error stops right away. Raises GenerationError if everything fails.
    """
    if before_call is not None:
        before_call(feature)
    for model in models_for(feature):
        for attempt in range(1, RETRIES + 1):
            try:
                print(f"Asking {model} (try {attempt})...")
                return ask(client, model, contents, validate, feature)
            except errors.ServerError:
                print("  Server busy.")
                time.sleep(attempt * 3)
            except errors.ClientError as e:
                print(f"  {model} failed: {describe(e)}")
                if is_file_error(e):
                    raise GenerationError(
                        f"Gemini couldn't read the uploaded file ({e.message or e.code}). "
                        "Check that it's a valid, non-empty PDF."
                    ) from e
                break
            except json.JSONDecodeError:
                print("  Bad JSON from model, retrying.")
            except BadResponse as e:
                print(f"  Unexpected answer shape ({e}), retrying.")

    raise GenerationError(
        "The AI couldn't produce usable notes for this file. Please try again in a minute."
    )


def cache_key(feature, request):
    """Same feature + same input = same key, so an identical request never calls Gemini twice."""
    return hashlib.sha256(f"{feature}\n{request}".encode("utf-8")).hexdigest()


def cached_ask(client, contents, validate, feature, request, fresh=False):
    """ask_with_fallback, but answered from the cache when the same request was made before.

    request: text that identifies the input (usually the prompt). fresh=True skips the lookup
    (for "Regenerate") and stores the new answer.
    """
    if not fresh:
        data = from_cache(feature, request, validate)
        if data is not None:
            return data
    data = ask_with_fallback(client, contents, validate, feature)
    if cache_put is not None:
        cache_put(cache_key(feature, request), feature, data)
    return data


def from_cache(feature, request, validate):
    """The cached answer to this request, or None."""
    data = cache_get(cache_key(feature, request)) if cache_get is not None else None
    if data is None:
        return None
    try:
        validate(data)
    except BadResponse:
        return None  # an old answer in a shape we no longer accept: ask again
    print(f"  {feature}: answered from cache, no tokens used.")
    return data


def upload_file(client, file_path, content=None):
    """Upload a file to Gemini: the given bytes if we have them, else read file_path."""
    if before_call is not None:
        before_call("upload")
    file_path = Path(file_path)
    try:
        if content is None:
            print(f"Uploading {file_path.name}...")
            return client.files.upload(file=file_path)
        print(f"Uploading {file_path.name} ({len(content)} bytes)...")
        return client.files.upload(
            file=io.BytesIO(content),
            config=types.UploadFileConfig(mime_type="application/pdf", display_name=file_path.name),
        )
    except errors.ClientError as e:
        print(f"  Upload failed: {describe(e)}")
        raise GenerationError(f"Couldn't upload the file to Gemini ({e.message or e.code}).") from e


def compact(text):
    """Squeeze runs of spaces and blank lines: same words, fewer tokens."""
    lines = (re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines())
    return "\n".join(line for line in lines if line)


PAGE_NUMBER = re.compile(r"^(page\s*)?\d+(\s*(of|/)\s*\d+)?$", re.IGNORECASE)


def drop_headers(pages):
    """Remove page headers/footers: lines found on at least half the pages (3 or more),
    and page numbers like "Page 3 of 16". They'd be paid for once per page otherwise."""
    page_lines = [compact(p).splitlines() for p in pages]
    seen_on = Counter(line for lines in page_lines for line in set(lines))
    limit = max(3, len(pages) / 2)
    return ["\n".join(line for line in lines if seen_on[line] < limit and not PAGE_NUMBER.match(line))
            for lines in page_lines]


def has_images(page):
    """True if the page shows an embedded image. If pypdf can't tell, assume yes (send the PDF)."""
    try:
        return len(page.images) > 0
    except Exception:
        return True


def read_pdf(content):
    """Read a PDF (bytes) once at upload.

    Returns {"text", "pages", "chars_per_page", "has_images"}. "text" is "" when pypdf can't
    read the file or there is too little text to be useful (a scanned PDF).
    """
    try:
        reader = PdfReader(io.BytesIO(content))
        pages = [page.extract_text() or "" for page in reader.pages]
        images = any(has_images(page) for page in reader.pages)
    except Exception as e:
        print(f"  Couldn't read the PDF: {e}")
        return {"text": "", "pages": 0, "chars_per_page": 0, "has_images": False}
    text = compact("\n".join(drop_headers(pages)))
    chars_per_page = len(text) / len(pages) if pages else 0
    if chars_per_page < MIN_CHARS_PER_PAGE:
        text = ""
    return {"text": text[:MAX_TEXT_CHARS], "pages": len(pages),
            "chars_per_page": round(chars_per_page), "has_images": images}


def extract_text(content):
    """The PDF's usable text, or "" (then later calls send the PDF itself)."""
    return read_pdf(content)["text"]


def text_for_first_generate(info):
    """What the first generate sends, from read_pdf()'s result: the text, or "" for the PDF itself.

    The PDF is sent when it has embedded images (diagrams, picture slides) or less than
    TEXT_ONLY_CHARS_PER_PAGE characters of text per page on average; extracted text would miss too much.
    """
    if info["has_images"]:
        reason = "it has embedded images"
    elif info["chars_per_page"] < TEXT_ONLY_CHARS_PER_PAGE:
        reason = f"only {info['chars_per_page']} characters of text per page"
    else:
        print(f"  First generate: sending extracted text ({info['chars_per_page']} characters per page, no images).")
        return info["text"]
    print(f"  First generate: sending the PDF, {reason}.")
    return ""


def material(text):
    return f"\nStudy material:\n{text}"


def generate(client, file_path, content=None, text=""):
    """Return the notes/MCQs/flashcards dict for one PDF.

    text: the PDF's extracted text. If there is any, only that is sent;
    otherwise the file is uploaded (content = its bytes if already in memory, else file_path is read).
    """
    if text:
        prompt = PROMPT + material(text)
        return cached_ask(client, [prompt], validate_notes, "generate", prompt)
    if content is None and Path(file_path).is_file():
        content = Path(file_path).read_bytes()
    if not content:
        return ask_with_fallback(client, [upload_file(client, file_path), PROMPT], validate_notes)
    # A scanned PDF: same bytes = same request, so the cache key uses the file's hash.
    request = f"pdf:{hashlib.sha256(content).hexdigest()}\n{PROMPT}"
    cached = from_cache("generate", request, validate_notes)
    if cached is not None:
        return cached  # not even uploaded
    uploaded = upload_file(client, file_path, content)
    return cached_ask(client, [uploaded, PROMPT], validate_notes, "generate", request, fresh=True)


def normalize(text):
    """Lowercase, drop punctuation and extra spaces: for spotting duplicates."""
    return " ".join(re.sub(r"[^\w\s]", " ", str(text).lower()).split())


def only_new(items, existing, key):
    """Keep items whose item[key] isn't already in existing (or earlier in items)."""
    seen = {normalize(e) for e in existing}
    fresh = []
    for item in items:
        text = normalize(item.get(key, "")) if isinstance(item, dict) else ""
        if text and text not in seen:
            seen.add(text)
            fresh.append(item)
    return fresh


def ask_more(client, template, validate, feature, subject, topic, notes, existing, pdf_path, text):
    """Ask for more items: from the PDF's saved text, else the PDF file, else the notes."""
    about = f'"{topic or "untitled"}" ({subject or "unknown subject"})'
    if text:
        source = f"Use the study material below on {about}."
        notes_block = material(text)
        contents = []
    elif pdf_path and Path(pdf_path).exists():
        source = f"Use the attached study material on {about}."
        notes_block = ""
        contents = [upload_file(client, pdf_path)]
    else:
        source = f"Use the study notes below on {about}."
        notes_block = "\nNotes:\n" + "\n".join(f"- {n}" for n in notes)
        contents = []
    prompt = template.format(
        source=source,
        existing="\n".join(f"- {e}" for e in existing) or "- (none yet)",
        notes=notes_block,
    )
    return ask_with_fallback(client, contents + [prompt], validate, feature)


def generate_flashcards(client, subject, topic, notes, existing=(), pdf_path=None, text=""):
    """Make new flashcards that don't repeat the existing fronts."""
    data = ask_more(client, FLASHCARD_PROMPT, validate_flashcards, "flashcards",
                    subject, topic, notes, existing, pdf_path, text)
    return only_new(data["flashcards"], existing, "front")


def generate_mcqs(client, subject, topic, notes, existing=(), pdf_path=None, text=""):
    """Make new MCQs (options shuffled) that don't repeat the existing questions."""
    data = ask_more(client, MCQ_PROMPT, validate_mcqs, "more_mcqs",
                    subject, topic, notes, existing, pdf_path, text)
    return shuffle_options(only_new(data["mcqs"], existing, "question"))


# ---------- AI tutor ----------

def is_text_list(value, min_len=1):
    return isinstance(value, list) and len(value) >= min_len and all(isinstance(v, str) and v.strip() for v in value)


def validate_explanation(data):
    """Raise BadResponse unless data looks like EXPLAIN_PROMPT's JSON."""
    if not isinstance(data, dict):
        raise BadResponse(f"expected an object, got {type(data).__name__}")
    if not is_text_list(data.get("explanation")):
        raise BadResponse('"explanation" missing or not a list of text')
    example = data.get("example")
    if not (isinstance(example, dict) and isinstance(example.get("problem"), str)
            and isinstance(example.get("solution"), str)):
        raise BadResponse('"example" needs "problem" and "solution"')
    if not is_text_list(data.get("key_points"), 3):
        raise BadResponse('"key_points" needs 3 points')


def validate_why(data):
    if not (isinstance(data, dict) and isinstance(data.get("why"), str) and data["why"].strip()):
        raise BadResponse('"why" missing or empty')


def context_text(notes, chunks=()):
    """Note bullets, then the matching excerpts of the PDF text."""
    text = "\nNotes:\n" + ("\n".join(f"- {n}" for n in notes) or "- (no notes saved)")
    if chunks:
        text += "\nFrom the lecture material:\n" + "\n---\n".join(chunks)
    return text


def explain_topic(client, subject, subtopic, notes, chunks=(), fresh=False):
    """Simple explanation, one worked example and 3 key points for one subtopic.

    notes / chunks: only the note bullets and PDF text pieces about this subtopic.
    """
    prompt = EXPLAIN_PROMPT.format(subject=subject, subtopic=subtopic, notes=context_text(notes, chunks))
    data = cached_ask(client, [prompt], validate_explanation, "explain", prompt, fresh)
    return {
        "explanation": data["explanation"],
        "example": {"problem": data["example"]["problem"], "solution": data["example"]["solution"]},
        "key_points": data["key_points"][:3],
    }


def generate_practice(client, subject, plan, notes, existing=(), chunks=()):
    """New MCQs for weak subtopics. plan: [(subtopic, how_many)], weakest first.

    Questions whose subtopic isn't one of the planned ones are dropped;
    the rest get the planned spelling, so their answers count for the right topic.
    """
    names = {topic.casefold(): topic for topic, _ in plan}

    def validate(data):
        validate_mcqs(data)
        if not any(str(q.get("subtopic", "")).casefold() in names for q in data["mcqs"]):
            raise BadResponse("no question uses one of the requested subtopics")

    prompt = PRACTICE_PROMPT.format(
        total=sum(n for _, n in plan),
        plan="\n".join(f"- {topic}: {n}" for topic, n in plan),
        existing="\n".join(f"- {e}" for e in existing) or "- (none yet)",
        notes=context_text(notes, chunks),
    )
    data = ask_with_fallback(client, [prompt], validate, "practice")
    mcqs = [
        {**q, "subtopic": names[str(q.get("subtopic", "")).casefold()]}
        for q in data["mcqs"]
        if str(q.get("subtopic", "")).casefold() in names
    ]
    return shuffle_options(only_new(mcqs, existing, "question"))


def why_wrong(client, question, options, chosen, answer):
    """3-4 lines on why `chosen` is wrong and `answer` is right (both option indexes)."""
    letter = "ABCDEFGH"
    prompt = WHY_PROMPT.format(
        question=question,
        options="\n".join(f"{letter[i]}) {o}" for i, o in enumerate(options)),
        chosen=f"{letter[chosen]}) {options[chosen]}",
        answer=f"{letter[answer]}) {options[answer]}",
    )
    return cached_ask(client, [prompt], validate_why, "why_wrong", prompt)["why"].strip()
