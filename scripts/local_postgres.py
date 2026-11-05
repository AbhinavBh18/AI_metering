"""Optional Windows/native PostgreSQL bootstrap, isolated to this repository.

Uses existing PostgreSQL binaries; installs no service and changes no system DB.
Credentials are generated into git-ignored .env. Existing clusters are preserved.
"""
import argparse
import os
from pathlib import Path
import secrets
import subprocess
import sys
from urllib.parse import urlsplit

import psycopg
from psycopg import sql
from dotenv import dotenv_values, set_key

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pg-bin", default=r"C:\Program Files\PostgreSQL\18\bin")
    parser.add_argument("--port", type=int, default=55432)
    parser.add_argument("--stop", action="store_true")
    args = parser.parse_args()
    pg_bin = Path(args.pg_bin)
    suffix = ".exe" if os.name == "nt" else ""
    cluster = ROOT / "data" / "postgresql"
    log = ROOT / "data" / "postgresql.log"
    env_file = ROOT / ".env"
    flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    def run(binary, *arguments, **kwargs):
        return subprocess.run([str(pg_bin / (binary + suffix)), *map(str, arguments)], **flags, **kwargs)
    if args.stop:
        if not (cluster / "PG_VERSION").exists():
            raise SystemExit("No project-local PostgreSQL cluster exists.")
        run("pg_ctl", "-D", cluster, "stop", "-m", "fast", check=True)
        return
    values = dotenv_values(env_file)
    existing_url = values.get("DATABASE_URL")
    if existing_url:
        parsed = urlsplit(existing_url)
        if parsed.hostname != "127.0.0.1" or parsed.port != args.port or parsed.username != "metering":
            raise SystemExit(".env already points to a different database. Leave it intact and use that server, or configure this helper explicitly.")
    elif cluster.exists():
        raise SystemExit("Existing cluster has no matching .env configuration; refusing to overwrite it.")
    else:
        password = secrets.token_hex(24)
        existing_url = f"postgresql://metering:{password}@127.0.0.1:{args.port}/metering"
        if not env_file.exists():
            env_file.write_text((ROOT / ".env.example").read_text(encoding="utf-8"), encoding="utf-8")
        set_key(str(env_file), "DATABASE_URL", existing_url)
        set_key(str(env_file), "TEST_DATABASE_URL", existing_url + "_test")
    if not (cluster / "PG_VERSION").exists():
        cluster.parent.mkdir(parents=True, exist_ok=True)
        password_file = cluster.parent / "postgres-bootstrap-password.tmp"
        password_file.write_text(urlsplit(existing_url).password + "\n", encoding="utf-8")
        try:
            run("initdb", "-D", cluster, "-U", "metering", "--pwfile", password_file,
                "--auth=scram-sha-256", "--encoding=UTF8", "--locale=C", check=True)
        finally:
            password_file.unlink(missing_ok=True)
    status = run("pg_ctl", "-D", cluster, "status", capture_output=True)
    if status.returncode != 0:
        run("pg_ctl", "-D", cluster, "-l", log, "-o", f"-h 127.0.0.1 -p {args.port}", "-w", "start", check=True)
    admin_url = existing_url.rsplit("/", 1)[0] + "/postgres"
    with psycopg.connect(admin_url, autocommit=True) as conn:
        for name in ("metering", "metering_test"):
            if not conn.execute("SELECT 1 FROM pg_database WHERE datname=%s", (name,)).fetchone():
                conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    print(f"Project PostgreSQL ready on 127.0.0.1:{args.port}. Credentials saved only in .env.")


if __name__ == "__main__":
    main()
