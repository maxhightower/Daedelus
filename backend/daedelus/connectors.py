"""Office connectors (V1.2): Microsoft Graph (OneDrive / SharePoint), Google Workspace (Drive)
and a local LibreOffice converter.

Contract (``OfficeConnector``):

- ``status()``     -> configured / available, with the reason when not;
- ``get(id)``      -> ``RemoteFile`` metadata including the remote *version* (Graph eTag,
                      Drive ``version``) used for conflict detection;
- ``download(id)`` -> the file as OOXML (Google-native Docs/Sheets/Slides are exported);
- ``upload(id, path, expected_version)`` -> new ``RemoteFile``; raises ``ConnectorConflict``
                      when the remote changed since ``expected_version``.

Security: requests go only to fixed API hosts (and, for Graph downloads, to Microsoft content
hosts it redirects to); remote ids are validated before they are put into a URL; tokens come
from the environment and are never stored in the project. Arbitrary URLs are never fetched.

Verification status: the HTTP contracts are tested against recorded-shape fixtures with a mock
transport. **No live tenant / Google account has been called** (no credentials here).
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, Field

from .adapters.office import common as oc

ID_RE = re.compile(r"^[A-Za-z0-9!_\-.]{1,256}$")
OOXML = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}
ADAPTER_FOR = {"xlsx": "spreadsheet", "docx": "document", "pptx": "presentation"}
GOOGLE_NATIVE = {
    "application/vnd.google-apps.spreadsheet": "xlsx",
    "application/vnd.google-apps.document": "docx",
    "application/vnd.google-apps.presentation": "pptx",
}


class ConnectorError(Exception):
    pass


class ConnectorBlocked(ConnectorError):
    """The connector cannot run here (no credentials / tool missing)."""


class ConnectorConflict(ConnectorError):
    def __init__(self, msg: str, remote: "RemoteFile | None" = None):
        super().__init__(msg)
        self.remote = remote


class RemoteFile(BaseModel):
    connector: str
    remote_id: str
    name: str
    mime_type: str = ""
    format: str | None = None  # xlsx / docx / pptx after download
    version: str | None = None
    size: int | None = None
    modified: str | None = None
    web_url: str | None = None
    exported: bool = False  # converted from a Google-native format on download
    metadata: dict[str, Any] = Field(default_factory=dict)


class ConnectorStatus(BaseModel):
    name: str
    title: str
    configured: bool
    available: bool
    reason: str | None = None
    verification: str = "fixture-tested (no live account)"


def _fmt_of(name: str, mime: str) -> str | None:
    ext = Path(name).suffix.lower().lstrip(".")
    if ext in OOXML:
        return ext
    return next((k for k, v in OOXML.items() if v == mime), None)


class OfficeConnector:
    name = "base"
    title = "Connector"
    hosts: tuple[str, ...] = ()
    redirect_suffixes: tuple[str, ...] = ()
    token_env = ""

    def __init__(self, transport: httpx.BaseTransport | None = None, token: str | None = None):
        self._transport = transport
        self._token = token

    # -- plumbing -----------------------------------------------------------------
    def token(self) -> str | None:
        return self._token or (os.environ.get(self.token_env) if self.token_env else None)

    def status(self) -> ConnectorStatus:
        t = self.token()
        return ConnectorStatus(name=self.name, title=self.title, configured=bool(t),
                               available=bool(t),
                               reason=None if t else f"blocked: {self.token_env} is not set")

    def _client(self) -> httpx.Client:
        tok = self.token()
        if not tok:
            raise ConnectorBlocked(f"{self.title}: no credentials ({self.token_env} not set)")
        return httpx.Client(transport=self._transport, timeout=60,
                            headers={"Authorization": f"Bearer {tok}"}, follow_redirects=False)

    @staticmethod
    def _check_id(remote_id: str) -> str:
        if not ID_RE.match(remote_id or ""):
            raise ConnectorError(f"invalid remote id: {remote_id!r}")
        return remote_id

    def _allowed(self, url: str, redirect: bool = False) -> bool:
        u = urlparse(url)
        if u.scheme != "https" or not u.hostname:
            return False
        if u.hostname in self.hosts:
            return True
        return redirect and any(u.hostname.endswith(s) for s in self.redirect_suffixes)

    def _request(self, c: httpx.Client, method: str, url: str, **kw) -> httpx.Response:
        if not self._allowed(url):
            raise ConnectorError(f"refused request to non-allowlisted URL: {url}")
        r = c.request(method, url, **kw)
        hops = 0
        while r.status_code in (301, 302, 303, 307, 308) and method == "GET":
            loc = r.headers.get("location", "")
            if hops >= 3 or not self._allowed(loc, redirect=True):
                raise ConnectorError(f"refused redirect to {loc or '(none)'}")
            # content hosts get pre-authenticated URLs: never forward the bearer token
            r = httpx.Client(transport=self._transport, timeout=60).get(loc)
            hops += 1
        if r.status_code in (401, 403):
            raise ConnectorBlocked(f"{self.title}: not authorised ({r.status_code})")
        if r.status_code == 404:
            raise ConnectorError(f"{self.title}: not found")
        return r

    # -- contract -------------------------------------------------------------------
    def get(self, remote_id: str) -> RemoteFile:
        raise NotImplementedError

    def download(self, remote_id: str, dest_dir: Path) -> tuple[RemoteFile, Path]:
        raise NotImplementedError

    def upload(self, remote_id: str, path: Path, expected_version: str | None) -> RemoteFile:
        raise NotImplementedError


class GraphConnector(OfficeConnector):
    """Microsoft Graph drive items (OneDrive / SharePoint document libraries)."""

    name = "msgraph"
    title = "Microsoft 365 (Graph)"
    hosts = ("graph.microsoft.com",)
    redirect_suffixes = (".sharepoint.com", ".1drv.com", ".files.1drv.com", ".svc.ms")
    token_env = "DAEDELUS_MSGRAPH_TOKEN"
    base = "https://graph.microsoft.com/v1.0/me/drive/items"

    def _remote(self, d: dict[str, Any]) -> RemoteFile:
        mime = (d.get("file") or {}).get("mimeType", "")
        return RemoteFile(connector=self.name, remote_id=d["id"], name=d.get("name", ""),
                          mime_type=mime, format=_fmt_of(d.get("name", ""), mime),
                          version=d.get("eTag"), size=d.get("size"),
                          modified=d.get("lastModifiedDateTime"), web_url=d.get("webUrl"))

    def get(self, remote_id: str) -> RemoteFile:
        rid = self._check_id(remote_id)
        with self._client() as c:
            r = self._request(c, "GET", f"{self.base}/{rid}", params={
                "$select": "id,name,eTag,size,file,lastModifiedDateTime,webUrl"})
            r.raise_for_status()
            return self._remote(r.json())

    def download(self, remote_id: str, dest_dir: Path) -> tuple[RemoteFile, Path]:
        meta = self.get(remote_id)
        if meta.format is None:
            raise ConnectorError(f"'{meta.name}' is not an xlsx/docx/pptx file")
        with self._client() as c:
            r = self._request(c, "GET", f"{self.base}/{meta.remote_id}/content")
            r.raise_for_status()
        dest_dir.mkdir(parents=True, exist_ok=True)
        out = dest_dir / f"download.{meta.format}"
        out.write_bytes(r.content)
        return meta, out

    def upload(self, remote_id: str, path: Path, expected_version: str | None) -> RemoteFile:
        rid = self._check_id(remote_id)
        headers = {"Content-Type": OOXML.get(path.suffix.lstrip("."), "application/octet-stream")}
        if expected_version:
            headers["If-Match"] = expected_version  # atomic: Graph answers 412 on mismatch
        with self._client() as c:
            r = self._request(c, "PUT", f"{self.base}/{rid}/content", content=path.read_bytes(),
                              headers=headers)
            if r.status_code == 412:
                raise ConnectorConflict("remote file changed since it was imported "
                                        "(eTag mismatch); nothing was overwritten")
            r.raise_for_status()
            return self._remote(r.json())


class GoogleDriveConnector(OfficeConnector):
    """Google Drive files; Google-native Docs/Sheets/Slides are exported to OOXML."""

    name = "google"
    title = "Google Workspace (Drive)"
    hosts = ("www.googleapis.com",)
    token_env = "DAEDELUS_GOOGLE_TOKEN"
    base = "https://www.googleapis.com/drive/v3/files"
    upload_base = "https://www.googleapis.com/upload/drive/v3/files"
    fields = "id,name,mimeType,version,size,modifiedTime,webViewLink,md5Checksum"

    def _remote(self, d: dict[str, Any]) -> RemoteFile:
        mime = d.get("mimeType", "")
        native = GOOGLE_NATIVE.get(mime)
        return RemoteFile(connector=self.name, remote_id=d["id"], name=d.get("name", ""),
                          mime_type=mime, format=native or _fmt_of(d.get("name", ""), mime),
                          version=str(d["version"]) if d.get("version") is not None else None,
                          size=int(d["size"]) if d.get("size") else None,
                          modified=d.get("modifiedTime"), web_url=d.get("webViewLink"),
                          exported=bool(native), metadata={"md5": d.get("md5Checksum")})

    def get(self, remote_id: str) -> RemoteFile:
        rid = self._check_id(remote_id)
        with self._client() as c:
            r = self._request(c, "GET", f"{self.base}/{rid}", params={"fields": self.fields})
            r.raise_for_status()
            return self._remote(r.json())

    def download(self, remote_id: str, dest_dir: Path) -> tuple[RemoteFile, Path]:
        meta = self.get(remote_id)
        if meta.format is None:
            raise ConnectorError(f"'{meta.name}' ({meta.mime_type}) is not a supported "
                                 "Office/Google document")
        with self._client() as c:
            if meta.exported:
                r = self._request(c, "GET", f"{self.base}/{meta.remote_id}/export",
                                  params={"mimeType": OOXML[meta.format]})
            else:
                r = self._request(c, "GET", f"{self.base}/{meta.remote_id}",
                                  params={"alt": "media"})
            r.raise_for_status()
        dest_dir.mkdir(parents=True, exist_ok=True)
        out = dest_dir / f"download.{meta.format}"
        out.write_bytes(r.content)
        return meta, out

    def upload(self, remote_id: str, path: Path, expected_version: str | None) -> RemoteFile:
        current = self.get(remote_id)
        if current.exported:
            # overwriting a Google-native file with OOXML bytes would replace it with an
            # uploaded copy; refuse instead of changing the file's type behind the user's back
            raise ConnectorError("the remote file is a Google-native document; publishing back "
                                 "into it is not supported (export a copy instead)")
        if expected_version and current.version != expected_version:
            raise ConnectorConflict(f"remote version {current.version} != imported version "
                                    f"{expected_version}; nothing was overwritten", current)
        # Drive v3 offers no precondition header for media updates: this check-then-write
        # leaves a short race window, documented in V1_2_ARCHITECTURE.md
        with self._client() as c:
            r = self._request(c, "PATCH", f"{self.upload_base}/{current.remote_id}",
                              params={"uploadType": "media", "fields": self.fields},
                              content=path.read_bytes(),
                              headers={"Content-Type": OOXML.get(path.suffix.lstrip("."),
                                                                 "application/octet-stream")})
            r.raise_for_status()
            return self._remote(r.json())


class LibreOfficeConnector(OfficeConnector):
    """Local ODF <-> OOXML conversion through headless LibreOffice (on copies)."""

    name = "libreoffice"
    title = "LibreOffice (local conversion)"
    ODF = {".ods": ("xlsx", "xlsx:Calc MS Excel 2007 XML"),
           ".odt": ("docx", "docx:MS Word 2007 XML"),
           ".odp": ("pptx", "pptx:Impress MS PowerPoint 2007 XML")}
    EXPORT = {"xlsx": ("ods", "ods"), "docx": ("odt", "odt"), "pptx": ("odp", "odp")}

    def status(self) -> ConnectorStatus:
        ok = oc.soffice() is not None
        return ConnectorStatus(name=self.name, title=self.title, configured=ok, available=ok,
                               reason=None if ok else "blocked: LibreOffice (soffice) not found",
                               verification="integration-tested with LibreOffice")

    def to_ooxml(self, src: Path, dest_dir: Path) -> tuple[RemoteFile, Path]:
        if oc.soffice() is None:
            raise ConnectorBlocked("LibreOffice not installed")
        ext = src.suffix.lower()
        if ext not in self.ODF:
            raise ConnectorError(f"not an OpenDocument file: {src.name}")
        fmt, filt = self.ODF[ext]
        with tempfile.TemporaryDirectory(prefix="dd_odf_") as td:
            out = oc.lo_convert(src, filt, Path(td))
            if out is None:
                raise ConnectorError(f"LibreOffice could not convert {src.name}")
            dest_dir.mkdir(parents=True, exist_ok=True)
            final = dest_dir / f"converted.{fmt}"
            shutil.copy2(out, final)
        return RemoteFile(connector=self.name, remote_id=src.name, name=src.name, format=fmt,
                          exported=True, size=src.stat().st_size), final

    def from_ooxml(self, src: Path, dest_dir: Path) -> Path:
        if oc.soffice() is None:
            raise ConnectorBlocked("LibreOffice not installed")
        fmt = src.suffix.lower().lstrip(".")
        if fmt not in self.EXPORT:
            raise ConnectorError(f"cannot convert {src.name} to OpenDocument")
        ext, filt = self.EXPORT[fmt]
        with tempfile.TemporaryDirectory(prefix="dd_odf_") as td:
            out = oc.lo_convert(src, filt, Path(td))
            if out is None:
                raise ConnectorError(f"LibreOffice could not convert {src.name}")
            dest_dir.mkdir(parents=True, exist_ok=True)
            final = dest_dir / f"{src.stem}.{ext}"
            shutil.copy2(out, final)
        return final


def registry(transport: httpx.BaseTransport | None = None) -> dict[str, OfficeConnector]:
    return {c.name: c for c in (GraphConnector(transport), GoogleDriveConnector(transport),
                                LibreOfficeConnector())}


# --------------------------------------------------------------------- project integration
def import_remote(store, connector: OfficeConnector, remote_id: str, name: str | None = None):
    """Download a remote document and create an editable artifact linked to it."""
    from .artifacts import create_artifact

    with tempfile.TemporaryDirectory(prefix="dd_imp_") as td:
        meta, path = connector.download(remote_id, Path(td))
        art, rev = create_artifact(
            store, name=name or Path(meta.name).stem or meta.remote_id,
            adapter=ADAPTER_FOR[meta.format], template="import",
            params={"path": str(path), "name": name or meta.name},
            metadata={"remote": {**meta.model_dump(), "imported_revision_id": None}})
    art.metadata["remote"]["imported_revision_id"] = rev.id
    store.save_artifact(art)
    return art, rev


def publish_remote(store, artifact_id: str, connector: OfficeConnector) -> RemoteFile:
    """Upload the head revision; refuses when the remote changed since import/last publish."""
    from .artifacts import native_path

    art = store.get_artifact(artifact_id)
    link = art.metadata.get("remote") or {}
    if link.get("connector") != connector.name:
        raise ConnectorError(f"'{art.name}' is not linked to {connector.title}")
    new = connector.upload(link["remote_id"], native_path(store, art) / art.entry,
                           link.get("version"))
    art.metadata["remote"] = {**link, **new.model_dump(), "published_revision_id":
                              art.head_revision_id}
    store.save_artifact(art)
    return new
