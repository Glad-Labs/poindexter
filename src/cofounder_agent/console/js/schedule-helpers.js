/* ══════════════════════════════════════════════════════════════
   Publish-slot picker — pure helpers (window.PXSchedule)
   ──────────────────────────────────────────────────────────────
   Plain-JS, no React: local-time formatting, the preset slot list,
   and slot validation for the approve drawer's Schedule action.
   Kept out of drawer.jsx (kpis.js / chat-helpers.js pattern) so the
   node:test + vm suite exercises the REAL logic with no DOM.

   The one invariant worth stating up front: the operator picks a
   WALL-CLOCK time and the server stores UTC. Everything here works
   in local time and converts exactly once, at the boundary
   (`toIso`). Any shortcut through toISOString() mid-pipeline
   shifts the chosen hour by the operator's offset.
   ══════════════════════════════════════════════════════════════ */
(function () {
  // <input type="datetime-local"> reads and writes "YYYY-MM-DDTHH:mm"
  // in LOCAL time with no zone suffix. toISOString() returns UTC, so
  // using it to seed the input would silently move the operator's
  // chosen hour — pick 09:00 in EDT and the box redisplays 13:00.
  // Build from the local getters instead.
  function toLocalInput(d) {
    const p = (n) => String(n).padStart(2, '0');
    return (
      d.getFullYear() +
      '-' +
      p(d.getMonth() + 1) +
      '-' +
      p(d.getDate()) +
      'T' +
      p(d.getHours()) +
      ':' +
      p(d.getMinutes())
    );
  }

  // The single local→UTC conversion point. `new Date('YYYY-MM-DDTHH:mm')`
  // (no zone suffix) is parsed as LOCAL time per ES2015+, which is what
  // we want — the operator meant their own clock.
  function toIso(localValue) {
    const d = new Date(localValue);
    return isFinite(d.getTime()) ? d.toISOString() : null;
  }

  // Validate a datetime-local string. Empty (nothing picked yet) and
  // unparseable (mid-edit) are both "not usable"; a past slot is called
  // out separately so the UI can explain rather than just disable —
  // scheduled_publisher sweeps every 60s, so a past slot ships almost
  // immediately, which is rarely what the operator meant.
  function slotState(localValue, now = new Date()) {
    if (!localValue) return { valid: false, reason: 'empty', date: null };
    const d = new Date(localValue);
    if (!isFinite(d.getTime()))
      return { valid: false, reason: 'unparseable', date: null };
    if (d <= now) return { valid: false, reason: 'past', date: d };
    return {
      valid: true,
      reason: null,
      date: d,
      lead: d.getTime() - now.getTime(),
    };
  }

  // Quick presets. The first two are plain calendar math; "After queue"
  // extends the existing cadence by one `publish_spacing_hours` interval
  // past the last future slot, so picking it paces a post behind what is
  // already queued instead of stacking on top of it.
  //
  // That third preset is omitted when the queue is empty or the spacing
  // setting is unreadable. An invented interval would be worse than one
  // fewer button (feedback_no_dummy_data) — the operator can always type
  // a time.
  function slotPresets(schedule, spacingHours, now = new Date()) {
    const at = (daysAhead, hour) => {
      const d = new Date(now);
      d.setDate(d.getDate() + daysAhead);
      d.setHours(hour, 0, 0, 0);
      return d;
    };
    const out = [
      ['Tomorrow 09:00', at(1, 9)],
      ['Tomorrow 14:00', at(1, 14)],
    ];
    const lastQueued = ((schedule && schedule.rows) || [])
      .map((r) => new Date(r.published_at).getTime())
      .filter((t) => isFinite(t) && t >= now.getTime())
      .sort((a, b) => b - a)[0];
    if (lastQueued && spacingHours > 0) {
      out.push([
        `After queue (+${spacingHours}h)`,
        new Date(lastQueued + spacingHours * 3600000),
      ]);
    }
    return out;
  }

  // Human-readable lead time. Mirrors panels2.jsx relWhen() so the
  // drawer and the queue panel describe the same slot the same way.
  function leadLabel(ms) {
    const s = Math.abs(ms) / 1000;
    if (s < 3600) return Math.max(1, Math.round(s / 60)) + 'm';
    if (s < 86400) return Math.round(s / 3600) + 'h';
    return Math.round(s / 86400) + 'd';
  }

  window.PXSchedule = {
    toLocalInput,
    toIso,
    slotState,
    slotPresets,
    leadLabel,
  };
})();
