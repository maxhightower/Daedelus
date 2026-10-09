"""Job isolation on workers (V2.1).

V2 ran each job in a child process as the worker's (non-root) user. The child inherited the
worker's environment - including its credential - and could read every other job directory
and reach the network. That is not a sandbox. V2.1 runs every job under one of two profiles:

``process`` (always available)
    * environment reduced to an allowlist: no worker credential, no provider keys;
    * private job directory (0700), ``HOME`` and ``TMPDIR`` inside it, umask 077;
    * POSIX resource limits: address space, file size, open files, processes, no core dumps;
    * own session / process group, killed as a tree on cancel, deadline or lease loss.

``bwrap`` (Linux, when bubblewrap works here; verified by a self-test at worker start)
    everything above, plus
    * new user, PID, IPC, UTS, cgroup and **network** namespaces: no network at all;
    * read-only view of the root filesystem; only the job directory is writable;
    * the worker's work directory (other jobs, the blob cache), ``/root``, ``/home`` and
      ``/run/secrets`` are hidden behind empty tmpfs mounts; fresh ``/proc``, ``/dev``, ``/tmp``;
    * all capabilities dropped, ``no_new_privs``, dies with the worker.

The self-test (``probe``) runs a probe *inside* the profile and records which controls
actually took effect; the worker reports it at registration so the control plane and the
studio show what isolation a job really had. Nothing is claimed that the probe did not see.

``DAEDELUS_JOB_ISOLATION`` = ``auto`` (bwrap if verified, else process) | ``process`` |
``bwrap`` (refuse to start without it). Adapters listed in ``DAEDELUS_REQUIRE_SANDBOX``
(default ``code`` in the hosted profile) are only offered when a network-isolating sandbox is
verified, because code jobs run repository tests (arbitrary code).
"""

from __future__ import annotations

import dataclasses
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import profile

ENV_ALLOW = ("PATH", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "DAEDELUS_BLENDER",
             "DAEDELUS_REQUIRE_LIBREOFFICE", "DAEDELUS_WORKER_ALLOWED_COMMANDS", "PYTHONPATH",
             "DAEDELUS_JOB_PROFILE_PHASES",
             # Windows process essentials
             "SYSTEMROOT", "SYSTEMDRIVE", "COMSPEC", "PATHEXT", "WINDIR", "PROGRAMFILES",
             "PROGRAMFILES(X86)", "LOCALAPPDATA", "APPDATA")
SECRET_MARKERS = ("TOKEN", "SECRET", "PASSWORD", "CREDENTIAL", "API_KEY", "PRIVATE")


def _mb(var: str, default: int) -> int:
    try:
        return int(os.environ.get(var, default))
    except ValueError:
        return default


@dataclass
class Limits:
    memory_mb: int = field(default_factory=lambda: _mb("DAEDELUS_JOB_MEMORY_MB", 8192))
    file_mb: int = field(default_factory=lambda: _mb("DAEDELUS_JOB_MAX_FILE_MB", 4096))
    nofile: int = field(default_factory=lambda: _mb("DAEDELUS_JOB_NOFILE", 4096))
    nproc: int = field(default_factory=lambda: _mb("DAEDELUS_JOB_NPROC", 0))  # 0 = unset

    def preexec(self):
        """Function run in the child before exec (POSIX only)."""
        lim = self

        def apply():
            import resource
            os.umask(0o077)
            pairs = [(resource.RLIMIT_CORE, 0),
                     (resource.RLIMIT_AS, lim.memory_mb * 1024 * 1024),
                     (resource.RLIMIT_FSIZE, lim.file_mb * 1024 * 1024),
                     (resource.RLIMIT_NOFILE, lim.nofile)]
            if lim.nproc:
                pairs.append((resource.RLIMIT_NPROC, lim.nproc))
            for res, val in pairs:
                try:
                    soft, hard = resource.getrlimit(res)
                    if hard != resource.RLIM_INFINITY:
                        val = min(val, hard)
                    resource.setrlimit(res, (val, hard if hard != resource.RLIM_INFINITY
                                             and hard < val else val))
                except (ValueError, OSError):
                    pass
        return apply

    def describe(self) -> dict[str, Any]:
        return {"memory_mb": self.memory_mb, "file_mb": self.file_mb, "nofile": self.nofile,
                "nproc": self.nproc or None}


def job_env(jobdir: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k in ENV_ALLOW}
    home, tmp = jobdir / "home", jobdir / "tmp"
    home.mkdir(exist_ok=True)
    tmp.mkdir(exist_ok=True)
    env.update(HOME=str(home), TMPDIR=str(tmp), TMP=str(tmp), TEMP=str(tmp),
               PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1")
    if os.name == "nt":
        env["USERPROFILE"] = str(home)
    return env


def tool_paths() -> list[Path]:
    """Directories a job runner must see: the daedelus package, the Python installation and
    the external tools (Blender, LibreOffice, git)."""
    import daedelus
    out = {Path(daedelus.__file__).resolve().parent.parent, Path(sys.prefix).resolve(),
           Path(sys.base_prefix).resolve(), Path(sys.executable).resolve().parent}
    for exe in (os.environ.get("DAEDELUS_BLENDER"), shutil.which("blender"),
                shutil.which("soffice"), shutil.which("git")):
        if exe and Path(exe).exists():
            out.add(Path(exe).resolve().parent)
    for entry in (os.environ.get("PYTHONPATH") or "").split(os.pathsep):
        if entry and Path(entry).is_dir():
            out.add(Path(entry).resolve())
    # editable installs point into source trees through .pth files in site-packages
    import site
    for sp in site.getsitepackages() if hasattr(site, "getsitepackages") else []:
        for pth in Path(sp).glob("*.pth"):
            try:
                for line in pth.read_text().splitlines():
                    if line and not line.startswith(("#", "import")) and Path(line).is_dir():
                        out.add(Path(line).resolve())
            except OSError:
                pass
    return sorted(out)


def bwrap_path() -> str | None:
    return shutil.which("bwrap") if sys.platform.startswith("linux") else None


class Sandbox:
    def __init__(self, work_dir: Path, profile_name: str = "process",
                 limits: Limits | None = None, hide: list[Path] | None = None):
        self.work_dir = Path(work_dir).resolve()
        self.profile = profile_name
        self.limits = limits or Limits()
        self.hide = [Path(p) for p in (hide or [])]
        self.report: dict[str, Any] = {"profile": profile_name, "verified": False}

    # ------------------------------------------------------------------ command
    def wrap(self, argv: list[str], jobdir: Path) -> list[str]:
        if self.profile != "bwrap":
            return argv
        jobdir = Path(jobdir).resolve()
        args = [bwrap_path() or "bwrap", "--unshare-all", "--die-with-parent", "--new-session",
                "--cap-drop", "ALL", "--hostname", "daedelus-job",
                "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp"]
        masked = [self.work_dir] + self.hide + [Path(p) for p in ("/root", "/home",
                                                                  "/run/secrets")]
        for m in masked:
            if m.exists() and m.is_dir():
                args += ["--tmpfs", str(m)]
        # re-expose, read-only, the tools a job needs if they live under a masked directory
        # (an editable checkout or a Blender build in a home directory)
        for t in tool_paths():
            if any(t == m or m in t.parents for m in masked + [Path("/tmp")]) and \
                    not any(t == m or m in t.parents for m in [self.work_dir] + self.hide):
                args += ["--ro-bind", str(t), str(t)]
        args += ["--bind", str(jobdir), str(jobdir), "--chdir", str(jobdir), "--"]
        return args + argv

    def popen(self, argv: list[str], jobdir: Path, memory_mb: int | None = None,
              **kw) -> subprocess.Popen:
        from .worker import new_group_kwargs
        Path(jobdir).chmod(0o700)
        extra: dict[str, Any] = {}
        if os.name != "nt":
            lim = self.limits
            if memory_mb and memory_mb < lim.memory_mb:  # a job may only lower the limit
                lim = dataclasses.replace(lim, memory_mb=int(memory_mb))
            extra["preexec_fn"] = lim.preexec()
        return subprocess.Popen(self.wrap(argv, jobdir), env=job_env(Path(jobdir)),
                                cwd=str(jobdir), **new_group_kwargs(), **extra, **kw)

    # ------------------------------------------------------------------ self-test
    def probe(self) -> dict[str, Any]:
        """Run the probe inside this profile; record which controls really took effect."""
        jd = Path(tempfile.mkdtemp(prefix="probe_", dir=self.work_dir))
        sibling = Path(tempfile.mkdtemp(prefix="other_job_", dir=self.work_dir))
        (sibling / "secret.txt").write_text("another job's data")
        try:
            p = self.popen([sys.executable, "-I", "-c", PROBE, str(sibling)], jd,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            out, err = p.communicate(timeout=60)
            data = json.loads(out.decode().strip().splitlines()[-1])
        except Exception as exc:
            return {"profile": self.profile, "verified": False,
                    "error": f"probe failed: {type(exc).__name__}: {exc}"[:500]}
        finally:
            shutil.rmtree(jd, ignore_errors=True)
            shutil.rmtree(sibling, ignore_errors=True)
        c = {
            "no_secrets_in_env": not data["secret_env"],
            "private_job_dir": not data["sibling_readable"],
            "rlimit_memory": data["rlimit_as"] not in (None, -1),
            "no_core_dumps": data["rlimit_core"] == 0,
            "network_isolated": not data["network"],
            "read_only_root": not data["root_writable"],
            "pid_namespace": data["pids_visible"] <= 3,
            "no_new_privs": data["no_new_privs"] == 1,
            "capabilities_dropped": data["cap_eff"] == 0,
            "seccomp": data["seccomp"] in (1, 2),
        }
        self.report = {"profile": self.profile, "verified": True, "controls": c,
                       "limits": self.limits.describe(), "uid": data["uid"],
                       "raw": {k: data[k] for k in ("network", "pids_visible", "cap_eff",
                                                    "seccomp", "no_new_privs")}}
        return self.report

    @property
    def network_isolated(self) -> bool:
        return bool(self.report.get("verified") and
                    self.report.get("controls", {}).get("network_isolated"))


PROBE = r"""
import json, os, socket, sys
st = {}
try:
    for line in open("/proc/self/status"):
        k, _, v = line.partition(":")
        st[k] = v.strip()
except OSError:
    pass
def can_write(p):
    try:
        with open(os.path.join(p, ".dd_probe"), "w") as f:
            f.write("x")
        os.unlink(os.path.join(p, ".dd_probe"))
        return True
    except OSError:
        return False
def net():
    for host in ("1.1.1.1", "8.8.8.8"):
        try:
            socket.create_connection((host, 443), timeout=2).close()
            return True
        except OSError:
            pass
    try:
        return len(socket.getaddrinfo("example.com", 443)) > 0
    except OSError:
        return False
try:
    import resource
    ras = resource.getrlimit(resource.RLIMIT_AS)[0]
    rcore = resource.getrlimit(resource.RLIMIT_CORE)[0]
except Exception:
    ras = rcore = None
try:
    sib = open(os.path.join(sys.argv[1], "secret.txt")).read() != ""
except OSError:
    sib = False
try:
    pids = len([d for d in os.listdir("/proc") if d.isdigit()])
except OSError:
    pids = 9999
markers = ("TOKEN", "SECRET", "PASSWORD", "CREDENTIAL", "API_KEY", "PRIVATE")
print(json.dumps({
    "uid": os.getuid() if hasattr(os, "getuid") else None,
    "secret_env": sorted(k for k in os.environ if any(m in k.upper() for m in markers)),
    "sibling_readable": sib,
    "rlimit_as": ras, "rlimit_core": rcore,
    "network": net(),
    "root_writable": can_write("/usr") or can_write("/etc"),
    "pids_visible": pids,
    "no_new_privs": int(st.get("NoNewPrivs", "-1") or -1),
    "cap_eff": int(st.get("CapEff", "1"), 16) if st.get("CapEff") else -1,
    "seccomp": int(st.get("Seccomp", "-1") or -1),
}))
"""


def choose(work_dir: Path, hide: list[Path] | None = None) -> Sandbox:
    """Pick and verify the isolation profile according to DAEDELUS_JOB_ISOLATION."""
    want = (os.environ.get("DAEDELUS_JOB_ISOLATION") or "auto").strip().lower()
    if want in ("auto", "bwrap") and bwrap_path():
        sb = Sandbox(work_dir, "bwrap", hide=hide)
        rep = sb.probe()
        if rep.get("verified") and rep["controls"].get("network_isolated") and \
                rep["controls"].get("private_job_dir"):
            return sb
        if want == "bwrap":
            raise RuntimeError(f"bwrap sandbox requested but not working here: {rep}")
    elif want == "bwrap":
        raise RuntimeError("bwrap sandbox requested but bubblewrap is not installed")
    sb = Sandbox(work_dir, "process", hide=hide)
    sb.probe()
    if want == "auto" and bwrap_path():
        sb.report["note"] = "bubblewrap present but its self-test failed; using process profile"
    return sb


def required_adapters() -> set[str]:
    raw = os.environ.get("DAEDELUS_REQUIRE_SANDBOX")
    if raw is None:
        raw = "code" if profile.hosted() else ""
    return {a.strip() for a in raw.split(",") if a.strip()}
