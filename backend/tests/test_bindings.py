"""Source-binding resolution: scope inheritance, cascading aspects, overrides, conflicts."""

from __future__ import annotations

from daedelus import ingest
from daedelus.bindings import resolve
from daedelus.models import (
    Artifact,
    Component,
    Constraint,
    RoleProfile,
    SourceBinding,
    TargetSelector,
)


def _artifact(store):
    a = Artifact(name="Thing", artifact_type="model3d", adapter="blender", native_dir="x",
                 entry="x", components=[
                     Component(id="root", name="Root", kind="group"),
                     Component(id="body", name="Body", kind="mesh", parent_id="root"),
                     Component(id="arm", name="Arm", kind="group", parent_id="root"),
                     Component(id="hand", name="Hand", kind="mesh", parent_id="arm")])
    return store.save_artifact(a)


def comp(a, cid):
    return TargetSelector(scope="component", artifact_id=a.id, component_id=cid)


def test_one_source_many_bindings_and_scopes(store):
    a = _artifact(store)
    s = ingest.register_text(store, "ref", "base_color: #ff0000")
    b1 = store.save_binding(SourceBinding(source_id=s.id, role="reference", aspects=["color"],
                                          target=comp(a, "hand")))
    b2 = store.save_binding(SourceBinding(source_id=s.id, role="evaluation", aspects=["color"],
                                          target=TargetSelector(scope="project")))
    assert {b.id for b in store.list_bindings(s.id)} == {b1.id, b2.id}
    ctx = resolve(store, comp(a, "hand"))
    rel = {e.binding.id: e.relation for e in ctx.entries}
    assert rel == {b1.id: "self", b2.id: "inherited"}
    ctx_body = resolve(store, comp(a, "body"))
    assert {e.binding.id for e in ctx_body.entries} == {b2.id}  # hand binding not in body's scope


def test_cascading_vs_anchor_aspects(store):
    a = _artifact(store)
    s = ingest.register_text(store, "s", "x")
    geo = store.save_binding(SourceBinding(source_id=s.id, role="reference",
                                           aspects=["geometry"], target=comp(a, "arm")))
    col = store.save_binding(SourceBinding(source_id=s.id, role="reference",
                                           aspects=["color"], target=comp(a, "arm")))
    ctx = resolve(store, comp(a, "hand"))
    e = {x.binding.id: x for x in ctx.entries}
    assert not e[geo.id].applies and "do not cascade" in e[geo.id].notes[0]
    assert e[col.id].applies and e[col.id].cascading_aspects == ["color"]
    # from the root, the arm bindings are "descendant": shown but applied at their own unit
    root = {x.binding.id: x for x in resolve(store, comp(a, "root")).entries}
    assert root[geo.id].relation == "descendant" and not root[geo.id].applies


def test_artifact_scope_is_own_for_root(store):
    a = _artifact(store)
    s = ingest.register_text(store, "s", "x")
    b = store.save_binding(SourceBinding(source_id=s.id, role="reference", aspects=["geometry"],
                                         target=TargetSelector(scope="artifact",
                                                               artifact_id=a.id)))
    ctx = resolve(store, comp(a, "root"))
    assert ctx.entries[0].binding.id == b.id and ctx.entries[0].relation == "self"
    assert ctx.entries[0].applies


def test_more_specific_soft_overrides_inherited(store):
    a = _artifact(store)
    s1 = ingest.register_text(store, "global", "x")
    s2 = ingest.register_text(store, "local", "y")
    g = store.save_binding(SourceBinding(source_id=s1.id, role="reference", aspects=["color"],
                                         target=TargetSelector(scope="project")))
    loc = store.save_binding(SourceBinding(source_id=s2.id, role="inspiration",
                                           aspects=["color"], target=comp(a, "hand")))
    ctx = resolve(store, comp(a, "hand"))
    e = {x.binding.id: x for x in ctx.entries}
    assert loc.id in e[g.id].overridden_by
    assert not e[g.id].applies
    assert ctx.conflicts == []


def test_hard_inherited_not_overridden_without_permission(store):
    a = _artifact(store)
    s1 = ingest.register_text(store, "global", "x")
    s2 = ingest.register_text(store, "local", "y")
    store.save_binding(SourceBinding(source_id=s1.id, role="reference", aspects=["color"],
                                     constraint="hard", target=TargetSelector(scope="project")))
    store.save_binding(SourceBinding(source_id=s2.id, role="reference", aspects=["color"],
                                     constraint="hard", priority=5, target=comp(a, "hand")))
    ctx = resolve(store, comp(a, "hand"))
    assert ctx.blocking_conflicts and "does not permit override" in ctx.conflicts[0].detail


def test_hard_override_when_permitted_and_higher_priority(store):
    a = _artifact(store)
    s1 = ingest.register_text(store, "global", "x")
    s2 = ingest.register_text(store, "local", "y")
    g = store.save_binding(SourceBinding(source_id=s1.id, role="reference", aspects=["color"],
                                         constraint="hard", allow_override=True,
                                         target=TargetSelector(scope="project")))
    store.save_binding(SourceBinding(source_id=s2.id, role="reference", aspects=["color"],
                                     constraint="hard", priority=5, target=comp(a, "hand")))
    ctx = resolve(store, comp(a, "hand"))
    assert not ctx.blocking_conflicts
    assert next(x for x in ctx.entries if x.binding.id == g.id).overridden_by


def test_constraint_conflicts(store):
    a = _artifact(store)
    s = ingest.register_text(store, "c", "x")
    store.save_binding(SourceBinding(source_id=s.id, role="constraint", constraint="hard",
                                     constraints=[Constraint(property="height", op="preserve")],
                                     target=comp(a, "arm")))
    store.save_binding(SourceBinding(source_id=s.id, role="constraint", constraint="hard",
                                     constraints=[Constraint(property="height", op="eq",
                                                             value=2.0)],
                                     target=comp(a, "hand")))
    ctx = resolve(store, comp(a, "hand"))
    assert len(ctx.blocking_conflicts) == 1
    assert ctx.blocking_conflicts[0].property == "height"
    # compatible ranges do not conflict
    ctx2 = resolve(store, comp(a, "body"))
    assert ctx2.blocking_conflicts == []


def test_soft_constraint_loses_to_hard(store):
    a = _artifact(store)
    s = ingest.register_text(store, "c", "x")
    store.save_binding(SourceBinding(source_id=s.id, role="constraint", constraint="hard",
                                     constraints=[Constraint(property="width", op="lte",
                                                             value=1.0)],
                                     target=TargetSelector(scope="project")))
    store.save_binding(SourceBinding(source_id=s.id, role="constraint", constraint="soft",
                                     constraints=[Constraint(property="width", op="gte",
                                                             value=2.0)],
                                     target=comp(a, "hand")))
    ctx = resolve(store, comp(a, "hand"))
    assert not ctx.blocking_conflicts
    assert ctx.conflicts[0].resolved and "hard constraint" in ctx.conflicts[0].resolution
    active = [c for c in ctx.constraints if c["active"]]
    assert [c["constraint"]["op"] for c in active] == ["lte"]


def test_hard_directive_conflict(store):
    a = _artifact(store)
    s1 = ingest.register_text(store, "g1", "roughness: 0.2")
    s2 = ingest.register_text(store, "g2", "roughness: 0.9")
    store.save_binding(SourceBinding(source_id=s1.id, role="guideline", constraint="hard",
                                     target=TargetSelector(scope="project")))
    store.save_binding(SourceBinding(source_id=s2.id, role="guideline", constraint="hard",
                                     target=comp(a, "hand")))
    ctx = resolve(store, comp(a, "hand"))
    assert any(c.property == "directive:roughness" and not c.resolved for c in ctx.conflicts)


def test_exclusions(store):
    a = _artifact(store)
    s = ingest.register_text(store, "s", "x")
    b = store.save_binding(SourceBinding(source_id=s.id, role="reference", aspects=["color"],
                                         target=comp(a, "root"), exclusions=["arm"]))
    ctx = resolve(store, comp(a, "hand"))
    e = next(x for x in ctx.entries if x.binding.id == b.id)
    assert not e.applies and "excluded" in " ".join(e.notes)


def test_role_semantics_and_unknown_roles(store):
    a = _artifact(store)
    p = store.get_project()
    p.settings.roles.append(RoleProfile(name="mood board", use="blend", weight=0.3))
    store.save_project(p)
    s = ingest.register_text(store, "s", "x")
    store.save_binding(SourceBinding(source_id=s.id, role="mood board", aspects=["color"],
                                     strength=1.0, target=comp(a, "hand")))
    store.save_binding(SourceBinding(source_id=s.id, role="whatever", aspects=["color"],
                                     target=comp(a, "hand")))
    ctx = resolve(store, comp(a, "hand"))
    by_role = {e.binding.role: e for e in ctx.entries}
    assert by_role["mood board"].role_use == "blend"
    assert by_role["mood board"].effective_weight == 0.3
    assert by_role["whatever"].role_use == "context" and by_role["whatever"].effective_weight == 0
    assert any("no profile" in w for w in ctx.warnings)


def test_failed_source_is_not_applied(store):
    a = _artifact(store)
    s = ingest.register_bytes(store, b"garbage", "x.png")
    b = store.save_binding(SourceBinding(source_id=s.id, role="reference", aspects=["color"],
                                         target=comp(a, "hand")))
    ctx = resolve(store, comp(a, "hand"))
    e = next(x for x in ctx.entries if x.binding.id == b.id)
    assert not e.applies and ctx.warnings


def test_target_selector_keys_round_trip():
    for t in (TargetSelector(), TargetSelector(scope="artifact", artifact_id="art_1"),
              TargetSelector(scope="component", artifact_id="art_1", component_id="c:x")):
        assert TargetSelector.parse(t.key()) == t
