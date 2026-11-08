# Deploy identity — SSS worker overlay + Studio SSS drafts

Parent is verifying which container is which. This bundle does not replace
the custom Harvey web app.

## Two containers (do not conflate)

| Identity | Container | How it is reached | This package |
|---|---|---|---|
| Custom Harvey web app | `harvey` | Access hostname (email code). Exact Cloudflare route unverified; no DNS/tunnel changes. | Surgical inject only: `sss_glm_studio.py` / `sss_glm_studio.js` and two anchors in `dashboard.py` + `studio.html`. Never overwrite the rest of the UI. Never recreate this container. |
| SSS outreach worker | `harvey-sss` | localhost `:5556` → container `:5555` | Overlay `/opt/harvey-sss/data` + optional exact-config recreate for GLM channel mounts. Image snapshot via `docker commit`. Original kept stopped as `harvey-sss.pre-glm`. |
| GLM runtime | `hermes-agent` | internal gateway | Approved worker scripts. Exact-config recreate preserves anonymous `/opt/data`, `lobodesk-proxy`, entrypoint, labels, health. Original kept as `hermes-agent.pre-glm`. Brief gateway bounce only if recreate is applied. |

Saved Oct 19–20 dashboard/campaign/editor/mode-switch/assistant requests are
**not replaced**. This inject adds SSS GLM review on Studio `#sssglm`.

Upstream Sellbird `sellbird dashboard` / terminal Outbox is **not** the
verified custom UI. SSS `:5556` is the SSS container dashboard, not Studio.

## Owner review (implemented in source; runtime unverified until parent deploys)

- URL path: existing Access hostname `/#sssglm` (container `harvey`).
- Full subject/body, recipient, outbox id, binding hash.
- Owner may **edit** subject/body. That clears approval, writes a **new**
  immutable hash, sets `pending_review`, and needs a new explicit approve.
  Editing does not make the row permanently unsendable. A stale hash
  (concurrent edit or replay) is refused.
- Approve and Reject the exact current copy. Confirm phrases required.
  No send button. No blanket auto-approval.
- SSS send stats on the same page: today / MTD / total with
  `status=sent` in `America/Tijuana`. Held and pending counts. Source
  timestamp. Query failures show **unavailable**, never a fake 0.
  Does **not** replace the independent Hermes 8PM report (money/scans/Harvey).
- harvey does **not** mount harvey-sss SQLite. Preview/approve/edit/stats go over
  internal docker network `sss-glm-review` (`--internal`, port 18765 not
  published) with a review token stored in each existing data dir.
  Existing Access authentication on the dashboard is unchanged.

## Exact recreate (fixes the blocked dry-run)

Approximate `docker run` dropped localhost `:5556`, Hermes anonymous
`/opt/data`, `lobodesk-proxy`, writable-layer files, entrypoint, security
opts, capabilities, labels, health, and other HostConfig. CLI
`--entrypoint` only takes one executable, so Hermes
`['/init','/opt/hermes/docker/main-wrapper.sh']` cannot be flattened.
The replacement:

1. `docker commit` writable layer to `sss-glm-snap/<name>:<stamp>`
2. Docker Engine API `POST /v1.41/containers/create` with the **full**
   Entrypoint array and Cmd (never a single-string `--entrypoint`)
3. Keep original stopped as `<name>.pre-glm` (not removed)
4. Pre/post fingerprint compare; on mismatch, swap original back and fail
5. Unsupported non-default HostConfig keys fail closed before any rename
6. Secret-bearing inspect/env is written only under `/root/sss-glm-snapshots`
   (0600). Public dry-run prints redacted env keys and the Entrypoint array.
7. harvey and harvey-sss PID1 is `sleep infinity`. Recreate/reload captures
   the :5555 listener (argv/cwd/uid, no environ) and relaunches that argv.
   **Never `docker restart`**. Hermes PID1 is `/init` + wrapper; children
   are not docker-exec'd. Review :18765 starts only if empty.

`--recreate` still requires `SSS_GLM_CONFIRM_RECREATE=harvey-sss-only`.
Do not restart cloudflared, DMTC, or the independent Hermes report job
except the hermes-agent container itself when adding channel mounts.

## Allowed write set

Worker (`apply-vps.sh`):

```
/opt/harvey-sss/data/sss_glm/
/opt/harvey-sss/data/sss_engine.py
/opt/harvey-sss/data/sss_spaced_send.py
/opt/harvey-sss/data/legacy_*            (preservation copies)
/opt/harvey-sss/data/sss_glm_preview.py
/opt/harvey-sss/data/sss_glm_approve.py
hermes-agent:/opt/hermes/.hermes/scripts/approved/sss_glm/
/opt/sss-glm-channel/{requests,responses,ledger}
optional systemd sss-glm-worker + sss-glm-review
```

Dashboard (`apply-dashboard.sh`):

```
harvey:/app/harvey/sss_glm_studio.py          (new)
harvey:/app/harvey/web/sss_glm_studio.js      (new)
harvey:/app/harvey/dashboard.py               (anchor insert + .pre-sss-glm backup)
harvey:/app/harvey/web/studio.html            (nav + script + backup)
/opt/harvey/data/sss-glm-review.token
/opt/harvey-sss/data/sss-glm-review.token
docker network sss-glm-review (internal)
```

Forbidden: wholesale replace of studio/home/dashboard; shared SQLite;
publishing 18765; copying OpenRouter/SMTP keys; outbox SMTP tests;
container recreate of `harvey` or `harvey-cloudflared`; `docker restart`
of harvey or harvey-sss (kills the separately launched dashboard).
