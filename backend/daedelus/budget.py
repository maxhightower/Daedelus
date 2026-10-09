"""Per-workflow execution budgets (V2.1).

A workflow's ``parameters["budget"]`` (or the defaults below) bounds one execution:

==================== ======================================================================
``max_model_calls``  model calls (analyse, plan, evaluate, revise) across the execution
``max_cost_usd``     spend on calls whose price is known; calls with an unknown price are
                     counted separately and bounded by ``max_model_calls``/``max_tokens``
``max_tokens``       input + output tokens across the execution
``max_seconds``      wall-clock time of the execution
``max_iterations``   corrective iterations of any Evaluate/Revise loop (caps node settings)
``call_timeout_s``   timeout of one model request (SDK retries included in the budget)
``max_retries``      SDK retries per model request (rate limits, 5xx, timeouts)
``job_timeout_s``    deadline of one remote job attempt
``job_memory_mb``    memory limit applied to remote jobs (min with the worker's own limit)
``max_concurrent_jobs`` remote jobs of this project leased at the same time
==================== ======================================================================

Enforcement is *before* each call (a call that would start over a limit is refused) and the
running totals are recorded on the execution. A model whose price is unknown is reported as
cost ``unknown`` - never as zero.
"""

from __future__ import annotations

import contextlib
import contextvars
import time
from dataclasses import asdict, dataclass, field
from typing import Any

DEFAULTS: dict[str, Any] = {
    "max_model_calls": 40,
    "max_cost_usd": 5.0,
    "max_tokens": 2_000_000,
    "max_seconds": 3600.0,
    "max_iterations": 3,
    "call_timeout_s": 180.0,
    "max_retries": 2,
    "job_timeout_s": 900.0,
    "job_memory_mb": None,
    "max_concurrent_jobs": 4,
}


class BudgetExceeded(RuntimeError):
    """A model call or step would exceed the execution budget (nothing was spent on it)."""


@dataclass
class ExecutionBudget:
    limits: dict[str, Any] = field(default_factory=lambda: dict(DEFAULTS))
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0  # known-price spend
    unpriced_calls: int = 0  # calls whose cost is unknown
    started: float = field(default_factory=time.monotonic)
    refusals: list[str] = field(default_factory=list)
    by_provider: dict[str, dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def from_config(cls, cfg: dict[str, Any] | None) -> "ExecutionBudget":
        lim = dict(DEFAULTS)
        for k, v in (cfg or {}).items():
            if k in DEFAULTS and v is not None:
                lim[k] = v
        return cls(limits=lim)

    # ---------------------------------------------------------------- checks
    def elapsed(self) -> float:
        return time.monotonic() - self.started

    def _refuse(self, why: str) -> None:
        self.refusals.append(why)
        raise BudgetExceeded(why)

    def check_time(self) -> None:
        ms = self.limits.get("max_seconds")
        if ms is not None and self.elapsed() > float(ms):
            self._refuse(f"execution time budget of {ms} s exhausted")

    def before_call(self, provider: str, contract: str) -> None:
        self.check_time()
        lim = self.limits
        if lim.get("max_model_calls") is not None and self.calls >= int(lim["max_model_calls"]):
            self._refuse(f"model call budget exhausted ({self.calls} of "
                         f"{lim['max_model_calls']}); {provider}.{contract} not called")
        if lim.get("max_cost_usd") is not None and self.cost_usd >= float(lim["max_cost_usd"]):
            self._refuse(f"cost budget exhausted (${self.cost_usd:.4f} of "
                         f"${float(lim['max_cost_usd']):.2f}); {provider}.{contract} not called")
        if lim.get("max_tokens") is not None and \
                self.input_tokens + self.output_tokens >= int(lim["max_tokens"]):
            self._refuse(f"token budget exhausted; {provider}.{contract} not called")

    def charge(self, provider: str, usage: dict[str, Any] | None, *, live: bool) -> None:
        u = usage or {}
        n = int(u.get("calls") or (1 if live else 0))
        self.calls += n
        self.input_tokens += int(u.get("input_tokens") or 0)
        self.output_tokens += int(u.get("output_tokens") or 0)
        if u.get("cost_usd") is not None:
            self.cost_usd += float(u["cost_usd"])
        elif n:
            self.unpriced_calls += n
        p = self.by_provider.setdefault(provider, {"calls": 0, "input_tokens": 0,
                                                   "output_tokens": 0, "cost_usd": 0.0,
                                                   "cost_known": True, "models": []})
        p["calls"] += n
        p["input_tokens"] += int(u.get("input_tokens") or 0)
        p["output_tokens"] += int(u.get("output_tokens") or 0)
        if u.get("cost_usd") is not None:
            p["cost_usd"] += float(u["cost_usd"])
        elif n:
            p["cost_known"] = False
        if u.get("model") and u["model"] not in p["models"]:
            p["models"].append(u["model"])

    def iterations_cap(self, requested: int) -> int:
        cap = self.limits.get("max_iterations")
        return min(int(requested), int(cap)) if cap is not None else int(requested)

    def snapshot(self) -> dict[str, Any]:
        return {"limits": self.limits, "used": {
            "model_calls": self.calls, "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_usd": round(self.cost_usd, 6),
            "cost": ("unknown" if self.unpriced_calls and not self.cost_usd else
                     f"${self.cost_usd:.4f}" + (f" + {self.unpriced_calls} call(s) of unknown "
                                                 "price" if self.unpriced_calls else "")),
            "unpriced_calls": self.unpriced_calls, "seconds": round(self.elapsed(), 2)},
            "by_provider": self.by_provider, "refusals": self.refusals}

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


_ctx: contextvars.ContextVar[ExecutionBudget | None] = contextvars.ContextVar("dd_budget",
                                                                               default=None)


@contextlib.contextmanager
def use_budget(b: ExecutionBudget | None):
    tok = _ctx.set(b)
    try:
        yield b
    finally:
        _ctx.reset(tok)


def current() -> ExecutionBudget | None:
    return _ctx.get()


def call_settings() -> dict[str, Any]:
    """Client settings for model SDK calls under the active budget (or defaults)."""
    b = current()
    lim = b.limits if b else DEFAULTS
    return {"timeout": float(lim.get("call_timeout_s") or DEFAULTS["call_timeout_s"]),
            "max_retries": int(lim.get("max_retries") if lim.get("max_retries") is not None
                               else DEFAULTS["max_retries"])}
