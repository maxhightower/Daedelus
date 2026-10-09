"""Pre-run summary of where work will execute and what it may cost (V2.1).

``preflight(store, workflow)`` answers, before anything runs: for every node that touches an
artifact, which execution target it resolves to right now (local machine, a container worker
or a hosted worker - with the worker's isolation profile), which model provider and model will
be called and whether that provider is live and configured, whether its price is known, and the
budget limits that bound the run. Nothing is executed and no job is submitted.
"""

from __future__ import annotations

from typing import Any

from .adapters import registry as adapter_registry
from .budget import DEFAULTS
from .distributed import remote
from .distributed.service import get_cluster
from .nodes import NODE_TYPES
from .providers import providers
from .semantic.service import PRICES


def _model_for(provider: str, cfg: dict[str, Any]) -> str | None:
    if cfg.get("model"):
        return cfg["model"]
    if provider == "anthropic":
        from .providers.anthropic_provider import DEFAULT_MODEL
        return DEFAULT_MODEL
    if provider == "gemini":
        from .providers.gemini_provider import DEFAULT_MODEL
        return DEFAULT_MODEL
    return None


def preflight(store, wf) -> dict[str, Any]:
    proj = store.get_project()
    budget = {**DEFAULTS, **((wf.parameters or {}).get("budget") or {})}
    provs = providers()
    cl = get_cluster()
    workers = {w.id: w for w in (cl.live_workers() if cl else [])}
    nodes = []
    live_any = remote_any = False
    for n in wf.nodes:
        nt = NODE_TYPES.get(n.type)
        cfg = {**(nt.defaults if nt else {}), **n.config}
        item: dict[str, Any] = {"node_id": n.id, "type": n.type, "label": n.label}
        arts = [e.source for e in wf.edges if e.target == n.id and e.target_port in
                ("artifact", "revision")]
        adapters = set()
        for src in arts:
            sn = next((x for x in wf.nodes if x.id == src), None)
            if sn and sn.type == "artifact" and sn.config.get("artifact_id"):
                try:
                    adapters.add(store.get_artifact(sn.config["artifact_id"]).adapter)
                except LookupError:
                    pass
        if adapters:
            target = cfg.get("execution") or proj.settings.execution_target
            exe = {}
            with remote.use_target(proj.id, target, origin={}) as tc:
                for ad in sorted(adapters):
                    try:
                        d = remote.decide(adapter_registry()[ad], tc)
                    except remote.ExecutionUnavailable as exc:
                        exe[ad] = {"resolved": None, "error": str(exc)}
                        continue
                    w = None
                    if d["resolved"] != "local" and cl:
                        cap = "gpu" if d["resolved"] == "cloud_gpu" else "cpu"
                        w = cl.can_run(ad, cap)
                    exe[ad] = {**d, "where": "this machine" if d["resolved"] == "local" else
                               (w.deployment or "worker") if w else "worker",
                               "worker": w.name if w else None,
                               "isolation": (w.isolation or {}).get("profile") if w else
                               "local process", "isolation_verified":
                               bool((w.isolation or {}).get("verified")) if w else None}
                    remote_any |= d["resolved"] != "local"
            item["execution"] = {"requested": target, "adapters": exe}
        if n.type == "agent":
            pname = cfg.get("provider") or proj.settings.default_provider
            p = provs.get(pname)
            ok, why = p.available() if p else (False, "unknown provider")
            model = _model_for(pname, cfg)
            loop = cfg.get("loop") or {}
            item["model"] = {"provider": pname, "model": model, "live": bool(p and p.live),
                             "available": ok, "detail": why,
                             "price_known": (model in PRICES) if p and p.live else None,
                             "evaluate_revise_loop": bool(loop.get("enabled")),
                             "max_iterations": min(int(loop.get("max_iterations", 3)),
                                                   int(budget["max_iterations"]))
                             if loop.get("enabled") else 0}
            live_any |= bool(p and p.live)
        if len(item) > 3:
            nodes.append(item)
    unknown_price = any(n.get("model", {}).get("live") and not n["model"].get("price_known")
                        for n in nodes)
    spend = ("no model calls (deterministic providers only)" if not live_any else
             f"at most ${float(budget['max_cost_usd']):.2f} (enforced before every call)"
             + (" for priced models; models with unknown prices are bounded only by "
                f"{budget['max_model_calls']} calls and {budget['max_tokens']} tokens"
                if unknown_price else ""))
    return {"workflow_id": wf.id, "nodes": nodes, "budget": budget, "live_models": live_any,
            "remote_execution": remote_any, "spend_bound": spend,
            "needs_confirmation": live_any or remote_any}
