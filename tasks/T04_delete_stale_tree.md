# T04 — Delete stale jchick/ top-level tree

## Goal
Remove the top-level `jchick/` directory. It is a duplicate of
`src/jchick/` that is not packaged (`pyproject.toml:24` says
`packages = ["src/jchick"]`) and has caused the MJPEG work to land in
the wrong tree. Keeping it around guarantees this happens again.

## Context
Two parallel trees existed:
- `src/jchick/` — packaged, installed on Jetson
- `jchick/` — orphaned, never installed

After T01-T03 the `src/jchick/` tree has everything the stale tree had,
plus the overlay and bug fixes. The stale tree can be deleted safely.

## Files touched
- `jchick/` directory — DELETED (9 .py files + __pycache__)

## Pre-conditions
- [ ] T01 must be complete
- [ ] T02 must be complete
- [ ] T03 must be complete

## Exact changes required

### Change 1: Delete the stale tree

Action: DELETE the entire `jchick/` directory at repo root.

    rm -rf jchick/

Confirm `pyproject.toml` still points at `src/jchick` (it does — line 24).
Confirm no other file imports from the top-level `jchick` package (they
should all use the installed package via `src/`).

## Validation plan

    # Tree is gone
    ls jchick/ 2>&1
    # Expected: No such file or directory

    # Package still installs and imports from src/
    python3 -c "import jchick, inspect; print(inspect.getsourcefile(jchick))"
    # Expected: .../src/jchick/__init__.py

    # No stale references in the rest of the repo
    rg --no-heading -g '!tasks/**' "from jchick\.|import jchick" .
    # Expected: only src/jchick/* matches (internal package imports)

    # Build still works
    python3 -m py_compile src/jchick/*.py
    # Expected: no output, exit 0

## Success criteria
- [ ] `jchick/` directory does not exist
- [ ] `python -c "import jchick"` still resolves to `src/jchick/`
- [ ] `py_compile` passes for all src/jchick/*.py
- [ ] No test or script references the deleted tree

## Rollback
    git checkout jchick/

## Next task
After confirmed complete: execute T05