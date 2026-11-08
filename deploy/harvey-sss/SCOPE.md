# Deploy identity — SSS worker overlay only

Parent is verifying which container is which. This bundle must not be applied
as if it were the custom Harvey web app.

## Two containers (do not conflate)

| Identity | Container | How it is reached | This package |
|---|---|---|---|
| Custom Harvey web app | `harvey` | `harvey.dmtoclose.com` behind Cloudflare Access (email code) | **Never touch.** No docker cp, no recreate, no bind, no file overlay. |
| SSS outreach worker | `harvey-sss` | localhost `:5556` (not the public hostname) | Overlay **only** `/opt/harvey-sss/data` worker scripts + GLM package. Optional extra channel mounts. Image stays `harvey-sss:v3`, uid 1000, `sleep infinity`. |
| GLM runtime | `hermes-agent` | internal | Approved worker scripts + channel mounts. No app UI. |

Saved Oct 19–20 requested features (professional dashboard, Campaigns/drips,
contacts import/tags/segments, visual editor, Harvey vs full-platform mode,
GLM assistant) are **saved requests**, not proof each one shipped, and they
are **not** in this fork overlay. Untracked `studio*` / `home*` files seen on
a harvey-sss `/app` git status are leftover in that worker tree. They are not
this package and must not be replaced from here.

Upstream Sellbird `sellbird dashboard` / terminal Outbox (`127.0.0.1:5555`) is
**not** the verified custom UI.

## Can the owner review SSS GLM drafts in the custom web app?

**No — not with this package.** Missing wiring, stated honestly:

1. GLM enqueue writes `pending_review` rows into **harvey-sss**
   `/app/data/harvey.db` (`outbox.kind = harvey_mode`).
2. Documented harvey-sss mounts are `harvey.local.yaml`, claude-home, and
   `/opt/harvey-sss/data` → `/app/data`. There is **no** shared volume with
   container `harvey`.
3. This overlay adds no API, proxy, or dashboard change that would list those
   SSS rows on `harvey.dmtoclose.com`.
4. Localhost `:5556` is the SSS container. If a UI is bound there, parent must
   confirm it already reads **that** SQLite outbox. This bundle does not ship
   or verify that UI, and `--recreate` would drop harvey-sss writable-layer
   `/app` files that are not on a volume.

Review path this package **does** provide:

- Read-only copy: `python /app/data/sss_glm_preview.py --show-copy` inside
  harvey-sss (or `host/verify-install.sh --show-copy` on the VPS).
- Approve is still a status flip on those SSS outbox rows. Status-only
  approve keeps the GLM binding; recipient/subject/body edits hold.

No new dashboard. No real sends.

## Allowed write set

```
/opt/harvey-sss/data/sss_glm/
/opt/harvey-sss/data/sss_engine.py
/opt/harvey-sss/data/sss_spaced_send.py
/opt/harvey-sss/data/legacy_permission.py   (preservation copy, unchanged)
/opt/harvey-sss/data/legacy_release.py
/opt/harvey-sss/data/legacy_validate.py
/opt/harvey-sss/data/sss_glm_preview.py
/opt/harvey-sss/data/sss_glm_approve.py
hermes-agent:/opt/hermes/.hermes/scripts/approved/sss_glm/
/opt/sss-glm-channel/{requests,responses,ledger}   (new, outside app data)
optional systemd sss-glm-worker.{service,timer}
```

Forbidden: container `harvey`; `/app/web`; `studio*`; `dashboard.py`;
`home*.js`; any path under a Harvey app tree; outbox row updates; SMTP;
copying OpenRouter/Hermes keys into SSS.

`--recreate` requires `SSS_GLM_CONFIRM_RECREATE=harvey-sss-only` after the
parent confirms the custom dashboard container stays up and any SSS `:5556`
UI lives in the image or a volume.
