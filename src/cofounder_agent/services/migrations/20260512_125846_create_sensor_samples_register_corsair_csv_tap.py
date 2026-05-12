"""Migration 20260512_125846: create sensor_samples + register corsair_csv tap.

Matt 2026-05-12 12:57 UTC — "can you help me create a tap job for this
sensor data?" The Corsair iCUE LINK CSV export at ``~/sensor_logs/``
captures rail-level PSU power, coolant-loop temps, GPU framebuffer,
DDR5 voltages, and 17 individual fan RPMs — sensors that the existing
Prometheus exporters (windows_exporter + nvidia-smi-exporter) don't
surface. The motivating gap is the HX1500i PSU monitoring noted in
``memory/project_hx1500i_psu.md``: rail-level current + efficiency
are only exposed via Corsair's iCUE link, and the manual CSV export
was the operator workaround.

This migration creates two things:

1. ``sensor_samples`` table — timestamp-resolution time-series sink.
   Distinct from ``external_metrics`` because that table is DATE
   resolution (one row per day), which is too coarse for ~30s — 5min
   polling. Shape mirrors external_metrics otherwise so operators
   reading both feel at home.

2. An ``external_taps`` row registering the ``corsair_csv`` handler
   under the standard declarative-data-plane. Disabled by default
   (operators flip ``enabled=true`` after the tap_runner job has
   verified the handler import succeeds).

Both polling cadences are operator-configurable at runtime:

- The tap_runner job cadence (how often the runner wakes up to call
  enabled taps) is tuned via ``plugin.job.run_taps.config.interval_*``.
- The per-tap "is it due yet?" check uses ``config.poll_interval_minutes``
  on the external_taps row (default 5 min, matching Matt's current
  iCUE log frequency).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    """Create sensor_samples + register the corsair_csv tap row."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS public.sensor_samples (
                id           bigserial PRIMARY KEY,
                source       text NOT NULL,
                metric_name  text NOT NULL,
                metric_value numeric(14,4) NOT NULL,
                unit         text,
                dimensions   jsonb DEFAULT '{}'::jsonb,
                sampled_at   timestamp with time zone NOT NULL,
                fetched_at   timestamp with time zone DEFAULT now() NOT NULL
            )
            """
        )
        # Time-series queries hit sampled_at first, so partial-by-source
        # is the dominant Grafana access pattern: WHERE source='corsair_csv'
        # AND metric_name='cpu_package_temp' AND sampled_at > now() - '1h'.
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_sensor_samples_source_metric_time
                ON public.sensor_samples (source, metric_name, sampled_at DESC)
            """
        )
        # Idempotency guard: rerunning the tap on an already-processed
        # row would double-count. (source, sampled_at, metric_name)
        # uniquely identifies a single CSV cell.
        await conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_sensor_samples_source_time_metric
                ON public.sensor_samples (source, sampled_at, metric_name)
            """
        )

        # Register the tap. Disabled by default — operator flips it on
        # via `poindexter taps set corsair_csv enabled=true` (or direct
        # UPDATE) once they confirm the handler is loadable in their env.
        await conn.execute(
            """
            INSERT INTO public.external_taps (
                name,
                handler_name,
                tap_type,
                target_table,
                schedule,
                config,
                state,
                enabled,
                metadata
            )
            VALUES (
                'corsair_csv',
                'corsair_csv',
                'corsair_csv',
                'sensor_samples',
                'every 5 minutes',
                $1::jsonb,
                '{}'::jsonb,
                false,
                $2::jsonb
            )
            ON CONFLICT (name) DO NOTHING
            """,
            # Default config — operator overridable at runtime
            (
                '{'
                '"directory": "/host/sensor_logs",'
                '"filename_glob": "corsair_cue_*.csv",'
                '"poll_interval_minutes": 5,'
                '"max_rows_per_run": 10000,'
                # iCUE writes naive wall-clock timestamps; subtract this
                # offset to land them on a real UTC axis so Grafana's
                # $__timeFilter matches against now() correctly. -4 = EDT
                # for Matt in summer; switch to -5 in winter (or wire a
                # smarter time source). UTC/GMT zones can stay at 0.
                '"local_timezone_offset_hours": -4.0,'
                '"metrics": {'
                '"CPU Package": {"name": "cpu_package_temp", "unit": "celsius"},'
                '"CPU Load": {"name": "cpu_load_pct", "unit": "percent"},'
                '"GPU Temp #1": {"name": "gpu_temp", "unit": "celsius"},'
                '"GPU Load": {"name": "gpu_load_pct", "unit": "percent"},'
                '"GPU Memory Load": {"name": "gpu_memory_load_pct", "unit": "percent"},'
                '"GPU Frame Buffer": {"name": "gpu_framebuffer_pct", "unit": "percent"},'
                '"HX1500i Power In": {"name": "psu_power_in", "unit": "watts"},'
                '"HX1500i Power Out": {"name": "psu_power_out", "unit": "watts"},'
                '"HX1500i Efficiency": {"name": "psu_efficiency_pct", "unit": "percent"},'
                '"HX1500i Temp": {"name": "psu_temp", "unit": "celsius"},'
                '"HX1500i 12V Current": {"name": "psu_12v_current", "unit": "amps"},'
                '"HX1500i 12V Power": {"name": "psu_12v_power", "unit": "watts"},'
                '"HX1500i 5V Power": {"name": "psu_5v_power", "unit": "watts"},'
                '"HX1500i 3.3V Power": {"name": "psu_3v3_power", "unit": "watts"},'
                '"VENGEANCE RGB DDR5 Temp #1": {"name": "ram_temp_1", "unit": "celsius"},'
                '"VENGEANCE RGB DDR5 Temp #2": {"name": "ram_temp_2", "unit": "celsius"},'
                '"iCUE LINK XC7 ELITE LCD Coolant Temp": {"name": "coolant_temp_cpu_block", "unit": "celsius"},'
                '" XD6 Pump: Pump": {"name": "pump_rpm", "unit": "rpm"}'
                '}'
                '}'
            ),
            '{"source": "matt-2026-05-12", "reason": "rail-level PSU + coolant gap from project_hx1500i_psu"}',
        )
        logger.info(
            "Migration create_sensor_samples_register_corsair_csv_tap: applied"
        )


async def down(pool) -> None:
    """Drop sensor_samples and unregister the corsair_csv tap.

    Safe to run — the table is purely additive observability data and
    can be recreated empty on the next up().
    """
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM public.external_taps WHERE name = 'corsair_csv'"
        )
        await conn.execute("DROP TABLE IF EXISTS public.sensor_samples")
        logger.info(
            "Migration create_sensor_samples_register_corsair_csv_tap down: reverted"
        )
