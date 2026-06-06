---
name: pytest
description: "Helps write, run, and debug pytest tests, fixtures, parametrization, markers, and focused test commands; use when users ask about pytest mechanics."
metadata:
  version: "0.1.0"
  intent: "create_skill"
  tags: ["python","pytest","testing"]
  inputs_schema: {"type":"object","additionalProperties":true}
  outputs_schema: {"type":"object","additionalProperties":true}
  relationships: {"depends_on":[],"extends":[],"conflicts_with":[],"overlaps_with":[]}
---
# Instructions

Use this skill for pytest mechanics. Keep broader Python test strategy in `python-testing` and coverage policy in `testing-coverage`.

## Focused Commands

- Run one file: `pytest path/to/test_file.py`
- Run one test: `pytest path/to/test_file.py::test_name`
- Stop early: `pytest -x`
- Show useful failure detail: `pytest -q -ra`
- Select by marker: `pytest -m "integration"`
- Select by name expression: `pytest -k "parser and not slow"`

## Test Mechanics

- Use plain `assert` statements; pytest rewrites them into useful failure output.
- Use `pytest.raises(ExpectedError, match="message")` for expected exceptions.
- Use `tmp_path` for filesystem tests and `monkeypatch` for environment or attribute replacement.
- Use fixtures for shared setup that has a clear name and lifecycle.
- Use parametrization when the same behavior should hold across multiple inputs.

## Fixture Rules

- Keep fixture scope as narrow as possible.
- Avoid autouse fixtures unless the setup is truly universal and obvious.
- Prefer returning values from fixtures; use `yield` only when teardown is needed.
- Do not hide important test inputs inside distant fixtures.

## Example

```python
import pytest


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("1", 1), (" 2 ", 2)],
)
def test_parse_int_strips_whitespace(raw, expected):
    assert parse_int(raw) == expected


def test_parse_int_rejects_words():
    with pytest.raises(ValueError, match="integer"):
        parse_int("one")
```

## Troubleshooting

- If collection fails, fix imports, fixtures, or syntax before debugging assertions.
- If a fixture makes the test hard to read, inline the setup or rename the fixture.
- If tests are order-dependent, isolate shared state and remove hidden global mutation.
