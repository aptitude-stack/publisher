# Compact Publish Plan Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render a compact pre-inspection Publish Plan using the selected skill's extracted name, version, and optional license, with muted gray frame titles.

**Architecture:** Extend the existing `MenuSkill` and `PublishPlan` value objects rather than adding a second parser or running pipeline stages early. `_read_menu_skill` remains the single pre-inspection frontmatter read, `_build_publish_plan` copies the selected values into the immutable plan, and `_render_plan` only formats that plan.

**Tech Stack:** Python 3.12, Rich, pytest, uv

---

### Task 1: Extract and carry compact skill metadata

**Files:**
- Modify: `publisher/app/menu.py`
- Test: `tests/unit/test_menu_separators.py`

- [ ] **Step 1: Write failing extraction and plan-construction tests**

Add this frontmatter fixture test, then extend the existing `_build_publish_plan` test with the three plan assertions below:

```python
def test_read_menu_skill_extracts_compact_plan_metadata(tmp_path: Path) -> None:
    skill_file = tmp_path / "SKILL.md"
    skill_file.write_text(
        """---
name: brainstorming
license: MIT
metadata:
  version: 0.1.0
  intent: create_skill
---

# Brainstorming
""",
        encoding="utf-8",
    )

    skill = menu._read_menu_skill(skill_file)

    assert skill is not None
    assert skill.name == "brainstorming"
    assert skill.version == "0.1.0"
    assert skill.license == "MIT"
```

```python
assert plan.skill_name == "example"
assert plan.skill_version == "0.1.0"
assert plan.license is None
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
UV_CACHE_DIR=.uv-cache uv run --extra dev pytest tests/unit/test_menu_separators.py -q -k "read_menu_skill or build_publish_plan"
```

Expected: failure because `MenuSkill`/`PublishPlan` do not expose the new license and display metadata fields.

- [ ] **Step 3: Add the minimum fields and copy them into the plan**

Add optional compatibility fields at the end of the existing dataclasses so unrelated test constructors remain valid:

```python
@dataclass(frozen=True, slots=True)
class MenuSkill:
    path: Path
    name: str
    version: str
    intent: str
    license: str | None = None


@dataclass(frozen=True, slots=True)
class PublishPlan:
    action: Action
    skill_path: Path
    slug: str | None
    intent: Intent
    trust_tier: TrustTier
    namespace: str
    artifact_origin: ArtifactOrigin
    policy_pack_slug: str | None
    publisher_identity: str | None
    scan_profile: ScanProfile
    skill_name: str | None = None
    skill_version: str | None = None
    license: str | None = None
```

In `_read_menu_skill`, extract a non-empty top-level license string. In `_build_publish_plan`, pass the selected `MenuSkill` values into `PublishPlan`. Preserve the current name and version fallbacks and all pipeline governance values.

- [ ] **Step 4: Run the focused tests and verify GREEN**

Run the Step 2 command. Expected: selected tests pass.

### Task 2: Render compact rows and gray frame titles

**Files:**
- Modify: `publisher/app/menu.py`
- Test: `tests/unit/test_menu_separators.py`

- [ ] **Step 1: Write failing rendering tests**

Update the plan rendering test to construct a plan with `skill_name="brainstorming"`, `skill_version="0.1.0"`, and `license="MIT"`, then assert:

```python
assert "Name" in rendered
assert "brainstorming" in rendered
assert "Version" in rendered
assert "0.1.0" in rendered
assert "License" in rendered
assert "MIT" in rendered
assert "Skill version" not in rendered
assert "resolved during inspection" not in rendered
assert "Trust" not in rendered
assert "Origin" not in rendered
```

Add a second rendering case with `license=None` and assert the License row is omitted. Add:

```python
assert menu._frame("body", title="Publish Plan").title.style == menu.THEME.text_muted
```

- [ ] **Step 2: Run rendering tests and verify RED**

Run:

```bash
UV_CACHE_DIR=.uv-cache uv run --extra dev pytest tests/unit/test_menu_separators.py -q -k "render_plan or frame_title"
```

Expected: failure because the renderer still uses the folder/placeholder rows and frame titles use the primary style.

- [ ] **Step 3: Implement the compact renderer**

Change `_frame` to create its title with `THEME.text_muted`. Change `_render_plan` to render `Name` and `Version` from the plan, retain Action, publish-only Intent, Inspection depth, and Namespace, and conditionally add License. Do not restore Trust or Origin and do not add long metadata.

- [ ] **Step 4: Run rendering tests and verify GREEN**

Run the Step 2 command. Expected: selected tests pass.

### Task 3: Verify the Publisher wizard change

**Files:**
- Verify: `publisher/app/menu.py`
- Verify: `tests/unit/test_menu_separators.py`

- [ ] **Step 1: Run the complete focused test module**

```bash
UV_CACHE_DIR=.uv-cache uv run --extra dev pytest tests/unit/test_menu_separators.py -q
```

Expected: all tests pass.

- [ ] **Step 2: Run formatting and diff validation**

```bash
UV_CACHE_DIR=.uv-cache uv run --extra dev ruff check publisher/app/menu.py tests/unit/test_menu_separators.py
git diff --check
```

Expected: both commands exit zero. Inspect the scoped diff to confirm unrelated dirty work remains untouched.

No commit step is included because repository guidance keeps commits user-controlled.
