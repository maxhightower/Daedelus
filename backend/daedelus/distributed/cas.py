"""Content-addressed blob store and directory manifests.

Blobs are immutable files named by their sha256. Writing verifies the hash before the blob
becomes visible (write to a temp name, fsync, rename), so a truncated or tampered upload can
never be served. Materialising a manifest builds the whole tree in a staging directory and
swaps it in with renames: readers see the old tree or the new one, never a mix.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from pathlib import Path

from .models import FileEntry, Manifest

SHA_RE_LEN = 64


class BlobError(Exception):
    pass


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def valid_sha(s: str) -> bool:
    return len(s) == SHA_RE_LEN and all(c in "0123456789abcdef" for c in s)


class BlobStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, sha: str) -> Path:
        if not valid_sha(sha):
            raise BlobError(f"invalid blob id {sha!r}")
        return self.root / sha[:2] / sha

    def has(self, sha: str) -> bool:
        return valid_sha(sha) and self.path(sha).exists()

    def put_bytes(self, data: bytes, expected: str | None = None) -> str:
        sha = hashlib.sha256(data).hexdigest()
        if expected and sha != expected:
            raise BlobError(f"hash mismatch: expected {expected}, got {sha}")
        dst = self.path(sha)
        if dst.exists():
            return sha
        dst.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=dst.parent, prefix=".up_")
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, dst)
        return sha

    def put_file(self, p: Path) -> str:
        sha = sha256_file(p)
        dst = self.path(sha)
        if not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=dst.parent, prefix=".up_")
            os.close(fd)
            shutil.copyfile(p, tmp)
            if sha256_file(Path(tmp)) != sha:  # file changed while copying
                os.unlink(tmp)
                raise BlobError(f"{p} changed while being stored")
            os.replace(tmp, dst)
        return sha

    def read(self, sha: str) -> bytes:
        p = self.path(sha)
        if not p.exists():
            raise BlobError(f"missing blob {sha}")
        return p.read_bytes()


def _safe_rel(rel: str) -> str:
    p = Path(rel)
    if p.is_absolute() or ".." in p.parts or rel.startswith(("/", "\\")) or not rel:
        raise BlobError(f"unsafe path in manifest: {rel!r}")
    return p.as_posix()


def snapshot(dir_: Path, store: BlobStore | None = None) -> Manifest:
    """Manifest of a directory (symlinks are refused); blobs are stored when ``store`` given."""
    m = Manifest()
    dir_ = Path(dir_)
    if not dir_.exists():
        return m
    for p in sorted(dir_.rglob("*")):
        if p.is_symlink():
            raise BlobError(f"symlinks are not allowed in artifact trees: {p}")
        if p.is_dir():
            continue
        rel = p.relative_to(dir_).as_posix()
        sha = store.put_file(p) if store else sha256_file(p)
        m.files[rel] = FileEntry(sha256=sha, size=p.stat().st_size,
                                 mode=0o755 if os.access(p, os.X_OK) else 0o644)
    return m


def materialize(m: Manifest, store: BlobStore, dst: Path) -> None:
    """Write a manifest into a new directory ``dst`` (must not exist), verifying every blob."""
    dst = Path(dst)
    if dst.exists():
        raise BlobError(f"{dst} already exists")
    tmp = Path(tempfile.mkdtemp(prefix=".mat_", dir=dst.parent))
    try:
        for rel, fe in m.files.items():
            out = tmp / _safe_rel(rel)
            out.parent.mkdir(parents=True, exist_ok=True)
            src = store.path(fe.sha256)
            if not src.exists():
                raise BlobError(f"missing blob {fe.sha256} for {rel}")
            shutil.copyfile(src, out)
            if sha256_file(out) != fe.sha256:
                raise BlobError(f"corrupt blob {fe.sha256} for {rel}")
            os.chmod(out, fe.mode)
        os.replace(tmp, dst)
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise


def swap_in(m: Manifest, store: BlobStore, target: Path) -> None:
    """Atomically replace ``target`` with the manifest's tree (rename-swap on one filesystem)."""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.parent / f".{target.name}.new"
    old = target.parent / f".{target.name}.old"
    shutil.rmtree(staging, ignore_errors=True)
    shutil.rmtree(old, ignore_errors=True)
    materialize(m, store, staging)
    if target.exists():
        os.replace(target, old)
    os.replace(staging, target)
    shutil.rmtree(old, ignore_errors=True)
