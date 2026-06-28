/* Pure helpers for the console Telemetry surface. Dual-mode (browser global +
   module.exports) so it unit-tests on node:test with no build step, exactly
   like js/kpis.js. Loaded by index.html before the .jsx panels. */
(function () {
  // Compose a Grafana single-panel embed URL. Returns '' (honest-empty) when
  // base or uid is missing, so the iframe renders nothing rather than a broken src.
  function grafanaPanelUrl(base, uid, panelId, opts) {
    if (!base || !uid) return '';
    const o = opts || {};
    const theme = o.theme || 'dark';
    const root = String(base).replace(/\/+$/, '');
    let u = `${root}/d-solo/${encodeURIComponent(uid)}?panelId=${encodeURIComponent(panelId)}&theme=${theme}&kiosk`;
    if (o.from) u += `&from=${encodeURIComponent(o.from)}`;
    if (o.to) u += `&to=${encodeURIComponent(o.to)}`;
    return u;
  }

  const api = { grafanaPanelUrl };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (typeof window !== 'undefined')
    (window.PX || (window.PX = {})).telemetry = api;
})();
