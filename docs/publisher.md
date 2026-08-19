# Publisher Pipeline

The publisher prepares a local skill folder, evaluates it, builds the registry
payload, and uploads a deterministic `.tar.zst` bundle.

## MCP Interface

The package includes a local stdio server launched with
`uvx aptitude-publisher mcp`, `aptitude-publisher mcp`, or the direct
`aptitude-publisher-mcp` entrypoint. It is a thin adapter over the same
`PublisherPipeline`, bundle builder, and registry client used by the CLI.

The interface exposes two tools:

- `aptitude_publisher_inspect_skill` runs the full evaluation pipeline. It is
  not read-only because pipeline stages write `.publisher_artifacts/` below the
  selected skill.
- `aptitude_publisher_publish_skill` requires explicit `slug`, `intent`, and
  `confirm_upload=true`, reruns evaluation, checks new-skill slug availability,
  builds a fresh bundle, and uploads with environment-provided credentials.

The publish tool accepts no token field. It uses the existing publish-token
environment aliases and prefers the existing read-token aliases for duplicate
and relationship checks. Blocked evaluation and bundle failures stop before
registry upload. Admin batch upload, remote HTTP transport, and persistent MCP
state are intentionally deferred.

## Admin Batch Upload

`aptitude-publisher admin-batch-upload` accepts multiple skill folder paths and
runs the normal publisher pipeline for each skill concurrently. It uses an
admin-scoped registry token from `--admin-token`, `APTITUDE_ADMIN_TOKEN`,
`APTITUDE_REGISTRY_ADMIN_TOKEN`, or `REGISTRY_ADMIN_TOKEN`.

Upload commands fail before local scans when their required upload token is
missing or blank. For `create_skill`, they also check slug availability before
LLM Guard, Upskill, bundle creation, or upload work begins. `publish_version`
keeps running when the slug exists because that is the expected versioning path.
`publish --dry-run` and `admin-batch-upload --dry-run` still run locally without
upload credentials.

Batch mode intentionally suppresses per-skill pipeline reports. Local scans,
bundle creation, duplicate checks, and uploads run in the background with a
visible progress bar; the CLI prints one final summary with status, HTTP code,
slug, version, and message for each input skill. Batch upload defaults to the
fast scan profile, `verified` trust tier, and `verified` artifact origin to keep
unattended admin runs lightweight and trusted. Pass `--scan-profile full` for a
deeper review.

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

Upskill evaluates publishable skills through official OpenAI by default. It
generates evaluation cases from the selected skill when no test file is supplied.

```bash
export OPENAI_API_KEY=...
export UPSKILL_PROVIDER=openai
export UPSKILL_MODELS=gpt-4.1-mini
export UPSKILL_NO_BASELINE=false
```

Set `UPSKILL_TESTS_PATH` to use a reviewed JSON test suite instead:

```json
{"cases":[{"input":"Review this Python function.","expected":{"contains":"type hint"}}]}
```

```bash
export UPSKILL_TESTS_PATH=/absolute/path/to/upskill-tests.json
```

Set `UPSKILL_BASE_URL` only for a custom OpenAI-compatible endpoint. Missing,
partial, empty, or failed Upskill evidence blocks publishing and records the
reason in evaluator artifacts. A scored result that shows no benefit remains
quality evidence, not an evaluator failure. For a live smoke check, run an
inspect with the configuration above and verify `status: scored`,
`gpt-4.1-mini`, nonzero token metrics, and no validation errors. Evaluation
sends skill/test content to OpenAI; see [OpenAI API data controls](https://developers.openai.com/api/docs/guides/your-data#default-usage-policies-by-endpoint).
If LLM Guard is unavailable or failing, the security stage records that
evaluator status and blocks the publish flow because security has no local
fallback source. An explicit `PUBLISHER_LLM_GUARD_ENABLED=false` bypass is
recorded as disabled.
