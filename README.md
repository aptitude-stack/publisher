# Aptitude Publisher

Local publisher for Aptitude skills. It evaluates a skill folder, builds a
registry payload, compresses the bundle, and uploads it to an Aptitude registry.

## Install

```bash
uv venv
uv pip install -e .
```

The base install includes:

- `llm-guard` for skill-content security scanning
- `upskill` for performance evaluation

## Inspect A Skill

```bash
aptitude-publisher inspect /path/to/skill
```

## Publish A Skill

```bash
APTITUDE_PUBLISH_TOKEN=publisher-token \
aptitude-publisher publish /path/to/skill --version 1.0.0
```

The default registry URL is `http://127.0.0.1:8000`. Override it with:

```bash
export APTITUDE_REGISTRY_URL="https://api.aptitude-registry.dev"
```

## Evaluator Configuration

LLM Guard runs locally over the skill package content. It scans the main
`SKILL.md`, metadata fields, schemas, companion markdown, scripts, references,
and other text files for prompt injection, secrets, and hidden text.

Upskill can be pointed at models with:

```bash
export UPSKILL_MODELS="haiku,sonnet"
```

Provider API keys are needed when the configured Upskill target uses a hosted
model provider. For example, an OpenAI-compatible target expects:

```bash
export OPENAI_API_KEY="..."
```

Security publishing decisions depend on LLM Guard. If LLM Guard is not configured or
does not produce a scored result, the publisher blocks the publish flow.
