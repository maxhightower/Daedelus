"""Regressions found by native Windows validation (NATIVE-1).

D-002: text written or read without an explicit encoding uses the Windows ANSI code page
(cp1252) outside Python's UTF-8 mode, so any character outside it in a bound source
crashed agent planning. D-003: a frozen build took the Microsoft Store ``python`` alias
stub for a real interpreter.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import daedelus
from daedelus import ingest
from daedelus.adapters import codefiles
from daedelus.artifacts import create_artifact
from daedelus.engine import Engine
from daedelus.models import (
    RunStatus,
    SourceBinding,
    TargetSelector,
    Workflow,
    WorkflowEdge,
    WorkflowNode,
)

PKG = Path(daedelus.__file__).parent
# Linux-only probe inside the sandbox self-test; /proc is ASCII.
EXEMPT = {("distributed/sandbox.py", "/proc/self/status")}


def _kw(call: ast.Call, name: str):
    return next((k for k in call.keywords if k.arg == name), None)


def _unencoded_text_io(path: Path) -> list[str]:
    src = path.read_text(encoding="utf-8")
    rel = path.relative_to(PKG).as_posix()
    bad = []
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.Call) or _kw(node, "encoding"):
            continue
        f, what = node.func, None
        if isinstance(f, ast.Attribute) and f.attr in ("read_text", "write_text"):
            what = f.attr
        elif isinstance(f, ast.Name) and f.id == "open":
            mode = node.args[1] if len(node.args) > 1 else getattr(_kw(node, "mode"), "value", None)
            if mode is None or (isinstance(mode, ast.Constant) and "b" not in mode.value):
                what = "open"
        elif (isinstance(f, ast.Attribute) and f.attr == "run" and _kw(node, "text")
              and getattr(f.value, "id", None) == "subprocess"):
            what = "subprocess.run(text=True)"
        if what and not any(r == rel and s in (ast.get_source_segment(src, node) or "")
                            for r, s in EXEMPT):
            bad.append(f"{rel}:{node.lineno} {what}")
    return bad


def test_text_io_declares_encoding():
    bad = [b for p in sorted(PKG.rglob("*.py")) for b in _unencoded_text_io(p)]
    assert not bad, "text I/O without encoding= (cp1252 on Windows):\n" + "\n".join(bad)


def test_non_cp1252_guideline_plans_and_replays(store):
    img = create_artifact(store, name="Picture", adapter="layered2d", template="layers", params={
        "width": 32, "height": 24, "root_id": "picture",
        "layers": [{"id": "sky", "name": "Sky", "fill": {"type": "solid", "colors": ["#88aadd"]}}]})[0]
    guide = ingest.register_text(store, "guide", "tint: #ffcc88\n"
                                 "note: leg profile → taper; height ≤ 0.75 m; CO₂-neutral oil 🌳\n")
    store.save_binding(SourceBinding(source_id=guide.id, role="guideline",
                                     aspects=["style", "color"], target=TargetSelector(scope="project")))
    wf = store.save_workflow(Workflow(name="unicode", nodes=[
        WorkflowNode(id="src", type="sources"),
        WorkflowNode(id="img", type="artifact", config={"artifact_id": img.id}),
        WorkflowNode(id="paint", type="agent", config={"fan_out": False})], edges=[
        WorkflowEdge(id="a", source="img", source_port="artifact", target="paint",
                     target_port="artifact"),
        WorkflowEdge(id="b", source="src", source_port="sources", target="paint",
                     target_port="sources")]))
    eng = Engine(store)
    ex = eng.execute(wf.id)
    assert ex.run("paint").status == RunStatus.succeeded, ex.run("paint").error
    requests = list(store.execution_dir(ex.id).glob("plan_request_*.json"))
    assert requests and "→" in requests[0].read_text(encoding="utf-8")
    rep = eng.replay(ex.id)
    assert all(r.get("reproducible", True) for r in rep["results"]), rep


def test_python_interpreter_skips_store_alias_stub(tmp_path, monkeypatch):
    monkeypatch.delenv("DAEDELUS_PYTHON", raising=False)
    stub = tmp_path / "store_alias.py"  # behaves like the WindowsApps python.exe stub
    stub.write_text("import sys\nprint('Python was not found; run without arguments to install "
                    "from the Microsoft Store')\nsys.exit(9009)\n", encoding="utf-8")
    real = codefiles._python_interpreter([[sys.executable, str(stub)], [sys.executable]])
    assert real and Path(real).is_file()
    assert codefiles._python_interpreter([[sys.executable, str(stub)]]) is None
    assert codefiles._python_interpreter([[str(tmp_path / "missing.exe")]]) is None
