/* SSS GLM review panel for Studio. Approval-only. No send button. */
(function () {
  const H = window.HV;
  if (!H || !H.views) return;
  const esc = H.esc;
  const api = H.api;
  const post = H.post;
  const toast = H.toast;
  H.views.sssglm = async function () {
    const title = document.querySelector('#title');
    if (title) title.textContent = 'SSS drafts';
    const V = document.querySelector('#view');
    V.innerHTML = '<div class="card"><p class=mut>Loading SSS GLM drafts…</p></div>';
    let d;
    try {
      d = await api('/api/sss-glm/preview');
    } catch (e) {
      V.innerHTML = `<div class="card empty"><b>SSS drafts unavailable</b>${esc(e.message)}. This page talks to harvey-sss over the internal review network. The classic Outbox tab is not this list.</div>`;
      return;
    }
    const batches = d.batches || [];
    if (!batches.length) {
      V.innerHTML = '<div class="card empty"><b>No SSS GLM batches</b>Nothing waiting. New drafts show subject, body, id and hash here. Approving does not send.</div>';
      return;
    }
    V.innerHTML = batches.map(batch => {
      const rows = (batch.items || []).map(it => {
        const ok = it.matches_binding ? 'binding ok' : ('hold: ' + (it.hold_reason || 'changed'));
        return `<div class="card" style="margin-top:12px" data-oid="${esc(it.outbox_id)}" data-hash="${esc(it.binding_sha256)}">
          <p class=mut>id <code>${esc(it.outbox_id)}</code> · hash <code>${esc(it.binding_sha256)}</code> · ${esc(it.status || '')} · ${esc(ok)}</p>
          <p><b>Subject</b> ${esc(it.subject || '')}</p>
          <pre class="hm-draft" style="white-space:pre-wrap">${esc(it.body || '')}</pre>
          <p class=mut>Edits in the SSS outbox row invalidate this hash. There is no send button here.</p>
          <div class=tbar>
            <button class="btn primary ap" ${it.matches_binding && it.status === 'pending_review' ? '' : 'disabled'}>Approve (does not send)</button>
            <button class="btn danger rj" ${it.status === 'pending_review' || it.status === 'held' ? '' : 'disabled'}>Reject</button>
          </div>
        </div>`;
      }).join('');
      return `<div class=card><h3>Batch ${esc(batch.batch_id)}</h3>
        <p class=mut>preview ${esc(batch.preview_sha256)} · ${esc(batch.status)}</p>${rows}</div>`;
    }).join('');
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
