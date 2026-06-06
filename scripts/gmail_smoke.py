from app.gmail_reader import fetch_recent_emails


def main() -> None:
    emails = fetch_recent_emails(
        max_results=5,
        query="newer_than:30d -in:spam -in:trash subject:TLDR",
    )

    print(f"Fetched {len(emails)} emails.")

    for email in emails:
        print("\n---")
        print("From:", email["sender"])
        print("Subject:", email["subject"])
        print(email["text"][:500])


if __name__ == "__main__":
    main()
