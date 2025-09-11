// Milestone 4 — Minimal SPA: slug, KV table, realtime, editor, consoles, cluster viz
(function () {
  const qs = (s) => document.querySelector(s);
  const qsa = (s) => Array.from(document.querySelectorAll(s));

  // ------- Slug bootstrap -------
  function parseSlug() {
    // Prefer #ns=<slug>
    if (location.hash && location.hash.startsWith('#ns=')) {
      const slug = location.hash.slice(4).trim();
      if (slug) return slug;
    }
    // Fallback: /ns/<slug>
    const m = location.pathname.match(/^\/ns\/(.+)$/);
    if (m && m[1]) return m[1];
    return null;
  }

  function genSlug(len = 9) {
    const alphabet = '0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ';
    const arr = new Uint8Array(len);
    if (crypto && crypto.getRandomValues) crypto.getRandomValues(arr);
    return Array.from(arr, (n) => alphabet[n % alphabet.length]).join('');
  }

  function ensureSlug() {
    let slug = parseSlug();
    if (!slug) {
      slug = genSlug(9);
      // Prefer hash so it isn’t sent as Referer
      history.replaceState(null, '', `/#ns=${slug}`);
    } else if (!location.hash.startsWith('#ns=')) {
      // Normalize to hash without reload
      history.replaceState(null, '', `/#ns=${slug}`);
    }
    return slug;
  }

  // ------- API helpers -------
  function apiBase(slug) {
    return `/v1`;
  }

  async function fetchJSON(url, opts) {
    const res = await fetch(url, opts);
    if (!res.ok) {
      let body = '';
      try { body = await res.text(); } catch {}
      throw new Error(`HTTP ${res.status}${body ? `: ${body}` : ''}`);
    }
    return res.json();
  }

  function wsURL(path) {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    return `${proto}://${location.host}${path}`;
  }

  function tryParseValue(input) {
    const s = input.trim();
    if (s === 'true') return true;
    if (s === 'false') return false;
    if (/^-?\d+$/.test(s)) return parseInt(s, 10);
    if (/^-?\d+\.\d+$/.test(s)) return parseFloat(s);
    return s;
  }

  // ------- UI state -------
  const state = {
    slug: null,
    env: 'demo',
    kv: new Map(), // key -> { value, version, metadata }
    rows: new Map(), // key -> tr element
    ws: null,
    consoles: {}, // service -> { rootEl, logEl }
    clusterPrev: { leaderId: null, membersById: {}, healthByEndpoint: {} },
  };

  function setNSLabel() {
    qs('#ns-label').textContent = state.slug;
  }

  // ------- KV table rendering -------
  function renderRow(key, cfg) {
    let tr = state.rows.get(key);
    if (!tr) {
      tr = document.createElement('tr');
      const tds = ['key', 'value', 'version', 'by', 'at'].map(() => document.createElement('td'));
      tr.append(...tds);
      state.rows.set(key, tr);
      qs('#kv-body').appendChild(tr);
    }
    const [tdKey, tdVal, tdVer, tdBy, tdAt] = tr.children;
    tdKey.textContent = key;
    tdVal.textContent = JSON.stringify(cfg.value);
    tdVer.textContent = cfg.version || '';
    const md = cfg.metadata || {};
    tdBy.textContent = md.created_by || '';
    const at = md.created_at ? new Date(md.created_at).toLocaleString() : '';
    tdAt.textContent = at;
    return tr;
  }

  function removeRow(key) {
    const tr = state.rows.get(key);
    if (tr) {
      tr.remove();
      state.rows.delete(key);
    }
  }

  function renderAll() {
    // Sort keys for deterministic display
    const keys = Array.from(state.kv.keys()).sort();
    qs('#kv-body').innerHTML = '';
    state.rows.clear();
    keys.forEach((k) => renderRow(k, state.kv.get(k)));
  }

  // ------- WebSocket realtime -------
  function startWS() {
    const url = wsURL(`/v1/watch/${state.slug}/${state.env}`);
    const ws = new WebSocket(url);
    state.ws = ws;

    ws.onopen = () => {
      // Keepalive ping
      try { ws.send(JSON.stringify({ type: 'ping' })); } catch {}
    };
    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data);
        if (msg.type === 'config_change') {
          const { key, value, version, metadata } = msg;
          state.kv.set(key, { value, version, metadata });
          renderRow(key, { value, version, metadata });
          logToConsoles(key, value, 'PUT');
        } else if (msg.type === 'config_delete') {
          const { key } = msg;
          state.kv.delete(key);
          removeRow(key);
          logToConsoles(key, null, 'DEL');
        }
      } catch {}
    };
    ws.onclose = () => {
      // basic backoff reconnect
      setTimeout(startWS, 1000);
    };
  }

  // ------- Consoles -------
  function ensureConsole(service) {
    if (state.consoles[service]) return state.consoles[service];
    const root = document.createElement('div');
    root.className = 'console';
    const title = document.createElement('h3');
    title.textContent = service;
    const log = document.createElement('div');
    log.className = 'log';
    root.append(title, log);
    qs('#consoles').appendChild(root);
    state.consoles[service] = { rootEl: root, logEl: log };
    return state.consoles[service];
  }

  function logToConsoles(key, value, op) {
    const services = Object.keys(state.consoles);
    for (const svc of services) {
      const prefix = `${svc}/`;
      if (key && key.startsWith(prefix)) {
        const c = state.consoles[svc];
        if (!c) continue;
        const line = document.createElement('div');
        const ts = new Date().toLocaleTimeString();
        line.textContent = `[${ts}] ${op} ${key} ${value !== null ? '→ ' + JSON.stringify(value) : ''}`;
        c.logEl.appendChild(line);
        c.rootEl.scrollTop = c.rootEl.scrollHeight;
      }
    }
  }

  // ------- Cluster viz -------
  async function pollCluster() {
    try {
      const data = await fetchJSON('/cluster/status');
      const container = qs('#cluster');
      container.innerHTML = '';
      (data.members || []).forEach((m) => {
        const div = document.createElement('div');
        div.className = 'node';
        if (m.is_leader) div.classList.add('leader');
        if (!m.healthy) div.classList.add('unhealthy');
        const title = document.createElement('div');
        title.textContent = `${m.name || 'node'}${m.is_leader ? ' (leader)' : ''}`;
        const meta = document.createElement('div');
        meta.className = 'hint';
        meta.textContent = `${m.endpoint || ''} · ${m.version || ''}`;
        div.append(title, meta);
        container.appendChild(div);
      });

      handleClusterEvents(data);
    } catch (e) {
      // Ignore transient errors
    } finally {
      setTimeout(pollCluster, 2500);
    }
  }

  function handleClusterEvents(data) {
    const prev = state.clusterPrev || { leaderId: null, membersById: {}, healthByEndpoint: {} };
    const members = data.members || [];
    const byId = {};
    const healthByEndpoint = {};
    members.forEach((m) => {
      if (m.id) byId[m.id] = m.name || '';
      if (m.endpoint) healthByEndpoint[m.endpoint] = !!m.healthy;
    });

    // Leader change
    const curLeaderId = data.leader_id || (members.find((m) => m.is_leader)?.id ?? null);
    if (prev.leaderId && curLeaderId && curLeaderId !== prev.leaderId) {
      const oldName = prev.membersById?.[prev.leaderId] || prev.leaderId;
      const newName = byId[curLeaderId] || curLeaderId;
      appendClusterLog(`Leader changed: ${oldName} → ${newName}`);
    }

    // Health transitions
    members.forEach((m) => {
      const ep = m.endpoint || '';
      if (!ep) return;
      const prevH = prev.healthByEndpoint?.[ep];
      if (typeof prevH === 'boolean' && prevH !== !!m.healthy) {
        appendClusterLog(`${ep} ${m.healthy ? 'recovered' : 'became unhealthy'}`);
      }
    });

    state.clusterPrev = { leaderId: curLeaderId || prev.leaderId, membersById: byId, healthByEndpoint };
  }

  function appendClusterLog(text) {
    const box = qs('#cluster-log');
    if (!box) return;
    const line = document.createElement('div');
    const ts = new Date().toLocaleTimeString();
    line.textContent = `[${ts}] ${text}`;
    box.appendChild(line);
    // cap at last 100 lines
    while (box.childNodes.length > 100) box.removeChild(box.firstChild);
    box.scrollTop = box.scrollHeight;
  }

  async function killLeader() {
    try {
      appendClusterLog('Kill Leader requested');
      const res = await fetchJSON('/v1/chaos/kill-leader', { method: 'POST' });
      appendClusterLog(`Simulated kill of leader. Down: ${(res.down||[]).join(', ')}`);
      pollCluster();
    } catch (e) {
      appendClusterLog(`Kill Leader failed: ${e.message || e}`);
    }
  }

  async function reviveAll() {
    try {
      appendClusterLog('Revive requested');
      await fetchJSON('/v1/chaos/revive', { method: 'POST' });
      appendClusterLog('Simulated revive of all endpoints');
      pollCluster();
    } catch (e) {
      appendClusterLog(`Revive failed: ${e.message || e}`);
    }
  }

  // ------- Editor actions -------
  async function writeKey() {
    const key = qs('#key-input').value.trim();
    const raw = qs('#value-input').value;
    if (!key) return;
    const payload = { value: tryParseValue(raw) };
    const url = `/v1/config/${state.slug}/${state.env}/${encodeURIComponent(key)}`;
    try {
      const res = await fetchJSON(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      // optimistic: table will update via WS
      renderWriteLogs({
        context: res.replication_context || {
          namespace: state.slug,
          environment: state.env,
          key,
          value: payload.value,
          version: '(pending)'
        },
        log: res.replication_log || []
      });
    } catch (e) {
      alert(`Write failed: ${e.message || e}`);
    }
  }

  function renderWriteLogs(payload) {
    const box = qs('#write-logs');
    if (!box) return;
    const { context, log } = payload || {};
    const wrap = document.createElement('div');
    wrap.className = 'logs-entry';
    const title = document.createElement('h4');
    const when = context?.started_at ? new Date(context.started_at).toLocaleTimeString() : new Date().toLocaleTimeString();
    title.textContent = `Write ${context?.key ?? ''} = ${JSON.stringify(context?.value)} · ns=${context?.namespace} env=${context?.environment} @ ${when}`;
    const pre = document.createElement('pre');
    const lines = (log || []).map((r) => {
      const status = r.ok ? 'ok' : 'pending/timeout';
      return `${r.endpoint} — ${status} (${r.elapsed_ms}ms)`;
    });
    pre.textContent = lines.join('\n');
    wrap.append(title, pre);
    box.prepend(wrap);
  }

  function addServicePrompt() {
    let name = prompt('Add service (e.g., payments)');
    if (!name) return;
    name = name.trim();
    if (!/^[A-Za-z0-9][A-Za-z0-9-_]*$/.test(name)) {
      alert('Invalid service name. Use letters, numbers, dash or underscore.');
      return;
    }
    ensureConsole(name);
  }

  async function loadInitial() {
    try {
      const data = await fetchJSON(`/v1/config/${state.slug}/${state.env}`);
      const cfgs = data.configs || {};
      state.kv.clear();
      Object.keys(cfgs).forEach((k) => {
        state.kv.set(k, cfgs[k]);
      });
      renderAll();
    } catch (e) {
      // show empty state
    }
  }

  function share() {
    const url = location.href;
    navigator.clipboard?.writeText(url).then(
      () => { /* ok */ },
      () => { /* ignore */ }
    );
  }

  // ------- Init -------
  function init() {
    state.slug = ensureSlug();
    setNSLabel();
    loadInitial();
    startWS();
    pollCluster();

    qs('#write-btn').addEventListener('click', writeKey);
    qs('#add-service-btn').addEventListener('click', addServicePrompt);
    qs('#share-btn').addEventListener('click', share);
    const killBtn = qs('#kill-leader');
    const reviveBtn = qs('#revive-all');
    if (killBtn) killBtn.addEventListener('click', killLeader);
    if (reviveBtn) reviveBtn.addEventListener('click', reviveAll);
  }

  document.addEventListener('DOMContentLoaded', init);
})();
