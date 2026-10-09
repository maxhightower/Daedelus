"""Print a board's items (type, frame membership, position, size, z) for layout checks.

    python tools/native_validation/board_items.py http://127.0.0.1:PORT PROJECT_ID [types...]
"""
from __future__ import annotations

import sys

import httpx


def main(base: str, pid: str, types: list[str]) -> None:
    c = httpx.Client(base_url=base, timeout=30)
    names = {s["id"]: s["name"] for s in c.get(f"/api/projects/{pid}/sources").json()}
    names |= {a["id"]: a["name"] for a in c.get(f"/api/projects/{pid}/artifacts").json()}
    board = c.get(f"/api/projects/{pid}/boards").json()[0]
    full = c.get(f"/api/projects/{pid}/boards/{board['id']}").json()
    print("board revision", full.get("revision"))
    for i in full["items"]:
        if types and i["item_type"] not in types:
            continue
        ref = i.get("resource_ref") or {}
        label = (i.get("presentation_state") or {}).get("title") or names.get(ref.get("id"), "")
        p, s = i["position"], i["size"]
        print(f"{i['item_type']:13} {i['id']:22} group={str(i.get('group_id')):22} "
              f"pos=({p['x']:.0f},{p['y']:.0f}) size=({s['width']:.0f}x{s['height']:.0f}) "
              f"z={i.get('z_index')} {label}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3:])
