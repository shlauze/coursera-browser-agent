async request => {
  const nb = globalThis.Jupyter?.notebook;
  if (!nb?.get_cells || !nb?.toJSON || !nb?.save_notebook)
    return {error: 'Supported classic Jupyter notebook is not ready'};
  const path = String(nb.notebook_path || '');
  if (!path.endsWith('.ipynb') || path.startsWith('/') || path.split('/').includes('..'))
    return {error: 'Invalid notebook path'};
  if (request.path && request.path !== path) return {error: 'Notebook identity changed'};
  const cells = nb.get_cells();
  const document = nb.toJSON();
  if (!cells.length || cells.length > 400 || JSON.stringify(document).length > 8000000)
    return {error: 'Notebook exceeds supported size'};
  const signature = () => JSON.stringify(nb.toJSON().cells.map(c => {
    const metadata = {...c.metadata};
    for (const key of ['trusted', 'collapsed', 'scrolled', 'execution', 'ExecuteTime']) delete metadata[key];
    return [c.cell_type, c.source, metadata, c.id];
  }));
  const stateKey = '__courseraV2NotebookExecution';
  const bounded = promise => Promise.race([promise, new Promise((_, reject) => setTimeout(() => reject(new Error('Notebook request timed out')), 20000))]);
  // Retry only idempotent reads. Kernel execution/restart/save and submission
  // are never repeated by this helper after an uncertain response.
  const readJSON = async (url, description) => {
    for (let attempt = 0; attempt < 3; attempt++) {
      try {
        const response = await fetch(url, {method: 'GET', credentials: 'same-origin', cache: 'no-store', signal: AbortSignal.timeout(15000)});
        if (response.status >= 500 && attempt < 2) {
          await new Promise(resolve => setTimeout(resolve, 300 * (attempt + 1)));
          continue;
        }
        if (!response.ok) throw new Error(description + ': HTTP ' + response.status);
        return await response.json();
      } catch (error) {
        if (attempt >= 2 || !['TimeoutError', 'AbortError', 'TypeError'].includes(error.name)) throw error;
        await new Promise(resolve => setTimeout(resolve, 300 * (attempt + 1)));
      }
    }
  };
  const kernelState = async () => {
    if (!nb.kernel?.kernel_url || !nb.kernel.is_connected()) return 'disconnected';
    const url = new URL(nb.kernel.kernel_url, location.href);
    if (url.origin !== location.origin) throw new Error('Unexpected kernel origin');
    const model = await readJSON(url.href, 'Cannot read kernel status');
    if (model.id !== nb.kernel.id) throw new Error('Kernel identity changed');
    return model.execution_state;
  };
  if (request.action === 'snapshot') return {path, document, signature: signature(), kernel: await kernelState()};
  if (request.signature && request.signature !== signature()) return {error: 'Notebook changed since inspection'};
  if (request.action === 'edit') {
    const cell = cells[request.index];
    if (await kernelState() !== 'idle') return {error: 'Kernel must be idle before editing'};
    if (request.signature !== signature()) return {error: 'Notebook changed while waiting for kernel'};
    if (!cell || cell.cell_type !== 'code' || cell.metadata?.nbgrader?.locked ||
        cell.metadata?.nbgrader?.grade || cell.metadata?.editable === false)
      return {error: 'Cell is protected or not code'};
    if (typeof request.code !== 'string' || request.code.length > 40000) return {error: 'Invalid cell source'};
    cell.set_text(request.code);
    nb.set_dirty(true);
    return {ok: true};
  }
  if (request.action === 'save') {
    await bounded(nb.save_notebook());
    const base = String(nb.base_url || '/').replace(/\/?$/, '/');
    const url = new URL(base + 'api/contents/' + path.split('/').map(encodeURIComponent).join('/') + '?content=1', location.origin);
    if (url.origin !== location.origin) return {error: 'Unexpected notebook server origin'};
    const saved = await readJSON(url.href, 'Notebook server did not confirm save');
    if (saved.type !== 'notebook' || JSON.stringify(saved.content?.cells) !== JSON.stringify(document.cells)) {
      // Object key order can differ on the server; compare meaningful source/metadata below.
      const canonical = x => Array.isArray(x) ? x.map(canonical) : x && typeof x === 'object'
        ? Object.fromEntries(Object.keys(x).sort().map(k => [k, canonical(x[k])])) : x;
      const normalize = d => d?.cells?.map(c => {
        // Jupyter recomputes output trust when saving/loading. It is not source
        // content and cannot be compared to the editor's pre-save trust flag.
        const metadata = {...c.metadata};
        delete metadata.trusted;
        return {...c, metadata, source: Array.isArray(c.source) ? c.source.join('') : c.source};
      });
      if (saved.type !== 'notebook' || JSON.stringify(canonical(normalize(saved.content))) !== JSON.stringify(canonical(normalize(document)))) {
        const difference = (a, b, path = 'cells') => {
          if (JSON.stringify(a) === JSON.stringify(b)) return null;
          if (!a || !b || typeof a !== 'object' || typeof b !== 'object') return path;
          for (const key of new Set([...Object.keys(a), ...Object.keys(b)])) {
            const found = difference(a[key], b[key], path + '.' + key);
            if (found) return found;
          }
          return path;
        };
        return {error: 'Saved notebook differs from the editor at ' + difference(canonical(normalize(saved.content)), canonical(normalize(document)))};
      }
    }
    return {ok: true};
  }
  if (request.action === 'restart') {
    if (await kernelState() !== 'idle') return {error: 'Cannot restart a busy kernel'};
    await bounded(new Promise((resolve, reject) => nb.kernel.restart(resolve, () => reject(new Error('Kernel restart failed')))));
    globalThis[stateKey] = null;
    return {ok: true};
  }
  if (request.action === 'run') {
    if (await kernelState() !== 'idle') return {error: 'Kernel is already busy; will not duplicate execution'};
    if (request.signature !== signature()) return {error: 'Notebook changed while waiting for kernel'};
    const previous = globalThis[stateKey];
    if (previous && (!previous.reply || !previous.idle)) return {error: 'Previous execution is unresolved'};
    const cell = cells[request.index];
    if (!cell || cell.cell_type !== 'code' || !cell.get_text().trim() || !cell.get_callbacks)
      return {error: 'Cell cannot be executed'};
    const state = {token: request.token, index: request.index, reply: null, idle: false, message_id: null};
    globalThis[stateKey] = state;
    const original = cell.get_callbacks;
    cell.get_callbacks = function () {
      const callbacks = original.call(this);
      const reply = callbacks.shell?.reply;
      const status = callbacks.iopub?.status;
      callbacks.shell = {...callbacks.shell, reply: msg => {
        if (msg.parent_header?.msg_id === state.message_id) state.reply = msg.content;
        if (reply) reply(msg);
      }};
      callbacks.iopub = {...callbacks.iopub, status: msg => {
        if (msg.parent_header?.msg_id === state.message_id && msg.content?.execution_state === 'idle') state.idle = true;
        if (status) status(msg);
      }};
      return callbacks;
    };
    try { cell.execute(); state.message_id = cell.last_msg_id; }
    finally { cell.get_callbacks = original; }
    if (!state.message_id) return {error: 'Kernel did not accept execution'};
    return {ok: true, message_id: state.message_id};
  }
  if (request.action === 'poll' || request.action === 'interrupt') {
    const state = globalThis[stateKey];
    if (!state || state.token !== request.token) return {error: 'Execution ownership was lost'};
    if (request.action === 'interrupt') {
      if (!state.idle) nb.kernel.interrupt();
      return {ok: true};
    }
    return {...state, kernel: await kernelState(), outputs: cells[state.index].output_area?.outputs || []};
  }
  return {error: 'Unknown notebook operation'};
}
