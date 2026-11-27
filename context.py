"""Pick the parts of a subject's material that are about some topics, to keep prompts small.

No AI or database code here, so it's easy to test.
"""
import re

CHUNK_CHARS = 600
STOPWORDS = set("""
the and for are but not you all any can had her was one our out has his how its may new now
old see two way who did get got let put say she too use with that this from have they will
what when which where while your into than then them these those there their been were more
most some such only also each other about after before over under very just like does done
following true false correct statement statements question answer best given value example
""".split())


def keywords(text):
    """Lowercase word stems (first 6 letters), without short and very common words.

    "Relations" and "relation" both become "relati", so they match.
    """
    return [w[:6] for w in re.findall(r"[a-z0-9]+", str(text).lower()) if len(w) > 2 and w not in STOPWORDS]


def topic_words(subtopic, questions=()):
    """{stem: weight}: words of the topic name count 3, words of its questions count 1."""
    weights = {w: 1 for w in keywords(" ".join(questions))}
    weights.update({w: 3 for w in keywords(subtopic)})
    return weights


def split_chunks(text, size=CHUNK_CHARS):
    """Cut text into pieces of about `size` characters, at line breaks where possible."""
    chunks, current = [], ""
    for line in text.splitlines():
        while len(line) > size:  # one very long line: cut it
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:size])
            line = line[size:]
        if current and len(current) + 1 + len(line) > size:
            chunks.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current.strip():
        chunks.append(current)
    return chunks


def score(piece, weights):
    return sum(weights.get(w, 0) for w in keywords(piece))


def best(pieces, weights, max_chars):
    """The best-matching pieces that fit in max_chars, in their original order.

    Only pieces scoring at least half of the top score count, so a table of contents
    that mentions every topic once doesn't fill the space.
    """
    scores = [score(p, weights) for p in pieces]
    ranked = sorted(range(len(pieces)), key=lambda i: -scores[i])
    cutoff = max(1, max(scores, default=0) / 2)
    keep, used = [], 0
    for i in ranked:
        if scores[i] < cutoff:
            break
        if used + len(pieces[i]) <= max_chars:
            keep.append(i)
            used += len(pieces[i])
    return [pieces[i] for i in sorted(keep)]


def related_context(notes, topics, note_chars=1200, text_chars=1800):
    """Note bullets and PDF text chunks about the given topics.

    notes:  [{"notes": [bullets], "source_text": "..."}] for one subject.
    topics: [(subtopic, [question stems about it])]. The budget is shared between topics.
    Returns (bullets, chunks). If no bullet matches at all, the first bullets are used,
    so the model always has something to work from.
    """
    bullets = [b for n in notes for b in n["notes"]]
    chunks = [c for n in notes for c in split_chunks(n.get("source_text") or "")]
    picked_bullets, picked_chunks = [], []
    for subtopic, questions in topics:
        weights = topic_words(subtopic, questions)
        for b in best(bullets, weights, note_chars // len(topics)):
            if b not in picked_bullets:
                picked_bullets.append(b)
        for c in best(chunks, weights, text_chars // len(topics)):
            if c not in picked_chunks:
                picked_chunks.append(c)
    if not picked_bullets:
        for b in bullets:
            if sum(map(len, picked_bullets)) + len(b) > note_chars:
                break
            picked_bullets.append(b)
    return picked_bullets, picked_chunks


def least_covered(text, existing, max_chars):
    """The parts of a PDF's text that the existing questions/cards cover least, up to max_chars.

    Used for "Generate more": new items should come from new material, and the model
    doesn't need the parts that are already well covered. Short texts are returned whole.
    """
    if len(text) <= max_chars:
        return text
    covered = {w: 1 for w in keywords(" ".join(existing))}
    chunks = split_chunks(text)
    # Fewest already-covered words per character first; ties keep their order.
    ranked = sorted(range(len(chunks)), key=lambda i: score(chunks[i], covered) / max(len(chunks[i]), 1))
    keep, used = [], 0
    for i in ranked:
        if used + len(chunks[i]) <= max_chars:
            keep.append(i)
            used += len(chunks[i]) + 1
    return "\n".join(chunks[i] for i in sorted(keep))
