"""Process a bounded number of pending push notifications."""
from app.db import SessionLocal
from app.services.push import dispatch_pending


def main() -> None:
    with SessionLocal.begin() as session:
        result = dispatch_pending(session)
    print(f"Push attempts: {result['sent']} sent, {result['failed']} failed")


if __name__ == "__main__":
    main()
