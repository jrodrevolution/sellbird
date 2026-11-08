# Approval-only GLM drafts for Site Safety Scan (Harvey overlay)

This is a **source and patch bundle**, not a live install. No VPS credentials
were used. Do not claim the worker is running until the parent applies it.

License: GNU AGPL v3.0 only (see `/LICENSE` and `/NOTICE` in this repository).

## What this does

Future SSS outreach drafts (dental / law / accounting) get a **full subject and
body** from Hermes's existing OpenRouter model `z-ai/glm-5.3-flash`. SSS never
holds AI keys. The model has no tools, no memory, no SOUL, no context files, no
trajectories, no paid fallback, and no send capability.

Every new GLM draft is `pending_review` or `held`. Nothing is auto-approved or
sent. Jose reviews in the **existing Outbox**, not a new UI.

The three opener-selection prototypes in the handoff archive are **rejected**.
They used an app-writable spool and only picked a sentence variant.

## Where Jose reviews batches

1. **Dashboard Outbox** — the Outbox Jose already uses. Local checkout:
   `sellbird dashboard` prints a private loopback link
   (`http://127.0.0.1:5555/#token=…`). Open **Outbox**. `J`/`K` move, `A`
   approve, `E` edit, `R` reject. On the VPS, use the Harvey dashboard you
   already open for pending_review mail (same Outbox / decisions desk).
2. **CLI** — `sellbird outbox`, then `sellbird outbox --approve <id>`.
3. **Binding snapshot** (read-only) —
   `python /app/data/sss_glm_preview.py` prints immutable content hashes.
   Editing subject/body invalidates the hash; the sender holds the row until
   Jose re-previews. `python /app/data/sss_glm_approve.py --batch ID` is dry
   by default; `--confirm` is the same human approval as Outbox, still no send.

Do not invent a second approval page.

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
fallback_model={})`. Tools must be `[]` before any generate. Historical $20
OpenRouter budget is unchanged; no paid fallback.

## Apply later (parent)

From this checkout:

```
bash deploy/harvey-sss/host/install.sh /tmp/sss-glm-stage
```

Then on the VPS, follow `/tmp/sss-glm-stage/COPY_TO_VPS.txt` and
`host/recreate-harvey-sss.snippet.sh`. Run `host/channel-layout.sh` as root.
Keep `harvey-sss:v3`, uid 1000, `sleep infinity`, and the original three mounts.

## Rollback

`host/rollback.sh` on the VPS (root): stop the timer, restore
`sss_engine.py.pre-glm` and `sss_spaced_send.py.pre-glm`, recreate harvey-sss
without the extra mounts. Leave `legacy_*` alone. GLM drafts remain
pending_review/held for Jose to reject in Outbox.

## Verify after parent deploy (live work remaining)

This cloud agent **did not** install on the VPS. Remaining live checks:

1. `requests` is RW and `responses` is RO **inside** harvey-sss; SSS `env` has
   no OpenRouter/Hermes keys.
2. Hermes worker runs as `hermes`, tools empty, ledger not visible in SSS.
3. One **synthetic** fixture (`example.com` / `.example`) request → real GLM →
   `pending_review` row, **zero** prospect sends. Optional:
   `SSS_GLM_LIVE=1 python -m sss_glm.live_dry_run` (disabled by default).
4. Re-count legacy 83: hashes unchanged, timer 9:05 AM Tijuana still bounded,
   sender cap 20, window intact, DMTC still paused.
5. Mutating a GLM body in Outbox (`E`) then approving must **hold** at send
   (`glm content changed; fresh preview required`).
6. Do not run SMTP tests against prospects.

## Tests in this repo

`pytest tests/test_sss_glm_*.py` — request→worker→pending_review, errors, stale
proof, injection, symlink/path/replay, approval mutation, cap 20, legacy file
hashes. All hosts are `*.example`. No live DB, no SMTP.
