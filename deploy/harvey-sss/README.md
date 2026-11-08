# Approval-only GLM drafts for Site Safety Scan (Harvey overlay)

This is a **source and patch bundle**, not a live install. No VPS credentials
were used. Do not claim the worker is running until the parent applies it.

License: GNU AGPL v3.0 only (see `/LICENSE` and `/NOTICE` in this repository).

**Scope:** overlay the SSS worker only. Read `SCOPE.md` before applying.
This package cannot replace the custom Harvey dashboard.

## What this does

Future SSS outreach drafts (dental / law / accounting) get a **full subject and
body** from Hermes's existing OpenRouter model `z-ai/glm-5.3-flash`. SSS never
holds AI keys. The model has no tools, no memory, no SOUL, no context files, no
trajectories, no paid fallback, and no send capability.

Every new GLM draft is `pending_review` or `held`. Nothing is auto-approved or
sent.

The three opener-selection prototypes in the handoff archive are **rejected**.
They used an app-writable spool and only picked a sentence variant.

## Where drafts can actually be reviewed

Honest wiring (also in `SCOPE.md`):

- GLM rows land in **harvey-sss** `/app/data/harvey.db` outbox
  (`kind=harvey_mode`, `status=pending_review`).
- The custom web app at `harvey.dmtoclose.com` (container `harvey`, Cloudflare
  Access) is **not wired** to that table. This overlay does not add that
  wiring. Saved Oct 19–20 dashboard/campaign/editor/GLM-assistant requests are
  not shipped here.
- Upstream Sellbird `sellbird dashboard` / terminal Outbox is **not** the
  verified custom UI. Do not open `127.0.0.1:5555` and treat it as production.
- SSS is a separate container on localhost `:5556`. Parent must confirm whether
  anything already bound there lists this outbox; this bundle does not.

Read the copy (not hashes):

```
docker exec -u 1000 harvey-sss python3 /app/data/sss_glm_preview.py --show-copy
```

Approving only flips SSS outbox status. Recipient/subject/body edits invalidate
the binding; the sender holds. Do not invent a second approval page in this
change.

## Preserve (do not overwrite with stale snapshots)

| Path | Rule |
|---|---|
| `legacy_permission.py`, `legacy_release.py`, `legacy_validate.py` | Byte-identical to current production (see `preservation/`). Separate user permission for the frozen 83 original template drafts only. No new GLM ids are covered by that permission. |
| Sender window / cap | 9:00–before 18:00 America/Los_Angeles (same clock as Tijuana in November), `min(20, warmup ramp)`, existing precheck, unsub, footer, suppression, prior-contact, replies. |
| `require_approval` | stays true |
| DMTC | emaildisabled / paused / max 0 — this bundle does not touch it |
| Job `a79ca986e5d2` (8 PM Tijuana Harvey report), 7:58 PM exporter, Telegram `@LoboDeskBot` | untouched |
| Root-written jobs | do not change them (ownership breakage) |
| Live outbox / recipient manifests | this bundle never modifies them |
| Container `harvey` / `harvey.dmtoclose.com` | untouched |

The 12:02 8 Nov observation (20 approved / 63 held / 0 new sends / 159 total)
is **not** a current-count guarantee. Re-count on the VPS after deploy.

## Channel layout (least privilege)

Host directory **outside** `/opt/harvey-sss/data` so SSS cannot replace the parent:

```
/opt/sss-glm-channel/            root:root 0751
  requests/                      uid 1000, mounted RW in SSS, RO in Hermes
  responses/                     uid 1000, mounted RO in SSS, RW in Hermes
  ledger/                        uid 1000, Hermes only (private usage cap)
```

Both containers are uid 1000; **mount flags** are the isolation, not Unix
usernames. SSS must not get `docker.sock`, `--privileged`, or Hermes env.

Request JSON is only `{schema,id,domain,finding,category,verified_at,issued_at,nonce}`.
No scraped page text, no recipients, no proof blobs.

## Worker

Fixed command (do not parameterize):

```
docker exec -u hermes hermes-agent \
  /opt/hermes/.venv/bin/python \
  /opt/hermes/.hermes/scripts/approved/sss_glm/worker_main.py
```

Official SDK: `AIAgent(model='z-ai/glm-5.3-flash', provider='openrouter',
enabled_toolsets=[], max_iterations=1, max_tokens=500, skip_memory=True,
skip_context_files=True, load_soul_identity=False, save_trajectories=False,
fallback_model={})`. Unknown kwargs are dropped; isolation keys above are
required. Tools must be `[]` before any generate. Historical $20 OpenRouter
budget is unchanged; no paid fallback.

## Apply later (parent)

On the VPS, from this staged tree (default is dry-run, no sends):

```
bash host/apply-vps.sh
bash host/apply-vps.sh --apply
bash host/verify-install.sh
```

`--apply` copies worker files to `/opt/harvey-sss/data` and Hermes approved
scripts. It does **not** recreate containers.

`--recreate` adds channel mounts on `harvey-sss` and `hermes-agent` only.
It requires `SSS_GLM_CONFIRM_RECREATE=harvey-sss-only` after confirming:

1. container `harvey` (custom dashboard) will not be touched
2. harvey-sss writable-layer files under `/app` (any localhost `:5556` UI)
   live in the image or a volume — `docker rm`+`run` drops the rest
3. image stays `harvey-sss:v3`, uid 1000, command `sleep infinity`

Keep the original three harvey-sss mounts. Do not copy env/keys. Do not
modify outbox rows.

From this checkout without a live VPS:

```
bash deploy/harvey-sss/host/install.sh /tmp/sss-glm-stage
```

## Rollback

`host/rollback.sh` on the VPS (root): stop the timer, restore
`sss_engine.py.pre-glm` and `sss_spaced_send.py.pre-glm`, recreate harvey-sss
without the extra mounts. Leave `legacy_*` alone. GLM drafts remain
pending_review/held. Rollback never targets container `harvey`.

## Verify after parent deploy (live work remaining)

This cloud agent **did not** install on the VPS. Remaining live checks:

1. `requests` is RW and `responses` is RO **inside** harvey-sss; SSS `env` has
   no OpenRouter/Hermes keys. Container `harvey` still serves the custom UI.
2. Hermes worker runs as `hermes`, tools empty, ledger not visible in SSS.
3. One **synthetic** fixture (`example.com` / `.example`) request → real GLM →
   `pending_review` row, **zero** prospect sends. Optional:
   `SSS_GLM_LIVE=1 python -m sss_glm.live_dry_run` (disabled by default).
4. Re-count legacy 83: hashes unchanged, timer 9:05 AM Tijuana still bounded,
   sender cap 20, window intact, DMTC still paused.
5. Mutating a GLM body then approving must **hold** at send
   (`glm content changed; fresh preview required`). Untouched status-only
   approve must still be permitted.
6. Do not run SMTP tests against prospects.

## Tests in this repo

`pytest tests/test_sss_glm_*.py` — request→worker→pending_review, errors, stale
proof, injection, symlink/path/replay, approval mutation, cap 20, legacy file
hashes, SSS-only deploy scope. All hosts are `*.example`. No live DB, no SMTP.
