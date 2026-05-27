# Aptitude Publisher

Local publisher for Aptitude skills. It evaluates a skill folder, builds a
registry payload, compresses the bundle, and uploads it to an Aptitude registry.

## Install

```bash
uv venv
uv pip install -e ".[evaluators]"
```

The evaluator extra installs:

- `garak` for security scanning
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

Garak needs a model target:

```bash
export GARAK_TARGET_TYPE="openai"
export GARAK_TARGET_NAME="gpt-4o-mini"
```

Upskill can be pointed at models with:

```bash
export UPSKILL_MODELS="haiku,sonnet"
```

Provider API keys are needed when the configured Garak or Upskill target uses a
hosted model provider. For example, an OpenAI-backed Garak target expects:

```bash
export OPENAI_API_KEY="..."
```

Security publishing decisions depend on Garak. If Garak is not configured or
does not produce a scored result, the publisher blocks the publish flow.
