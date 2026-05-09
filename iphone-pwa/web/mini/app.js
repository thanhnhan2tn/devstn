/** DevStation PWA — Mini dashboard */
const PIPELINE_URL = `http://${location.hostname}:8001`;

window.loadMetrics = async function() {
  try {
    const r = await fetch(`${PIPELINE_URL}/metrics`);
    const txt = await r.text();
    return Object.fromEntries(
      txt.split('\n').filter(l => l && !l.startsWith('#')).map(l => {
        const [k, v] = l.split(' ');
        return [k, v];
      })
    );
  } catch { return {}; }
};

window.loadStatus = async function() {
  try {
    const r = await fetch(`${PIPELINE_URL}/healthz`);
    return await r.json();
  } catch { return null; }
};

window.approveReject = async function(id, action) {
  try {
    await fetch(`${PIPELINE_URL}/review/${id}/${action}`, {method: 'POST'});
    return true;
  } catch { return false; }
};
