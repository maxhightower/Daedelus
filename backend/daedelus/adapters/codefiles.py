"""Source-code / text-file adapter backed by a git repository.

Components are files (``file:<path>``) and, for Python, top-level symbols
(``file:<path>::<name>``). Every applied plan becomes a git commit, so changes
are reviewable as ordinary diffs; tests run as a permission-checked subprocess.
"""

from __future__ import annotations

import ast
import difflib
import hashlib
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

from ..models import Component, PlannedOperation, ValidationReport
from .base import Adapter, AdapterError, AdapterInfo, ApplyResult, InspectResult, OperationSpec

ROOT_ID = "repo"
GIT_ENV = {
    "GIT_AUTHOR_NAME": "Daedelus", "GIT_AUTHOR_EMAIL": "daedelus@localhost",
    "GIT_COMMITTER_NAME": "Daedelus", "GIT_COMMITTER_EMAIL": "daedelus@localhost",
    "GIT_AUTHOR_DATE": "2020-01-01T00:00:00Z", "GIT_COMMITTER_DATE": "2020-01-01T00:00:00Z",
    "GIT_CONFIG_NOSYSTEM": "1",
}
TEXT_EXT = {".py", ".json", ".md", ".txt", ".toml", ".yaml", ".yml", ".ts", ".tsx", ".js",
            ".jsx", ".css", ".html", ".rs", ".go", ".java", ".c", ".h", ".cpp", ".ini", ".cfg"}


def _git(cwd: Path, *args: str, check: bool = True) -> str:
    env = {**os.environ, **GIT_ENV, "HOME": str(cwd)}
    proc = subprocess.run(["git", "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false",
                           "-c", "init.defaultBranch=main", *args],
                          cwd=cwd, capture_output=True, text=True, env=env, timeout=120)
    if check and proc.returncode != 0:
        raise AdapterError(f"git {' '.join(args)} failed: {proc.stderr.strip()[:500]}")
    return proc.stdout


def file_id(rel: str) -> str:
    return f"file:{rel}"


def _resolve_in(root: Path, rel: str) -> Path:
    p = (root / rel).resolve()
    if root.resolve() not in p.parents:
        raise AdapterError(f"path escapes repository: {rel}")
    return p


def _json_pointer_set(doc: Any, pointer: str, value: Any) -> Any:
    if pointer in ("", "/"):
        return value
    parts = [p.replace("~1", "/").replace("~0", "~") for p in pointer.lstrip("/").split("/")]
    cur = doc
    for part in parts[:-1]:
        if isinstance(cur, list):
            cur = cur[int(part)]
        else:
            cur = cur.setdefault(part, {})
    last = parts[-1]
    if isinstance(cur, list):
        if last == "-":
            cur.append(value)
        else:
            cur[int(last)] = value
    else:
        cur[last] = value
    return doc


def tracked_files(root: Path) -> list[str]:
    out = _git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    return sorted(f for f in out.split("\0") if f)


class CodeAdapter(Adapter):
    name = "code"
    version = "1"

    def check_environment(self) -> tuple[bool, str]:
        try:
            subprocess.run(["git", "--version"], capture_output=True, check=True, timeout=10)
            return True, "git available"
        except Exception as exc:
            return False, f"git not available: {exc}"

    def info(self) -> AdapterInfo:
        ok, detail = self.check_environment()
        return AdapterInfo(
            name=self.name, version=self.version,
            description="Text/source files in a git repository; plans become commits.",
            artifact_types=["code", "config", "text"], native_formats=["git repository"],
            preview_formats=["unified diff", "file contents"], export_formats=["zip"],
            scopes=["artifact", "file", "symbol"],
            measurements=["file_count", "lines", "bytes"],
            validation=["component_preservation", "tests", "json_valid", "op_results",
                        "file_reopens"],
            environment={"requires": "git"}, available=ok,
            unavailable_reason=None if ok else detail, templates=["files"],
            operations=[
                OperationSpec(
                    name="write_file", family="file_write", aspects=["code", "content"],
                    target_kinds=["file", "new"], description="Create or overwrite a file.",
                    params_schema={"type": "object", "additionalProperties": False,
                                   "required": ["path", "content"], "properties": {
                                       "path": {"type": "string"}, "content": {"type": "string"}}}),
                OperationSpec(
                    name="replace_text", family="file_edit", aspects=["code", "content"],
                    target_kinds=["file"], description="Replace exact text in a file "
                    "(fails if the text is absent).",
                    params_schema={"type": "object", "additionalProperties": False,
                                   "required": ["old", "new"], "properties": {
                                       "old": {"type": "string"}, "new": {"type": "string"},
                                       "count": {"type": "integer", "minimum": 1}}}),
                OperationSpec(
                    name="update_json", family="structured_update",
                    aspects=["code", "data", "manifest", "convention", "code_style"],
                    target_kinds=["file"], description="Set a value at a JSON pointer and "
                    "re-serialise deterministically.",
                    params_schema={"type": "object", "additionalProperties": False,
                                   "required": ["pointer", "value"], "properties": {
                                       "pointer": {"type": "string"}, "value": {},
                                       "indent": {"type": "integer", "minimum": 0, "maximum": 8},
                                       "sort_keys": {"type": "boolean"}}}),
                OperationSpec(
                    name="apply_patch", family="file_edit", aspects=["code"],
                    target_kinds=["*"], description="Apply a unified diff with git apply.",
                    params_schema={"type": "object", "additionalProperties": False,
                                   "required": ["patch"], "properties": {
                                       "patch": {"type": "string"}}}),
            ],
        )

    # ------------------------------------------------------------------
    def create(self, native_dir: Path, template: str, params: dict[str, Any]) -> str:
        if template != "files":
            raise AdapterError(f"unknown code template: {template}")
        native_dir.mkdir(parents=True, exist_ok=True)
        files = dict(params.get("files", {}))
        files.setdefault(".gitignore", "__pycache__/\n*.pyc\n.pytest_cache/\n")
        for rel, content in files.items():
            p = _resolve_in(native_dir, rel)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8", newline="\n")
        _git(native_dir, "init", "-q")
        _git(native_dir, "add", "-A")
        _git(native_dir, "commit", "-q", "--allow-empty", "-m",
             params.get("message", "Initial artifact state"))
        return "."

    def inspect(self, native_dir: Path, entry: str) -> InspectResult:
        root = native_dir
        comps = [Component(id=ROOT_ID, name=root.name, kind="repo", native_ref=".")]
        states: dict[str, str] = {}
        meas: dict[str, dict[str, Any]] = {}
        props: dict[str, dict[str, Any]] = {}
        root_h = hashlib.sha256()
        total = 0
        for rel in tracked_files(root):
            p = root / rel
            if not p.is_file():
                continue
            data = p.read_bytes()
            fid = file_id(rel)
            digest = hashlib.sha256(data).hexdigest()
            comps.append(Component(id=fid, name=rel, kind="file", parent_id=ROOT_ID, native_ref=rel,
                                   metadata={"language": p.suffix.lstrip(".") or "text"}))
            states[fid] = digest
            root_h.update(rel.encode() + digest.encode())
            text = data.decode("utf-8", errors="replace")
            meas[fid] = {"bytes": len(data), "lines": text.count("\n") + (0 if text.endswith("\n")
                                                                         else 1 if text else 0)}
            total += 1
            if p.suffix == ".json":
                try:
                    props[fid] = {"json_keys": sorted(json.loads(text).keys())
                                  if isinstance(json.loads(text), dict) else None}
                except json.JSONDecodeError:
                    props[fid] = {"json_error": True}
            if p.suffix == ".py":
                try:
                    tree = ast.parse(text)
                except SyntaxError:
                    continue
                lines = text.splitlines(keepends=True)
                for node in tree.body:
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        sid = f"{fid}::{node.name}"
                        seg = "".join(lines[node.lineno - 1:node.end_lineno])
                        comps.append(Component(
                            id=sid, name=node.name, parent_id=fid,
                            kind="symbol", native_ref=f"{rel}:{node.lineno}",
                            metadata={"symbol_kind": "class" if isinstance(node, ast.ClassDef)
                                      else "function", "line": node.lineno}))
                        states[sid] = hashlib.sha256(seg.encode()).hexdigest()
        states[ROOT_ID] = root_h.hexdigest()
        meas[ROOT_ID] = {"file_count": total}
        head = _git(root, "rev-parse", "HEAD", check=False).strip()
        props[ROOT_ID] = {"head": head}
        return InspectResult(components=comps, states=states, measurements=meas, properties=props)

    def apply(self, native_dir: Path, entry: str, operations: list[PlannedOperation],
              context: dict[str, Any]) -> ApplyResult:
        results = []
        for i, op in enumerate(operations):
            try:
                detail = self._apply_one(native_dir, op)
                results.append({"index": i, "op": op.op, "component_id": op.component_id,
                                "status": "applied", "detail": detail})
            except Exception as exc:
                results.append({"index": i, "op": op.op, "component_id": op.component_id,
                                "status": "failed", "detail": f"{type(exc).__name__}: {exc}"})
                return ApplyResult(ok=False, results=results, error=results[-1]["detail"])
        _git(native_dir, "add", "-A")
        if _git(native_dir, "status", "--porcelain").strip():
            msg = context.get("message") or "Daedelus revision"
            _git(native_dir, "commit", "-q", "-m", msg)
        commit = _git(native_dir, "rev-parse", "HEAD").strip()
        return ApplyResult(ok=True, results=results, logs=f"commit {commit}")

    def _file_from(self, root: Path, op: PlannedOperation) -> Path:
        cid = op.component_id or ""
        if not cid.startswith("file:"):
            raise AdapterError(f"{op.op} needs a file component, got '{cid}'")
        return _resolve_in(root, cid[len("file:"):].split("::")[0])

    def _apply_one(self, root: Path, op: PlannedOperation) -> str:
        p = op.params
        if op.op == "write_file":
            path = _resolve_in(root, p["path"])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(p["content"], encoding="utf-8", newline="\n")
            return f"wrote {p['path']} ({len(p['content'])} chars)"
        if op.op == "replace_text":
            path = self._file_from(root, op)
            text = path.read_text(encoding="utf-8")
            if p["old"] not in text:
                raise AdapterError(f"text to replace not found in {path.name}")
            text = text.replace(p["old"], p["new"], int(p.get("count", -1)) if p.get("count")
                                else -1)
            path.write_text(text, encoding="utf-8", newline="\n")
            return f"replaced text in {path.relative_to(root).as_posix()}"
        if op.op == "update_json":
            path = self._file_from(root, op)
            doc = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
            doc = _json_pointer_set(doc, p["pointer"], p["value"])
            path.write_text(json.dumps(doc, indent=int(p.get("indent", 2)),
                                       sort_keys=bool(p.get("sort_keys", True))) + "\n",
                            encoding="utf-8", newline="\n")
            return f"set {p['pointer']} in {path.relative_to(root).as_posix()}"
        if op.op == "apply_patch":
            proc = subprocess.run(["git", "apply", "--whitespace=nowarn", "-"], cwd=root,
                                  input=p["patch"], capture_output=True, text=True, timeout=60)
            if proc.returncode != 0:
                raise AdapterError(f"git apply failed: {proc.stderr.strip()[:400]}")
            return "patch applied"
        raise AdapterError(f"unknown operation {op.op}")

    def preview(self, native_dir: Path, entry: str, out_dir: Path) -> dict[str, Path]:
        out_dir.mkdir(parents=True, exist_ok=True)
        log = out_dir / "git_log.txt"
        log.write_text(_git(native_dir, "log", "--stat", "-n", "5", "--format=%H %s"))
        return {"git_log": log}

    def export(self, native_dir: Path, entry: str, fmt: str, out_dir: Path) -> Path:
        if fmt != "zip":
            raise AdapterError(f"unsupported export format {fmt}")
        import zipfile

        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{native_dir.name}.zip"
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            for rel in tracked_files(native_dir):
                z.write(native_dir / rel, rel)
        return path

    def diff(self, before_dir: Path, after_dir: Path, entry: str) -> str | None:
        def files(d: Path) -> dict[str, Path]:
            return {p.relative_to(d).as_posix(): p for p in d.rglob("*")
                    if p.is_file() and ".git" not in p.relative_to(d).parts}
        a, b = files(before_dir), files(after_dir)
        out: list[str] = []
        for rel in sorted(set(a) | set(b)):
            pa, pb = a.get(rel), b.get(rel)
            ta = pa.read_text(errors="replace").splitlines(keepends=True) if pa else []
            tb = pb.read_text(errors="replace").splitlines(keepends=True) if pb else []
            if Path(rel).suffix not in TEXT_EXT and (pa and pb) and pa.read_bytes() != pb.read_bytes():
                out.append(f"Binary file {rel} differs\n")
                continue
            if ta != tb:
                out.extend(difflib.unified_diff(ta, tb, f"a/{rel}" if pa else "/dev/null",
                                                f"b/{rel}" if pb else "/dev/null"))
        return "".join(out)

    def validate(self, native_dir: Path, entry: str, checks: list[str],
                 context: dict[str, Any]) -> ValidationReport:
        rep = ValidationReport()
        if "json_valid" in checks:
            bad = []
            for rel in tracked_files(native_dir):
                if rel.endswith(".json"):
                    try:
                        json.loads((native_dir / rel).read_text(encoding="utf-8"))
                    except Exception as exc:
                        bad.append(f"{rel}: {exc}")
            rep.add("json_valid", not bad, "all JSON files parse" if not bad else "; ".join(bad))
        if "file_reopens" in checks:
            dirty = _git(native_dir, "status", "--porcelain").strip()
            rep.add("file_reopens", not dirty, "working tree clean and committed" if not dirty
                    else f"uncommitted changes: {dirty[:300]}")
        if "tests" in checks:
            cmd = context.get("test_command")
            allowed = context.get("allowed_commands", [])
            if not cmd:
                rep.add("tests", False, "no test command configured for this artifact")
            elif not any(cmd == a or cmd.startswith(a + " ") for a in allowed):
                rep.add("tests", False, f"test command '{cmd}' is not in the project's "
                        f"allowed_commands {allowed} - not executed")
            else:
                argv = shlex.split(cmd)
                if argv and argv[0] in ("python", "python3"):
                    argv[0] = sys.executable
                env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", **context.get("env", {})}
                if argv[:3] == [sys.executable, "-m", "pytest"]:
                    argv += ["-p", "no:cacheprovider"]
                try:
                    proc = subprocess.run(argv, cwd=native_dir, capture_output=True, text=True,
                                          timeout=float(context.get("test_timeout", 180)), env=env)
                    out = (proc.stdout + proc.stderr)[-4000:]
                    rep.add("tests", proc.returncode == 0, f"`{cmd}` exited {proc.returncode}",
                            output=out, command=cmd)
                except subprocess.TimeoutExpired:
                    rep.add("tests", False, f"`{cmd}` timed out")
        return rep
