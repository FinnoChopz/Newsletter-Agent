from app.gmail_reader import discover_newsletters

candidates = discover_newsletters(days=30, max_results=200)

print(f"Found {len(candidates)} likely newsletter sender(s).\n")

for i, c in enumerate(candidates, start=1):
    print(f"{i}. {c['sender']}")
    print(f"   Count: {c['count']}")
    print("   Examples:")
    for subject in c["example_subjects"]:
        print(f"   - {subject}")
    print()