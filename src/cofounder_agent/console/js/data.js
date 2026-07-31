/* ──────────────────────────────────────────────────────────────
   Poindexter Operator Console — mock data + live simulation.
   Authentic to the real stack: services, probes, pipeline stages.
   Exposes window.PX.
   ────────────────────────────────────────────────────────────── */
(function () {
  const now = new Date('2026-06-08T14:32:00');
  const hhmmss = (d) => d.toTimeString().slice(0, 8);
  const ago = (mins) => {
    if (mins < 1) return 'now';
    if (mins < 60) return mins + 'm ago';
    const h = Math.floor(mins / 60);
    if (h < 24) return h + 'h ago';
    return Math.floor(h / 24) + 'd ago';
  };

  // ── KPI strip ───────────────────────────────────────────────
  const kpis = [
    {
      id: 'published',
      label: 'Published (30d)',
      value: 79,
      unit: '',
      tone: '',
      delta: +6,
      deltaLabel: '+6 vs prev',
      spark: [
        3, 1, 0, 2, 1, 0, 4, 2, 3, 1, 0, 5, 2, 1, 3, 4, 2, 6, 3, 2, 1, 0, 4, 5,
        3, 2, 7, 4, 3, 5,
      ],
    },
    {
      id: 'approval',
      label: 'Awaiting Approval',
      value: 4,
      unit: '',
      tone: 'amber',
      action: 'approvals',
      delta: +4,
      deltaLabel: 'needs you',
      spark: [
        0, 0, 1, 0, 2, 1, 0, 1, 3, 2, 1, 0, 2, 1, 0, 1, 2, 3, 2, 1, 0, 1, 2, 1,
        3, 2, 4, 3, 2, 4,
      ],
    },
    {
      id: 'failed',
      label: 'Failed Tasks (24h)',
      value: 3,
      unit: '',
      tone: 'alert',
      action: 'tasks',
      delta: +3,
      deltaLabel: 'investigate',
      spark: [
        0, 0, 0, 1, 0, 0, 0, 2, 0, 1, 0, 0, 0, 0, 1, 0, 0, 3, 0, 0, 1, 0, 0, 2,
        0, 1, 0, 0, 2, 3,
      ],
    },
    {
      id: 'quality',
      label: 'Avg Quality (7d)',
      value: 82.4,
      unit: '',
      tone: 'mint',
      delta: +1.7,
      deltaLabel: 'target ≥ 80',
      spark: [
        78, 79, 77, 80, 81, 79, 82, 80, 83, 81, 82, 84, 83, 82, 81, 83, 84, 82,
        83, 85, 82, 81, 83, 84, 82, 83, 84, 82, 83, 82,
      ],
    },
    {
      id: 'traffic',
      label: 'Page Views (24h)',
      value: 1284,
      unit: '',
      tone: '',
      delta: -8,
      deltaLabel: '-8% d/d',
      spark: [
        42, 38, 55, 61, 48, 72, 80, 65, 90, 77, 84, 69, 95, 88, 76, 82, 91, 70,
        66, 84, 79, 92, 88, 74, 81, 77, 69, 85, 72, 64,
      ],
    },
    {
      id: 'spend',
      label: 'Cloud Spend (mo)',
      value: 11.42,
      unit: '$',
      tone: '',
      delta: -2.1,
      deltaLabel: '$50 budget',
      spark: [
        0.2, 0.3, 0.4, 0.4, 0.5, 0.6, 0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.4,
        1.5, 1.6, 1.7, 1.9, 2.0, 2.2, 2.4, 2.6, 2.9, 3.2, 3.6, 4.0, 4.6, 5.2,
        6.1, 7.0,
      ],
    },
  ];

  // ── Action inbox — the thing Grafana can't do ───────────────
  const inbox = [
    {
      id: 'apr-512',
      kind: 'approve',
      priority: 1,
      title: 'The Quiet Compounding of Local-First Infrastructure',
      sub: [
        ['QUALITY', '84'],
        ['MODEL', 'glm-4.7-50b'],
        ['WORDS', '1,840'],
        ['CHANNEL', 'Blog · Newsletter'],
      ],
      age: ago(12),
      tags: [['cyan', 'READY']],
      detail: {
        excerpt:
          "Most teams reach for a managed cloud before they understand their own workload. The compounding advantage of running your own stack isn't cost — it's the feedback loop. When the GPU, the database, and the model all live under one roof, every experiment is free and every failure is legible…",
        quality: 84,
        breakdown: [
          ['Originality', 86],
          ['Clarity', 83],
          ['Evidence', 81],
          ['SEO', 87],
          ['Brand voice', 82],
        ],
        pipeline: 'awaiting_approval',
        topic: 'Infrastructure',
        created: ago(38),
      },
    },
    {
      id: 'apr-513',
      kind: 'approve',
      priority: 1,
      title: 'Why We Killed Our Observability Vendor (And What We Built)',
      sub: [
        ['QUALITY', '81'],
        ['MODEL', 'glm-4.7-50b'],
        ['WORDS', '2,210'],
        ['CHANNEL', 'Blog'],
      ],
      age: ago(34),
      tags: [['cyan', 'READY']],
      detail: {
        excerpt:
          "A $0 observability stack is not a fantasy. Prometheus, Loki, Tempo, and a 200-line brain daemon replaced a four-figure monthly bill. Here's the migration, the gaps, and the one thing we still miss…",
        quality: 81,
        breakdown: [
          ['Originality', 82],
          ['Clarity', 84],
          ['Evidence', 79],
          ['SEO', 80],
          ['Brand voice', 80],
        ],
        pipeline: 'awaiting_approval',
        topic: 'Observability',
        created: ago(58),
      },
    },
    {
      id: 'fail-2231',
      kind: 'fail',
      priority: 2,
      title: 'Task #2231 failed — image_search timeout',
      sub: [
        ['STAGE', 'illustrate'],
        ['RETRIES', '2/3'],
        ['ERROR', 'httpx.ReadTimeout'],
      ],
      age: ago(48),
      tags: [['red', 'FAILED']],
      detail: {
        task: 2231,
        stage: 'generate_images',
        error:
          'httpx.ReadTimeout: image_search provider exceeded 30s read timeout',
        topic: 'The Economics of Self-Hosted GPUs',
        retries: 2,
        lastRun: ago(48),
        trace:
          'brain.probe.image_search → unsplash_provider → connect 0.4s → read >30s ✕',
      },
    },
    {
      id: 'alert-dec-88',
      kind: 'alert',
      priority: 2,
      title: 'EmbeddingsStale — semantic memory 41m behind',
      sub: [
        ['SEVERITY', 'warning'],
        ['PROBE', 'embeddings_freshness'],
        ['SINCE', '41m'],
      ],
      age: ago(6),
      tags: [['amber', 'UNACKED']],
      detail: {
        severity: 'warning',
        probe: 'embeddings_freshness',
        source: 'Prometheus → EmbeddingsStale',
        detail:
          '24 source rows changed since last embed cycle. Threshold is 15m; currently 41m behind. nomic-embed-text queue depth = 24.',
        recommend:
          'Embedding worker is up but throttled behind GPU contention (image-gen running). Will self-clear when illustrate stage drains, or trigger embed cycle manually.',
        firstSeen: ago(41),
      },
    },
    {
      id: 'drift-prefect',
      kind: 'drift',
      priority: 3,
      title: 'Operator URL drift — Prefect UI unreachable',
      sub: [
        ['SURFACE', 'Pipeline :: Prefect UI'],
        ['STATUS', 'ConnectError'],
        ['IP', '100.64.1.5'],
      ],
      age: ago(73),
      tags: [['amber', 'DRIFT']],
      detail: {
        surface: 'Pipeline Operations :: Prefect UI',
        url: 'http://100.64.1.5:4200/dashboard',
        error: 'ConnectError: All connection attempts failed',
        recommend:
          "Tailscale IP for device 'brain-pc' drifted. Run: UPDATE system_devices SET tailscale_ip='100.64.1.9' WHERE hostname='brain-pc';",
        fix: "UPDATE system_devices SET tailscale_ip='100.64.1.9' WHERE hostname='brain-pc';",
        firstSeen: ago(73),
      },
    },
    {
      id: 'alert-dec-89',
      kind: 'alert',
      priority: 3,
      title: 'Cadence SLO — 0.8/day vs 1.0/day target',
      sub: [
        ['SEVERITY', 'warning'],
        ['PROBE', 'cadence_slo'],
        ['WINDOW', '7d'],
      ],
      age: ago(180),
      tags: [['amber', 'UNACKED']],
      detail: {
        severity: 'warning',
        probe: 'cadence_slo',
        source: 'brain.probe.cadence_slo',
        detail:
          'Actual publish cadence is 0.8 posts/day against operator-configured target of 1.0/day over the trailing 7 days.',
        recommend:
          'Two failed tasks this week reduced throughput. No action required if backlog clears; consider raising research concurrency.',
        firstSeen: ago(180),
      },
    },
  ];

  // ── Services / containers ───────────────────────────────────
  // The FULL container roster (all ~44 poindexter-* containers, incl. redis/
  // minio/db sidecars + backups — poindexter#909: an operator wants to see AND
  // restart plumbing, not just the top-level services). `container` is the
  // REAL cAdvisor `name` label used by the live health query
  //   time() - container_last_seen{name="<container>"}
  // (see js/api.js serviceHealth) AND the real name the restart action posts
  // to `POST /api/services/{container}/restart`. `host:true` = a host process
  // cAdvisor can't see (ollama at :11434) — never faked live, and NOT
  // restartable via this surface (no docker.sock reach — it isn't a
  // container). The status/metric/cpu/mem/img/uptime values here are
  // MOCK-mode realism; live mode overwrites every one of them from cAdvisor
  // (img comes from the series' `image` label).
  const services = [
    {
      name: 'worker',
      container: 'poindexter-worker',
      port: 8002,
      status: 'ok',
      metric: 'p95 142ms',
      sub: 'FastAPI API',
      uptime: '6d 4h',
      cpu: 18,
      mem: 1240,
      probe: 'content_gen ✓',
      img: 'glad-labs-website-worker',
    },
    {
      name: 'prefect-worker',
      container: 'poindexter-prefect-worker',
      port: null,
      status: 'ok',
      metric: 'task #2241',
      sub: 'pipeline runner',
      uptime: '6d 4h',
      cpu: 22,
      mem: 1680,
      probe: 'flow heartbeat ✓',
      img: 'glad-labs-website-prefect-worker',
    },
    {
      name: 'brain-daemon',
      container: 'poindexter-brain-daemon',
      port: null,
      status: 'ok',
      metric: 'cycle 4.2s',
      sub: 'self-heal watchdog',
      uptime: '6d 4h',
      cpu: 3,
      mem: 210,
      probe: '28/28 probes ✓',
      img: 'glad-labs-website-brain-daemon',
    },
    {
      name: 'postgres-local',
      container: 'poindexter-postgres-local',
      port: 5432,
      status: 'ok',
      metric: '17.3 GB',
      sub: 'pg16 · pgvector',
      uptime: '21d 9h',
      cpu: 6,
      mem: 2100,
      probe: 'db_ping ✓',
      img: 'pgvector/pgvector:pg16',
    },
    {
      name: 'ollama',
      container: null,
      host: true,
      port: 11434,
      status: 'warn',
      metric: 'GPU queue 2',
      // Descriptor only — do NOT pin a model name here. serviceHealth() never
      // overrides `sub`, so a hardcoded model freezes stale (it read
      // "glm-4.7-50b" long after that model was retired). Which model is loaded
      // is a live fact ollama's /api/ps owns, not a static console string.
      sub: 'LLM runtime · host',
      uptime: '2d 11h',
      cpu: 64,
      mem: 9800,
      probe: 'ollama_models ⚠',
      img: 'host process',
    },
    {
      name: 'image-gen-server',
      container: 'poindexter-image-gen-server',
      port: 9836,
      status: 'ok',
      metric: 'gen 8.4s',
      sub: 'image gen',
      uptime: '2d 11h',
      cpu: 71,
      mem: 6200,
      probe: 'image_gen ✓',
      img: 'glad-labs-website-image-gen-server',
    },
    {
      name: 'prefect-server',
      container: 'poindexter-prefect-server',
      port: 4200,
      status: 'err',
      metric: 'unreachable',
      sub: 'orchestration',
      uptime: '—',
      cpu: 0,
      mem: 0,
      probe: 'connect ✕',
      img: 'prefecthq/prefect:3',
    },
    {
      name: 'prometheus',
      container: 'poindexter-prometheus',
      port: 9091,
      status: 'ok',
      metric: '142 series',
      sub: 'metrics',
      uptime: '21d 9h',
      cpu: 2,
      mem: 480,
      probe: 'scrape ✓',
      img: 'prom/prometheus:v2.54',
    },
    {
      name: 'grafana',
      container: 'poindexter-grafana',
      port: 3000,
      status: 'ok',
      metric: '13 boards',
      sub: 'dashboards',
      uptime: '21d 9h',
      cpu: 2,
      mem: 360,
      probe: 'ready ✓',
      img: 'grafana/grafana:11.3.0',
    },
    {
      name: 'loki',
      container: 'poindexter-loki',
      port: 3100,
      status: 'ok',
      metric: '4.1 GB',
      sub: 'logs',
      uptime: '21d 9h',
      cpu: 1,
      mem: 320,
      probe: 'ready ✓',
      img: 'grafana/loki:3.3.2',
    },
    {
      name: 'tempo',
      container: 'poindexter-tempo',
      port: 3200,
      status: 'ok',
      metric: '12k spans/h',
      sub: 'traces',
      uptime: '21d 9h',
      cpu: 2,
      mem: 410,
      probe: 'ready ✓',
      img: 'grafana/tempo:2.6',
    },
    {
      name: 'pyroscope',
      container: 'poindexter-pyroscope',
      port: 4040,
      status: 'ok',
      metric: 'profiling',
      sub: 'flame graphs',
      uptime: '21d 9h',
      cpu: 1,
      mem: 290,
      probe: 'ready ✓',
      img: 'grafana/pyroscope:1.9',
    },
    {
      name: 'alertmanager',
      container: 'poindexter-alertmanager',
      port: 9093,
      status: 'ok',
      metric: '0 firing',
      sub: 'alert routing',
      uptime: '21d 9h',
      cpu: 1,
      mem: 120,
      probe: 'ready ✓',
      img: 'prom/alertmanager:v0.27',
    },
    {
      name: 'langfuse-web',
      container: 'poindexter-langfuse-web',
      port: 3010,
      status: 'ok',
      metric: 'traces ✓',
      sub: 'LLM traces',
      uptime: '14d 2h',
      cpu: 2,
      mem: 640,
      probe: 'ready ✓',
      img: 'langfuse/langfuse:3',
    },
    {
      name: 'glitchtip-web',
      container: 'poindexter-glitchtip-web',
      port: 8080,
      status: 'ok',
      metric: '0 new',
      sub: 'errors',
      uptime: '14d 2h',
      cpu: 1,
      mem: 540,
      probe: 'triage ✓',
      img: 'glitchtip/glitchtip:4.1',
    },
    {
      name: 'pgadmin',
      container: 'poindexter-pgadmin',
      port: 18443,
      status: 'ok',
      metric: 'idle',
      sub: 'db admin',
      uptime: '14d 2h',
      cpu: 0,
      mem: 280,
      probe: 'ready ✓',
      img: 'dpage/pgadmin4:8',
    },
    {
      name: 'cadvisor',
      container: 'poindexter-cadvisor',
      port: 8080,
      status: 'ok',
      metric: '39 cntrs',
      sub: 'container metrics',
      uptime: '21d 9h',
      cpu: 4,
      mem: 220,
      probe: 'scrape ✓',
      img: 'gcr.io/cadvisor/cadvisor:v0.49.1',
    },
    {
      name: 'uptime-kuma',
      container: 'poindexter-uptime-kuma',
      port: 3002,
      status: 'ok',
      metric: 'all up',
      sub: 'uptime',
      uptime: '21d 9h',
      cpu: 1,
      mem: 160,
      probe: 'ready ✓',
      img: 'louislam/uptime-kuma:1',
    },
    // NOTE: the voice-room tiles (livekit + voice-agent-livekit) are absent —
    // both sit behind the opt-in `voice` compose profile (off on the operator
    // stack since 2026-07-26). A roster tile for an intentionally-stopped
    // container renders a permanent "DOWN" in Service Health, which is alarm
    // noise, not signal. Re-add the two tiles if the voice profile returns.
    {
      name: 'speaches',
      container: 'poindexter-speaches',
      tailnet: true,
      port: null,
      status: 'ok',
      metric: 'STT/TTS',
      sub: 'voice models',
      uptime: '6d 4h',
      cpu: 3,
      mem: 1400,
      probe: 'ready ✓',
      img: 'ghcr.io/speaches-ai/speaches',
    },
    // ── Media / GPU sidecars ─────────────────────────────────
    {
      name: 'wan-server',
      container: 'poindexter-wan-server',
      port: 9840,
      status: 'ok',
      metric: 'render idle',
      sub: 'video gen (WAN)',
      uptime: '2d 11h',
      cpu: 4,
      mem: 2100,
      probe: 'ready ✓',
      img: 'glad-labs-website-wan-server',
    },
    {
      name: 'chatterbox',
      container: 'poindexter-chatterbox',
      port: 8011,
      status: 'ok',
      metric: 'TTS idle',
      sub: 'podcast/video TTS',
      uptime: '1d 2h',
      cpu: 1,
      mem: 900,
      probe: 'ready ✓',
      img: 'glad-labs-website-chatterbox',
    },
    {
      name: 'gpu-exporter',
      container: 'poindexter-gpu-exporter',
      port: 9835,
      status: 'ok',
      metric: 'scrape ✓',
      sub: 'nvidia-smi exporter',
      uptime: '21d 9h',
      cpu: 1,
      mem: 60,
      probe: 'ready ✓',
      img: 'glad-labs-website-gpu-exporter',
    },
    // ── Operator bots / CI ───────────────────────────────────
    {
      name: 'pipeline-bot',
      container: 'poindexter-pipeline-bot',
      port: null,
      status: 'ok',
      metric: 'polling',
      sub: 'Telegram operator bot',
      uptime: '2d 11h',
      cpu: 1,
      mem: 180,
      probe: 'ready ✓',
      img: 'glad-labs-website-pipeline-bot',
    },
    {
      name: 'ci-runner',
      container: 'poindexter-ci-runner-1',
      port: null,
      status: 'ok',
      metric: 'idle',
      sub: 'self-hosted GH Actions runner',
      uptime: '2d 11h',
      cpu: 1,
      mem: 220,
      probe: 'ready ✓',
      img: 'myoung34/github-runner:latest',
    },
    // (voice-agent-livekit tile removed with the voice profile — see the
    // livekit note above.)
    // ── Backups ──────────────────────────────────────────────
    {
      name: 'backup-hourly',
      container: 'poindexter-backup-hourly',
      port: null,
      status: 'ok',
      metric: 'last run ok',
      sub: 'DB backup · hourly',
      uptime: '21d 9h',
      cpu: 0,
      mem: 40,
      probe: 'ready ✓',
      img: 'poindexter-backup:latest',
    },
    {
      name: 'backup-daily',
      container: 'poindexter-backup-daily',
      port: null,
      status: 'ok',
      metric: 'last run ok',
      sub: 'DB backup · daily',
      uptime: '21d 9h',
      cpu: 0,
      mem: 40,
      probe: 'ready ✓',
      img: 'poindexter-backup:latest',
    },
    {
      name: 'backup-offsite',
      container: 'poindexter-backup-offsite',
      port: null,
      status: 'ok',
      metric: 'last run ok',
      sub: 'B2 offsite backup',
      uptime: '21d 9h',
      cpu: 0,
      mem: 40,
      probe: 'ready ✓',
      img: 'poindexter-backup:latest',
    },
    // ── Postiz (social distribution) ────────────────────────
    {
      name: 'postiz',
      container: 'poindexter-postiz',
      port: 5003,
      status: 'ok',
      metric: 'ready',
      sub: 'social distribution hub',
      uptime: '14d 2h',
      cpu: 2,
      mem: 480,
      probe: 'ready ✓',
      img: 'ghcr.io/gitroomhq/postiz-app:latest',
    },
    {
      name: 'postiz-db',
      container: 'poindexter-postiz-db',
      port: null,
      status: 'ok',
      metric: 'ready',
      sub: 'postiz · db sidecar',
      uptime: '14d 2h',
      cpu: 1,
      mem: 180,
      probe: 'ready ✓',
      img: 'postgres:16-alpine',
    },
    {
      name: 'postiz-redis',
      container: 'poindexter-postiz-redis',
      port: null,
      status: 'ok',
      metric: 'ready',
      sub: 'postiz · cache',
      uptime: '14d 2h',
      cpu: 1,
      mem: 60,
      probe: 'ready ✓',
      img: 'redis:7-alpine',
    },
    {
      name: 'postiz-temporal',
      container: 'poindexter-postiz-temporal',
      port: null,
      status: 'ok',
      metric: 'ready',
      sub: 'postiz · workflow engine',
      uptime: '14d 2h',
      cpu: 2,
      mem: 260,
      probe: 'ready ✓',
      img: 'temporalio/auto-setup:latest',
    },
    // ── Langfuse sidecars ────────────────────────────────────
    {
      name: 'langfuse-worker',
      container: 'poindexter-langfuse-worker',
      port: null,
      status: 'ok',
      metric: 'ready',
      sub: 'langfuse · ingest worker',
      uptime: '14d 2h',
      cpu: 2,
      mem: 380,
      probe: 'ready ✓',
      img: 'langfuse/langfuse-worker:3',
    },
    {
      name: 'langfuse-clickhouse',
      container: 'poindexter-langfuse-clickhouse',
      port: null,
      status: 'ok',
      metric: 'ready',
      sub: 'langfuse · trace store',
      uptime: '14d 2h',
      cpu: 3,
      mem: 900,
      probe: 'ready ✓',
      img: 'clickhouse/clickhouse-server',
    },
    {
      name: 'langfuse-minio',
      container: 'poindexter-langfuse-minio',
      port: null,
      status: 'ok',
      metric: 'ready',
      sub: 'langfuse · object store',
      uptime: '14d 2h',
      cpu: 1,
      mem: 220,
      probe: 'ready ✓',
      img: 'minio/minio',
    },
    {
      name: 'langfuse-redis',
      container: 'poindexter-langfuse-redis',
      port: null,
      status: 'ok',
      metric: 'ready',
      sub: 'langfuse · cache',
      uptime: '14d 2h',
      cpu: 1,
      mem: 60,
      probe: 'ready ✓',
      img: 'redis:7',
    },
    // ── GlitchTip sidecars ───────────────────────────────────
    {
      name: 'glitchtip-worker',
      container: 'poindexter-glitchtip-worker',
      port: null,
      status: 'ok',
      metric: 'ready',
      sub: 'glitchtip · task worker',
      uptime: '14d 2h',
      cpu: 2,
      mem: 260,
      probe: 'ready ✓',
      img: 'glitchtip/glitchtip:4.1',
    },
    {
      name: 'glitchtip-db',
      container: 'poindexter-glitchtip-db',
      port: null,
      status: 'ok',
      metric: 'ready',
      sub: 'glitchtip · db sidecar',
      uptime: '14d 2h',
      cpu: 1,
      mem: 180,
      probe: 'ready ✓',
      img: 'postgres:16-alpine',
    },
    {
      name: 'glitchtip-redis',
      container: 'poindexter-glitchtip-redis',
      port: null,
      status: 'ok',
      metric: 'ready',
      sub: 'glitchtip · cache',
      uptime: '14d 2h',
      cpu: 1,
      mem: 60,
      probe: 'ready ✓',
      img: 'redis:7-alpine',
    },
    // ── Prefect sidecars ─────────────────────────────────────
    {
      name: 'prefect-services',
      container: 'poindexter-prefect-services',
      port: null,
      status: 'ok',
      metric: 'ready',
      sub: 'prefect · scheduler + services',
      uptime: '6d 4h',
      cpu: 2,
      mem: 220,
      probe: 'ready ✓',
      img: 'prefecthq/prefect:3',
    },
    {
      name: 'prefect-redis',
      container: 'poindexter-prefect-redis',
      port: null,
      status: 'ok',
      metric: 'ready',
      sub: 'prefect · messaging',
      uptime: '6d 4h',
      cpu: 1,
      mem: 60,
      probe: 'ready ✓',
      img: 'redis:7',
    },
    // ── Metrics exporters ────────────────────────────────────
    {
      name: 'postgres-exporter',
      container: 'poindexter-postgres-exporter',
      port: 9187,
      status: 'ok',
      metric: 'scrape ✓',
      sub: 'postgres metrics exporter',
      uptime: '21d 9h',
      cpu: 1,
      mem: 40,
      probe: 'ready ✓',
      img: 'prometheuscommunity/postgres-exporter:v0.19.1',
    },
    {
      name: 'promtail',
      container: 'poindexter-promtail',
      port: null,
      status: 'ok',
      metric: 'shipping',
      sub: 'log shipper → Loki',
      uptime: '21d 9h',
      cpu: 1,
      mem: 60,
      probe: 'ready ✓',
      img: 'grafana/promtail:3.3.2',
    },
    // ── Content pipeline internals ──────────────────────────
    {
      name: 'auto-embed',
      container: 'poindexter-auto-embed',
      port: null,
      status: 'ok',
      metric: 'sweeping',
      sub: 'embedding backfill loop',
      uptime: '21d 9h',
      cpu: 2,
      mem: 260,
      probe: 'ready ✓',
      img: 'glad-labs-website-auto-embed',
    },
  ];

  // ── GPU — RTX 5090 ──────────────────────────────────────────
  const gpu = {
    name: 'RTX 5090',
    driver: '560.94',
    util: 71,
    temp: 67,
    power: 412,
    powerMax: 575,
    vramUsed: 21.4,
    vramTotal: 32,
    fan: 58,
    clock: 2820,
    clockMax: 2950,
    procs: [
      { name: 'ollama (glm-4.7-50b)', vram: 14.2, util: 48 },
      { name: 'image-gen-server', vram: 6.1, util: 21 },
      { name: 'nomic-embed-text', vram: 1.1, util: 2 },
    ],
    utilHist: [
      62, 58, 71, 69, 74, 81, 77, 72, 68, 74, 79, 83, 76, 71, 69, 73, 77, 80,
      84, 79, 72, 68, 71, 74, 77, 71,
    ],
    tempHist: [
      61, 62, 64, 63, 65, 67, 68, 66, 64, 65, 67, 69, 68, 66, 65, 66, 68, 69,
      70, 68, 66, 65, 66, 67, 68, 67,
    ],
  };

  // ── Pipeline ────────────────────────────────────────────────
  const pipeline = {
    // Real canonical_blog graph_def blocks (36 nodes — see
    // services/canonical_blog_spec.py::CANONICAL_BLOG_GRAPH_DEF). Replaces the
    // deleted research→draft→edit→illustrate→review→publish flow (gone 2026-05-16).
    // `nodes` lets the live loader map a task's current `stage` → its block.
    stages: [
      { name: 'verify', nodes: ['verify_task'], count: 0, state: '' },
      {
        name: 'writer',
        nodes: [
          'generate_draft',
          'generate_title',
          'check_title_originality',
          'normalize_draft',
          'draft_gate',
          'writer_self_review',
          'resolve_internal_link_placeholders',
          'reconcile_citations',
          'quality_evaluation',
          'url_validation',
        ],
        count: 2,
        state: 'hot',
      },
      {
        name: 'image',
        nodes: [
          'plan_image_markers',
          'generate_images',
          'inject_images',
          'source_featured_image',
          'caption_images',
        ],
        count: 1,
        state: 'warn',
      },
      {
        name: 'qa',
        nodes: [
          'qa_programmatic',
          'qa_critic',
          'qa_deepeval',
          'qa_ragas',
          'qa_vision',
          'qa_topic_delivery',
          'qa_citations',
          'qa_unlinked_attribution',
          'qa_consistency',
          'qa_self_consistency',
          'qa_web_factcheck',
          'qa_aggregate',
        ],
        count: 1,
        state: 'hot',
      },
      {
        name: 'seo',
        nodes: [
          'seo_all_metadata',
          'generate_media_scripts',
          'generate_video_shot_list',
          'capture_training_data',
        ],
        count: 0,
        state: '',
      },
      {
        name: 'finalize',
        nodes: [
          'compile_meta',
          'persist_task',
          'record_pipeline_version',
          'evaluate_auto_publish',
        ],
        count: 0,
        state: '',
      },
    ],
    perDay: [
      4, 2, 6, 3, 5, 7, 3, 5, 8, 4, 6, 9, 5, 4, 7, 6, 8, 5, 7, 4, 3, 6, 9, 7, 5,
      8, 6, 4, 7, 5,
    ],
    successRate: 94,
    avgCompletion: '38m',
    tasks: [
      {
        id: 2241,
        topic: 'The Quiet Compounding of Local-First Infra',
        stage: 'qa_aggregate',
        status: 'ok',
        quality: 84,
        model: 'glm-4.7-50b',
        age: ago(12),
      },
      {
        id: 2240,
        topic: 'Why We Killed Our Observability Vendor',
        stage: 'qa_aggregate',
        status: 'ok',
        quality: 81,
        model: 'glm-4.7-50b',
        age: ago(34),
      },
      {
        id: 2238,
        topic: 'The Economics of Self-Hosted GPUs',
        stage: 'generate_images',
        status: 'fail',
        quality: null,
        model: 'glm-4.7-50b',
        age: ago(48),
      },
      {
        id: 2237,
        topic: 'A Field Guide to pgvector at Small Scale',
        stage: 'generate_draft',
        status: 'run',
        quality: null,
        model: 'glm-4.7-50b',
        age: ago(4),
      },
      {
        id: 2236,
        topic: 'Tailscale as the Only Network You Need',
        stage: 'normalize_draft',
        status: 'run',
        quality: null,
        model: 'glm-4.7-50b',
        age: ago(9),
      },
      {
        id: 2235,
        topic: 'Prometheus Recording Rules, Demystified',
        stage: 'generate_draft',
        status: 'run',
        quality: null,
        model: 'glm-4.7-50b',
        age: ago(2),
      },
      {
        id: 2234,
        topic: 'The 200-Line Supervisor Pattern',
        stage: 'published',
        status: 'ok',
        quality: 85,
        model: 'glm-4.7-50b',
        age: ago(380),
      },
      {
        id: 2233,
        topic: 'Cost Telemetry Without a SaaS Bill',
        stage: 'published',
        status: 'ok',
        quality: 82,
        model: 'glm-4.7-50b',
        age: ago(520),
      },
    ],
  };

  // ── Brain / embeddings ──────────────────────────────────────
  // Shape mirrors GET /api/memory/stats (see memory_dashboard_routes.py):
  //   totalEmbeddings ← total · bySource ← by_source_table · byWriter ← by_writer
  // Live mode replaces those from the real read, and `decisions` (+ the
  // decisions24h/7d counters) from GET /api/brain/stats. queueDepth / lastCycle
  // / growth / recent are brain-daemon internals (brain_queue) with NO HTTP
  // route — mock-only; live wipes them once at state init in app.jsx and
  // renders honest-empty (feedback_no_dummy_data). Corpus keys are the
  // real source_tables (posts/issues/audit/memory/brain/claude_sessions).
  const brain = {
    totalEmbeddings: 16932,
    model: 'nomic-embed-text',
    dim: 768,
    queueDepth: 6,
    lastCycle: ago(41),
    bySource: [
      ['posts', 8210],
      ['issues', 4120],
      ['audit', 2980],
      ['memory', 902],
      ['claude_sessions', 498],
      ['brain', 222],
    ],
    // By writer, with per-writer staleness (mirrors by_writer rows).
    byWriter: [
      { key: 'worker', count: 11240, age: 95, stale: false },
      { key: 'claude-code', count: 3010, age: 220, stale: false },
      { key: 'user', count: 1605, age: 5400, stale: false },
      { key: 'openclaw', count: 720, age: 28800, stale: true },
      { key: 'brain', count: 357, age: 410, stale: false },
    ],
    growth: [
      12, 8, 21, 15, 32, 18, 9, 24, 41, 17, 28, 53, 22, 19, 36, 44, 31, 27, 48,
      33, 19, 29, 57, 42, 38, 61, 45, 33, 52, 40,
    ],
    decisions: [
      {
        ts: '14:31:08',
        kind: 'observe',
        msg: 'embeddings_freshness → OK · 6 queued, last cycle 41s',
        tone: 'mint',
      },
      {
        ts: '14:28:44',
        kind: 'remediate',
        msg: 'restarted ollama after 3 consecutive ollama_models timeouts',
        tone: 'cyan',
      },
      {
        ts: '14:12:02',
        kind: 'observe',
        msg: 'infra.database_size → 17.3 GB (conf 1.0)',
        tone: 'mint',
      },
      {
        ts: '13:58:19',
        kind: 'alert',
        msg: 'operator_url_probe → Prefect UI unreachable',
        tone: 'amber',
      },
      {
        ts: '13:40:55',
        kind: 'observe',
        msg: 'quality.trend → 82.4 avg over 7d (conf 0.92)',
        tone: 'mint',
      },
    ],
    recent: [
      {
        src: 'issues',
        id: 612,
        chunk: 0,
        model: 'nomic-embed-text',
        preview: 'feat: auto-capture screenshots during Playwright runs',
        at: ago(41),
      },
      {
        src: 'posts',
        id: 79,
        chunk: 2,
        model: 'nomic-embed-text',
        preview: 'The 200-Line Supervisor Pattern — chunk 3 of 9',
        at: ago(44),
      },
      {
        src: 'issues',
        id: 611,
        chunk: 0,
        model: 'nomic-embed-text',
        preview: 'dream: always-on thought capture — ambient recording',
        at: ago(52),
      },
      {
        src: 'memory',
        id: 88,
        chunk: 1,
        model: 'nomic-embed-text',
        preview: 'decision_log — why we pick cost tiers over hardcoded models',
        at: ago(67),
      },
    ],
  };

  // ── Cost — the honest model ─────────────────────────────────
  // Infra is ~$0/mo (fully self-hosted). The real levers are LLM/API SPEND
  // against the monthly cap (live: GET /api/metrics/costs/budget) and local
  // ENERGY (kWh × rate). The old Cloudflare/B2 cloud-egress `byProvider` framing
  // overstated cloud cost and is dropped. `byModel`/`daily` have no HTTP route
  // yet, so live mode renders them as an explicit "backend read pending" empty
  // (never mocked — see app.jsx budget poll + CostPanel).
  const cost = {
    monthToDate: 11.42, // LLM/API spend MTD — live: budget.amount_spent
    budget: 50, // monthly cap — live: budget.monthly_budget
    projected: 28.6, // live: budget.projected_final_cost
    dailyBurn: 0.41, // live: budget.daily_burn_rate
    percentUsed: 22.8, // live: budget.percent_used
    status: 'healthy', // live: budget.status (healthy | warning)
    alerts: [], // live: budget.alerts
    // Infra is self-hosted → $0. The honest replacement for the cloud-egress mock.
    infraMonth: 0,
    infraNote:
      'fully self-hosted — local Docker · GPU · Postgres; only Vercel / R2 / Resend touch cloud',
    // Local energy: the real physical cost of running the box, from the
    // measured cost_logs electricity ledger (see cost_ledger.get_spend).
    electricityUsdMonth: 1.35, // ~= 9.5 kWh x $0.142/kWh, kept plausible
    electricitySource: 'measured',
    electricityCoveragePct: 98.3,
    // Scheduled-agent Anthropic API spend (Phase 6.2). Agents were paused
    // 2026-06-09 for cost control and bill to Anthropic at full rate from
    // 2026-06-15. No by-provider cost route yet → rolls into monthToDate.
    agentApiMonth: 0,
    agentApiNote:
      'scheduled agents paused 2026-06-09 · full Anthropic rate from 2026-06-15',
    daily: [
      0.2, 0.3, 0.4, 0.4, 0.5, 0.6, 0.6, 0.7, 0.8, 0.4, 0.5, 0.6, 0.6, 0.4, 0.7,
      0.6, 0.5, 0.5, 0.7, 0.4, 0.3, 0.6, 0.9, 0.7, 0.5, 0.8, 0.6, 0.4, 0.7, 0.5,
    ],
  };

  // ── Audit / event feed (live) ───────────────────────────────
  const auditSeed = [
    {
      ts: '14:31:52',
      tag: ['cyan', 'PIPELINE'],
      html: '<b>Task #2237</b> entered <span class="c-cyan">research</span> · topic "A Field Guide to pgvector"',
    },
    {
      ts: '14:31:08',
      tag: ['amber', 'PROBE'],
      html: '<span class="c-amber">embeddings_freshness</span> WARN — queue depth <b>24</b>, 41m behind',
    },
    {
      ts: '14:30:44',
      tag: ['mint', 'PUBLISH'],
      html: '<b>RUN 0078</b> <span class="c-mint">PUBLISHED</span> · "The 200-Line Supervisor Pattern" · Q=85',
    },
    {
      ts: '14:29:30',
      tag: ['cyan', 'BRAIN'],
      html: 'embedded <b>4</b> chunks from <span class="c-cyan">posts</span> · nomic-embed-text',
    },
    {
      ts: '14:28:44',
      tag: ['cyan', 'REMEDIATE'],
      html: 'brain restarted <b>ollama</b> after 3× ollama_models timeout',
    },
    {
      ts: '14:27:11',
      tag: ['red', 'TASK'],
      html: '<b>Task #2231</b> <span class="c-red">FAILED</span> · illustrate · httpx.ReadTimeout',
    },
    {
      ts: '14:25:02',
      tag: ['amber', 'DRIFT'],
      html: 'operator_url_probe — <b>Prefect UI</b> unreachable (100.64.1.5)',
    },
    {
      ts: '14:22:38',
      tag: ['mint', 'QUALITY'],
      html: 'Task #2240 scored <span class="c-mint">81</span> · awaiting approval',
    },
    {
      ts: '14:20:15',
      tag: ['cyan', 'GPU'],
      html: 'RTX 5090 util <b>71%</b> · 67°C · 412W · VRAM 21.4/32 GB',
    },
    {
      ts: '14:18:50',
      tag: ['cyan', 'CONFIG'],
      html: 'operator updated <span class="c-cyan">research.concurrency</span> 2 → 3',
    },
  ];

  // live-feed generators
  const liveTemplates = [
    () => ({
      tag: ['cyan', 'GPU'],
      html: `RTX 5090 util <b>${60 + Math.floor(Math.random() * 28)}%</b> · ${64 + Math.floor(Math.random() * 7)}°C · ${390 + Math.floor(Math.random() * 60)}W`,
    }),
    () => ({
      tag: ['cyan', 'BRAIN'],
      html: `embedded <b>${1 + Math.floor(Math.random() * 6)}</b> chunks · nomic-embed-text`,
    }),
    () => ({
      tag: ['mint', 'PROBE'],
      html: `<span class="c-mint">db_ping</span> ✓ · ${1 + Math.floor(Math.random() * 4)}ms`,
    }),
    () => ({
      tag: ['cyan', 'PIPELINE'],
      html: `scheduler tick · <b>${2 + Math.floor(Math.random() * 3)}</b> tasks in flight`,
    }),
    () => ({
      tag: ['mint', 'PROBE'],
      html: `<span class="c-mint">content_gen</span> ✓ · ollama 1.2s`,
    }),
    () => ({
      tag: ['cyan', 'TRACE'],
      html: `tempo · brain.probe cycle <b>${(3.8 + Math.random() * 1.2).toFixed(1)}s</b>`,
    }),
  ];

  // ── Revenue (Lemon Squeezy) ─────────────────────────────────
  // Pre-revenue: the Lemon Squeezy store + Pro subscription are built but
  // billing is GATED (project_monetization), so there are zero real orders.
  // The console shows an honest $0 / "billing not live yet" until the revenue
  // engine records a first sale — never a fabricated figure (feedback_no_dummy_data).
  // `live:false` drives every revenue surface (panel KPI, drawer detail, mode
  // stat) to its empty state; flip to a real read when a /api/revenue route
  // lands and the store opens.
  const revenue = {
    live: false,
    today: 0,
    month: 0,
    net: 0,
    prevMonth: 0,
    orders: 0,
    subscriptions: 0,
    refunds: 0,
    mrr: 0,
    byType: [],
    bySource: [],
    daily: [],
    topPosts: [],
    recent: [],
  };

  // ── Media pipeline — Stage 2 (video + podcast) ──────────────
  const media = {
    renderSuccess24h: 92,
    gate2Pending: 3,
    dispatched: 41,
    videosPersisted: 128,
    queue: [
      {
        id: 'vid-318',
        medium: 'video',
        title: 'The 200-Line Supervisor Pattern',
        quality: 86,
        dur: '4:12',
        shots: 9,
        age: ago(22),
      },
      {
        id: 'pod-204',
        medium: 'podcast',
        title: 'Why We Killed Our Observability Vendor',
        quality: 80,
        dur: '11:38',
        shots: null,
        age: ago(51),
      },
      {
        id: 'vid-317',
        medium: 'video',
        title: 'Tailscale as the Only Network You Need',
        quality: 78,
        dur: '3:48',
        shots: 7,
        age: ago(96),
      },
    ],
    byMedium: [
      ['video', 22],
      ['podcast', 14],
      ['short', 5],
    ],
  };

  // ── QA rails — rejections & hallucination guardrails ────────
  const qa = {
    rejectionRate: 12,
    passRate: 88,
    ragFallback: 4,
    // The real multi-model QA rails (modules/content/atoms/qa_*.py → qa.aggregate).
    // Hard gates block publish; advisory rails score but don't block. Graduate an
    // advisory rail via qa_gates.<rail>.required_to_pass=true (project_qa_rails_state).
    rails: [
      { rail: 'qa.programmatic', gate: 'hard' },
      { rail: 'qa.critic', gate: 'hard' },
      { rail: 'qa.deepeval', gate: 'advisory' },
      { rail: 'qa.ragas', gate: 'advisory' },
      { rail: 'qa.vision', gate: 'advisory' },
      { rail: 'qa.topic_delivery', gate: 'advisory' },
      { rail: 'qa.citations', gate: 'advisory' },
      { rail: 'qa.unlinked_attribution', gate: 'advisory' },
      { rail: 'qa.consistency', gate: 'advisory' },
      { rail: 'qa.self_consistency', gate: 'advisory' },
      { rail: 'qa.content_originality', gate: 'advisory' },
      { rail: 'qa.title_coherence', gate: 'advisory' },
      { rail: 'qa.web_factcheck', gate: 'advisory' },
    ],
    reasons: [
      ['Off-brand voice', 7],
      ['Thin evidence', 5],
      ['SEO weak', 4],
      ['Structure', 3],
      ['Factual risk', 2],
    ],
    // Real programmatic anti-hallucination rule families (content_validator.py).
    hallucination: [
      { rule: 'fake_stat', rate: 0.8, tone: 'amber' },
      { rule: 'hallucinated_reference', rate: 0.3, tone: 'red' },
      { rule: 'json_envelope_leak', rate: 0.2, tone: 'amber' },
      { rule: 'fake_quote', rate: 0.1, tone: 'amber' },
    ],
    byModel: [
      { model: 'glm-4.7-50b', appr: 89, tasks: 147 },
      { model: 'gemma3:27b', appr: 81, tasks: 38 },
      { model: 'qwen3:8b', appr: 74, tasks: 22 },
    ],
  };

  // ── Cost by model (extends cost) ────────────────────────────
  cost.byModel = [
    { model: 'glm-4.7-50b', calls: 1840, tokens: '4.2M', kwh: 6.1, share: 62 },
    { model: 'gemma3:27b', calls: 612, tokens: '1.1M', kwh: 1.8, share: 22 },
    { model: 'qwen3:8b', calls: 980, tokens: '0.6M', kwh: 0.7, share: 12 },
    { model: 'sdxl-lightning', calls: 128, tokens: '—', kwh: 0.9, share: 4 },
  ];

  // ── Container restarts (24h) — cAdvisor signal ──────────────
  const restarts = [
    { name: 'ollama', count: 3, last: ago(64), tone: 'amber' },
    { name: 'prefect-server', count: 5, last: ago(73), tone: 'red' },
    { name: 'image-gen-server', count: 1, last: ago(220), tone: 'mint' },
  ];

  // ── Sub-tool launcher (Mission Control link registry) ───────
  // Same-host, different-port: the operator reaches the console AND these tools
  // at the same hostname on different ports, so each URL is built from the
  // CURRENT host + the compose port. That keeps the links working from every
  // access context (localhost on the machine, or a private-network IP / DNS name)
  // instead of a dead literal localhost. (Ports are the stack's compose defaults;
  // an operator who remaps them or fronts a tool with a reverse proxy would
  // override these — a future app_settings-backed launcher registry.)
  const launcher = ((h) => {
    const grafana = `http://${h}:3000`;
    // Loki (:3100) and Tempo (:3200) are storage backends with no browsable UI of
    // their own, and Kuma's own :3002 UI serves an empty reply. All three are
    // surfaced in Grafana's merged Observability board (rows: Logs/Loki,
    // Tracing/Tempo, Uptime/Kuma), which renders for an anonymous Viewer. Grafana
    // Explore is NOT usable here -- it redirects anonymous Viewers to sign-in.
    const observability = `${grafana}/d/observability-merged/observability`;
    return [
      { name: 'Prefect', sub: 'orchestration', url: `http://${h}:4200` },
      { name: 'Langfuse', sub: 'LLM traces', url: `http://${h}:3010` },
      { name: 'GlitchTip', sub: 'errors', url: `http://${h}:8080` },
      { name: 'Grafana', sub: 'deep metrics', url: grafana },
      { name: 'Pyroscope', sub: 'profiling', url: `http://${h}:4040` },
      { name: 'pgAdmin', sub: 'database', url: `http://${h}:18443` },
      { name: 'Prometheus', sub: 'metrics', url: `http://${h}:9091` },
      { name: 'Tempo', sub: 'tracing (Grafana)', url: observability },
      { name: 'Loki', sub: 'logs (Grafana)', url: observability },
      { name: 'Uptime Kuma', sub: 'uptime (Grafana)', url: observability },
    ];
  })(
    (typeof window !== 'undefined' &&
      window.location &&
      window.location.hostname) ||
      'localhost'
  );

  // media items injected into the Action Inbox (human-gated)
  const mediaInbox = [
    {
      id: 'media-318',
      kind: 'media',
      priority: 2,
      title: 'Video ready — The 200-Line Supervisor Pattern',
      sub: [
        ['MEDIUM', 'video'],
        ['QUALITY', '86'],
        ['DUR', '4:12'],
      ],
      age: ago(22),
      tags: [['mint', 'RENDERED']],
      detail: {
        post_id: 'post-318',
        medium: 'video',
        title: 'The 200-Line Supervisor Pattern',
        quality: 86,
        dur: '4:12',
        shots: 9,
        stage: 'gate_2_review',
      },
    },
  ];

  // ── Topic discovery batches (operator triage) ──────────────
  // Mirrors GET /api/topics/proposals exactly: open batches per niche, each
  // with its ranked candidate list. The operator picks a winner (rank #1),
  // then resolves (→ content pipeline) or rejects (→ fresh sweep).
  // operator_rank is null until the operator ranks; rank_in_batch is the
  // system pre-rank. A stuck open batch = content goes dark, so this is the
  // surface to drain it. (Brand niches: AI/ML, gaming, PC hardware.)
  // Canonical offset envelope (poindexter#745): {items, total, limit, offset}.
  const topics = {
    total: 2,
    limit: 2,
    offset: 0,
    items: [
      {
        batch_id: 'b1a2c3d4-aiml-open-batch',
        niche_id: 'niche-aiml',
        niche_slug: 'ai-ml',
        niche_name: 'AI / ML',
        status: 'open',
        candidate_count: 4,
        candidates: [
          {
            id: 'cand-aiml-1',
            kind: 'external',
            title: 'Speculative decoding is quietly eating inference costs',
            summary: 'Why draft-model speculative decoding is the cheapest 2x',
            score: 88.4,
            effective_score: 88.4,
            rank_in_batch: 1,
            operator_rank: null,
            operator_edited_topic: null,
            operator_edited_angle: null,
          },
          {
            id: 'cand-aiml-2',
            kind: 'internal',
            title: 'What we learned running a 70B writer on one 5090',
            summary: 'VRAM math, quant trade-offs, and where it broke',
            score: 81.0,
            effective_score: 81.0,
            rank_in_batch: 2,
            operator_rank: null,
            operator_edited_topic: null,
            operator_edited_angle: null,
          },
          {
            id: 'cand-aiml-3',
            kind: 'external',
            title: 'The case against agent frameworks for solo builders',
            summary: 'When a graph engine is overkill and a for-loop wins',
            score: 74.6,
            effective_score: 74.6,
            rank_in_batch: 3,
            operator_rank: null,
            operator_edited_topic: null,
            operator_edited_angle: null,
          },
          {
            id: 'cand-aiml-4',
            kind: 'external',
            title: 'pgvector vs a dedicated vector DB at 100k rows',
            summary: 'Latency and recall benchmarks at small scale',
            score: 69.2,
            effective_score: 69.2,
            rank_in_batch: 4,
            operator_rank: null,
            operator_edited_topic: null,
            operator_edited_angle: null,
          },
        ],
      },
      {
        batch_id: 'b5e6f7a8-hw-open-batch',
        niche_id: 'niche-hw',
        niche_slug: 'pc-hardware',
        niche_name: 'PC Hardware',
        status: 'open',
        candidate_count: 3,
        candidates: [
          {
            id: 'cand-hw-1',
            kind: 'external',
            title: 'The 5090 power-spike problem nobody warns you about',
            summary: 'Transient loads, PSU headroom, and HX1500i logs',
            score: 84.1,
            effective_score: 84.1,
            rank_in_batch: 1,
            operator_rank: null,
            operator_edited_topic: null,
            operator_edited_angle: null,
          },
          {
            id: 'cand-hw-2',
            kind: 'external',
            title: 'Undervolting a 5090: a repeatable curve',
            summary: 'Holds clocks at roughly 60W less draw',
            score: 78.5,
            effective_score: 78.5,
            rank_in_batch: 2,
            operator_rank: null,
            operator_edited_topic: null,
            operator_edited_angle: null,
          },
          {
            id: 'cand-hw-3',
            kind: 'internal',
            title: 'Our local-first GPU scheduler, one year in',
            summary: 'Time-sharing one card across image-gen and Ollama',
            score: 72.3,
            effective_score: 72.3,
            rank_in_batch: 3,
            operator_rank: null,
            operator_edited_topic: null,
            operator_edited_angle: null,
          },
        ],
      },
    ],
  };

  // ── Findings — probe-routing triage (#461) ──────────────────
  // Mirrors GET /api/findings exactly: emitted/pending counts, by-kind /
  // by-severity rollups, the kind→delivery policy, and the latest finding rows
  // with routed/PENDING/log-only status. Counts cover the window; the detail
  // list is the latest slice (so emitted > rows is expected, as in the route).
  // status: routed (id≤watermark) · PENDING (routable, above watermark) ·
  // log-only (info severity, never paged). Brand niches: AI/ML, gaming, PC HW.
  const findings = {
    counts: { emitted: 9, pending: 2 },
    watermark: 18432,
    hours: 168,
    by_severity: [
      { severity: 'warn', count: 5 },
      { severity: 'info', count: 3 },
      { severity: 'critical', count: 1 },
    ],
    by_kind: [
      { kind: 'broken_external_link', count: 3 },
      { kind: 'quality_regression', count: 2 },
      { kind: 'embeddings_freshness', count: 2 },
      { kind: 'media_drift', count: 1 },
      { kind: 'cadence_slo', count: 1 },
    ],
    delivery_by_kind: {
      broken_external_link: 'github_issue',
      quality_regression: 'discord',
      media_drift: 'discord',
      cadence_slo: 'log_only',
    },
    findings: [
      {
        id: 18440,
        timestamp: '2026-06-08T13:58:00',
        source: 'audit_published_quality',
        severity: 'critical',
        kind: 'quality_regression',
        title: 'Avg quality fell to 76 over the trailing 5 posts',
        status: 'PENDING',
        delivery: 'discord',
      },
      {
        id: 18439,
        timestamp: '2026-06-08T13:51:00',
        source: 'brain.probe.embeddings_freshness',
        severity: 'warn',
        kind: 'embeddings_freshness',
        title: 'Semantic memory 41m behind (queue depth 24)',
        status: 'PENDING',
        delivery: 'route',
      },
      {
        id: 18431,
        timestamp: '2026-06-08T11:20:00',
        source: 'audit_external_links',
        severity: 'warn',
        kind: 'broken_external_link',
        title: '2 dead links in "Tailscale as the Only Network You Need"',
        status: 'routed',
        delivery: 'github_issue',
      },
      {
        id: 18402,
        timestamp: '2026-06-08T09:04:00',
        source: 'audit_media',
        severity: 'warn',
        kind: 'media_drift',
        title: 'Video render 18% slower week-over-week',
        status: 'routed',
        delivery: 'discord',
      },
      {
        id: 18377,
        timestamp: '2026-06-07T22:40:00',
        source: 'audit_published_quality',
        severity: 'info',
        kind: 'note',
        title: 'SEO score steady at 84 across the last 10 posts',
        status: 'log-only',
        delivery: 'route',
      },
      {
        id: 18361,
        timestamp: '2026-06-07T19:12:00',
        source: 'brain.probe.cadence_slo',
        severity: 'info',
        kind: 'cadence_slo',
        title: 'Publish cadence 0.8/day vs 1.0/day target (7d)',
        status: 'log-only',
        delivery: 'log_only',
      },
    ],
  };

  // ── Scheduled-publish queue ─────────────────────────────────
  // Mirrors GET /api/scheduling: posts with status='scheduled' + a future
  // published_at slot. The panel DERIVES depth / next-slot / past-due /
  // upcoming-24h from published_at (calculated, not stored). published_at is an
  // ISO string in both mock + live so the derivation is identical.
  const _sch = (h) => new Date(Date.now() + h * 3600000).toISOString();
  const schedule = {
    count: 5,
    rows: [
      // one past-due slot (scheduled_publisher hasn't fired yet) — operator flag
      {
        post_id: 'p-512',
        slug: 'gpu-scheduler-one-year',
        title: 'Our local-first GPU scheduler, one year in',
        published_at: _sch(-0.5),
        status: 'scheduled',
      },
      {
        post_id: 'p-514',
        slug: 'why-we-killed-observability-vendor',
        title: 'Why We Killed Our Observability Vendor',
        published_at: _sch(2),
        status: 'scheduled',
      },
      {
        post_id: 'p-517',
        slug: 'tailscale-only-network',
        title: 'Tailscale as the Only Network You Need',
        published_at: _sch(8),
        status: 'scheduled',
      },
      {
        post_id: 'p-520',
        slug: 'supervisor-pattern',
        title: 'The 200-Line Supervisor Pattern',
        published_at: _sch(26),
        status: 'scheduled',
      },
      {
        post_id: 'p-523',
        slug: 'cost-tiers-over-models',
        title: 'Cost Tiers Beat Hardcoded Model Names',
        published_at: _sch(50),
        status: 'scheduled',
      },
    ],
  };

  // ── SEO refresh pipeline ────────────────────────────────────
  // Mirrors GET /api/seo (seo_opportunities). queue = open+queued by gap_score;
  // refreshes = acted-on rows with a baseline→outcome SERP-position delta
  // (positive = moved up). Read-only — the seo.refresh loop runs autonomously,
  // the panel only observes. Timestamps are ISO (matching the live read).
  const _ago = (h) => new Date(Date.now() - h * 3600000).toISOString();
  const seo = {
    queue: [
      {
        id: 'o1',
        slug: 'best-budget-gpu-2026',
        target_query: 'best budget gpu 2026',
        tier: 'quick_win',
        status: 'open',
        position: 8.4,
        impressions: 2100,
        gap_score: 142.0,
        detected_at: _ago(1),
      },
      {
        id: 'o2',
        slug: 'local-llm-vram-guide',
        target_query: 'llm vram requirements',
        tier: 'quick_win',
        status: 'queued',
        position: 11.2,
        impressions: 1680,
        gap_score: 98.5,
        detected_at: _ago(2),
      },
      {
        id: 'o3',
        slug: 'tailscale-funnel-vs-serve',
        target_query: 'tailscale funnel vs serve',
        tier: 'high_value',
        status: 'open',
        position: 14.0,
        impressions: 1340,
        gap_score: 76.0,
        detected_at: _ago(3),
      },
      {
        id: 'o4',
        slug: 'sdxl-on-5090',
        target_query: 'sdxl rtx 5090',
        tier: 'long_tail',
        status: 'open',
        position: 19.5,
        impressions: 540,
        gap_score: 31.0,
        detected_at: _ago(5),
      },
    ],
    refreshes: [
      {
        id: 'r1',
        slug: 'supervisor-pattern',
        target_query: '200 line supervisor',
        tier: 'high_value',
        status: 'refreshed',
        baseline_position: 12.0,
        outcome_position: 6.0,
        delta: 6.0,
        measured_at: _ago(24),
      },
      {
        id: 'r2',
        slug: 'observability-vendor',
        target_query: 'self-hosted observability',
        tier: 'quick_win',
        status: 'refreshed',
        baseline_position: 9.0,
        outcome_position: 7.5,
        delta: 1.5,
        measured_at: _ago(48),
      },
      {
        id: 'r3',
        slug: 'gpu-scheduler',
        target_query: 'gpu scheduler ollama sdxl',
        tier: 'long_tail',
        status: 'refreshed',
        baseline_position: 15.0,
        outcome_position: 16.0,
        delta: -1.0,
        measured_at: _ago(72),
      },
    ],
    by_status: [
      { status: 'open', count: 18 },
      { status: 'queued', count: 4 },
      { status: 'refreshed', count: 11 },
      { status: 'dismissed', count: 3 },
    ],
    by_tier: [
      { tier: 'quick_win', count: 14 },
      { tier: 'high_value', count: 12 },
      { tier: 'long_tail', count: 10 },
    ],
  };

  // Mock telemetry — only used in mock mode (live polls /api/logs + /api/traces).
  const logs = {
    lines: [
      {
        ts: '2026-06-27T14:31:55Z',
        service: 'poindexter-worker',
        level: 'info',
        line: 'claimed pipeline_task #1842',
      },
      {
        ts: '2026-06-27T14:31:50Z',
        service: 'poindexter-worker',
        level: 'error',
        line: 'qa.aggregate rejected — fabrication veto',
      },
      {
        ts: '2026-06-27T14:31:40Z',
        service: 'poindexter-brain',
        level: 'info',
        line: 'health cycle ok (5m)',
      },
    ],
    stats: { count: 3, query: '{service=~".+"}' },
  };
  const traces = {
    traces: [
      {
        id: 'demo-1',
        name: 'qa_pass',
        model: 'gemma-4-31b',
        latency_ms: 2480,
        cost_usd: 0,
        qa_score: 87,
        task_id: '1842',
        timestamp: '2026-06-27T14:31:00Z',
        web_url: '',
      },
      {
        id: 'demo-2',
        name: 'writer_draft',
        model: 'gemma-4-31b',
        latency_ms: 18120,
        cost_usd: 0,
        qa_score: null,
        task_id: '1842',
        timestamp: '2026-06-27T14:29:00Z',
        web_url: '',
      },
    ],
    stats: { count: 2 },
  };

  // ── Task-Trace seeds (console board + per-task deep-dive) ────
  // Authentic to services/trace_read.py shapes so the port renders a real
  // example offline. MOCK-mode demo data ONLY — live mode reads /api/trace/*
  // and never fabricates (feedback_no_dummy_data). The detailed run is a
  // REJECTED canonical_blog run: it exercises every deep-dive section (full
  // spine, a hard-gate halt, per-rail QA verdicts, a cost rollup, open-on-halt)
  // — the richest thing an operator opens the deep-dive to see.
  const _tIso = (minsAgo) =>
    new Date(now.getTime() - minsAgo * 60000).toISOString();
  const traceActive = {
    runs: [
      {
        task_id: 'task-4412',
        topic: 'Why Self-Hosted LLMs Beat API Costs at Scale',
        template_slug: 'canonical_blog',
        niche_slug: 'ai-ml',
        status: 'in_progress',
        stage: 'qa.critic',
        percentage: 68,
        message: 'QA — adversarial critic reviewing the draft',
        started_at: _tIso(3),
        last_progress_at: _tIso(0),
        nodes_done: 21,
      },
      {
        task_id: 'task-4410',
        topic: 'RTX 5090 vs 4090: Real-World Inference Benchmarks',
        template_slug: 'canonical_blog',
        niche_slug: 'pc-hardware',
        status: 'awaiting_gate',
        stage: 'draft_gate',
        percentage: 34,
        message: 'Draft gate — awaiting operator approval',
        started_at: _tIso(9),
        last_progress_at: _tIso(2),
        nodes_done: 9,
      },
    ],
    recent: [
      {
        task_id: 'task-4409',
        topic: 'The Quiet Death of the Gaming Motherboard',
        template_slug: 'canonical_blog',
        status: 'awaiting_approval',
        started_at: _tIso(22),
        completed_at: _tIso(14),
      },
      {
        task_id: 'task-4408',
        topic: 'This Week in Local AI (podcast)',
        template_slug: 'podcast_pipeline',
        status: 'published',
        started_at: _tIso(41),
        completed_at: _tIso(33),
      },
      {
        task_id: 'task-4405',
        topic: 'Benchmarking Ollama Concurrency Limits',
        template_slug: 'canonical_blog',
        status: 'rejected',
        started_at: _tIso(58),
        completed_at: _tIso(49),
      },
    ],
  };
  const traceSummary = {
    window_hours: 24,
    tasks: 37,
    by_status: {
      published: 8,
      approved: 4,
      awaiting_approval: 3,
      in_progress: 2,
      awaiting_gate: 1,
      rejected: 5,
      failed: 2,
      cancelled: 1,
    },
    pass_rate: 0.706,
    avg_quality: 82.4,
    avg_cost_usd: 0.1874,
  };
  // Compact node factory — keeps the 28-node spine readable.
  const _tn = (
    seq,
    atom,
    node_id,
    status,
    tier,
    model,
    latency_ms,
    cost,
    output_keys,
    output_preview
  ) => ({
    seq,
    atom,
    node_id,
    status,
    tier,
    model,
    latency_ms,
    cost,
    output_keys,
    output_preview,
  });
  const traceDetail = {
    task_id: 'task-4405',
    run_id: 'task-4405',
    runs: [
      {
        run_id: 'task-4405',
        template_slug: 'canonical_blog',
        kind: 'content',
        status: 'halted',
        node_count: 28,
        halted_at: 28,
      },
      {
        run_id: 'task-4405-r1',
        template_slug: 'canonical_blog',
        kind: 'retry',
        status: 'halted',
        node_count: 24,
        halted_at: 22,
      },
    ],
    task: {
      task_id: 'task-4405',
      topic: 'Benchmarking Ollama Concurrency Limits',
      status: 'rejected',
      stage: 'qa.aggregate',
      template_slug: 'canonical_blog',
      niche_slug: 'ai-ml',
      percentage: 100,
      message: 'QA aggregate — rejected (critic veto)',
      created_at: _tIso(58),
      started_at: _tIso(58),
      completed_at: _tIso(49),
      last_progress_at: _tIso(49),
    },
    nodes: [
      _tn(
        1,
        'stage.verify_task',
        'verify_task',
        'ok',
        'free',
        'rule-based',
        40,
        0,
        ['task_verified'],
        '{"task_verified": true}'
      ),
      _tn(
        2,
        'content.generate_draft',
        'content.generate_draft',
        'ok',
        'premium',
        'claude-sonnet-5',
        48210,
        0.1712,
        ['draft', 'content'],
        '{"content": "Ollama\'s default OLLAMA_NUM_PARALLEL of 1 means concurrent requests queue rather than run in parallel…"}'
      ),
      _tn(
        3,
        'content.generate_title',
        'content.generate_title',
        'ok',
        'standard',
        'gemma-4-31b',
        3110,
        0,
        ['title'],
        '{"title": "How Many Requests Can Ollama Really Handle at Once?"}'
      ),
      _tn(
        4,
        'content.check_title_originality',
        'content.check_title_originality',
        'ok',
        'free',
        'rule-based',
        220,
        0,
        ['title_ok'],
        '{"title_ok": true, "nearest": 0.34}'
      ),
      _tn(
        5,
        'content.normalize_draft',
        'content.normalize_draft',
        'ok',
        'free',
        'rule-based',
        90,
        0,
        ['content'],
        '{"headings_fixed": 2}'
      ),
      _tn(
        6,
        'atoms.approval_gate',
        'draft_gate',
        'ok',
        'free',
        'gate',
        15,
        0,
        ['gate'],
        '{"gate": "disabled", "auto": true}'
      ),
      _tn(
        7,
        'stage.writer_self_review',
        'writer_self_review',
        'ok',
        'standard',
        'glm-4-6',
        8420,
        0,
        ['self_review'],
        '{"revised": true, "notes": "tightened intro, cut 1 hedge"}'
      ),
      _tn(
        8,
        'stage.resolve_internal_link_placeholders',
        'resolve_internal_link_placeholders',
        'ok',
        'free',
        'rule-based',
        260,
        0,
        ['content'],
        '{"links_resolved": 3}'
      ),
      _tn(
        9,
        'content.reconcile_citations',
        'content.reconcile_citations',
        'ok',
        'free',
        'rule-based',
        180,
        0,
        ['content'],
        '{"citations_relinked": 1}'
      ),
      _tn(
        10,
        'stage.quality_evaluation',
        'quality_evaluation',
        'ok',
        'free',
        'rule-based',
        320,
        0,
        ['quality_score'],
        '{"quality_score": 71}'
      ),
      _tn(
        11,
        'stage.url_validation',
        'url_validation',
        'ok',
        'free',
        'rule-based',
        1420,
        0,
        ['urls_ok'],
        '{"checked": 6, "dead": 0}'
      ),
      _tn(
        12,
        'content.plan_image_markers',
        'content.plan_image_markers',
        'ok',
        'standard',
        'gemma-4-31b',
        2610,
        0,
        ['image_markers'],
        '{"markers": 2}'
      ),
      _tn(
        13,
        'content.generate_images',
        'content.generate_images',
        'ok',
        'image',
        'z-image-turbo',
        9240,
        0,
        ['images'],
        '{"generated": 2}'
      ),
      _tn(
        14,
        'content.inject_images',
        'content.inject_images',
        'ok',
        'free',
        'rule-based',
        60,
        0,
        ['content'],
        '{"injected": 2}'
      ),
      _tn(
        15,
        'stage.source_featured_image',
        'source_featured_image',
        'ok',
        'free',
        'rule-based',
        70,
        0,
        ['featured_image'],
        '{"source": "generated[0]"}'
      ),
      _tn(
        16,
        'stage.caption_images',
        'caption_images',
        'ok',
        'vision',
        'qwen2.5-vl-7b',
        4110,
        0,
        ['captions'],
        '{"captioned": 2}'
      ),
      _tn(
        17,
        'qa.programmatic',
        'qa.programmatic',
        'ok',
        'free',
        'rule-based',
        210,
        0,
        ['qa_rail'],
        '{"approved": true, "issues": 0}'
      ),
      _tn(
        18,
        'qa.critic',
        'qa.critic',
        'ok',
        'standard',
        'glm-4-6',
        9870,
        0,
        ['qa_rail'],
        '{"approved": false, "score": 58, "veto": "Section 3 asserts a \'~4x faster\' latency figure with no benchmark or source."}'
      ),
      _tn(
        19,
        'qa.deepeval',
        'qa.deepeval',
        'ok',
        'standard',
        'llama-3.1-8b',
        5320,
        0,
        ['qa_rail'],
        '{"faithfulness": 0.81, "advisory": true}'
      ),
      _tn(
        20,
        'qa.ragas',
        'qa.ragas',
        'ok',
        'standard',
        'llama-3.1-8b',
        6110,
        0,
        ['qa_rail'],
        '{"score": 0.77, "advisory": true}'
      ),
      _tn(
        21,
        'qa.vision',
        'qa.vision',
        'ok',
        'vision',
        'qwen2.5-vl-7b',
        3980,
        0,
        ['qa_rail'],
        '{"approved": true, "images_ok": 2}'
      ),
      _tn(
        22,
        'qa.topic_delivery',
        'qa.topic_delivery',
        'ok',
        'standard',
        'gemma-4-31b',
        4210,
        0,
        ['qa_rail'],
        '{"approved": true, "covers_topic": true}'
      ),
      _tn(
        23,
        'qa.citations',
        'qa.citations',
        'ok',
        'free',
        'rule-based',
        150,
        0,
        ['qa_rail'],
        '{"approved": true, "citations": 4}'
      ),
      _tn(
        24,
        'qa.unlinked_attribution',
        'qa.unlinked_attribution',
        'ok',
        'free',
        'rule-based',
        130,
        0,
        ['qa_rail'],
        '{"advisory": true, "unlinked": 1}'
      ),
      _tn(
        25,
        'qa.consistency',
        'qa.consistency',
        'ok',
        'standard',
        'gemma-4-31b',
        3720,
        0,
        ['qa_rail'],
        '{"approved": true}'
      ),
      _tn(
        26,
        'qa.self_consistency',
        'qa.self_consistency',
        'ok',
        'standard',
        'gemma-4-31b',
        4050,
        0,
        ['qa_rail'],
        '{"advisory": true, "variance": 0.12}'
      ),
      _tn(
        27,
        'qa.web_factcheck',
        'qa.web_factcheck',
        'ok',
        'standard',
        'gemma-4-31b',
        7640,
        0,
        ['qa_rail'],
        '{"checked": 3, "unsupported": 1}'
      ),
      _tn(
        28,
        'qa.aggregate',
        'qa.aggregate',
        'halted',
        'free',
        'rule-based',
        40,
        0,
        ['qa_decision'],
        '{"approved": false, "reason": "critic veto (score 58 < 70) + 1 unsupported web claim", "failed": ["qa.critic"]}'
      ),
    ],
    corpus:
      '- Ollama concurrency docs (OLLAMA_NUM_PARALLEL, OLLAMA_MAX_LOADED_MODELS) → https://github.com/ollama/ollama/blob/main/docs/faq.md\n' +
      '- vLLM vs Ollama throughput benchmark (r/LocalLLaMA, 2026-04) → https://reddit.com/r/LocalLLaMA/\n' +
      '- Poindexter internal: single-GPU VRAM budget note (num_ctx defaults, flash-attn KV) → memory://single_gpu_vram_budget',
    decisions: [],
    qa: [
      {
        seq: 17,
        atom: 'qa.programmatic',
        status: 'ok',
        preview: '{"approved": true, "issues": 0}',
      },
      {
        seq: 18,
        atom: 'qa.critic',
        status: 'ok',
        preview:
          '{"approved": false, "score": 58, "veto": "unbenchmarked latency claim"}',
      },
      {
        seq: 19,
        atom: 'qa.deepeval',
        status: 'ok',
        preview: '{"faithfulness": 0.81, "advisory": true}',
      },
      {
        seq: 20,
        atom: 'qa.ragas',
        status: 'ok',
        preview: '{"score": 0.77, "advisory": true}',
      },
      {
        seq: 21,
        atom: 'qa.vision',
        status: 'ok',
        preview: '{"approved": true}',
      },
      {
        seq: 22,
        atom: 'qa.topic_delivery',
        status: 'ok',
        preview: '{"approved": true}',
      },
      {
        seq: 23,
        atom: 'qa.citations',
        status: 'ok',
        preview: '{"approved": true}',
      },
      {
        seq: 24,
        atom: 'qa.unlinked_attribution',
        status: 'ok',
        preview: '{"advisory": true}',
      },
      {
        seq: 25,
        atom: 'qa.consistency',
        status: 'ok',
        preview: '{"approved": true}',
      },
      {
        seq: 26,
        atom: 'qa.self_consistency',
        status: 'ok',
        preview: '{"advisory": true}',
      },
      {
        seq: 27,
        atom: 'qa.web_factcheck',
        status: 'ok',
        preview: '{"unsupported": 1}',
      },
      {
        seq: 28,
        atom: 'qa.aggregate',
        status: 'halted',
        preview: '{"approved": false, "reason": "critic veto"}',
      },
    ],
    cost_rollup: {
      by_model: [
        {
          model: 'claude-sonnet-5',
          provider: 'anthropic',
          usd: 0.1712,
          tokens: 12300,
          calls: 1,
        },
        {
          model: 'gemma-4-31b',
          provider: 'ollama',
          usd: 0,
          tokens: 14240,
          calls: 6,
        },
        {
          model: 'glm-4-6',
          provider: 'ollama',
          usd: 0,
          tokens: 9110,
          calls: 2,
        },
        {
          model: 'llama-3.1-8b',
          provider: 'ollama',
          usd: 0,
          tokens: 5400,
          calls: 2,
        },
        {
          model: 'qwen2.5-vl-7b',
          provider: 'ollama',
          usd: 0,
          tokens: 2400,
          calls: 2,
        },
      ],
      total_usd: 0.1712,
    },
    final: {
      title: 'How Many Requests Can Ollama Really Handle at Once?',
      quality_score: 58,
      content_length: 6820,
    },
    halt: {
      seq: 28,
      atom: 'qa.aggregate',
      reason:
        '{"approved": false, "reason": "critic veto (score 58 < 70) + 1 unsupported web claim"}',
    },
    langfuse: { session_id: 'task-4405', run_id: 'task-4405' },
  };

  // ── NOW RUNNING — mock in-flight media renders (design 1a band) ──
  // Live has NO render-progress route yet; the band shows an honest empty
  // with the gate-2 pending count instead. These mock rows exercise the UI.
  const mediaRendering = [
    {
      id: 'vid-318',
      medium: 'video',
      title: 'The 200-Line Supervisor Pattern',
      step: 'shot 6 / 9',
      pct: 67,
      age: ago(22),
      eta: '~2m',
    },
    {
      id: 'vid-317',
      medium: 'video',
      title: 'Tailscale as the Only Network You Need',
      step: 'shot 3 / 7',
      pct: 43,
      age: ago(8),
      eta: '~5m',
    },
    {
      id: 'pod-204',
      medium: 'podcast',
      title: 'Why We Killed Our Observability Vendor',
      step: 'mixing audio',
      pct: 55,
      age: ago(51),
      eta: '—',
    },
  ];

  // Live-activity pulse mock (NOW RUNNING band). started_at/finished_at use REAL
  // Date.now() (not the frozen mock `now`) because the band computes elapsed
  // client-side against Date.now() — mirrors the _ago helper below.
  const isoAgo = (s) => new Date(Date.now() - s * 1000).toISOString();
  const activity = {
    running: [
      {
        kind: 'content',
        ref_id: '4412',
        title: 'A Field Guide to pgvector at Small Scale',
        status: 'running',
        step: 'qa.critic',
        progress_pct: 62,
        started_at: isoAgo(3 * 60),
        detail: { model: 'gemma-4-31b' },
      },
      {
        kind: 'job',
        ref_id: 'dispatch_media_pipeline',
        title: 'Media dispatch',
        status: 'running',
        started_at: isoAgo(35),
      },
      {
        kind: 'brain',
        ref_id: 'monitor_cycle',
        title: 'Brain monitor cycle',
        status: 'running',
        started_at: isoAgo(4),
      },
    ],
    recent: [
      {
        kind: 'job',
        title: 'topic-harvest',
        status: 'ok',
        started_at: isoAgo(48 * 60),
        finished_at: isoAgo(47 * 60),
        duration_ms: 8200,
      },
      {
        kind: 'job',
        title: 'render_prometheus_rules',
        status: 'ok',
        started_at: isoAgo(122 * 60),
        finished_at: isoAgo(122 * 60 - 1),
        duration_ms: 1100,
      },
      {
        kind: 'job',
        title: 'deepeval_g_eval',
        status: 'fail',
        started_at: isoAgo(430 * 60),
        finished_at: isoAgo(430 * 60 - 3),
        duration_ms: 3400,
      },
    ],
    summary: { running_by_kind: { content: 1, job: 1, brain: 1, media: 0 } },
  };

  // ── Cofounder chat mock (poindexter#948) ────────────────────
  // Shapes mirror the REAL /api/chat payloads (P1 backend) exactly —
  // conversations, typed message parts, and the NDJSON turn events the
  // scripted mock send emits. Only shapes P1 actually produces appear
  // here (tool chips, task_link cards, markdown): the demo shows the
  // truth, not vaporware (plan/result cards arrive with P3/P4).
  const chatMock = {
    persona: 'Poindexter',
    tools: [
      { name: 'list_tasks', description: 'List content pipeline tasks, newest first.', tier: 'read' },
      { name: 'get_task', description: "Get one pipeline task's detail by id.", tier: 'read' },
      { name: 'get_budget', description: 'Current AI spend vs the monthly budget cap.', tier: 'read' },
      { name: 'search_memory', description: "Semantic search across the operator's memory.", tier: 'read' },
      { name: 'find_similar_posts', description: 'Find published posts similar to a topic.', tier: 'read' },
      { name: 'get_audit_summary', description: 'System activity summary from the audit log.', tier: 'read' },
      { name: 'get_setting', description: 'Read one non-secret app_settings value.', tier: 'read' },
      { name: 'create_post', description: 'Create a blog post pipeline task (waits for your approval).', tier: 'write' },
    ],
    conversations: [
      {
        id: 'mock-conv-1',
        title: "What's in the pipeline right now?",
        brain: 'local',
        status: 'active',
        message_count: 4,
        created_at: isoAgo(3600),
        last_message_at: isoAgo(3400),
      },
      {
        id: 'mock-conv-2',
        title: 'Write a post about self-hosted observability',
        brain: 'local',
        status: 'active',
        message_count: 2,
        created_at: isoAgo(9200),
        last_message_at: isoAgo(9000),
      },
    ],
    threads: {
      'mock-conv-1': [
        {
          id: 'mock-m1', role: 'user', turn_status: 'complete',
          parts: [{ type: 'markdown', text: "What's in the pipeline right now?" }],
          created_at: isoAgo(3600),
        },
        {
          id: 'mock-m2', role: 'assistant', turn_status: 'complete',
          model: 'qwen2.5:7b', prompt_tokens: 1480, completion_tokens: 92, cost_usd: 0,
          parts: [
            { type: 'tool_call', name: 'list_tasks', ok: true, ms: 412, args_digest: '{"limit": 10}', result_digest: '3 task(s):\n- 9b1c8d2e [awaiting_approval] Own your numbers…\n- 41f2aa07 [in_progress] Local-first analytics…\n- c07d3b19 [pending] RAG retrieval stacks…' },
            { type: 'markdown', text: '3 tasks in flight: one awaiting your approval (9b1c8d2e), one mid-pipeline, one queued.' },
          ],
          created_at: isoAgo(3560),
        },
        {
          id: 'mock-m3', role: 'user', turn_status: 'complete',
          parts: [{ type: 'markdown', text: 'Write a post about VRAM contention in shared GPU rigs' }],
          created_at: isoAgo(3450),
        },
        {
          id: 'mock-m4', role: 'assistant', turn_status: 'complete',
          model: 'qwen2.5:7b', prompt_tokens: 1720, completion_tokens: 141, cost_usd: 0,
          parts: [
            { type: 'tool_call', name: 'create_post', ok: true, ms: 933, args_digest: '{"topic": "VRAM contention in shared GPU rigs"}', result_digest: '{"task_id": "f3a91c44", "status": "pending"}' },
            { type: 'card', card: { kind: 'task_link', task_id: 'f3a91c44' } },
            { type: 'markdown', text: 'Queued it — task f3a91c44 is pending. It runs the full pipeline and lands in your approval inbox; nothing publishes without you.' },
          ],
          created_at: isoAgo(3400),
        },
      ],
      'mock-conv-2': [
        {
          id: 'mock-m5', role: 'user', turn_status: 'complete',
          parts: [{ type: 'markdown', text: 'Write a post about self-hosted observability' }],
          created_at: isoAgo(9200),
        },
        {
          id: 'mock-m6', role: 'assistant', turn_status: 'interrupted',
          model: 'qwen2.5:7b', prompt_tokens: 610, completion_tokens: 0, cost_usd: 0,
          parts: [
            { type: 'markdown', text: 'Turn hit the 120s deadline (console_chat_turn_timeout_s) and was interrupted.' },
          ],
          created_at: isoAgo(9000),
        },
      ],
    },
    // Scripted mock turn: the event sequence a real send streams, with
    // per-event delays (ms). api.js plays this back through the same
    // onEvent path the live NDJSON reader uses.
    scriptFor(text) {
      const t = (text || '').toLowerCase();
      const mkText = (s) => ({ event: 'text', text: s });
      const base = [
        [120, { event: 'turn_started', message_id: 'mock-live-' + Date.now() }],
      ];
      if (t.includes('post about') || t.startsWith('/create-post')) {
        const topic = text.replace(/^.*post about/i, '').trim() || 'that topic';
        const id = 'mk' + String(Date.now()).slice(-6);
        return base.concat([
          [300, { event: 'tool_start', name: 'create_post', args_digest: JSON.stringify({ topic }) }],
          [900, { event: 'tool_result', name: 'create_post', ok: true, ms: 902, digest: '{"task_id": "' + id + '", "status": "pending"}' }],
          [60, { event: 'task_linked', task_id: id }],
          [500, mkText('Queued it — task ' + id + ' is pending and will land in your approval inbox.')],
          [80, { event: 'done', turn_status: 'complete', prompt_tokens: 1650, completion_tokens: 120, cost_usd: 0 }],
        ]);
      }
      if (t.includes('spent') || t.includes('budget')) {
        return base.concat([
          [280, { event: 'tool_start', name: 'get_budget', args_digest: '{}' }],
          [520, { event: 'tool_result', name: 'get_budget', ok: true, ms: 517, digest: '{"amount_spent": 4.31, "monthly_budget": 30, "status": "healthy"}' }],
          [420, mkText('$4.31 of the $30 monthly cap — healthy.')],
          [80, { event: 'done', turn_status: 'complete', prompt_tokens: 1390, completion_tokens: 64, cost_usd: 0 }],
        ]);
      }
      return base.concat([
        [300, { event: 'tool_start', name: 'list_tasks', args_digest: '{"limit": 10}' }],
        [650, { event: 'tool_result', name: 'list_tasks', ok: true, ms: 641, digest: '3 task(s):\n- 9b1c8d2e [awaiting_approval] …' }],
        [500, mkText('3 tasks in flight: one awaiting your approval, one mid-pipeline, one queued.')],
        [80, { event: 'done', turn_status: 'complete', prompt_tokens: 1480, completion_tokens: 92, cost_usd: 0 }],
      ]);
    },
  };

  window.PX = {
    now,
    hhmmss,
    ago,
    kpis,
    chatMock,
    inbox: [...inbox, ...mediaInbox],
    services,
    gpu,
    pipeline,
    brain,
    cost,
    auditSeed,
    liveTemplates,
    revenue,
    media,
    mediaRendering,
    activity,
    qa,
    topics,
    findings,
    schedule,
    seo,
    logs,
    traces,
    traceActive,
    traceSummary,
    traceDetail,
    restarts,
    launcher,
    nextTs() {
      now.setSeconds(now.getSeconds() + 30 + Math.floor(Math.random() * 90));
      return hhmmss(now);
    },
  };
})();
