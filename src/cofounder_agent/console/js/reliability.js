/* ──────────────────────────────────────────────────────────────
   Poindexter Operator Console — reliability core.
   Pure logic (computeStale / resourceReducer / connectionReducer),
   the ConnectionState store, and the usePolledResource hook.
   Dual-mode: window globals (browser) + module.exports (Node tests).
   ────────────────────────────────────────────────────────────── */
(function () {
  'use strict';

  // A resource is stale when its last fetch errored, it has never loaded, or
  // its data is older than twice its own poll interval.
  function computeStale(lastUpdatedAt, intervalMs, error, now) {
    if (error != null) return true;
    if (lastUpdatedAt == null) return true;
    return now - lastUpdatedAt > 2 * intervalMs;
  }

  const RESOURCE_INIT = { data: null, lastUpdatedAt: null, error: null };

  // On success, swap in fresh data + timestamp and clear any error. On error,
  // KEEP the last good data (panels hold their last values) and record the
  // error so the UI can mark the panel stale.
  function resourceReducer(state, action) {
    switch (action.type) {
      case 'success':
        return { data: action.data, lastUpdatedAt: action.at, error: null };
      case 'error':
        return { ...state, error: action.error };
      default:
        return state;
    }
  }

  const CONNECTION_INIT = { consecutiveHealthFailures: 0, lastSeenAt: null };

  function connectionReducer(state, event) {
    switch (event.type) {
      case 'health-ok':
        return { consecutiveHealthFailures: 0, lastSeenAt: event.at };
      case 'health-fail':
        return {
          ...state,
          consecutiveHealthFailures: state.consecutiveHealthFailures + 1,
        };
      default:
        return state;
    }
  }

  function isDisconnected(state) {
    return state.consecutiveHealthFailures >= 3;
  }

  // Module-level singleton: the health resource reports here; the banner + the
  // topbar SYNC indicator subscribe. Keyed on a single connection signal.
  const ConnectionState = (function () {
    let state = CONNECTION_INIT;
    const subs = new Set();
    return {
      reportHealth(ok, at) {
        state = connectionReducer(
          state,
          ok
            ? { type: 'health-ok', at: at ?? Date.now() }
            : { type: 'health-fail' }
        );
        subs.forEach((fn) => fn(state));
      },
      getState() {
        return state;
      },
      subscribe(fn) {
        subs.add(fn);
        return () => subs.delete(fn);
      },
    };
  })();

  // React polling hook. Owns the interval, per-cycle retain-on-error state (via
  // resourceReducer), and connection reporting. The fetchFn is provided by the
  // caller (usually a PX.api method, which is already abort-bounded by http()).
  // The alive flag prevents a slow earlier response from clobbering a fresher
  // one after unmount / interval change.
  function usePolledResource(fetchFn, opts) {
    const intervalMs = opts.intervalMs;
    const key = opts.key;
    const React = window.React;
    const [state, dispatch] = React.useReducer(resourceReducer, RESOURCE_INIT);
    const fnRef = React.useRef(fetchFn);
    fnRef.current = fetchFn;

    React.useEffect(() => {
      let alive = true;
      const run = async () => {
        try {
          const data = await fnRef.current();
          if (!alive) return;
          dispatch({ type: 'success', data, at: Date.now() });
          if (key === 'health') ConnectionState.reportHealth(true);
        } catch (e) {
          if (!alive) return;
          dispatch({ type: 'error', error: e });
          if (key === 'health') ConnectionState.reportHealth(false);
        }
      };
      run();
      const timer = setInterval(run, intervalMs);
      return () => {
        alive = false;
        clearInterval(timer);
      };
    }, [intervalMs, key]);

    const stale = computeStale(
      state.lastUpdatedAt,
      intervalMs,
      state.error,
      Date.now()
    );
    return {
      data: state.data,
      lastUpdatedAt: state.lastUpdatedAt,
      error: state.error,
      stale,
    };
  }

  const api = {
    computeStale,
    resourceReducer,
    RESOURCE_INIT,
    connectionReducer,
    CONNECTION_INIT,
    isDisconnected,
    ConnectionState,
  };

  // Hook is browser-only (it needs React); attach it to the window namespace but
  // keep it out of module.exports so Node tests of the pure API never pull React.
  if (typeof window !== 'undefined') {
    window.PXR = Object.assign(window.PXR || {}, api, { usePolledResource });
  }
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  }
})();
