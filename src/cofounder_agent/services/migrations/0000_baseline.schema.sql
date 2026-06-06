--
--

CREATE EXTENSION IF NOT EXISTS pg_stat_statements WITH SCHEMA public;

--
--

CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public;

--
--

CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public;

--
--

CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;

--
--

CREATE OR REPLACE FUNCTION public.app_settings_auto_encrypt() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    enc_key TEXT;
BEGIN
    IF NEW.is_secret IS NOT TRUE THEN
        RETURN NEW;
    END IF;
    IF NEW.value IS NULL OR NEW.value = '' THEN
        RETURN NEW;
    END IF;
    IF NEW.value LIKE 'enc:v1:%' THEN
        RETURN NEW;
    END IF;

    BEGIN
        enc_key := current_setting('poindexter.secret_key', true);
    EXCEPTION WHEN OTHERS THEN
        enc_key := NULL;
    END;

    IF enc_key IS NULL OR enc_key = '' THEN
        RETURN NEW;
    END IF;

    NEW.value := 'enc:v1:' || encode(
        pgp_sym_encrypt(NEW.value, enc_key),
        'base64'
    );
    RETURN NEW;
END;
$$;

--
--

CREATE OR REPLACE FUNCTION public.content_tasks_delete_redirect() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    DELETE FROM pipeline_tasks WHERE task_id = OLD.task_id;
    RETURN OLD;
END;
$$;

--
--

CREATE OR REPLACE FUNCTION public.content_tasks_insert_redirect() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    INSERT INTO pipeline_tasks (task_id, task_type, topic, status, stage, style, tone, target_length, category, primary_keyword, target_audience, percentage, message, model_used, error_message, created_at, updated_at)
    VALUES (NEW.task_id, COALESCE(NEW.task_type, 'blog_post'), COALESCE(NEW.topic, NEW.title, 'untitled'), COALESCE(NEW.status, 'pending'), COALESCE(NEW.stage, 'pending'), COALESCE(NEW.style, 'technical'), COALESCE(NEW.tone, 'professional'), COALESCE(NEW.target_length, 1500), NEW.category, NEW.primary_keyword, NEW.target_audience, COALESCE(NEW.percentage, 0), NEW.message, NEW.model_used, NEW.error_message, COALESCE(NEW.created_at, NOW()), COALESCE(NEW.updated_at, NOW()))
    ON CONFLICT (task_id) DO NOTHING;

    INSERT INTO pipeline_versions (task_id, version, title, content, excerpt, featured_image_url, seo_title, seo_description, seo_keywords, quality_score, qa_feedback, models_used_by_phase, stage_data)
    VALUES (NEW.task_id, 1, NEW.title, NEW.content, NEW.excerpt, NEW.featured_image_url, NEW.seo_title, NEW.seo_description, NEW.seo_keywords, NEW.quality_score, NEW.qa_feedback, COALESCE(NEW.models_used_by_phase, '{}'),
        jsonb_strip_nulls(jsonb_build_object('metadata', NEW.metadata, 'result', NEW.result, 'task_metadata', NEW.task_metadata)))
    ON CONFLICT (task_id, version) DO UPDATE SET
        title = COALESCE(EXCLUDED.title, pipeline_versions.title),
        content = COALESCE(EXCLUDED.content, pipeline_versions.content),
        excerpt = COALESCE(EXCLUDED.excerpt, pipeline_versions.excerpt),
        featured_image_url = COALESCE(EXCLUDED.featured_image_url, pipeline_versions.featured_image_url),
        seo_title = COALESCE(EXCLUDED.seo_title, pipeline_versions.seo_title),
        seo_description = COALESCE(EXCLUDED.seo_description, pipeline_versions.seo_description),
        seo_keywords = COALESCE(EXCLUDED.seo_keywords, pipeline_versions.seo_keywords),
        quality_score = COALESCE(EXCLUDED.quality_score, pipeline_versions.quality_score),
        qa_feedback = COALESCE(EXCLUDED.qa_feedback, pipeline_versions.qa_feedback),
        stage_data = pipeline_versions.stage_data || EXCLUDED.stage_data;

    RETURN NEW;
END;
$$;

--
--

CREATE OR REPLACE FUNCTION public.content_tasks_update_redirect() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    UPDATE pipeline_tasks SET
        status = NEW.status,
        stage = COALESCE(NEW.stage, pipeline_tasks.stage),
        percentage = COALESCE(NEW.percentage, pipeline_tasks.percentage),
        message = COALESCE(NEW.message, pipeline_tasks.message),
        model_used = COALESCE(NEW.model_used, pipeline_tasks.model_used),
        error_message = COALESCE(NEW.error_message, pipeline_tasks.error_message),
        category = COALESCE(NEW.category, pipeline_tasks.category),
        style = COALESCE(NEW.style, pipeline_tasks.style),
        tone = COALESCE(NEW.tone, pipeline_tasks.tone),
        target_audience = COALESCE(NEW.target_audience, pipeline_tasks.target_audience),
        primary_keyword = COALESCE(NEW.primary_keyword, pipeline_tasks.primary_keyword),
        target_length = COALESCE(NEW.target_length, pipeline_tasks.target_length),
        updated_at = COALESCE(NEW.updated_at, NOW()),
        started_at = COALESCE(NEW.started_at, pipeline_tasks.started_at),
        completed_at = CASE
            WHEN NEW.status IN ('published','failed','cancelled','rejected','rejected_final')
            THEN NOW()
            ELSE pipeline_tasks.completed_at
        END
    WHERE task_id = NEW.task_id;

    UPDATE pipeline_versions SET
        title = COALESCE(NEW.title, pipeline_versions.title),
        content = COALESCE(NEW.content, pipeline_versions.content),
        excerpt = COALESCE(NEW.excerpt, pipeline_versions.excerpt),
        featured_image_url = COALESCE(NEW.featured_image_url, pipeline_versions.featured_image_url),
        seo_title = COALESCE(NEW.seo_title, pipeline_versions.seo_title),
        seo_description = COALESCE(NEW.seo_description, pipeline_versions.seo_description),
        seo_keywords = COALESCE(NEW.seo_keywords, pipeline_versions.seo_keywords),
        quality_score = COALESCE(NEW.quality_score, pipeline_versions.quality_score),
        qa_feedback = COALESCE(NEW.qa_feedback, pipeline_versions.qa_feedback),
        models_used_by_phase = COALESCE(NEW.models_used_by_phase, pipeline_versions.models_used_by_phase),
        stage_data = pipeline_versions.stage_data || jsonb_strip_nulls(
            jsonb_build_object(
                'metadata', NEW.metadata,
                'result', NEW.result,
                'task_metadata', NEW.task_metadata
            )
        )
    WHERE task_id = NEW.task_id AND version = 1;

    RETURN NEW;
END;
$$;

--
--

CREATE OR REPLACE FUNCTION public.experiments_touch_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;

--
--

CREATE OR REPLACE FUNCTION public.external_taps_touch_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    AS $$ BEGIN NEW.updated_at = now(); RETURN NEW; END; $$;

--
--

CREATE OR REPLACE FUNCTION public.notify_pipeline_event() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    PERFORM pg_notify('pipeline_events', NEW.id::TEXT);
    RETURN NEW;
END;
$$;

--
--

CREATE OR REPLACE FUNCTION public.object_stores_touch_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;

--
--

CREATE OR REPLACE FUNCTION public.publishing_adapters_touch_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    AS $$ BEGIN NEW.updated_at = now(); RETURN NEW; END; $$;

--
--

CREATE OR REPLACE FUNCTION public.qa_gates_touch_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;

--
--

CREATE OR REPLACE FUNCTION public.retention_policies_touch_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    AS $$ BEGIN NEW.updated_at = now(); RETURN NEW; END; $$;

--
--

CREATE OR REPLACE FUNCTION public.webhook_endpoints_touch_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    AS $$ BEGIN NEW.updated_at = now(); RETURN NEW; END; $$;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
--

CREATE TABLE IF NOT EXISTS public.alert_dedup_state (
    fingerprint text NOT NULL,
    first_seen_at timestamp with time zone NOT NULL,
    last_seen_at timestamp with time zone NOT NULL,
    repeat_count integer DEFAULT 1 NOT NULL,
    summary_dispatched_at timestamp with time zone,
    severity text NOT NULL,
    source text NOT NULL,
    sample_message text NOT NULL
);

--
--

CREATE TABLE IF NOT EXISTS public.alert_events (
    id bigint NOT NULL,
    alertname text NOT NULL,
    status text NOT NULL,
    severity text,
    category text,
    labels jsonb DEFAULT '{}'::jsonb NOT NULL,
    annotations jsonb DEFAULT '{}'::jsonb NOT NULL,
    starts_at timestamp with time zone,
    ends_at timestamp with time zone,
    fingerprint text,
    received_at timestamp with time zone DEFAULT now() NOT NULL,
    dispatched_at timestamp with time zone,
    dispatch_result text
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.alert_events_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.alert_events_id_seq OWNED BY public.alert_events.id;

--
--

CREATE TABLE IF NOT EXISTS public.alert_log (
    id bigint NOT NULL,
    alert_action_id integer,
    pattern character varying(200) NOT NULL,
    trigger_detail text DEFAULT ''::text,
    action_taken character varying(50),
    result character varying(20) DEFAULT 'pending'::character varying,
    resolution_detail text DEFAULT ''::text,
    escalated boolean DEFAULT false,
    created_at timestamp with time zone DEFAULT now()
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.alert_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.alert_log_id_seq OWNED BY public.alert_log.id;

--
--

CREATE TABLE IF NOT EXISTS public.app_settings (
    id integer NOT NULL,
    key character varying(255) NOT NULL,
    value text DEFAULT ''::text,
    category character varying(100) DEFAULT 'general'::character varying,
    description text DEFAULT ''::text,
    is_secret boolean DEFAULT false,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    is_active boolean DEFAULT true NOT NULL
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.app_settings_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.app_settings_id_seq OWNED BY public.app_settings.id;

--
--

CREATE TABLE IF NOT EXISTS public.app_state (
    id character varying(200) NOT NULL,
    revision bigint,
    content text
);

--
--

CREATE TABLE IF NOT EXISTS public.audit_log (
    id bigint NOT NULL,
    "timestamp" timestamp with time zone DEFAULT now(),
    event_type character varying(50) NOT NULL,
    source character varying(50) NOT NULL,
    task_id character varying(255),
    details jsonb DEFAULT '{}'::jsonb,
    severity character varying(10) DEFAULT 'info'::character varying
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.audit_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.audit_log_id_seq OWNED BY public.audit_log.id;

--
--

CREATE TABLE IF NOT EXISTS public.auth_token (
    id character varying(255) NOT NULL,
    token_hash character varying(255),
    user_id bigint,
    expires_unix bigint
);

--
--

CREATE TABLE IF NOT EXISTS public.authors (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name character varying(255) NOT NULL,
    bio text,
    avatar_url character varying(500),
    created_at timestamp with time zone DEFAULT now()
);

--
--

CREATE TABLE IF NOT EXISTS public.brain_decisions (
    id integer NOT NULL,
    decision text NOT NULL,
    reasoning text NOT NULL,
    context jsonb DEFAULT '{}'::jsonb,
    outcome character varying(50),
    confidence double precision DEFAULT 0.5,
    created_at timestamp with time zone DEFAULT now()
);

--
--

ALTER TABLE public.brain_decisions ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.brain_decisions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

--
--

CREATE TABLE IF NOT EXISTS public.brain_knowledge (
    id integer NOT NULL,
    entity character varying(255) NOT NULL,
    attribute character varying(255) NOT NULL,
    value text NOT NULL,
    confidence double precision DEFAULT 1.0,
    source character varying(255),
    source_session character varying(100),
    tags text[] DEFAULT '{}'::text[],
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    expires_at timestamp with time zone
);

--
--

ALTER TABLE public.brain_knowledge ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.brain_knowledge_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

--
--

CREATE TABLE IF NOT EXISTS public.campaign_email_logs (
    id integer NOT NULL,
    subscriber_id integer NOT NULL,
    campaign_name character varying(255) NOT NULL,
    campaign_id integer,
    email_subject character varying(500),
    sent_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    delivery_status character varying(50),
    delivery_error text,
    opened boolean DEFAULT false,
    opened_at timestamp with time zone,
    clicked boolean DEFAULT false,
    clicked_at timestamp with time zone,
    bounce_type character varying(50),
    bounce_reason text
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.campaign_email_logs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.campaign_email_logs_id_seq OWNED BY public.campaign_email_logs.id;

--
--

CREATE TABLE IF NOT EXISTS public.capability_outcomes (
    id bigint NOT NULL,
    task_id text,
    template_slug text NOT NULL,
    node_name text NOT NULL,
    atom_name text,
    capability_tier text,
    model_used text,
    ok boolean NOT NULL,
    halted boolean DEFAULT false NOT NULL,
    failure_reason text,
    elapsed_ms integer DEFAULT 0 NOT NULL,
    quality_score numeric(5,2),
    metrics jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    niche_slug text,
    prompt_template_key text,
    prompt_template_version integer,
    variant_id uuid
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.capability_outcomes_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.capability_outcomes_id_seq OWNED BY public.capability_outcomes.id;

--
--

CREATE TABLE IF NOT EXISTS public.capability_registry (
    id character varying(100) NOT NULL,
    entity_type character varying(30) NOT NULL,
    name character varying(255) NOT NULL,
    description text,
    capabilities jsonb DEFAULT '{}'::jsonb,
    config jsonb DEFAULT '{}'::jsonb,
    health jsonb DEFAULT '{}'::jsonb,
    cost_profile jsonb DEFAULT '{}'::jsonb,
    performance jsonb DEFAULT '{}'::jsonb,
    last_heartbeat timestamp with time zone,
    status character varying(20) DEFAULT 'starting'::character varying,
    registered_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);

--
--

CREATE TABLE IF NOT EXISTS public.categories (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name character varying(255) NOT NULL,
    slug character varying(255) NOT NULL,
    description text,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP
);

--
--

CREATE TABLE IF NOT EXISTS public.checkpoint_blobs (
    thread_id text NOT NULL,
    checkpoint_ns text DEFAULT ''::text NOT NULL,
    channel text NOT NULL,
    version text NOT NULL,
    type text NOT NULL,
    blob bytea
);

--
--

CREATE TABLE IF NOT EXISTS public.checkpoint_writes (
    thread_id text NOT NULL,
    checkpoint_ns text DEFAULT ''::text NOT NULL,
    checkpoint_id text NOT NULL,
    task_id text NOT NULL,
    idx integer NOT NULL,
    channel text NOT NULL,
    type text,
    blob bytea NOT NULL,
    task_path text DEFAULT ''::text NOT NULL
);

--
--

CREATE TABLE IF NOT EXISTS public.checkpoints (
    thread_id text NOT NULL,
    checkpoint_ns text DEFAULT ''::text NOT NULL,
    checkpoint_id text NOT NULL,
    parent_checkpoint_id text,
    type text,
    checkpoint jsonb NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL
);

--
--

CREATE TABLE IF NOT EXISTS public.content_revisions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    task_id text NOT NULL,
    post_id uuid,
    revision_number integer DEFAULT 1 NOT NULL,
    content text NOT NULL,
    title text,
    word_count integer DEFAULT 0,
    quality_score numeric(5,2) DEFAULT NULL::numeric,
    change_summary text,
    change_type text,
    model_used text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

--
--

CREATE TABLE IF NOT EXISTS public.pipeline_distributions (
    id integer NOT NULL,
    task_id character varying NOT NULL,
    target character varying NOT NULL,
    status character varying DEFAULT 'pending'::character varying NOT NULL,
    external_id character varying,
    external_url character varying,
    post_id uuid,
    post_slug character varying,
    published_at timestamp with time zone,
    error_message text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

--
--

CREATE TABLE IF NOT EXISTS public.pipeline_gate_history (
    id bigint NOT NULL,
    task_id text,
    post_id text,
    gate_name text NOT NULL,
    event_kind text NOT NULL,
    feedback text,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT pipeline_gate_history_one_id CHECK (((task_id IS NOT NULL) <> (post_id IS NOT NULL)))
);

--
--

CREATE TABLE IF NOT EXISTS public.pipeline_tasks (
    id integer NOT NULL,
    task_id character varying NOT NULL,
    task_type character varying DEFAULT 'blog_post'::character varying NOT NULL,
    topic character varying NOT NULL,
    status character varying DEFAULT 'pending'::character varying NOT NULL,
    stage character varying DEFAULT 'pending'::character varying NOT NULL,
    site_id uuid,
    style character varying DEFAULT 'technical'::character varying,
    tone character varying DEFAULT 'professional'::character varying,
    target_length integer DEFAULT 1500,
    category character varying,
    primary_keyword character varying,
    target_audience character varying,
    percentage integer DEFAULT 0,
    message text,
    model_used character varying,
    error_message text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    started_at timestamp with time zone,
    completed_at timestamp with time zone,
    scheduled_at timestamp with time zone,
    seed_url text,
    awaiting_gate character varying(64),
    gate_artifact jsonb DEFAULT '{}'::jsonb NOT NULL,
    gate_paused_at timestamp with time zone,
    niche_slug text,
    topic_batch_id uuid,
    template_slug text,
    auto_cancelled_at timestamp with time zone,
    retry_count integer DEFAULT 0 NOT NULL
);

--
--

CREATE TABLE IF NOT EXISTS public.pipeline_versions (
    id integer NOT NULL,
    task_id character varying NOT NULL,
    version integer DEFAULT 1 NOT NULL,
    title character varying,
    content text,
    excerpt text,
    featured_image_url character varying,
    seo_title character varying,
    seo_description character varying,
    seo_keywords character varying,
    quality_score integer,
    qa_feedback text,
    models_used_by_phase jsonb DEFAULT '{}'::jsonb,
    stage_data jsonb DEFAULT '{}'::jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

--
--

CREATE OR REPLACE VIEW public.content_tasks AS
 SELECT pt.id,
    pt.task_id,
    pt.task_type,
    pt.task_type AS content_type,
    pv.title,
    pt.topic,
    pt.status,
    pt.stage,
    pt.style,
    pt.tone,
    pt.target_length,
    pt.category,
    pt.primary_keyword,
    pt.target_audience,
    pv.content,
    pv.excerpt,
    pv.featured_image_url,
    pv.quality_score,
    pv.qa_feedback,
    pv.seo_title,
    pv.seo_description,
    pv.seo_keywords,
    pt.percentage,
    pt.message,
    pt.model_used,
    pt.error_message,
    pv.models_used_by_phase,
    COALESCE((pv.stage_data -> 'metadata'::text), pv.stage_data) AS metadata,
    COALESCE((pv.stage_data -> 'result'::text), pv.stage_data) AS result,
    COALESCE((pv.stage_data -> 'task_metadata'::text), pv.stage_data) AS task_metadata,
    pt.site_id,
    pt.created_at,
    pt.updated_at,
    pt.started_at,
    pt.completed_at,
    ( SELECT (
                CASE
                    WHEN (pgh.event_kind = 'approved'::text) THEN 'approved'::text
                    WHEN (pgh.event_kind ~~ 'rejected%'::text) THEN 'rejected'::text
                    ELSE pgh.event_kind
                END)::character varying AS event_kind
           FROM public.pipeline_gate_history pgh
          WHERE ((pgh.task_id = (pt.task_id)::text) AND (pgh.event_kind = ANY (ARRAY['approved'::text, 'rejected'::text, 'rejected_retry'::text, 'rejected_final'::text])))
          ORDER BY pgh.created_at DESC
         LIMIT 1) AS approval_status,
    ( SELECT ((pgh.metadata ->> 'reviewer'::text))::character varying AS "varchar"
           FROM public.pipeline_gate_history pgh
          WHERE ((pgh.task_id = (pt.task_id)::text) AND (pgh.event_kind = ANY (ARRAY['approved'::text, 'rejected'::text, 'rejected_retry'::text, 'rejected_final'::text])))
          ORDER BY pgh.created_at DESC
         LIMIT 1) AS approved_by,
    ( SELECT pgh.feedback
           FROM public.pipeline_gate_history pgh
          WHERE ((pgh.task_id = (pt.task_id)::text) AND (pgh.event_kind = ANY (ARRAY['approved'::text, 'rejected'::text, 'rejected_retry'::text, 'rejected_final'::text])))
          ORDER BY pgh.created_at DESC
         LIMIT 1) AS human_feedback,
    ( SELECT pd.post_id
           FROM public.pipeline_distributions pd
          WHERE (((pd.task_id)::text = (pt.task_id)::text) AND ((pd.target)::text = 'gladlabs.io'::text))
         LIMIT 1) AS post_id,
    ( SELECT pd.post_slug
           FROM public.pipeline_distributions pd
          WHERE (((pd.task_id)::text = (pt.task_id)::text) AND ((pd.target)::text = 'gladlabs.io'::text))
         LIMIT 1) AS post_slug,
    ( SELECT pd.published_at
           FROM public.pipeline_distributions pd
          WHERE (((pd.task_id)::text = (pt.task_id)::text) AND ((pd.target)::text = 'gladlabs.io'::text))
         LIMIT 1) AS published_at,
    pt.awaiting_gate,
    pt.gate_artifact,
    pt.gate_paused_at,
    pt.niche_slug,
    pt.topic_batch_id
   FROM (public.pipeline_tasks pt
     LEFT JOIN public.pipeline_versions pv ON ((((pv.task_id)::text = (pt.task_id)::text) AND (pv.version = ( SELECT max(pipeline_versions.version) AS max
           FROM public.pipeline_versions
          WHERE ((pipeline_versions.task_id)::text = (pt.task_id)::text))))));

--
--

CREATE TABLE IF NOT EXISTS public.content_validator_rules (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name text NOT NULL,
    enabled boolean DEFAULT true NOT NULL,
    severity text DEFAULT 'warning'::text NOT NULL,
    threshold jsonb DEFAULT '{}'::jsonb NOT NULL,
    applies_to_niches text[],
    description text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT content_validator_rules_severity_check CHECK ((severity = ANY (ARRAY['info'::text, 'warning'::text, 'error'::text])))
);

--
--

CREATE TABLE IF NOT EXISTS public.cost_logs (
    id integer NOT NULL,
    task_id character varying(255),
    user_id uuid,
    phase character varying(50) NOT NULL,
    model character varying(100) NOT NULL,
    provider character varying(50) NOT NULL,
    input_tokens integer DEFAULT 0,
    output_tokens integer DEFAULT 0,
    total_tokens integer DEFAULT 0,
    cost_usd numeric(10,6),
    quality_score double precision,
    duration_ms integer,
    success boolean DEFAULT true,
    error_message text,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    cost_type character varying(30) DEFAULT 'inference'::character varying,
    electricity_kwh numeric(12,8)
);

--
--

ALTER TABLE public.cost_logs ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.cost_logs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

--
--

CREATE TABLE IF NOT EXISTS public.decision_log (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    decision_type text NOT NULL,
    decision_point text NOT NULL,
    context jsonb DEFAULT '{}'::jsonb NOT NULL,
    decision jsonb DEFAULT '{}'::jsonb NOT NULL,
    outcome jsonb,
    outcome_recorded_at timestamp with time zone,
    task_id text,
    post_id uuid,
    model_used text,
    duration_ms integer,
    cost_usd numeric(10,6) DEFAULT 0,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

--
--

CREATE TABLE IF NOT EXISTS public.discovery_runs (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    niche_id uuid NOT NULL,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    finished_at timestamp with time zone,
    candidates_generated integer,
    candidates_carried_forward integer,
    batch_id uuid,
    error text
);

--
--

CREATE TABLE IF NOT EXISTS public.electricity_costs (
    id bigint NOT NULL,
    "timestamp" timestamp with time zone DEFAULT now(),
    avg_watts double precision,
    kwh double precision,
    cost_usd double precision,
    rate_per_kwh double precision DEFAULT 0.14770
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.electricity_costs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.electricity_costs_id_seq OWNED BY public.electricity_costs.id;

--
--

CREATE TABLE IF NOT EXISTS public.embeddings (
    id bigint NOT NULL,
    source_table character varying(50) NOT NULL,
    source_id character varying(255) NOT NULL,
    content_hash character varying(64) NOT NULL,
    chunk_index integer DEFAULT 0,
    text_preview character varying(500) NOT NULL,
    embedding_model character varying(100) NOT NULL,
    embedding public.vector(768) NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    writer character varying(50),
    origin_path text,
    is_summary boolean DEFAULT false NOT NULL,
    text_search tsvector GENERATED ALWAYS AS (to_tsvector('simple'::regconfig, (COALESCE(text_preview, ''::character varying))::text)) STORED
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.embeddings_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.embeddings_id_seq OWNED BY public.embeddings.id;

--
--

CREATE TABLE IF NOT EXISTS public.experiment_variants (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    experiment_id uuid NOT NULL,
    label text NOT NULL,
    weight numeric DEFAULT 1.0 NOT NULL,
    prompt_template_key text,
    prompt_template_version integer,
    writer_model text,
    rag_config jsonb DEFAULT '{}'::jsonb NOT NULL,
    active boolean DEFAULT true NOT NULL,
    paused_at timestamp with time zone,
    paused_reason text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

--
--

CREATE TABLE IF NOT EXISTS public.experiments (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    key text NOT NULL,
    niche_slug text NOT NULL,
    description text DEFAULT ''::text NOT NULL,
    status text DEFAULT 'draft'::text NOT NULL,
    objective_function text DEFAULT 'views_7d'::text NOT NULL,
    min_approval_rate_pct integer DEFAULT 50 NOT NULL,
    min_posts_before_pause integer DEFAULT 10 NOT NULL,
    cost_alert_multiplier numeric DEFAULT 3.0 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    activated_at timestamp with time zone,
    concluded_at timestamp with time zone,
    conclusion_note text,
    winner_variant_label text,
    CONSTRAINT experiments_objective_function_check CHECK ((objective_function = ANY (ARRAY['views_7d'::text, 'views_24h'::text, 'approval_rate'::text, 'views_per_dollar'::text, 'composite_score'::text]))),
    CONSTRAINT experiments_status_check CHECK ((status = ANY (ARRAY['draft'::text, 'active'::text, 'paused'::text, 'concluded'::text])))
);

--
--

CREATE TABLE IF NOT EXISTS public.page_views (
    id bigint NOT NULL,
    path character varying(500),
    slug character varying(500),
    referrer character varying(1000),
    user_agent text,
    created_at timestamp with time zone DEFAULT now()
);

--
--

CREATE TABLE IF NOT EXISTS public.posts (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    title text,
    slug character varying(500),
    status character varying(50),
    category_id uuid,
    view_count integer DEFAULT 0,
    published_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    site_id uuid,
    seo_description character varying(500),
    seo_keywords character varying(1000),
    cover_image_url character varying(1000),
    created_by character varying(255),
    updated_by character varying(255),
    content text,
    excerpt text,
    seo_title character varying(255),
    featured_image_url character varying(1000),
    featured_image_data jsonb DEFAULT '{}'::jsonb,
    metadata jsonb DEFAULT '{}'::jsonb,
    author character varying(255),
    reading_time integer,
    word_count integer,
    author_id uuid,
    tag_ids uuid[] DEFAULT '{}'::uuid[],
    preview_token text,
    distributed_at timestamp with time zone,
    awaiting_gate character varying(64),
    gate_artifact jsonb DEFAULT '{}'::jsonb NOT NULL,
    gate_paused_at timestamp with time zone,
    media_to_generate text[] DEFAULT '{}'::text[] NOT NULL,
    cli_idempotency_key text,
    video_shot_list jsonb
);

--
--

CREATE TABLE IF NOT EXISTS public.published_post_edit_metrics (
    id bigint NOT NULL,
    task_id text NOT NULL,
    post_id bigint,
    niche_slug text,
    category text,
    approver text NOT NULL,
    pre_approve_hash text NOT NULL,
    post_approve_hash text NOT NULL,
    char_diff_count integer DEFAULT 0 NOT NULL,
    line_diff_count integer DEFAULT 0 NOT NULL,
    pre_approve_len integer DEFAULT 0 NOT NULL,
    post_approve_len integer DEFAULT 0 NOT NULL,
    approve_method text,
    approved_at timestamp with time zone DEFAULT now() NOT NULL,
    metrics jsonb DEFAULT '{}'::jsonb NOT NULL,
    model_used text,
    prompt_template_key text,
    prompt_template_version integer
);

--
--

CREATE TABLE IF NOT EXISTS public.routing_outcomes (
    id bigint NOT NULL,
    task_id character varying(255),
    task_type character varying(100),
    task_category character varying(50),
    worker_id character varying(100),
    model_used character varying(200),
    compute_tier character varying(20),
    estimated_cost double precision,
    actual_cost double precision,
    quality_score double precision,
    duration_ms integer,
    success boolean,
    created_at timestamp with time zone DEFAULT now(),
    niche_slug character varying(100),
    prompt_template_key character varying(200),
    prompt_template_version integer
);

--
--

CREATE OR REPLACE VIEW public.lab_outcomes_v1 AS
 SELECT co.task_id,
    co.niche_slug,
    co.template_slug,
    co.atom_name,
    co.model_used,
    co.prompt_template_key,
    co.prompt_template_version,
    co.ok AS atom_ok,
    co.halted AS atom_halted,
    co.quality_score AS atom_quality_score,
    co.elapsed_ms,
    co.created_at AS run_at,
    ro.actual_cost,
    ro.estimated_cost,
    ro.compute_tier,
    ro.success AS routing_success,
    pem.approver,
    pem.char_diff_count,
    pem.line_diff_count,
    pem.pre_approve_len,
    pem.post_approve_len,
    pem.approve_method,
    pem.approved_at,
    pv_count.views_24h AS views_24h_post_publish,
    pv_count.views_7d AS views_7d_post_publish,
    ev.label AS variant_label,
    ev.id AS variant_id,
    e.key AS experiment_key,
    e.status AS experiment_status,
    e.objective_function AS experiment_objective_function
   FROM (((((public.capability_outcomes co
     LEFT JOIN public.routing_outcomes ro ON (((ro.task_id)::text = co.task_id)))
     LEFT JOIN public.published_post_edit_metrics pem ON ((pem.task_id = co.task_id)))
     LEFT JOIN LATERAL ( SELECT count(*) FILTER (WHERE ((pv.created_at >= pem.approved_at) AND (pv.created_at <= (pem.approved_at + '24:00:00'::interval)))) AS views_24h,
            count(*) FILTER (WHERE ((pv.created_at >= pem.approved_at) AND (pv.created_at <= (pem.approved_at + '7 days'::interval)))) AS views_7d
           FROM (public.page_views pv
             JOIN public.posts p ON (((p.slug)::text = (pv.slug)::text)))
          WHERE ((pem.approved_at IS NOT NULL) AND ((p.metadata ->> 'pipeline_task_id'::text) = co.task_id))) pv_count ON (true))
     LEFT JOIN public.experiment_variants ev ON ((ev.id = co.variant_id)))
     LEFT JOIN public.experiments e ON ((e.id = ev.experiment_id)))
  WHERE (co.created_at > (now() - '90 days'::interval));

--
--

COMMENT ON VIEW public.lab_outcomes_v1 IS 'Unified read surface for the content R&D lab — joins capability_outcomes + routing_outcomes + published_post_edit_metrics + page_views per task. Phase 0 (2026-05-28). Bandit/dashboards/learnings digest read from this view.';

--
--

CREATE OR REPLACE VIEW public.experiment_variant_scorecard_v1 AS
 SELECT e.id AS experiment_id,
    e.key AS experiment_key,
    e.niche_slug,
    e.status AS experiment_status,
    e.objective_function,
    ev.id AS variant_id,
    ev.label AS variant_label,
    ev.weight,
    ev.active AS variant_active,
    ev.paused_at,
    ev.paused_reason,
    count(DISTINCT lo.task_id) AS posts_attempted,
    count(DISTINCT lo.task_id) FILTER (WHERE (lo.approver IS NOT NULL)) AS posts_approved,
    round((((count(DISTINCT lo.task_id) FILTER (WHERE (lo.approver IS NOT NULL)))::numeric / (NULLIF(count(DISTINCT lo.task_id), 0))::numeric) * (100)::numeric), 1) AS approval_rate_pct,
    avg(((lo.char_diff_count)::numeric / (NULLIF(lo.pre_approve_len, 0))::numeric)) FILTER (WHERE (lo.approver IS NOT NULL)) AS avg_edit_distance_pct,
    avg(lo.views_24h_post_publish) AS avg_views_24h,
    avg(lo.views_7d_post_publish) AS avg_views_7d,
    avg(lo.actual_cost) AS avg_cost_per_post,
    sum(lo.actual_cost) AS total_cost
   FROM ((public.experiments e
     JOIN public.experiment_variants ev ON ((ev.experiment_id = e.id)))
     LEFT JOIN public.lab_outcomes_v1 lo ON ((lo.variant_id = ev.id)))
  GROUP BY e.id, e.key, e.niche_slug, e.status, e.objective_function, ev.id, ev.label, ev.weight, ev.active, ev.paused_at, ev.paused_reason;

--
--

COMMENT ON VIEW public.experiment_variant_scorecard_v1 IS 'Per-variant rollup for active+concluded experiments. Read by the poindexter experiments status CLI (PR 3) and the Grafana panels (PR 4). objective_function tells the consumer which column to rank on. Phase 1 ranks manually; Phase 2 bandit reads this directly.';

--
--

CREATE TABLE IF NOT EXISTS public.external_metrics (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    source text NOT NULL,
    metric_name text NOT NULL,
    metric_value numeric(14,4) NOT NULL,
    dimensions jsonb DEFAULT '{}'::jsonb,
    post_id uuid,
    slug text,
    date date NOT NULL,
    fetched_at timestamp with time zone DEFAULT now() NOT NULL
);

--
--

CREATE TABLE IF NOT EXISTS public.external_taps (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name text NOT NULL,
    handler_name text NOT NULL,
    tap_type text NOT NULL,
    target_table text,
    record_handler text,
    schedule text,
    config jsonb DEFAULT '{}'::jsonb NOT NULL,
    state jsonb DEFAULT '{}'::jsonb NOT NULL,
    enabled boolean DEFAULT false NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    last_run_at timestamp with time zone,
    last_run_duration_ms integer,
    last_run_status text,
    last_run_records bigint,
    last_error text,
    total_runs bigint DEFAULT 0 NOT NULL,
    total_records bigint DEFAULT 0 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

--
--

CREATE TABLE IF NOT EXISTS public.fact_overrides (
    id integer NOT NULL,
    pattern text NOT NULL,
    correct_fact text NOT NULL,
    category character varying(50) DEFAULT 'hardware'::character varying NOT NULL,
    severity character varying(20) DEFAULT 'critical'::character varying NOT NULL,
    active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.fact_overrides_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.fact_overrides_id_seq OWNED BY public.fact_overrides.id;

--
--

CREATE TABLE IF NOT EXISTS public.financial_entries (
    id integer NOT NULL,
    entry_type character varying(50) NOT NULL,
    amount numeric(15,2) NOT NULL,
    currency character varying(3) DEFAULT 'USD'::character varying,
    description text,
    category character varying(100),
    date date DEFAULT CURRENT_DATE NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.financial_entries_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.financial_entries_id_seq OWNED BY public.financial_entries.id;

--
--

CREATE TABLE IF NOT EXISTS public.gpu_metrics (
    id bigint NOT NULL,
    "timestamp" timestamp with time zone DEFAULT now(),
    utilization double precision,
    temperature double precision,
    power_draw double precision,
    memory_used double precision,
    memory_total double precision,
    fan_speed double precision,
    clock_graphics double precision,
    clock_memory double precision
);

--
--

CREATE TABLE IF NOT EXISTS public.gpu_metrics_hourly (
    bucket_start timestamp with time zone NOT NULL,
    avg_utilization double precision,
    peak_utilization double precision,
    avg_temperature double precision,
    peak_temperature double precision,
    avg_power_draw double precision,
    peak_power_draw double precision,
    avg_memory_used double precision,
    peak_memory_used double precision,
    avg_fan_speed double precision,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.gpu_metrics_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.gpu_metrics_id_seq OWNED BY public.gpu_metrics.id;

--
--

CREATE TABLE IF NOT EXISTS public.gpu_task_sessions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    task_id text NOT NULL,
    phase text NOT NULL,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    ended_at timestamp with time zone,
    duration_seconds numeric(8,2) DEFAULT NULL::numeric,
    gpu_model text,
    avg_utilization_pct numeric(5,2) DEFAULT NULL::numeric,
    avg_power_watts numeric(6,1) DEFAULT NULL::numeric,
    peak_power_watts numeric(6,1) DEFAULT NULL::numeric,
    vram_used_mb integer,
    kwh_consumed numeric(8,4) DEFAULT NULL::numeric,
    electricity_rate_kwh numeric(6,4) DEFAULT 0.12,
    electricity_cost_usd numeric(10,6) DEFAULT NULL::numeric,
    model_name text,
    tokens_generated integer DEFAULT 0
);

--
--

CREATE TABLE IF NOT EXISTS public.internal_topic_candidates (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    batch_id uuid NOT NULL,
    niche_id uuid NOT NULL,
    source_kind text NOT NULL,
    primary_ref text NOT NULL,
    supporting_refs jsonb DEFAULT '[]'::jsonb NOT NULL,
    distilled_topic text NOT NULL,
    distilled_angle text NOT NULL,
    score numeric NOT NULL,
    score_breakdown jsonb DEFAULT '{}'::jsonb NOT NULL,
    rank_in_batch integer NOT NULL,
    operator_rank integer,
    operator_edited_topic text,
    operator_edited_angle text,
    decay_factor numeric DEFAULT 1.0 NOT NULL,
    carried_from_batch_id uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT internal_topic_candidates_source_kind_check CHECK ((source_kind = ANY (ARRAY['claude_session'::text, 'brain_knowledge'::text, 'audit_event'::text, 'git_commit'::text, 'decision_log'::text, 'memory_file'::text, 'post_history'::text])))
);

--
--

CREATE TABLE IF NOT EXISTS public.media_approvals (
    post_id uuid NOT NULL,
    medium text NOT NULL,
    status text DEFAULT 'pending'::text NOT NULL,
    decided_at timestamp with time zone,
    decided_by text,
    notes text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    quality_score numeric,
    quality_signals jsonb,
    quality_evaluated_at timestamp with time zone,
    CONSTRAINT media_approvals_medium_check CHECK ((medium = ANY (ARRAY['podcast'::text, 'video'::text, 'video_short'::text]))),
    CONSTRAINT media_approvals_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'approved'::text, 'rejected'::text])))
);

--
--

CREATE TABLE IF NOT EXISTS public.media_assets (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid,
    site_id uuid,
    type character varying(30) NOT NULL,
    source character varying(30) NOT NULL,
    storage_provider character varying(30),
    url character varying(1000),
    storage_path character varying(1000),
    thumbnail_url character varying(1000),
    title character varying(500),
    description text,
    alt_text character varying(500),
    metadata jsonb DEFAULT '{}'::jsonb,
    ai_metadata jsonb DEFAULT '{}'::jsonb,
    task_id character varying(255),
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    post_id uuid,
    provider_plugin character varying(128),
    width integer,
    height integer,
    duration_ms integer,
    file_size_bytes bigint,
    mime_type character varying(64),
    cost_usd numeric(10,6),
    electricity_kwh numeric(12,8),
    platform_video_ids jsonb DEFAULT '{}'::jsonb NOT NULL
);

--
--

CREATE TABLE IF NOT EXISTS public.model_performance (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    model_name text NOT NULL,
    task_type text NOT NULL,
    task_id text,
    quality_score numeric(5,2) DEFAULT NULL::numeric,
    generation_time_ms integer,
    tokens_input integer DEFAULT 0,
    tokens_output integer DEFAULT 0,
    cost_usd numeric(10,6) DEFAULT 0,
    gpu_watts_avg numeric(6,1) DEFAULT NULL::numeric,
    electricity_cost_usd numeric(10,6) DEFAULT 0,
    human_approved boolean,
    post_published boolean,
    post_performance_score numeric(5,2) DEFAULT NULL::numeric,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

--
--

CREATE TABLE IF NOT EXISTS public.module_schema_migrations (
    id integer NOT NULL,
    module_name character varying(64) NOT NULL,
    migration_name character varying(255) NOT NULL,
    applied_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.module_schema_migrations_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.module_schema_migrations_id_seq OWNED BY public.module_schema_migrations.id;

--
--

CREATE TABLE IF NOT EXISTS public.newsletter_subscribers (
    id integer NOT NULL,
    email character varying(255) NOT NULL,
    first_name character varying(100),
    last_name character varying(100),
    company character varying(255),
    interest_categories jsonb,
    subscribed_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    ip_address character varying(45),
    user_agent text,
    verified boolean DEFAULT false,
    verification_token character varying(255),
    verified_at timestamp with time zone,
    unsubscribed_at timestamp with time zone,
    unsubscribe_reason text,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    marketing_consent boolean DEFAULT false,
    unsubscribe_token text NOT NULL
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.newsletter_subscribers_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.newsletter_subscribers_id_seq OWNED BY public.newsletter_subscribers.id;

--
--

CREATE TABLE IF NOT EXISTS public.niche_goals (
    niche_id uuid NOT NULL,
    goal_type text NOT NULL,
    weight_pct integer NOT NULL,
    CONSTRAINT niche_goals_goal_type_check CHECK ((goal_type = ANY (ARRAY['TRAFFIC'::text, 'EDUCATION'::text, 'BRAND'::text, 'AUTHORITY'::text, 'REVENUE'::text, 'COMMUNITY'::text, 'NICHE_DEPTH'::text]))),
    CONSTRAINT niche_goals_weight_pct_check CHECK (((weight_pct >= 0) AND (weight_pct <= 100)))
);

--
--

CREATE TABLE IF NOT EXISTS public.niche_sources (
    niche_id uuid NOT NULL,
    source_name text NOT NULL,
    enabled boolean DEFAULT true NOT NULL,
    weight_pct integer NOT NULL,
    CONSTRAINT niche_sources_weight_pct_check CHECK (((weight_pct >= 0) AND (weight_pct <= 100)))
);

--
--

CREATE TABLE IF NOT EXISTS public.niches (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    slug text NOT NULL,
    name text NOT NULL,
    active boolean DEFAULT true NOT NULL,
    target_audience_tags text[] DEFAULT '{}'::text[] NOT NULL,
    writer_prompt_override text,
    batch_size integer DEFAULT 5 NOT NULL,
    discovery_cadence_minute_floor integer DEFAULT 60 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    default_media_to_generate text[] DEFAULT ARRAY[]::text[] NOT NULL,
    default_template_slug text,
    CONSTRAINT niches_batch_size_check CHECK (((batch_size >= 1) AND (batch_size <= 20))),
    CONSTRAINT niches_discovery_cadence_minute_floor_check CHECK ((discovery_cadence_minute_floor >= 1))
);

--
--

CREATE TABLE IF NOT EXISTS public.notification (
    id bigint NOT NULL,
    user_id bigint NOT NULL,
    repo_id bigint NOT NULL,
    status smallint NOT NULL,
    source smallint NOT NULL,
    issue_id bigint NOT NULL,
    commit_id character varying(255),
    comment_id bigint,
    updated_by bigint NOT NULL,
    created_unix bigint NOT NULL,
    updated_unix bigint NOT NULL
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.notification_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.notification_id_seq OWNED BY public.notification.id;

--
--

CREATE TABLE IF NOT EXISTS public.oauth_authorization_codes (
    code text NOT NULL,
    client_id character varying(64) NOT NULL,
    code_challenge text NOT NULL,
    redirect_uri text NOT NULL,
    redirect_uri_provided_explicitly boolean DEFAULT true NOT NULL,
    scopes text[] DEFAULT '{}'::text[] NOT NULL,
    resource text,
    state text,
    expires_at timestamp with time zone NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

--
--

CREATE TABLE IF NOT EXISTS public.oauth_clients (
    client_id character varying(64) NOT NULL,
    name text NOT NULL,
    scopes text[] DEFAULT '{}'::text[] NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    last_used_at timestamp with time zone,
    revoked_at timestamp with time zone,
    redirect_uris text[] DEFAULT '{}'::text[] NOT NULL,
    grant_types text[] DEFAULT '{authorization_code,refresh_token,client_credentials}'::text[] NOT NULL,
    response_types text[] DEFAULT '{code}'::text[] NOT NULL,
    token_endpoint_auth_method text DEFAULT 'client_secret_post'::text NOT NULL,
    client_metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    client_secret_encrypted text NOT NULL
);

--
--

CREATE TABLE IF NOT EXISTS public.object_stores (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name text NOT NULL,
    provider text NOT NULL,
    endpoint_url text,
    bucket text NOT NULL,
    public_url text,
    credentials_ref text DEFAULT 'storage_credentials'::text NOT NULL,
    cache_busting_strategy text DEFAULT 'none'::text NOT NULL,
    cache_busting_config jsonb DEFAULT '{}'::jsonb NOT NULL,
    enabled boolean DEFAULT false NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    last_upload_at timestamp with time zone,
    last_upload_status text,
    last_error text,
    total_uploads bigint DEFAULT 0 NOT NULL,
    total_failures bigint DEFAULT 0 NOT NULL,
    total_bytes_uploaded bigint DEFAULT 0 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

--
--

CREATE TABLE IF NOT EXISTS public.operator_notes (
    id bigint NOT NULL,
    niche_slug text NOT NULL,
    note_date date DEFAULT CURRENT_DATE NOT NULL,
    note text NOT NULL,
    mood text,
    created_by text DEFAULT 'operator'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.operator_notes_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.operator_notes_id_seq OWNED BY public.operator_notes.id;

--
--

CREATE TABLE IF NOT EXISTS public.orchestrator_training_data (
    id integer NOT NULL,
    execution_id character varying(255) NOT NULL,
    user_request text NOT NULL,
    intent character varying(100),
    business_state jsonb DEFAULT '{}'::jsonb,
    execution_plan text,
    execution_result text,
    quality_score numeric(3,2) DEFAULT 0.5 NOT NULL,
    success boolean DEFAULT false NOT NULL,
    tags text[] DEFAULT ARRAY[]::text[],
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    source_agent character varying(100),
    source_model character varying(100),
    execution_time_ms integer
);

--
--

ALTER TABLE public.orchestrator_training_data ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.orchestrator_training_data_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.page_views_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.page_views_id_seq OWNED BY public.page_views.id;

--
--

CREATE TABLE IF NOT EXISTS public.pipeline_atoms (
    id bigint NOT NULL,
    name text NOT NULL,
    type text NOT NULL,
    version text NOT NULL,
    description text DEFAULT ''::text NOT NULL,
    capability_tier text,
    cost_class text DEFAULT 'compute'::text NOT NULL,
    meta jsonb DEFAULT '{}'::jsonb NOT NULL,
    last_seen_at timestamp with time zone DEFAULT now() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.pipeline_atoms_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.pipeline_atoms_id_seq OWNED BY public.pipeline_atoms.id;

--
--

CREATE SEQUENCE IF NOT EXISTS public.pipeline_distributions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.pipeline_distributions_id_seq OWNED BY public.pipeline_distributions.id;

--
--

CREATE SEQUENCE IF NOT EXISTS public.pipeline_gate_history_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.pipeline_gate_history_id_seq OWNED BY public.pipeline_gate_history.id;

--
--

CREATE SEQUENCE IF NOT EXISTS public.pipeline_tasks_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.pipeline_tasks_id_seq OWNED BY public.pipeline_tasks.id;

--
--

CREATE OR REPLACE VIEW public.pipeline_tasks_view AS
 SELECT pt.id,
    pt.task_id,
    pt.task_type,
    pt.task_type AS content_type,
    pv.title,
    pt.topic,
    pt.status,
    pt.stage,
    pt.style,
    pt.tone,
    pt.target_length,
    pt.category,
    pt.primary_keyword,
    pt.target_audience,
    pv.content,
    pv.excerpt,
    pv.featured_image_url,
    pv.quality_score,
    pv.qa_feedback,
    pv.seo_title,
    pv.seo_description,
    pv.seo_keywords,
    pt.percentage,
    pt.message,
    pt.model_used,
    pt.error_message,
    pv.models_used_by_phase,
    COALESCE((pv.stage_data -> 'metadata'::text), pv.stage_data) AS metadata,
    COALESCE((pv.stage_data -> 'result'::text), pv.stage_data) AS result,
    COALESCE((pv.stage_data -> 'task_metadata'::text), pv.stage_data) AS task_metadata,
    pt.site_id,
    pt.created_at,
    pt.updated_at,
    pt.started_at,
    pt.completed_at,
    ( SELECT (
                CASE
                    WHEN (pgh.event_kind = 'approved'::text) THEN 'approved'::text
                    WHEN (pgh.event_kind ~~ 'rejected%'::text) THEN 'rejected'::text
                    ELSE pgh.event_kind
                END)::character varying AS event_kind
           FROM public.pipeline_gate_history pgh
          WHERE ((pgh.task_id = (pt.task_id)::text) AND (pgh.event_kind = ANY (ARRAY['approved'::text, 'rejected'::text, 'rejected_retry'::text, 'rejected_final'::text])))
          ORDER BY pgh.created_at DESC
         LIMIT 1) AS approval_status,
    ( SELECT ((pgh.metadata ->> 'reviewer'::text))::character varying AS "varchar"
           FROM public.pipeline_gate_history pgh
          WHERE ((pgh.task_id = (pt.task_id)::text) AND (pgh.event_kind = ANY (ARRAY['approved'::text, 'rejected'::text, 'rejected_retry'::text, 'rejected_final'::text])))
          ORDER BY pgh.created_at DESC
         LIMIT 1) AS approved_by,
    ( SELECT pgh.feedback
           FROM public.pipeline_gate_history pgh
          WHERE ((pgh.task_id = (pt.task_id)::text) AND (pgh.event_kind = ANY (ARRAY['approved'::text, 'rejected'::text, 'rejected_retry'::text, 'rejected_final'::text])))
          ORDER BY pgh.created_at DESC
         LIMIT 1) AS human_feedback,
    ( SELECT pd.post_id
           FROM public.pipeline_distributions pd
          WHERE (((pd.task_id)::text = (pt.task_id)::text) AND ((pd.target)::text = 'gladlabs.io'::text))
         LIMIT 1) AS post_id,
    ( SELECT pd.post_slug
           FROM public.pipeline_distributions pd
          WHERE (((pd.task_id)::text = (pt.task_id)::text) AND ((pd.target)::text = 'gladlabs.io'::text))
         LIMIT 1) AS post_slug,
    ( SELECT pd.published_at
           FROM public.pipeline_distributions pd
          WHERE (((pd.task_id)::text = (pt.task_id)::text) AND ((pd.target)::text = 'gladlabs.io'::text))
         LIMIT 1) AS published_at
   FROM (public.pipeline_tasks pt
     LEFT JOIN public.pipeline_versions pv ON ((((pv.task_id)::text = (pt.task_id)::text) AND (pv.version = ( SELECT max(pipeline_versions.version) AS max
           FROM public.pipeline_versions
          WHERE ((pipeline_versions.task_id)::text = (pt.task_id)::text))))));

--
--

CREATE TABLE IF NOT EXISTS public.pipeline_templates (
    id bigint NOT NULL,
    slug text NOT NULL,
    name text NOT NULL,
    description text DEFAULT ''::text NOT NULL,
    version integer DEFAULT 1 NOT NULL,
    active boolean DEFAULT true NOT NULL,
    graph_def jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_by text DEFAULT 'operator'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.pipeline_templates_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.pipeline_templates_id_seq OWNED BY public.pipeline_templates.id;

--
--

CREATE SEQUENCE IF NOT EXISTS public.pipeline_versions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.pipeline_versions_id_seq OWNED BY public.pipeline_versions.id;

--
--

--
--

CREATE TABLE IF NOT EXISTS public.post_performance (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    post_id uuid NOT NULL,
    slug text NOT NULL,
    views_1d integer DEFAULT 0,
    views_7d integer DEFAULT 0,
    views_30d integer DEFAULT 0,
    views_total integer DEFAULT 0,
    avg_time_on_page_seconds numeric(8,2) DEFAULT NULL::numeric,
    scroll_depth_pct numeric(5,2) DEFAULT NULL::numeric,
    bounce_rate numeric(5,2) DEFAULT NULL::numeric,
    shares_twitter integer DEFAULT 0,
    shares_linkedin integer DEFAULT 0,
    shares_other integer DEFAULT 0,
    comments_count integer DEFAULT 0,
    google_impressions integer DEFAULT 0,
    google_clicks integer DEFAULT 0,
    google_avg_position numeric(6,2) DEFAULT NULL::numeric,
    top_keywords text[] DEFAULT '{}'::text[],
    affiliate_clicks integer DEFAULT 0,
    affiliate_revenue_usd numeric(10,2) DEFAULT 0,
    direct_revenue_usd numeric(10,2) DEFAULT 0,
    measured_at timestamp with time zone DEFAULT now() NOT NULL,
    period text DEFAULT 'snapshot'::text
);

--
--

CREATE TABLE IF NOT EXISTS public.post_tags (
    post_id uuid NOT NULL,
    tag_id uuid NOT NULL
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.published_post_edit_metrics_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.published_post_edit_metrics_id_seq OWNED BY public.published_post_edit_metrics.id;

--
--

CREATE TABLE IF NOT EXISTS public.publishing_adapters (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name text NOT NULL,
    platform text NOT NULL,
    handler_name text NOT NULL,
    credentials_ref text,
    default_tags jsonb,
    rate_limit_per_day integer,
    enabled boolean DEFAULT false NOT NULL,
    config jsonb DEFAULT '{}'::jsonb NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    last_run_at timestamp with time zone,
    last_run_status text,
    last_run_duration_ms integer,
    last_error text,
    total_runs bigint DEFAULT 0 NOT NULL,
    total_failures bigint DEFAULT 0 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

--
--

CREATE TABLE IF NOT EXISTS public.qa_gates (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name text NOT NULL,
    stage_name text DEFAULT 'qa'::text NOT NULL,
    execution_order integer DEFAULT 100 NOT NULL,
    reviewer text NOT NULL,
    required_to_pass boolean DEFAULT true NOT NULL,
    enabled boolean DEFAULT true NOT NULL,
    config jsonb DEFAULT '{}'::jsonb NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    last_run_at timestamp with time zone,
    last_run_duration_ms integer,
    last_run_status text,
    total_runs bigint DEFAULT 0 NOT NULL,
    total_rejections bigint DEFAULT 0 NOT NULL,
    last_error text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

--
--

CREATE TABLE IF NOT EXISTS public.quality_evaluations (
    id integer NOT NULL,
    content_id character varying(255) NOT NULL,
    task_id character varying(255),
    overall_score numeric(3,1) NOT NULL,
    clarity numeric(3,1) NOT NULL,
    accuracy numeric(3,1) NOT NULL,
    completeness numeric(3,1) NOT NULL,
    relevance numeric(3,1) NOT NULL,
    seo_quality numeric(3,1) NOT NULL,
    readability numeric(3,1) NOT NULL,
    engagement numeric(3,1) NOT NULL,
    passing boolean DEFAULT false NOT NULL,
    feedback text,
    suggestions jsonb DEFAULT '[]'::jsonb,
    evaluated_by character varying(100) DEFAULT 'QualityEvaluator'::character varying NOT NULL,
    evaluation_method character varying(50) DEFAULT 'pattern-based'::character varying NOT NULL,
    evaluation_timestamp timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    refinement_count integer DEFAULT 0,
    is_final boolean DEFAULT false,
    content_length integer,
    context_data jsonb DEFAULT '{}'::jsonb
);

--
--

ALTER TABLE public.quality_evaluations ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.quality_evaluations_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

--
--

CREATE TABLE IF NOT EXISTS public.retention_policies (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name text NOT NULL,
    handler_name text NOT NULL,
    table_name text NOT NULL,
    filter_sql text,
    age_column text DEFAULT 'created_at'::text NOT NULL,
    ttl_days integer,
    downsample_rule jsonb,
    summarize_handler text,
    enabled boolean DEFAULT false NOT NULL,
    config jsonb DEFAULT '{}'::jsonb NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    last_run_at timestamp with time zone,
    last_run_duration_ms integer,
    last_run_deleted bigint,
    last_run_summarized bigint,
    last_error text,
    total_runs bigint DEFAULT 0 NOT NULL,
    total_deleted bigint DEFAULT 0 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT retention_policies_parameter_required_chk CHECK (((ttl_days IS NOT NULL) OR (downsample_rule IS NOT NULL) OR (summarize_handler IS NOT NULL)))
);

--
--

CREATE TABLE IF NOT EXISTS public.revenue_events (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    event_type text NOT NULL,
    source text NOT NULL,
    amount_usd numeric(10,2) DEFAULT 0,
    currency text DEFAULT 'USD'::text,
    recurring boolean DEFAULT false,
    source_post_id uuid,
    source_slug text,
    source_url text,
    affiliate_id text,
    customer_email text,
    customer_id text,
    external_id text,
    external_data jsonb DEFAULT '{}'::jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

--
--

ALTER TABLE public.routing_outcomes ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.routing_outcomes_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

--
--

CREATE TABLE IF NOT EXISTS public.schema_migrations (
    id integer NOT NULL,
    name character varying(255) NOT NULL,
    applied_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.schema_migrations_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.schema_migrations_id_seq OWNED BY public.schema_migrations.id;

--
--

CREATE TABLE IF NOT EXISTS public.sensor_samples (
    id bigint NOT NULL,
    source text NOT NULL,
    metric_name text NOT NULL,
    metric_value numeric(14,4) NOT NULL,
    unit text,
    dimensions jsonb DEFAULT '{}'::jsonb,
    sampled_at timestamp with time zone NOT NULL,
    fetched_at timestamp with time zone DEFAULT now() NOT NULL
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.sensor_samples_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.sensor_samples_id_seq OWNED BY public.sensor_samples.id;

--
--

CREATE TABLE IF NOT EXISTS public.subscriber_events (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    subscriber_id uuid,
    email text,
    event_type text NOT NULL,
    event_data jsonb DEFAULT '{}'::jsonb,
    post_id uuid,
    campaign_id text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

--
--

CREATE TABLE IF NOT EXISTS public.sync_metrics (
    id integer NOT NULL,
    metric_name character varying(100) NOT NULL,
    metric_value jsonb NOT NULL,
    synced_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.sync_metrics_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.sync_metrics_id_seq OWNED BY public.sync_metrics.id;

--
--

CREATE TABLE IF NOT EXISTS public.tags (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name character varying(255) NOT NULL,
    slug character varying(255) NOT NULL,
    description text,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP
);

--
--

CREATE TABLE IF NOT EXISTS public.task_failure_alerts (
    task_id text NOT NULL,
    error_hash text NOT NULL,
    last_sent_at timestamp with time zone DEFAULT now() NOT NULL,
    alert_count integer DEFAULT 1 NOT NULL,
    last_error text,
    last_severity text
);

--
--

CREATE TABLE IF NOT EXISTS public.task_status_history (
    id bigint NOT NULL,
    task_id character varying(255) NOT NULL,
    old_status character varying(50),
    new_status character varying(50) NOT NULL,
    changed_by character varying(100) DEFAULT 'system'::character varying,
    reason text,
    metadata jsonb DEFAULT '{}'::jsonb,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_status_history_new_status CHECK (((new_status)::text = ANY (ARRAY[('pending'::character varying)::text, ('queued'::character varying)::text, ('in_progress'::character varying)::text, ('completed'::character varying)::text, ('failed'::character varying)::text, ('cancelled'::character varying)::text, ('awaiting_approval'::character varying)::text, ('approved'::character varying)::text, ('validation_failed'::character varying)::text, ('validation_error'::character varying)::text]))),
    CONSTRAINT chk_status_history_old_status CHECK (((old_status)::text = ANY (ARRAY[('pending'::character varying)::text, ('queued'::character varying)::text, ('in_progress'::character varying)::text, ('completed'::character varying)::text, ('failed'::character varying)::text, ('cancelled'::character varying)::text, ('awaiting_approval'::character varying)::text, ('approved'::character varying)::text, ('validation_failed'::character varying)::text, ('validation_error'::character varying)::text])))
);

--
--

ALTER TABLE public.task_status_history ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.task_status_history_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

--
--

CREATE TABLE IF NOT EXISTS public.topic_batches (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    niche_id uuid NOT NULL,
    status text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    resolved_at timestamp with time zone,
    picked_candidate_id uuid,
    picked_candidate_kind text,
    CONSTRAINT topic_batches_picked_candidate_kind_check CHECK (((picked_candidate_kind = ANY (ARRAY['external'::text, 'internal'::text])) OR (picked_candidate_kind IS NULL))),
    CONSTRAINT topic_batches_status_check CHECK ((status = ANY (ARRAY['open'::text, 'resolved'::text, 'expired'::text])))
);

--
--

CREATE TABLE IF NOT EXISTS public.topic_candidates (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    batch_id uuid NOT NULL,
    niche_id uuid NOT NULL,
    source_name text NOT NULL,
    source_ref text NOT NULL,
    title text NOT NULL,
    summary text,
    score numeric NOT NULL,
    score_breakdown jsonb DEFAULT '{}'::jsonb NOT NULL,
    rank_in_batch integer NOT NULL,
    operator_rank integer,
    operator_edited_topic text,
    operator_edited_angle text,
    decay_factor numeric DEFAULT 1.0 NOT NULL,
    carried_from_batch_id uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

--
--

CREATE TABLE IF NOT EXISTS public.users (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    github_id character varying(255),
    email character varying(255),
    username character varying(255),
    display_name character varying(255),
    avatar_url character varying(500),
    role character varying(50) DEFAULT 'user'::character varying,
    is_active boolean DEFAULT true NOT NULL,
    is_admin boolean DEFAULT false,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    last_login_at timestamp with time zone
);

--
--

CREATE TABLE IF NOT EXISTS public.webhook_endpoints (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name text NOT NULL,
    direction text NOT NULL,
    handler_name text NOT NULL,
    path text,
    url text,
    signing_algorithm text DEFAULT 'none'::text NOT NULL,
    secret_key_ref text,
    event_filter jsonb DEFAULT '{}'::jsonb NOT NULL,
    enabled boolean DEFAULT false NOT NULL,
    config jsonb DEFAULT '{}'::jsonb NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    last_success_at timestamp with time zone,
    last_failure_at timestamp with time zone,
    last_error text,
    total_success bigint DEFAULT 0 NOT NULL,
    total_failure bigint DEFAULT 0 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT webhook_endpoints_direction_check CHECK ((direction = ANY (ARRAY['inbound'::text, 'outbound'::text]))),
    CONSTRAINT webhook_endpoints_direction_config_chk CHECK ((((direction = 'inbound'::text) AND (url IS NULL)) OR ((direction = 'outbound'::text) AND ((url IS NOT NULL) OR (secret_key_ref IS NOT NULL))))),
    CONSTRAINT webhook_endpoints_signing_algorithm_check CHECK ((signing_algorithm = ANY (ARRAY['none'::text, 'hmac-sha256'::text, 'svix'::text, 'bearer'::text])))
);

--
--

CREATE TABLE IF NOT EXISTS public.webhook_events (
    id bigint NOT NULL,
    event_type character varying(100) NOT NULL,
    payload jsonb NOT NULL,
    delivered boolean DEFAULT false,
    delivery_attempts integer DEFAULT 0,
    last_attempt_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now()
);

--
--

ALTER TABLE public.webhook_events ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.webhook_events_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

--
--

CREATE TABLE IF NOT EXISTS public.writing_samples (
    id integer NOT NULL,
    user_id character varying(255) NOT NULL,
    title character varying(500) NOT NULL,
    description text,
    content text NOT NULL,
    is_active boolean DEFAULT false,
    word_count integer,
    char_count integer,
    metadata jsonb DEFAULT '{}'::jsonb,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.writing_samples_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.writing_samples_id_seq OWNED BY public.writing_samples.id;

--
--

ALTER TABLE ONLY public.alert_events ALTER COLUMN id SET DEFAULT nextval('public.alert_events_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.alert_log ALTER COLUMN id SET DEFAULT nextval('public.alert_log_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.app_settings ALTER COLUMN id SET DEFAULT nextval('public.app_settings_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.audit_log ALTER COLUMN id SET DEFAULT nextval('public.audit_log_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.campaign_email_logs ALTER COLUMN id SET DEFAULT nextval('public.campaign_email_logs_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.capability_outcomes ALTER COLUMN id SET DEFAULT nextval('public.capability_outcomes_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.electricity_costs ALTER COLUMN id SET DEFAULT nextval('public.electricity_costs_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.embeddings ALTER COLUMN id SET DEFAULT nextval('public.embeddings_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.fact_overrides ALTER COLUMN id SET DEFAULT nextval('public.fact_overrides_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.financial_entries ALTER COLUMN id SET DEFAULT nextval('public.financial_entries_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.gpu_metrics ALTER COLUMN id SET DEFAULT nextval('public.gpu_metrics_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.module_schema_migrations ALTER COLUMN id SET DEFAULT nextval('public.module_schema_migrations_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.newsletter_subscribers ALTER COLUMN id SET DEFAULT nextval('public.newsletter_subscribers_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.notification ALTER COLUMN id SET DEFAULT nextval('public.notification_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.operator_notes ALTER COLUMN id SET DEFAULT nextval('public.operator_notes_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.page_views ALTER COLUMN id SET DEFAULT nextval('public.page_views_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.pipeline_atoms ALTER COLUMN id SET DEFAULT nextval('public.pipeline_atoms_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.pipeline_distributions ALTER COLUMN id SET DEFAULT nextval('public.pipeline_distributions_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.pipeline_gate_history ALTER COLUMN id SET DEFAULT nextval('public.pipeline_gate_history_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.pipeline_tasks ALTER COLUMN id SET DEFAULT nextval('public.pipeline_tasks_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.pipeline_templates ALTER COLUMN id SET DEFAULT nextval('public.pipeline_templates_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.pipeline_versions ALTER COLUMN id SET DEFAULT nextval('public.pipeline_versions_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.published_post_edit_metrics ALTER COLUMN id SET DEFAULT nextval('public.published_post_edit_metrics_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.schema_migrations ALTER COLUMN id SET DEFAULT nextval('public.schema_migrations_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.sensor_samples ALTER COLUMN id SET DEFAULT nextval('public.sensor_samples_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.sync_metrics ALTER COLUMN id SET DEFAULT nextval('public.sync_metrics_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.writing_samples ALTER COLUMN id SET DEFAULT nextval('public.writing_samples_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.alert_dedup_state
    ADD CONSTRAINT alert_dedup_state_pkey PRIMARY KEY (fingerprint);

--
--

ALTER TABLE ONLY public.alert_events
    ADD CONSTRAINT alert_events_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.alert_log
    ADD CONSTRAINT alert_log_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.app_settings
    ADD CONSTRAINT app_settings_key_key UNIQUE (key);

--
--

ALTER TABLE ONLY public.app_settings
    ADD CONSTRAINT app_settings_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.app_state
    ADD CONSTRAINT app_state_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.audit_log
    ADD CONSTRAINT audit_log_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.auth_token
    ADD CONSTRAINT auth_token_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.authors
    ADD CONSTRAINT authors_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.brain_decisions
    ADD CONSTRAINT brain_decisions_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.brain_knowledge
    ADD CONSTRAINT brain_knowledge_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.campaign_email_logs
    ADD CONSTRAINT campaign_email_logs_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.capability_outcomes
    ADD CONSTRAINT capability_outcomes_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.capability_registry
    ADD CONSTRAINT capability_registry_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.categories
    ADD CONSTRAINT categories_name_key UNIQUE (name);

--
--

ALTER TABLE ONLY public.categories
    ADD CONSTRAINT categories_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.categories
    ADD CONSTRAINT categories_slug_key UNIQUE (slug);

--
--

ALTER TABLE ONLY public.checkpoint_blobs
    ADD CONSTRAINT checkpoint_blobs_pkey PRIMARY KEY (thread_id, checkpoint_ns, channel, version);

--
--

ALTER TABLE ONLY public.checkpoint_writes
    ADD CONSTRAINT checkpoint_writes_pkey PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx);

--
--

ALTER TABLE ONLY public.checkpoints
    ADD CONSTRAINT checkpoints_pkey PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id);

--
--

ALTER TABLE ONLY public.content_revisions
    ADD CONSTRAINT content_revisions_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.content_validator_rules
    ADD CONSTRAINT content_validator_rules_name_key UNIQUE (name);

--
--

ALTER TABLE ONLY public.content_validator_rules
    ADD CONSTRAINT content_validator_rules_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.cost_logs
    ADD CONSTRAINT cost_logs_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.decision_log
    ADD CONSTRAINT decision_log_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.discovery_runs
    ADD CONSTRAINT discovery_runs_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.electricity_costs
    ADD CONSTRAINT electricity_costs_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.embeddings
    ADD CONSTRAINT embeddings_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.embeddings
    ADD CONSTRAINT embeddings_source_table_source_id_chunk_index_embedding_mod_key UNIQUE (source_table, source_id, chunk_index, embedding_model);

--
--

ALTER TABLE ONLY public.experiment_variants
    ADD CONSTRAINT experiment_variants_experiment_id_label_key UNIQUE (experiment_id, label);

--
--

ALTER TABLE ONLY public.experiment_variants
    ADD CONSTRAINT experiment_variants_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.experiments
    ADD CONSTRAINT experiments_key_key UNIQUE (key);

--
--

ALTER TABLE ONLY public.experiments
    ADD CONSTRAINT experiments_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.external_metrics
    ADD CONSTRAINT external_metrics_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.external_taps
    ADD CONSTRAINT external_taps_name_key UNIQUE (name);

--
--

ALTER TABLE ONLY public.external_taps
    ADD CONSTRAINT external_taps_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.fact_overrides
    ADD CONSTRAINT fact_overrides_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.financial_entries
    ADD CONSTRAINT financial_entries_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.gpu_metrics_hourly
    ADD CONSTRAINT gpu_metrics_hourly_pkey PRIMARY KEY (bucket_start);

--
--

ALTER TABLE ONLY public.gpu_metrics
    ADD CONSTRAINT gpu_metrics_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.gpu_task_sessions
    ADD CONSTRAINT gpu_task_sessions_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.internal_topic_candidates
    ADD CONSTRAINT internal_topic_candidates_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.media_approvals
    ADD CONSTRAINT media_approvals_pkey PRIMARY KEY (post_id, medium);

--
--

ALTER TABLE ONLY public.media_assets
    ADD CONSTRAINT media_assets_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.model_performance
    ADD CONSTRAINT model_performance_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.module_schema_migrations
    ADD CONSTRAINT module_schema_migrations_module_name_migration_name_key UNIQUE (module_name, migration_name);

--
--

ALTER TABLE ONLY public.module_schema_migrations
    ADD CONSTRAINT module_schema_migrations_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.newsletter_subscribers
    ADD CONSTRAINT newsletter_subscribers_email_key UNIQUE (email);

--
--

ALTER TABLE ONLY public.newsletter_subscribers
    ADD CONSTRAINT newsletter_subscribers_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.niche_goals
    ADD CONSTRAINT niche_goals_pkey PRIMARY KEY (niche_id, goal_type);

--
--

ALTER TABLE ONLY public.niche_sources
    ADD CONSTRAINT niche_sources_pkey PRIMARY KEY (niche_id, source_name);

--
--

ALTER TABLE ONLY public.niches
    ADD CONSTRAINT niches_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.niches
    ADD CONSTRAINT niches_slug_key UNIQUE (slug);

--
--

ALTER TABLE ONLY public.notification
    ADD CONSTRAINT notification_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.oauth_authorization_codes
    ADD CONSTRAINT oauth_authorization_codes_pkey PRIMARY KEY (code);

--
--

ALTER TABLE ONLY public.oauth_clients
    ADD CONSTRAINT oauth_clients_pkey PRIMARY KEY (client_id);

--
--

ALTER TABLE ONLY public.object_stores
    ADD CONSTRAINT object_stores_name_key UNIQUE (name);

--
--

ALTER TABLE ONLY public.object_stores
    ADD CONSTRAINT object_stores_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.operator_notes
    ADD CONSTRAINT operator_notes_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.orchestrator_training_data
    ADD CONSTRAINT orchestrator_training_data_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.page_views
    ADD CONSTRAINT page_views_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.pipeline_atoms
    ADD CONSTRAINT pipeline_atoms_name_key UNIQUE (name);

--
--

ALTER TABLE ONLY public.pipeline_atoms
    ADD CONSTRAINT pipeline_atoms_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.pipeline_distributions
    ADD CONSTRAINT pipeline_distributions_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.pipeline_distributions
    ADD CONSTRAINT pipeline_distributions_task_id_target_key UNIQUE (task_id, target);

--
--

ALTER TABLE ONLY public.pipeline_gate_history
    ADD CONSTRAINT pipeline_gate_history_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.pipeline_tasks
    ADD CONSTRAINT pipeline_tasks_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.pipeline_tasks
    ADD CONSTRAINT pipeline_tasks_task_id_key UNIQUE (task_id);

--
--

ALTER TABLE ONLY public.pipeline_templates
    ADD CONSTRAINT pipeline_templates_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.pipeline_templates
    ADD CONSTRAINT pipeline_templates_slug_key UNIQUE (slug);

--
--

ALTER TABLE ONLY public.pipeline_versions
    ADD CONSTRAINT pipeline_versions_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.pipeline_versions
    ADD CONSTRAINT pipeline_versions_task_id_version_key UNIQUE (task_id, version);

--
--


--
--

ALTER TABLE ONLY public.post_performance
    ADD CONSTRAINT post_performance_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.post_tags
    ADD CONSTRAINT post_tags_pkey PRIMARY KEY (post_id, tag_id);

--
--

ALTER TABLE ONLY public.posts
    ADD CONSTRAINT posts_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.posts
    ADD CONSTRAINT posts_slug_key UNIQUE (slug);

--
--

ALTER TABLE ONLY public.published_post_edit_metrics
    ADD CONSTRAINT published_post_edit_metrics_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.publishing_adapters
    ADD CONSTRAINT publishing_adapters_name_key UNIQUE (name);

--
--

ALTER TABLE ONLY public.publishing_adapters
    ADD CONSTRAINT publishing_adapters_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.qa_gates
    ADD CONSTRAINT qa_gates_name_key UNIQUE (name);

--
--

ALTER TABLE ONLY public.qa_gates
    ADD CONSTRAINT qa_gates_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.quality_evaluations
    ADD CONSTRAINT quality_evaluations_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.retention_policies
    ADD CONSTRAINT retention_policies_name_key UNIQUE (name);

--
--

ALTER TABLE ONLY public.retention_policies
    ADD CONSTRAINT retention_policies_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.revenue_events
    ADD CONSTRAINT revenue_events_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.routing_outcomes
    ADD CONSTRAINT routing_outcomes_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.schema_migrations
    ADD CONSTRAINT schema_migrations_name_key UNIQUE (name);

--
--

ALTER TABLE ONLY public.schema_migrations
    ADD CONSTRAINT schema_migrations_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.sensor_samples
    ADD CONSTRAINT sensor_samples_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.subscriber_events
    ADD CONSTRAINT subscriber_events_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.sync_metrics
    ADD CONSTRAINT sync_metrics_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.tags
    ADD CONSTRAINT tags_name_key UNIQUE (name);

--
--

ALTER TABLE ONLY public.tags
    ADD CONSTRAINT tags_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.tags
    ADD CONSTRAINT tags_slug_key UNIQUE (slug);

--
--

ALTER TABLE ONLY public.task_failure_alerts
    ADD CONSTRAINT task_failure_alerts_pkey PRIMARY KEY (task_id, error_hash);

--
--

ALTER TABLE ONLY public.task_status_history
    ADD CONSTRAINT task_status_history_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.topic_batches
    ADD CONSTRAINT topic_batches_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.topic_candidates
    ADD CONSTRAINT topic_candidates_batch_id_source_name_source_ref_key UNIQUE (batch_id, source_name, source_ref);

--
--

ALTER TABLE ONLY public.topic_candidates
    ADD CONSTRAINT topic_candidates_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_email_key UNIQUE (email);

--
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_github_id_key UNIQUE (github_id);

--
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.webhook_endpoints
    ADD CONSTRAINT webhook_endpoints_name_key UNIQUE (name);

--
--

ALTER TABLE ONLY public.webhook_endpoints
    ADD CONSTRAINT webhook_endpoints_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.webhook_events
    ADD CONSTRAINT webhook_events_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.writing_samples
    ADD CONSTRAINT writing_samples_pkey PRIMARY KEY (id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_auth_token_expires_unix" ON public.auth_token USING btree (expires_unix);

--
--

CREATE INDEX IF NOT EXISTS "IDX_auth_token_user_id" ON public.auth_token USING btree (user_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_notification_idx_notification_commit_id" ON public.notification USING btree (commit_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_notification_idx_notification_issue_id" ON public.notification USING btree (issue_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_notification_idx_notification_repo_id" ON public.notification USING btree (repo_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_notification_idx_notification_source" ON public.notification USING btree (source);

--
--

CREATE INDEX IF NOT EXISTS "IDX_notification_idx_notification_status" ON public.notification USING btree (status);

--
--

CREATE INDEX IF NOT EXISTS "IDX_notification_idx_notification_updated_by" ON public.notification USING btree (updated_by);

--
--

CREATE INDEX IF NOT EXISTS "IDX_notification_idx_notification_user_id" ON public.notification USING btree (user_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_notification_u_s_uu" ON public.notification USING btree (user_id, status, updated_unix);

--
--

CREATE UNIQUE INDEX IF NOT EXISTS brain_knowledge_entity_attribute_key ON public.brain_knowledge USING btree (entity, attribute);

--
--

CREATE INDEX IF NOT EXISTS checkpoint_blobs_thread_id_idx ON public.checkpoint_blobs USING btree (thread_id);

--
--

CREATE INDEX IF NOT EXISTS checkpoint_writes_thread_id_idx ON public.checkpoint_writes USING btree (thread_id);

--
--

CREATE INDEX IF NOT EXISTS checkpoints_thread_id_idx ON public.checkpoints USING btree (thread_id);

--
--

CREATE INDEX IF NOT EXISTS idx_alert_dedup_state_last_seen ON public.alert_dedup_state USING btree (last_seen_at DESC);

--
--

CREATE INDEX IF NOT EXISTS idx_alert_events_alertname ON public.alert_events USING btree (alertname, received_at DESC);

--
--

CREATE INDEX IF NOT EXISTS idx_alert_events_received_at ON public.alert_events USING btree (received_at DESC);

--
--

CREATE INDEX IF NOT EXISTS idx_alert_events_undispatched ON public.alert_events USING btree (id) WHERE (dispatched_at IS NULL);

--
--

CREATE INDEX IF NOT EXISTS idx_app_settings_category ON public.app_settings USING btree (category);

--
--

CREATE INDEX IF NOT EXISTS idx_app_settings_is_active ON public.app_settings USING btree (is_active) WHERE (is_active = true);

--
--

CREATE INDEX IF NOT EXISTS idx_audit_log_event_type ON public.audit_log USING btree (event_type);

--
--

CREATE INDEX IF NOT EXISTS idx_audit_log_severity ON public.audit_log USING btree (severity);

--
--

CREATE INDEX IF NOT EXISTS idx_audit_log_task_id ON public.audit_log USING btree (task_id);

--
--

CREATE INDEX IF NOT EXISTS idx_audit_log_timestamp ON public.audit_log USING btree ("timestamp");

--
--

CREATE INDEX IF NOT EXISTS idx_brain_decisions_created ON public.brain_decisions USING btree (created_at);

--
--

CREATE INDEX IF NOT EXISTS idx_brain_knowledge_entity ON public.brain_knowledge USING btree (entity);

--
--

CREATE INDEX IF NOT EXISTS idx_brain_knowledge_tags ON public.brain_knowledge USING gin (tags);

--
--

CREATE INDEX IF NOT EXISTS idx_capability_outcomes_atom_model ON public.capability_outcomes USING btree (atom_name, model_used) WHERE ((atom_name IS NOT NULL) AND (model_used IS NOT NULL));

--
--

CREATE INDEX IF NOT EXISTS idx_capability_outcomes_niche_template ON public.capability_outcomes USING btree (niche_slug, prompt_template_key, created_at DESC) WHERE (niche_slug IS NOT NULL);

--
--

CREATE INDEX IF NOT EXISTS idx_capability_outcomes_task ON public.capability_outcomes USING btree (task_id) WHERE (task_id IS NOT NULL);

--
--

CREATE INDEX IF NOT EXISTS idx_capability_outcomes_template ON public.capability_outcomes USING btree (template_slug, created_at DESC);

--
--

CREATE INDEX IF NOT EXISTS idx_capability_outcomes_tier ON public.capability_outcomes USING btree (capability_tier, ok) WHERE (capability_tier IS NOT NULL);

--
--

CREATE INDEX IF NOT EXISTS idx_capability_outcomes_variant ON public.capability_outcomes USING btree (variant_id) WHERE (variant_id IS NOT NULL);

--
--

CREATE INDEX IF NOT EXISTS idx_capability_registry_heartbeat ON public.capability_registry USING btree (last_heartbeat);

--
--

CREATE INDEX IF NOT EXISTS idx_capability_registry_status ON public.capability_registry USING btree (status);

--
--

CREATE INDEX IF NOT EXISTS idx_capability_registry_type ON public.capability_registry USING btree (entity_type);

--
--

CREATE INDEX IF NOT EXISTS idx_content_revisions_post ON public.content_revisions USING btree (post_id) WHERE (post_id IS NOT NULL);

--
--

CREATE INDEX IF NOT EXISTS idx_content_revisions_task ON public.content_revisions USING btree (task_id);

--
--

CREATE INDEX IF NOT EXISTS idx_content_validator_rules_enabled ON public.content_validator_rules USING btree (enabled) WHERE (enabled = true);

--
--

CREATE INDEX IF NOT EXISTS idx_cost_logs_cost_type ON public.cost_logs USING btree (cost_type);

--
--

CREATE INDEX IF NOT EXISTS idx_cost_logs_created_at ON public.cost_logs USING btree (created_at);

--
--

CREATE INDEX IF NOT EXISTS idx_cost_logs_electricity_kwh ON public.cost_logs USING btree (created_at, provider) WHERE (electricity_kwh IS NOT NULL);

--
--

CREATE INDEX IF NOT EXISTS idx_cost_logs_task_id ON public.cost_logs USING btree (task_id);

--
--

CREATE INDEX IF NOT EXISTS idx_cost_logs_task_phase ON public.cost_logs USING btree (task_id, phase);

--
--

CREATE INDEX IF NOT EXISTS idx_decision_log_created ON public.decision_log USING btree (created_at DESC);

--
--

CREATE INDEX IF NOT EXISTS idx_decision_log_pending_outcome ON public.decision_log USING btree (outcome_recorded_at) WHERE ((outcome_recorded_at IS NULL) AND (outcome IS NULL));

--
--

CREATE INDEX IF NOT EXISTS idx_decision_log_task ON public.decision_log USING btree (task_id) WHERE (task_id IS NOT NULL);

--
--

CREATE INDEX IF NOT EXISTS idx_decision_log_type ON public.decision_log USING btree (decision_type);

--
--

CREATE INDEX IF NOT EXISTS idx_embeddings_created_at ON public.embeddings USING btree (created_at);

--
--

CREATE INDEX IF NOT EXISTS idx_embeddings_hnsw ON public.embeddings USING hnsw (embedding public.vector_cosine_ops) WITH (m='16', ef_construction='64');

--
--

CREATE INDEX IF NOT EXISTS idx_embeddings_model ON public.embeddings USING btree (embedding_model);

--
--

CREATE INDEX IF NOT EXISTS idx_embeddings_source ON public.embeddings USING btree (source_table, source_id);

--
--

CREATE INDEX IF NOT EXISTS idx_embeddings_text_search ON public.embeddings USING gin (text_search);

--
--

CREATE INDEX IF NOT EXISTS idx_embeddings_writer ON public.embeddings USING btree (writer);

--
--

CREATE INDEX IF NOT EXISTS idx_experiment_variants_active_lookup ON public.experiment_variants USING btree (experiment_id) WHERE active;

--
--

CREATE INDEX IF NOT EXISTS idx_experiments_key ON public.experiments USING btree (key);

--
--

CREATE UNIQUE INDEX IF NOT EXISTS idx_experiments_one_active_per_niche ON public.experiments USING btree (niche_slug) WHERE (status = 'active'::text);

--
--

CREATE INDEX IF NOT EXISTS idx_external_metrics_post ON public.external_metrics USING btree (post_id) WHERE (post_id IS NOT NULL);

--
--

CREATE INDEX IF NOT EXISTS idx_external_metrics_slug ON public.external_metrics USING btree (slug) WHERE (slug IS NOT NULL);

--
--

CREATE INDEX IF NOT EXISTS idx_external_metrics_source_date ON public.external_metrics USING btree (source, date DESC);

--
--

CREATE INDEX IF NOT EXISTS idx_external_taps_enabled ON public.external_taps USING btree (enabled);

--
--

CREATE INDEX IF NOT EXISTS idx_external_taps_name ON public.external_taps USING btree (name);

--
--

CREATE INDEX IF NOT EXISTS idx_fact_overrides_active ON public.fact_overrides USING btree (active) WHERE (active = true);

--
--

CREATE INDEX IF NOT EXISTS idx_gpu_metrics_hourly_bucket ON public.gpu_metrics_hourly USING btree (bucket_start DESC);

--
--

CREATE INDEX IF NOT EXISTS idx_gpu_metrics_timestamp ON public.gpu_metrics USING btree ("timestamp");

--
--

CREATE INDEX IF NOT EXISTS idx_gpu_sessions_phase ON public.gpu_task_sessions USING btree (phase);

--
--

CREATE INDEX IF NOT EXISTS idx_gpu_sessions_started ON public.gpu_task_sessions USING btree (started_at DESC);

--
--

CREATE INDEX IF NOT EXISTS idx_gpu_sessions_task ON public.gpu_task_sessions USING btree (task_id);

--
--

CREATE INDEX IF NOT EXISTS idx_media_assets_kind_created ON public.media_assets USING btree (type, created_at DESC);

--
--

CREATE INDEX IF NOT EXISTS idx_media_assets_platform_video_ids ON public.media_assets USING gin (platform_video_ids);

--
--

CREATE INDEX IF NOT EXISTS idx_media_assets_post_id ON public.media_assets USING btree (post_id) WHERE (post_id IS NOT NULL);

--
--

CREATE INDEX IF NOT EXISTS idx_media_assets_site ON public.media_assets USING btree (site_id);

--
--

CREATE INDEX IF NOT EXISTS idx_media_assets_task ON public.media_assets USING btree (task_id);

--
--

CREATE INDEX IF NOT EXISTS idx_media_assets_type ON public.media_assets USING btree (type);

--
--

CREATE INDEX IF NOT EXISTS idx_model_performance_created ON public.model_performance USING btree (created_at DESC);

--
--

CREATE INDEX IF NOT EXISTS idx_model_performance_model ON public.model_performance USING btree (model_name);

--
--

CREATE INDEX IF NOT EXISTS idx_model_performance_task_type ON public.model_performance USING btree (task_type);

--
--

CREATE INDEX IF NOT EXISTS idx_newsletter_interests_gin ON public.newsletter_subscribers USING gin (interest_categories);

--
--

CREATE INDEX IF NOT EXISTS idx_oauth_auth_codes_expires ON public.oauth_authorization_codes USING btree (expires_at);

--
--

CREATE INDEX IF NOT EXISTS idx_oauth_clients_active ON public.oauth_clients USING btree (client_id) WHERE (revoked_at IS NULL);

--
--

CREATE INDEX IF NOT EXISTS idx_object_stores_enabled ON public.object_stores USING btree (enabled);

--
--

CREATE UNIQUE INDEX IF NOT EXISTS idx_object_stores_name ON public.object_stores USING btree (name);

--
--

CREATE INDEX IF NOT EXISTS idx_operator_notes_date ON public.operator_notes USING btree (note_date DESC);

--
--

CREATE INDEX IF NOT EXISTS idx_operator_notes_niche_date ON public.operator_notes USING btree (niche_slug, note_date DESC);

--
--

CREATE INDEX IF NOT EXISTS idx_page_views_created ON public.page_views USING btree (created_at);

--
--

CREATE INDEX IF NOT EXISTS idx_page_views_slug ON public.page_views USING btree (slug);

--
--

CREATE INDEX IF NOT EXISTS idx_pipeline_atoms_capability_tier ON public.pipeline_atoms USING btree (capability_tier) WHERE (capability_tier IS NOT NULL);

--
--

CREATE INDEX IF NOT EXISTS idx_pipeline_atoms_cost_class ON public.pipeline_atoms USING btree (cost_class);

--
--

CREATE INDEX IF NOT EXISTS idx_pipeline_atoms_type ON public.pipeline_atoms USING btree (type);

--
--

CREATE INDEX IF NOT EXISTS idx_pipeline_distributions_target_status ON public.pipeline_distributions USING btree (target, status);

--
--

CREATE INDEX IF NOT EXISTS idx_pipeline_distributions_task ON public.pipeline_distributions USING btree (task_id);

--
--

CREATE INDEX IF NOT EXISTS idx_pipeline_gate_history_post_lookup ON public.pipeline_gate_history USING btree (post_id, gate_name, event_kind) WHERE (post_id IS NOT NULL);

--
--

CREATE INDEX IF NOT EXISTS idx_pipeline_gate_history_task_lookup ON public.pipeline_gate_history USING btree (task_id, gate_name, event_kind) WHERE (task_id IS NOT NULL);

--
--

CREATE INDEX IF NOT EXISTS idx_pipeline_tasks_auto_cancelled ON public.pipeline_tasks USING btree (auto_cancelled_at) WHERE (auto_cancelled_at IS NOT NULL);

--
--

CREATE INDEX IF NOT EXISTS idx_pipeline_tasks_awaiting_gate ON public.pipeline_tasks USING btree (awaiting_gate, gate_paused_at) WHERE (awaiting_gate IS NOT NULL);

--
--

CREATE INDEX IF NOT EXISTS idx_pipeline_tasks_scheduled ON public.pipeline_tasks USING btree (status, scheduled_at) WHERE (scheduled_at IS NOT NULL);

--
--

CREATE INDEX IF NOT EXISTS idx_pipeline_tasks_stage ON public.pipeline_tasks USING btree (stage);

--
--

CREATE INDEX IF NOT EXISTS idx_pipeline_tasks_status ON public.pipeline_tasks USING btree (status);

--
--

CREATE INDEX IF NOT EXISTS idx_pipeline_tasks_status_created ON public.pipeline_tasks USING btree (status, created_at DESC);

--
--

CREATE INDEX IF NOT EXISTS idx_pipeline_tasks_template_slug ON public.pipeline_tasks USING btree (template_slug) WHERE (template_slug IS NOT NULL);

--
--

CREATE INDEX IF NOT EXISTS idx_pipeline_templates_active ON public.pipeline_templates USING btree (active) WHERE (active = true);

--
--

CREATE INDEX IF NOT EXISTS idx_pipeline_templates_created_by ON public.pipeline_templates USING btree (created_by);

--
--


--
--

CREATE INDEX IF NOT EXISTS idx_post_edit_metrics_category ON public.published_post_edit_metrics USING btree (category, approved_at DESC);

--
--

CREATE INDEX IF NOT EXISTS idx_post_edit_metrics_niche ON public.published_post_edit_metrics USING btree (niche_slug, approved_at DESC) WHERE (niche_slug IS NOT NULL);

--
--

CREATE INDEX IF NOT EXISTS idx_post_edit_metrics_task ON public.published_post_edit_metrics USING btree (task_id);

--
--

CREATE INDEX IF NOT EXISTS idx_post_performance_measured ON public.post_performance USING btree (measured_at DESC);

--
--

CREATE INDEX IF NOT EXISTS idx_post_performance_post ON public.post_performance USING btree (post_id);

--
--

CREATE INDEX IF NOT EXISTS idx_post_tags_post_id ON public.post_tags USING btree (post_id);

--
--

CREATE INDEX IF NOT EXISTS idx_post_tags_tag_id ON public.post_tags USING btree (tag_id);

--
--

CREATE INDEX IF NOT EXISTS idx_posts_author_id ON public.posts USING btree (author_id);

--
--

CREATE INDEX IF NOT EXISTS idx_posts_awaiting_gate ON public.posts USING btree (awaiting_gate, gate_paused_at) WHERE (awaiting_gate IS NOT NULL);

--
--

CREATE INDEX IF NOT EXISTS idx_posts_category_id ON public.posts USING btree (category_id);

--
--

CREATE INDEX IF NOT EXISTS idx_posts_cli_idempotency_key ON public.posts USING btree (cli_idempotency_key, created_at) WHERE (cli_idempotency_key IS NOT NULL);

--
--

CREATE INDEX IF NOT EXISTS idx_posts_preview_token ON public.posts USING btree (preview_token) WHERE (preview_token IS NOT NULL);

--
--

CREATE INDEX IF NOT EXISTS idx_posts_published_at ON public.posts USING btree (published_at);

--
--

CREATE INDEX IF NOT EXISTS idx_posts_site_id ON public.posts USING btree (site_id);

--
--

CREATE INDEX IF NOT EXISTS idx_posts_slug ON public.posts USING btree (slug);

--
--

CREATE INDEX IF NOT EXISTS idx_posts_status ON public.posts USING btree (status);

--
--

CREATE INDEX IF NOT EXISTS idx_posts_status_published ON public.posts USING btree (status, published_at DESC);

--
--

CREATE INDEX IF NOT EXISTS idx_publishing_adapters_enabled ON public.publishing_adapters USING btree (enabled);

--
--

CREATE INDEX IF NOT EXISTS idx_publishing_adapters_name ON public.publishing_adapters USING btree (name);

--
--

CREATE INDEX IF NOT EXISTS idx_publishing_adapters_platform ON public.publishing_adapters USING btree (platform);

--
--

CREATE INDEX IF NOT EXISTS idx_qa_gates_enabled ON public.qa_gates USING btree (enabled);

--
--

CREATE INDEX IF NOT EXISTS idx_qa_gates_stage_order ON public.qa_gates USING btree (stage_name, execution_order);

--
--

CREATE INDEX IF NOT EXISTS idx_quality_evaluations_content_id ON public.quality_evaluations USING btree (content_id);

--
--

CREATE INDEX IF NOT EXISTS idx_quality_evaluations_task_id ON public.quality_evaluations USING btree (task_id);

--
--

CREATE INDEX IF NOT EXISTS idx_retention_policies_enabled ON public.retention_policies USING btree (enabled);

--
--

CREATE INDEX IF NOT EXISTS idx_retention_policies_name ON public.retention_policies USING btree (name);

--
--

CREATE INDEX IF NOT EXISTS idx_revenue_events_created ON public.revenue_events USING btree (created_at DESC);

--
--

CREATE INDEX IF NOT EXISTS idx_revenue_events_post ON public.revenue_events USING btree (source_post_id) WHERE (source_post_id IS NOT NULL);

--
--

CREATE INDEX IF NOT EXISTS idx_revenue_events_type ON public.revenue_events USING btree (event_type);

--
--

CREATE INDEX IF NOT EXISTS idx_routing_outcomes_model ON public.routing_outcomes USING btree (model_used, task_type);

--
--

CREATE INDEX IF NOT EXISTS idx_routing_outcomes_worker ON public.routing_outcomes USING btree (worker_id);

--
--

CREATE INDEX IF NOT EXISTS idx_sensor_samples_source_metric_time ON public.sensor_samples USING btree (source, metric_name, sampled_at DESC);

--
--

CREATE INDEX IF NOT EXISTS idx_subscriber_events_created ON public.subscriber_events USING btree (created_at DESC);

--
--

CREATE INDEX IF NOT EXISTS idx_subscriber_events_subscriber ON public.subscriber_events USING btree (subscriber_id) WHERE (subscriber_id IS NOT NULL);

--
--

CREATE INDEX IF NOT EXISTS idx_subscriber_events_type ON public.subscriber_events USING btree (event_type);

--
--

CREATE INDEX IF NOT EXISTS idx_task_failure_alerts_last_sent ON public.task_failure_alerts USING btree (last_sent_at DESC);

--
--

CREATE INDEX IF NOT EXISTS idx_task_status_history_created_at ON public.task_status_history USING btree (created_at DESC);

--
--

CREATE INDEX IF NOT EXISTS idx_task_status_history_task_id ON public.task_status_history USING btree (task_id);

--
--

CREATE INDEX IF NOT EXISTS idx_webhook_endpoints_direction_enabled ON public.webhook_endpoints USING btree (direction, enabled);

--
--

CREATE INDEX IF NOT EXISTS idx_webhook_endpoints_name ON public.webhook_endpoints USING btree (name);

--
--

CREATE INDEX IF NOT EXISTS idx_webhook_events_undelivered ON public.webhook_events USING btree (delivered, created_at) WHERE (NOT delivered);

--
--

CREATE INDEX IF NOT EXISTS idx_writing_samples_user_id ON public.writing_samples USING btree (user_id);

--
--

CREATE INDEX IF NOT EXISTS ix_discovery_runs_niche_started ON public.discovery_runs USING btree (niche_id, started_at DESC);

--
--

CREATE INDEX IF NOT EXISTS ix_internal_topic_candidates_batch ON public.internal_topic_candidates USING btree (batch_id);

--
--

CREATE INDEX IF NOT EXISTS ix_media_approvals_pending ON public.media_approvals USING btree (medium, created_at DESC) WHERE (status = 'pending'::text);

--
--

CREATE UNIQUE INDEX IF NOT EXISTS ix_newsletter_subscribers_unsubscribe_token ON public.newsletter_subscribers USING btree (unsubscribe_token);

--
--

CREATE INDEX IF NOT EXISTS ix_pipeline_tasks_batch ON public.pipeline_tasks USING btree (topic_batch_id) WHERE (topic_batch_id IS NOT NULL);

--
--

CREATE INDEX IF NOT EXISTS ix_pipeline_tasks_niche ON public.pipeline_tasks USING btree (niche_slug) WHERE (niche_slug IS NOT NULL);

--
--

CREATE INDEX IF NOT EXISTS ix_topic_candidates_batch ON public.topic_candidates USING btree (batch_id);

--
--

CREATE INDEX IF NOT EXISTS module_schema_migrations_module_idx ON public.module_schema_migrations USING btree (module_name);

--
--

CREATE UNIQUE INDEX IF NOT EXISTS orchestrator_training_data_execution_id_key ON public.orchestrator_training_data USING btree (execution_id);

--
--

CREATE UNIQUE INDEX IF NOT EXISTS uq_one_open_batch_per_niche ON public.topic_batches USING btree (niche_id) WHERE (status = 'open'::text);

--
--

CREATE UNIQUE INDEX IF NOT EXISTS uq_sensor_samples_source_time_metric ON public.sensor_samples USING btree (source, sampled_at, metric_name);

--
--

DROP TRIGGER IF EXISTS app_settings_auto_encrypt_trigger ON public.app_settings;
CREATE TRIGGER app_settings_auto_encrypt_trigger BEFORE INSERT OR UPDATE OF value ON public.app_settings FOR EACH ROW EXECUTE FUNCTION public.app_settings_auto_encrypt();

--
--

DROP TRIGGER IF EXISTS content_tasks_delete_trigger ON public.content_tasks;
CREATE TRIGGER content_tasks_delete_trigger INSTEAD OF DELETE ON public.content_tasks FOR EACH ROW EXECUTE FUNCTION public.content_tasks_delete_redirect();

--
--

DROP TRIGGER IF EXISTS content_tasks_insert_trigger ON public.content_tasks;
CREATE TRIGGER content_tasks_insert_trigger INSTEAD OF INSERT ON public.content_tasks FOR EACH ROW EXECUTE FUNCTION public.content_tasks_insert_redirect();

--
--

DROP TRIGGER IF EXISTS content_tasks_update_trigger ON public.content_tasks;
CREATE TRIGGER content_tasks_update_trigger INSTEAD OF UPDATE ON public.content_tasks FOR EACH ROW EXECUTE FUNCTION public.content_tasks_update_redirect();

--
--

DROP TRIGGER IF EXISTS external_taps_touch_updated_at_trg ON public.external_taps;
CREATE TRIGGER external_taps_touch_updated_at_trg BEFORE UPDATE ON public.external_taps FOR EACH ROW EXECUTE FUNCTION public.external_taps_touch_updated_at();

--
--

DROP TRIGGER IF EXISTS object_stores_touch_updated_at_trg ON public.object_stores;
CREATE TRIGGER object_stores_touch_updated_at_trg BEFORE UPDATE ON public.object_stores FOR EACH ROW EXECUTE FUNCTION public.object_stores_touch_updated_at();

--
--

DROP TRIGGER IF EXISTS publishing_adapters_touch_updated_at_trg ON public.publishing_adapters;
CREATE TRIGGER publishing_adapters_touch_updated_at_trg BEFORE UPDATE ON public.publishing_adapters FOR EACH ROW EXECUTE FUNCTION public.publishing_adapters_touch_updated_at();

--
--

DROP TRIGGER IF EXISTS qa_gates_touch_updated_at_trg ON public.qa_gates;
CREATE TRIGGER qa_gates_touch_updated_at_trg BEFORE UPDATE ON public.qa_gates FOR EACH ROW EXECUTE FUNCTION public.qa_gates_touch_updated_at();

--
--

DROP TRIGGER IF EXISTS retention_policies_touch_updated_at_trg ON public.retention_policies;
CREATE TRIGGER retention_policies_touch_updated_at_trg BEFORE UPDATE ON public.retention_policies FOR EACH ROW EXECUTE FUNCTION public.retention_policies_touch_updated_at();

--
--

DROP TRIGGER IF EXISTS webhook_endpoints_touch_updated_at_trg ON public.webhook_endpoints;
CREATE TRIGGER webhook_endpoints_touch_updated_at_trg BEFORE UPDATE ON public.webhook_endpoints FOR EACH ROW EXECUTE FUNCTION public.webhook_endpoints_touch_updated_at();

--
--

ALTER TABLE ONLY public.campaign_email_logs
    ADD CONSTRAINT campaign_email_logs_subscriber_id_fkey FOREIGN KEY (subscriber_id) REFERENCES public.newsletter_subscribers(id) ON DELETE CASCADE;

--
--

ALTER TABLE ONLY public.capability_outcomes
    ADD CONSTRAINT capability_outcomes_variant_id_fkey FOREIGN KEY (variant_id) REFERENCES public.experiment_variants(id) ON DELETE SET NULL;

--
--

ALTER TABLE ONLY public.discovery_runs
    ADD CONSTRAINT discovery_runs_batch_id_fkey FOREIGN KEY (batch_id) REFERENCES public.topic_batches(id);

--
--

ALTER TABLE ONLY public.discovery_runs
    ADD CONSTRAINT discovery_runs_niche_id_fkey FOREIGN KEY (niche_id) REFERENCES public.niches(id) ON DELETE CASCADE;

--
--

ALTER TABLE ONLY public.experiment_variants
    ADD CONSTRAINT experiment_variants_experiment_id_fkey FOREIGN KEY (experiment_id) REFERENCES public.experiments(id) ON DELETE CASCADE;

--
--

ALTER TABLE ONLY public.external_metrics
    ADD CONSTRAINT fk_external_metrics_post FOREIGN KEY (post_id) REFERENCES public.posts(id) ON DELETE SET NULL;

--
--

ALTER TABLE ONLY public.post_performance
    ADD CONSTRAINT fk_post_performance_post FOREIGN KEY (post_id) REFERENCES public.posts(id) ON DELETE CASCADE;

--
--

ALTER TABLE ONLY public.internal_topic_candidates
    ADD CONSTRAINT internal_topic_candidates_batch_id_fkey FOREIGN KEY (batch_id) REFERENCES public.topic_batches(id) ON DELETE CASCADE;

--
--

ALTER TABLE ONLY public.internal_topic_candidates
    ADD CONSTRAINT internal_topic_candidates_carried_from_batch_id_fkey FOREIGN KEY (carried_from_batch_id) REFERENCES public.topic_batches(id);

--
--

ALTER TABLE ONLY public.internal_topic_candidates
    ADD CONSTRAINT internal_topic_candidates_niche_id_fkey FOREIGN KEY (niche_id) REFERENCES public.niches(id) ON DELETE CASCADE;

--
--

ALTER TABLE ONLY public.media_approvals
    ADD CONSTRAINT media_approvals_post_id_fkey FOREIGN KEY (post_id) REFERENCES public.posts(id) ON DELETE CASCADE;

--
--

ALTER TABLE ONLY public.media_assets
    ADD CONSTRAINT media_assets_post_id_fkey FOREIGN KEY (post_id) REFERENCES public.posts(id) ON DELETE SET NULL;

--
--

ALTER TABLE ONLY public.niche_goals
    ADD CONSTRAINT niche_goals_niche_id_fkey FOREIGN KEY (niche_id) REFERENCES public.niches(id) ON DELETE CASCADE;

--
--

ALTER TABLE ONLY public.niche_sources
    ADD CONSTRAINT niche_sources_niche_id_fkey FOREIGN KEY (niche_id) REFERENCES public.niches(id) ON DELETE CASCADE;

--
--

ALTER TABLE ONLY public.oauth_authorization_codes
    ADD CONSTRAINT oauth_authorization_codes_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.oauth_clients(client_id) ON DELETE CASCADE;

--
--

ALTER TABLE ONLY public.pipeline_distributions
    ADD CONSTRAINT pipeline_distributions_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.pipeline_tasks(task_id) ON DELETE CASCADE;

--
--

ALTER TABLE ONLY public.pipeline_tasks
    ADD CONSTRAINT pipeline_tasks_topic_batch_id_fkey FOREIGN KEY (topic_batch_id) REFERENCES public.topic_batches(id);

--
--

ALTER TABLE ONLY public.pipeline_versions
    ADD CONSTRAINT pipeline_versions_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.pipeline_tasks(task_id) ON DELETE CASCADE;

--
--

--
--

ALTER TABLE ONLY public.post_tags
    ADD CONSTRAINT post_tags_post_id_fkey FOREIGN KEY (post_id) REFERENCES public.posts(id) ON DELETE CASCADE;

--
--

ALTER TABLE ONLY public.post_tags
    ADD CONSTRAINT post_tags_tag_id_fkey FOREIGN KEY (tag_id) REFERENCES public.tags(id) ON DELETE CASCADE;

--
--

ALTER TABLE ONLY public.topic_batches
    ADD CONSTRAINT topic_batches_niche_id_fkey FOREIGN KEY (niche_id) REFERENCES public.niches(id) ON DELETE CASCADE;

--
--

ALTER TABLE ONLY public.topic_candidates
    ADD CONSTRAINT topic_candidates_batch_id_fkey FOREIGN KEY (batch_id) REFERENCES public.topic_batches(id) ON DELETE CASCADE;

--
--

ALTER TABLE ONLY public.topic_candidates
    ADD CONSTRAINT topic_candidates_carried_from_batch_id_fkey FOREIGN KEY (carried_from_batch_id) REFERENCES public.topic_batches(id);

--
--

ALTER TABLE ONLY public.topic_candidates
    ADD CONSTRAINT topic_candidates_niche_id_fkey FOREIGN KEY (niche_id) REFERENCES public.niches(id) ON DELETE CASCADE;

--
--

