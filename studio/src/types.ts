// Mirrors of the backend's Pydantic models (subset used by the studio).

export type Scope = "project" | "artifact" | "component";

export interface TargetSelector {
  scope: Scope;
  artifact_id?: string | null;
  component_id?: string | null;
}

export interface Processing {
  state: "pending" | "processing" | "ready" | "partial" | "failed" | "unsupported";
  extractor?: string | null;
  error?: string | null;
  warnings: string[];
}

export interface MediaSource {
  id: string;
  name: string;
  media_type: string;
  mime_type?: string | null;
  locator: { kind: string; path?: string | null; url?: string | null };
  content_hash?: string | null;
  metadata: Record<string, any>;
  extracted: Record<string, any>;
  preview_path?: string | null;
  processing: Processing;
  provenance: { origin: string; original_name?: string | null; url?: string | null; notes?: string | null };
  created_at: string;
  binding_count?: number;
  bindings?: Binding[];
}

export interface Constraint {
  property: string;
  op: "preserve" | "eq" | "lte" | "gte" | "range" | "forbid_change";
  value?: any;
  tolerance?: number;
  description?: string | null;
}

export interface Segment {
  kind: "time" | "page" | "region" | "lines";
  start?: number | null;
  end?: number | null;
  page?: number | null;
  region?: number[] | null;
  note?: string | null;
}

export interface Binding {
  id: string;
  source_id: string;
  role: string;
  aspects: string[];
  target: TargetSelector;
  instructions: string;
  priority: number;
  strength: number;
  constraint: "soft" | "hard";
  constraints: Constraint[];
  allow_override: boolean;
  segment?: Segment | null;
  exclusions: string[];
  enabled: boolean;
}

export interface RoleProfile {
  name: string;
  description: string;
  use: string;
  weight: number;
  builtin: boolean;
}

export interface Component {
  id: string;
  name: string;
  kind: string;
  parent_id?: string | null;
  native_ref?: string | null;
  metadata: Record<string, any>;
}

export interface Artifact {
  id: string;
  name: string;
  artifact_type: string;
  adapter: string;
  native_dir: string;
  entry: string;
  components: Component[];
  head_revision_id?: string | null;
  metadata: Record<string, any>;
}

export interface PlannedOperation {
  op: string;
  component_id?: string | null;
  params: Record<string, any>;
  derived_from: string[];
  rationale: string;
}

export interface Check {
  name: string;
  passed: boolean;
  detail: string;
  data: Record<string, any>;
}

export interface Revision {
  id: string;
  artifact_id: string;
  number: number;
  parent_revision_id?: string | null;
  created_at: string;
  message: string;
  execution_id?: string | null;
  node_id?: string | null;
  units: string[];
  snapshot_dir: string;
  previews: Record<string, string>;
  component_states: Record<string, string>;
  measurements: Record<string, any>;
  operations: PlannedOperation[];
  operation_results: { index: number; op: string; component_id?: string; status: string; detail: string }[];
  attribution: {
    binding_id: string;
    source_id: string;
    source_name: string;
    role: string;
    aspects: string[];
    anchor: string;
    unit: string;
    applied: boolean;
    operations: number[];
    interpretation: string;
  }[];
  changed_components: string[];
  validation?: { passed: boolean; checks: Check[] } | null;
  diff?: string | null;
  has_diff?: boolean;
  vcs_commit?: string | null;
  origin?: "create" | "workflow" | "manual" | "restore" | null;
}

export interface WorkflowNode {
  id: string;
  type: string;
  label: string;
  config: Record<string, any>;
  position: { x: number; y: number };
}

export interface WorkflowEdge {
  id: string;
  source: string;
  source_port: string;
  target: string;
  target_port: string;
}

export interface Workflow {
  id: string;
  name: string;
  version: number;
  description: string;
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
  parameters: Record<string, any>;
  created_at?: string;
}

export interface Port {
  name: string;
  type: string;
  required: boolean;
  multiple: boolean;
  description: string;
}

export interface NodeType {
  type: string;
  title: string;
  category: string;
  description: string;
  inputs: Port[];
  outputs: Port[];
  config_schema: any;
  defaults: Record<string, any>;
}

export interface UnitRun {
  unit: string;
  status: string;
  fingerprint?: string | null;
  previous_fingerprint?: string | null;
  reason: string;
  context_id?: string | null;
  plan?: any;
  error?: string | null;
}

export interface NodeRun {
  node_id: string;
  node_type: string;
  status: string;
  attempts: number;
  started_at?: string | null;
  finished_at?: string | null;
  units: UnitRun[];
  outputs: Record<string, any>;
  error?: string | null;
  logs: string[];
  approval?: any;
}

export interface Execution {
  id: string;
  workflow_id: string;
  workflow_version: number;
  mode: string;
  status: string;
  created_at: string;
  started_at?: string | null;
  finished_at?: string | null;
  node_runs: NodeRun[];
  error?: string | null;
}

export interface ExecutionSummary {
  id: string;
  workflow_id: string;
  workflow_version: number;
  mode: string;
  status: string;
  created_at: string;
  finished_at?: string | null;
  error?: string | null;
  nodes: Record<string, string>;
}

export interface ResolvedEntry {
  binding: Binding;
  source_name: string;
  media_type: string;
  processing_state: string;
  anchor: string;
  relation: "self" | "inherited" | "descendant";
  cascading_aspects: string[];
  applies: boolean;
  role_use: string;
  effective_weight: number;
  overridden_by: string[];
  notes: string[];
  summary: Record<string, any>;
}

export interface ResolvedContext {
  id: string;
  target: TargetSelector;
  target_path: string[];
  entries: ResolvedEntry[];
  constraints: any[];
  conflicts: { property: string; bindings: string[]; detail: string; resolved: boolean; resolution: string }[];
  warnings: string[];
}

export interface Project {
  id: string;
  name: string;
  description: string;
  created_at: string;
  settings: {
    roles: RoleProfile[];
    allowed_commands: string[];
    default_provider: string;
    default_model?: string | null;
    allow_network_fetch: boolean;
  };
}

export interface AdapterInfo {
  name: string;
  version: string;
  description: string;
  artifact_types: string[];
  operations: { name: string; family: string; description: string; aspects: string[]; target_kinds: string[] }[];
  measurements: string[];
  available: boolean;
  unavailable_reason?: string | null;
  templates: string[];
  export_formats: string[];
}

export interface Health {
  ok: boolean;
  version: string;
  workspace: string;
  adapters: AdapterInfo[];
  providers: { name: string; description: string; available: boolean; detail: string }[];
}

export const targetKey = (t: TargetSelector): string =>
  t.scope === "project"
    ? "project"
    : t.scope === "artifact"
      ? `artifact:${t.artifact_id}`
      : `artifact:${t.artifact_id}#${t.component_id}`;

// ---------------------------------------------------------------------------
// V1 spatial boards (mirrors backend/daedelus/boards.py)
// ---------------------------------------------------------------------------

export type ResourceKind = "artifact" | "source" | "workflow" | "workflow_node" | "execution" | "note" | "frame" | "none";
export type ItemType = "artifact_view" | "source" | "operation" | "note" | "frame" | "workflow";
export type ConnectionType = "reference" | "execution" | "dependency" | "annotation";

export interface ResourceRef {
  kind: ResourceKind;
  id?: string | null;
  workflow_id?: string | null;
  node_id?: string | null;
}

export interface CanvasItem {
  id: string;
  item_type: ItemType;
  resource_ref: ResourceRef;
  position: { x: number; y: number };
  size: { width: number; height: number };
  z_index: number;
  presentation_state: Record<string, any>;
  group_id?: string | null;
  collapsed: boolean;
  metadata: Record<string, any>;
}

export interface Anchor {
  handle?: string | null;
  component_id?: string | null;
  port?: string | null;
}

export interface CanvasConnection {
  id: string;
  connection_type: ConnectionType;
  source_item_id: string;
  target_item_id: string;
  source_anchor: Anchor;
  target_anchor: Anchor;
  domain_ref: { kind: "binding" | "workflow_edge" | "none"; id?: string | null; workflow_id?: string | null };
  presentation_state: Record<string, any>;
  derived: boolean;
}

export interface Viewport {
  x: number;
  y: number;
  zoom: number;
}

export interface CanvasBoard {
  id: string;
  project_id: string;
  name: string;
  schema_version: number;
  revision: number;
  items: CanvasItem[];
  connections: CanvasConnection[];
  saved_views: { id: string; name: string; viewport: Viewport }[];
  viewport: Viewport;
  created_at: string;
  updated_at: string;
  missing_items?: string[];
}

export const refKey = (r: ResourceRef): string =>
  r.kind === "workflow_node" ? `workflow_node:${r.workflow_id}:${r.node_id}` : `${r.kind}:${r.id}`;

export type Selection =
  | { kind: "none" }
  | { kind: "items"; itemIds: string[] }
  | { kind: "component"; itemId: string | null; artifactId: string; componentId: string }
  | { kind: "connection"; connectionId: string }
  | { kind: "resource"; ref: ResourceRef };
