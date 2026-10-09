"""Runs ONE adapter method inside a job directory (separate process, killable).

Layout prepared by the worker::

    <jobdir>/job.json      JobRequest
    <jobdir>/native/       the artifact tree (modified in place by mutating methods)
    <jobdir>/before/       second tree for diff
    <jobdir>/files/<n>     context files (source images, rendered charts, imports)
    <jobdir>/out/          preview / export output
    <jobdir>/result.json   written here: {"ok", "value", "error", "adapter_error"}

Only the fixed adapter methods can run; commands (code tests) remain subject to the
adapter's own allowlist intersected with the worker's.
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
from pathlib import Path

T_START = time.perf_counter()


ADAPTER_CLASSES = {
    "blender": ("..adapters.blender", "BlenderAdapter"),
    "layered2d": ("..adapters.layered2d", "LayeredImageAdapter"),
    "code": ("..adapters.codefiles", "CodeAdapter"),
    "spreadsheet": ("..adapters.office.spreadsheet", "SpreadsheetAdapter"),
    "document": ("..adapters.office.document", "DocumentAdapter"),
    "presentation": ("..adapters.office.presentation", "PresentationAdapter"),
}


def _adapter(name: str):
    """Import only the adapter this job needs (V2.1: shorter job start-up)."""
    import importlib
    if name in ADAPTER_CLASSES:
        mod, cls = ADAPTER_CLASSES[name]
        return getattr(importlib.import_module(mod, __package__), cls)()
    from ..adapters import registry
    return registry()[name]


def main(jobdir: str) -> int:
    jd = Path(jobdir)
    from ..adapters import AdapterError
    from ..models import PlannedOperation
    from .models import JobRequest

    req = JobRequest.model_validate_json((jd / "job.json").read_text())
    ad = _adapter(req.adapter)  # the real local adapter (never the remote proxy)
    native, out = jd / "native", jd / "out"
    out.mkdir(exist_ok=True)
    files = {k: str(jd / "files" / str(i)) for i, k in enumerate(sorted(req.files))}
    a = req.args
    res: dict = {"ok": True, "value": {}}
    t_imported = time.perf_counter()
    try:
        m = req.method
        if m == "create":
            params = dict(a.get("params") or {})
            if "import" in files:
                params["path"] = files["import"]
            native.mkdir(exist_ok=True)
            res["value"] = {"entry": ad.create(native, a["template"], params)}
        elif m == "inspect":
            res["value"] = ad.inspect(native, req.entry).model_dump()
        elif m == "apply":
            ctx = {"message": a.get("message", ""),
                   "source_paths": {k.split(":", 1)[1]: p for k, p in files.items()
                                    if k.startswith("source:")},
                   "files": {k.split(":", 1)[1]: p for k, p in files.items()
                             if k.startswith("file:")}}
            ops = [PlannedOperation(**o) for o in a.get("operations", [])]
            r = ad.apply(native, req.entry, ops, ctx)
            res["value"] = r.model_dump()
            res["ok"] = True  # an unsuccessful ApplyResult is a value, not a job failure
        elif m == "preview":
            got = ad.preview(native, req.entry, out)
            res["value"] = {"files": {k: Path(p).relative_to(out).as_posix()
                                      for k, p in got.items() if Path(p).is_relative_to(out)}}
        elif m == "export":
            p = ad.export(native, req.entry, a["fmt"], out)
            res["value"] = {"path": Path(p).relative_to(out).as_posix()}
        elif m == "diff":
            res["value"] = {"diff": ad.diff(jd / "before", native, req.entry)}
        elif m == "after_restore":
            ad.after_restore(native, req.entry, a.get("message", ""))
        elif m == "validate":
            worker_allowed = [c.strip() for c in os.environ.get(
                "DAEDELUS_WORKER_ALLOWED_COMMANDS", "python -m pytest").split(",") if c.strip()]
            allowed = [c for c in a.get("allowed_commands", []) if c in worker_allowed]
            rep = ad.validate(native, req.entry, a.get("checks", []),
                              {"allowed_commands": allowed, "test_command": a.get("test_command"),
                               "env": {}})
            res["value"] = rep.model_dump()
        elif m == "side_effect_scope" and "ops" in a:  # V2.1 batch: all ops in one job
            res["value"] = {"ids_per_op": [ad.side_effect_scope(native, req.entry,
                                                                PlannedOperation(**o))
                                           for o in a["ops"]]}
        elif m == "side_effect_scope":
            res["value"] = {"ids": ad.side_effect_scope(native, req.entry,
                                                        PlannedOperation(**a["op"]))}
        else:
            raise ValueError(f"unsupported method {m}")
        if a.get("post_inspect") and m in ("create", "apply", "after_restore") and res["ok"] \
                and (m != "apply" or res["value"].get("ok", True)):
            ent = res["value"].get("entry", req.entry) if m == "create" else req.entry
            res["value"]["_post_inspect"] = ad.inspect(native, ent).model_dump()
    except AdapterError as exc:
        res = {"ok": False, "error": str(exc), "adapter_error": True}
    except Exception as exc:
        res = {"ok": False, "error": f"{type(exc).__name__}: {exc}",
               "trace": traceback.format_exc()[-4000:]}
    res["timings"] = {"startup": round(t_imported - T_START, 4),
                      "method": round(time.perf_counter() - t_imported, 4)}
    (jd / "result.json").write_text(json.dumps(res, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
