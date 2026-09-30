import os
from pathlib import Path

from dotenv import load_dotenv
from google.cloud import firestore


BASE_DIR = Path(__file__).resolve().parent


def get_firestore_client():
    """Return an authenticated client for the configured Firestore database."""
    load_dotenv(BASE_DIR / ".env")

    project_id = os.getenv("FIRESTORE_PROJECT_ID")
    database_id = os.getenv("FIRESTORE_DATABASE_ID", "(default)")

    if not project_id:
        raise ValueError("FIRESTORE_PROJECT_ID is missing from .env")

    return firestore.Client(project=project_id, database=database_id)


if __name__ == "__main__":
    db = get_firestore_client()
    # Listing collections is a non-destructive connection and permission check.
    list(db.collections())
    print(f"Connected to Firestore project {db.project} successfully.")
