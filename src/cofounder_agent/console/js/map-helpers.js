'use strict';
/* System-map topology + per-card GPU helpers (window.PXMap).
 *
 * Split out of modes.jsx so it is unit-testable without a JSX compile —
 * the qa-helpers / image-helpers / trace-helpers pattern. Pure: no DOM, no
 * React, no fetch. modes.jsx does the drawing.
 */
(function () {
  'use strict';
  // Keys MUST match the `name` field of the entries in data.js `services` —
  // SystemMap looks each node up via svcByName[name] for its live status. (The
  // 2026-06 service rename dropped the `poindexter-` display prefix; the real
  // cAdvisor container name lives on `s.container`, used by the health query.)
  //
  // Read left→right in five columns: storage + observability sinks · the control
  // plane · orchestration · the media/render tier · GPU runtimes. Spacing is set
  // so a 132px node box (150px for `core`) can't collide with a neighbour at the
  // canvas sizes .mapwrap produces; the bottom-left corner is left clear for the
  // legend and the top-right for the hint.
  //
  // This table sat untouched from the console's first commit (2026-06-09) while
  // the stack grew past it, so the map showed 11 of ~49 containers with no media
  // tier and no prefect-worker — the process that actually runs the pipeline.
  const MAP_NODES = [
    // ── storage + observability sinks ──
    { key: 'postgres-local', x: 10, y: 26 },
    { key: 'prometheus', x: 10, y: 46 },
    { key: 'loki', x: 10, y: 64 },
    { key: 'tempo', x: 10, y: 82 },
    // ── control plane ──
    { key: 'brain-daemon', x: 29, y: 12 },
    { key: 'worker', x: 29, y: 40, core: true },
    { key: 'glitchtip-web', x: 29, y: 64 },
    // ── orchestration ──
    { key: 'prefect-server', x: 47, y: 12 },
    { key: 'prefect-worker', x: 47, y: 40, core: true },
    // ── media / render tier ──
    // x=62 rather than 65: the GPU scheduler node sits in the corridor between
    // this column and the cards, and at 65 its 132px box overlapped `rife` on a
    // ~1190px canvas (measured, not estimated).
    { key: 'image-gen-server', x: 62, y: 17 },
    { key: 'comfyui', x: 62, y: 28 },
    { key: 'wan-server', x: 62, y: 39 },
    { key: 'rife', x: 62, y: 50 },
    { key: 'stable-audio', x: 62, y: 61 },
    { key: 'chatterbox', x: 62, y: 72 },
    { key: 'speaches', x: 62, y: 83 },
    // ── LLM runtime ──
    { key: 'ollama', x: 87, y: 14 },
  ];

  // Structural edges. The third field is FLOW EMPHASIS, not a health claim:
  // 'hot' brightens the line for the paths carrying the pipeline's actual work.
  // Edge COLOUR is derived from the live status of the endpoints (see flowKind).
  // A hardcoded 'err' here used to paint a permanent red link out of
  // prefect-server, and a hardcoded 'amber' a permanently degraded-looking one
  // out of image-gen-server — on infrastructure that was healthy throughout.
  const MAP_EDGES = [
    // Postgres is the bus: components talk THROUGH it, not to each other.
    ['worker', 'postgres-local', 'hot'],
    ['prefect-worker', 'postgres-local', 'hot'],
    ['brain-daemon', 'postgres-local', 'hot'],
    ['prefect-server', 'postgres-local', ''],
    // Dispatch — the content flow runs in prefect-worker, not in the FastAPI
    // worker. The old map had no prefect-worker node at all and drew
    // prefect-server straight at `worker`, which is not where a task executes.
    ['prefect-server', 'prefect-worker', 'hot'],
    // The brain additionally probes the API and reads Prometheus.
    ['brain-daemon', 'worker', ''],
    ['brain-daemon', 'prometheus', ''],
    // LLM
    ['prefect-worker', 'ollama', 'hot'],
    ['worker', 'ollama', ''],
    // Media / render tier is driven by the pipeline runner (ffmpeg is baked into
    // that image since #1449, and stage-2/3 render calls originate there).
    ['prefect-worker', 'image-gen-server', 'hot'],
    ['prefect-worker', 'comfyui', 'hot'],
    ['prefect-worker', 'wan-server', 'hot'],
    ['prefect-worker', 'rife', ''],
    ['prefect-worker', 'stable-audio', ''],
    ['prefect-worker', 'chatterbox', 'hot'],
    ['prefect-worker', 'speaches', ''],
    // Observability
    ['worker', 'loki', ''],
    ['worker', 'tempo', ''],
    ['worker', 'glitchtip-web', ''],
    ['prometheus', 'worker', ''],
  ];

  // Everything that contends for the GPU pool. These draw to the SCHEDULER,
  // never to an individual card: which card a consumer lands on is a scheduling
  // fact this surface doesn't have, so a consumer→card edge would assert a
  // pinning we'd be inventing. Routing them through the scheduler is also just
  // true — `services/gpu_scheduler.py` is what serializes them.
  const GPU_CONSUMERS = [
    'ollama',
    'image-gen-server',
    'comfyui',
    'wan-server',
    'rife',
    'stable-audio',
    'chatterbox',
    'speaches',
  ];

  // Per-card GPU layout. `gpu.gpus` is the per-card array api.gpu() builds off the
  // `gpu` label (poindexter#921); the `[gpu]` fallback mirrors panels.jsx so a
  // single-card install still renders. Cards are labelled by INDEX, never by
  // model — nvidia_gpu_* doesn't export a model name and api.gpu() deliberately
  // returns name:'' rather than fabricate one. This map carried a hardcoded
  // 'RTX 5090' string and read only the lowest-indexed card's scalars, so the
  // second card was invisible for the whole two-card era.
  const GPU_COL_X = 90;
  // The GPU scheduler sits where every consumer edge converges, because that is
  // literally what it does. It was an INVISIBLE anchor point until 2026-09-20 —
  // eight edges fanned into a spot with nothing drawn on it, which read as an
  // unlabelled node rather than as the lock arbitrating them.
  const GPU_SCHEDULER_POS = { x: 76, y: 52 };
  const GPU_ROW_GAP = 16;
  // Same threshold the Prometheus rule builder alerts on
  // (`threshold.gpu_temperature_celsius` → GpuTemperatureHigh), so the map and
  // the alert rules can't disagree about what "hot" means. Utilisation is
  // deliberately NOT a warn trigger: a card at 100% is doing the work it exists
  // for, and the old `util > 90` rule amber-flagged every render.
  const GPU_TEMP_WARN_C = 85;

  function gpuCardNodes(gpu) {
    const cards = gpu && gpu.gpus && gpu.gpus.length ? gpu.gpus : [gpu || {}];
    const n = cards.length;
    return cards.map((c, i) => ({
      key: 'gpu-' + (c && c.index != null ? c.index : i),
      x: GPU_COL_X,
      y: GPU_SCHEDULER_POS.y + (i - (n - 1) / 2) * GPU_ROW_GAP,
      gpu: true,
      card: c || {},
      index: c && c.index != null ? c.index : i,
    }));
  }

  // The GPU scheduler node (`services/gpu_scheduler.py`, GET /api/gpu/queue).
  //
  // READ THE SEMANTICS BEFORE CHANGING THIS. `holder` is the API process's OWN
  // in-process view of the lock (`gpu_scheduler._current_owner`). The pipeline
  // runs in a DIFFERENT process (poindexter-prefect-worker), so a lock held by
  // a live generation shows up here as `holder: null` — the route says so in its
  // docstring. `waiters` is the cross-process truth: DB-mirrored `gpu_queue`
  // rows, visible no matter which process holds the lock.
  //
  // Therefore: an empty queue is reported as "no contention" and NEVER as
  // "idle" or "free". Nothing is queued — that much is true and useful — but
  // this surface cannot see whether another process is mid-render, and saying
  // the GPU is free when it may be saturated is the same class of confident
  // lie as a stale probe reading green.
  //
  // `available` is the caller's answer to "did the queue poll actually succeed?"
  // Without it an unreachable endpoint is indistinguishable from a quiet one,
  // because both arrive as an empty object.
  function gpuSchedulerService(queue, available) {
    if (!available) {
      return {
        name: 'gpu-scheduler',
        status: 'off',
        metric: 'queue unavailable',
        sub: 'GPU lock',
      };
    }
    const q = queue || {};
    const waiters = Array.isArray(q.waiters) ? q.waiters : [];
    const holder = q.holder || null;
    let metric;
    if (waiters.length) {
      // Cross-process and authoritative — lead with it.
      const longest = waiters.reduce(
        (m, w) => Math.max(m, (w && w.waiting_s) || 0),
        0
      );
      metric =
        waiters.length + ' waiting · ' + fmtHoldSeconds(longest) + ' longest';
    } else if (holder) {
      metric =
        'held by ' +
        holder.owner +
        ' · ' +
        fmtHoldSeconds(holder.held_for_s || 0);
    } else {
      metric = 'no contention';
    }
    return {
      name: 'gpu-scheduler',
      // Contention is the scheduler WORKING, not failing — same reasoning as a
      // card at 100% util. Nothing here is ever 'err'.
      status: 'ok',
      metric,
      sub: 'GPU lock',
    };
  }

  // Compact seconds for hold/wait durations.
  function fmtHoldSeconds(secs) {
    const n = Number(secs) || 0;
    if (n < 90) return Math.round(n) + 's';
    if (n < 5400) return Math.round(n / 60) + 'm';
    return Math.round(n / 3600) + 'h';
  }

  // A card's node shape. A card with no reading is 'off' — absent telemetry must
  // never render as a healthy card.
  function gpuCardService(card, index) {
    const parts = [];
    if (card.util != null) parts.push(card.util + '%');
    if (card.temp != null) parts.push(card.temp + '°C');
    if (card.power != null) parts.push(card.power + 'W');
    if (card.vramUsed != null && card.vramTotal != null)
      parts.push(card.vramUsed + '/' + card.vramTotal + ' GB');
    const reporting = card.util != null || card.temp != null;
    return {
      name: 'GPU ' + index,
      status: !reporting
        ? 'off'
        : card.temp != null && card.temp >= GPU_TEMP_WARN_C
          ? 'warn'
          : 'ok',
      metric: parts.length ? parts.join(' · ') : 'no reading',
      sub: 'GPU',
      index,
    };
  }

  // Edge colour is DERIVED from endpoint health, never declared in the table.
  // Returns '' (neutral cyan flow), 'amber', or 'err'. An unknown endpoint
  // (null status) contributes nothing rather than guessing.
  function flowKind(statusA, statusB) {
    if (statusA === 'err' || statusB === 'err') return 'err';
    if (statusA === 'warn' || statusB === 'warn') return 'amber';
    return '';
  }

  // The GPU POOL's colour, for the consumer→cluster edges: a card with no
  // telemetry reads as down, a card over the temp threshold as degraded. This
  // describes the pool, never one card — see GPU_CONSUMERS.
  function poolFlowKind(cardStatuses) {
    if (cardStatuses.some((s) => s === 'off' || s === 'err')) return 'err';
    if (cardStatuses.some((s) => s === 'warn')) return 'amber';
    return '';
  }

  window.PXMap = {
    MAP_NODES,
    MAP_EDGES,
    GPU_CONSUMERS,
    GPU_COL_X,
    GPU_SCHEDULER_POS,
    GPU_ROW_GAP,
    GPU_TEMP_WARN_C,
    gpuCardNodes,
    gpuCardService,
    gpuSchedulerService,
    fmtHoldSeconds,
    flowKind,
    poolFlowKind,
  };
})();
