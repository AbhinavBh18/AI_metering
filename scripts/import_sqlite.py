"""Run once with the old API stopped and an empty PostgreSQL target."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.legacy_import import import_sqlite
from app.postgres import Database
from config import Settings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source", help="Existing SQLite database; opened read-only")
    args = parser.parse_args()
    settings = Settings.from_env()
    db = Database(settings.database_url)
    db.migrate()
    print(json.dumps(import_sqlite(args.source, db), indent=2))


if __name__ == "__main__":
    main()
