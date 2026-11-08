/* SSS GLM review panel for Studio. Approval-only. No send button. */
(function () {
  const H = window.HV;
  if (!H || !H.views) return;
  const esc = H.esc;
  const api = H.api;
  const post = H.post;
  const toast = H.toast;

  function num(v, unavailable) {
    if (unavailable || v === null || v === undefined) return 'unavailable';
    return String(v);
  }

  function statsCard(s) {
    const bad = !s || s.unavailable || s.ok === false;
    const asOf = (s && s.as_of) ? esc(s.as_of) : 'unavailable';
    const tz = (s && s.timezone) ? esc(s.timezone) : 'America/Tijuana';
    return `<div class="card">
      <h3>SSS sends</h3>
      <p class=mut>status=sent · ${tz} · as of ${asOf}. Money, scans and Harvey totals stay on the independent 8PM Hermes report — not duplicated here.</p>
      <p>Today <b>${num(s && s.sent_today, bad)}</b>
         · MTD <b>${num(s && s.sent_mtd, bad)}</b>
         · Total <b>${num(s && s.sent_total, bad)}</b></p>
      <p class=mut>Pending review ${num(s && s.pending_review, bad)}
         · Held ${num(s && s.held, bad)}</p>
    </div>`;
  }

  H.views.sssglm = async function () {
    const title = document.querySelector('#title');
    if (title) title.textContent = 'SSS drafts';
    const V = document.querySelector('#view');
    V.innerHTML = '<div class="card"><p class=mut>Loading SSS GLM drafts…</p></div>';
    let d, s;
    try {
      d = await api('/api/sss-glm/preview');
    } catch (e) {
      V.innerHTML = `<div class="card empty"><b>SSS drafts unavailable</b>${esc(e.message)}. This page talks to harvey-sss over the internal review network. The classic Outbox tab is not this list.</div>`;
      return;
    }
    try {
      s = await api('/api/sss-glm/stats');
    } catch (e) {
      s = { unavailable: true, ok: false };
    }
    const batches = d.batches || [];
    const head = statsCard(s);
    if (!batches.length) {
      V.innerHTML = head + '<div class="card empty"><b>No SSS GLM batches</b>Nothing waiting. New drafts show subject, body, id and hash here. Save an edit to get a new hash; then approve. Approving does not send.</div>';
      return;
    }
    V.innerHTML = head + batches.map(batch => {
      const rows = (batch.items || []).map(it => {
        const ok = it.matches_binding ? 'binding ok' : ('needs save: ' + (it.hold_reason || 'changed'));
        const canApprove = it.matches_binding && it.status === 'pending_review';
        const canReject = it.status === 'pending_review' || it.status === 'held' || it.status === 'approved';
        const canEdit = it.status !== 'sent';
        return `<div class="card" style="margin-top:12px" data-oid="${esc(it.outbox_id)}" data-hash="${esc(it.binding_sha256)}">
          <p class=mut>id <code>${esc(it.outbox_id)}</code> · hash <code>${esc(it.binding_sha256)}</code> · ${esc(it.status || '')} · ${esc(ok)}</p>
          <p class=mut>to ${esc(it.to_email || '')}</p>
          <label class=mut>Subject</label>
          <input class="ed-subj" style="width:100%;box-sizing:border-box" value="${esc(it.subject || '')}" ${canEdit ? '' : 'disabled'}>
          <label class=mut>Body</label>
          <textarea class="ed-body hm-draft" style="width:100%;box-sizing:border-box;min-height:8em;white-space:pre-wrap" ${canEdit ? '' : 'disabled'}>${esc(it.body || '')}</textarea>
          <p class=mut>Save writes a new hash and clears approval. Approve the exact current copy. No send button.</p>
          <div class=tbar>
            <button class="btn sv" ${canEdit ? '' : 'disabled'}>Save edit</button>
            <button class="btn primary ap" ${canApprove ? '' : 'disabled'}>Approve (does not send)</button>
            <button class="btn danger rj" ${canReject && it.status !== 'sent' ? '' : 'disabled'}>Reject</button>
          </div>
        </div>`;
      }).join('');
      return `<div class=card><h3>Batch ${esc(batch.batch_id)}</h3>
        <p class=mut>preview ${esc(batch.preview_sha256)} · ${esc(batch.status)}</p>${rows}</div>`;
    }).join('');
    V.querySelectorAll('.sv').forEach(b => b.onclick = async () => {
      const card = b.closest('[data-oid]');
      const subject = card.querySelector('.ed-subj').value;
      const body = card.querySelector('.ed-body').value;
      if (!confirm('Save this SSS GLM edit? Approval is cleared. The new copy needs a new approve. Nothing is sent.')) return;
      try {
        const r = await post('/api/sss-glm/edit', {
          confirm: 'edit',
          items: [{
            outbox_id: card.dataset.oid,
            binding_sha256: card.dataset.hash,
            subject,
            body
          }]
        });
        const one = (r.results && r.results[0]) || r;
        if (!one.ok) toast(one.error || 'edit held');
        else toast(one.unchanged ? 'Unchanged.' : 'Saved. New hash. Needs approve. Not sent.');
        H.views.sssglm();
      } catch (e) { toast(e.message); }
    });
    V.querySelectorAll('.ap').forEach(b => b.onclick = async () => {
      const card = b.closest('[data-oid]');
      if (!confirm('Approve this SSS GLM draft? It will wait for the existing sender. Nothing is sent now.')) return;
      try {
        const r = await post('/api/sss-glm/approve', {
          confirm: 'approve',
          items: [{ outbox_id: card.dataset.oid, binding_sha256: card.dataset.hash }]
        });
        if (r.held && r.held.length) toast(r.held[0].reason || 'held');
        else toast('Approved. Not sent.');
        H.views.sssglm();
      } catch (e) { toast(e.message); }
    });
    V.querySelectorAll('.rj').forEach(b => b.onclick = async () => {
      const card = b.closest('[data-oid]');
      if (!confirm('Reject this SSS GLM draft? It will be held. Nothing is sent.')) return;
      try {
        await post('/api/sss-glm/reject', {
          confirm: 'reject',
          items: [{ outbox_id: card.dataset.oid, binding_sha256: card.dataset.hash }]
        });
        toast('Rejected. Held. Not sent.');
        H.views.sssglm();
      } catch (e) { toast(e.message); }
    });
  };
})();
