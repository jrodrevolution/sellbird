# Approval-only GLM drafts for Site Safety Scan (Harvey overlay)

This is a **source and patch bundle**, not a live install. No VPS credentials
were used. Do not claim the worker is running until the parent applies it.

License: GNU AGPL v3.0 only (see `/LICENSE` and `/NOTICE` in this repository).

Read `SCOPE.md` first.

## What this does

Future SSS outreach drafts (dental / law / accounting) get a **full subject and
body** from Hermes's existing OpenRouter model `z-ai/glm-5.3-flash`. SSS never
holds AI keys. Every new GLM draft is `pending_review` or `held`. Nothing is
auto-approved or sent.

## Owner review path

Implemented in this overlay (runtime unverified until parent deploys and
Access login succeeds):

1. Existing Access hostname (email code), container `harvey`.
2. Studio nav **SSS drafts** → `/#sssglm`.
3. Full copy, ids, hashes. Approve / Reject only. No send button.
4. harvey proxies to harvey-sss over internal network `sss-glm-review`.
   Databases stay separate.

SSS localhost `:5556` and upstream Sellbird Outbox are **not** this UI.

CLI still works inside harvey-sss:
`python /app/data/sss_glm_preview.py --show-copy`

## Safe parent install (nothing is live until you run it)

On the VPS, from the staged tree, as root. Default is dry-run. No prospect
sends.

```
# 1. Worker files only (no container recreate)
bash host/apply-vps.sh
bash host/apply-vps.sh --apply

# 2. Studio SSS drafts (no recreate of harvey)
bash host/apply-dashboard.sh
bash host/apply-dashboard.sh --apply --reload-dashboard

# 3. Exact-config recreate of harvey-sss and hermes-agent ONLY, after
#    confirming dry-run JSON still lists 127.0.0.1:5556, /opt/data,
#    lobodesk-proxy, entrypoint, labels, health.
SSS_GLM_CONFIRM_RECREATE=harvey-sss-only bash host/apply-vps.sh --apply --recreate

# 4. Channel worker + internal review HTTP (no host publish of 18765)
bash host/apply-vps.sh --apply --enable-timer

bash host/verify-install.sh
```

Step 3 snapshots writable layers, keeps `<name>.pre-glm` stopped, and fail-
closes on config drift. Skip it until the dry-run plan matches production.

Do not restart cloudflared. Do not SMTP-test prospects.

## Rollback

`host/rollback.sh` (root): restore worker scripts, swap `.pre-glm` containers
back, revert surgical dashboard backups. GLM rows stay pending_review/held.

## Preserve

| Path | Rule |
|---|---|
| `legacy_permission.py`, `legacy_release.py`, `legacy_validate.py` | Byte-identical preservation copies. Frozen 83 only. |
| Sender window / cap | 9:00–before 18:00 America/Los_Angeles, `min(20, warmup ramp)`, existing guards. |
| `require_approval` | stays true |
| DMTC | emaildisabled / paused / max 0 |
| Job `a79ca986e5d2`, 7:58 PM exporter, Telegram | untouched |
| Container `harvey` identity | not recreated |
| Live outbox / recipient manifests | never modified by this bundle |

## Tests

`pytest tests/test_sss_glm_*.py` — channel, approval binding, exact recreate
fixtures (ports/volumes/networks/entrypoint/labels/health), review API, Studio
patch anchors. Hosts are `*.example`. No live DB, no SMTP.
