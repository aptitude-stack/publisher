# Aptitude Publisher

![Python](https://img.shields.io/badge/python-3.13-3776AB?style=for-the-badge&logo=python&logoColor=white)
![uv](https://img.shields.io/badge/uv-managed-6E56CF?style=for-the-badge&logo=uv&logoColor=white)
![Rich](https://img.shields.io/badge/rich-CLI-111111?style=for-the-badge)
![pytest](https://img.shields.io/badge/pytest-0A9EDC?style=for-the-badge&logo=pytest&logoColor=white)
![Ruff](https://img.shields.io/badge/ruff-D7FF64?style=for-the-badge&logo=ruff&logoColor=111111)
![Last Commit](https://img.shields.io/github/last-commit/aptitude-stack/publisher?style=for-the-badge)

`Aptitude Publisher` is the review-first CLI for validating local Aptitude skill folders and publishing approved versions to the Aptitude Registry. It reads standard skill fields from `SKILL.md` and Aptitude publishing metadata from the required `aptitude.yaml` sidecar, runs validation and evaluator gates, builds a deterministic `.tar.zst` artifact, and uploads the artifact plus registry metadata.

The system is intentionally split in three:

- Publisher prepares local skill folders, evaluator evidence, immutable artifacts, and publish payloads.
- Registry stores immutable skill metadata, version records, authored relationships, artifacts, lifecycle state, and audit data.
- Resolver discovers, selects, solves, locks, and materializes skills for agents.

## Current CLI

Primary commands:

- `aptitude-publisher`
- `aptitude-publisher inspect /path/to/skill`
- `aptitude-publisher publish /path/to/skill`
- `aptitude-publisher admin-batch-upload /path/to/skill-a /path/to/skill-b`
- `aptitude-publisher mcp`

Running `aptitude-publisher` without a subcommand launches the guided review-first wizard. `inspect` runs the full local pipeline and prints detailed Structure/Risk/Quality summaries without uploading; use `--no-verbose` for the compact three-phase summary. `publish` runs the same gates, builds the upload bundle, checks registry state when tokens are available, and uploads only when the publish decision allows it.
`admin-batch-upload` runs the same local gates for multiple skill folders concurrently with the fast scan profile and verified trust defaults, uploads each accepted skill with an admin token, shows a progress bar, and prints only a final summary.
When `APTITUDE_ADMIN_TOKEN`, `APTITUDE_REGISTRY_ADMIN_TOKEN`, or `REGISTRY_ADMIN_TOKEN` is set, the wizard also offers an admin batch-upload path that accepts one directory containing skill folders and starts immediately with those defaults.

Common publish flags:

- `--dry-run`: run the full local flow and skip registry upload
- `--slug`: override the registry slug
- `--version`: override the semantic version
- `--intent create_skill|publish_version`: choose whether this is a new skill or a new version
- `--namespace`: target registry namespace, defaulting to `public`
- `--trust-tier untrusted|internal|verified`: governance trust tier
- `--artifact-origin internal|imported|verified|restricted`: governance artifact origin
- `--publisher-identity`: optional provenance identity for the publisher

## How To Install

Install the publisher and development dependencies with `uv`:

```bash
uv sync --extra dev
```

This creates the local environment from `pyproject.toml` and makes the CLI available through `uv run`.

For published usage after a PyPI release:

```bash
uv tool install aptitude-publisher
aptitude-publisher --help
```

For one-off execution without a persistent install:

```bash
uvx aptitude-publisher --help
```

## MCP Server

The PyPI package includes a local stdio MCP server for agent hosts. Run the
published package without a persistent install:

```bash
uvx aptitude-publisher mcp
```

An installed package also exposes the direct `aptitude-publisher-mcp` command.
Both commands wait for MCP protocol messages; they are not interactive terminal
interfaces.

Example MCP client configuration:

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

The server exposes:

- `aptitude_publisher_inspect_skill`: runs the full local evaluation pipeline
  and returns the latest evaluation report path.
- `aptitude_publisher_publish_skill`: reruns evaluation and uploads only when
  the caller supplies an explicit slug, intent, and `confirm_upload=true`.

The evaluation response exposes `report_path`; `artifacts_dir` is no longer a
response field. The report contains normalized stage, gate, and evaluator
evidence, plus a nested signed inspection receipt when available.

Use inspect first, review its validation, security, performance, ranking, and
identity result, then ask for confirmation before publish. Publish credentials
come only from the publisher environment variables; they are never MCP tool
arguments or response fields. Admin batch upload is not exposed through MCP.

Test the local checkout with MCP Inspector:

```bash
bunx @modelcontextprotocol/inspector uv --directory "$PWD" run aptitude-publisher-mcp
```

## Registry Access

Publishing requires a registry publish token:

```bash
export APTITUDE_PUBLISH_TOKEN=your-publish-token
```

`aptitude-publisher publish` validates this token and checks `create_skill`
slug availability before running local scans, security checks, performance
evaluations, or bundle creation. Use `inspect` or `publish --dry-run` when you
want local validation without an upload token.

The packaged CLI and MCP server use `https://api.aptitude-registry.dev` by default. For local development or a self-hosted registry, override it:

```bash
export APTITUDE_REGISTRY_URL=http://127.0.0.1:8000
```

Relationship checks and existing-skill checks can also use a read token:

```bash
export APTITUDE_READ_TOKEN=your-read-token
```

Admin batch upload requires an admin-scoped registry token:

```bash
export APTITUDE_ADMIN_TOKEN=your-admin-token
```

`admin-batch-upload` validates the admin token and blocks existing
`create_skill` slugs before each worker runs expensive local checks unless
`--dry-run` is set.

## Packaging And Publishing

This project builds and publishes as a normal Python package. `uv` is the build and publish tool, and the release registry is PyPI.

The packaging metadata lives in `pyproject.toml`:

- `[project]` defines the package name, version, dependencies, and console entry point
- `[project].readme` points at `PYPI.md`, which is the public project description rendered on PyPI
- `[project.scripts]` exposes `aptitude-publisher`, mapped to `publisher.app.cli:main`
- `[project.scripts]` exposes `aptitude-publisher-mcp`, mapped to `publisher.interfaces.mcp.main:main`
- `[build-system]` tells `uv` to build the package with setuptools

Build the package artifacts locally:

```bash
make build
```

`make build` runs `uv build --no-sources` and creates:

```text
dist/*.whl
dist/*.tar.gz
```

For a local manual publish with a PyPI API token:

```bash
export PYPI_API_TOKEN=your-pypi-token
make build-publish
```

`make build-publish`:

- requires `PYPI_API_TOKEN`
- builds fresh artifacts into `.build-publish-dist/`
- publishes with `uv publish`
- defaults to the production PyPI upload endpoint

To rehearse the local flow against TestPyPI instead of production PyPI:

```bash
export PYPI_API_TOKEN=your-testpypi-token
make build-publish REPOSITORY=testpypi
```

For the normal release path, publish to PyPI through GitHub Actions trusted publishing:

```bash
uv version --bump patch
git tag v$(uv version --short)
git push origin v$(uv version --short)
```

The release workflow lives at `.github/workflows/publish.yml` and:

- triggers on tags matching `v*`
- builds the wheel and sdist with `uv build --no-sources`
- publishes with `pypa/gh-action-pypi-publish`
- authenticates to PyPI with GitHub OIDC trusted publishing
- does not use PyPI API tokens or repository secrets for the CI release path

The publish job uses the GitHub Environment `pypi`. That gives releases a dedicated protection boundary in GitHub and matches the PyPI trusted-publisher configuration.

## How To Use

Launch the interactive wizard:

```bash
uv run aptitude-publisher
```

Inspect a skill folder before publishing:

```bash
uv run aptitude-publisher inspect /path/to/skill
```

Run the full local publish path without uploading:

```bash
uv run aptitude-publisher publish /path/to/skill --dry-run
```

Publish a new skill:

```bash
uv run aptitude-publisher publish /path/to/skill --intent create_skill
```

Publish a new version of an existing skill:

```bash
uv run aptitude-publisher publish /path/to/skill --intent publish_version
```

Upload multiple skills concurrently as an admin:

```bash
uv run aptitude-publisher admin-batch-upload \
  /path/to/skill-a \
  /path/to/skill-b \
  --intent create_skill \
  --concurrency 4
```

Batch upload suppresses per-skill pipeline reports while scans and uploads run. The CLI prints only a final summary with each skill's status, HTTP result, slug, version, and message.
Batch upload defaults to `--scan-profile fast`, `--trust-tier verified`, and `--artifact-origin verified` so unattended admin runs stay lightweight and trusted. Use `--scan-profile full` when a deeper pre-upload review is needed.

Override registry identity when the skill metadata needs an explicit local override:

```bash
uv run aptitude-publisher publish /path/to/skill \
  --slug my-skill \
  --version 1.0.0 \
  --publisher-identity my-team
```

## Skill Folder Contract

A publish-ready source is a local skill folder with required `SKILL.md` and
`aptitude.yaml` files. `SKILL.md` keeps the standard `name`, `description`,
`license`, and `compatibility` fields. Aptitude metadata is a flat sidecar:

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

`relationships` and the numeric hints are optional; omitted relationship
families default to empty lists. CLI and MCP values override sidecar identity
values. `agents/openai.yaml`, when present, remains independent and unchanged.
Duplicate YAML keys, unknown fields, invalid types, malformed relationship
selectors, and known Aptitude fields left in legacy `SKILL.md` frontmatter are
rejected. Move legacy fields manually to `aptitude.yaml` rather than relying on
fallback parsing.

The publisher retains one latest JSON report per canonical skill directory. Its
path is `<cache-root>/aptitude/publisher/<sha256(canonical-absolute-skill-directory)>.json`,
where `<cache-root>` is the absolute `XDG_CACHE_HOME` when configured or
`~/.cache` otherwise. The report has `schema_version`, `skill_root`,
`updated_at`, `status`, `stages`, `gates`, `evidence`, `warnings`, `error`, and
`inspection_receipt`. Status is `running`, `ready`, `blocked`, or `failed`.
Writes are atomic and owner-only. Raw evaluator transcripts, credentials,
environment dumps, and temporary paths are not retained; evaluator copies and
working directories are temporary, outside the source tree, and cleaned after
success, failure, or timeout.

Existing `.publisher_artifacts/` directories are preserved historical content,
excluded from inventory and the immutable upload bundle, and never read or
written by the current publisher.

The upload bundle is a deterministic `.tar.zst` archive built from the skill folder after publisher gates complete. Registry clients later install from that stored artifact rather than from the publisher's local working tree.

## Evaluator Configuration

LLM Guard runs locally over the skill package content. It scans the primary
`SKILL.md`, `aptitude.yaml` metadata, schemas, companion markdown, scripts,
references, and other text files for prompt injection, secrets, and hidden text.

Security publishing decisions depend on LLM Guard. If it is unavailable or
fails, the security stage blocks publishing because security has no local
fallback source. An explicit `PUBLISHER_LLM_GUARD_ENABLED=false` bypass is
recorded as disabled.

Upskill evaluates publishable skills through official OpenAI by default. With
an OpenAI key, it generates evaluation cases from the selected skill and then
compares the model with and without that skill.

```bash
export OPENAI_API_KEY=...
export UPSKILL_PROVIDER=openai
export UPSKILL_MODELS=gpt-4.1-mini
export UPSKILL_NO_BASELINE=false
```

To run a reviewed, stable suite instead, set `UPSKILL_TESTS_PATH`:

```json
{"cases":[{"input":"Review this Python function.","expected":{"contains":"type hint"}}]}
```

```bash
export UPSKILL_TESTS_PATH=/absolute/path/to/upskill-tests.json
```

Set `UPSKILL_BASE_URL` only for a custom OpenAI-compatible endpoint. Missing,
partial, empty, or failed Upskill evidence blocks publishing and is recorded in
the latest report. A scored but non-beneficial result remains reviewable quality
evidence rather than an evaluator outage.

For a live smoke check, run `aptitude-publisher inspect /path/to/skill` with
the variables above, then verify the report's performance evidence records
`status: scored`, `gpt-4.1-mini`, nonzero token metrics, and no validation
errors. This sends skill and test content to OpenAI; see
[OpenAI API data controls](https://developers.openai.com/api/docs/guides/your-data#default-usage-policies-by-endpoint).

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
- admin batch upload with concurrent skill processing and summary-only output
- local PyPI build and publish targets
- GitHub Actions trusted publishing on `v*` tags

## Current Package Map

```text
publisher/
  app/
    cli.py
    menu.py
    pipeline.py
  artifacts/
  domain/
  gates/
  integrations/
  registry/
  stages/
  frontmatter.py
  relationships.py
```

## Development

Requirements:

- Python `>=3.13.5,<3.14`
- `uv`

Developer workflow:

```bash
make test
make build
```

Run focused tests directly:

```bash
UV_CACHE_DIR=.uv-cache uv run --extra dev pytest
```

## Source Of Truth Docs

Start with the publisher pipeline reference:

- [docs/publisher.md](docs/publisher.md)

Related Aptitude component docs:

- [../registry/README.md](../registry/README.md)
- [../resolver/README.md](../resolver/README.md)
