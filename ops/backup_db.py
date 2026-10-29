"""Daily pg_dump of the pipeline DB, keeping the last 7 daily dumps.

python -m ops.backup_db   (scheduled at 10:40, after the 10:00 pipeline)
Writes <BACKUP_DIR>/skroutz_daily_YYYYMMDD.dump (custom format), checks it with
pg_restore --list, then deletes only skroutz_daily_*.dump files older than KEEP_DAYS.
The password goes to pg_dump via PGPASSWORD in the child env and is never printed.
"""

from __future__ import annotations

import datetime as dt
import os
import re
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
BACKUP_DIR = Path(os.environ.get("BACKUP_DIR", ROOT.parent / "db_backups"))
PG_BIN = Path(os.environ.get("PG_BIN", r"C:\Program Files\PostgreSQL\17\bin"))
KEEP_DAYS = 7
NAME_RE = re.compile(r"^skroutz_daily_(\d{8})\.dump$")


def prune(folder: Path, today: dt.date, keep: int = KEEP_DAYS) -> list[Path]:
    """Delete skroutz_daily_YYYYMMDD.dump older than `keep` days. Other files are never touched."""
    removed = []
    for f in folder.iterdir():
        m = NAME_RE.match(f.name)
        if not m:
            continue
        day = dt.datetime.strptime(m.group(1), "%Y%m%d").date()
        if (today - day).days >= keep:
            f.unlink()
            removed.append(f)
    return removed


def _exe(name: str) -> str:
    p = PG_BIN / (name + (".exe" if os.name == "nt" else ""))
    return str(p) if p.exists() else name


def main() -> int:
    load_dotenv(ROOT / ".env")
    today = dt.date.today()
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    out = BACKUP_DIR / f"skroutz_daily_{today:%Y%m%d}.dump"
    env = dict(os.environ, PGPASSWORD=os.environ.get("DB_PASSWORD", ""))
    args = ["-h", os.environ.get("DB_HOST", "localhost"), "-p", os.environ.get("DB_PORT", "5432"),
            "-U", os.environ.get("DB_USER", "postgres")]
    db = os.environ.get("DB_NAME", "SkroutzPR")
    tmp = out.with_suffix(".tmp")
    r = subprocess.run([_exe("pg_dump"), *args, "-Fc", "-f", str(tmp), db], env=env,
                       capture_output=True, text=True)
    if r.returncode != 0:
        tmp.unlink(missing_ok=True)
        print("pg_dump failed:", r.stderr.strip(), file=sys.stderr)
        return 1
    check = subprocess.run([_exe("pg_restore"), "--list", str(tmp)], capture_output=True, text=True)
    if check.returncode != 0 or "TABLE DATA public price_snapshots" not in check.stdout:
        print("pg_restore --list check failed, dump kept as", tmp, file=sys.stderr)
        return 1
    tmp.replace(out)
    removed = prune(BACKUP_DIR, today)
    print(f"backup ok: {out} ({out.stat().st_size / 1e6:.1f} MB), pruned {len(removed)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
