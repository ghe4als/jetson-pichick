#!/usr/bin/env python3
"""A/B harness: llama-server per-request leak (L1), byte-effect (G2), quality parity (G4).

Runs ON THE JETSON against the live Ollama, importing the STAGED src/jchick
(relative to this script) so it exercises the new v0.2.2 code. Stdlib
urllib + subprocess + PIL only; run with /opt/jchick/.venv/bin/python
(or any python with PIL) as pichick from the staging tree.

Protocol (plan: .omo/plans/inference-downscale.md, Verification layer 2):
  header   ollama -v + active model (from /etc/jchick/jchick.env, never hardcoded)
  warmup   ONE /api/chat call - pays the lazy model load (~2.5 GB RSS step),
           anchors the llama-server PID set + post-load RSS baseline.
           EXCLUDED from every slope; the runner spawning AT this call is
           expected, not an abort.
  frames   up to 3 distinct 1280x720 frames ~30 s apart (ffmpeg one-shot,
           same as capture.py). Warmup uses the first grab.
  arms     full(15) -> resized(15) -> full(15), frames CYCLED (1,2,3,1,2,3,...)
           so no identical consecutive calls; back-to-back, no artificial delay.
  verdict  L1: full-arm VmRSS least-squares slope in MB/min AND MB/request
           (leak is per-request; MB/request is pacing-independent); confirmed
           when >= 10 MB/min at back-to-back pacing (expected: ollama#18106,
           ~5-12 MiB/request). G2: resized slope vs both full arms (parity
           expected). G4: per frame, EVERY full-vs-resized pair must match on
           folded chickens AND |conf delta| <= 0.05, computed on POST-
           _build_result values (the consumer sees folded values through its
           conf>=0.80 gate); pairs straddling 0.80 are surfaced even when the
           gate passes.

Abort discipline: a llama-server PID-set change vs the post-warmup anchor,
a per-call timeout (120 s, matching production), repeated HTTP errors, or
frame-grab failure all abort CLEANLY - no partial verdict. On abort,
restart jetson-pichick FIRST, then decide on a re-run.

Offline: --self-test exercises the resize import + verdict math on fake
metrics (null prompt_eval_count, insufficient signal, L1-confirmed) with
no Jetson and no camera.
"""
from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
import time
from io import BytesIO
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from PIL import Image  # noqa: E402

from jchick.ollama import (  # noqa: E402
    PROMPT,
    _build_result,
    _resize_for_inference,
)

HTTP_TIMEOUT_S = 120          # matches the production OllamaClient timeout
CALLS_PER_ARM = 15
L1_FLOOR_MB_MIN = 10.0        # confirmation floor at back-to-back pacing
NUM_CTX = 2048                # production num_ctx; token-pressure confound bound
CONSUMER_CONF_GATE = 0.80     # coop_door_controller INFERENCE_MIN_CONFIDENCE
G4_CONF_TOL = 0.05
ENV_FILE = Path("/etc/jchick/jchick.env")


def log(msg: str) -> None:
    print(f"[harness] {msg}", flush=True)


def parse_env_file(path: Path) -> dict:
    env = {}
    try:
        text = path.read_text()
    except OSError as e:
        # Root-owned on the box (mode 600) - expected when run as pichick.
        # Distinguish "unreadable" from "key absent" so the failure is
        # diagnosable; caller should pass --model/--base-url explicitly.
        print(f"[harness] WARNING: cannot read {path} ({e}) - "
              f"reading knobs from defaults; pass --model explicitly "
              f"(the seeded value is visible via: sudo cat {path})")
        return env
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def llama_pids() -> list[int]:
    try:
        out = subprocess.run(
            ["pgrep", "-f", "llama-server"],
            capture_output=True, timeout=10,
        )
    except Exception:
        return []
    if out.returncode != 0:
        return []
    return [int(p) for p in out.stdout.split()]


def read_proc_status(pid: int) -> dict:
    vals = {}
    try:
        with open(f"/proc/{pid}/status", encoding="ascii", errors="replace") as fh:
            for line in fh:
                if line.startswith(("VmRSS:", "RssAnon:", "VmSwap:")):
                    key, rest = line.split(":", 1)
                    vals[key] = int(rest.split()[0]) // 1024  # kB -> MB
    except Exception:
        pass
    return vals


def snapshot() -> tuple[frozenset, int, int, int]:
    """(pid set, max VmRSS MB, max RssAnon MB, max VmSwap MB) across runners."""
    pids = llama_pids()
    rss = anon = swap = -1
    for pid in pids:
        s = read_proc_status(pid)
        rss = max(rss, s.get("VmRSS", -1))
        anon = max(anon, s.get("RssAnon", -1))
        swap = max(swap, s.get("VmSwap", -1))
    return frozenset(pids), max(rss, 0), max(anon, 0), max(swap, 0)


# ---- HTTP ----------------------------------------------------------------


def chat_call(base_url: str, model: str, jpeg: bytes, timeout: int = HTTP_TIMEOUT_S) -> dict:
    """One /api/chat POST with the production PROMPT + options. Raw urllib."""
    b64 = base64.b64encode(jpeg).decode("ascii")
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": f"System: {PROMPT}"},
            {"role": "user", "content": "Analyze this image.", "images": [b64]},
        ],
        "format": "json",
        "stream": False,
        "options": {
            "temperature": 0.0,
            "num_predict": 256,
            "num_ctx": NUM_CTX,
        },
    }
    import urllib.error
    import urllib.request
    req = urllib.request.Request(
        f"{base_url}/api/chat",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"ollama HTTP {e.code}: {e.read()[:200]!r}") from e
    except Exception as e:
        raise RuntimeError(f"ollama call failed: {e}") from e


def parse_content(envelope: dict) -> dict:
    message = envelope.get("message")
    if message is None:
        raise RuntimeError(f"no message in envelope: {str(envelope)[:200]}")
    text = message.get("content", "")
    if isinstance(text, list):
        text = "".join(text)
    text = text.strip().removeprefix("```json").removesuffix("```").strip()
    return json.loads(text)


# ---- frame grab (same one-shot as capture.py) ----------------------------


def grab_frame(device: str, width: int, height: int) -> bytes:
    cmd = [
        "ffmpeg", "-loglevel", "error",
        "-f", "v4l2",
        "-video_size", f"{width}x{height}",
        "-i", device,
        "-frames:v", "1",
        "-pix_fmt", "yuvj420p",
        "-f", "image2pipe", "-vcodec", "mjpeg", "-",
    ]
    proc = subprocess.run(cmd, capture_output=True, timeout=30)
    if proc.returncode != 0 or not proc.stdout:
        raise RuntimeError(
            f"ffmpeg grab failed: {proc.stderr.decode(errors='replace')[:200]}"
        )
    return proc.stdout


# ---- verdict math (shared by online run and --self-test) ------------------


def least_squares_slope(points) -> float:
    """Slope of y vs x for [(x, y), ...]; 0.0 when degenerate."""
    n = len(points)
    if n < 2:
        return 0.0
    sx = sum(p[0] for p in points)
    sy = sum(p[1] for p in points)
    sxx = sum(p[0] * p[0] for p in points)
    sxy = sum(p[0] * p[1] for p in points)
    denom = n * sxx - sx * sx
    if denom == 0:
        return 0.0
    return (n * sxy - sx * sy) / denom


def arm_slopes(samples) -> dict:
    """samples: list of dicts with t_min (minutes since arm start), ordinal,
    rss_after_mb. Returns MB/min and MB/request least-squares slopes."""
    mb_min = least_squares_slope([(s["t_min"], s["rss_after_mb"]) for s in samples])
    mb_req = least_squares_slope([(float(s["ordinal"]), s["rss_after_mb"]) for s in samples])
    return {"mb_min": mb_min, "mb_req": mb_req}


def l1_verdict(full_slopes) -> dict:
    worst = max(s["mb_min"] for s in full_slopes)
    return {
        "confirmed": worst >= L1_FLOOR_MB_MIN,
        "worst_full_mb_min": round(worst, 1),
        "floor_mb_min": L1_FLOOR_MB_MIN,
    }


def g2_verdict(full_slopes, resized_slope) -> str:
    worst_full = max(s["mb_min"] for s in full_slopes)
    if worst_full < L1_FLOOR_MB_MIN:
        return "UNCLEAR (no leak signal to compare)"
    if resized_slope["mb_min"] <= 0.5 * worst_full:
        return "IMPROVED (unexpected bonus finding - leak is partly per-byte)"
    if resized_slope["mb_min"] <= 2.0 * worst_full:
        return "PARITY (supports per-request leak; recycle-only fix is correct)"
    return "WORSE than full arms (investigate)"


def fold(raw_json: dict):
    """Production post-processing: what the consumer actually sees."""
    return _build_result(raw_json, model="harness", latency_ms=0, allowed=None)


def g4_pairs(full_entries, resized_entries) -> dict:
    """full_entries/resized_entries: list of (frame_id, folded VisionResult).
    Per frame: every full sample vs every resized sample."""
    per_frame_full: dict = {}
    per_frame_resized: dict = {}
    for fid, r in full_entries:
        per_frame_full.setdefault(fid, []).append(r)
    for fid, r in resized_entries:
        per_frame_resized.setdefault(fid, []).append(r)
    total = passed = 0
    failures = []
    straddles = []
    for fid in sorted(per_frame_full):
        for rf in per_frame_full[fid]:
            for rr in per_frame_resized.get(fid, []):
                total += 1
                ok = rf.chickens == rr.chickens and abs(rf.confidence - rr.confidence) <= G4_CONF_TOL
                passed += 1 if ok else 0
                if not ok:
                    failures.append(
                        f"frame {fid}: full(chickens={rf.chickens}, conf={rf.confidence:.3f}, "
                        f"other={rf.other_animals}) vs resized(chickens={rr.chickens}, "
                        f"conf={rr.confidence:.3f}, other={rr.other_animals})"
                    )
                if (rf.confidence >= CONSUMER_CONF_GATE) != (rr.confidence >= CONSUMER_CONF_GATE):
                    straddles.append(
                        f"frame {fid}: conf {rf.confidence:.3f} vs {rr.confidence:.3f} "
                        f"straddles the {CONSUMER_CONF_GATE} consumer gate (door flip risk)"
                    )
    return {
        "total_pairs": total,
        "passed": passed,
        "pass": total > 0 and passed == total,
        "failures": failures,
        "straddles": straddles,
    }


def token_confounds(samples) -> list:
    """Flag prompt+text token totals over num_ctx where the count is plausible
    (ollama#6392: this model reports absent/1/null prompt_eval_count)."""
    flags = []
    for s in samples:
        pec = s.get("prompt_eval_count")
        if pec is None or not isinstance(pec, int) or pec == 1:
            continue  # broken/absent count - tolerated, not a signal
        ec = s.get("eval_count") or 0
        if pec + ec > NUM_CTX:
            flags.append(
                f"call {s['ordinal']}: prompt_eval_count={pec} + eval_count={ec} "
                f"> num_ctx={NUM_CTX} - CONTEXT OVERFLOW CONFOUND"
            )
    return flags


def verdict_matrix(l1: dict, g4: dict) -> str:
    if not l1["confirmed"]:
        return ("HALT - L1 NOT REPRODUCED on this box. Report to user; "
                "recycle ships as harmless insurance only if the user says so.")
    if g4["pass"]:
        return "DEPLOY BOTH CHANGES (unlock todo 7). Document L1 slope + G2 result."
    return "HALT - G4 FAIL (downscale hurts quality). Recycle-only deploy becomes a user decision."


# ---- offline self-test -----------------------------------------------------


def self_test() -> int:
    import io

    ok = True

    def expect(name, cond, detail=""):
        nonlocal ok
        print(f"{'PASS' if cond else 'FAIL'}  {name}  {detail}")
        ok = ok and cond

    # resize import + behavior on synthesized JPEGs
    def make_jpeg(w, h):
        im = Image.new("RGB", (w, h), (90, 60, 30))
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=90)
        return buf.getvalue()

    big = make_jpeg(1280, 720)
    out = _resize_for_inference(big, 640)
    with Image.open(BytesIO(out)) as im:
        expect("resize 1280x720 -> 640x360", im.size == (640, 360), str(im.size))
    small = make_jpeg(400, 300)
    expect("resize passthrough 400x300", _resize_for_inference(small, 640) is small)

    # verdict math on fake metrics
    def fake_samples(slope_mb_per_call, n=CALLS_PER_ARM):
        return [
            {
                "t_min": i * 0.1,
                "ordinal": i,
                "rss_after_mb": 2500.0 + slope_mb_per_call * i,
                "prompt_eval_count": None if i % 4 == 0 else (1 if i % 5 == 0 else 850),
                "eval_count": 40,
            }
            for i in range(n)
        ]

    fast = fake_samples(8.0)   # 8 MB/call ~ 80 MB/min at 10 calls/min
    slow = fake_samples(0.02)  # ~0.2 MB/min
    s_fast = arm_slopes(fast)
    s_slow = arm_slopes(slow)
    expect("slope math ~MB/min", 70 <= s_fast["mb_min"] <= 90, f"{s_fast['mb_min']:.1f}")
    expect("slope math ~MB/request", 7.5 <= s_fast["mb_req"] <= 8.5, f"{s_fast['mb_req']:.2f}")

    l1_yes = l1_verdict([s_fast, s_fast])
    expect("L1 confirmed case", l1_yes["confirmed"] is True, str(l1_yes))
    l1_no = l1_verdict([s_slow, s_slow])
    expect("L1 insufficient-signal case", l1_no["confirmed"] is False, str(l1_no))

    # null prompt_eval_count tolerated by confound logic
    expect("null prompt_eval_count tolerated", token_confounds(fast) == [])

    # token overflow flagged when count is plausible
    overflow = dict(fake_samples(1.0)[0])
    overflow.update({"prompt_eval_count": 1900, "eval_count": 300, "ordinal": 1})
    expect("overflow confound flagged", len(token_confounds([overflow])) == 1)

    # G4 on folded values: build raw model JSONs, fold via production code
    def raw(chickens, conf, other):
        return {"chickens": chickens, "confidence": conf, "other_animals": other,
                "movement": "still", "notes": ""}

    # parity case
    full_e = [(0, fold(raw(3, 0.87, [])))] * 5 + [(1, fold(raw(0, 0.9, [])))] * 5
    resz_e = [(0, fold(raw(3, 0.85, [])))] * 5 + [(1, fold(raw(0, 0.9, [])))] * 5
    g4 = g4_pairs(full_e, resz_e)
    expect("G4 pass case", g4["pass"] and g4["total_pairs"] == 50 and not g4["straddles"],
           f"pairs={g4['total_pairs']} passed={g4['passed']}")

    # straddle case: passes tolerance but flips the door decision
    full_e2 = [(0, fold(raw(3, 0.82, [])))]
    resz_e2 = [(0, fold(raw(3, 0.78, [])))]
    g4s = g4_pairs(full_e2, resz_e2)
    expect("G4 straddle surfaced", g4s["pass"] and len(g4s["straddles"]) == 1,
           str(g4s["straddles"]))

    # poultry fold: raw split ("2 chickens + 1 rooster") folds to 3 @ conf 0.5
    folded = fold(raw(2, 0.9, ["rooster"]))
    expect("poultry fold via production code",
           folded.chickens == 3 and folded.confidence == 0.5,
           f"chickens={folded.chickens} conf={folded.confidence}")

    # fail case: count mismatch
    g4f = g4_pairs([(0, fold(raw(3, 0.9, [])))], [(0, fold(raw(2, 0.9, [])))])
    expect("G4 fail case", not g4f["pass"] and g4f["total_pairs"] == 1)

    print(f"\nself-test {'GREEN' if ok else 'RED'}")
    return 0 if ok else 1


# ---- online run ------------------------------------------------------------


def abort(reason: str) -> None:
    print(f"\n*** HARNESS ABORT: {reason} ***", flush=True)
    print("*** No partial verdict. Restart jetson-pichick FIRST, "
          "then decide on a re-run. ***", flush=True)
    sys.exit(3)


def online(base_url: str, model: str, env: dict, max_frames: int = 3) -> int:
    t_start = time.monotonic()

    log(f"base_url={base_url} model={model}")

    # ---- warmup: pays the lazy model load, anchors PID + baseline ----
    device = env.get("JCHICK_CAPTURE_DEVICE", "/dev/video0")
    width = env.get("JCHICK_CAPTURE_WIDTH", "1280")
    height = env.get("JCHICK_CAPTURE_HEIGHT", "720")

    log(f"grabbing warmup frame ({device} {width}x{height})...")
    try:
        warmup_frame = grab_frame(device, width, height)
    except RuntimeError as e:
        abort(f"warmup frame grab failed: {e}")

    log("warmup /api/chat call (NOT measured; pays model load)...")
    try:
        envelope = chat_call(base_url, model, warmup_frame)
        parse_content(envelope)
    except Exception as e:
        abort(f"warmup call failed: {e}")
    pids, rss, anon, swap = snapshot()
    if not pids:
        abort("no llama-server runner after warmup - cannot anchor")
    anchor = pids
    log(f"post-warmup anchor pids={sorted(anchor)} VmRSS={rss}MB "
        f"RssAnon={anon}MB VmSwap={swap}MB")

    # ---- frames: up to max_frames distinct grabs ~30 s apart ----
    frames = [warmup_frame]
    frame_times = [time.strftime("%H:%M:%S")]
    while len(frames) < max_frames:
        time.sleep(30)
        try:
            frames.append(grab_frame(device, width, height))
            frame_times.append(time.strftime("%H:%M:%S"))
            log(f"grabbed frame {len(frames) - 1} at {frame_times[-1]}")
        except RuntimeError as e:
            log(f"frame grab {len(frames)} failed ({e}) - continuing with {len(frames)}")
            break

    log(f"distinct frames: {len(frames)} grabbed at {frame_times} "
        "(empty frames give trivial 0=0 parity - noted)")

    # ---- arms: full(15) -> resized(15) -> full(15), frames cycled ----
    table = []
    all_entries = {"full1": [], "resized": [], "full2": []}
    errors_consecutive = 0

    def run_arm(name: str, resize: bool) -> None:
        nonlocal errors_consecutive
        arm_t0 = time.monotonic()
        i = 0
        retries = 0
        while i < CALLS_PER_ARM:
            frame = frames[i % len(frames)]
            fid = i % len(frames)
            jpeg = _resize_for_inference(frame, 640) if resize else frame
            p_before, rss_b, anon_b, swap_b = snapshot()
            if p_before != anchor:
                abort(f"llama-server PID set changed mid-run: "
                      f"anchor={sorted(anchor)} now={sorted(p_before)}")
            call_t0 = time.monotonic()
            try:
                envelope = chat_call(base_url, model, jpeg)
                raw = parse_content(envelope)
                errors_consecutive = 0
                retries = 0
            except Exception as e:
                errors_consecutive += 1
                retries += 1
                log(f"call failed ({e}) - retry {retries}")
                if errors_consecutive >= 2:
                    abort(f"repeated HTTP errors in {name} arm")
                continue  # do NOT advance i: retry this ordinal
            wall = time.monotonic() - call_t0
            p_after, rss_a, anon_a, swap_a = snapshot()
            if p_after != anchor:
                abort(f"llama-server PID set changed after call: "
                      f"anchor={sorted(anchor)} now={sorted(p_after)}")
            folded = fold(raw)
            sample = {
                "arm": name, "ordinal": i, "frame": fid,
                "t_min": (time.monotonic() - arm_t0) / 60.0,
                "wall_s": round(wall, 2),
                "rss_before_mb": rss_b, "rss_after_mb": rss_a,
                "anon_mb": anon_a, "swap_mb": swap_a,
                "prompt_eval_count": envelope.get("prompt_eval_count"),
                "prompt_eval_duration": envelope.get("prompt_eval_duration"),
                "eval_count": envelope.get("eval_count"),
                "chickens": folded.chickens, "confidence": folded.confidence,
                "other_animals": list(folded.other_animals),
                "raw": raw,
            }
            table.append(sample)
            all_entries[name].append((fid, folded))
            print(f"  {name}[{i+1:2d}] f{fid} wall={wall:5.1f}s "
                  f"rss={rss_b}->{rss_a}MB anon={anon_a} swap={swap_a} "
                  f"pec={sample['prompt_eval_count']} "
                  f"chickens={folded.chickens} conf={folded.confidence:.3f}",
                  flush=True)
            i += 1

    log("arm 1/3: full resolution (15 calls)...")
    run_arm("full1", resize=False)
    log("arm 2/3: resized 640 (15 calls)...")
    run_arm("resized", resize=True)
    log("arm 3/3: full resolution (15 calls)...")
    run_arm("full2", resize=False)

    # ---- verdicts ----
    def samples_of(name):
        return [s for s in table if s["arm"] == name]

    s_full1 = arm_slopes(samples_of("full1"))
    s_resz = arm_slopes(samples_of("resized"))
    s_full2 = arm_slopes(samples_of("full2"))
    l1 = l1_verdict([s_full1, s_full2])
    g2 = g2_verdict([s_full1, s_full2], s_resz)
    full_entries = all_entries["full1"] + all_entries["full2"]
    g4 = g4_pairs(full_entries, all_entries["resized"])
    confounds = token_confounds(table)

    print("\n===== VERDICT =====")
    print(f"slopes: full1={s_full1}  resized={s_resz}  full2={s_full2}")
    print(f"L1 (leak): {'CONFIRMED' if l1['confirmed'] else 'NOT REPRODUCED'} "
          f"- worst full arm {l1['worst_full_mb_min']} MB/min "
          f"(floor {l1['floor_mb_min']}); per-request values above")
    print(f"G2 (byte-effect): {g2}")
    print(f"G4 (quality parity): {'PASS' if g4['pass'] else 'FAIL'} "
          f"({g4['passed']}/{g4['total_pairs']} pairs)")
    for s in g4["straddles"]:
        print(f"  STRADDLE: {s}")
    for f in g4["failures"][:10]:
        print(f"  FAIL: {f}")
    if confounds:
        print("TOKEN-PRESSURE CONFOUNDS (Metis F8 - stated, not hidden):")
        for c in confounds:
            print(f"  {c}")
    else:
        print("token pressure: no overflow flags (plausible counts within num_ctx)")
    print(f"frames: {len(frames)} at {frame_times}; empty frames give trivial 0=0 parity")
    print(f"MATRIX: {verdict_matrix(l1, g4)}")
    print(f"\nper-call table follows ({len(table)} measured calls):")
    for s in table:
        print(json.dumps({k: v for k, v in s.items() if k != "raw"}, default=str))
    log(f"done in {(time.monotonic() - t_start) / 60:.1f} min")
    return 0

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--self-test", action="store_true",
                    help="offline: resize import + verdict math on fake metrics")
    ap.add_argument("--base-url", default=None,
                    help="Ollama base URL (default: OLLAMA_URL from env file)")
    ap.add_argument("--model", default=None,
                    help="model tag (default: JCHICK_DETAIL_MODEL from env file)")
    ap.add_argument("--max-frames", type=int, default=3,
                    help="distinct frame grabs ~30 s apart (plan default 3; "
                         "extend toward 6 when frames are mostly empty)")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    env = parse_env_file(ENV_FILE)
    base_url = args.base_url or env.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
    if args.model:
        model = args.model
        model_src = "--model argument"
    else:
        model = env.get("JCHICK_DETAIL_MODEL")
        model_src = f"{ENV_FILE}: JCHICK_DETAIL_MODEL"
    if not model:
        abort("no model: JCHICK_DETAIL_MODEL absent from /etc/jchick/jchick.env "
              "and no --model given")
    try:
        v = subprocess.run(["ollama", "-v"], capture_output=True, timeout=15)
        print(f"[harness] ollama -v: {v.stdout.decode().strip() or v.stderr.decode().strip()}")
    except Exception as e:
        print(f"[harness] ollama -v unavailable: {e}")
    print(f"[harness] active model: {model} (from {model_src})")
    return online(base_url, model, env, max_frames=args.max_frames)



if __name__ == "__main__":
    sys.exit(main())