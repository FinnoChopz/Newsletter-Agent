from pathlib import Path

import yaml

from app.gmail_reader import discover_newsletters, classify_sender_with_model


AUTO_REJECT_CLASSES = {
    "promotional_marketing",
    "transactional",
    "personal",
    "spam_or_low_value",
}

AUTO_APPROVE_MIN_CONFIDENCE = 0.92


def ask_yes_no(prompt: str, default: bool = False) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"

    while True:
        answer = input(f"{prompt} {suffix} ").strip().lower()

        if not answer:
            return default

        if answer in ["y", "yes"]:
            return True

        if answer in ["n", "no"]:
            return False

        print("Please type y or n.")


def main():
    candidates = discover_newsletters(days=30, max_results=300)

    approved_sources = []
    rejected_sources = []
    review_sources = []

    print("\nClassifying candidates...\n")

    for candidate in candidates:
        try:
            result = classify_sender_with_model(candidate)
        except Exception as e:
            print("\n---")
            print(candidate["sender"])
            print("FAILED TO CLASSIFY:", e)
            continue

        source_record = {
            "name": result["suggested_name"],
            "senders": [candidate["sender"]],
            "enabled": True,
            "classification": result["classification"],
            "confidence": result["confidence"],
            "reason": result["reason"],
            "count": candidate["count"],
            "example_subjects": candidate["example_subjects"],
        }

        classification = result["classification"]
        confidence = result["confidence"]

        # Hard reject obvious non-newsletters.
        if classification in AUTO_REJECT_CLASSES and confidence >= 0.8:
            rejected_sources.append(source_record)
            continue

        # Auto-approve extremely confident real newsletters.
        if (
            classification == "newsletter"
            and result["should_include"]
            and confidence >= AUTO_APPROVE_MIN_CONFIDENCE
        ):
            approved_sources.append(source_record)
            continue

        # Everything else goes to human review.
        review_sources.append(source_record)

    print("\nAuto-approved sources:")
    if approved_sources:
        for source in approved_sources:
            print(f"- {source['name']} <{source['senders'][0]}>")
    else:
        print("- None")

    print("\nNeeds review:")
    final_sources = approved_sources[:]

    for idx, source in enumerate(review_sources, start=1):
        print("\n---")
        print(f"{idx}. {source['name']}")
        print(f"Sender: {source['senders'][0]}")
        print(f"Class: {source['classification']} ({source['confidence']})")
        print(f"Count: {source['count']}")
        print("Examples:")
        for subject in source["example_subjects"]:
            print(f"- {subject}")
        print(f"Reason: {source['reason']}")

        include = ask_yes_no("Include this source?", default=False)

        if include:
            source["enabled"] = True
            final_sources.append(source)
        else:
            rejected_sources.append(source)

    output = {
        "sources": final_sources,
    }

    output_path = Path("data/newsletter_sources.generated.yaml")
    output_path.write_text(
        yaml.dump(output, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )

    rejected_path = Path("data/newsletter_sources.rejected.yaml")
    rejected_path.write_text(
        yaml.dump({"rejected": rejected_sources}, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )

    print("\nDone.")
    print(f"Saved {len(final_sources)} approved source(s) to {output_path}")
    print(f"Saved {len(rejected_sources)} rejected source(s) to {rejected_path}")


if __name__ == "__main__":
    main()