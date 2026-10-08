"""Spatial boards: persistent canvas layouts that *present* project resources.

A board never owns domain data. Each ``CanvasItem`` is a *view* of a typed
resource (an artifact, a source, a workflow node, ...); several items may view
the same resource with different presentation state (camera, file, layer).

Connections come in two kinds:

* **stored** connections that only exist on the board (``dependency``
  information and ``annotation`` links), and
* **derived** connections computed on every read from the domain model:
  ``reference`` connections from ``SourceBinding``s and ``execution``
  connections from workflow edges. Their presentation preferences may be stored,
  keyed by ``domain_ref``, but their existence and meaning come from the domain.
  Editing them edits the binding / workflow, never the board.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .models import new_id, now_iso
from .store import ProjectStore

BOARD_SCHEMA_VERSION = 1
MIGRATION_KEY = "board_migration_version"
MIGRATION_VERSION = 1

ResourceKind = Literal["artifact", "source", "workflow", "workflow_node", "execution", "note",
                       "frame", "none"]
ItemType = Literal["artifact_view", "source", "operation", "note", "frame", "workflow"]
ConnectionType = Literal["reference", "execution", "dependency", "annotation"]


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ResourceRef(_M):
    """Typed pointer to the resource an item presents."""

    kind: ResourceKind
    id: str | None = None  # artifact / source / workflow / execution id
    workflow_id: str | None = None  # for workflow_node
    node_id: str | None = None  # for workflow_node

    def key(self) -> str:
        if self.kind == "workflow_node":
            return f"workflow_node:{self.workflow_id}:{self.node_id}"
        return f"{self.kind}:{self.id}"


class Vec2(_M):
    x: float = 0.0
    y: float = 0.0


class Size(_M):
    width: float = 280.0
    height: float = 200.0


class CanvasItem(_M):
    id: str = Field(default_factory=lambda: new_id("item"))
    item_type: ItemType
    resource_ref: ResourceRef
    position: Vec2 = Field(default_factory=Vec2)
    size: Size = Field(default_factory=Size)
    z_index: int = 0
    # view-specific state: camera, open file, selected layer, pinned references, note text...
    presentation_state: dict[str, Any] = Field(default_factory=dict)
    group_id: str | None = None  # id of the frame item containing this item
    collapsed: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class Anchor(_M):
    """Where a connection attaches. ``component_id`` anchors stay meaningful when the camera
    moves because they refer to persistent component ids, not pixels."""

    handle: str | None = None
    component_id: str | None = None
    port: str | None = None


class DomainRef(_M):
    kind: Literal["binding", "workflow_edge", "artifact_dependency", "none"] = "none"
    id: str | None = None  # binding id / edge id / dependency id
    workflow_id: str | None = None


class CanvasConnection(_M):
    id: str = Field(default_factory=lambda: new_id("conn"))
    connection_type: ConnectionType
    source_item_id: str
    target_item_id: str
    source_anchor: Anchor = Field(default_factory=Anchor)
    target_anchor: Anchor = Field(default_factory=Anchor)
    domain_ref: DomainRef = Field(default_factory=DomainRef)
    presentation_state: dict[str, Any] = Field(default_factory=dict)  # label, hidden...
    derived: bool = False  # computed from the domain model (not stored)


class Viewport(_M):
    x: float = 0.0
    y: float = 0.0
    zoom: float = 1.0


class SavedView(_M):
    id: str = Field(default_factory=lambda: new_id("view"))
    name: str
    viewport: Viewport


class CanvasBoard(_M):
    id: str = Field(default_factory=lambda: new_id("board"))
    project_id: str
    name: str
    schema_version: int = BOARD_SCHEMA_VERSION
    revision: int = 0  # increments on every save (optimistic concurrency)
    items: list[CanvasItem] = Field(default_factory=list)
    connections: list[CanvasConnection] = Field(default_factory=list)  # stored kinds only
    saved_views: list[SavedView] = Field(default_factory=list)
    viewport: Viewport = Field(default_factory=Viewport)
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)

    @property
    def frames(self) -> list[CanvasItem]:
        return [i for i in self.items if i.item_type == "frame"]


class BoardConflict(Exception):
    pass


# ---------------------------------------------------------------------------
# persistence
# ---------------------------------------------------------------------------

def list_boards(store: ProjectStore) -> list[CanvasBoard]:
    return store.list_docs("boards", CanvasBoard)


def get_board(store: ProjectStore, board_id: str) -> CanvasBoard:
    b = store.get_doc("boards", board_id, CanvasBoard)
    if b is None:
        raise LookupError(f"board not found: {board_id}")
    return b


def save_board(store: ProjectStore, board: CanvasBoard, *, expected_revision: int | None = None
               ) -> CanvasBoard:
    """Persist a board. Stored connections must be presentation-only kinds."""
    with store.lock():
        cur = store.get_doc("boards", board.id, CanvasBoard)
        if cur is not None and expected_revision is not None and cur.revision != expected_revision:
            raise BoardConflict(f"board changed elsewhere (revision {cur.revision}, "
                                f"expected {expected_revision})")
        ids = {i.id for i in board.items}
        if len(ids) != len(board.items):
            raise ValueError("duplicate canvas item ids")
        for it in board.items:
            if it.group_id and it.group_id not in ids:
                it.group_id = None
        stored = []
        for c in board.connections:
            if c.derived:
                continue  # derived connections are recomputed, never stored
            if c.connection_type in ("reference", "execution"):
                # only presentation preferences for domain-backed connections may be stored
                if c.domain_ref.kind == "none":
                    raise ValueError(f"{c.connection_type} connections must reference the domain "
                                     "model (binding / workflow edge)")
            if c.source_item_id not in ids or c.target_item_id not in ids:
                continue  # endpoint removed: drop the dangling connection
            stored.append(c)
        board.connections = stored
        board.revision = (cur.revision if cur else board.revision) + 1
        board.updated_at = now_iso()
        store.put_doc("boards", board.id, board)
        return board


def delete_board(store: ProjectStore, board_id: str) -> None:
    store.delete_doc("boards", board_id)


# ---------------------------------------------------------------------------
# derived connections
# ---------------------------------------------------------------------------

def derived_connections(store: ProjectStore, board: CanvasBoard) -> list[CanvasConnection]:
    by_res: dict[str, list[CanvasItem]] = {}
    for it in board.items:
        by_res.setdefault(it.resource_ref.key(), []).append(it)
    prefs = {(c.domain_ref.kind, c.domain_ref.id, c.source_item_id, c.target_item_id):
             c.presentation_state for c in board.connections if c.domain_ref.kind != "none"}
    out: list[CanvasConnection] = []
    # reference connections from source bindings
    for b in store.list_bindings():
        srcs = by_res.get(f"source:{b.source_id}", [])
        if not srcs or b.target.scope == "project":
            continue  # project-wide bindings are shown on the source item, not as N edges
        tgts = by_res.get(f"artifact:{b.target.artifact_id}", [])
        for s in srcs:
            for t in tgts:
                if t.item_type != "artifact_view":
                    continue
                comp = b.target.component_id if b.target.scope == "component" else None
                out.append(CanvasConnection(
                    id=f"ref:{b.id}:{s.id}:{t.id}", connection_type="reference",
                    source_item_id=s.id, target_item_id=t.id, derived=True,
                    source_anchor=Anchor(handle="ref-out"),
                    target_anchor=Anchor(handle=f"comp-in:{comp}" if comp else "ref-in",
                                         component_id=comp),
                    domain_ref=DomainRef(kind="binding", id=b.id),
                    presentation_state={**prefs.get(("binding", b.id, s.id, t.id), {}),
                                        "label": b.role, "enabled": b.enabled,
                                        "hard": b.constraint == "hard"}))
    # execution connections from workflow edges
    for wf in store.list_workflows():
        for e in wf.edges:
            srcs = by_res.get(f"workflow_node:{wf.id}:{e.source}", [])
            tgts = by_res.get(f"workflow_node:{wf.id}:{e.target}", [])
            for s in srcs:
                for t in tgts:
                    out.append(CanvasConnection(
                        id=f"exe:{wf.id}:{e.id}:{s.id}:{t.id}", connection_type="execution",
                        source_item_id=s.id, target_item_id=t.id, derived=True,
                        source_anchor=Anchor(handle=f"out:{e.source_port}", port=e.source_port),
                        target_anchor=Anchor(handle=f"in:{e.target_port}", port=e.target_port),
                        domain_ref=DomainRef(kind="workflow_edge", id=e.id, workflow_id=wf.id),
                        presentation_state={**prefs.get(("workflow_edge", e.id, s.id, t.id), {}),
                                            "label": e.source_port}))
    # dependency connections between artifact views (component-level links, V1.2)
    from . import dependencies as dep_mod

    for st in dep_mod.cached_status(store):
        srcs = [i for i in by_res.get(f"artifact:{st['source']['artifact_id']}", [])
                if i.item_type == "artifact_view"]
        tgts = [i for i in by_res.get(f"artifact:{st['target']['artifact_id']}", [])
                if i.item_type == "artifact_view"]
        for s in srcs:
            for t in tgts:
                out.append(CanvasConnection(
                    id=f"dep:{st['id']}:{s.id}:{t.id}", connection_type="dependency",
                    source_item_id=s.id, target_item_id=t.id, derived=True,
                    source_anchor=Anchor(handle="dep-out", component_id=st["source"]["component_id"]),
                    target_anchor=Anchor(handle="dep-in", component_id=st["target"]["component_id"]),
                    domain_ref=DomainRef(kind="artifact_dependency", id=st["id"]),
                    presentation_state={
                        **prefs.get(("artifact_dependency", st["id"], s.id, t.id), {}),
                        "label": f"{st['source']['component_id']} → {st['target']['component_id']}",
                        "status": st["status"], "stale": st["status"] != "synced"}))
    return out


def board_view(store: ProjectStore, board: CanvasBoard) -> dict[str, Any]:
    """Board + derived connections + resolution status of each item's resource."""
    missing = [i.id for i in board.items if not resource_exists(store, i.resource_ref)]
    d = board.model_dump()
    d["connections"] = [c.model_dump() for c in board.connections] + \
        [c.model_dump() for c in derived_connections(store, board)]
    d["missing_items"] = missing
    return d


def resource_exists(store: ProjectStore, ref: ResourceRef) -> bool:
    try:
        if ref.kind == "artifact":
            store.get_artifact(ref.id or "")
        elif ref.kind == "source":
            store.get_source(ref.id or "")
        elif ref.kind == "workflow":
            store.get_workflow(ref.id or "")
        elif ref.kind == "workflow_node":
            wf = store.get_workflow(ref.workflow_id or "")
            return any(n.id == ref.node_id for n in wf.nodes)
        elif ref.kind == "execution":
            store.get_execution(ref.id or "")
        return True
    except LookupError:
        return False


# ---------------------------------------------------------------------------
# default layout + migration of pre-V1 projects
# ---------------------------------------------------------------------------

def item_for(ref: ResourceRef, x: float, y: float, **kw: Any) -> CanvasItem:
    sizes = {"artifact": Size(width=420, height=340), "source": Size(width=240, height=210),
             "workflow_node": Size(width=230, height=150), "note": Size(width=240, height=140)}
    types = {"artifact": "artifact_view", "source": "source", "workflow_node": "operation",
             "note": "note", "frame": "frame", "workflow": "workflow"}
    return CanvasItem(item_type=types[ref.kind], resource_ref=ref,  # type: ignore[arg-type]
                      position=Vec2(x=x, y=y), size=kw.pop("size", sizes.get(ref.kind, Size())),
                      **kw)


def default_board(store: ProjectStore, name: str = "Main board") -> CanvasBoard:
    """Lay out every existing source, artifact and workflow of the project in named frames."""
    project = store.get_project()
    board = CanvasBoard(project_id=project.id, name=name)
    pad, gap = 40.0, 30.0

    def frame(title: str, x: float, y: float, w: float, h: float, color: str) -> CanvasItem:
        f = CanvasItem(item_type="frame", resource_ref=ResourceRef(kind="frame"),
                       position=Vec2(x=x, y=y), size=Size(width=w, height=h), z_index=-10,
                       presentation_state={"title": title, "color": color})
        board.items.append(f)
        return f

    arts = store.list_artifacts()
    srcs = store.list_sources()
    # artifacts (row)
    aw = len(arts) * (420 + gap) + pad * 2 - gap if arts else 500
    fa = frame("Artifacts", 0, 0, aw, 340 + pad * 2 + 30, "#3f6ca8")
    for i, a in enumerate(arts):
        board.items.append(item_for(ResourceRef(kind="artifact", id=a.id), pad + i * (420 + gap),
                                    pad + 30, group_id=fa.id))
    # sources (grid of 4 columns) below
    cols = 4
    rows = max(1, (len(srcs) + cols - 1) // cols)
    fs = frame("References", 0, fa.size.height + 60, cols * (240 + gap) + pad * 2 - gap,
               rows * (210 + gap) + pad * 2 + 30 - gap, "#a08033")
    for i, s in enumerate(srcs):
        board.items.append(item_for(ResourceRef(kind="source", id=s.id),
                                    pad + (i % cols) * (240 + gap),
                                    fs.position.y + pad + 30 + (i // cols) * (210 + gap),
                                    group_id=fs.id))
    # workflows, each in its own frame to the right, keeping the editor's relative layout
    x0 = max(fa.size.width, fs.size.width) + 120
    y = 0.0
    for wf in store.list_workflows():
        if not wf.nodes:
            continue
        xs = [n.position.get("x", 0) for n in wf.nodes]
        ys = [n.position.get("y", 0) for n in wf.nodes]
        sx, sy = 1.05, 0.62
        w = (max(xs) - min(xs)) * sx + 230 + pad * 2
        h = (max(ys) - min(ys)) * sy + 150 + pad * 2 + 30
        fw = frame(f"Workflow: {wf.name}", x0, y, w, h, "#7a5aa0")
        fw.resource_ref = ResourceRef(kind="workflow", id=wf.id)
        fw.item_type = "workflow"
        for n in wf.nodes:
            board.items.append(item_for(
                ResourceRef(kind="workflow_node", workflow_id=wf.id, node_id=n.id),
                x0 + pad + (n.position.get("x", 0) - min(xs)) * sx,
                y + pad + 30 + (n.position.get("y", 0) - min(ys)) * sy, group_id=fw.id))
        y += h + 60
    return board


def ensure_boards(store: ProjectStore) -> list[CanvasBoard]:
    """Idempotent, versioned migration: projects without boards get a default board once."""
    with store.lock():
        version = int(store.get_meta(MIGRATION_KEY) or 0)
        boards = list_boards(store)
        if version < 1:
            if not boards:
                boards = [save_board(store, default_board(store))]
            store.set_meta(MIGRATION_KEY, str(MIGRATION_VERSION))
        return boards


def place_resource(store: ProjectStore, board: CanvasBoard, ref: ResourceRef,
                   x: float | None = None, y: float | None = None, **kw: Any) -> CanvasItem:
    if not resource_exists(store, ref) and ref.kind not in ("note", "frame"):
        raise LookupError(f"resource not found: {ref.key()}")
    if x is None or y is None:
        right = max((i.position.x + i.size.width for i in board.items if not i.group_id),
                    default=0.0)
        x, y = right + 80, 0.0
    item = item_for(ref, x, y, **kw)
    board.items.append(item)
    return item
