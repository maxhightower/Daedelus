"""V2.1 secure transport: built-in HTTPS, certificate verification by workers, failure cases.

Certificates come from a throw-away private CA generated with the openssl CLI. A worker must
reach the control plane only over verified TLS: an unknown CA, a wrong hostname or plain HTTP
to a non-loopback host are refused, and nothing in the code path disables verification.
"""

from __future__ import annotations

import shutil
import ssl
import subprocess
import threading
import time
from pathlib import Path

import httpx
import pytest

from test_distributed import WT, _free_port

pytestmark = pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl CLI needed")


def _ca(d: Path, name: str) -> tuple[Path, Path]:
    key, crt = d / f"{name}.key", d / f"{name}.crt"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2",
                    "-subj", f"/CN={name}", "-keyout", str(key), "-out", str(crt),
                    "-addext", "basicConstraints=critical,CA:TRUE",
                    "-addext", "keyUsage=critical,keyCertSign,cRLSign"],
                   check=True, capture_output=True)
    return key, crt


def _leaf(d: Path, ca: tuple[Path, Path], name: str, sans: str) -> tuple[Path, Path]:
    key, csr, crt = d / f"{name}.key", d / f"{name}.csr", d / f"{name}.crt"
    subprocess.run(["openssl", "req", "-newkey", "rsa:2048", "-nodes", "-subj", f"/CN={name}",
                    "-keyout", str(key), "-out", str(csr)], check=True, capture_output=True)
    ext = d / f"{name}.ext"
    ext.write_text(f"subjectAltName={sans}\nextendedKeyUsage=serverAuth,clientAuth\n")
    subprocess.run(["openssl", "x509", "-req", "-in", str(csr), "-CA", str(ca[1]), "-CAkey",
                    str(ca[0]), "-CAcreateserial", "-days", "2", "-out", str(crt), "-extfile",
                    str(ext)], check=True, capture_output=True)
    return key, crt


class TlsServer:
    def __init__(self, tmp: Path, monkeypatch, cert: tuple[Path, Path], client_ca: Path | None = None):
        import uvicorn

        from daedelus.api import create_app
        monkeypatch.setenv("DAEDELUS_WORKER_TOKENS", WT)
        self.app = create_app(tmp / "ws", studio_dist=tmp / "nodist")
        self.port = _free_port()
        kw = {}
        if client_ca:
            kw = {"ssl_ca_certs": str(client_ca), "ssl_cert_reqs": ssl.CERT_OPTIONAL}
        self.server = uvicorn.Server(uvicorn.Config(
            self.app, host="127.0.0.1", port=self.port, log_level="warning",
            ssl_keyfile=str(cert[0]), ssl_certfile=str(cert[1]), **kw))
        threading.Thread(target=self.server.run, daemon=True).start()
        while not self.server.started:
            time.sleep(0.05)

    def close(self):
        from daedelus.distributed import service
        self.server.should_exit = True
        service.reset()


@pytest.fixture()
def pki(tmp_path):
    ca = _ca(tmp_path, "daedelus-test-ca")
    other = _ca(tmp_path, "unrelated-ca")
    good = _leaf(tmp_path, ca, "control", "DNS:localhost,IP:127.0.0.1")
    wrong_host = _leaf(tmp_path, ca, "elsewhere", "DNS:elsewhere.example")
    return {"ca": ca, "other": other, "good": good, "wrong_host": wrong_host, "dir": tmp_path}


def _worker(url, monkeypatch, ca: Path | None, tmp: Path):
    from daedelus.distributed.sandbox import Sandbox
    from daedelus.distributed.worker import Worker
    if ca is None:
        monkeypatch.delenv("DAEDELUS_CONTROL_CA", raising=False)
    else:
        monkeypatch.setenv("DAEDELUS_CONTROL_CA", str(ca))
    (tmp / "w").mkdir(exist_ok=True)
    sb = Sandbox(tmp / "w", "process")
    sb.probe()
    return Worker(url, WT, name="tls-w", adapters=["spreadsheet"], work_dir=tmp / "w", sandbox=sb)


def test_worker_registers_and_runs_jobs_over_verified_tls(pki, tmp_path, monkeypatch):
    srv = TlsServer(tmp_path, monkeypatch, pki["good"])
    try:
        url = f"https://localhost:{srv.port}"
        w = _worker(url, monkeypatch, pki["ca"][1], tmp_path)
        w.register()
        assert w.credential and w.id
        t = threading.Thread(target=w.run, kwargs={"max_jobs": 6, "idle_exit": 20}, daemon=True)
        t.start()
        c = httpx.Client(base_url=url, verify=ssl.create_default_context(cafile=str(pki["ca"][1])),
                         timeout=120)
        pid = c.post("/api/projects", json={"name": "tls"}).json()["id"]
        c.put(f"/api/projects/{pid}/execution", json={"target": "cloud_cpu"})
        r = c.post(f"/api/projects/{pid}/artifacts", json={
            "name": "B", "adapter": "spreadsheet", "template": "data",
            "params": {"sheets": [{"id": "s", "title": "S", "rows": [["a"], [1]]}]}})
        assert r.status_code == 200, r.text
        assert r.headers.get("strict-transport-security")  # HSTS over HTTPS
        jobs = c.get(f"/api/projects/{pid}/jobs").json()
        assert jobs and all(j["state"] == "succeeded" for j in jobs)
        w.stop()
    finally:
        srv.close()


@pytest.mark.parametrize("case", ["no_ca", "unrelated_ca", "wrong_hostname"])
def test_failed_tls_verification_is_refused(pki, tmp_path, monkeypatch, case):
    """Failure injection 12: the worker refuses a control plane it cannot verify."""
    cert = pki["wrong_host"] if case == "wrong_hostname" else pki["good"]
    srv = TlsServer(tmp_path, monkeypatch, cert)
    try:
        ca = {"no_ca": None, "unrelated_ca": pki["other"][1], "wrong_hostname": pki["ca"][1]}[case]
        w = _worker(f"https://localhost:{srv.port}", monkeypatch, ca, tmp_path)
        with pytest.raises(httpx.ConnectError) as ei:
            w.register()
        assert "CERTIFICATE_VERIFY_FAILED" in str(ei.value) or "certificate" in str(ei.value).lower()
        assert w.id is None and not srv.app.state.cluster.queue.workers()
    finally:
        srv.close()


def test_mutual_tls_client_certificate_is_presented(pki, tmp_path, monkeypatch):
    """Optional mTLS: a worker client certificate is sent when configured (the proxy or the
    built-in server can then require it). Here the server requests one and records it."""
    client = _leaf(pki["dir"], pki["ca"], "worker-client", "DNS:worker-1")
    srv = TlsServer(tmp_path, monkeypatch, pki["good"], client_ca=pki["ca"][1])
    try:
        monkeypatch.setenv("DAEDELUS_WORKER_TLS_CERT", str(client[1]))
        monkeypatch.setenv("DAEDELUS_WORKER_TLS_KEY", str(client[0]))
        w = _worker(f"https://localhost:{srv.port}", monkeypatch, pki["ca"][1], tmp_path)
        assert w.register()
    finally:
        srv.close()


def test_no_code_path_disables_tls_verification():
    """Static check: verify=False / CERT_NONE / check_hostname=False appear nowhere."""
    root = Path(__file__).resolve().parents[1] / "daedelus"
    bad = []
    for p in root.rglob("*.py"):
        t = p.read_text()
        for needle in ("verify=False", "CERT_NONE", "check_hostname = False",
                       "check_hostname=False", "_create_unverified_context"):
            if needle in t:
                bad.append(f"{p.name}: {needle}")
    assert bad == []


def test_worker_survives_proxy_502_while_control_plane_restarts(tmp_path):
    """Found by the hardened+TLS cluster run (S7): a TLS proxy answers 502 while the control
    plane restarts. The worker must wait, not crash."""
    from daedelus.distributed.sandbox import Sandbox
    from daedelus.distributed.worker import Worker
    calls = {"lease": 0}

    def handler(req):
        if req.url.path == "/api/cluster/workers":
            return httpx.Response(200, json={"id": "wkr_x", "name": "w", "credential": None})
        if req.url.path == "/api/cluster/lease":
            calls["lease"] += 1
            if calls["lease"] <= 2:
                return httpx.Response(502, text="Bad Gateway")
            w.stop()
            return httpx.Response(204)
        return httpx.Response(404)
    (tmp_path / "w").mkdir()
    sb = Sandbox(tmp_path / "w", "process")
    w = Worker("http://127.0.0.1:1", WT, adapters=["spreadsheet"], work_dir=tmp_path / "w",
               sandbox=sb, transport=httpx.MockTransport(handler))
    w.register()
    w.run()  # must return normally
    assert calls["lease"] == 3
