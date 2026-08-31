# T07 — Offline gate eval harness

## Goal
Add a CLI that runs the gate `describe()` call against a directory of saved JPEG frames using a chosen prompt and model, printing each frame's verdict plus a summary. This is the A/B testing tool: it lets you compare the current prompt vs the de-biased prompt (and any model vs any other model) against the SAME fixed set of frames offline — no deploy, no daylight window needed. Same model (`llava-phi3:3.8b`) by default; `--model` lets you test others, which is the "be sure we can change between them for testing" requirement.

## Context
The investigation's hypothesis is that `llava-phi3:3.8b` is capable but commit `12c7722` over-biased the prompt toward "empty coop." To prove/disprove that cheaply we need to run the de-biased prompt against the same frames the current prompt failed on (captured by T06). Re-deploying and waiting for daylight for every prompt tweak is far too slow. Instead: capture frames once (T08), then iterate prompts/models offline against them with this harness.

The harness reuses `OllamaClient` from the package, so it produces results identical to the live pipeline's gate call. To support swapping the prompt without editing source, `OllamaClient.describe` gains a `prompt` parameter (default = the shipped module-level `PROMPT`), so the live code path is unchanged. The de-biased prompt ships as a text file (`assets/gate_prompt_debiased.txt`) the harness reads via `--prompt-file`; omitting `--prompt-file` uses the current shipped `PROMPT` (the "before" baseline).

## Files touched
- `src/jchick/ollama.py` — add `prompt: str = PROMPT` keyword param to `OllamaClient.describe`; use it in the system message instead of the bare `PROMPT` constant.
- `scripts/eval_gate.py` — NEW. Async CLI: iterate a frame dir, run `describe` with chosen model+prompt, print table + summary.
- `assets/gate_prompt_debiased.txt` — NEW. The de-biased prompt text (the investigation's "prompt tuning" suggestion), consumed by `--prompt-file`.

## Pre-conditions
- [ ] T06 must be complete and committed.

## Exact changes required

### Change 1: make describe() prompt-injectable
File: `src/jchick/ollama.py`
Action: REPLACE the `describe` signature and the system-message line.

Before (current, around lines 67-74):
```python
    async def describe(self, jpeg: bytes, *, model: str) -> VisionResult:
        b64 = base64.b64encode(jpeg).decode("ascii")
        
        # Use /api/chat endpoint which properly supports format=json
        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": f"System: {PROMPT}"},
                {"role": "user", "content": "Analyze this image.", "images": [b64]},
            ],
```

After:
```python
    async def describe(self, jpeg: bytes, *, model: str, prompt: str = PROMPT) -> VisionResult:
        b64 = base64.b64encode(jpeg).decode("ascii")
        
        # Use /api/chat endpoint which properly supports format=json
        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": f"System: {prompt}"},
                {"role": "user", "content": "Analyze this image.", "images": [b64]},
            ],
```

Everything else in `describe` is unchanged. Existing callers (`Cascade.run`, app inference loop) pass no `prompt`, so they keep using the shipped `PROMPT` — live behavior identical.

### Change 2: the de-biased prompt file
File: `assets/gate_prompt_debiased.txt`
Action: CREATE. Full content (this is the prompt body; `describe` prepends `"System: "`):
```
You are a chicken-coop security camera analyzing one still frame. Reply ONLY with one JSON object on a single line, matching this schema. No prose. No code fence. No trailing text. No newlines in strings.
Schema: {"chickens": <int>, "other_animals": [<lowercase species>], "movement": "still"|"calm"|"active", "confidence": <float 0..1>, "notes": "<one short sentence>"}
Rules:
- chickens = the number of chickens VISIBLE in the frame, even if only partially visible, seen at an angle, in low light, or while moving. Use 0 only if you genuinely see no chickens.
- other_animals = list of other animal species clearly visible in the frame. Empty list if none.
- Do not invent animals that are not visible. But do not default to empty — report what you actually see.
- confidence = how sure you are animals are actually present, 0..1. Use under 0.5 if unclear, dark, or motion-blurred.
- movement = still | calm | active, judged from this single frame.
```

Difference vs the current shipped `PROMPT`: removes the canned `{"chickens": 0, ...}` template, "Do not assume chickens are present," and "An empty coop is the expected, correct answer"; adds partial/angle/low-light/moving guidance and "do not default to empty — report what you actually see." Keeps the anti-invention rule. This is a hypothesis to be measured by the harness (T09), not assumed correct.

### Change 3: the eval harness script
File: `scripts/eval_gate.py`
Action: CREATE. Full content (use the corrected summary-line form shown after the block):
```python
#!/usr/bin/env python3
"""Offline gate-prompt / gate-model A/B eval.

Runs the gate describe() call against a directory of saved JPEG frames
(e.g. the JCHICK_GATE_AUDIT_DIR dump from T06) using a chosen model and
prompt, and prints each frame's verdict plus a summary. Compare prompts
and models against a FIXED set of frames without deploying or waiting
for daylight.

Run on the Jetson (Ollama is at 127.0.0.1:11434), frames already on disk:

  /opt/jchick/.venv/bin/python /opt/jchick/scripts/eval_gate.py \
      --dir /var/lib/jchick/gate-audit \
      --model llava-phi3:3.8b \
      --prompt-file /opt/jchick/assets/gate_prompt_debiased.txt

Omit --prompt-file to use the current shipped PROMPT (the "before" baseline).
Pass --model llava:7b (after `ollama pull llava:7b`) to test a different model
against the same frames. --top N keeps only the N highest-diff-score frames
(the ones most likely to contain real motion).
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from jchick.ollama import OllamaClient, OllamaError, PROMPT, VisionResult


def _frame_score(p: Path) -> float:
    # filenames are <utc_ms>_<diffscore>.jpg — sort by the score suffix.
    stem = p.stem
    try:
        return float(stem.rsplit("_", 1)[1])
    except (IndexError, ValueError):
        return 0.0


async def _eval_one(client: OllamaClient, jpg: Path, model: str, prompt: str) -> VisionResult:
    return await client.describe(jpg.read_bytes(), model=model, prompt=prompt)


async def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", required=True, help="directory of *.jpg frames to evaluate")
    ap.add_argument("--model", default=os.environ.get("JCHICK_GATE_MODEL", "llava-phi3:3.8b"),
                    help="Ollama model (default: llava-phi3:3.8b or $JCHICK_GATE_MODEL)")
    ap.add_argument("--prompt-file", help="text file with a prompt body; omit to use the shipped PROMPT")
    ap.add_argument("--top", type=int, default=0,
                    help="keep only the N highest-diff-score frames (0 = all)")
    args = ap.parse_args()

    base_url = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
    prompt = PROMPT
    if args.prompt_file:
        prompt = Path(args.prompt_file).read_text().strip()

    d = Path(args.dir)
    frames = sorted(d.glob("*.jpg"), key=_frame_score, reverse=True)
    if args.top > 0:
        frames = frames[: args.top]
    if not frames:
        print(f"no *.jpg frames in {d}", file=sys.stderr)
        return 1

    src = f"file:{args.prompt_file}" if args.prompt_file else "shipped PROMPT"
    print(f"# eval: {len(frames)} frames | model={args.model} | prompt={src}")
    print(f"# ollama: {base_url}")
    print(f"{'frame':<32} {'chk':>3} {'other':<18} {'move':<6} {'conf':>5} {'ms':>5} notes")
    client = OllamaClient(base_url)

    n_chickens = 0
    conf_sum = 0.0
    lat_sum = 0
    ok = 0
    for jpg in frames:
        try:
            r = await _eval_one(client, jpg, args.model, prompt)
        except OllamaError as e:
            print(f"{jpg.name:<32} ERROR {e}")
            continue
        if r.chickens > 0:
            n_chickens += 1
        conf_sum += r.confidence
        lat_sum += r.latency_ms
        ok += 1
        print(f"{jpg.name:<32} {r.chickens:>3} {','.join(r.other_animals) or '-':<18} "
              f"{r.movement:<6} {r.confidence:>5.2f} {r.latency_ms:>5} {r.notes}")

    mean_conf = conf_sum / ok if ok else 0.0
    mean_lat = lat_sum // ok if ok else 0
    print()
    print(f"# summary: {ok}/{len(frames)} ok | chickens>0 on {n_chickens} frames | "
          f"mean conf={mean_conf:.2f} | mean lat={mean_lat}ms")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
```

## Validation plan
Run from the repo root on the dev box (Ollama may not be present locally — these are compile/arg checks, not live inference):

```
python3 -m py_compile src/jchick/ollama.py scripts/eval_gate.py
# Expected: no output, exit 0

python3 -c "from jchick.ollama import OllamaClient; import inspect; sig=inspect.signature(OllamaClient.describe); print('prompt' in sig.parameters); print(sig.parameters['prompt'].default)"
# Expected:
# True
# PROMPT   (i.e. the default is the shipped PROMPT constant)

python3 scripts/eval_gate.py --help
# Expected: usage text printed, exit 0

test -f assets/gate_prompt_debiased.txt && head -1 assets/gate_prompt_debiased.txt
# Expected: "You are a chicken-coop security camera analyzing one still frame. ..."
```

Live-inference validation happens on the Jetson in T09 against the T08 baseline frames. It cannot run on the dev box (no Ollama, no frames yet).

## Success criteria
- [ ] `py_compile` passes for both files.
- [ ] `describe` has a `prompt` param defaulting to `PROMPT`; existing callers unchanged.
- [ ] `eval_gate.py --help` works.
- [ ] `assets/gate_prompt_debiased.txt` exists with the de-biased prompt.
- [ ] `git diff` shows only the 3 files listed (ollama.py, scripts/eval_gate.py new, assets/gate_prompt_debiased.txt new).

## Rollback
```
git checkout src/jchick/ollama.py
rm scripts/eval_gate.py assets/gate_prompt_debiased.txt
```

## Next task
After confirmed complete: execute T08 (deploy T06+T07, enable audit, capture the "before" baseline).