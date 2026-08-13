# T05 — Deploy to Jetson and verify stream in browser

## Goal
Push the updated repo to the running Jetson at 192.168.0.18, re-run the
install script, restart the service, and verify the MJPEG stream is
reachable at `http://192.168.0.18:8090/` with overlay text visible.

## Context
The Jetson currently runs the pre-MJPEG code. After T01-T04 the local
repo has the stream + overlay in `src/jchick/`. We deploy the standard
way (rsync + install.sh, per SETUP.md:401-406), then set
`JCHICK_HTTP_PORT=8090` in `/etc/jchick/jchick.env` on the device (since
install.sh doesn't clobber an existing env file by default), restart,
and verify.

## Files touched
- None in the repo. Changes are on the device only:
  - `/opt/jchick/` (rsync'd from repo)
  - `/etc/jchick/jchick.env` (add JCHICK_HTTP_PORT=8090)

## Pre-conditions
- [ ] T01 must be complete
- [ ] T02 must be complete
- [ ] T03 must be complete
- [ ] T04 must be complete
- [ ] Jetson reachable at 192.168.0.18 via ssh pichick@192.168.0.18
- [ ] NATS broker at 192.168.0.151:4222 reachable from the Jetson
- [ ] Camera at /dev/video0 (or whatever jchick.env says)

## Exact changes required

### Change 1: rsync repo to Jetson /tmp/jetson-pichick/

    rsync -av --delete \
      --exclude .venv --exclude __pycache__ --exclude .git --exclude .omo \
      --exclude tasks \
      ~/code/jetson-pichick/ pichick@192.168.0.18:/tmp/jetson-pichick/

### Change 2: Run install.sh on the Jetson (idempotent — rebuilds venv)

    ssh pichick@192.168.0.18 'sudo bash /tmp/jetson-pichick/scripts/install.sh'

Note: install.sh does NOT overwrite /etc/jchick/jchick.env if it already
exists (unless INSTALL_UPDATE_ENV=1). We want to preserve existing
per-device overrides, so we DON'T set INSTALL_UPDATE_ENV. Instead we
add the one new var by hand in Change 3.

### Change 3: Add JCHICK_HTTP_PORT to the device env file

    ssh pichick@192.168.0.18 'sudo grep -q "^JCHICK_HTTP_PORT=" /etc/jchick/jchick.env \
      || echo "JCHICK_HTTP_PORT=8090" | sudo tee -a /etc/jchick/jchick.env'
    ssh pichick@192.168.0.18 'sudo grep "^JCHICK_HTTP_PORT=" /etc/jchick/jchick.env'
    # Expected: JCHICK_HTTP_PORT=8090

### Change 4: Restart the service

    ssh pichick@192.168.0.18 'sudo systemctl restart jetson-pichick'
    ssh pichick@192.168.0.18 'sudo systemctl is-active jetson-pichick'
    # Expected: active

## Validation plan

### Step 1: service logs show MJPEG started

    ssh pichick@192.168.0.18 'sudo journalctl -u jetson-pichick --since "30 seconds ago" --no-pager | grep -E "MJPEG|starting|nats:"'
    # Expected:
    #   INFO jchick.app: jchick: starting host=pichick ... capture=v4l2@1.00fps
    #   INFO jchick.app: MJPEG streaming enabled on port 8090
    #   INFO jchick.mjpeg_server: MJPEG server listening on port 8090
    #   INFO jchick.nats_pub: nats: connected to nats://192.168.0.151:4222

### Step 2: port is listening

    ssh pichick@192.168.0.18 'ss -ltn | grep 8090'
    # Expected: LISTEN ... :8090 ...

### Step 3: HTTP viewer page responds

    curl -sS -o /dev/null -w "%{http_code}\n" http://192.168.0.18:8090/
    # Expected: 200

    curl -sS http://192.168.0.18:8090/ | grep -o "<title>[^<]*</title>"
    # Expected: <title>Jetson Picchk Camera</title>

### Step 4: stream endpoint serves multipart

    curl -sS -m 5 -D - http://192.168.0.18:8090/stream -o /tmp/stream-sample.bin
    # Expected: first line "HTTP/1.1 200 OK", a Content-Type header containing
    # "multipart/x-mixed-replace", and the output file is non-empty.

    file /tmp/stream-sample.bin
    # Expected: something like "JPEG image data, ..." (first chunk of multipart)

### Step 5: human check — open in browser

    echo "Open http://192.168.0.18:8090/ in a browser. You should see:"
    echo "  - live camera feed"
    echo "  - top HUD bar with chickens=N, conf=N%, move=..., diff=..., model=..."
    echo "  - bottom bar with notes (if the model returned any)"
    echo "  - bar color: green when chickens>0, yellow when gated, red when stale"

## Success criteria
- [ ] service is active
- [ ] journal shows "MJPEG streaming enabled on port 8090"
- [ ] port 8090 is listening
- [ ] curl GET / returns 200 with the viewer HTML
- [ ] curl GET /stream returns 200 with multipart content-type and JPEG bytes
- [ ] (manual) browser shows live feed with HUD overlay

## Rollback
    # Disable the stream on the device
    ssh pichick@192.168.0.18 'sudo sed -i "s|^JCHICK_HTTP_PORT=.*|JCHICK_HTTP_PORT=0|" /etc/jchick/jchick.env'
    ssh pichick@192.168.0.18 'sudo systemctl restart jetson-pichick'

    # Revert repo to pre-T01 state if needed
    git checkout main~5   # or however many commits back

## Next task
None. Project complete.