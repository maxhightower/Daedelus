"""Build the worker seccomp profile: Docker's default profile + what bubblewrap needs.

    python deploy/seccomp/make_profile.py   # writes deploy/seccomp/worker-sandbox.json

Docker's default profile (moby v28.0.0, Apache-2.0, ``moby-default-v28.0.0.json``) allows
namespace creation only to containers holding CAP_SYS_ADMIN. The Daedelus worker container
runs WITHOUT any capability (cap_drop: ALL) and needs to create *unprivileged user namespaces*
so bubblewrap can give each job its own network/PID/mount namespace. This profile adds one
rule allowing exactly the syscalls bubblewrap uses for that. The kernel still confines every
one of them to the new user namespace: the container gains no host privilege, and everything
else in the default profile (bpf, perf_event_open, kexec, module loading, ...) stays blocked.
"""

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXTRA = {
    "names": ["clone", "clone3", "unshare", "mount", "umount2", "pivot_root", "setns",
              "sethostname", "setdomainname"],
    "action": "SCMP_ACT_ALLOW",
    "comment": "Daedelus: bubblewrap job sandbox (unprivileged user namespaces only)",
}

if __name__ == "__main__":
    prof = json.loads((HERE / "moby-default-v28.0.0.json").read_text())
    # drop the default's clone3 ENOSYS rule for non-admin so glibc/bwrap may use clone3
    prof["syscalls"] = [s for s in prof["syscalls"]
                        if not (s.get("names") == ["clone3"] and s["action"] == "SCMP_ACT_ERRNO")]
    prof["syscalls"].append(EXTRA)
    (HERE / "worker-sandbox.json").write_text(json.dumps(prof, indent=1) + "\n")
    print("wrote", HERE / "worker-sandbox.json")
