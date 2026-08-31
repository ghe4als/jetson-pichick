# T08 — Deploy T06+T07 and capture the "before" baseline

## Goal
Ship the audit dump (T06) and eval harness (T07) to the Jetson, enable the audit dump, and during a daylight window with chickens present capture the dismissed frames. These frames + their JSON sidecars are the fixed A/B test set AND the "before" results (each sidecar holds the current shipped prompt's verdict). Keep `llava-phi3:3.8b` — this task does not change the model or the prompt.

## Context
T06 ships audit disabled; T07 ships the harness (which does not affect the live pipeline — `describe`'s new `prompt` param defaults to the shipped `PROMPT`). To capture the baseline we enable the audit dir by setting it in `.env.example` to a path under `/var/lib/jchick` (created and owned by the `jchick` user by `scripts/install.sh`, so writable). `install.sh` re-seeds `/etc/jchick/jchick.env` from `.env.example` on every run, so the change ships with a normal deploy.

After this deploy, the service writes `<ts>_<score>.jpg` + `.json` pairs into `/var/lib/jchick/gate-audit/` for every gate-dismissed frame. Each `.json` is the "before" verdict (current prompt + current model). T09 then runs the harness with the de-biased prompt against these same frames for the "after" comparison — offline, same model.

This task uses the `deploy` skill (rsync + install.sh + service restart + health checks).

## Files touched
- `.env.example` — set `JCHICK_GATE_AUDIT_DIR=/var/lib/jchick/gate-audit` (was empty). No model or prompt change.

## Pre-conditions
- [ ] T06 complete and committed.
- [ ] T07 complete and committed.
- [ ] Working tree clean (deploy skill pre-flight).

## Exact changes required

### Change 1: enable audit dir for the baseline capture
File: `.env.example`
Action: REPLACE the line `JCHICK_GATE_AUDIT_DIR=` with:
```
JCHICK_GATE_AUDIT_DIR=/var/lib/jchick/gate-audit
```
Leave the surrounding comment block from T06 intact. This is a temporary enablement for the baseline run; T10 reverts it to empty once diagnosis is complete (audit has disk/IO cost and should not run permanently). Do NOT change `JCHICK_GATE_MODEL`, `JCHICK_DETAIL_MODEL`, or the prompt — the baseline must use the current shipped state.

## Validation plan
Follow the `deploy` skill procedure. Commands (run from repo root):

```
# 0. Pre-flight
git status --short
git log --oneline -3
# Expected: clean tree; T06+T07 commits on top.

# 1. rsync to Jetson
rsync -av --delete \
  --exclude .git --exclude .venv --exclude __pycache__ \
  --exclude .pytest_cache --exclude .ruff_cache --exclude .opencode \
  ./ pichick@192.168.0.18:/tmp/jetson-pichick/

# 2. install.sh (re-seeds env from .env.example, rebuilds venv, reinstalls unit)
ssh pichick@192.168.0.18 'sudo bash /tmp/jetson-pichick/scripts/install.sh'
# Expected: completes without error, prints "Install complete."

# 3. restart service
ssh pichick@192.168.0.18 'sudo systemctl restart jetson-pichick'

# 4a. service active
ssh pichick@192.168.0.18 'systemctl is-active jetson-pichick'
# Expected: active

# 4b. startup + audit line + no tracebacks
ssh pichick@192.168.0.18 'journalctl -u jetson-pichick -n 40 --no-pager'
# Expected: a status.startup line; a "gate audit: dumping dismissed frames to
# /var/lib/jchick/gate-audit (cap=500)" info line; no tracebacks.

# 5. daylight capture (REQUIRES a window with chickens present + motion)
#    Let it run during daylight, then confirm the test set was collected:
ssh pichick@192.168.0.18 'ls -1 /var/lib/jchick/gate-audit/*.jpg | wc -l; ls -1 /var/lib/jchick/gate-audit/*.json | wc -l'
# Expected: equal jpg/json counts, > 0 after a daylight window with motion.
# Verify a sidecar carries a current-prompt "before" verdict:
ssh pichick@192.168.0.18 'cat "$(ls /var/lib/jchick/gate-audit/*.json | head -1)"'
# Expected: JSON with "chickens": 0, "model": "llava-phi3:3.8b", diff_score, etc.

# 6. copy the test set to the dev box for T09 inspection + backup
mkdir -p /tmp/jchick-audit-baseline
rsync -av pichick@192.168.0.18:/var/lib/jchick/gate-audit/ /tmp/jchick-audit-baseline/
# Expected: jpg+json pairs copied.
```

## Success criteria
- [ ] `is-active` returns `active`.
- [ ] Journal shows the `gate audit: dumping dismissed frames to ...` info line; no tracebacks.
- [ ] After a daylight window with motion, `/var/lib/jchick/gate-audit/` has matching jpg+json pairs with `model: llava-phi3:3.8b` and `chickens: 0` (the "before" baseline).
- [ ] Baseline frames + sidecars copied to `/tmp/jchick-audit-baseline/` on the dev box.

## Rollback
- Revert `.env.example` audit line to `JCHICK_GATE_AUDIT_DIR=`, redeploy.
- Or full code rollback: `git revert` T06+T07 commits, redeploy.

## Next task
After confirmed complete (baseline captured): execute T09 ("after" test — run the de-biased prompt against the baseline frames via the harness, same model). T09 needs the baseline frames on the Jetson (they are, at /var/lib/jchick/gate-audit) — no further daylight window required.