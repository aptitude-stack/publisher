# Publisher Skeleton

This package is a scaffold for the client-side publisher flow.

The goal is to prepare a skill and pass it through several pipeline stages before creating the final payload for the publish endpoint.

## Current stages

1. `identity`
   Builds `slug`, `version`, and `intent`
2. `metadata`
   Prepares the metadata block
3. `ranking`
   Holds the future internal ranking logic
4. `security`
   Holds prompt-injection oriented security scanning, findings, and security scoring
5. `validation`
   Holds the future verification and error checks
6. `delivery`
   Builds the final payload shape that can later be sent to the client/server endpoint

## Files

- `models.py`
  Shared dataclasses for the pipeline state
- `pipeline.py`
  Runs the stages in order
- `stages/`
  One file per stage

## Notes

- This scaffold does not implement the real business logic yet.
- Each stage writes placeholder data into the shared `PublishContext`.
- `stage_history` keeps a trace of what happened at each stage so we can inspect pipeline progress later.
