# Publisher Pipeline

The publisher prepares a local skill folder, evaluates it, builds the registry
payload, and uploads a deterministic `.tar.zst` bundle.

## Stages

1. `discovery`
   Reads the skill folder, parses `SKILL.md`, and inventories files.
2. `identity`
   Builds `slug`, `version`, and `intent`.
3. `metadata`
   Extracts metadata, schemas, tags, token estimate, and local quality fields.
4. `security`
   Runs NVIDIA garak as the authoritative security source. If Garak is not
   configured or does not produce a scored result, publishing is blocked.
5. `validation`
   Validates the local skill folder and Anthropic `SKILL.md` file contract.
6. `performance_exam`
   Runs Hugging Face upskill and uses only its measured performance metrics.
7. `ranking`
   Combines Garak, Upskill, token efficiency, metadata, and validation signals into the publish decision.
8. `delivery`
   Builds the final registry payload shape.
9. `compression`
   Builds the `.tar.zst` artifact for upload.

## External Evaluators

Install evaluator tools with:

```bash
uv pip install -e ".[evaluators]"
```

Security depends on NVIDIA garak. Configure Garak with native target settings:

```bash
export GARAK_TARGET_TYPE="openai"
export GARAK_TARGET_NAME="gpt-4o-mini"
```

Set the provider token required by the selected Garak target, for example:

```bash
export OPENAI_API_KEY="..."
```

or use an explicit command template:

```bash
export PUBLISHER_GARAK_COMMAND='garak --target_type openai --target_name gpt-4o-mini --probes promptinject --report_prefix {artifact_dir}/garak'
```

Upskill can run directly when installed:

```bash
export UPSKILL_MODELS="haiku,sonnet"
```

or through an explicit command template:

```bash
export PUBLISHER_UPSKILL_COMMAND='upskill eval {skill_path} --runs-dir {runs_dir}'
```

Both templates support `{skill_path}`, `{skill_file}`, `{artifact_dir}`, and
for upskill `{runs_dir}`. If Upskill is unavailable or unconfigured, the
performance exam records no score because performance has no local fallback
source. If Garak is unavailable or unconfigured, the security stage blocks the
publish flow because security has no local fallback source.
