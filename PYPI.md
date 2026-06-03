# Aptitude Publisher

[![PyPI](https://img.shields.io/badge/PyPI-aptitude--publisher-3775A9?style=for-the-badge&logo=pypi&logoColor=white)](https://pypi.org/project/aptitude-publisher/)
[![GitHub](https://img.shields.io/badge/GitHub-repository-181717?style=for-the-badge&logo=github&logoColor=white)](https://github.com/aptitude-stack/publisher)
![Python](https://img.shields.io/badge/Python-3.12-3776AB?style=for-the-badge&logo=python&logoColor=white)
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

---

## Configure Registry Access

Publisher uploads require a registry publish token:

```bash
export APTITUDE_PUBLISH_TOKEN=publisher-token
```

The default registry URL is `http://127.0.0.1:8000`. Override it for production or self-hosted registries:

```bash
export APTITUDE_REGISTRY_URL=https://api.aptitude-registry.dev
```

Relationship and existing-skill checks can also use a read token:

```bash
export APTITUDE_READ_TOKEN=reader-token
```

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

A publish-ready source is a local skill folder with a required `SKILL.md` file. The publisher reads the `SKILL.md` frontmatter for registry identity, version, metadata, schemas, and relationships.

Generated files under `.publisher_artifacts/` are local trace artifacts. They are not source files to author, and they are excluded from the immutable upload bundle.

---

## What Works Today

- guided inspect, publish, and admin batch-upload wizard
- skill-root discovery from local folders or explicit paths
- registry identity derivation from `SKILL.md` frontmatter
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
