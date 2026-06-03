# Publisher Pipeline

The publisher prepares a local skill folder, evaluates it, builds the registry
payload, and uploads a deterministic `.tar.zst` bundle.

## Admin Batch Upload

`aptitude-publisher admin-batch-upload` accepts multiple skill folder paths and
runs the normal publisher pipeline for each skill concurrently. It uses an
admin-scoped registry token from `--admin-token`, `APTITUDE_ADMIN_TOKEN`,
`APTITUDE_REGISTRY_ADMIN_TOKEN`, or `REGISTRY_ADMIN_TOKEN`.

Batch mode intentionally suppresses per-skill pipeline reports. Local scans,
bundle creation, duplicate checks, and uploads run in the background; the CLI
prints one final summary with status, HTTP code, slug, version, and message for
each input skill.

## Stages

1. `discovery`
   Reads the skill folder, parses `SKILL.md`, and inventories files.
2. `identity`
   Builds `slug`, `version`, and `intent`.
3. `metadata`
   Extracts metadata, schemas, tags, token estimate, and local quality fields.
4. `security`
   Runs LLM Guard as the authoritative skill-content security source. If LLM
   Guard is not available or does not produce a scored result, publishing is blocked.
5. `validation`
   Validates the local skill folder and Anthropic `SKILL.md` file contract.
6. `performance_exam`
   Runs Hugging Face upskill and uses only its measured performance metrics.
7. `ranking`
   Combines LLM Guard, Upskill, token efficiency, metadata, and validation signals into the publish decision.
8. `delivery`
   Builds the final registry payload shape.
9. `compression`
   Builds the `.tar.zst` artifact for upload.

## External Evaluators

Install the publisher with evaluator tools:

```bash
uv pip install -e .
```

Security depends on LLM Guard. LLM Guard scans the skill package content:
the primary `SKILL.md`, metadata fields, schemas, companion markdown, scripts,
references, and other text files. It checks for prompt injection, secrets, and
hidden text before the skill can be published.

Upskill can run directly when installed:

```bash
export UPSKILL_MODELS="haiku,sonnet"
```

or through an explicit command template:

```bash
export PUBLISHER_UPSKILL_COMMAND='upskill eval {skill_path}'
```

The Upskill command template supports `{skill_path}`, `{skill_file}`,
`{artifact_dir}`, and `{runs_dir}` when the selected command supports it. If Upskill is
disabled, unavailable, or failing, the performance exam records that evaluator
status and produces no score because performance has no local fallback source.
If LLM Guard is disabled, unavailable, or failing, the security stage records
that evaluator status. Unavailable or failing LLM Guard blocks the publish flow
because security has no local fallback source.
