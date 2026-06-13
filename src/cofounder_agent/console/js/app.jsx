/* ──────────────────────────────────────────────────────────────
   Poindexter Operator Console — app shell + orchestration.
   ────────────────────────────────────────────────────────────── */
const { useState: useS, useEffect: useE, useRef: useR, useMemo } = React;

// Poindexter version shown in the topbar eyebrow. release-please bumps the
// literal below on every release (see the `generic` extra-files entry in
// release-please-config.json) so the console tracks the real build instead of
// drifting. Keep the `// x-release-please-version` annotation on this line.
const POINDEXTER_VERSION = '0.74.0'; // x-release-please-version

const RAIL = [
  { id: 'overview', icon: 'overview', label: 'Overview' },
  { id: 'pipeline', icon: 'pipeline', label: 'Pipeline' },
  { id: 'brain', icon: 'brain', label: 'Brain' },
  { id: 'gpu', icon: 'gpu', label: 'GPU' },
  { id: 'services', icon: 'services', label: 'Services' },
  { id: 'audit', icon: 'audit', label: 'Audit' },
  { id: 'cost', icon: 'cost', label: 'Cost' },
  { id: 'revenue', icon: 'pulse', label: 'Revenue' },
];

function App() {
  const PX = window.PX;
  const [inbox, setInbox] = useS(PX.inbox);
  const [approved, setApproved] = useS([]); // live: staged tasks, awaiting publish
  const [services, setServices] = useS(PX.services);
  const [gpu, setGpu] = useS(PX.gpu);
  const [pipeline, setPipeline] = useS(PX.pipeline); // live: real /api/tasks
  const [feed, setFeed] = useS(() =>
    PX.auditSeed.map((l, i) => ({ ...l, key: 'seed' + i }))
  );
  const [entity, setEntity] = useS(null);
  const [filter, setFilter] = useS('all');
  const [feedFilter, setFeedFilter] = useS('all');
  const [active, setActive] = useS('overview');
  const [mode, setMode] = useS('console');
  const [paletteOpen, setPaletteOpen] = useS(false);
  const [clock, setClock] = useS('14:32:00');
  const [toastNode, pushToast] = useToasts();
  const mainRef = useR(null);
  const feedKey = useR(0);

  // ── Live simulation (subtle) ──────────────────────────────
  useE(() => {
    const feedTimer = setInterval(() => {
      const tpl =
        PX.liveTemplates[Math.floor(Math.random() * PX.liveTemplates.length)]();
      const line = {
        ...tpl,
        ts: PX.nextTs(),
        fresh: true,
        key: 'live' + feedKey.current++,
      };
      setFeed((f) => [line, ...f].slice(0, 40));
      setClock(PX.hhmmss(PX.now));
      setTimeout(
        () =>
          setFeed((f) =>
            f.map((x) => (x.key === line.key ? { ...x, fresh: false } : x))
          ),
        1200
      );
    }, 5200);
    const gpuTimer = setInterval(() => {
      setGpu((g) => {
        const util = Math.max(
          40,
          Math.min(96, g.util + (Math.random() * 10 - 5))
        );
        const temp = Math.max(
          58,
          Math.min(82, g.temp + (Math.random() * 2 - 1))
        );
        const power = Math.max(
          360,
          Math.min(560, g.power + (Math.random() * 30 - 15))
        );
        return {
          ...g,
          util: Math.round(util),
          temp: Math.round(temp),
          power: Math.round(power),
          utilHist: [...g.utilHist.slice(1), Math.round(util)],
        };
      });
    }, 3000);
    return () => {
      clearInterval(feedTimer);
      clearInterval(gpuTimer);
    };
  }, []);

  // ── Live: load real pending approvals into the inbox ──────
  // In live mode the inbox shows ONLY real /api/tasks/pending-approval rows.
  // Other inbox kinds (fail/alert/drift/media) stay empty here until their own
  // phases wire them — we never carry mock rows into a live view.
  useE(() => {
    if (!PX.api.isLive()) return;
    let alive = true;
    const load = async () => {
      try {
        const [pending, appr] = await Promise.all([
          PX.api.listApprovals(),
          PX.api.listTasks('?status=approved&limit=50'),
        ]);
        if (!alive) return;
        setInbox(((pending && pending.tasks) || []).map(approvalToInbox));
        setApproved((Array.isArray(appr) ? appr : appr && appr.tasks) || []);
      } catch (e) {
        pushToast(`Approvals load failed — ${e.message}`, 'red', '✕');
      }
    };
    load();
    const timer = setInterval(load, 5 * 60 * 1000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  // ── Live: load real tasks into the Pipeline panel ─────────
  // Maps /api/tasks rows → the panel task shape and derives per-block counts
  // from each task's current `stage` (the real graph_def node). Mock mode is
  // untouched. Polls on the same 5-min cadence as approvals.
  useE(() => {
    if (!PX.api.isLive()) return;
    let alive = true;
    const load = async () => {
      try {
        const res = await PX.api.listTasks('?limit=50');
        if (!alive) return;
        const rows = ((res && res.tasks) || []).map(taskToRow);
        setPipeline((p) => withLiveCounts(p, rows));
      } catch (e) {
        pushToast(`Tasks load failed — ${e.message}`, 'red', '✕');
      }
    };
    load();
    const timer = setInterval(load, 5 * 60 * 1000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  // ── ⌘K command palette ────────────────────────────────────
  useE(() => {
    const onKey = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        setPaletteOpen((o) => !o);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  // ── Scroll-spy for rail ───────────────────────────────────
  useE(() => {
    if (mode !== 'console') return;
    const el = mainRef.current;
    if (!el) return;
    const onScroll = () => {
      const cTop = el.getBoundingClientRect().top + 130;
      let cur = 'overview',
        best = -Infinity;
      for (const r of RAIL) {
        const s = document.getElementById('sec-' + r.id);
        if (!s) continue;
        const t = s.getBoundingClientRect().top;
        if (t <= cTop && t > best) {
          best = t;
          cur = r.id;
        }
      }
      setActive(cur);
    };
    el.addEventListener('scroll', onScroll);
    return () => el.removeEventListener('scroll', onScroll);
  }, [mode]);

  const scrollToSec = (id) => {
    const el = mainRef.current,
      sec = document.getElementById('sec-' + id);
    if (!el || !sec) return;
    const top =
      el.scrollTop +
      (sec.getBoundingClientRect().top - el.getBoundingClientRect().top) -
      10;
    el.scrollTo({ top: Math.max(0, top), behavior: 'smooth' });
  };

  const goTo = (id) => {
    if (mode !== 'console') {
      setMode('console');
      setActive(id);
      setTimeout(() => scrollToSec(id), 90);
      return;
    }
    scrollToSec(id);
    setActive(id);
  };

  // ── Action handlers ───────────────────────────────────────
  const removeInbox = (id) => setInbox((x) => x.filter((i) => i.id !== id));
  const pushFeed = (tag, html) => {
    const line = {
      tag,
      html,
      ts: PX.hhmmss(PX.now),
      fresh: true,
      key: 'act' + feedKey.current++,
    };
    setFeed((f) => [line, ...f].slice(0, 40));
    setTimeout(
      () =>
        setFeed((f) =>
          f.map((x) => (x.key === line.key ? { ...x, fresh: false } : x))
        ),
      1200
    );
  };
  const closeDrawer = () => setEntity(null);

  const A = {
    // Approve STAGES the task (auto_publish=false). Optimistic remove + roll
    // back on failure. Publish is a separate gate (below).
    approve: async (e) => {
      const prev = inbox;
      removeInbox(e.id);
      closeDrawer();
      try {
        await PX.api.approve(e.id);
        pushToast(
          `Approved — “${trunc(e.title)}” staged (not published)`,
          'mint',
          '✓'
        );
        pushFeed(
          ['mint', 'APPROVE'],
          `operator approved <b>${trunc(e.title)}</b> → staged`
        );
        // Surface it in the ready-to-publish list (the poll reconciles later).
        setApproved((a) => [
          { id: e.id, title: e.title, quality: e.detail?.quality },
          ...a.filter((t) => t.id !== e.id),
        ]);
      } catch (err) {
        setInbox(prev);
        pushToast(`Approve failed — ${err.message}`, 'red', '✕');
      }
    },
    // Publish SHIPS a staged task — the deliberate second gate after approve.
    publish: async (e) => {
      closeDrawer();
      try {
        await PX.api.publishTask(e.id);
        setApproved((a) => a.filter((t) => t.id !== e.id));
        pushToast(`Published — “${trunc(e.title)}” is live`, 'mint', '✓');
        pushFeed(
          ['mint', 'PUBLISH'],
          `operator published <b>${trunc(e.title)}</b>`
        );
      } catch (err) {
        pushToast(`Publish failed — ${err.message}`, 'red', '✕');
      }
    },
    reject: async (e) => {
      const prev = inbox;
      removeInbox(e.id);
      closeDrawer();
      try {
        await PX.api.reject(e.id, e.detail?.feedback || '');
        pushToast(`Rejected — sent back to edit`, 'amber', '⚠');
        pushFeed(
          ['amber', 'REVIEW'],
          `operator rejected <b>${trunc(e.title || '#' + (e.detail?.task || ''))}</b>`
        );
      } catch (err) {
        setInbox(prev);
        pushToast(`Reject failed — ${err.message}`, 'red', '✕');
      }
    },
    schedule: (e) => {
      removeInbox(e.id);
      closeDrawer();
      pushToast('Scheduled for 09:00 tomorrow', 'cyan', '✓');
    },
    // Real task actions (mock-safe: PX.api.* return mock {ok:true} offline).
    // retry → PUT /api/tasks/{id}/status {status:'pending'} (the flow re-claims
    // it; also clears a poisoned LangGraph checkpoint server-side). cancel →
    // DELETE /api/tasks/{id}. Optimistic pipeline update; roll a red toast on error.
    retry: async (e) => {
      const id = e.detail?.task || e.id;
      removeInbox(e.id);
      closeDrawer();
      try {
        await PX.api.retryTask(id);
        setPipeline((p) => ({
          ...p,
          tasks: (p.tasks || []).map((t) =>
            t.id === id ? { ...t, status: 'run' } : t
          ),
        }));
        pushToast(`Task #${id} re-queued from failed stage`, 'cyan', '↻');
        pushFeed(['cyan', 'PIPELINE'], `operator retried <b>#${id}</b>`);
      } catch (err) {
        pushToast(`Retry failed — ${err.message}`, 'red', '✕');
      }
    },
    kill: async (e) => {
      const id = e.detail?.task || e.id;
      removeInbox(e.id);
      closeDrawer();
      try {
        await PX.api.killTask(id);
        setPipeline((p) => ({
          ...p,
          tasks: (p.tasks || []).filter((t) => t.id !== id),
        }));
        pushToast(`Task #${id} cancelled`, 'red', '✕');
        pushFeed(['red', 'PIPELINE'], `operator cancelled <b>#${id}</b>`);
      } catch (err) {
        pushToast(`Cancel failed — ${err.message}`, 'red', '✕');
      }
    },
    skipStage: (e) => {
      removeInbox(e.id);
      closeDrawer();
      pushToast('Stage skipped, advancing task', 'amber', '⚠');
    },
    ack: (e) => {
      removeInbox(e.id);
      closeDrawer();
      pushToast('Alert acknowledged', 'cyan', '✓');
      pushFeed(
        ['cyan', 'ALERT'],
        `operator acked <b>${e.detail?.probe || e.title}</b>`
      );
    },
    snooze: (e) => {
      removeInbox(e.id);
      closeDrawer();
      pushToast('Snoozed 1 hour', 'cyan', '✓');
    },
    fix: (e) => {
      removeInbox(e.id);
      closeDrawer();
      pushToast('Fix applied — re-probing surface', 'mint', '✓');
      pushFeed(
        ['mint', 'REMEDIATE'],
        `operator applied URL fix · <b>${e.detail?.surface || ''}</b>`
      );
      setServices((s) =>
        s.map((x) =>
          x.name === 'prefect-server'
            ? { ...x, status: 'ok', metric: 'starting…' }
            : x
        )
      );
    },
    runProbe: (e) => {
      closeDrawer();
      pushToast('Probe re-running…', 'cyan', '◐');
      pushFeed(
        ['cyan', 'PROBE'],
        `operator triggered <b>${e.detail?.probe || 'probe'}</b>`
      );
    },
    restart: (s) => {
      closeDrawer();
      pushToast(`Restarting ${s.name}…`, 'cyan', '↻');
      pushFeed(['cyan', 'REMEDIATE'], `operator restarted <b>${s.name}</b>`);
      setServices((arr) =>
        arr.map((x) =>
          x.name === s.name ? { ...x, status: 'ok', metric: 'starting…' } : x
        )
      );
    },
    probe: (s) => {
      closeDrawer();
      pushToast(`Probing ${s.name}…`, 'cyan', '◐');
    },
    logs: (s) => {
      pushToast(`Streaming ${s.name} logs`, 'cyan', '▤');
    },
    embed: () => {
      closeDrawer();
      pushToast('Embed cycle triggered', 'cyan', '⚡');
      pushFeed(
        ['cyan', 'BRAIN'],
        `operator triggered <b>embed cycle</b> · 24 queued`
      );
    },
    runPipeline: () => {
      closeDrawer();
      pushToast('Pipeline run triggered', 'cyan', '▶');
      pushFeed(['cyan', 'PIPELINE'], `operator triggered <b>pipeline run</b>`);
    },
    openPrefect: () => pushToast('Opening Prefect UI…', 'cyan', '↗'),
    editBudget: () =>
      pushToast('Budget editing — coming in detail view', 'cyan', '✎'),
    openLemon: () => pushToast('Opening Lemon Squeezy dashboard…', 'cyan', '↗'),
    mediaApprove: (it) => {
      pushToast(
        `${it.title ? trunc(it.title) : 'Media'} → publish queue`,
        'mint',
        '✓'
      );
      pushFeed(
        ['mint', 'MEDIA'],
        `operator published <b>${it.medium || 'media'}</b> · ${trunc(it.title || '', 36)}`
      );
    },
    launch: (t) => pushToast(`Opening ${t.name}…`, 'cyan', '↗'),
    voice: () =>
      pushToast('Connecting to Poindexter voice (LiveKit)…', 'cyan', '🎙'),
  };

  const runAllProbes = () => {
    pushToast('Running all 28 probes…', 'cyan', '◐');
    pushFeed(
      ['cyan', 'PROBE'],
      `operator triggered <b>run-all-probes</b> · 28 checks`
    );
  };

  const open = (type, data) => setEntity({ type, data });

  // ── Command palette commands (built from live state) ──────────────────
  const commands = useMemo(() => {
    const cmds = [];
    inbox
      .filter((i) => i.kind === 'approve')
      .forEach((i) => {
        cmds.push({
          id: 'apr-' + i.id,
          group: 'Approve',
          icon: 'check',
          label: 'Approve & publish — ' + trunc(i.title, 44),
          hint: 'Q' + (i.detail?.quality || ''),
          run: () => A.approve(i),
        });
      });
    inbox
      .filter((i) => i.kind === 'fail')
      .forEach((i) => {
        cmds.push({
          id: 'rty-' + i.id,
          group: 'Pipeline',
          icon: 'retry',
          label: 'Retry — ' + trunc(i.title, 44),
          hint: 'failed',
          run: () => A.retry(i),
        });
        cmds.push({
          id: 'kill-' + i.id,
          group: 'Pipeline',
          icon: 'kill',
          label: 'Kill — ' + trunc(i.title, 40),
          danger: true,
          run: () => A.kill(i),
        });
      });
    inbox
      .filter((i) => i.kind === 'alert')
      .forEach((i) => {
        cmds.push({
          id: 'ack-' + i.id,
          group: 'Alerts',
          icon: 'check',
          label: 'Acknowledge — ' + trunc(i.title, 42),
          run: () => A.ack(i),
        });
      });
    inbox
      .filter((i) => i.kind === 'drift')
      .forEach((i) => {
        cmds.push({
          id: 'fix-' + i.id,
          group: 'Alerts',
          icon: 'bolt',
          label: 'Apply fix — ' + trunc(i.title, 42),
          run: () => A.fix(i),
        });
      });
    services.forEach((s) => {
      cmds.push({
        id: 'rs-' + s.name,
        group: 'Services',
        icon: 'retry',
        label: 'Restart ' + s.name,
        hint:
          s.status === 'err'
            ? 'down'
            : s.status === 'warn'
              ? 'degraded'
              : 'healthy',
        danger: s.status === 'err',
        run: () => A.restart(s),
      });
    });
    cmds.push({
      id: 'probes',
      group: 'Run',
      icon: 'bolt',
      label: 'Run all 28 probes',
      run: runAllProbes,
    });
    cmds.push({
      id: 'embed',
      group: 'Run',
      icon: 'bolt',
      label: 'Trigger embed cycle',
      run: A.embed,
    });
    cmds.push({
      id: 'pipe',
      group: 'Run',
      icon: 'play',
      label: 'Trigger pipeline run',
      run: A.runPipeline,
    });
    [
      ['console', 'Console overview', 'overview'],
      ['feed', 'Operations feed', null],
      ['map', 'System map', null],
      ['wall', 'Wall display', null],
    ].forEach(([m, lbl]) => {
      cmds.push({
        id: 'view-' + m,
        group: 'Go to',
        icon:
          m === 'map'
            ? 'gpu'
            : m === 'wall'
              ? 'overview'
              : m === 'feed'
                ? 'audit'
                : 'overview',
        label: lbl,
        hint: 'view',
        run: () => setMode(m),
      });
    });
    RAIL.forEach((r) =>
      cmds.push({
        id: 'nav-' + r.id,
        group: 'Go to',
        icon: r.icon,
        label: r.label,
        hint: 'section',
        run: () => goTo(r.id),
      })
    );
    cmds.push({
      id: 'set',
      group: 'Go to',
      icon: 'settings',
      label: 'App settings',
      hint: 'config',
      run: () => setMode('settings'),
    });
    PX.launcher.forEach((t) =>
      cmds.push({
        id: 'open-' + t.name,
        group: 'Launch',
        icon: 'link',
        label: 'Open ' + t.name,
        hint: t.sub,
        run: () => A.launch(t),
      })
    );
    cmds.push({
      id: 'voice',
      group: 'Launch',
      icon: 'play',
      label: 'Talk to Poindexter (voice)',
      hint: 'livekit',
      run: () => A.voice(),
    });
    return cmds;
  }, [inbox, services]);

  const sysState = useMemo(() => {
    if (services.some((s) => s.status === 'err'))
      return [
        'err',
        `${services.filter((s) => s.status === 'err').length} SERVICE DOWN`,
      ];
    const open = inbox.length;
    if (open > 0) return ['warn', `${open} NEED ATTENTION`];
    return ['ok', 'ALL SYSTEMS NOMINAL'];
  }, [services, inbox]);

  return (
    <div className={`app gl-atmosphere mode-${mode}`}>
      {/* Rail */}
      <nav className="rail">
        <div className="rail__logo" title="Poindexter">
          P
        </div>
        {RAIL.map((r) => {
          const count =
            r.id === 'overview'
              ? inbox.length
              : r.id === 'services'
                ? services.filter((s) => s.status === 'err').length
                : 0;
          return (
            <button
              key={r.id}
              className={`rail__btn ${active === r.id ? 'is-active' : ''}`}
              title={r.label}
              onClick={() => goTo(r.id)}
            >
              <Icon name={r.icon} size={19} />
              {count > 0 && <span className="rail__count">{count}</span>}
              <span className="rail__label">{r.label}</span>
            </button>
          );
        })}
        <span className="rail__spacer" />
        <button
          className={`rail__btn ${mode === 'settings' ? 'is-active' : ''}`}
          title="App settings"
          onClick={() => setMode('settings')}
        >
          <Icon name="settings" size={19} />
          <span className="rail__label">App Settings</span>
        </button>
      </nav>

      {/* Topbar */}
      <header className="topbar">
        <div className="topbar__title">
          <span className="topbar__eyebrow">
            {`// GLAD LABS · POINDEXTER V${POINDEXTER_VERSION}`}
          </span>
          <span className="topbar__crumb">
            OPERATOR <em>CONSOLE</em>
          </span>
        </div>
        <span className="topbar__spacer" />
        <div className="modeswitch">
          {[
            ['console', 'overview', 'Console'],
            ['feed', 'audit', 'Feed'],
            ['map', 'gpu', 'Map'],
            ['wall', 'overview', 'Wall'],
          ].map(([m, ic, lbl]) => (
            <button
              key={m}
              className={mode === m ? 'is-active' : ''}
              onClick={() => setMode(m)}
              title={lbl}
            >
              <Icon name={ic} size={13} />
              <span className="lbl">{lbl}</span>
            </button>
          ))}
        </div>
        <button className="kbd-hint" onClick={() => setPaletteOpen(true)}>
          <Icon name="search" size={12} />
          Command<kbd>⌘K</kbd>
        </button>
        <button
          className={`sys-chip sys-chip--${sysState[0]}`}
          onClick={() => {
            setMode('console');
            goTo('overview');
          }}
        >
          <span className="dot" />
          {sysState[1]}
        </button>
        <div className="topbar__meta">
          <span>
            <span className="k">SYNC</span>{' '}
            <span className="live-dot" style={{ verticalAlign: 'middle' }} /> 5m
          </span>
          <span className="tnum">{clock}</span>
        </div>
      </header>

      {/* Main */}
      <main className="main" ref={mainRef}>
        {mode === 'console' && (
          <div className="main__inner">
            <div id="sec-overview">
              <KpiStrip kpis={PX.kpis} onOpen={(k) => open('kpi', k)} />
            </div>

            <div className="masonry masonry--overview">
              <div id="sec-overview-inbox">
                <ActionInbox
                  items={inbox}
                  filter={filter}
                  setFilter={setFilter}
                  onOpen={(it) => open('inbox', it)}
                  onApprove={A.approve}
                  onReject={A.reject}
                  onRetry={A.retry}
                  onAck={A.ack}
                  onFix={A.fix}
                />
              </div>
              {approved.length > 0 && (
                <div id="sec-publish">
                  <PublishQueue items={approved} onPublish={A.publish} />
                </div>
              )}
              <div id="sec-services">
                <ServiceGrid
                  services={services}
                  onOpen={(s) => open('service', s)}
                  onRestart={A.restart}
                  onProbe={runAllProbes}
                />
              </div>
              <div id="sec-pipeline">
                <PipelinePanel
                  pipeline={pipeline}
                  onOpen={() => open('pipeline', pipeline)}
                  onOpenTask={(t) => open('task', t)}
                  onRetry={A.retry}
                />
              </div>
              <div id="sec-gpu">
                <GpuHud gpu={gpu} onOpen={() => open('gpu', gpu)} />
              </div>
              <div id="sec-media">
                <MediaPanel
                  media={PX.media}
                  onOpenItem={(it) =>
                    open(
                      'inbox',
                      PX.inbox.find((x) => x.id === 'media-318') || {
                        kind: 'media',
                        detail: { ...it, stage: 'gate_2_review' },
                      }
                    )
                  }
                  onApprove={A.mediaApprove}
                />
              </div>
              <div id="sec-revenue">
                <RevenuePanel
                  revenue={PX.revenue}
                  onOpen={() => open('revenue', PX.revenue)}
                />
              </div>
              <div id="sec-brain">
                <BrainPanel
                  brain={PX.brain}
                  onOpen={() => open('brain', PX.brain)}
                  onEmbed={A.embed}
                />
              </div>
              <div id="sec-qa">
                <QAPanel qa={PX.qa} onOpen={() => open('qa', PX.qa)} />
              </div>
              <div id="sec-cost">
                <CostPanel
                  cost={PX.cost}
                  onOpen={() => open('cost', PX.cost)}
                />
              </div>
              <div id="sec-launch">
                <LauncherPanel
                  tools={PX.launcher}
                  onLaunch={A.launch}
                  onVoice={A.voice}
                />
              </div>
              <div id="sec-audit">
                <AuditFeed
                  lines={feed}
                  onOpen={() =>
                    pushToast('Opening full audit log', 'cyan', '↗')
                  }
                />
              </div>
            </div>

            <footer
              style={{
                textAlign: 'center',
                padding: '22px 0 8px',
                fontFamily: 'var(--gl-font-mono)',
                fontSize: 10,
                letterSpacing: '.16em',
                color: 'var(--gl-text-dim)',
              }}
            >
              // POINDEXTER OPERATOR CONSOLE · LOCAL-FIRST · OLLAMA ONLY · NO
              PAID APIS
            </footer>
          </div>
        )}

        {mode === 'feed' && (
          <div className="main__inner">
            <FeedMode
              inbox={inbox}
              feed={feed}
              filter={feedFilter}
              setFilter={setFeedFilter}
              onOpen={(it) => open('inbox', it)}
              A={A}
            />
          </div>
        )}

        {mode === 'map' && (
          <div style={{ padding: 16 }}>
            <SystemMap
              services={services}
              gpu={gpu}
              onOpen={(s) => open('service', s)}
              onOpenGpu={() => open('gpu', gpu)}
              onRestart={A.restart}
            />
          </div>
        )}

        {mode === 'wall' && (
          <WallDisplay
            kpis={PX.kpis}
            gpu={gpu}
            pipeline={pipeline}
            brain={PX.brain}
            services={services}
            inbox={inbox}
            clock={clock}
            sysState={sysState}
            revenue={PX.revenue}
          />
        )}

        {mode === 'settings' && (
          <SettingsMode
            onApply={(changes, ok, errMsg) => {
              if (!ok) {
                pushToast(`Save failed — ${errMsg || 'API error'}`, 'red', '✕');
                return;
              }
              pushToast(
                `Applied ${changes.length} setting${changes.length > 1 ? 's' : ''} · brain re-reads ≤ 5m`,
                'mint',
                '✓'
              );
              changes.forEach((c) =>
                pushFeed(
                  ['cyan', 'CONFIG'],
                  `operator set <span class="c-cyan">${c.key}</span> <span class="c-dim">${c.from}</span> → <b>${c.to}</b>`
                )
              );
            }}
            pushFeed={pushFeed}
          />
        )}
      </main>

      <CommandPalette
        open={paletteOpen}
        commands={commands}
        onClose={() => setPaletteOpen(false)}
      />

      <Drawer entity={entity} onClose={closeDrawer} actions={A} />
      {toastNode}
    </div>
  );
}

// Minutes since an ISO timestamp (for relative-age display).
function minsSince(iso) {
  return Math.max(0, Math.round((Date.now() - new Date(iso)) / 60000));
}

// Map a /api/tasks/pending-approval row → the Action Inbox item shape.
function approvalToInbox(t) {
  const PX = window.PX;
  return {
    id: t.task_id,
    kind: 'approve',
    priority: 1,
    title: t.task_name || t.topic || `Task ${t.task_id}`,
    sub: [
      [
        'QUALITY',
        t.quality_score != null ? String(Math.round(t.quality_score)) : '—',
      ],
      ['TYPE', t.task_type || 'blog_post'],
      ['TOPIC', t.topic || '—'],
    ],
    age: t.created_at ? PX.ago(minsSince(t.created_at)) : '',
    tags: [['cyan', 'READY']],
    detail: {
      excerpt: t.content_preview || '',
      quality: t.quality_score,
      pipeline: t.status,
      topic: t.topic,
      featured_image_url: t.featured_image_url,
      task: t.task_id,
    },
  };
}

// Map a /api/tasks UnifiedTaskResponse status → the panel's StatusText kind.
// TASK_STATUS only knows ok|run|fail, so collapse the richer API states.
function taskStatusKind(status) {
  const s = (status || '').toLowerCase();
  if (['failed', 'fail', 'error', 'cancelled', 'canceled'].includes(s))
    return 'fail';
  if (
    [
      'completed',
      'complete',
      'approved',
      'published',
      'awaiting_approval',
    ].includes(s)
  )
    return 'ok';
  return 'run'; // pending / queued / generating / running / in_progress …
}

// Map a /api/tasks row → the Pipeline panel task shape. `stage` is the real
// current graph_def node when the API exposes it (else the status/publish bucket).
function taskToRow(t) {
  const PX = window.PX;
  const id = t.id || t.task_id;
  return {
    id,
    topic: t.topic || t.task_name || `Task ${id}`,
    stage: t.stage || t.publish_status || t.status || '—',
    status: taskStatusKind(t.status),
    quality: t.quality_score != null ? Math.round(t.quality_score) : null,
    model: t.model_used || '—',
    age: t.created_at ? PX.ago(minsSince(t.created_at)) : '',
    _raw: t,
  };
}

// Rebuild a pipeline object from live rows: swap in the real task list and
// recount each block by how many ACTIVE tasks sit at one of its nodes. Terminal
// tasks (ok/fail) don't inflate "in-flight" block counts — honest empties result.
function withLiveCounts(p, rows) {
  const nodeToBlock = {};
  (p.stages || []).forEach((b) =>
    (b.nodes || []).forEach((n) => (nodeToBlock[n] = b.name))
  );
  const counts = {};
  let ok = 0,
    terminal = 0;
  rows.forEach((r) => {
    if (r.status === 'ok' || r.status === 'fail') {
      terminal++;
      if (r.status === 'ok') ok++;
    }
    if (r.status !== 'run') return; // only active tasks occupy a block
    const block = nodeToBlock[r.stage];
    if (block) counts[block] = (counts[block] || 0) + 1;
  });
  return {
    ...p,
    tasks: rows,
    // Honest live meta: success rate over the loaded terminal tasks. Average
    // completion isn't derivable from the list endpoint, so it reads unknown
    // rather than carrying the mock value into a live view.
    successRate: terminal ? Math.round((100 * ok) / terminal) : '—',
    avgCompletion: '—',
    stages: (p.stages || []).map((b) => ({
      ...b,
      count: counts[b.name] || 0,
      state: counts[b.name] ? 'hot' : '',
    })),
  };
}

// Ready-to-publish list — staged (approved) tasks awaiting the publish gate.
// Renders nothing when empty, so mock mode and an empty live queue show nothing
// (no fabricated rows). Accepts both server rows (task_name/quality_score) and
// the optimistic row pushed on approve (title/quality).
function PublishQueue({ items, onPublish }) {
  if (!items || items.length === 0) return null;
  return (
    <div className="panel">
      <div className="panel__head">
        <span className="panel__title">
          <span className="idx">▲</span>READY TO PUBLISH
        </span>
        <span className="panel__spacer" style={{ flex: 1 }} />
        <span className="panel__meta">{items.length} staged</span>
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {items.map((t) => {
          const title = t.title || t.task_name || t.topic || t.id;
          const q = t.quality != null ? t.quality : t.quality_score;
          return (
            <div
              key={t.id}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 10,
                padding: '8px 10px',
                border: '1px solid var(--gl-line, rgba(255,255,255,0.1))',
                borderRadius: 2,
              }}
            >
              <div style={{ flex: 1, minWidth: 0 }}>
                <div
                  style={{
                    fontFamily: 'var(--gl-font-mono)',
                    fontSize: 12,
                    whiteSpace: 'nowrap',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                  }}
                >
                  {trunc(title, 52)}
                </div>
                <div
                  style={{
                    fontSize: 10,
                    letterSpacing: '.12em',
                    color: 'var(--gl-text-dim)',
                  }}
                >
                  {q != null ? `Q${Math.round(q)} · ` : ''}APPROVED · AWAITING
                  PUBLISH
                </div>
              </div>
              <button
                className="mbtn mbtn--primary"
                onClick={() =>
                  onPublish({
                    id: t.id,
                    title: t.title || t.task_name || t.topic || t.id,
                  })
                }
              >
                <Icon name="play" size={12} />
                Publish
              </button>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function trunc(s, n = 40) {
  return s && s.length > n ? s.slice(0, n - 1) + '…' : s;
}

ReactDOM.createRoot(document.getElementById('root')).render(<App />);
