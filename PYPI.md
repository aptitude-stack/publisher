# Aptitude Publisher

[![PyPI](https://img.shields.io/badge/PyPI-aptitude--publisher-3775A9?style=for-the-badge&logo=pypi&logoColor=white)](https://pypi.org/project/aptitude-publisher/)
[![GitHub](https://img.shields.io/badge/GitHub-repository-181717?style=for-the-badge&logo=github&logoColor=white)](https://github.com/aptitude-stack/publisher)
![Python](https://img.shields.io/badge/Python-3.13-3776AB?style=for-the-badge&logo=python&logoColor=white)
![uv](https://img.shields.io/badge/uv-tooling-6E56CF?style=for-the-badge&logo=uv&logoColor=white)
![CLI](https://img.shields.io/badge/CLI-review--first-111111?style=for-the-badge)

Review-first CLI for validating and publishing Aptitude skills.

---

## Install

Install the published package as a CLI tool:

```bash
uv tool install aptitude-publisher
aptitude-publisher --help
```

Run it without a persistent install:

```bash
uvx aptitude-publisher --help
```

The package installs this console command:

- `aptitude-publisher`
- `aptitude-publisher-mcp`

---

## Configure Registry Access

Publisher uploads require a registry publish token:

```bash
export APTITUDE_PUBLISH_TOKEN=publisher-token
```

The packaged CLI and MCP server use `https://api.aptitude-registry.dev` by default. For local development or a self-hosted registry, override it:

```bash
export APTITUDE_REGISTRY_URL=http://127.0.0.1:8000
```

Relationship and existing-skill checks can also use a read token:

```bash
export APTITUDE_READ_TOKEN=reader-token
```

---

## Performance Evaluation

Automatic Upskill case generation and evaluation currently use one OpenAI model:
`gpt-4.1-mini`. Set `OPENAI_API_KEY` before inspection; do not configure
multiple `UPSKILL_MODELS` values for publisher evaluation.

```bash
export OPENAI_API_KEY=...
export UPSKILL_PROVIDER=openai
export UPSKILL_MODELS=gpt-4.1-mini
```

---

## MCP Server

Run the local stdio MCP server directly from PyPI:

```bash
uvx aptitude-publisher mcp
```

For a persistent installation, use `aptitude-publisher mcp` or the direct
`aptitude-publisher-mcp` executable. The process waits for an MCP host; it does
not display the guided terminal wizard.

```json
{
  "mcpServers": {
    "aptitude-publisher": {
      "command": "uvx",
      "args": ["aptitude-publisher", "mcp"],
      "env": {
        "APTITUDE_PUBLISH_TOKEN": "replace-with-publish-token"
      }
    }
  }
}
```

`aptitude_publisher_inspect_skill` runs the local evaluation pipeline and
returns the latest JSON report path. `aptitude_publisher_publish_skill` reruns
that evaluation and can mutate registry state, so it requires an explicit slug,
publish intent, and `confirm_upload=true`. Its evaluation response uses
`report_path`; `artifacts_dir` is obsolete. Credentials are read only from the
server environment and are never accepted as tool inputs. Admin batch upload
and remote HTTP transport are outside the MCP v1 surface.

---

## Usage

Launch the guided publisher wizard:

```bash
aptitude-publisher
```

Inspect a skill folder before publishing:

```bash
aptitude-publisher inspect /path/to/skill
```

Run the full local flow and stop before upload:

```bash
aptitude-publisher publish /path/to/skill --dry-run
```

Publish a skill to the configured registry:

```bash
aptitude-publisher publish /path/to/skill --intent create_skill
```

Upload multiple skills concurrently with an admin-scoped registry token:

```bash
export APTITUDE_ADMIN_TOKEN=admin-token
aptitude-publisher admin-batch-upload /path/to/skill-a /path/to/skill-b --intent create_skill
```

Batch upload runs local scans and uploads in the background with a visible progress bar, then prints only a final summary. It defaults to `--scan-profile fast`, `--trust-tier verified`, and `--artifact-origin verified`; pass `--scan-profile full` for deeper pre-upload checks. When an admin token is set, the guided wizard also offers a batch-upload path that accepts one directory containing skill folders and starts immediately with those defaults.

Publish a new version of an existing skill:

```bash
aptitude-publisher publish /path/to/skill --intent publish_version
```

Override identity fields when needed:

```bash
aptitude-publisher publish /path/to/skill \
  --slug my-skill \
  --version 1.0.0 \
  --publisher-identity my-team
```

---

## Skill Folder Contract

A publish-ready source is a local skill folder with required `SKILL.md` and
`aptitude.yaml` files. `SKILL.md` keeps the standard `name`, `description`,
`license`, and `compatibility` fields. Aptitude publishing metadata is a flat
sidecar:

```yaml
version: "0.1.0"
intent: create_skill
tags: [python, review]
inputs_schema: {}
outputs_schema: {}
relationships:
  depends_on:
    - slug: python-testing
      version: "0.1.2"
token_estimate: 1200
maturity_score: 0.8
security_score: 0.9
```

`relationships` and numeric hints are optional; omitted relationship families
default to empty lists. CLI and MCP values override sidecar identity values.
`agents/openai.yaml`, when present, remains independent and unchanged. The
publisher rejects duplicate YAML keys, unknown fields, invalid types, malformed
relationship selectors, and known Aptitude fields left in legacy frontmatter.
Move legacy fields manually to `aptitude.yaml` instead of relying on fallback
parsing.

The publisher retains one latest JSON report per canonical skill directory. Its
path is `<cache-root>/aptitude/publisher/<sha256(canonical-absolute-skill-directory)>.json`,
where `<cache-root>` is the absolute `XDG_CACHE_HOME` when configured or
`~/.cache` otherwise. The report has `schema_version`, `skill_root`,
`updated_at`, `status`, `stages`, `gates`, `evidence`, `warnings`, `error`, and
`inspection_receipt`; status is `running`, `ready`, `blocked`, or `failed`.
Writes are atomic and owner-only. Raw evaluator transcripts, credentials,
environment dumps, and temporary paths are not retained. Evaluator copies and
working directories are temporary, outside the source tree, and cleaned after
success, failure, or timeout.

Existing `.publisher_artifacts/` directories are preserved historical content,
excluded from inventory and the immutable upload bundle, and never read or
written by the current publisher.

---

## What Works Today

- guided inspect, publish, and admin batch-upload wizard
- skill-root discovery from local folders or explicit paths
- registry identity derivation from `SKILL.md` `name` and `aptitude.yaml`
  `version`/`intent`
- create-skill and publish-version intent handling
- relationship normalization and registry existence alerts
- metadata extraction for public skill facts and generated estimates
- SKILL.md contract validation
- LLM Guard security scanning over skill text and companion files
- Upskill-backed performance evaluation when configured
- weighted publish ranking and block/allow decisions
- deterministic `tar.zst` bundle creation
- registry upload with multipart artifact delivery
- admin batch upload with summary-only output

---

## Source

Source and contributor documentation live in the project repository:

https://github.com/aptitude-stack/publisher
