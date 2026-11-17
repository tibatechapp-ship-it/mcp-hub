"""
Backup and restore for the catalog database (v1.3.1 P2.1).

`cp` on a live SQLite file is unsafe: a copy taken mid-transaction can be
torn. This module uses SQLite's online backup API, which produces a consistent
snapshot of a database that is being written to, then records a SHA-256 sidecar
so a restore can prove it is putting back the exact bytes that were saved.

Layout: ``<path>`` (the database) and ``<path>.sha256`` (``<hex>  <filename>``).
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

__all__ = ["backup", "restore", "verify_backup", "BackupError"]


class BackupError(RuntimeError):
    """Raised when a backup or restore cannot be completed safely."""


def _checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _sidecar(path: Path) -> Path:
    return path.with_name(path.name + ".sha256")


def backup(db_path: str | Path, dest: str | Path) -> dict[str, object]:
    """
    Copy the live database to `dest` using the SQLite online backup API.

    Returns a summary dict (path, bytes, sha256). Never overwrites an existing
    destination file — a backup that silently replaces the previous one is how
    people lose the copy they needed.
    """
    source = Path(db_path)
    target = Path(dest)
    if not source.exists():
        raise BackupError(f"database not found: {source}")
    if target.exists():
        raise BackupError(f"refusing to overwrite existing file: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)

    src = sqlite3.connect(str(source))
    try:
        dst = sqlite3.connect(str(target))
        try:
            src.backup(dst)
            dst.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            dst.close()
    finally:
        src.close()

    checksum = _checksum(target)
    _sidecar(target).write_text(f"{checksum}  {target.name}\n", encoding="utf-8")
    return {"path": str(target), "bytes": target.stat().st_size, "sha256": checksum}


def verify_backup(path: str | Path) -> str:
    """Return the recorded checksum, raising if it does not match the file."""
    target = Path(path)
    if not target.exists():
        raise BackupError(f"backup not found: {target}")
    sidecar = _sidecar(target)
    if not sidecar.exists():
        raise BackupError(f"checksum sidecar missing: {sidecar}")
    recorded = sidecar.read_text(encoding="utf-8").split()[0].strip()
    actual = _checksum(target)
    if recorded != actual:
        raise BackupError(f"checksum mismatch: recorded {recorded[:12]}…, file is {actual[:12]}…")
    return recorded


def restore(backup_path: str | Path, db_path: str | Path, force: bool = False) -> dict[str, object]:
    """
    Replace `db_path` with the backup, after verifying its checksum.

    The current database is moved aside to ``<db>.pre-restore`` instead of
    being deleted — if the restore turns out to be the wrong file, the previous
    state is one `mv` away.
    """
    source = Path(backup_path)
    target = Path(db_path)
    checksum = verify_backup(source)
    target.parent.mkdir(parents=True, exist_ok=True)

    previous: Path | None = None
    if target.exists():
        if not force:
            raise BackupError(
                f"{target} already exists; pass force=True to replace it "
                f"(the current file is kept as {target}.pre-restore)"
            )
        previous = target.with_name(target.name + ".pre-restore")
        if previous.exists():
            previous.unlink()
        target.replace(previous)

    # Copy rather than move: the backup stays usable for a second attempt.
    with source.open("rb") as src, target.open("wb") as dst:
        for block in iter(lambda: src.read(1 << 20), b""):
            dst.write(block)

    if _checksum(target) != checksum:
        if previous is not None:
            target.unlink(missing_ok=True)
            previous.replace(target)
        raise BackupError("restored file does not match the backup checksum; rolled back")

    return {
        "path": str(target),
        "bytes": target.stat().st_size,
        "sha256": checksum,
        "previous": str(previous) if previous else None,
    }
