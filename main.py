import json
import sys
from pathlib import Path

from study_ai import GenerationError, generate, list_models, make_client, read_pdf, text_for_first_generate

OUTPUT_DIR = Path("output")


def save(data, file_path):
    OUTPUT_DIR.mkdir(exist_ok=True)
    out = OUTPUT_DIR / f"{file_path.stem}.json"
    out.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Saved to {out}")


def show_notes(data):
    print(f"\n=== {data['topic']} ===\n")
    for note in data["notes"]:
        print(f"- {note}")


def quiz(data):
    letters = "ABCD"
    score = 0
    print("\n=== Quiz ===")
    for i, q in enumerate(data["mcqs"], 1):
        print(f"\nQ{i}. {q['question']}")
        for letter, option in zip(letters, q["options"]):
            print(f"  {letter}) {option}")

        choice = input("Your answer: ").strip().upper()
        correct = letters[q["answer"]]
        if choice == correct:
            score += 1
            print("Correct!")
        else:
            print(f"Wrong. Answer: {correct}")
        print(f"  -> {q['explanation']}")

    print(f"\nScore: {score}/{len(data['mcqs'])}")


def main():
    if len(sys.argv) < 2:
        sys.exit("Usage: python main.py <file.pdf|image>   or   python main.py --models")

    try:
        client = make_client()

        if sys.argv[1] == "--models":
            for name in list_models(client):
                print(name)
            return

        file_path = Path(sys.argv[1])
        if not file_path.exists():
            sys.exit(f"File not found: {file_path}")

        content = file_path.read_bytes()
        data = generate(client, file_path, content, text_for_first_generate(read_pdf(content)))
    except GenerationError as e:
        sys.exit(str(e))

    save(data, file_path)
    show_notes(data)
    quiz(data)


if __name__ == "__main__":
    main()
