# T10 — Apply the winning prompt, confirm live, close out (CONDITIONAL)

## Goal
If T09 confirmed the de-biased prompt recognizes chickens on the baseline frames (same model, no new hallucinations), ship it: copy the de-biased prompt text into the live `PROMPT` constant, deploy, confirm during one daylight window with the live pipeline, then disable the audit dump (diagnosis complete). Keep `llava-phi3:3.8b` — no model change. Write the final outcome.

## Context
T09 ran the A/B and decided `PROMPT FIX CONFIRMED`: the fault was the `12c7722` prompt over-correction, not the model. This task promotes the tested prompt from `assets/gate_prompt_debiased.txt` into the shipped `PROMPT` so the live pipeline uses it, then verifies end-to-end in daylight (the offline harness proved it against captured frames; the live run confirms the whole pipeline still fires `inference.fired` / `detection.chicken` when chickens are present). The audit dump is a diagnostic tool with disk/IO cost, so it is disabled once diagnosis is done.

This task is CONDITIONAL: only run if T09 = `PROMPT FIX CONFIRMED`. If T09 said `PROMPT FIX INSUFFICIENT` or `HARDWARE/FRAMING`, do not run — mark this task dropped in PLAN.md. The same model is kept throughout; the `--model` harness capability remains available for any future model experiments, but no task forces a swap.

## Files touched
- `src/jchick/ollama.py` — replace the `PROMPT` constant with the de-biased text (synced from `assets/gate_prompt_debiased.txt`).
- `.env.example` — revert `JCHICK_GATE_AUDIT_DIR=` to empty (disable audit; diagnosis complete).
- `tasks/diff-gate-findings-2026-08-28.md` — append the live-confirmation result and final outcome.

## Pre-conditions
- [ ] T09 complete with decision `PROMPT FIX CONFIRMED`.
- [ ] If T09 decision was `PROMPT FIX INSUFFICIENT` or `HARDWARE/FRAMING`, this task is DROPPED.

## Exact changes required

### Change 1: promote the de-biased prompt to the shipped PROMPT
File: `src/jchick/ollama.py`
Action: REPLACE the `PROMPT = ( ... )` block (the constant defined around lines 23-43) with the de-biased text, expressed as the same parenthesized string-concatenation style as the current constant.

Before (current shipped `PROMPT`, lines ~23-43):
```python
PROMPT = (
     'You are a chicken-coop security camera analyzing one still frame. '
     'Reply ONLY with one JSON object on a single line, matching this '
     'schema. No prose. No code fence. No trailing text. No newlines in '
     'strings.\n'
     'Schema: {"chickens": <int>, "other_animals": [<lowercase species>], '
     '"movement": "still"|"calm"|"active", "confidence": <float 0..1>, '
     '"notes": "<one short sentence>"}\n'
     'Rules:\n'
     '- chickens = number of chickens VISIBLE in the frame. If no chickens '
     'are visible, use 0. Do not assume chickens are present.\n'
     '- other_animals = list of other animal species clearly visible in the '
     'frame. Empty list if none.\n'
     '- If the frame shows no animals at all, return exactly: '
     '{"chickens": 0, "other_animals": [], "movement": "still", '
     '"confidence": 0.9, "notes": "No animals visible."}\n'
     '- confidence = how sure you are animals are actually present, 0..1. '
     'Use under 0.5 if unclear, dark, or motion-blurred.\n'
     '- Do not invent animals. An empty coop is the expected, correct '
     'answer when no animals are visible.'
)
```

After (must match `assets/gate_prompt_debiased.txt` content exactly, as a Python string literal):
```python
PROMPT = (
     'You are a chicken-coop security camera analyzing one still frame. '
     'Reply ONLY with one JSON object on a single line, matching this '
     'schema. No prose. No code fence. No trailing text. No newlines in '
     'strings.\n'
     'Schema: {"chickens": <int>, "other_animals": [<lowercase species>], '
     '"movement": "still"|"calm"|"active", "confidence": <float 0..1>, '
     '"notes": "<one short sentence>"}\n'
     'Rules:\n'
     '- chickens = the number of chickens VISIBLE in the frame, even if '
     'only partially visible, seen at an angle, in low light, or while '
     'moving. Use 0 only if you genuinely see no chickens.\n'
     '- other_animals = list of other animal species clearly visible in the '
     'frame. Empty list if none.\n'
     '- Do not invent animals that are not visible. But do not default to '
     'empty — report what you actually see.\n'
     '- confidence = how sure you are animals are actually present, 0..1. '
     'Use under 0.5 if unclear, dark, or motion-blurred.\n'
     '- movement = still | calm | active, judged from this single frame.'
)
```

After editing, verify the Python literal and the text file produce the same string:
```
python3 -c "from jchick.ollama import PROMPT; from pathlib import Path; \
print(PROMPT == Path('assets/gate_prompt_debiased.txt').read_text().strip())"
# Expected: True
```
If False, reconcile whitespace/newlines until they match. Keeping the file and the constant in sync is what makes future A/B tests via the harness representative of the live prompt.

### Change 2: disable the audit dump
File: `.env.example`
Action: REPLACE `JCHICK_GATE_AUDIT_DIR=/var/lib/jchick/gate-audit` with:
```
JCHICK_GATE_AUDIT_DIR=
```
(Diagnosis complete; audit has disk/IO cost and should not run permanently. Leave the comment block intact.)

### Change 3: deploy + confirm live (deploy skill)
```
rsync -av --delete --exclude .git --exclude .venv --exclude __pycache__ \
  --exclude .pytest_cache --exclude .ruff_cache --exclude .opencode \
  ./ pichick@192.168.0.18:/tmp/jetson-pichick/
ssh pichick@192.168.0.18 'sudo bash /tmp/jetson-pichick/scripts/install.sh'
ssh pichick@192.168.0.18 'sudo systemctl restart jetson-pichick'
```

## Validation plan
```
# 1. local: prompt constant == debiased file
python3 -m py_compile src/jchick/ollama.py
python3 -c "from jchick.ollama import PROMPT; from pathlib import Path; \
print(PROMPT == Path('assets/gate_prompt_debiased.txt').read_text().strip())"
# Expected: True

# 2. service active
ssh pichick@192.168.0.18 'systemctl is-active jetson-pichick'
# Expected: active

# 3. audit disabled (no "gate audit: dumping" line; dir stops growing)
ssh pichick@192.168.0.18 'journalctl -u jetson-pichick -n 30 --no-pager | grep -c "gate audit: dumping"'
# Expected: 0

# 4. live daylight confirmation (REQUIRES chickens present + motion)
ssh pichick@192.168.0.18 'journalctl -u jetson-pichick --since "20 min ago" --no-pager' | grep -E '"chickens":[1-9]|inference.fired|detection.chicken'
# Expected: at least one hit — the live pipeline now fires on chickens with the
# de-biased prompt and the same model. This is the end-to-end confirmation that
# the offline A/B result holds in the live pipeline.

# 5. append final outcome to the findings doc
# (manual: add the live-confirmation lines + "RESOLVED — prompt was the fault,
#  llava-phi3:3.8b retained" to tasks/diff-gate-findings-2026-08-28.md)
```

## Success criteria
- [ ] `PROMPT` constant == `assets/gate_prompt_debiased.txt` content (Python check prints True).
- [ ] Service `active`; no `gate audit: dumping` line (audit disabled).
- [ ] During a daylight window with chickens present, journal shows `chickens>0` / `inference.fired` / `detection.chicken` — the live pipeline now detects chickens with the same model.
- [ ] `tasks/diff-gate-findings-2026-08-28.md` records the live confirmation and final resolution.
- [ ] `git diff` shows only `src/jchick/ollama.py`, `.env.example`, and the findings doc.

## Rollback
If the live confirmation fails (pipeline still says 0 in daylight despite the offline A/B passing — e.g. a distribution mismatch between captured frames and live frames), revert:
```
git checkout src/jchick/ollama.py .env.example
# redeploy (deploy skill)
```
and reopen in the findings doc: the offline A/B was misleading; fall back to `PROMPT FIX INSUFFICIENT` and consider hardware/lighting or a model experiment via the harness.

## Next task
None. Update PLAN.md: mark T10 done and the diff-gate phase RESOLVED (prompt was the fault; `llava-phi3:3.8b` retained; eval harness remains for future prompt/model testing).