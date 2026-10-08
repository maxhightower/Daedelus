import { describe, expect, it } from "vitest";
import { keyLabel, short, targetLabel } from "../components/common";
import type { Artifact } from "../types";
import { targetKey } from "../types";

const art: Artifact = {
  id: "art_1",
  name: "Table",
  artifact_type: "model3d",
  adapter: "blender",
  native_dir: "x",
  entry: "model.blend",
  metadata: {},
  components: [
    { id: "table", name: "Table", kind: "group", metadata: {} },
    { id: "legs", name: "Legs", kind: "group", parent_id: "table", metadata: {} },
    { id: "leg_fl", name: "Leg FL", kind: "mesh", parent_id: "legs", metadata: {} },
  ],
};

describe("target selectors", () => {
  it("builds the same keys as the backend", () => {
    expect(targetKey({ scope: "project" })).toBe("project");
    expect(targetKey({ scope: "artifact", artifact_id: "art_1" })).toBe("artifact:art_1");
    expect(targetKey({ scope: "component", artifact_id: "art_1", component_id: "legs" })).toBe("artifact:art_1#legs");
  });
  it("labels component paths through the hierarchy", () => {
    expect(targetLabel({ scope: "component", artifact_id: "art_1", component_id: "leg_fl" }, [art])).toBe(
      "Table → Table → Legs → Leg FL",
    );
    expect(keyLabel("artifact:art_1#legs", [art])).toBe("Table → Table → Legs");
    expect(keyLabel("project", [art])).toBe("Project");
  });
  it("shortens ids", () => {
    expect(short("exe_abcdef123456")).toBe("abcdef12");
  });
});
