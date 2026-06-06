from app.gmail_reader import build_newsletter_query, fetch_recent_newsletters


def main() -> None:
    query = build_newsletter_query(days=7)
    print("Query:")
    print(query)

    emails = fetch_recent_newsletters(max_results=20, days=7)

    print(f"\nFetched {len(emails)} approved newsletter email(s).")

    for email in emails:
        print("\n---")
        print("From:", email["sender"])
        print("Subject:", email["subject"])
        print(email["text"][:700])


if __name__ == "__main__":
    main()
