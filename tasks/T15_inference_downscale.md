# T15 — llama-server OOM fix (watermark recycle) + inference downscale hygiene

## Goal
Stop the nightly llama-server OOM-kill loop (179 kills since Aug 20,
~7.0 GB at kill, ~120 MB/min growth) by recycling the Ollama runner via
`keep_alive: 0` when its VmRSS crosses a watermark (6000 MB), and ship a
640-px inference downscale as payload hygiene (NOT claimed to fix memory).

## Context
Research (2026-09-01) overturned the "fewer pixels = less memory" theory
for llava-phi3:3.8b: its mmproj GGUF (projector sha256 004fc096…)
declares `clip.vision.image_size=336`, `patch_size=14`, no
`image_res_candidates` — llama.cpp b10630's mtmd preprocessor letterboxes
EVERY input to 336x189 content inside a fixed 336x336 canvas → exactly
576 vision tokens regardless of input resolution. The observed ~120
MB/min linear growth instead matches ollama#18106 (open, 0.32.1):
llama-server leaks ~5-12 MiB of anonymous memory PER REQUEST, survives
idle, reproduces on text-only models. ollama#18099 (open) shows the
same; context-length shrink does not help. The documented workaround:
recycle the runner with a `keep_alive: 0` request. Our watermark
trigger formalizes it: recycle exactly when needed, at a frame
boundary, before the kernel does it for us (each kill costs a ~65 s
reload + alert.ollama burst; swap sits at 4 GB / 92%).

Two changes, both inside `OllamaClient.describe()`:
1. RECYCLE (the fix): read llama-server VmRSS from /proc/<pid>/status
   before each call; crossing JCHICK_OLLAMA_RECYCLE_RSS_MB (default
   6000) embeds `"keep_alive": 0` in that /api/chat request so the
   runner unloads after serving.
2. DOWNSCALE (hygiene): cap longest side at JCHICK_INFERENCE_MAX_DIM
   (default 640, LANCZOS, JPEG q85) before base64 — ~4x smaller
   payloads, faster server-side decode.

Full reviewed plan: `.omo/plans/inference-downscale.md`
(SHA-256 5422a665c8ed63a2e0f57a70f27a2909b23a747004a74029d74ab6e933322212,
dual-approved round-2, 2026-09-01).

## Files touched
- `src/jchick/ollama.py` — both helpers + describe() wiring + ctor params
- `src/jchick/config.py` — both knobs + parsing (invalid → default + warning)
- `src/jchick/app.py` — pass both knobs to OllamaClient (~line 72)
- `.env.example` — document both knobs
- `src/jchick/__init__.py` — 0.2.2
- `pyproject.toml` — stale version 0.1.0 → 0.2.2 (consistency)
- `scripts/ab_inference_resize.py` — NEW Jetson harness
- `tasks/T15_inference_downscale.md` (this file) + `tasks/PLAN.md` + `RESUME.md`

## Pre-conditions
- [x] Plan reviewed + dual-approved (round-2, 2026-09-01)
- [ ] Dep precheck: `/usr/bin/python3 -c "import httpx, PIL"` — httpx was
      missing on the dev box 2026-09-02; installed via
      `/usr/bin/python3 -m pip install --user httpx pillow` per the plan
      contingency. PIL.__version__ must be >= 9.1 (Image.Resampling).
- Dev-box scenario scripts run under `/usr/bin/python3` (repo venv hangs).

## Exact changes (content-based; plan todos 2-5 hold verbatim specs)

### Todo 2 — resize helper (ollama.py)
- Add imports `io` + `from PIL import Image` (check existing first; never
  replace an existing import line to add one).
- Module helper `_resize_for_inference(jpeg: bytes, max_dim: int) -> bytes`:
  `max_dim <= 0` → original bytes; PIL open; `max(w, h) <= max_dim` →
  original (no upscale — also protects small crops); else scale to
  longest side = max_dim via `Image.Resampling.LANCZOS` (pillow>=10
  removed the bare alias; pyproject pins >=10), save JPEG quality 85 to
  BytesIO; `except Exception` → WARNING + return ORIGINAL bytes
  (degraded inference beats none; no bare except, no re-raise).
- Ctor: keyword param `max_image_dim: int = 0` → `self.max_image_dim`.
- `describe()`: FIRST statement, before the b64encode line:
  `if self.max_image_dim > 0: jpeg = _resize_for_inference(jpeg, self.max_image_dim)`.
- PROMPT + species-guard comment untouched (load-bearing).

### Todo 3 — RSS watermark recycle (ollama.py)
- Module helper `_llama_server_rss_mb() -> int | None`: `pgrep -f
  llama-server`; zero matches → `None` + DEBUG log ("no llama-server
  runner — expected post-recycle or fresh boot"; DEBUG not WARNING —
  no-runner is the normal post-recycle state, a WARNING would cry wolf
  in the alarm path the post-deploy verification greps); matches but
  ALL /proc/<pid>/status reads fail or exception → `None` +
  rate-limited WARNING (module timestamp, once per 10 min); else parse
  each `VmRSS:` line (kB → MB), return the max (conservative: small
  false pgrep matches never trigger; big ones trigger a harmless early
  recycle).
- Module rate-limited warning helper for read failures.
- Ctor: keyword param `recycle_rss_mb: int = 0` (0 = disabled).
- `describe()`: after resize, before building the request body: if
  enabled and `rss is not None and rss >= self.recycle_rss_mb` → put
  `"keep_alive": 0` at the TOP LEVEL of the /api/chat JSON body + log
  WARNING `llama-server VmRSS {rss}MB >= watermark {mb}MB - recycling
  runner after this call (next inference pays model reload)`.
  `rss is None` → proceed WITHOUT recycle and WITHOUT caller-side
  warning (helper's differentiated logging already handled
  diagnostics). Response handling UNCHANGED — next call pays the ~65 s
  reload, inside the existing 120 s timeout.
- pgrep + PIL run synchronously inside async describe() — ms-scale,
  accepted choice (sync diff-gate evaluate in the same loop is heavier).

### Todo 4 — config + wiring + version
- `config.py`: fields `inference_max_dim: int = 640`,
  `recycle_rss_mb: int = 6000`; `from_env` parses
  JCHICK_INFERENCE_MAX_DIM / JCHICK_OLLAMA_RECYCLE_RSS_MB: missing →
  default silently; invalid int → default + logged warning (config.py
  has no logging today — add `import logging` + module logger); `0`
  honored as disabled. Deliberate divergence from older knobs (which
  crash on invalid env): a bad knob must not blind the coop.
- `app.py`: extend `OllamaClient(cfg.ollama_url, allowed_species=...)`
  with `max_image_dim=cfg.inference_max_dim,
  recycle_rss_mb=cfg.recycle_rss_mb`.
- `.env.example`: both vars + one-line comments each.
- `__init__.py` → 0.2.2; `pyproject.toml` version → 0.2.2.

### Todo 5 — Jetson harness `scripts/ab_inference_resize.py`
Self-contained; stdlib urllib + subprocess + PIL only; on the box run
with `/opt/jchick/.venv/bin/python` as pichick from staging
(`/tmp/jetson-pichick`), importing the STAGED `src/jchick` via sys.path
so it exercises the new code.
- `--self-test` (offline, dev box): synthesized JPEGs, resize import,
  verdict math on fake metrics — null/absent prompt_eval_count,
  insufficient-signal case, L1-confirmed case.
- Online: ONE warmup /api/chat call FIRST (pays the lazy model load
  ~2.5 GB RSS step, anchors the llama-server PID + post-load baseline;
  EXCLUDED from every slope — arm-1 call-1's load step would otherwise
  confirm tautologically). Header: `ollama -v` + active model read from
  `/etc/jchick/jchick.env` JCHICK_DETAIL_MODEL (never hardcode). Grab
  up to 3 distinct 1280x720 frames ~30 s apart (same ffmpeg one-shot as
  capture.py:119-137; record time-of-day; empty frames give trivial
  0=0 parity, noted). Arms full(15) → resized(15) → full(15), frames
  CYCLED 1,2,3,1,2,3… (no identical consecutive calls; defeats
  KV-prefix-reuse confounds). Measurement calls back-to-back. Per call:
  wall time, raw prompt_eval_count (absent/1/null tolerated —
  ollama#6392 documents broken counts for this model),
  prompt_eval_duration, eval_count, FULL parsed result JSON, and
  llama-server VmRSS/RssAnon/VmSwap before/after.
- PER-CALL HTTP TIMEOUT = 120 s, matching the production client (urllib
  defaults to blocking forever — a wedged runner must abort the
  harness, not hang the gated window). Per-call timeout, repeated HTTP
  errors, or frame-grab failure → infrastructure failure → clean
  abort, NO partial verdict. On abort: restart `jetson-pichick` FIRST,
  then decide on re-run.
- llama-server PID change vs the post-warmup anchor mid-run → clean
  abort + re-run (runner spawning AT the warmup call is expected, not
  an abort).
- Token pressure: where prompt_eval_count is a plausible real value,
  compare prompt+text totals against num_ctx=2048; flag any overflow as
  a LABELED CONFOUND (a full-arm overflow the resized arm doesn't share
  would make G4 measure truncated-vs-untruncated — decision-relevant,
  must be stated, not hidden).
- G4 verdict + 0.80-straddle detection computed on POST-`_build_result`
  values (poultry folding changes `chickens`; splits clamp conf to 0.5
  — the consumer sees folded values through its conf >= 0.80 gate).
  Raw JSON still recorded per call for diagnosis. Pairs: per frame,
  each of 5 full-arm samples vs each of 5 resized-arm samples (SAME
  frame — 25 pairs/frame). Pass = chickens identical AND
  |conf_full − conf_resized| ≤ 0.05 on every pair; every gate
  straddle (e.g. 0.82→0.78) surfaced as a labeled verdict entry even
  when the gate passes. Record other_animals parity.
- Verdicts: L1 = full-arm RSS least-squares slope over the 15 measured
  calls, reported BOTH as MB/min and MB/request (leak is per-request;
  MB/request is pacing-independent), confirmed when ≥ 10 MB/min at
  back-to-back pacing (expected 5-12 MiB/request). G2 = resized slope
  vs both full arms (parity expected). Matrix:
  L1 confirmed + G4 pass → deploy both / L1 confirmed + G4 fail →
  HALT / L1 not reproduced → HALT + report.

## Validation plan
1. Dev box: `/usr/bin/python3 -m py_compile` on all touched sources.
2. Scenario matrices (ad-hoc scripts in /tmp, transcripts filed below;
   never committed): resize 10 cases (1280x720→640x360, 720x1280→
   360x640, 1280x800→640x400, passthrough 640x480 / 400x300 / 640x640,
   bad-bytes→original+warning, max_dim=0→original; outputs valid JPEG,
   aspect within 1px); recycle 6 cases (over-watermark → body carries
   keep_alive:0 + WARNING; under → absent; no-runner → DEBUG only;
   unreadable /proc → rate-limited WARNING once per 10 min; disabled →
   helper never called; consecutive over-watermark → both carry); config
   matrix both knobs (320→320, abc→640+warning, 0→0; 5500→5500,
   xyz→6000+warning, 0→0; missing→defaults).
3. `/usr/bin/python3 scripts/ab_inference_resize.py --self-test` → exit 0.
4. Jetson harness [USER GATE: service stop ~20-25 min + ollama unit
   restart]: warmup + 15/15/15 measured calls, verdict matrix recorded;
   `jetson-pichick` restarted IMMEDIATELY after (before verdict
   analysis). Gate prompt must state the door-controller tradeoff:
   during the window the consumer gets no fresh inference.fired, and at
   dusk that is exactly when it decides "all birds home" before close
   (coop_door_controller/src/nats_integration.c:380). Options: (a) dusk
   run — bird-visible frames strengthen G4, blind window during the
   close decision; (b) after roost/door-close — night frames, G4 = weak
   parity, noted in verdict.
5. Live post-deploy [USER GATE]: startup shows 0.2.2; /etc/jchick/
   jchick.env seeded with both knobs by install.sh; dev-box
   `nats sub 'home.coop.>'` shows live inference.fired with top-level
   chickens present and key order unchanged vs 0.2.1; 60-min window
   asserted to overlap ≥1 kept-frame inference (a quiet window can't
   demonstrate absence of kills): zero llama-server OOM kills in
   journalctl -k, ~0 alert.ollama, recycle WARNING in
   journalctl -u jetson-pichick when the watermark is crossed (if not
   crossed in-window, record the RSS trajectory and defer recycle-event
   proof to the T14 overnight soak).

## Success criteria
- All scenario matrices green; self-test exit 0.
- Harness verdict: L1 confirmed on THIS box + G4 pass → deploy both.
- v0.2.2 live: zero OOM kills first hour (night baseline ~11/hr),
  recycle events visible when crossed, no reload > 120 s.
- Wire contract intact — any payload claim cites BOTH the publish side
  (src/jchick/app.py) AND the consumer parse
  (coop_door_controller/src/nats_integration.c:226-232).
- No regression: dusk chicken counts correct, phantom species 0,
  07:15 door-open reset intact.

## Rollback
Set `JCHICK_OLLAMA_RECYCLE_RSS_MB=0` and/or `JCHICK_INFERENCE_MAX_DIM=0`
in `.env.example` + normal redeploy (install.sh re-seeds
/etc/jchick/jchick.env with a .bak; NEVER hand-edit the box env).
Config-only disable; no code revert needed.

## Contingency (if recycle proves insufficient)
systemd-timer recycle and/or ollama unit MemoryMax — separate user
decision, NOT this task. Other future levers (out of scope):
OLLAMA_FLASH_ATTENTION, KV quant (#8597 mitigation list), swap/kernel
tuning, model swap.

## Evidence trail
- mmproj GGUF fixed-336 letterbox: llava-phi3:3.8b projector
  sha256 004fc096…, image_size=336, patch=14, mlp projector → 576
  vision tokens constant across all Ollama versions for this model.
- ollama#18106 (open, 0.32.1): ~5-12 MiB/request anonymous leak,
  survives idle, text-only repro; keep_alive:0 = citable workaround.
- ollama#18099 (open, 0.32.15): parallel report; KV/context not the
  leaking component.
- OOM history (journal sweep 2026-09-01): 179 kills since Aug 20
  (10-28/day), kill at ~7.0 GB anon-rss, ~120 MB/min under continuous
  night inference, swap 4 GB at 92%, ~23 alert.ollama in the hour
  before the sweep, each kill ≈ 65 s reload.
- Version caveat: `ollama -v` recorded by the harness; ≥ 0.12.4-rc6
  carries the #12283 Tegra memory-accounting fix.

## Results
(pending execution — verdict tables + transcripts filed here)

## Next task
After verdict + deploy + soak handoff: close T15, update PLAN.md; T14
morning protocol absorbs the overnight soak checks (kill count 0,
recycle-event count, reloads > 120 s, alert.ollama, phantom species).