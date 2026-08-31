# T11 — Raise CHICKENS_CONFIRM_STREAK 1 → 2 in coop_door_controller

## Goal
Require two consecutive qualifying inference frames before `all_home`
latches, so a single phantom or lucky 3-count frame cannot close the door
on an empty coop.

## Context
Live review of 3 days of `inference.fired` history (2026-08-31 session,
jetson-pichick repo) measured the producer's count signal at 2↔3 flicker
with 209 flips across 575 frames, plus 13 phantom-predator frames at
conf ≥ 0.80 that passed the consumer's confidence filter. The producer
side was fixed in jetson-pichick v0.2.0 (commits f674442 + c3d5703:
visibility prompt, poultry folding, conf clamp on split frames), but the
consumer still closes the door on a SINGLE qualifying frame:
`CHICKENS_CONFIRM_STREAK 1` in `coop_door_controller/src/config.h:18`
and the streak latch in `chickens.c` `chickens_on_count()`. The producer
now emits a correct frame roughly every ~20 s while chickens are visible
(dusk close window has ~180 frames/day), so streak=2 costs ≤ ~40 s of
extra confirmation delay — negligible against a 2.5 h roosting window
(`HARD_CLOSE_OFFSET_MIN 150`).

This task lives in the CONSUMER repo (`../coop_door_controller`), not
jetson-pichick. The two-repo wire contract (AGENTS.md) is unchanged:
this is a policy change in how the consumer consumes the same payload.

## Files touched
- `../coop_door_controller/src/config.h` — one constant

## Pre-conditions
- [ ] None (independent of jetson-pichick v0.2.0 deploy, but the two
      fixes are complementary; producer v0.2.0 already live on Jetson).

## Exact changes required

### Change 1: streak constant
File: `../coop_door_controller/src/config.h`
Action: REPLACE the define value (line ~18)

Before:
    #define CHICKENS_CONFIRM_STREAK  1

After:
    #define CHICKENS_CONFIRM_STREAK  2

The comment above it (config.h:15, "1 = single inference frame with
chickens >= expected closes the door") must be updated to match: two
consecutive frames required. The streak logic itself in chickens.c
(increment on qualifying frame, reset to 0 on any below-expected frame)
already implements N-consecutive semantics — only the constant changes.

## Validation plan
    # 1. Build (verify the consumer repo's actual build command from its
    #    own README/AGENTS docs first — not verified from this session;
    #    it is an ESP32/PlatformIO-style project):
    #    Expected: SUCCESS, no warnings about the changed constant
    #
    # 2. Unit-level check if the repo has tests for chickens.c streak
    #    logic — run them.
    #
    # 3. Flash + live check (next dusk window): with 3 chickens visible,
    #    door_controller logs should show:
    #      count: in=3 exp=3 streak=1  (first qualifying frame)
    #      count: in=3 exp=3 streak=2 all_home=1  (second frame, ~20s later)
    #    i.e. two frames before any close decision.
    #    Expected: "all chickens home @ ..." log line only after streak=2.

## Success criteria
- [ ] Build passes
- [ ] all_home latches only on the 2nd consecutive 3-count frame
- [ ] Single-frame phantoms (should be rare post-v0.2.0) cannot latch

## Rollback
    git checkout -- src/config.h   # (in the consumer repo)

## Next task
T12 — dusk verification of producer v0.2.0 + this change together.