# Daedelus V0 demonstration evidence

Overall: **PASS** (40/40 checks, 54.8 s)

## Sources and bindings

| Source | Media | State | Role | Aspects | Target | Strength | Constraint |
|---|---|---|---|---|---|---|---|
| Project style guide | text | ready | guideline | style, color, material, palette, mood | project | 0.8 | soft |
| Table reference photo | image | ready | reference | geometry, proportions | Table | 0.8 | soft |
| Table reference photo | image | ready | evaluation | color | Table | 1.0 | soft |
| Leg inspiration (flared) | image | ready | reference | shape, color | Table → legs | 0.9 | hard; height preserve |
| Background inspiration (sunset) | image | ready | reference | palette, lighting | Background | 0.7 | soft |
| Manifest conventions | text | ready | guideline | code_style, convention | Scene manifest | 1.0 | soft |
| Background inspiration (dawn) | image | ready | inspiration | palette | Background → sky | 0.8 | soft |
| Bevel modifier tutorial (video) | video_url | partial | technique | technique | Table → tabletop | 0.6 | soft |

Video source: state **partial**, title `Bevel Modifier | Blender Tutorial`; video content not analysed (no video download/understanding backend configured)

## Checks

| Scenario | Check | Result |
|---|---|---|
| S0 initial run | execution succeeded | PASS |
| S0 initial run | Table has a new revision | PASS |
| S0 initial run | Background has a new revision | PASS |
| S0 initial run | Scene manifest has a new revision | PASS |
| S0 initial run | legs height preserved (hard constraint) | PASS |
| S0 initial run | manifest tests executed and passed | PASS |
| S0 initial run | manifest change has a reviewable diff | PASS |
| S0 initial run | immediate re-run executes nothing (all units up to date) | PASS |
| S1 leg source | impact: only the legs unit of the 3D agent is stale | PASS |
| S1 leg source | execution succeeded | PASS |
| S1 leg source | only leg components changed in the table | PASS |
| S1 leg source | tabletop and table root untouched | PASS |
| S1 leg source | background not rebuilt (no new revision, unit skipped) | PASS |
| S1 leg source | manifest re-ran because an upstream revision changed | PASS |
| S2 background | impact: only the sky unit of the 2D agent is stale | PASS |
| S2 background | execution succeeded | PASS |
| S2 background | table not rebuilt | PASS |
| S2 background | only the sky layer changed | PASS |
| S3 global guideline | impact: table AND background identified as affected | PASS |
| S3 global guideline | execution succeeded | PASS |
| S3 global guideline | both artifacts received new revisions | PASS |
| S4 role change | impact: only the legs unit is stale | PASS |
| S4 role change | execution succeeded | PASS |
| S4 role change | source media unchanged | PASS |
| S4 role change | interpretation now uses 'reference (drive)' | PASS |
| S4 role change | only leg components changed | PASS |
| S5 conflict | 3D agent failed with an explicit conflict | PASS |
| S5 conflict | table left untouched | PASS |
| S5 conflict | downstream nodes reported as blocked | PASS |
| S6 failure | validation fails because tests were not permitted to run | PASS |
| S7 reproducibility | initial: re-plan + re-apply reproduce identical states | PASS |
| S7 reproducibility | leg change: re-plan + re-apply reproduce identical states | PASS |
| S7 reproducibility | guideline change: re-plan + re-apply reproduce identical states | PASS |
| S8 persistence | project reopens with sources, bindings and artifacts | PASS |
| S8 persistence | workflow export/import is lossless | PASS |
| S8 persistence | saving creates a new workflow version | PASS |
| S8 persistence | at least one source carries multiple independent bindings | PASS |
| S8 persistence | editable native file exists for Table | PASS |
| S8 persistence | editable native file exists for Background | PASS |
| S8 persistence | editable native file exists for Scene manifest | PASS |

## Gallery

### 00_initial: fixtures as created (revision 1)
![00_initial_table_r1.png](images/00_initial_table_r1.png)
![00_initial_background_r1.png](images/00_initial_background_r1.png)

### 01_after_initial_run: after first execution of the workflow
![01_after_initial_run_table_r2.png](images/01_after_initial_run_table_r2.png)
![01_after_initial_run_background_r2.png](images/01_after_initial_run_background_r2.png)

### 02_leg_source_changed: leg inspiration swapped (tapered -> flared teal)
![02_leg_source_changed_table_r3.png](images/02_leg_source_changed_table_r3.png)
![02_leg_source_changed_background_r2.png](images/02_leg_source_changed_background_r2.png)

### 03_background_changed: sky binding now points at the dawn image
![03_background_changed_table_r3.png](images/03_background_changed_table_r3.png)
![03_background_changed_background_r3.png](images/03_background_changed_background_r3.png)

### 04_guideline_changed: style guide edited (warm -> cool)
![04_guideline_changed_table_r4.png](images/04_guideline_changed_table_r4.png)
![04_guideline_changed_background_r4.png](images/04_guideline_changed_background_r4.png)

### 05_role_changed: leg binding role inspiration -> reference (stronger influence)
![05_role_changed_table_r5.png](images/05_role_changed_table_r5.png)
![05_role_changed_background_r4.png](images/05_role_changed_background_r4.png)

## Editable native files

- Table: `<scratch>/demo/workspace/projects/prj_78904db9f86b/artifacts/art_6dfb0202a775/native/model.blend`
- Background: `<scratch>/demo/workspace/projects/prj_78904db9f86b/artifacts/art_da99bd18e42c/native/image.ora`
- Scene manifest: `<scratch>/demo/workspace/projects/prj_78904db9f86b/artifacts/art_2c03d1642294/native`
