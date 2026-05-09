--
--

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

--
--

CREATE TABLE IF NOT EXISTS public.access (
    id bigint NOT NULL,
    user_id bigint,
    repo_id bigint,
    mode integer
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.access_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.access_id_seq OWNED BY public.access.id;

--
--

CREATE TABLE IF NOT EXISTS public.access_token (
    id bigint NOT NULL,
    uid bigint,
    name character varying(255),
    token_hash character varying(255),
    token_salt character varying(255),
    token_last_eight character varying(255),
    scope character varying(255),
    created_unix bigint,
    updated_unix bigint
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.access_token_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.access_token_id_seq OWNED BY public.access_token.id;

--
--

CREATE TABLE IF NOT EXISTS public.action (
    id bigint NOT NULL,
    user_id bigint,
    op_type integer,
    act_user_id bigint,
    repo_id bigint,
    comment_id bigint,
    is_deleted boolean DEFAULT false NOT NULL,
    ref_name character varying(255),
    is_private boolean DEFAULT false NOT NULL,
    content text,
    created_unix bigint
);

--
--

CREATE TABLE IF NOT EXISTS public.action_artifact (
    id bigint NOT NULL,
    run_id bigint,
    runner_id bigint,
    repo_id bigint,
    owner_id bigint,
    commit_sha character varying(255),
    storage_path character varying(255),
    file_size bigint,
    file_compressed_size bigint,
    content_encoding character varying(255),
    artifact_path character varying(255),
    artifact_name character varying(255),
    status bigint,
    created_unix bigint,
    updated_unix bigint,
    expired_unix bigint
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.action_artifact_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.action_artifact_id_seq OWNED BY public.action_artifact.id;

--
--

CREATE SEQUENCE IF NOT EXISTS public.action_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.action_id_seq OWNED BY public.action.id;

--
--

CREATE TABLE IF NOT EXISTS public.action_run (
    id bigint NOT NULL,
    title character varying(255),
    repo_id bigint,
    owner_id bigint,
    workflow_id character varying(255),
    index bigint,
    trigger_user_id bigint,
    schedule_id bigint,
    ref character varying(255),
    commit_sha character varying(255),
    is_fork_pull_request boolean,
    need_approval boolean,
    approved_by bigint,
    event character varying(255),
    event_payload text,
    trigger_event character varying(255),
    status integer,
    version integer DEFAULT 0,
    started bigint,
    stopped bigint,
    previous_duration bigint,
    created bigint,
    updated bigint
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.action_run_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.action_run_id_seq OWNED BY public.action_run.id;

--
--

CREATE TABLE IF NOT EXISTS public.action_run_index (
    group_id bigint NOT NULL,
    max_index bigint
);

--
--

CREATE TABLE IF NOT EXISTS public.action_run_job (
    id bigint NOT NULL,
    run_id bigint,
    repo_id bigint,
    owner_id bigint,
    commit_sha character varying(255),
    is_fork_pull_request boolean,
    name character varying(255),
    attempt bigint,
    workflow_payload bytea,
    job_id character varying(255),
    needs text,
    runs_on text,
    task_id bigint,
    status integer,
    started bigint,
    stopped bigint,
    created bigint,
    updated bigint
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.action_run_job_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.action_run_job_id_seq OWNED BY public.action_run_job.id;

--
--

CREATE TABLE IF NOT EXISTS public.action_runner (
    id bigint NOT NULL,
    uuid character(36),
    name character varying(255),
    version character varying(64),
    owner_id bigint,
    repo_id bigint,
    description text,
    base integer,
    repo_range character varying(255),
    token_hash character varying(255),
    token_salt character varying(255),
    last_online bigint,
    last_active bigint,
    agent_labels text,
    ephemeral boolean DEFAULT false NOT NULL,
    created bigint,
    updated bigint,
    deleted bigint
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.action_runner_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.action_runner_id_seq OWNED BY public.action_runner.id;

--
--

CREATE TABLE IF NOT EXISTS public.action_runner_token (
    id bigint NOT NULL,
    token character varying(255),
    owner_id bigint,
    repo_id bigint,
    is_active boolean,
    created bigint,
    updated bigint,
    deleted bigint
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.action_runner_token_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.action_runner_token_id_seq OWNED BY public.action_runner_token.id;

--
--

CREATE TABLE IF NOT EXISTS public.action_schedule (
    id bigint NOT NULL,
    title character varying(255),
    specs text,
    repo_id bigint,
    owner_id bigint,
    workflow_id character varying(255),
    trigger_user_id bigint,
    ref character varying(255),
    commit_sha character varying(255),
    event character varying(255),
    event_payload text,
    content bytea,
    created bigint,
    updated bigint
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.action_schedule_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.action_schedule_id_seq OWNED BY public.action_schedule.id;

--
--

CREATE TABLE IF NOT EXISTS public.action_schedule_spec (
    id bigint NOT NULL,
    repo_id bigint,
    schedule_id bigint,
    next bigint,
    prev bigint,
    spec character varying(255),
    created bigint,
    updated bigint
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.action_schedule_spec_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.action_schedule_spec_id_seq OWNED BY public.action_schedule_spec.id;

--
--

CREATE TABLE IF NOT EXISTS public.action_task (
    id bigint NOT NULL,
    job_id bigint,
    attempt bigint,
    runner_id bigint,
    status integer,
    started bigint,
    stopped bigint,
    repo_id bigint,
    owner_id bigint,
    commit_sha character varying(255),
    is_fork_pull_request boolean,
    token_hash character varying(255),
    token_salt character varying(255),
    token_last_eight character varying(255),
    log_filename character varying(255),
    log_in_storage boolean,
    log_length bigint,
    log_size bigint,
    log_indexes bytea,
    log_expired boolean,
    created bigint,
    updated bigint
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.action_task_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.action_task_id_seq OWNED BY public.action_task.id;

--
--

CREATE TABLE IF NOT EXISTS public.action_task_output (
    id bigint NOT NULL,
    task_id bigint,
    output_key character varying(255),
    output_value text
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.action_task_output_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.action_task_output_id_seq OWNED BY public.action_task_output.id;

--
--

CREATE TABLE IF NOT EXISTS public.action_task_step (
    id bigint NOT NULL,
    name character varying(255),
    task_id bigint,
    index bigint,
    repo_id bigint,
    status integer,
    log_index bigint,
    log_length bigint,
    started bigint,
    stopped bigint,
    created bigint,
    updated bigint
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.action_task_step_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.action_task_step_id_seq OWNED BY public.action_task_step.id;

--
--

CREATE TABLE IF NOT EXISTS public.action_tasks_version (
    id bigint NOT NULL,
    owner_id bigint,
    repo_id bigint,
    version bigint,
    created_unix bigint,
    updated_unix bigint
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.action_tasks_version_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.action_tasks_version_id_seq OWNED BY public.action_tasks_version.id;

--
--

CREATE TABLE IF NOT EXISTS public.action_variable (
    id bigint NOT NULL,
    owner_id bigint,
    repo_id bigint,
    name character varying(255) NOT NULL,
    data text NOT NULL,
    description text,
    created_unix bigint NOT NULL,
    updated_unix bigint
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.action_variable_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.action_variable_id_seq OWNED BY public.action_variable.id;

--
--

CREATE TABLE IF NOT EXISTS public.affiliate_links (
    id integer NOT NULL,
    keyword character varying(255) NOT NULL,
    url text NOT NULL,
    merchant character varying(100),
    commission_rate numeric(5,2),
    active boolean DEFAULT true,
    click_count integer DEFAULT 0,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.affiliate_links_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.affiliate_links_id_seq OWNED BY public.affiliate_links.id;

--
--

CREATE TABLE IF NOT EXISTS public.agent_permissions (
    id integer NOT NULL,
    agent_name character varying(100) NOT NULL,
    resource character varying(100) NOT NULL,
    action character varying(20) NOT NULL,
    allowed boolean DEFAULT false,
    requires_approval boolean DEFAULT false,
    description text DEFAULT ''::text,
    created_at timestamp with time zone DEFAULT now()
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.agent_permissions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.agent_permissions_id_seq OWNED BY public.agent_permissions.id;

--
--

CREATE TABLE IF NOT EXISTS public.agent_status (
    id integer NOT NULL,
    agent_name character varying(255) NOT NULL,
    status character varying(50) DEFAULT 'idle'::character varying NOT NULL,
    current_task_id character varying(255),
    last_heartbeat timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    metadata jsonb DEFAULT '{}'::jsonb,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.agent_status_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.agent_status_id_seq OWNED BY public.agent_status.id;

--
--

CREATE TABLE IF NOT EXISTS public.alert_actions (
    id integer NOT NULL,
    pattern character varying(200) NOT NULL,
    description text DEFAULT ''::text,
    action_type character varying(50) NOT NULL,
    action_config jsonb DEFAULT '{}'::jsonb,
    enabled boolean DEFAULT true,
    escalate_after_failures integer DEFAULT 3,
    cooldown_minutes integer DEFAULT 30,
    last_triggered_at timestamp with time zone,
    last_resolved_at timestamp with time zone,
    consecutive_failures integer DEFAULT 0,
    total_triggers integer DEFAULT 0,
    total_auto_resolved integer DEFAULT 0,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.alert_actions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.alert_actions_id_seq OWNED BY public.alert_actions.id;

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

CREATE TABLE IF NOT EXISTS public.alert_rules (
    id integer NOT NULL,
    name text NOT NULL,
    promql_query text NOT NULL,
    threshold numeric NOT NULL,
    duration text DEFAULT '0m'::text NOT NULL,
    severity text DEFAULT 'warning'::text NOT NULL,
    enabled boolean DEFAULT true NOT NULL,
    labels_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    annotations_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.alert_rules_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.alert_rules_id_seq OWNED BY public.alert_rules.id;

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

CREATE TABLE IF NOT EXISTS public.approval_queue (
    id bigint NOT NULL,
    agent_name character varying(100) NOT NULL,
    resource character varying(100) NOT NULL,
    action character varying(20) NOT NULL,
    proposed_change jsonb NOT NULL,
    reason text DEFAULT ''::text,
    status character varying(20) DEFAULT 'pending'::character varying,
    reviewed_by character varying(100),
    reviewed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now()
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.approval_queue_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.approval_queue_id_seq OWNED BY public.approval_queue.id;

--
--

CREATE TABLE IF NOT EXISTS public.attachment (
    id bigint NOT NULL,
    uuid uuid,
    repo_id bigint,
    issue_id bigint,
    release_id bigint,
    uploader_id bigint DEFAULT 0,
    comment_id bigint,
    name character varying(255),
    download_count bigint DEFAULT 0,
    size bigint DEFAULT 0,
    created_unix bigint
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.attachment_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.attachment_id_seq OWNED BY public.attachment.id;

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

CREATE TABLE IF NOT EXISTS public.audit_log_summaries (
    id bigint NOT NULL,
    bucket_start timestamp with time zone NOT NULL,
    bucket_end timestamp with time zone NOT NULL,
    row_count integer NOT NULL,
    event_type_counts jsonb DEFAULT '{}'::jsonb NOT NULL,
    severity_counts jsonb DEFAULT '{}'::jsonb NOT NULL,
    top_sources jsonb DEFAULT '[]'::jsonb NOT NULL,
    error_excerpts jsonb DEFAULT '[]'::jsonb NOT NULL,
    summary_text text NOT NULL,
    summary_method character varying(32) DEFAULT 'ollama'::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.audit_log_summaries_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.audit_log_summaries_id_seq OWNED BY public.audit_log_summaries.id;

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

CREATE TABLE IF NOT EXISTS public.badge (
    id bigint NOT NULL,
    slug character varying(255),
    description character varying(255),
    image_url character varying(255)
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.badge_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.badge_id_seq OWNED BY public.badge.id;

--
--

CREATE TABLE IF NOT EXISTS public.brain_decision_summaries (
    id bigint NOT NULL,
    bucket_start timestamp with time zone NOT NULL,
    bucket_end timestamp with time zone NOT NULL,
    row_count integer NOT NULL,
    outcome_counts jsonb DEFAULT '{}'::jsonb NOT NULL,
    avg_confidence double precision,
    decision_excerpts jsonb DEFAULT '[]'::jsonb NOT NULL,
    summary_text text NOT NULL,
    summary_method character varying(32) DEFAULT 'ollama'::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.brain_decision_summaries_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.brain_decision_summaries_id_seq OWNED BY public.brain_decision_summaries.id;

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

CREATE TABLE IF NOT EXISTS public.branch (
    id bigint NOT NULL,
    repo_id bigint,
    name character varying(255) NOT NULL,
    commit_id character varying(255),
    commit_message text,
    pusher_id bigint,
    is_deleted boolean,
    deleted_by_id bigint,
    deleted_unix bigint,
    commit_time bigint,
    created_unix bigint,
    updated_unix bigint
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.branch_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.branch_id_seq OWNED BY public.branch.id;

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

CREATE TABLE IF NOT EXISTS public.capability_executions (
    id character varying(36) NOT NULL,
    task_id character varying(36) NOT NULL,
    owner_id character varying(255) NOT NULL,
    status character varying(50) NOT NULL,
    error_message text,
    step_results jsonb DEFAULT '[]'::jsonb,
    final_outputs jsonb DEFAULT '{}'::jsonb,
    total_duration_ms double precision,
    progress_percent integer DEFAULT 0,
    completed_steps integer DEFAULT 0,
    total_steps integer NOT NULL,
    cost_cents integer,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    completed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    tags jsonb DEFAULT '[]'::jsonb,
    metadata jsonb DEFAULT '{}'::jsonb
);

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
    created_at timestamp with time zone DEFAULT now() NOT NULL
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

CREATE TABLE IF NOT EXISTS public.capability_tasks (
    id character varying(36) NOT NULL,
    name character varying(255) NOT NULL,
    description text,
    owner_id character varying(255) NOT NULL,
    steps jsonb NOT NULL,
    tags jsonb DEFAULT '[]'::jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    is_active boolean DEFAULT true,
    version integer DEFAULT 1,
    estimated_cost_cents integer,
    avg_duration_ms double precision,
    execution_count integer DEFAULT 0,
    success_count integer DEFAULT 0,
    failure_count integer DEFAULT 0,
    last_executed_at timestamp with time zone
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

CREATE TABLE IF NOT EXISTS public.checkpoint_migrations (
    v integer NOT NULL
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

CREATE TABLE IF NOT EXISTS public.collaboration (
    id bigint NOT NULL,
    repo_id bigint NOT NULL,
    user_id bigint NOT NULL,
    mode integer DEFAULT 2 NOT NULL,
    created_unix bigint,
    updated_unix bigint
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.collaboration_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.collaboration_id_seq OWNED BY public.collaboration.id;

--
--

CREATE TABLE IF NOT EXISTS public.comment (
    id bigint NOT NULL,
    type integer,
    poster_id bigint,
    original_author character varying(255),
    original_author_id bigint,
    issue_id bigint,
    label_id bigint,
    old_project_id bigint,
    project_id bigint,
    old_milestone_id bigint,
    milestone_id bigint,
    time_id bigint,
    assignee_id bigint,
    removed_assignee boolean,
    assignee_team_id bigint DEFAULT 0 NOT NULL,
    resolve_doer_id bigint,
    old_title character varying(255),
    new_title character varying(255),
    old_ref character varying(255),
    new_ref character varying(255),
    dependent_issue_id bigint,
    commit_id bigint,
    line bigint,
    tree_path character varying(4000),
    content text,
    content_version integer DEFAULT 0 NOT NULL,
    patch text,
    created_unix bigint,
    updated_unix bigint,
    commit_sha character varying(64),
    review_id bigint,
    invalidated boolean,
    ref_repo_id bigint,
    ref_issue_id bigint,
    ref_comment_id bigint,
    ref_action smallint,
    ref_is_pull boolean,
    comment_meta_data text
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.comment_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.comment_id_seq OWNED BY public.comment.id;

--
--

CREATE TABLE IF NOT EXISTS public.commit_status (
    id bigint NOT NULL,
    index bigint,
    repo_id bigint,
    state character varying(7) NOT NULL,
    sha character varying(64) NOT NULL,
    target_url text,
    description text,
    context_hash character varying(64),
    context text,
    creator_id bigint,
    created_unix bigint,
    updated_unix bigint
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.commit_status_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.commit_status_id_seq OWNED BY public.commit_status.id;

--
--

CREATE TABLE IF NOT EXISTS public.commit_status_index (
    id bigint NOT NULL,
    repo_id bigint,
    sha character varying(255),
    max_index bigint
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.commit_status_index_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.commit_status_index_id_seq OWNED BY public.commit_status_index.id;

--
--

CREATE TABLE IF NOT EXISTS public.commit_status_summary (
    id bigint NOT NULL,
    repo_id bigint,
    sha character varying(64) NOT NULL,
    state character varying(7) NOT NULL,
    target_url text
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.commit_status_summary_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.commit_status_summary_id_seq OWNED BY public.commit_status_summary.id;

--
--

CREATE TABLE IF NOT EXISTS public.content_calendar (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid,
    site_id uuid,
    date date NOT NULL,
    content_type character varying(50),
    topic character varying(500),
    notes text,
    priority character varying(20) DEFAULT 'normal'::character varying,
    source character varying(30) DEFAULT 'manual'::character varying,
    auto_generated boolean DEFAULT false,
    task_id character varying(255),
    status character varying(30) DEFAULT 'planned'::character varying,
    created_at timestamp with time zone DEFAULT now()
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

CREATE TABLE IF NOT EXISTS public.pipeline_reviews (
    id integer NOT NULL,
    task_id character varying NOT NULL,
    version integer DEFAULT 1 NOT NULL,
    reviewer character varying NOT NULL,
    decision character varying NOT NULL,
    feedback text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
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
    writer_rag_mode text,
    topic_batch_id uuid,
    template_slug text,
    auto_cancelled_at timestamp with time zone,
    CONSTRAINT pipeline_tasks_writer_rag_mode_check CHECK (((writer_rag_mode IS NULL) OR (writer_rag_mode = ANY (ARRAY['TOPIC_ONLY'::text, 'CITATION_BUDGET'::text, 'STORY_SPINE'::text, 'TWO_PASS'::text, 'DETERMINISTIC_COMPOSITOR'::text]))))
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
    ( SELECT pr.decision
           FROM public.pipeline_reviews pr
          WHERE ((pr.task_id)::text = (pt.task_id)::text)
          ORDER BY pr.created_at DESC
         LIMIT 1) AS approval_status,
    ( SELECT pr.reviewer
           FROM public.pipeline_reviews pr
          WHERE ((pr.task_id)::text = (pt.task_id)::text)
          ORDER BY pr.created_at DESC
         LIMIT 1) AS approved_by,
    ( SELECT pr.feedback
           FROM public.pipeline_reviews pr
          WHERE ((pr.task_id)::text = (pt.task_id)::text)
          ORDER BY pr.created_at DESC
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
    pt.writer_rag_mode,
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

CREATE TABLE IF NOT EXISTS public.custom_workflows (
    id uuid NOT NULL,
    name character varying(255) NOT NULL,
    description text NOT NULL,
    phases jsonb NOT NULL,
    owner_id character varying(255) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    tags jsonb DEFAULT '[]'::jsonb,
    is_template boolean DEFAULT false
);

--
--

CREATE TABLE IF NOT EXISTS public.dbfs_data (
    id bigint NOT NULL,
    revision bigint NOT NULL,
    meta_id bigint NOT NULL,
    blob_offset bigint NOT NULL,
    blob_size bigint NOT NULL,
    blob_data bytea NOT NULL
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.dbfs_data_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.dbfs_data_id_seq OWNED BY public.dbfs_data.id;

--
--

CREATE TABLE IF NOT EXISTS public.dbfs_meta (
    id bigint NOT NULL,
    full_path character varying(500) NOT NULL,
    block_size bigint NOT NULL,
    file_size bigint NOT NULL,
    create_timestamp bigint NOT NULL,
    modify_timestamp bigint NOT NULL
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.dbfs_meta_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.dbfs_meta_id_seq OWNED BY public.dbfs_meta.id;

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

CREATE TABLE IF NOT EXISTS public.deploy_key (
    id bigint NOT NULL,
    key_id bigint,
    repo_id bigint,
    name character varying(255),
    fingerprint character varying(255),
    mode integer DEFAULT 1 NOT NULL,
    created_unix bigint,
    updated_unix bigint
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.deploy_key_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.deploy_key_id_seq OWNED BY public.deploy_key.id;

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

CREATE TABLE IF NOT EXISTS public.distribution_channels (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid,
    site_id uuid,
    platform character varying(50) NOT NULL,
    account_name character varying(255),
    credentials_ref character varying(255),
    config jsonb DEFAULT '{}'::jsonb,
    posting_enabled boolean DEFAULT true,
    optimal_times jsonb DEFAULT '[]'::jsonb,
    last_posted_at timestamp with time zone,
    total_posts integer DEFAULT 0,
    avg_engagement jsonb DEFAULT '{}'::jsonb,
    created_at timestamp with time zone DEFAULT now()
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

CREATE TABLE IF NOT EXISTS public.email_address (
    id bigint NOT NULL,
    uid bigint NOT NULL,
    email character varying(255) NOT NULL,
    lower_email character varying(255) NOT NULL,
    is_activated boolean,
    is_primary boolean DEFAULT false NOT NULL
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.email_address_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.email_address_id_seq OWNED BY public.email_address.id;

--
--

CREATE TABLE IF NOT EXISTS public.email_hash (
    hash character varying(32) NOT NULL,
    email character varying(255) NOT NULL
);

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

CREATE TABLE IF NOT EXISTS public.experiment_assignments (
    id bigint NOT NULL,
    experiment_id uuid NOT NULL,
    subject_id character varying(128) NOT NULL,
    variant_key character varying(64) NOT NULL,
    assigned_at timestamp with time zone DEFAULT now() NOT NULL,
    metrics jsonb DEFAULT '{}'::jsonb NOT NULL
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.experiment_assignments_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.experiment_assignments_id_seq OWNED BY public.experiment_assignments.id;

--
--

CREATE TABLE IF NOT EXISTS public.experiments (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    experiment_type text NOT NULL,
    status text DEFAULT 'running'::text,
    variant_a jsonb NOT NULL,
    variant_b jsonb NOT NULL,
    metric_name text DEFAULT 'views_7d'::text,
    variant_a_value numeric(14,4) DEFAULT NULL::numeric,
    variant_b_value numeric(14,4) DEFAULT NULL::numeric,
    winner text,
    confidence numeric(5,4) DEFAULT NULL::numeric,
    post_id uuid,
    task_id text,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    completed_at timestamp with time zone
);

--
--

CREATE TABLE IF NOT EXISTS public.external_login_user (
    external_id character varying(255) NOT NULL,
    user_id bigint NOT NULL,
    login_source_id bigint NOT NULL,
    raw_data text,
    provider character varying(25),
    email character varying(255),
    name character varying(255),
    first_name character varying(255),
    last_name character varying(255),
    nick_name character varying(255),
    description character varying(255),
    avatar_url text,
    location character varying(255),
    access_token text,
    access_token_secret text,
    refresh_token text,
    expires_at timestamp without time zone
);

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

CREATE TABLE IF NOT EXISTS public.fine_tuning_jobs (
    id integer NOT NULL,
    job_id character varying(255) NOT NULL,
    status character varying(50) DEFAULT 'pending'::character varying NOT NULL,
    target_model character varying(50) NOT NULL,
    model_name character varying(255),
    dataset_id integer,
    dataset_version character varying(100),
    training_config jsonb DEFAULT '{}'::jsonb,
    result_model_id character varying(255),
    result_model_path character varying(500),
    training_examples_count integer,
    estimated_cost numeric(10,2),
    actual_cost numeric(10,2),
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    started_at timestamp with time zone,
    completed_at timestamp with time zone,
    duration_seconds integer,
    error_message text,
    error_code character varying(100),
    process_id character varying(100),
    api_request_id character varying(255),
    created_by character varying(100),
    updated_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.fine_tuning_jobs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.fine_tuning_jobs_id_seq OWNED BY public.fine_tuning_jobs.id;

--
--

CREATE TABLE IF NOT EXISTS public.follow (
    id bigint NOT NULL,
    user_id bigint,
    follow_id bigint,
    created_unix bigint
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.follow_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.follow_id_seq OWNED BY public.follow.id;

--
--

CREATE TABLE IF NOT EXISTS public.gpg_key (
    id bigint NOT NULL,
    owner_id bigint NOT NULL,
    key_id character(16) NOT NULL,
    primary_key_id character(16),
    content text NOT NULL,
    created_unix bigint,
    expired_unix bigint,
    added_unix bigint,
    emails text,
    verified boolean DEFAULT false NOT NULL,
    can_sign boolean,
    can_encrypt_comms boolean,
    can_encrypt_storage boolean,
    can_certify boolean
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.gpg_key_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.gpg_key_id_seq OWNED BY public.gpg_key.id;

--
--

CREATE TABLE IF NOT EXISTS public.gpg_key_import (
    key_id character(16) NOT NULL,
    content text NOT NULL
);

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

CREATE TABLE IF NOT EXISTS public.hook_task (
    id bigint NOT NULL,
    hook_id bigint,
    uuid character varying(255),
    payload_content text,
    payload_version integer DEFAULT 1,
    event_type character varying(255),
    is_delivered boolean,
    delivered bigint,
    is_succeed boolean,
    request_content text,
    response_content text
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.hook_task_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.hook_task_id_seq OWNED BY public.hook_task.id;

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

CREATE TABLE IF NOT EXISTS public.issue (
    id bigint NOT NULL,
    repo_id bigint,
    index bigint,
    poster_id bigint,
    original_author character varying(255),
    original_author_id bigint,
    name character varying(255),
    content text,
    content_version integer DEFAULT 0 NOT NULL,
    milestone_id bigint,
    priority integer,
    is_closed boolean,
    is_pull boolean,
    num_comments integer,
    ref character varying(255),
    deadline_unix bigint,
    created_unix bigint,
    updated_unix bigint,
    closed_unix bigint,
    is_locked boolean DEFAULT false NOT NULL,
    time_estimate bigint DEFAULT 0 NOT NULL
);

--
--

CREATE TABLE IF NOT EXISTS public.issue_assignees (
    id bigint NOT NULL,
    assignee_id bigint,
    issue_id bigint
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.issue_assignees_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.issue_assignees_id_seq OWNED BY public.issue_assignees.id;

--
--

CREATE TABLE IF NOT EXISTS public.issue_content_history (
    id bigint NOT NULL,
    poster_id bigint,
    issue_id bigint,
    comment_id bigint,
    edited_unix bigint,
    content_text text,
    is_first_created boolean,
    is_deleted boolean
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.issue_content_history_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.issue_content_history_id_seq OWNED BY public.issue_content_history.id;

--
--

CREATE TABLE IF NOT EXISTS public.issue_dependency (
    id bigint NOT NULL,
    user_id bigint NOT NULL,
    issue_id bigint NOT NULL,
    dependency_id bigint NOT NULL,
    created_unix bigint,
    updated_unix bigint
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.issue_dependency_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.issue_dependency_id_seq OWNED BY public.issue_dependency.id;

--
--

CREATE SEQUENCE IF NOT EXISTS public.issue_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.issue_id_seq OWNED BY public.issue.id;

--
--

CREATE TABLE IF NOT EXISTS public.issue_index (
    group_id bigint NOT NULL,
    max_index bigint
);

--
--

CREATE TABLE IF NOT EXISTS public.issue_label (
    id bigint NOT NULL,
    issue_id bigint,
    label_id bigint
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.issue_label_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.issue_label_id_seq OWNED BY public.issue_label.id;

--
--

CREATE TABLE IF NOT EXISTS public.issue_pin (
    id bigint NOT NULL,
    repo_id bigint NOT NULL,
    issue_id bigint NOT NULL,
    is_pull boolean NOT NULL,
    pin_order integer DEFAULT 0
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.issue_pin_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.issue_pin_id_seq OWNED BY public.issue_pin.id;

--
--

CREATE TABLE IF NOT EXISTS public.issue_user (
    id bigint NOT NULL,
    uid bigint,
    issue_id bigint,
    is_read boolean,
    is_mentioned boolean
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.issue_user_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.issue_user_id_seq OWNED BY public.issue_user.id;

--
--

CREATE TABLE IF NOT EXISTS public.issue_watch (
    id bigint NOT NULL,
    user_id bigint NOT NULL,
    issue_id bigint NOT NULL,
    is_watching boolean NOT NULL,
    created_unix bigint NOT NULL,
    updated_unix bigint NOT NULL
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.issue_watch_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.issue_watch_id_seq OWNED BY public.issue_watch.id;

--
--

CREATE TABLE IF NOT EXISTS public.jwt_blocklist (
    jti text NOT NULL,
    user_id text NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    blocklisted_at timestamp with time zone DEFAULT now() NOT NULL
);

--
--

CREATE TABLE IF NOT EXISTS public.label (
    id bigint NOT NULL,
    repo_id bigint,
    org_id bigint,
    name character varying(255),
    exclusive boolean,
    exclusive_order integer DEFAULT 0,
    description character varying(255),
    color character varying(7),
    num_issues integer,
    num_closed_issues integer,
    created_unix bigint,
    updated_unix bigint,
    archived_unix bigint
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.label_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.label_id_seq OWNED BY public.label.id;

--
--

CREATE TABLE IF NOT EXISTS public.language_stat (
    id bigint NOT NULL,
    repo_id bigint NOT NULL,
    commit_id character varying(255),
    is_primary boolean,
    language character varying(50) NOT NULL,
    size bigint DEFAULT 0 NOT NULL,
    created_unix bigint
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.language_stat_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.language_stat_id_seq OWNED BY public.language_stat.id;

--
--

CREATE TABLE IF NOT EXISTS public.learning_patterns (
    id integer NOT NULL,
    pattern_id character varying(255) NOT NULL,
    pattern_type character varying(100),
    pattern_description text,
    pattern_rule jsonb,
    support_count integer,
    confidence numeric(3,2),
    lift numeric(5,2),
    related_intents text[],
    related_tags text[],
    improves_quality boolean DEFAULT false,
    improves_success boolean DEFAULT false,
    avg_quality_improvement numeric(3,2),
    discovered_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    last_validated_at timestamp with time zone,
    validation_count integer DEFAULT 0,
    is_active boolean DEFAULT true
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.learning_patterns_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.learning_patterns_id_seq OWNED BY public.learning_patterns.id;

--
--

CREATE TABLE IF NOT EXISTS public.lfs_lock (
    id bigint NOT NULL,
    repo_id bigint NOT NULL,
    owner_id bigint NOT NULL,
    path text,
    created timestamp without time zone
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.lfs_lock_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.lfs_lock_id_seq OWNED BY public.lfs_lock.id;

--
--

CREATE TABLE IF NOT EXISTS public.lfs_meta_object (
    id bigint NOT NULL,
    oid character varying(255) NOT NULL,
    size bigint NOT NULL,
    repository_id bigint NOT NULL,
    created_unix bigint,
    updated_unix bigint
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.lfs_meta_object_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.lfs_meta_object_id_seq OWNED BY public.lfs_meta_object.id;

--
--

CREATE TABLE IF NOT EXISTS public.login_source (
    id bigint NOT NULL,
    type integer,
    name character varying(255),
    is_active boolean DEFAULT false NOT NULL,
    is_sync_enabled boolean DEFAULT false NOT NULL,
    two_factor_policy character varying(255) DEFAULT ''::character varying NOT NULL,
    cfg text,
    created_unix bigint,
    updated_unix bigint
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.login_source_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.login_source_id_seq OWNED BY public.login_source.id;

--
--

CREATE TABLE IF NOT EXISTS public.logs (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    agent_name character varying(255) NOT NULL,
    level character varying(20) NOT NULL,
    message text NOT NULL,
    context jsonb,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP
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

CREATE TABLE IF NOT EXISTS public.milestone (
    id bigint NOT NULL,
    repo_id bigint,
    name character varying(255),
    content text,
    is_closed boolean,
    num_issues integer,
    num_closed_issues integer,
    completeness integer,
    created_unix bigint,
    updated_unix bigint,
    deadline_unix bigint,
    closed_date_unix bigint
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.milestone_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.milestone_id_seq OWNED BY public.milestone.id;

--
--

CREATE TABLE IF NOT EXISTS public.mirror (
    id bigint NOT NULL,
    repo_id bigint,
    "interval" bigint,
    enable_prune boolean DEFAULT true NOT NULL,
    updated_unix bigint,
    next_update_unix bigint,
    lfs_enabled boolean DEFAULT false NOT NULL,
    lfs_endpoint text,
    remote_address character varying(2048)
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.mirror_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.mirror_id_seq OWNED BY public.mirror.id;

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
    marketing_consent boolean DEFAULT false
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
    writer_rag_mode text DEFAULT 'TOPIC_ONLY'::text NOT NULL,
    batch_size integer DEFAULT 5 NOT NULL,
    discovery_cadence_minute_floor integer DEFAULT 60 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT niches_batch_size_check CHECK (((batch_size >= 1) AND (batch_size <= 20))),
    CONSTRAINT niches_discovery_cadence_minute_floor_check CHECK ((discovery_cadence_minute_floor >= 1)),
    CONSTRAINT niches_writer_rag_mode_check CHECK ((writer_rag_mode = ANY (ARRAY['TOPIC_ONLY'::text, 'CITATION_BUDGET'::text, 'STORY_SPINE'::text, 'TWO_PASS'::text, 'DETERMINISTIC_COMPOSITOR'::text])))
);

--
--

CREATE TABLE IF NOT EXISTS public.notice (
    id bigint NOT NULL,
    type integer,
    description text,
    created_unix bigint
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.notice_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.notice_id_seq OWNED BY public.notice.id;

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

CREATE TABLE IF NOT EXISTS public.oauth2_application (
    id bigint NOT NULL,
    uid bigint,
    name character varying(255),
    client_id character varying(255),
    client_secret character varying(255),
    confidential_client boolean DEFAULT true NOT NULL,
    skip_secondary_authorization boolean DEFAULT false NOT NULL,
    redirect_uris text,
    created_unix bigint,
    updated_unix bigint
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.oauth2_application_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.oauth2_application_id_seq OWNED BY public.oauth2_application.id;

--
--

CREATE TABLE IF NOT EXISTS public.oauth2_authorization_code (
    id bigint NOT NULL,
    grant_id bigint,
    code character varying(255),
    code_challenge character varying(255),
    code_challenge_method character varying(255),
    redirect_uri character varying(255),
    valid_until bigint
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.oauth2_authorization_code_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.oauth2_authorization_code_id_seq OWNED BY public.oauth2_authorization_code.id;

--
--

CREATE TABLE IF NOT EXISTS public.oauth2_grant (
    id bigint NOT NULL,
    user_id bigint,
    application_id bigint,
    counter bigint DEFAULT 1 NOT NULL,
    scope text,
    nonce text,
    created_unix bigint,
    updated_unix bigint
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.oauth2_grant_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.oauth2_grant_id_seq OWNED BY public.oauth2_grant.id;

--
--

CREATE TABLE IF NOT EXISTS public.oauth_accounts (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    provider character varying(50) NOT NULL,
    provider_user_id character varying(255) NOT NULL,
    provider_data jsonb DEFAULT '{}'::jsonb,
    created_at timestamp with time zone DEFAULT now(),
    last_used timestamp with time zone DEFAULT now()
);

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

CREATE TABLE IF NOT EXISTS public.org_user (
    id bigint NOT NULL,
    uid bigint,
    org_id bigint,
    is_public boolean
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.org_user_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.org_user_id_seq OWNED BY public.org_user.id;

--
--

CREATE TABLE IF NOT EXISTS public.package (
    id bigint NOT NULL,
    owner_id bigint NOT NULL,
    repo_id bigint,
    type character varying(255) NOT NULL,
    name character varying(255) NOT NULL,
    lower_name character varying(255) NOT NULL,
    semver_compatible boolean DEFAULT false NOT NULL,
    is_internal boolean DEFAULT false NOT NULL
);

--
--

CREATE TABLE IF NOT EXISTS public.package_blob (
    id bigint NOT NULL,
    size bigint DEFAULT 0 NOT NULL,
    hash_md5 character(32) NOT NULL,
    hash_sha1 character(40) NOT NULL,
    hash_sha256 character(64) NOT NULL,
    hash_sha512 character(128) NOT NULL,
    created_unix bigint NOT NULL
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.package_blob_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.package_blob_id_seq OWNED BY public.package_blob.id;

--
--

CREATE TABLE IF NOT EXISTS public.package_blob_upload (
    id character varying(255) NOT NULL,
    bytes_received bigint DEFAULT 0 NOT NULL,
    hash_state_bytes bytea,
    created_unix bigint NOT NULL,
    updated_unix bigint NOT NULL
);

--
--

CREATE TABLE IF NOT EXISTS public.package_cleanup_rule (
    id bigint NOT NULL,
    enabled boolean DEFAULT false NOT NULL,
    owner_id bigint DEFAULT 0 NOT NULL,
    type character varying(255) NOT NULL,
    keep_count integer DEFAULT 0 NOT NULL,
    keep_pattern character varying(255) DEFAULT ''::character varying NOT NULL,
    remove_days integer DEFAULT 0 NOT NULL,
    remove_pattern character varying(255) DEFAULT ''::character varying NOT NULL,
    match_full_name boolean DEFAULT false NOT NULL,
    created_unix bigint DEFAULT 0 NOT NULL,
    updated_unix bigint DEFAULT 0 NOT NULL
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.package_cleanup_rule_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.package_cleanup_rule_id_seq OWNED BY public.package_cleanup_rule.id;

--
--

CREATE TABLE IF NOT EXISTS public.package_file (
    id bigint NOT NULL,
    version_id bigint NOT NULL,
    blob_id bigint NOT NULL,
    name character varying(255) NOT NULL,
    lower_name character varying(255) NOT NULL,
    composite_key character varying(255),
    is_lead boolean DEFAULT false NOT NULL,
    created_unix bigint NOT NULL
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.package_file_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.package_file_id_seq OWNED BY public.package_file.id;

--
--

CREATE SEQUENCE IF NOT EXISTS public.package_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.package_id_seq OWNED BY public.package.id;

--
--

CREATE TABLE IF NOT EXISTS public.package_property (
    id bigint NOT NULL,
    ref_type bigint NOT NULL,
    ref_id bigint NOT NULL,
    name character varying(255) NOT NULL,
    value text NOT NULL
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.package_property_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.package_property_id_seq OWNED BY public.package_property.id;

--
--

CREATE TABLE IF NOT EXISTS public.package_version (
    id bigint NOT NULL,
    package_id bigint NOT NULL,
    creator_id bigint DEFAULT 0 NOT NULL,
    version character varying(255) NOT NULL,
    lower_version character varying(255) NOT NULL,
    created_unix bigint NOT NULL,
    is_internal boolean DEFAULT false NOT NULL,
    metadata_json text,
    download_count bigint DEFAULT 0 NOT NULL
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.package_version_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.package_version_id_seq OWNED BY public.package_version.id;

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

CREATE TABLE IF NOT EXISTS public.pipeline_experiments (
    id integer NOT NULL,
    name character varying(200) NOT NULL,
    description text DEFAULT ''::text,
    stage_key character varying(100) NOT NULL,
    variant_a jsonb DEFAULT '{}'::jsonb NOT NULL,
    variant_b jsonb DEFAULT '{}'::jsonb NOT NULL,
    traffic_split_pct integer DEFAULT 50,
    is_active boolean DEFAULT false,
    results_a jsonb DEFAULT '{"runs": 0, "passes": 0, "avg_score": 0}'::jsonb,
    results_b jsonb DEFAULT '{"runs": 0, "passes": 0, "avg_score": 0}'::jsonb,
    started_at timestamp with time zone,
    ended_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.pipeline_experiments_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.pipeline_experiments_id_seq OWNED BY public.pipeline_experiments.id;

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

CREATE SEQUENCE IF NOT EXISTS public.pipeline_reviews_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.pipeline_reviews_id_seq OWNED BY public.pipeline_reviews.id;

--
--

CREATE TABLE IF NOT EXISTS public.pipeline_run_log (
    id bigint NOT NULL,
    task_id character varying(255) NOT NULL,
    stage_key character varying(100) NOT NULL,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    finished_at timestamp with time zone,
    duration_ms integer,
    result character varying(20) DEFAULT 'pending'::character varying,
    score double precision,
    experiment_id integer,
    experiment_variant character varying(1) DEFAULT NULL::character varying,
    details jsonb DEFAULT '{}'::jsonb
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.pipeline_run_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.pipeline_run_log_id_seq OWNED BY public.pipeline_run_log.id;

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
    ( SELECT pr.decision
           FROM public.pipeline_reviews pr
          WHERE ((pr.task_id)::text = (pt.task_id)::text)
          ORDER BY pr.created_at DESC
         LIMIT 1) AS approval_status,
    ( SELECT pr.reviewer
           FROM public.pipeline_reviews pr
          WHERE ((pr.task_id)::text = (pt.task_id)::text)
          ORDER BY pr.created_at DESC
         LIMIT 1) AS approved_by,
    ( SELECT pr.feedback
           FROM public.pipeline_reviews pr
          WHERE ((pr.task_id)::text = (pt.task_id)::text)
          ORDER BY pr.created_at DESC
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

CREATE TABLE IF NOT EXISTS public.post_approval_gates (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    post_id uuid NOT NULL,
    gate_name text NOT NULL,
    ordinal integer NOT NULL,
    state text DEFAULT 'pending'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    decided_at timestamp with time zone,
    approver text,
    notes text,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL
);

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

CREATE TABLE IF NOT EXISTS public.posts (
    id uuid NOT NULL,
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
    cli_idempotency_key text
);

--
--

CREATE TABLE IF NOT EXISTS public.project (
    id bigint NOT NULL,
    title character varying(255) NOT NULL,
    description text,
    owner_id bigint,
    repo_id bigint,
    creator_id bigint NOT NULL,
    is_closed boolean,
    board_type bigint,
    card_type bigint,
    type bigint,
    created_unix bigint,
    updated_unix bigint,
    closed_date_unix bigint
);

--
--

CREATE TABLE IF NOT EXISTS public.project_board (
    id bigint NOT NULL,
    title character varying(255),
    "default" boolean DEFAULT false NOT NULL,
    sorting integer DEFAULT 0 NOT NULL,
    color character varying(7),
    project_id bigint NOT NULL,
    creator_id bigint NOT NULL,
    created_unix bigint,
    updated_unix bigint
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.project_board_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.project_board_id_seq OWNED BY public.project_board.id;

--
--

CREATE SEQUENCE IF NOT EXISTS public.project_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.project_id_seq OWNED BY public.project.id;

--
--

CREATE TABLE IF NOT EXISTS public.project_issue (
    id bigint NOT NULL,
    issue_id bigint,
    project_id bigint,
    project_board_id bigint,
    sorting bigint DEFAULT 0 NOT NULL
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.project_issue_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.project_issue_id_seq OWNED BY public.project_issue.id;

--
--

CREATE TABLE IF NOT EXISTS public.protected_branch (
    id bigint NOT NULL,
    repo_id bigint,
    branch_name character varying(255),
    priority bigint DEFAULT 0 NOT NULL,
    can_push boolean DEFAULT false NOT NULL,
    enable_whitelist boolean,
    whitelist_user_i_ds text,
    whitelist_team_i_ds text,
    enable_merge_whitelist boolean DEFAULT false NOT NULL,
    whitelist_deploy_keys boolean DEFAULT false NOT NULL,
    merge_whitelist_user_i_ds text,
    merge_whitelist_team_i_ds text,
    can_force_push boolean DEFAULT false NOT NULL,
    enable_force_push_allowlist boolean DEFAULT false NOT NULL,
    force_push_allowlist_user_i_ds text,
    force_push_allowlist_team_i_ds text,
    force_push_allowlist_deploy_keys boolean DEFAULT false NOT NULL,
    enable_status_check boolean DEFAULT false NOT NULL,
    status_check_contexts text,
    enable_approvals_whitelist boolean DEFAULT false NOT NULL,
    approvals_whitelist_user_i_ds text,
    approvals_whitelist_team_i_ds text,
    required_approvals bigint DEFAULT 0 NOT NULL,
    block_on_rejected_reviews boolean DEFAULT false NOT NULL,
    block_on_official_review_requests boolean DEFAULT false NOT NULL,
    block_on_outdated_branch boolean DEFAULT false NOT NULL,
    dismiss_stale_approvals boolean DEFAULT false NOT NULL,
    ignore_stale_approvals boolean DEFAULT false NOT NULL,
    require_signed_commits boolean DEFAULT false NOT NULL,
    protected_file_patterns text,
    unprotected_file_patterns text,
    block_admin_merge_override boolean DEFAULT false NOT NULL,
    created_unix bigint,
    updated_unix bigint
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.protected_branch_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.protected_branch_id_seq OWNED BY public.protected_branch.id;

--
--

CREATE TABLE IF NOT EXISTS public.protected_tag (
    id bigint NOT NULL,
    repo_id bigint,
    name_pattern character varying(255),
    allowlist_user_i_ds text,
    allowlist_team_i_ds text,
    created_unix bigint,
    updated_unix bigint
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.protected_tag_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.protected_tag_id_seq OWNED BY public.protected_tag.id;

--
--

CREATE TABLE IF NOT EXISTS public.public_key (
    id bigint NOT NULL,
    owner_id bigint NOT NULL,
    name character varying(255) NOT NULL,
    fingerprint character varying(255) NOT NULL,
    content text NOT NULL,
    mode integer DEFAULT 2 NOT NULL,
    type integer DEFAULT 1 NOT NULL,
    login_source_id bigint DEFAULT 0 NOT NULL,
    created_unix bigint,
    updated_unix bigint,
    verified boolean DEFAULT false NOT NULL
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.public_key_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.public_key_id_seq OWNED BY public.public_key.id;

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
    metrics jsonb DEFAULT '{}'::jsonb NOT NULL
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

CREATE TABLE IF NOT EXISTS public.pull_auto_merge (
    id bigint NOT NULL,
    pull_id bigint,
    doer_id bigint NOT NULL,
    merge_style character varying(30),
    message text,
    delete_branch_after_merge boolean,
    created_unix bigint
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.pull_auto_merge_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.pull_auto_merge_id_seq OWNED BY public.pull_auto_merge.id;

--
--

CREATE TABLE IF NOT EXISTS public.pull_request (
    id bigint NOT NULL,
    type integer,
    status integer,
    conflicted_files text,
    commits_ahead integer,
    commits_behind integer,
    changed_protected_files text,
    issue_id bigint,
    index bigint,
    head_repo_id bigint,
    base_repo_id bigint,
    head_branch character varying(255),
    base_branch character varying(255),
    merge_base character varying(64),
    allow_maintainer_edit boolean DEFAULT false NOT NULL,
    has_merged boolean,
    merged_commit_id character varying(64),
    merger_id bigint,
    merged_unix bigint,
    flow integer DEFAULT 0 NOT NULL
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.pull_request_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.pull_request_id_seq OWNED BY public.pull_request.id;

--
--

CREATE TABLE IF NOT EXISTS public.push_mirror (
    id bigint NOT NULL,
    repo_id bigint,
    remote_name character varying(255),
    remote_address character varying(2048),
    sync_on_commit boolean DEFAULT true NOT NULL,
    "interval" bigint,
    created_unix bigint,
    last_update bigint,
    last_error text
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.push_mirror_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.push_mirror_id_seq OWNED BY public.push_mirror.id;

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

CREATE TABLE IF NOT EXISTS public.quality_improvement_logs (
    id integer NOT NULL,
    content_id character varying(255) NOT NULL,
    initial_score numeric(3,1) NOT NULL,
    improved_score numeric(3,1) NOT NULL,
    score_improvement numeric(3,1) NOT NULL,
    best_improved_criterion character varying(50),
    best_improvement_points numeric(3,1),
    refinement_type character varying(100),
    changes_made text,
    refinement_timestamp timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    passed_after_refinement boolean DEFAULT false NOT NULL
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.quality_improvement_logs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.quality_improvement_logs_id_seq OWNED BY public.quality_improvement_logs.id;

--
--

CREATE TABLE IF NOT EXISTS public.quality_metrics_daily (
    id integer NOT NULL,
    date date NOT NULL,
    total_evaluations integer DEFAULT 0,
    passing_count integer DEFAULT 0,
    failing_count integer DEFAULT 0,
    pass_rate numeric(5,2) DEFAULT 0.0,
    average_score numeric(3,1) DEFAULT 0.0,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.quality_metrics_daily_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.quality_metrics_daily_id_seq OWNED BY public.quality_metrics_daily.id;

--
--

CREATE TABLE IF NOT EXISTS public.reaction (
    id bigint NOT NULL,
    type character varying(255) NOT NULL,
    issue_id bigint NOT NULL,
    comment_id bigint,
    user_id bigint NOT NULL,
    original_author_id bigint DEFAULT 0 NOT NULL,
    original_author character varying(255),
    created_unix bigint
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.reaction_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.reaction_id_seq OWNED BY public.reaction.id;

--
--

CREATE TABLE IF NOT EXISTS public.release (
    id bigint NOT NULL,
    repo_id bigint,
    publisher_id bigint,
    tag_name character varying(255),
    original_author character varying(255),
    original_author_id bigint,
    lower_tag_name character varying(255),
    target character varying(255),
    title character varying(255),
    sha1 character varying(64),
    num_commits bigint,
    note text,
    is_draft boolean DEFAULT false NOT NULL,
    is_prerelease boolean DEFAULT false NOT NULL,
    is_tag boolean DEFAULT false NOT NULL,
    created_unix bigint
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.release_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.release_id_seq OWNED BY public.release.id;

--
--

CREATE TABLE IF NOT EXISTS public.renamed_branch (
    id bigint NOT NULL,
    repo_id bigint NOT NULL,
    "from" character varying(255),
    "to" character varying(255),
    created_unix bigint
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.renamed_branch_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.renamed_branch_id_seq OWNED BY public.renamed_branch.id;

--
--

CREATE TABLE IF NOT EXISTS public.repo_archiver (
    id bigint NOT NULL,
    repo_id bigint,
    type integer,
    status integer,
    commit_id character varying(64),
    created_unix bigint NOT NULL
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.repo_archiver_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.repo_archiver_id_seq OWNED BY public.repo_archiver.id;

--
--

CREATE TABLE IF NOT EXISTS public.repo_indexer_status (
    id bigint NOT NULL,
    repo_id bigint,
    commit_sha character varying(64),
    indexer_type integer DEFAULT 0 NOT NULL
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.repo_indexer_status_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.repo_indexer_status_id_seq OWNED BY public.repo_indexer_status.id;

--
--

CREATE TABLE IF NOT EXISTS public.repo_license (
    id bigint NOT NULL,
    repo_id bigint NOT NULL,
    commit_id character varying(255),
    license character varying(255) NOT NULL,
    created_unix bigint,
    updated_unix bigint
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.repo_license_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.repo_license_id_seq OWNED BY public.repo_license.id;

--
--

CREATE TABLE IF NOT EXISTS public.repo_redirect (
    id bigint NOT NULL,
    owner_id bigint,
    lower_name character varying(255) NOT NULL,
    redirect_repo_id bigint
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.repo_redirect_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.repo_redirect_id_seq OWNED BY public.repo_redirect.id;

--
--

CREATE TABLE IF NOT EXISTS public.repo_topic (
    repo_id bigint NOT NULL,
    topic_id bigint NOT NULL
);

--
--

CREATE TABLE IF NOT EXISTS public.repo_transfer (
    id bigint NOT NULL,
    doer_id bigint,
    recipient_id bigint,
    repo_id bigint,
    team_i_ds text,
    created_unix bigint NOT NULL,
    updated_unix bigint NOT NULL
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.repo_transfer_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.repo_transfer_id_seq OWNED BY public.repo_transfer.id;

--
--

CREATE TABLE IF NOT EXISTS public.repo_unit (
    id bigint NOT NULL,
    repo_id bigint,
    type integer,
    config text,
    created_unix bigint,
    anonymous_access_mode integer DEFAULT 0 NOT NULL,
    everyone_access_mode integer DEFAULT 0 NOT NULL
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.repo_unit_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.repo_unit_id_seq OWNED BY public.repo_unit.id;

--
--

CREATE TABLE IF NOT EXISTS public.repository (
    id bigint NOT NULL,
    owner_id bigint,
    owner_name character varying(255),
    lower_name character varying(255) NOT NULL,
    name character varying(255) NOT NULL,
    description text,
    website character varying(2048),
    original_service_type integer,
    original_url character varying(2048),
    default_branch character varying(255),
    default_wiki_branch character varying(255),
    num_watches integer,
    num_stars integer,
    num_forks integer,
    num_issues integer,
    num_closed_issues integer,
    num_pulls integer,
    num_closed_pulls integer,
    num_milestones integer DEFAULT 0 NOT NULL,
    num_closed_milestones integer DEFAULT 0 NOT NULL,
    num_projects integer DEFAULT 0 NOT NULL,
    num_closed_projects integer DEFAULT 0 NOT NULL,
    num_action_runs integer DEFAULT 0 NOT NULL,
    num_closed_action_runs integer DEFAULT 0 NOT NULL,
    is_private boolean,
    is_empty boolean,
    is_archived boolean,
    is_mirror boolean,
    status integer DEFAULT 0 NOT NULL,
    is_fork boolean DEFAULT false NOT NULL,
    fork_id bigint,
    is_template boolean DEFAULT false NOT NULL,
    template_id bigint,
    size bigint DEFAULT 0 NOT NULL,
    git_size bigint DEFAULT 0 NOT NULL,
    lfs_size bigint DEFAULT 0 NOT NULL,
    is_fsck_enabled boolean DEFAULT true NOT NULL,
    close_issues_via_commit_in_any_branch boolean DEFAULT false NOT NULL,
    topics text,
    object_format_name character varying(6) DEFAULT 'sha1'::character varying NOT NULL,
    trust_model integer,
    avatar character varying(64),
    created_unix bigint,
    updated_unix bigint,
    archived_unix bigint DEFAULT 0
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.repository_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.repository_id_seq OWNED BY public.repository.id;

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

CREATE TABLE IF NOT EXISTS public.review (
    id bigint NOT NULL,
    type integer,
    reviewer_id bigint,
    reviewer_team_id bigint DEFAULT 0 NOT NULL,
    original_author character varying(255),
    original_author_id bigint,
    issue_id bigint,
    content text,
    official boolean DEFAULT false NOT NULL,
    commit_id character varying(64),
    stale boolean DEFAULT false NOT NULL,
    dismissed boolean DEFAULT false NOT NULL,
    created_unix bigint,
    updated_unix bigint
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.review_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.review_id_seq OWNED BY public.review.id;

--
--

CREATE TABLE IF NOT EXISTS public.review_state (
    id bigint NOT NULL,
    user_id bigint NOT NULL,
    pull_id bigint DEFAULT 0 NOT NULL,
    commit_sha character varying(64) NOT NULL,
    updated_files text NOT NULL,
    updated_unix bigint
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.review_state_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.review_state_id_seq OWNED BY public.review_state.id;

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
    created_at timestamp with time zone DEFAULT now()
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

CREATE TABLE IF NOT EXISTS public.secret (
    id bigint NOT NULL,
    owner_id bigint NOT NULL,
    repo_id bigint DEFAULT 0 NOT NULL,
    name character varying(255) NOT NULL,
    data text,
    description text,
    created_unix bigint NOT NULL
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.secret_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.secret_id_seq OWNED BY public.secret.id;

--
--

CREATE TABLE IF NOT EXISTS public.session (
    key character(16) NOT NULL,
    data bytea,
    expiry bigint
);

--
--

CREATE TABLE IF NOT EXISTS public.sites (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    slug character varying(100) NOT NULL,
    name character varying(255) NOT NULL,
    domain character varying(255),
    base_url character varying(500),
    default_category_id uuid,
    config jsonb DEFAULT '{}'::jsonb,
    is_active boolean DEFAULT true,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);

--
--

CREATE TABLE IF NOT EXISTS public.social_posts (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid,
    site_id uuid,
    content_task_id character varying(255),
    channel_id uuid,
    platform character varying(50) NOT NULL,
    post_type character varying(30),
    content_text text,
    media_asset_ids uuid[],
    hashtags text[],
    scheduled_at timestamp with time zone,
    published_at timestamp with time zone,
    status character varying(30) DEFAULT 'draft'::character varying,
    platform_post_id character varying(255),
    engagement jsonb DEFAULT '{}'::jsonb,
    created_at timestamp with time zone DEFAULT now()
);

--
--

CREATE TABLE IF NOT EXISTS public.star (
    id bigint NOT NULL,
    uid bigint,
    repo_id bigint,
    created_unix bigint
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.star_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.star_id_seq OWNED BY public.star.id;

--
--

CREATE TABLE IF NOT EXISTS public.stopwatch (
    id bigint NOT NULL,
    issue_id bigint,
    user_id bigint,
    created_unix bigint
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.stopwatch_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.stopwatch_id_seq OWNED BY public.stopwatch.id;

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

CREATE TABLE IF NOT EXISTS public.system_agents (
    id integer NOT NULL,
    name character varying(100) NOT NULL,
    description text DEFAULT ''::text,
    agent_type character varying(50) DEFAULT 'llm'::character varying NOT NULL,
    trust_level integer DEFAULT 1 NOT NULL,
    is_active boolean DEFAULT true,
    created_at timestamp with time zone DEFAULT now()
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.system_agents_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.system_agents_id_seq OWNED BY public.system_agents.id;

--
--

CREATE TABLE IF NOT EXISTS public.system_credentials (
    id integer NOT NULL,
    service character varying(100) NOT NULL,
    username character varying(100),
    notes text,
    created_at timestamp with time zone DEFAULT now()
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.system_credentials_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.system_credentials_id_seq OWNED BY public.system_credentials.id;

--
--

CREATE TABLE IF NOT EXISTS public.system_devices (
    id integer NOT NULL,
    name character varying(100) NOT NULL,
    tailscale_ip character varying(45) NOT NULL,
    device_type character varying(50),
    os character varying(50),
    notes text,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.system_devices_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.system_devices_id_seq OWNED BY public.system_devices.id;

--
--

CREATE TABLE IF NOT EXISTS public.system_setting (
    id bigint NOT NULL,
    setting_key character varying(255),
    setting_value text,
    version integer,
    created bigint,
    updated bigint
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.system_setting_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.system_setting_id_seq OWNED BY public.system_setting.id;

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

CREATE TABLE IF NOT EXISTS public.task (
    id bigint NOT NULL,
    doer_id bigint,
    owner_id bigint,
    repo_id bigint,
    type integer,
    status integer,
    start_time bigint,
    end_time bigint,
    payload_content text,
    message text,
    created bigint
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

CREATE SEQUENCE IF NOT EXISTS public.task_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.task_id_seq OWNED BY public.task.id;

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

CREATE TABLE IF NOT EXISTS public.tasks (
    id integer NOT NULL,
    task_id character varying(255) NOT NULL,
    task_type character varying(100) NOT NULL,
    status character varying(50) DEFAULT 'pending'::character varying,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    completed_at timestamp with time zone,
    metadata jsonb DEFAULT '{}'::jsonb,
    result jsonb,
    error_message text,
    attempts integer DEFAULT 0,
    max_attempts integer DEFAULT 3
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.tasks_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.tasks_id_seq OWNED BY public.tasks.id;

--
--

CREATE TABLE IF NOT EXISTS public.team (
    id bigint NOT NULL,
    org_id bigint,
    lower_name character varying(255),
    name character varying(255),
    description character varying(255),
    authorize integer,
    num_repos integer,
    num_members integer,
    includes_all_repositories boolean DEFAULT false NOT NULL,
    can_create_org_repo boolean DEFAULT false NOT NULL
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.team_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.team_id_seq OWNED BY public.team.id;

--
--

CREATE TABLE IF NOT EXISTS public.team_invite (
    id bigint NOT NULL,
    token character varying(255) DEFAULT ''::character varying NOT NULL,
    inviter_id bigint DEFAULT 0 NOT NULL,
    org_id bigint DEFAULT 0 NOT NULL,
    team_id bigint DEFAULT 0 NOT NULL,
    email character varying(255) DEFAULT ''::character varying NOT NULL,
    created_unix bigint,
    updated_unix bigint
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.team_invite_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.team_invite_id_seq OWNED BY public.team_invite.id;

--
--

CREATE TABLE IF NOT EXISTS public.team_repo (
    id bigint NOT NULL,
    org_id bigint,
    team_id bigint,
    repo_id bigint
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.team_repo_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.team_repo_id_seq OWNED BY public.team_repo.id;

--
--

CREATE TABLE IF NOT EXISTS public.team_unit (
    id bigint NOT NULL,
    org_id bigint,
    team_id bigint,
    type integer,
    access_mode integer
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.team_unit_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.team_unit_id_seq OWNED BY public.team_unit.id;

--
--

CREATE TABLE IF NOT EXISTS public.team_user (
    id bigint NOT NULL,
    org_id bigint,
    team_id bigint,
    uid bigint
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.team_user_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.team_user_id_seq OWNED BY public.team_user.id;

--
--

CREATE TABLE IF NOT EXISTS public.topic (
    id bigint NOT NULL,
    name character varying(50),
    repo_count integer,
    created_unix bigint,
    updated_unix bigint
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

CREATE SEQUENCE IF NOT EXISTS public.topic_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.topic_id_seq OWNED BY public.topic.id;

--
--

CREATE TABLE IF NOT EXISTS public.tracked_time (
    id bigint NOT NULL,
    issue_id bigint,
    user_id bigint,
    created_unix bigint,
    "time" bigint NOT NULL,
    deleted boolean DEFAULT false NOT NULL
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.tracked_time_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.tracked_time_id_seq OWNED BY public.tracked_time.id;

--
--

CREATE TABLE IF NOT EXISTS public.two_factor (
    id bigint NOT NULL,
    uid bigint,
    secret character varying(255),
    scratch_salt character varying(255),
    scratch_hash character varying(255),
    last_used_passcode character varying(10),
    created_unix bigint,
    updated_unix bigint
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.two_factor_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.two_factor_id_seq OWNED BY public.two_factor.id;

--
--

CREATE TABLE IF NOT EXISTS public.upload (
    id bigint NOT NULL,
    uuid uuid,
    name character varying(255)
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.upload_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.upload_id_seq OWNED BY public.upload.id;

--
--

CREATE TABLE IF NOT EXISTS public."user" (
    id bigint NOT NULL,
    lower_name character varying(255) NOT NULL,
    name character varying(255) NOT NULL,
    full_name character varying(255),
    email character varying(255) NOT NULL,
    keep_email_private boolean,
    email_notifications_preference character varying(20) DEFAULT 'enabled'::character varying NOT NULL,
    passwd character varying(255) NOT NULL,
    passwd_hash_algo character varying(255) DEFAULT 'argon2'::character varying NOT NULL,
    must_change_password boolean DEFAULT false NOT NULL,
    login_type integer,
    login_source bigint DEFAULT 0 NOT NULL,
    login_name character varying(255),
    type integer,
    location character varying(255),
    website character varying(255),
    rands character varying(32),
    salt character varying(32),
    language character varying(5),
    description character varying(255),
    created_unix bigint,
    updated_unix bigint,
    last_login_unix bigint,
    last_repo_visibility boolean,
    max_repo_creation integer DEFAULT '-1'::integer NOT NULL,
    is_active boolean,
    is_admin boolean,
    is_restricted boolean DEFAULT false NOT NULL,
    allow_git_hook boolean,
    allow_import_local boolean,
    allow_create_organization boolean DEFAULT true,
    prohibit_login boolean DEFAULT false NOT NULL,
    avatar character varying(2048) NOT NULL,
    avatar_email character varying(255) NOT NULL,
    use_custom_avatar boolean,
    num_followers integer,
    num_following integer DEFAULT 0 NOT NULL,
    num_stars integer,
    num_repos integer,
    num_teams integer,
    num_members integer,
    visibility integer DEFAULT 0 NOT NULL,
    repo_admin_change_team_access boolean DEFAULT false NOT NULL,
    diff_view_style character varying(255) DEFAULT ''::character varying NOT NULL,
    theme character varying(255) DEFAULT ''::character varying NOT NULL,
    keep_activity_private boolean DEFAULT false NOT NULL
);

--
--

CREATE TABLE IF NOT EXISTS public.user_badge (
    id bigint NOT NULL,
    badge_id bigint,
    user_id bigint
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.user_badge_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.user_badge_id_seq OWNED BY public.user_badge.id;

--
--

CREATE TABLE IF NOT EXISTS public.user_blocking (
    id bigint NOT NULL,
    blocker_id bigint,
    blockee_id bigint,
    note character varying(255),
    created_unix bigint
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.user_blocking_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.user_blocking_id_seq OWNED BY public.user_blocking.id;

--
--

CREATE SEQUENCE IF NOT EXISTS public.user_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.user_id_seq OWNED BY public."user".id;

--
--

CREATE TABLE IF NOT EXISTS public.user_open_id (
    id bigint NOT NULL,
    uid bigint NOT NULL,
    uri character varying(255) NOT NULL,
    show boolean DEFAULT false
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.user_open_id_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.user_open_id_id_seq OWNED BY public.user_open_id.id;

--
--

CREATE TABLE IF NOT EXISTS public.user_redirect (
    id bigint NOT NULL,
    lower_name character varying(255) NOT NULL,
    redirect_user_id bigint
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.user_redirect_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.user_redirect_id_seq OWNED BY public.user_redirect.id;

--
--

CREATE TABLE IF NOT EXISTS public.user_setting (
    id bigint NOT NULL,
    user_id bigint,
    setting_key character varying(255),
    setting_value text
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.user_setting_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.user_setting_id_seq OWNED BY public.user_setting.id;

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

CREATE TABLE IF NOT EXISTS public.version (
    id bigint NOT NULL,
    version bigint
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.version_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.version_id_seq OWNED BY public.version.id;

--
--

CREATE TABLE IF NOT EXISTS public.voice_messages (
    id bigint NOT NULL,
    discord_user_id text,
    role text NOT NULL,
    content text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    embedding public.vector(768),
    discord_channel_id text
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.voice_messages_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.voice_messages_id_seq OWNED BY public.voice_messages.id;

--
--

CREATE TABLE IF NOT EXISTS public.watch (
    id bigint NOT NULL,
    user_id bigint,
    repo_id bigint,
    mode smallint DEFAULT 1 NOT NULL,
    created_unix bigint,
    updated_unix bigint
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.watch_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.watch_id_seq OWNED BY public.watch.id;

--
--

CREATE TABLE IF NOT EXISTS public.webauthn_credential (
    id bigint NOT NULL,
    name character varying(255),
    lower_name character varying(255),
    user_id bigint,
    credential_id bytea,
    public_key bytea,
    attestation_type character varying(255),
    aaguid bytea,
    sign_count bigint,
    clone_warning boolean,
    created_unix bigint,
    updated_unix bigint
);

--
--

CREATE SEQUENCE IF NOT EXISTS public.webauthn_credential_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.webauthn_credential_id_seq OWNED BY public.webauthn_credential.id;

--
--

CREATE TABLE IF NOT EXISTS public.webhook (
    id bigint NOT NULL,
    repo_id bigint,
    owner_id bigint,
    is_system_webhook boolean,
    url text,
    http_method character varying(255),
    content_type integer,
    secret text,
    events text,
    is_active boolean,
    type character varying(16),
    meta text,
    last_status integer,
    header_authorization_encrypted text,
    created_unix bigint,
    updated_unix bigint
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
    CONSTRAINT webhook_endpoints_direction_config_chk CHECK ((((direction = 'inbound'::text) AND (url IS NULL)) OR ((direction = 'outbound'::text) AND (url IS NOT NULL)))),
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

CREATE SEQUENCE IF NOT EXISTS public.webhook_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
--

ALTER SEQUENCE public.webhook_id_seq OWNED BY public.webhook.id;

--
--

CREATE TABLE IF NOT EXISTS public.workflow_executions (
    id uuid NOT NULL,
    workflow_id uuid NOT NULL,
    owner_id character varying(255) NOT NULL,
    execution_status character varying(50) DEFAULT 'pending'::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    started_at timestamp with time zone,
    completed_at timestamp with time zone,
    duration_ms integer,
    initial_input jsonb,
    phase_results jsonb DEFAULT '{}'::jsonb,
    final_output jsonb,
    error_message text,
    progress_percent integer DEFAULT 0,
    completed_phases integer DEFAULT 0,
    total_phases integer DEFAULT 0,
    tags jsonb DEFAULT '[]'::jsonb,
    metadata jsonb DEFAULT '{}'::jsonb,
    selected_model character varying(255),
    execution_mode character varying(50) DEFAULT 'agent'::character varying
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

ALTER TABLE ONLY public.access ALTER COLUMN id SET DEFAULT nextval('public.access_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.access_token ALTER COLUMN id SET DEFAULT nextval('public.access_token_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.action ALTER COLUMN id SET DEFAULT nextval('public.action_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.action_artifact ALTER COLUMN id SET DEFAULT nextval('public.action_artifact_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.action_run ALTER COLUMN id SET DEFAULT nextval('public.action_run_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.action_run_job ALTER COLUMN id SET DEFAULT nextval('public.action_run_job_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.action_runner ALTER COLUMN id SET DEFAULT nextval('public.action_runner_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.action_runner_token ALTER COLUMN id SET DEFAULT nextval('public.action_runner_token_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.action_schedule ALTER COLUMN id SET DEFAULT nextval('public.action_schedule_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.action_schedule_spec ALTER COLUMN id SET DEFAULT nextval('public.action_schedule_spec_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.action_task ALTER COLUMN id SET DEFAULT nextval('public.action_task_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.action_task_output ALTER COLUMN id SET DEFAULT nextval('public.action_task_output_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.action_task_step ALTER COLUMN id SET DEFAULT nextval('public.action_task_step_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.action_tasks_version ALTER COLUMN id SET DEFAULT nextval('public.action_tasks_version_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.action_variable ALTER COLUMN id SET DEFAULT nextval('public.action_variable_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.affiliate_links ALTER COLUMN id SET DEFAULT nextval('public.affiliate_links_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.agent_permissions ALTER COLUMN id SET DEFAULT nextval('public.agent_permissions_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.agent_status ALTER COLUMN id SET DEFAULT nextval('public.agent_status_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.alert_actions ALTER COLUMN id SET DEFAULT nextval('public.alert_actions_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.alert_events ALTER COLUMN id SET DEFAULT nextval('public.alert_events_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.alert_log ALTER COLUMN id SET DEFAULT nextval('public.alert_log_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.alert_rules ALTER COLUMN id SET DEFAULT nextval('public.alert_rules_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.app_settings ALTER COLUMN id SET DEFAULT nextval('public.app_settings_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.approval_queue ALTER COLUMN id SET DEFAULT nextval('public.approval_queue_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.attachment ALTER COLUMN id SET DEFAULT nextval('public.attachment_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.audit_log ALTER COLUMN id SET DEFAULT nextval('public.audit_log_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.audit_log_summaries ALTER COLUMN id SET DEFAULT nextval('public.audit_log_summaries_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.badge ALTER COLUMN id SET DEFAULT nextval('public.badge_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.brain_decision_summaries ALTER COLUMN id SET DEFAULT nextval('public.brain_decision_summaries_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.branch ALTER COLUMN id SET DEFAULT nextval('public.branch_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.campaign_email_logs ALTER COLUMN id SET DEFAULT nextval('public.campaign_email_logs_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.capability_outcomes ALTER COLUMN id SET DEFAULT nextval('public.capability_outcomes_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.collaboration ALTER COLUMN id SET DEFAULT nextval('public.collaboration_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.comment ALTER COLUMN id SET DEFAULT nextval('public.comment_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.commit_status ALTER COLUMN id SET DEFAULT nextval('public.commit_status_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.commit_status_index ALTER COLUMN id SET DEFAULT nextval('public.commit_status_index_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.commit_status_summary ALTER COLUMN id SET DEFAULT nextval('public.commit_status_summary_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.dbfs_data ALTER COLUMN id SET DEFAULT nextval('public.dbfs_data_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.dbfs_meta ALTER COLUMN id SET DEFAULT nextval('public.dbfs_meta_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.deploy_key ALTER COLUMN id SET DEFAULT nextval('public.deploy_key_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.electricity_costs ALTER COLUMN id SET DEFAULT nextval('public.electricity_costs_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.email_address ALTER COLUMN id SET DEFAULT nextval('public.email_address_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.embeddings ALTER COLUMN id SET DEFAULT nextval('public.embeddings_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.experiment_assignments ALTER COLUMN id SET DEFAULT nextval('public.experiment_assignments_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.fact_overrides ALTER COLUMN id SET DEFAULT nextval('public.fact_overrides_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.financial_entries ALTER COLUMN id SET DEFAULT nextval('public.financial_entries_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.fine_tuning_jobs ALTER COLUMN id SET DEFAULT nextval('public.fine_tuning_jobs_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.follow ALTER COLUMN id SET DEFAULT nextval('public.follow_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.gpg_key ALTER COLUMN id SET DEFAULT nextval('public.gpg_key_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.gpu_metrics ALTER COLUMN id SET DEFAULT nextval('public.gpu_metrics_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.hook_task ALTER COLUMN id SET DEFAULT nextval('public.hook_task_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.issue ALTER COLUMN id SET DEFAULT nextval('public.issue_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.issue_assignees ALTER COLUMN id SET DEFAULT nextval('public.issue_assignees_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.issue_content_history ALTER COLUMN id SET DEFAULT nextval('public.issue_content_history_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.issue_dependency ALTER COLUMN id SET DEFAULT nextval('public.issue_dependency_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.issue_label ALTER COLUMN id SET DEFAULT nextval('public.issue_label_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.issue_pin ALTER COLUMN id SET DEFAULT nextval('public.issue_pin_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.issue_user ALTER COLUMN id SET DEFAULT nextval('public.issue_user_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.issue_watch ALTER COLUMN id SET DEFAULT nextval('public.issue_watch_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.label ALTER COLUMN id SET DEFAULT nextval('public.label_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.language_stat ALTER COLUMN id SET DEFAULT nextval('public.language_stat_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.learning_patterns ALTER COLUMN id SET DEFAULT nextval('public.learning_patterns_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.lfs_lock ALTER COLUMN id SET DEFAULT nextval('public.lfs_lock_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.lfs_meta_object ALTER COLUMN id SET DEFAULT nextval('public.lfs_meta_object_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.login_source ALTER COLUMN id SET DEFAULT nextval('public.login_source_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.milestone ALTER COLUMN id SET DEFAULT nextval('public.milestone_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.mirror ALTER COLUMN id SET DEFAULT nextval('public.mirror_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.newsletter_subscribers ALTER COLUMN id SET DEFAULT nextval('public.newsletter_subscribers_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.notice ALTER COLUMN id SET DEFAULT nextval('public.notice_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.notification ALTER COLUMN id SET DEFAULT nextval('public.notification_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.oauth2_application ALTER COLUMN id SET DEFAULT nextval('public.oauth2_application_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.oauth2_authorization_code ALTER COLUMN id SET DEFAULT nextval('public.oauth2_authorization_code_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.oauth2_grant ALTER COLUMN id SET DEFAULT nextval('public.oauth2_grant_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.operator_notes ALTER COLUMN id SET DEFAULT nextval('public.operator_notes_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.org_user ALTER COLUMN id SET DEFAULT nextval('public.org_user_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.package ALTER COLUMN id SET DEFAULT nextval('public.package_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.package_blob ALTER COLUMN id SET DEFAULT nextval('public.package_blob_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.package_cleanup_rule ALTER COLUMN id SET DEFAULT nextval('public.package_cleanup_rule_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.package_file ALTER COLUMN id SET DEFAULT nextval('public.package_file_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.package_property ALTER COLUMN id SET DEFAULT nextval('public.package_property_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.package_version ALTER COLUMN id SET DEFAULT nextval('public.package_version_id_seq'::regclass);

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

ALTER TABLE ONLY public.pipeline_experiments ALTER COLUMN id SET DEFAULT nextval('public.pipeline_experiments_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.pipeline_gate_history ALTER COLUMN id SET DEFAULT nextval('public.pipeline_gate_history_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.pipeline_reviews ALTER COLUMN id SET DEFAULT nextval('public.pipeline_reviews_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.pipeline_run_log ALTER COLUMN id SET DEFAULT nextval('public.pipeline_run_log_id_seq'::regclass);

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

ALTER TABLE ONLY public.project ALTER COLUMN id SET DEFAULT nextval('public.project_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.project_board ALTER COLUMN id SET DEFAULT nextval('public.project_board_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.project_issue ALTER COLUMN id SET DEFAULT nextval('public.project_issue_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.protected_branch ALTER COLUMN id SET DEFAULT nextval('public.protected_branch_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.protected_tag ALTER COLUMN id SET DEFAULT nextval('public.protected_tag_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.public_key ALTER COLUMN id SET DEFAULT nextval('public.public_key_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.published_post_edit_metrics ALTER COLUMN id SET DEFAULT nextval('public.published_post_edit_metrics_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.pull_auto_merge ALTER COLUMN id SET DEFAULT nextval('public.pull_auto_merge_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.pull_request ALTER COLUMN id SET DEFAULT nextval('public.pull_request_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.push_mirror ALTER COLUMN id SET DEFAULT nextval('public.push_mirror_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.quality_improvement_logs ALTER COLUMN id SET DEFAULT nextval('public.quality_improvement_logs_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.quality_metrics_daily ALTER COLUMN id SET DEFAULT nextval('public.quality_metrics_daily_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.reaction ALTER COLUMN id SET DEFAULT nextval('public.reaction_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.release ALTER COLUMN id SET DEFAULT nextval('public.release_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.renamed_branch ALTER COLUMN id SET DEFAULT nextval('public.renamed_branch_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.repo_archiver ALTER COLUMN id SET DEFAULT nextval('public.repo_archiver_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.repo_indexer_status ALTER COLUMN id SET DEFAULT nextval('public.repo_indexer_status_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.repo_license ALTER COLUMN id SET DEFAULT nextval('public.repo_license_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.repo_redirect ALTER COLUMN id SET DEFAULT nextval('public.repo_redirect_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.repo_transfer ALTER COLUMN id SET DEFAULT nextval('public.repo_transfer_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.repo_unit ALTER COLUMN id SET DEFAULT nextval('public.repo_unit_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.repository ALTER COLUMN id SET DEFAULT nextval('public.repository_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.review ALTER COLUMN id SET DEFAULT nextval('public.review_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.review_state ALTER COLUMN id SET DEFAULT nextval('public.review_state_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.schema_migrations ALTER COLUMN id SET DEFAULT nextval('public.schema_migrations_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.secret ALTER COLUMN id SET DEFAULT nextval('public.secret_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.star ALTER COLUMN id SET DEFAULT nextval('public.star_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.stopwatch ALTER COLUMN id SET DEFAULT nextval('public.stopwatch_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.sync_metrics ALTER COLUMN id SET DEFAULT nextval('public.sync_metrics_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.system_agents ALTER COLUMN id SET DEFAULT nextval('public.system_agents_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.system_credentials ALTER COLUMN id SET DEFAULT nextval('public.system_credentials_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.system_devices ALTER COLUMN id SET DEFAULT nextval('public.system_devices_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.system_setting ALTER COLUMN id SET DEFAULT nextval('public.system_setting_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.task ALTER COLUMN id SET DEFAULT nextval('public.task_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.tasks ALTER COLUMN id SET DEFAULT nextval('public.tasks_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.team ALTER COLUMN id SET DEFAULT nextval('public.team_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.team_invite ALTER COLUMN id SET DEFAULT nextval('public.team_invite_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.team_repo ALTER COLUMN id SET DEFAULT nextval('public.team_repo_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.team_unit ALTER COLUMN id SET DEFAULT nextval('public.team_unit_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.team_user ALTER COLUMN id SET DEFAULT nextval('public.team_user_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.topic ALTER COLUMN id SET DEFAULT nextval('public.topic_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.tracked_time ALTER COLUMN id SET DEFAULT nextval('public.tracked_time_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.two_factor ALTER COLUMN id SET DEFAULT nextval('public.two_factor_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.upload ALTER COLUMN id SET DEFAULT nextval('public.upload_id_seq'::regclass);

--
--

ALTER TABLE ONLY public."user" ALTER COLUMN id SET DEFAULT nextval('public.user_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.user_badge ALTER COLUMN id SET DEFAULT nextval('public.user_badge_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.user_blocking ALTER COLUMN id SET DEFAULT nextval('public.user_blocking_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.user_open_id ALTER COLUMN id SET DEFAULT nextval('public.user_open_id_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.user_redirect ALTER COLUMN id SET DEFAULT nextval('public.user_redirect_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.user_setting ALTER COLUMN id SET DEFAULT nextval('public.user_setting_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.version ALTER COLUMN id SET DEFAULT nextval('public.version_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.voice_messages ALTER COLUMN id SET DEFAULT nextval('public.voice_messages_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.watch ALTER COLUMN id SET DEFAULT nextval('public.watch_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.webauthn_credential ALTER COLUMN id SET DEFAULT nextval('public.webauthn_credential_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.webhook ALTER COLUMN id SET DEFAULT nextval('public.webhook_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.writing_samples ALTER COLUMN id SET DEFAULT nextval('public.writing_samples_id_seq'::regclass);

--
--

ALTER TABLE ONLY public.access
    ADD CONSTRAINT access_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.access_token
    ADD CONSTRAINT access_token_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.action_artifact
    ADD CONSTRAINT action_artifact_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.action
    ADD CONSTRAINT action_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.action_run_index
    ADD CONSTRAINT action_run_index_pkey PRIMARY KEY (group_id);

--
--

ALTER TABLE ONLY public.action_run_job
    ADD CONSTRAINT action_run_job_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.action_run
    ADD CONSTRAINT action_run_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.action_runner
    ADD CONSTRAINT action_runner_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.action_runner_token
    ADD CONSTRAINT action_runner_token_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.action_schedule
    ADD CONSTRAINT action_schedule_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.action_schedule_spec
    ADD CONSTRAINT action_schedule_spec_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.action_task_output
    ADD CONSTRAINT action_task_output_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.action_task
    ADD CONSTRAINT action_task_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.action_task_step
    ADD CONSTRAINT action_task_step_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.action_tasks_version
    ADD CONSTRAINT action_tasks_version_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.action_variable
    ADD CONSTRAINT action_variable_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.affiliate_links
    ADD CONSTRAINT affiliate_links_keyword_key UNIQUE (keyword);

--
--

ALTER TABLE ONLY public.affiliate_links
    ADD CONSTRAINT affiliate_links_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.agent_permissions
    ADD CONSTRAINT agent_permissions_agent_name_resource_action_key UNIQUE (agent_name, resource, action);

--
--

ALTER TABLE ONLY public.agent_permissions
    ADD CONSTRAINT agent_permissions_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.agent_status
    ADD CONSTRAINT agent_status_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.alert_actions
    ADD CONSTRAINT alert_actions_pkey PRIMARY KEY (id);

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

ALTER TABLE ONLY public.alert_rules
    ADD CONSTRAINT alert_rules_name_key UNIQUE (name);

--
--

ALTER TABLE ONLY public.alert_rules
    ADD CONSTRAINT alert_rules_pkey PRIMARY KEY (id);

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

ALTER TABLE ONLY public.approval_queue
    ADD CONSTRAINT approval_queue_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.attachment
    ADD CONSTRAINT attachment_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.audit_log
    ADD CONSTRAINT audit_log_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.audit_log_summaries
    ADD CONSTRAINT audit_log_summaries_pkey PRIMARY KEY (id);

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

ALTER TABLE ONLY public.badge
    ADD CONSTRAINT badge_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.brain_decision_summaries
    ADD CONSTRAINT brain_decision_summaries_pkey PRIMARY KEY (id);

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

ALTER TABLE ONLY public.branch
    ADD CONSTRAINT branch_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.campaign_email_logs
    ADD CONSTRAINT campaign_email_logs_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.capability_executions
    ADD CONSTRAINT capability_executions_pkey PRIMARY KEY (id);

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

ALTER TABLE ONLY public.capability_tasks
    ADD CONSTRAINT capability_tasks_pkey PRIMARY KEY (id);

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

ALTER TABLE ONLY public.checkpoint_migrations
    ADD CONSTRAINT checkpoint_migrations_pkey PRIMARY KEY (v);

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

ALTER TABLE ONLY public.collaboration
    ADD CONSTRAINT collaboration_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.comment
    ADD CONSTRAINT comment_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.commit_status_index
    ADD CONSTRAINT commit_status_index_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.commit_status
    ADD CONSTRAINT commit_status_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.commit_status_summary
    ADD CONSTRAINT commit_status_summary_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.content_calendar
    ADD CONSTRAINT content_calendar_pkey PRIMARY KEY (id);

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

ALTER TABLE ONLY public.custom_workflows
    ADD CONSTRAINT custom_workflows_name_owner_unique UNIQUE (name, owner_id);

--
--

ALTER TABLE ONLY public.custom_workflows
    ADD CONSTRAINT custom_workflows_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.dbfs_data
    ADD CONSTRAINT dbfs_data_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.dbfs_meta
    ADD CONSTRAINT dbfs_meta_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.decision_log
    ADD CONSTRAINT decision_log_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.deploy_key
    ADD CONSTRAINT deploy_key_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.discovery_runs
    ADD CONSTRAINT discovery_runs_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.distribution_channels
    ADD CONSTRAINT distribution_channels_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.electricity_costs
    ADD CONSTRAINT electricity_costs_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.email_address
    ADD CONSTRAINT email_address_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.email_hash
    ADD CONSTRAINT email_hash_pkey PRIMARY KEY (hash);

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

ALTER TABLE ONLY public.experiment_assignments
    ADD CONSTRAINT experiment_assignments_experiment_id_subject_id_key UNIQUE (experiment_id, subject_id);

--
--

ALTER TABLE ONLY public.experiment_assignments
    ADD CONSTRAINT experiment_assignments_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.experiments
    ADD CONSTRAINT experiments_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.external_login_user
    ADD CONSTRAINT external_login_user_pkey PRIMARY KEY (external_id, login_source_id);

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

ALTER TABLE ONLY public.fine_tuning_jobs
    ADD CONSTRAINT fine_tuning_jobs_job_id_key UNIQUE (job_id);

--
--

ALTER TABLE ONLY public.fine_tuning_jobs
    ADD CONSTRAINT fine_tuning_jobs_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.follow
    ADD CONSTRAINT follow_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.gpg_key_import
    ADD CONSTRAINT gpg_key_import_pkey PRIMARY KEY (key_id);

--
--

ALTER TABLE ONLY public.gpg_key
    ADD CONSTRAINT gpg_key_pkey PRIMARY KEY (id);

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

ALTER TABLE ONLY public.hook_task
    ADD CONSTRAINT hook_task_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.internal_topic_candidates
    ADD CONSTRAINT internal_topic_candidates_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.issue_assignees
    ADD CONSTRAINT issue_assignees_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.issue_content_history
    ADD CONSTRAINT issue_content_history_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.issue_dependency
    ADD CONSTRAINT issue_dependency_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.issue_index
    ADD CONSTRAINT issue_index_pkey PRIMARY KEY (group_id);

--
--

ALTER TABLE ONLY public.issue_label
    ADD CONSTRAINT issue_label_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.issue_pin
    ADD CONSTRAINT issue_pin_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.issue
    ADD CONSTRAINT issue_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.issue_user
    ADD CONSTRAINT issue_user_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.issue_watch
    ADD CONSTRAINT issue_watch_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.jwt_blocklist
    ADD CONSTRAINT jwt_blocklist_pkey PRIMARY KEY (jti);

--
--

ALTER TABLE ONLY public.label
    ADD CONSTRAINT label_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.language_stat
    ADD CONSTRAINT language_stat_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.learning_patterns
    ADD CONSTRAINT learning_patterns_pattern_id_key UNIQUE (pattern_id);

--
--

ALTER TABLE ONLY public.learning_patterns
    ADD CONSTRAINT learning_patterns_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.lfs_lock
    ADD CONSTRAINT lfs_lock_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.lfs_meta_object
    ADD CONSTRAINT lfs_meta_object_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.login_source
    ADD CONSTRAINT login_source_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.logs
    ADD CONSTRAINT logs_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.media_assets
    ADD CONSTRAINT media_assets_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.milestone
    ADD CONSTRAINT milestone_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.mirror
    ADD CONSTRAINT mirror_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.model_performance
    ADD CONSTRAINT model_performance_pkey PRIMARY KEY (id);

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

ALTER TABLE ONLY public.notice
    ADD CONSTRAINT notice_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.notification
    ADD CONSTRAINT notification_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.oauth2_application
    ADD CONSTRAINT oauth2_application_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.oauth2_authorization_code
    ADD CONSTRAINT oauth2_authorization_code_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.oauth2_grant
    ADD CONSTRAINT oauth2_grant_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.oauth_accounts
    ADD CONSTRAINT oauth_accounts_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.oauth_accounts
    ADD CONSTRAINT oauth_accounts_provider_provider_user_id_key UNIQUE (provider, provider_user_id);

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

ALTER TABLE ONLY public.org_user
    ADD CONSTRAINT org_user_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.package_blob
    ADD CONSTRAINT package_blob_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.package_blob_upload
    ADD CONSTRAINT package_blob_upload_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.package_cleanup_rule
    ADD CONSTRAINT package_cleanup_rule_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.package_file
    ADD CONSTRAINT package_file_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.package
    ADD CONSTRAINT package_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.package_property
    ADD CONSTRAINT package_property_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.package_version
    ADD CONSTRAINT package_version_pkey PRIMARY KEY (id);

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

ALTER TABLE ONLY public.pipeline_experiments
    ADD CONSTRAINT pipeline_experiments_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.pipeline_gate_history
    ADD CONSTRAINT pipeline_gate_history_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.pipeline_reviews
    ADD CONSTRAINT pipeline_reviews_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.pipeline_run_log
    ADD CONSTRAINT pipeline_run_log_pkey PRIMARY KEY (id);

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

ALTER TABLE ONLY public.post_approval_gates
    ADD CONSTRAINT post_approval_gates_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.post_approval_gates
    ADD CONSTRAINT post_approval_gates_post_id_gate_name_ordinal_key UNIQUE (post_id, gate_name, ordinal);

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

ALTER TABLE ONLY public.project_board
    ADD CONSTRAINT project_board_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.project_issue
    ADD CONSTRAINT project_issue_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.project
    ADD CONSTRAINT project_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.protected_branch
    ADD CONSTRAINT protected_branch_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.protected_tag
    ADD CONSTRAINT protected_tag_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.public_key
    ADD CONSTRAINT public_key_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.published_post_edit_metrics
    ADD CONSTRAINT published_post_edit_metrics_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.pull_auto_merge
    ADD CONSTRAINT pull_auto_merge_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.pull_request
    ADD CONSTRAINT pull_request_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.push_mirror
    ADD CONSTRAINT push_mirror_pkey PRIMARY KEY (id);

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

ALTER TABLE ONLY public.quality_improvement_logs
    ADD CONSTRAINT quality_improvement_logs_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.quality_metrics_daily
    ADD CONSTRAINT quality_metrics_daily_date_key UNIQUE (date);

--
--

ALTER TABLE ONLY public.quality_metrics_daily
    ADD CONSTRAINT quality_metrics_daily_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.reaction
    ADD CONSTRAINT reaction_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.release
    ADD CONSTRAINT release_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.renamed_branch
    ADD CONSTRAINT renamed_branch_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.repo_archiver
    ADD CONSTRAINT repo_archiver_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.repo_indexer_status
    ADD CONSTRAINT repo_indexer_status_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.repo_license
    ADD CONSTRAINT repo_license_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.repo_redirect
    ADD CONSTRAINT repo_redirect_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.repo_topic
    ADD CONSTRAINT repo_topic_pkey PRIMARY KEY (repo_id, topic_id);

--
--

ALTER TABLE ONLY public.repo_transfer
    ADD CONSTRAINT repo_transfer_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.repo_unit
    ADD CONSTRAINT repo_unit_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.repository
    ADD CONSTRAINT repository_pkey PRIMARY KEY (id);

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

ALTER TABLE ONLY public.review
    ADD CONSTRAINT review_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.review_state
    ADD CONSTRAINT review_state_pkey PRIMARY KEY (id);

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

ALTER TABLE ONLY public.secret
    ADD CONSTRAINT secret_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.session
    ADD CONSTRAINT session_pkey PRIMARY KEY (key);

--
--

ALTER TABLE ONLY public.sites
    ADD CONSTRAINT sites_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.sites
    ADD CONSTRAINT sites_slug_key UNIQUE (slug);

--
--

ALTER TABLE ONLY public.social_posts
    ADD CONSTRAINT social_posts_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.star
    ADD CONSTRAINT star_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.stopwatch
    ADD CONSTRAINT stopwatch_pkey PRIMARY KEY (id);

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

ALTER TABLE ONLY public.system_agents
    ADD CONSTRAINT system_agents_name_key UNIQUE (name);

--
--

ALTER TABLE ONLY public.system_agents
    ADD CONSTRAINT system_agents_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.system_credentials
    ADD CONSTRAINT system_credentials_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.system_credentials
    ADD CONSTRAINT system_credentials_service_key UNIQUE (service);

--
--

ALTER TABLE ONLY public.system_devices
    ADD CONSTRAINT system_devices_name_key UNIQUE (name);

--
--

ALTER TABLE ONLY public.system_devices
    ADD CONSTRAINT system_devices_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.system_setting
    ADD CONSTRAINT system_setting_pkey PRIMARY KEY (id);

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

ALTER TABLE ONLY public.task
    ADD CONSTRAINT task_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.task_status_history
    ADD CONSTRAINT task_status_history_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.tasks
    ADD CONSTRAINT tasks_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.tasks
    ADD CONSTRAINT tasks_task_id_key UNIQUE (task_id);

--
--

ALTER TABLE ONLY public.team_invite
    ADD CONSTRAINT team_invite_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.team
    ADD CONSTRAINT team_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.team_repo
    ADD CONSTRAINT team_repo_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.team_unit
    ADD CONSTRAINT team_unit_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.team_user
    ADD CONSTRAINT team_user_pkey PRIMARY KEY (id);

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

ALTER TABLE ONLY public.topic
    ADD CONSTRAINT topic_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.tracked_time
    ADD CONSTRAINT tracked_time_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.two_factor
    ADD CONSTRAINT two_factor_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.upload
    ADD CONSTRAINT upload_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.user_badge
    ADD CONSTRAINT user_badge_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.user_blocking
    ADD CONSTRAINT user_blocking_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.user_open_id
    ADD CONSTRAINT user_open_id_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public."user"
    ADD CONSTRAINT user_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.user_redirect
    ADD CONSTRAINT user_redirect_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.user_setting
    ADD CONSTRAINT user_setting_pkey PRIMARY KEY (id);

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

ALTER TABLE ONLY public.version
    ADD CONSTRAINT version_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.voice_messages
    ADD CONSTRAINT voice_messages_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.watch
    ADD CONSTRAINT watch_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.webauthn_credential
    ADD CONSTRAINT webauthn_credential_pkey PRIMARY KEY (id);

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

ALTER TABLE ONLY public.webhook
    ADD CONSTRAINT webhook_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.workflow_executions
    ADD CONSTRAINT workflow_executions_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.writing_samples
    ADD CONSTRAINT writing_samples_pkey PRIMARY KEY (id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_access_token_created_unix" ON public.access_token USING btree (created_unix);

--
--

CREATE INDEX IF NOT EXISTS "IDX_access_token_token_last_eight" ON public.access_token USING btree (token_last_eight);

--
--

CREATE INDEX IF NOT EXISTS "IDX_access_token_uid" ON public.access_token USING btree (uid);

--
--

CREATE INDEX IF NOT EXISTS "IDX_access_token_updated_unix" ON public.access_token USING btree (updated_unix);

--
--

CREATE INDEX IF NOT EXISTS "IDX_action_artifact_artifact_name" ON public.action_artifact USING btree (artifact_name);

--
--

CREATE INDEX IF NOT EXISTS "IDX_action_artifact_artifact_path" ON public.action_artifact USING btree (artifact_path);

--
--

CREATE INDEX IF NOT EXISTS "IDX_action_artifact_expired_unix" ON public.action_artifact USING btree (expired_unix);

--
--

CREATE INDEX IF NOT EXISTS "IDX_action_artifact_repo_id" ON public.action_artifact USING btree (repo_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_action_artifact_run_id" ON public.action_artifact USING btree (run_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_action_artifact_status" ON public.action_artifact USING btree (status);

--
--

CREATE INDEX IF NOT EXISTS "IDX_action_artifact_updated_unix" ON public.action_artifact USING btree (updated_unix);

--
--

CREATE INDEX IF NOT EXISTS "IDX_action_au_c_u" ON public.action USING btree (act_user_id, created_unix, user_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_action_au_r_c_u_d" ON public.action USING btree (act_user_id, repo_id, created_unix, user_id, is_deleted);

--
--

CREATE INDEX IF NOT EXISTS "IDX_action_c_u" ON public.action USING btree (user_id, is_deleted);

--
--

CREATE INDEX IF NOT EXISTS "IDX_action_c_u_d" ON public.action USING btree (created_unix, user_id, is_deleted);

--
--

CREATE INDEX IF NOT EXISTS "IDX_action_comment_id" ON public.action USING btree (comment_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_action_r_u_d" ON public.action USING btree (repo_id, user_id, is_deleted);

--
--

CREATE INDEX IF NOT EXISTS "IDX_action_run_approved_by" ON public.action_run USING btree (approved_by);

--
--

CREATE INDEX IF NOT EXISTS "IDX_action_run_index" ON public.action_run USING btree (index);

--
--

CREATE INDEX IF NOT EXISTS "IDX_action_run_index_max_index" ON public.action_run_index USING btree (max_index);

--
--

CREATE INDEX IF NOT EXISTS "IDX_action_run_job_commit_sha" ON public.action_run_job USING btree (commit_sha);

--
--

CREATE INDEX IF NOT EXISTS "IDX_action_run_job_owner_id" ON public.action_run_job USING btree (owner_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_action_run_job_repo_id" ON public.action_run_job USING btree (repo_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_action_run_job_run_id" ON public.action_run_job USING btree (run_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_action_run_job_status" ON public.action_run_job USING btree (status);

--
--

CREATE INDEX IF NOT EXISTS "IDX_action_run_job_updated" ON public.action_run_job USING btree (updated);

--
--

CREATE INDEX IF NOT EXISTS "IDX_action_run_owner_id" ON public.action_run USING btree (owner_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_action_run_ref" ON public.action_run USING btree (ref);

--
--

CREATE INDEX IF NOT EXISTS "IDX_action_run_repo_id" ON public.action_run USING btree (repo_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_action_run_status" ON public.action_run USING btree (status);

--
--

CREATE INDEX IF NOT EXISTS "IDX_action_run_trigger_user_id" ON public.action_run USING btree (trigger_user_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_action_run_workflow_id" ON public.action_run USING btree (workflow_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_action_runner_last_active" ON public.action_runner USING btree (last_active);

--
--

CREATE INDEX IF NOT EXISTS "IDX_action_runner_last_online" ON public.action_runner USING btree (last_online);

--
--

CREATE INDEX IF NOT EXISTS "IDX_action_runner_owner_id" ON public.action_runner USING btree (owner_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_action_runner_repo_id" ON public.action_runner USING btree (repo_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_action_runner_token_owner_id" ON public.action_runner_token USING btree (owner_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_action_runner_token_repo_id" ON public.action_runner_token USING btree (repo_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_action_schedule_owner_id" ON public.action_schedule USING btree (owner_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_action_schedule_repo_id" ON public.action_schedule USING btree (repo_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_action_schedule_spec_next" ON public.action_schedule_spec USING btree (next);

--
--

CREATE INDEX IF NOT EXISTS "IDX_action_schedule_spec_repo_id" ON public.action_schedule_spec USING btree (repo_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_action_schedule_spec_schedule_id" ON public.action_schedule_spec USING btree (schedule_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_action_task_commit_sha" ON public.action_task USING btree (commit_sha);

--
--

CREATE INDEX IF NOT EXISTS "IDX_action_task_output_task_id" ON public.action_task_output USING btree (task_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_action_task_owner_id" ON public.action_task USING btree (owner_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_action_task_repo_id" ON public.action_task USING btree (repo_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_action_task_runner_id" ON public.action_task USING btree (runner_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_action_task_started" ON public.action_task USING btree (started);

--
--

CREATE INDEX IF NOT EXISTS "IDX_action_task_status" ON public.action_task USING btree (status);

--
--

CREATE INDEX IF NOT EXISTS "IDX_action_task_step_index" ON public.action_task_step USING btree (index);

--
--

CREATE INDEX IF NOT EXISTS "IDX_action_task_step_repo_id" ON public.action_task_step USING btree (repo_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_action_task_step_status" ON public.action_task_step USING btree (status);

--
--

CREATE INDEX IF NOT EXISTS "IDX_action_task_step_task_id" ON public.action_task_step USING btree (task_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_action_task_stopped_log_expired" ON public.action_task USING btree (stopped, log_expired);

--
--

CREATE INDEX IF NOT EXISTS "IDX_action_task_token_last_eight" ON public.action_task USING btree (token_last_eight);

--
--

CREATE INDEX IF NOT EXISTS "IDX_action_task_updated" ON public.action_task USING btree (updated);

--
--

CREATE INDEX IF NOT EXISTS "IDX_action_tasks_version_repo_id" ON public.action_tasks_version USING btree (repo_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_action_user_id" ON public.action USING btree (user_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_action_variable_repo_id" ON public.action_variable USING btree (repo_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_attachment_comment_id" ON public.attachment USING btree (comment_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_attachment_issue_id" ON public.attachment USING btree (issue_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_attachment_release_id" ON public.attachment USING btree (release_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_attachment_repo_id" ON public.attachment USING btree (repo_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_attachment_uploader_id" ON public.attachment USING btree (uploader_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_auth_token_expires_unix" ON public.auth_token USING btree (expires_unix);

--
--

CREATE INDEX IF NOT EXISTS "IDX_auth_token_user_id" ON public.auth_token USING btree (user_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_branch_deleted_unix" ON public.branch USING btree (deleted_unix);

--
--

CREATE INDEX IF NOT EXISTS "IDX_branch_is_deleted" ON public.branch USING btree (is_deleted);

--
--

CREATE INDEX IF NOT EXISTS "IDX_collaboration_created_unix" ON public.collaboration USING btree (created_unix);

--
--

CREATE INDEX IF NOT EXISTS "IDX_collaboration_repo_id" ON public.collaboration USING btree (repo_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_collaboration_updated_unix" ON public.collaboration USING btree (updated_unix);

--
--

CREATE INDEX IF NOT EXISTS "IDX_collaboration_user_id" ON public.collaboration USING btree (user_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_comment_created_unix" ON public.comment USING btree (created_unix);

--
--

CREATE INDEX IF NOT EXISTS "IDX_comment_dependent_issue_id" ON public.comment USING btree (dependent_issue_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_comment_issue_id" ON public.comment USING btree (issue_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_comment_poster_id" ON public.comment USING btree (poster_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_comment_ref_comment_id" ON public.comment USING btree (ref_comment_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_comment_ref_issue_id" ON public.comment USING btree (ref_issue_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_comment_ref_repo_id" ON public.comment USING btree (ref_repo_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_comment_review_id" ON public.comment USING btree (review_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_comment_type" ON public.comment USING btree (type);

--
--

CREATE INDEX IF NOT EXISTS "IDX_comment_updated_unix" ON public.comment USING btree (updated_unix);

--
--

CREATE INDEX IF NOT EXISTS "IDX_commit_status_context_hash" ON public.commit_status USING btree (context_hash);

--
--

CREATE INDEX IF NOT EXISTS "IDX_commit_status_created_unix" ON public.commit_status USING btree (created_unix);

--
--

CREATE INDEX IF NOT EXISTS "IDX_commit_status_index" ON public.commit_status USING btree (index);

--
--

CREATE INDEX IF NOT EXISTS "IDX_commit_status_index_max_index" ON public.commit_status_index USING btree (max_index);

--
--

CREATE INDEX IF NOT EXISTS "IDX_commit_status_repo_id" ON public.commit_status USING btree (repo_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_commit_status_sha" ON public.commit_status USING btree (sha);

--
--

CREATE INDEX IF NOT EXISTS "IDX_commit_status_summary_repo_id" ON public.commit_status_summary USING btree (repo_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_commit_status_summary_sha" ON public.commit_status_summary USING btree (sha);

--
--

CREATE INDEX IF NOT EXISTS "IDX_commit_status_updated_unix" ON public.commit_status USING btree (updated_unix);

--
--

CREATE INDEX IF NOT EXISTS "IDX_dbfs_data_meta_offset" ON public.dbfs_data USING btree (meta_id, blob_offset);

--
--

CREATE INDEX IF NOT EXISTS "IDX_deploy_key_key_id" ON public.deploy_key USING btree (key_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_deploy_key_repo_id" ON public.deploy_key USING btree (repo_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_email_address_uid" ON public.email_address USING btree (uid);

--
--

CREATE INDEX IF NOT EXISTS "IDX_external_login_user_provider" ON public.external_login_user USING btree (provider);

--
--

CREATE INDEX IF NOT EXISTS "IDX_external_login_user_user_id" ON public.external_login_user USING btree (user_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_follow_created_unix" ON public.follow USING btree (created_unix);

--
--

CREATE INDEX IF NOT EXISTS "IDX_gpg_key_key_id" ON public.gpg_key USING btree (key_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_gpg_key_owner_id" ON public.gpg_key USING btree (owner_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_hook_task_hook_id" ON public.hook_task USING btree (hook_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_issue_assignees_assignee_id" ON public.issue_assignees USING btree (assignee_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_issue_assignees_issue_id" ON public.issue_assignees USING btree (issue_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_issue_closed_unix" ON public.issue USING btree (closed_unix);

--
--

CREATE INDEX IF NOT EXISTS "IDX_issue_content_history_comment_id" ON public.issue_content_history USING btree (comment_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_issue_content_history_edited_unix" ON public.issue_content_history USING btree (edited_unix);

--
--

CREATE INDEX IF NOT EXISTS "IDX_issue_content_history_issue_id" ON public.issue_content_history USING btree (issue_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_issue_created_unix" ON public.issue USING btree (created_unix);

--
--

CREATE INDEX IF NOT EXISTS "IDX_issue_deadline_unix" ON public.issue USING btree (deadline_unix);

--
--

CREATE INDEX IF NOT EXISTS "IDX_issue_index_max_index" ON public.issue_index USING btree (max_index);

--
--

CREATE INDEX IF NOT EXISTS "IDX_issue_is_closed" ON public.issue USING btree (is_closed);

--
--

CREATE INDEX IF NOT EXISTS "IDX_issue_is_pull" ON public.issue USING btree (is_pull);

--
--

CREATE INDEX IF NOT EXISTS "IDX_issue_milestone_id" ON public.issue USING btree (milestone_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_issue_original_author_id" ON public.issue USING btree (original_author_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_issue_poster_id" ON public.issue USING btree (poster_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_issue_repo_id" ON public.issue USING btree (repo_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_issue_updated_unix" ON public.issue USING btree (updated_unix);

--
--

CREATE INDEX IF NOT EXISTS "IDX_issue_user_issue_id" ON public.issue_user USING btree (issue_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_issue_user_uid" ON public.issue_user USING btree (uid);

--
--

CREATE INDEX IF NOT EXISTS "IDX_label_created_unix" ON public.label USING btree (created_unix);

--
--

CREATE INDEX IF NOT EXISTS "IDX_label_org_id" ON public.label USING btree (org_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_label_repo_id" ON public.label USING btree (repo_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_label_updated_unix" ON public.label USING btree (updated_unix);

--
--

CREATE INDEX IF NOT EXISTS "IDX_language_stat_created_unix" ON public.language_stat USING btree (created_unix);

--
--

CREATE INDEX IF NOT EXISTS "IDX_language_stat_language" ON public.language_stat USING btree (language);

--
--

CREATE INDEX IF NOT EXISTS "IDX_language_stat_repo_id" ON public.language_stat USING btree (repo_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_lfs_lock_owner_id" ON public.lfs_lock USING btree (owner_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_lfs_lock_repo_id" ON public.lfs_lock USING btree (repo_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_lfs_meta_object_oid" ON public.lfs_meta_object USING btree (oid);

--
--

CREATE INDEX IF NOT EXISTS "IDX_lfs_meta_object_repository_id" ON public.lfs_meta_object USING btree (repository_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_lfs_meta_object_updated_unix" ON public.lfs_meta_object USING btree (updated_unix);

--
--

CREATE INDEX IF NOT EXISTS "IDX_login_source_created_unix" ON public.login_source USING btree (created_unix);

--
--

CREATE INDEX IF NOT EXISTS "IDX_login_source_is_active" ON public.login_source USING btree (is_active);

--
--

CREATE INDEX IF NOT EXISTS "IDX_login_source_is_sync_enabled" ON public.login_source USING btree (is_sync_enabled);

--
--

CREATE INDEX IF NOT EXISTS "IDX_login_source_updated_unix" ON public.login_source USING btree (updated_unix);

--
--

CREATE INDEX IF NOT EXISTS "IDX_milestone_created_unix" ON public.milestone USING btree (created_unix);

--
--

CREATE INDEX IF NOT EXISTS "IDX_milestone_repo_id" ON public.milestone USING btree (repo_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_milestone_updated_unix" ON public.milestone USING btree (updated_unix);

--
--

CREATE INDEX IF NOT EXISTS "IDX_mirror_next_update_unix" ON public.mirror USING btree (next_update_unix);

--
--

CREATE INDEX IF NOT EXISTS "IDX_mirror_repo_id" ON public.mirror USING btree (repo_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_mirror_updated_unix" ON public.mirror USING btree (updated_unix);

--
--

CREATE INDEX IF NOT EXISTS "IDX_notice_created_unix" ON public.notice USING btree (created_unix);

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

CREATE INDEX IF NOT EXISTS "IDX_oauth2_application_created_unix" ON public.oauth2_application USING btree (created_unix);

--
--

CREATE INDEX IF NOT EXISTS "IDX_oauth2_application_uid" ON public.oauth2_application USING btree (uid);

--
--

CREATE INDEX IF NOT EXISTS "IDX_oauth2_application_updated_unix" ON public.oauth2_application USING btree (updated_unix);

--
--

CREATE INDEX IF NOT EXISTS "IDX_oauth2_authorization_code_valid_until" ON public.oauth2_authorization_code USING btree (valid_until);

--
--

CREATE INDEX IF NOT EXISTS "IDX_oauth2_grant_application_id" ON public.oauth2_grant USING btree (application_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_oauth2_grant_user_id" ON public.oauth2_grant USING btree (user_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_org_user_is_public" ON public.org_user USING btree (is_public);

--
--

CREATE INDEX IF NOT EXISTS "IDX_org_user_org_id" ON public.org_user USING btree (org_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_org_user_uid" ON public.org_user USING btree (uid);

--
--

CREATE INDEX IF NOT EXISTS "IDX_package_blob_created_unix" ON public.package_blob USING btree (created_unix);

--
--

CREATE INDEX IF NOT EXISTS "IDX_package_blob_hash_md5" ON public.package_blob USING btree (hash_md5);

--
--

CREATE INDEX IF NOT EXISTS "IDX_package_blob_hash_sha1" ON public.package_blob USING btree (hash_sha1);

--
--

CREATE INDEX IF NOT EXISTS "IDX_package_blob_hash_sha256" ON public.package_blob USING btree (hash_sha256);

--
--

CREATE INDEX IF NOT EXISTS "IDX_package_blob_hash_sha512" ON public.package_blob USING btree (hash_sha512);

--
--

CREATE INDEX IF NOT EXISTS "IDX_package_blob_upload_updated_unix" ON public.package_blob_upload USING btree (updated_unix);

--
--

CREATE INDEX IF NOT EXISTS "IDX_package_cleanup_rule_enabled" ON public.package_cleanup_rule USING btree (enabled);

--
--

CREATE INDEX IF NOT EXISTS "IDX_package_cleanup_rule_owner_id" ON public.package_cleanup_rule USING btree (owner_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_package_cleanup_rule_type" ON public.package_cleanup_rule USING btree (type);

--
--

CREATE INDEX IF NOT EXISTS "IDX_package_file_blob_id" ON public.package_file USING btree (blob_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_package_file_composite_key" ON public.package_file USING btree (composite_key);

--
--

CREATE INDEX IF NOT EXISTS "IDX_package_file_created_unix" ON public.package_file USING btree (created_unix);

--
--

CREATE INDEX IF NOT EXISTS "IDX_package_file_lower_name" ON public.package_file USING btree (lower_name);

--
--

CREATE INDEX IF NOT EXISTS "IDX_package_file_version_id" ON public.package_file USING btree (version_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_package_lower_name" ON public.package USING btree (lower_name);

--
--

CREATE INDEX IF NOT EXISTS "IDX_package_owner_id" ON public.package USING btree (owner_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_package_property_name" ON public.package_property USING btree (name);

--
--

CREATE INDEX IF NOT EXISTS "IDX_package_property_ref_id" ON public.package_property USING btree (ref_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_package_property_ref_type" ON public.package_property USING btree (ref_type);

--
--

CREATE INDEX IF NOT EXISTS "IDX_package_repo_id" ON public.package USING btree (repo_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_package_type" ON public.package USING btree (type);

--
--

CREATE INDEX IF NOT EXISTS "IDX_package_version_created_unix" ON public.package_version USING btree (created_unix);

--
--

CREATE INDEX IF NOT EXISTS "IDX_package_version_is_internal" ON public.package_version USING btree (is_internal);

--
--

CREATE INDEX IF NOT EXISTS "IDX_package_version_lower_version" ON public.package_version USING btree (lower_version);

--
--

CREATE INDEX IF NOT EXISTS "IDX_package_version_package_id" ON public.package_version USING btree (package_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_project_board_created_unix" ON public.project_board USING btree (created_unix);

--
--

CREATE INDEX IF NOT EXISTS "IDX_project_board_project_id" ON public.project_board USING btree (project_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_project_board_updated_unix" ON public.project_board USING btree (updated_unix);

--
--

CREATE INDEX IF NOT EXISTS "IDX_project_created_unix" ON public.project USING btree (created_unix);

--
--

CREATE INDEX IF NOT EXISTS "IDX_project_is_closed" ON public.project USING btree (is_closed);

--
--

CREATE INDEX IF NOT EXISTS "IDX_project_issue_issue_id" ON public.project_issue USING btree (issue_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_project_issue_project_board_id" ON public.project_issue USING btree (project_board_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_project_issue_project_id" ON public.project_issue USING btree (project_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_project_owner_id" ON public.project USING btree (owner_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_project_repo_id" ON public.project USING btree (repo_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_project_title" ON public.project USING btree (title);

--
--

CREATE INDEX IF NOT EXISTS "IDX_project_updated_unix" ON public.project USING btree (updated_unix);

--
--

CREATE INDEX IF NOT EXISTS "IDX_public_key_fingerprint" ON public.public_key USING btree (fingerprint);

--
--

CREATE INDEX IF NOT EXISTS "IDX_public_key_owner_id" ON public.public_key USING btree (owner_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_pull_auto_merge_doer_id" ON public.pull_auto_merge USING btree (doer_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_pull_request_base_repo_id" ON public.pull_request USING btree (base_repo_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_pull_request_has_merged" ON public.pull_request USING btree (has_merged);

--
--

CREATE INDEX IF NOT EXISTS "IDX_pull_request_head_repo_id" ON public.pull_request USING btree (head_repo_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_pull_request_issue_id" ON public.pull_request USING btree (issue_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_pull_request_merged_unix" ON public.pull_request USING btree (merged_unix);

--
--

CREATE INDEX IF NOT EXISTS "IDX_pull_request_merger_id" ON public.pull_request USING btree (merger_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_push_mirror_last_update" ON public.push_mirror USING btree (last_update);

--
--

CREATE INDEX IF NOT EXISTS "IDX_push_mirror_repo_id" ON public.push_mirror USING btree (repo_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_reaction_comment_id" ON public.reaction USING btree (comment_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_reaction_created_unix" ON public.reaction USING btree (created_unix);

--
--

CREATE INDEX IF NOT EXISTS "IDX_reaction_issue_id" ON public.reaction USING btree (issue_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_reaction_original_author" ON public.reaction USING btree (original_author);

--
--

CREATE INDEX IF NOT EXISTS "IDX_reaction_original_author_id" ON public.reaction USING btree (original_author_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_reaction_type" ON public.reaction USING btree (type);

--
--

CREATE INDEX IF NOT EXISTS "IDX_reaction_user_id" ON public.reaction USING btree (user_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_release_created_unix" ON public.release USING btree (created_unix);

--
--

CREATE INDEX IF NOT EXISTS "IDX_release_original_author_id" ON public.release USING btree (original_author_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_release_publisher_id" ON public.release USING btree (publisher_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_release_repo_id" ON public.release USING btree (repo_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_release_sha1" ON public.release USING btree (sha1);

--
--

CREATE INDEX IF NOT EXISTS "IDX_release_tag_name" ON public.release USING btree (tag_name);

--
--

CREATE INDEX IF NOT EXISTS "IDX_renamed_branch_repo_id" ON public.renamed_branch USING btree (repo_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_repo_archiver_created_unix" ON public.repo_archiver USING btree (created_unix);

--
--

CREATE INDEX IF NOT EXISTS "IDX_repo_archiver_repo_id" ON public.repo_archiver USING btree (repo_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_repo_indexer_status_s" ON public.repo_indexer_status USING btree (repo_id, indexer_type);

--
--

CREATE INDEX IF NOT EXISTS "IDX_repo_license_created_unix" ON public.repo_license USING btree (created_unix);

--
--

CREATE INDEX IF NOT EXISTS "IDX_repo_license_updated_unix" ON public.repo_license USING btree (updated_unix);

--
--

CREATE INDEX IF NOT EXISTS "IDX_repo_redirect_lower_name" ON public.repo_redirect USING btree (lower_name);

--
--

CREATE INDEX IF NOT EXISTS "IDX_repo_transfer_created_unix" ON public.repo_transfer USING btree (created_unix);

--
--

CREATE INDEX IF NOT EXISTS "IDX_repo_transfer_updated_unix" ON public.repo_transfer USING btree (updated_unix);

--
--

CREATE INDEX IF NOT EXISTS "IDX_repo_unit_created_unix" ON public.repo_unit USING btree (created_unix);

--
--

CREATE INDEX IF NOT EXISTS "IDX_repo_unit_s" ON public.repo_unit USING btree (repo_id, type);

--
--

CREATE INDEX IF NOT EXISTS "IDX_repository_created_unix" ON public.repository USING btree (created_unix);

--
--

CREATE INDEX IF NOT EXISTS "IDX_repository_fork_id" ON public.repository USING btree (fork_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_repository_is_archived" ON public.repository USING btree (is_archived);

--
--

CREATE INDEX IF NOT EXISTS "IDX_repository_is_empty" ON public.repository USING btree (is_empty);

--
--

CREATE INDEX IF NOT EXISTS "IDX_repository_is_fork" ON public.repository USING btree (is_fork);

--
--

CREATE INDEX IF NOT EXISTS "IDX_repository_is_mirror" ON public.repository USING btree (is_mirror);

--
--

CREATE INDEX IF NOT EXISTS "IDX_repository_is_private" ON public.repository USING btree (is_private);

--
--

CREATE INDEX IF NOT EXISTS "IDX_repository_is_template" ON public.repository USING btree (is_template);

--
--

CREATE INDEX IF NOT EXISTS "IDX_repository_lower_name" ON public.repository USING btree (lower_name);

--
--

CREATE INDEX IF NOT EXISTS "IDX_repository_name" ON public.repository USING btree (name);

--
--

CREATE INDEX IF NOT EXISTS "IDX_repository_original_service_type" ON public.repository USING btree (original_service_type);

--
--

CREATE INDEX IF NOT EXISTS "IDX_repository_owner_id" ON public.repository USING btree (owner_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_repository_template_id" ON public.repository USING btree (template_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_repository_updated_unix" ON public.repository USING btree (updated_unix);

--
--

CREATE INDEX IF NOT EXISTS "IDX_review_created_unix" ON public.review USING btree (created_unix);

--
--

CREATE INDEX IF NOT EXISTS "IDX_review_issue_id" ON public.review USING btree (issue_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_review_reviewer_id" ON public.review USING btree (reviewer_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_review_state_pull_id" ON public.review_state USING btree (pull_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_review_updated_unix" ON public.review USING btree (updated_unix);

--
--

CREATE INDEX IF NOT EXISTS "IDX_secret_owner_id" ON public.secret USING btree (owner_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_secret_repo_id" ON public.secret USING btree (repo_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_star_created_unix" ON public.star USING btree (created_unix);

--
--

CREATE INDEX IF NOT EXISTS "IDX_stopwatch_issue_id" ON public.stopwatch USING btree (issue_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_stopwatch_user_id" ON public.stopwatch USING btree (user_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_task_doer_id" ON public.task USING btree (doer_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_task_owner_id" ON public.task USING btree (owner_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_task_repo_id" ON public.task USING btree (repo_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_task_status" ON public.task USING btree (status);

--
--

CREATE INDEX IF NOT EXISTS "IDX_team_invite_created_unix" ON public.team_invite USING btree (created_unix);

--
--

CREATE INDEX IF NOT EXISTS "IDX_team_invite_org_id" ON public.team_invite USING btree (org_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_team_invite_team_id" ON public.team_invite USING btree (team_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_team_invite_token" ON public.team_invite USING btree (token);

--
--

CREATE INDEX IF NOT EXISTS "IDX_team_invite_updated_unix" ON public.team_invite USING btree (updated_unix);

--
--

CREATE INDEX IF NOT EXISTS "IDX_team_org_id" ON public.team USING btree (org_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_team_repo_org_id" ON public.team_repo USING btree (org_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_team_unit_org_id" ON public.team_unit USING btree (org_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_team_user_org_id" ON public.team_user USING btree (org_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_topic_created_unix" ON public.topic USING btree (created_unix);

--
--

CREATE INDEX IF NOT EXISTS "IDX_topic_updated_unix" ON public.topic USING btree (updated_unix);

--
--

CREATE INDEX IF NOT EXISTS "IDX_tracked_time_issue_id" ON public.tracked_time USING btree (issue_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_tracked_time_user_id" ON public.tracked_time USING btree (user_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_two_factor_created_unix" ON public.two_factor USING btree (created_unix);

--
--

CREATE INDEX IF NOT EXISTS "IDX_two_factor_updated_unix" ON public.two_factor USING btree (updated_unix);

--
--

CREATE INDEX IF NOT EXISTS "IDX_user_badge_user_id" ON public.user_badge USING btree (user_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_user_blocking_created_unix" ON public.user_blocking USING btree (created_unix);

--
--

CREATE INDEX IF NOT EXISTS "IDX_user_created_unix" ON public."user" USING btree (created_unix);

--
--

CREATE INDEX IF NOT EXISTS "IDX_user_is_active" ON public."user" USING btree (is_active);

--
--

CREATE INDEX IF NOT EXISTS "IDX_user_last_login_unix" ON public."user" USING btree (last_login_unix);

--
--

CREATE INDEX IF NOT EXISTS "IDX_user_open_id_uid" ON public.user_open_id USING btree (uid);

--
--

CREATE INDEX IF NOT EXISTS "IDX_user_redirect_lower_name" ON public.user_redirect USING btree (lower_name);

--
--

CREATE INDEX IF NOT EXISTS "IDX_user_setting_setting_key" ON public.user_setting USING btree (setting_key);

--
--

CREATE INDEX IF NOT EXISTS "IDX_user_setting_user_id" ON public.user_setting USING btree (user_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_user_updated_unix" ON public."user" USING btree (updated_unix);

--
--

CREATE INDEX IF NOT EXISTS "IDX_watch_created_unix" ON public.watch USING btree (created_unix);

--
--

CREATE INDEX IF NOT EXISTS "IDX_watch_updated_unix" ON public.watch USING btree (updated_unix);

--
--

CREATE INDEX IF NOT EXISTS "IDX_webauthn_credential_created_unix" ON public.webauthn_credential USING btree (created_unix);

--
--

CREATE INDEX IF NOT EXISTS "IDX_webauthn_credential_credential_id" ON public.webauthn_credential USING btree (credential_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_webauthn_credential_updated_unix" ON public.webauthn_credential USING btree (updated_unix);

--
--

CREATE INDEX IF NOT EXISTS "IDX_webauthn_credential_user_id" ON public.webauthn_credential USING btree (user_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_webhook_created_unix" ON public.webhook USING btree (created_unix);

--
--

CREATE INDEX IF NOT EXISTS "IDX_webhook_is_active" ON public.webhook USING btree (is_active);

--
--

CREATE INDEX IF NOT EXISTS "IDX_webhook_owner_id" ON public.webhook USING btree (owner_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_webhook_repo_id" ON public.webhook USING btree (repo_id);

--
--

CREATE INDEX IF NOT EXISTS "IDX_webhook_updated_unix" ON public.webhook USING btree (updated_unix);

--
--

CREATE UNIQUE INDEX IF NOT EXISTS "UQE_access_s" ON public.access USING btree (user_id, repo_id);

--
--

CREATE UNIQUE INDEX IF NOT EXISTS "UQE_access_token_token_hash" ON public.access_token USING btree (token_hash);

--
--

CREATE UNIQUE INDEX IF NOT EXISTS "UQE_action_artifact_runid_name_path" ON public.action_artifact USING btree (run_id, artifact_path, artifact_name);

--
--

CREATE UNIQUE INDEX IF NOT EXISTS "UQE_action_run_repo_index" ON public.action_run USING btree (repo_id, index);

--
--

CREATE UNIQUE INDEX IF NOT EXISTS "UQE_action_runner_token_hash" ON public.action_runner USING btree (token_hash);

--
--

CREATE UNIQUE INDEX IF NOT EXISTS "UQE_action_runner_token_token" ON public.action_runner_token USING btree (token);

--
--

CREATE UNIQUE INDEX IF NOT EXISTS "UQE_action_runner_uuid" ON public.action_runner USING btree (uuid);

--
--

CREATE UNIQUE INDEX IF NOT EXISTS "UQE_action_task_output_task_id_output_key" ON public.action_task_output USING btree (task_id, output_key);

--
--

CREATE UNIQUE INDEX IF NOT EXISTS "UQE_action_task_step_task_index" ON public.action_task_step USING btree (task_id, index);

--
--

CREATE UNIQUE INDEX IF NOT EXISTS "UQE_action_task_token_hash" ON public.action_task USING btree (token_hash);

--
--

CREATE UNIQUE INDEX IF NOT EXISTS "UQE_action_tasks_version_owner_repo" ON public.action_tasks_version USING btree (owner_id, repo_id);

--
--

CREATE UNIQUE INDEX IF NOT EXISTS "UQE_action_variable_owner_repo_name" ON public.action_variable USING btree (owner_id, repo_id, name);

--
--

CREATE UNIQUE INDEX IF NOT EXISTS "UQE_attachment_uuid" ON public.attachment USING btree (uuid);

--
--

CREATE UNIQUE INDEX IF NOT EXISTS "UQE_badge_slug" ON public.badge USING btree (slug);

--
--

CREATE UNIQUE INDEX IF NOT EXISTS "UQE_branch_s" ON public.branch USING btree (repo_id, name);

--
--

CREATE UNIQUE INDEX IF NOT EXISTS "UQE_collaboration_s" ON public.collaboration USING btree (repo_id, user_id);

--
--

CREATE UNIQUE INDEX IF NOT EXISTS "UQE_commit_status_index_repo_sha" ON public.commit_status_index USING btree (repo_id, sha);

--
--

CREATE UNIQUE INDEX IF NOT EXISTS "UQE_commit_status_repo_sha_index" ON public.commit_status USING btree (index, repo_id, sha);

--
--

CREATE UNIQUE INDEX IF NOT EXISTS "UQE_commit_status_summary_repo_id_sha" ON public.commit_status_summary USING btree (repo_id, sha);

--
--

CREATE UNIQUE INDEX IF NOT EXISTS "UQE_dbfs_meta_full_path" ON public.dbfs_meta USING btree (full_path);

--
--

CREATE UNIQUE INDEX IF NOT EXISTS "UQE_deploy_key_s" ON public.deploy_key USING btree (key_id, repo_id);

--
--

CREATE UNIQUE INDEX IF NOT EXISTS "UQE_email_address_email" ON public.email_address USING btree (email);

--
--

CREATE UNIQUE INDEX IF NOT EXISTS "UQE_email_address_lower_email" ON public.email_address USING btree (lower_email);

--
--

CREATE UNIQUE INDEX IF NOT EXISTS "UQE_email_hash_email" ON public.email_hash USING btree (email);

--
--

CREATE UNIQUE INDEX IF NOT EXISTS "UQE_follow_follow" ON public.follow USING btree (user_id, follow_id);

--
--

CREATE UNIQUE INDEX IF NOT EXISTS "UQE_hook_task_uuid" ON public.hook_task USING btree (uuid);

--
--

CREATE UNIQUE INDEX IF NOT EXISTS "UQE_issue_dependency_issue_dependency" ON public.issue_dependency USING btree (issue_id, dependency_id);

--
--

CREATE UNIQUE INDEX IF NOT EXISTS "UQE_issue_label_s" ON public.issue_label USING btree (issue_id, label_id);

--
--

CREATE UNIQUE INDEX IF NOT EXISTS "UQE_issue_pin_s" ON public.issue_pin USING btree (repo_id, issue_id);

--
--

CREATE UNIQUE INDEX IF NOT EXISTS "UQE_issue_repo_index" ON public.issue USING btree (repo_id, index);

--
--

CREATE UNIQUE INDEX IF NOT EXISTS "UQE_issue_user_uid_to_issue" ON public.issue_user USING btree (uid, issue_id);

--
--

CREATE UNIQUE INDEX IF NOT EXISTS "UQE_issue_watch_watch" ON public.issue_watch USING btree (user_id, issue_id);

--
--

CREATE UNIQUE INDEX IF NOT EXISTS "UQE_language_stat_s" ON public.language_stat USING btree (repo_id, language);

--
--

CREATE UNIQUE INDEX IF NOT EXISTS "UQE_lfs_meta_object_s" ON public.lfs_meta_object USING btree (oid, repository_id);

--
--

CREATE UNIQUE INDEX IF NOT EXISTS "UQE_login_source_name" ON public.login_source USING btree (name);

--
--

CREATE UNIQUE INDEX IF NOT EXISTS "UQE_oauth2_application_client_id" ON public.oauth2_application USING btree (client_id);

--
--

CREATE UNIQUE INDEX IF NOT EXISTS "UQE_oauth2_authorization_code_code" ON public.oauth2_authorization_code USING btree (code);

--
--

CREATE UNIQUE INDEX IF NOT EXISTS "UQE_oauth2_grant_user_application" ON public.oauth2_grant USING btree (user_id, application_id);

--
--

CREATE UNIQUE INDEX IF NOT EXISTS "UQE_org_user_s" ON public.org_user USING btree (uid, org_id);

--
--

CREATE UNIQUE INDEX IF NOT EXISTS "UQE_package_blob_md5" ON public.package_blob USING btree (hash_md5);

--
--

CREATE UNIQUE INDEX IF NOT EXISTS "UQE_package_blob_sha1" ON public.package_blob USING btree (hash_sha1);

--
--

CREATE UNIQUE INDEX IF NOT EXISTS "UQE_package_blob_sha256" ON public.package_blob USING btree (hash_sha256);

--
--

CREATE UNIQUE INDEX IF NOT EXISTS "UQE_package_blob_sha512" ON public.package_blob USING btree (hash_sha512);

--
--

CREATE UNIQUE INDEX IF NOT EXISTS "UQE_package_cleanup_rule_s" ON public.package_cleanup_rule USING btree (owner_id, type);

--
--

CREATE UNIQUE INDEX IF NOT EXISTS "UQE_package_file_s" ON public.package_file USING btree (version_id, lower_name, composite_key);

--
--

CREATE UNIQUE INDEX IF NOT EXISTS "UQE_package_s" ON public.package USING btree (owner_id, type, lower_name);

--
--

CREATE UNIQUE INDEX IF NOT EXISTS "UQE_package_version_s" ON public.package_version USING btree (package_id, lower_version);

--
--

CREATE UNIQUE INDEX IF NOT EXISTS "UQE_protected_branch_s" ON public.protected_branch USING btree (repo_id, branch_name);

--
--

CREATE UNIQUE INDEX IF NOT EXISTS "UQE_pull_auto_merge_pull_id" ON public.pull_auto_merge USING btree (pull_id);

--
--

CREATE UNIQUE INDEX IF NOT EXISTS "UQE_reaction_s" ON public.reaction USING btree (type, issue_id, comment_id, user_id, original_author_id, original_author);

--
--

CREATE UNIQUE INDEX IF NOT EXISTS "UQE_release_n" ON public.release USING btree (repo_id, tag_name);

--
--

CREATE UNIQUE INDEX IF NOT EXISTS "UQE_repo_archiver_s" ON public.repo_archiver USING btree (repo_id, type, commit_id);

--
--

CREATE UNIQUE INDEX IF NOT EXISTS "UQE_repo_license_s" ON public.repo_license USING btree (repo_id, license);

--
--

CREATE UNIQUE INDEX IF NOT EXISTS "UQE_repo_redirect_s" ON public.repo_redirect USING btree (owner_id, lower_name);

--
--

CREATE UNIQUE INDEX IF NOT EXISTS "UQE_repository_s" ON public.repository USING btree (owner_id, lower_name);

--
--

CREATE UNIQUE INDEX IF NOT EXISTS "UQE_review_state_pull_commit_user" ON public.review_state USING btree (user_id, pull_id, commit_sha);

--
--

CREATE UNIQUE INDEX IF NOT EXISTS "UQE_secret_owner_repo_name" ON public.secret USING btree (owner_id, repo_id, name);

--
--

CREATE UNIQUE INDEX IF NOT EXISTS "UQE_star_s" ON public.star USING btree (uid, repo_id);

--
--

CREATE UNIQUE INDEX IF NOT EXISTS "UQE_system_setting_setting_key" ON public.system_setting USING btree (setting_key);

--
--

CREATE UNIQUE INDEX IF NOT EXISTS "UQE_team_invite_team_mail" ON public.team_invite USING btree (team_id, email);

--
--

CREATE UNIQUE INDEX IF NOT EXISTS "UQE_team_repo_s" ON public.team_repo USING btree (team_id, repo_id);

--
--

CREATE UNIQUE INDEX IF NOT EXISTS "UQE_team_unit_s" ON public.team_unit USING btree (team_id, type);

--
--

CREATE UNIQUE INDEX IF NOT EXISTS "UQE_team_user_s" ON public.team_user USING btree (team_id, uid);

--
--

CREATE UNIQUE INDEX IF NOT EXISTS "UQE_topic_name" ON public.topic USING btree (name);

--
--

CREATE UNIQUE INDEX IF NOT EXISTS "UQE_two_factor_uid" ON public.two_factor USING btree (uid);

--
--

CREATE UNIQUE INDEX IF NOT EXISTS "UQE_upload_uuid" ON public.upload USING btree (uuid);

--
--

CREATE UNIQUE INDEX IF NOT EXISTS "UQE_user_blocking_block" ON public.user_blocking USING btree (blocker_id, blockee_id);

--
--

CREATE UNIQUE INDEX IF NOT EXISTS "UQE_user_lower_name" ON public."user" USING btree (lower_name);

--
--

CREATE UNIQUE INDEX IF NOT EXISTS "UQE_user_name" ON public."user" USING btree (name);

--
--

CREATE UNIQUE INDEX IF NOT EXISTS "UQE_user_open_id_uri" ON public.user_open_id USING btree (uri);

--
--

CREATE UNIQUE INDEX IF NOT EXISTS "UQE_user_redirect_s" ON public.user_redirect USING btree (lower_name);

--
--

CREATE UNIQUE INDEX IF NOT EXISTS "UQE_user_setting_key_userid" ON public.user_setting USING btree (user_id, setting_key);

--
--

CREATE UNIQUE INDEX IF NOT EXISTS "UQE_watch_watch" ON public.watch USING btree (user_id, repo_id);

--
--

CREATE UNIQUE INDEX IF NOT EXISTS "UQE_webauthn_credential_s" ON public.webauthn_credential USING btree (lower_name, user_id);

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

CREATE INDEX IF NOT EXISTS idx_affiliate_links_keyword ON public.affiliate_links USING btree (keyword);

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

CREATE INDEX IF NOT EXISTS idx_alert_rules_enabled ON public.alert_rules USING btree (enabled) WHERE (enabled = true);

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

CREATE INDEX IF NOT EXISTS idx_audit_log_summaries_bucket ON public.audit_log_summaries USING btree (bucket_start);

--
--

CREATE INDEX IF NOT EXISTS idx_audit_log_task_id ON public.audit_log USING btree (task_id);

--
--

CREATE INDEX IF NOT EXISTS idx_audit_log_timestamp ON public.audit_log USING btree ("timestamp");

--
--

CREATE INDEX IF NOT EXISTS idx_brain_decision_summaries_bucket ON public.brain_decision_summaries USING btree (bucket_start);

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

CREATE INDEX IF NOT EXISTS idx_capability_outcomes_task ON public.capability_outcomes USING btree (task_id) WHERE (task_id IS NOT NULL);

--
--

CREATE INDEX IF NOT EXISTS idx_capability_outcomes_template ON public.capability_outcomes USING btree (template_slug, created_at DESC);

--
--

CREATE INDEX IF NOT EXISTS idx_capability_outcomes_tier ON public.capability_outcomes USING btree (capability_tier, ok) WHERE (capability_tier IS NOT NULL);

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

CREATE INDEX IF NOT EXISTS idx_content_calendar_date ON public.content_calendar USING btree (date);

--
--

CREATE INDEX IF NOT EXISTS idx_content_calendar_status ON public.content_calendar USING btree (status);

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

CREATE INDEX IF NOT EXISTS idx_custom_workflows_created_at ON public.custom_workflows USING btree (created_at DESC);

--
--

CREATE INDEX IF NOT EXISTS idx_custom_workflows_is_template ON public.custom_workflows USING btree (is_template);

--
--

CREATE INDEX IF NOT EXISTS idx_custom_workflows_owner_id ON public.custom_workflows USING btree (owner_id);

--
--

CREATE INDEX IF NOT EXISTS idx_custom_workflows_updated_at ON public.custom_workflows USING btree (updated_at DESC);

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

CREATE INDEX IF NOT EXISTS idx_distribution_channels_platform ON public.distribution_channels USING btree (platform);

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

CREATE INDEX IF NOT EXISTS idx_experiment_assignments_exp ON public.experiment_assignments USING btree (experiment_id, variant_key);

--
--

CREATE INDEX IF NOT EXISTS idx_experiments_status ON public.experiments USING btree (status) WHERE (status = 'running'::text);

--
--

CREATE INDEX IF NOT EXISTS idx_experiments_type ON public.experiments USING btree (experiment_type);

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

CREATE INDEX IF NOT EXISTS idx_jwt_blocklist_expires ON public.jwt_blocklist USING btree (expires_at);

--
--

CREATE INDEX IF NOT EXISTS idx_jwt_blocklist_user_id ON public.jwt_blocklist USING btree (user_id);

--
--

CREATE INDEX IF NOT EXISTS idx_logs_agent_name ON public.logs USING btree (agent_name);

--
--

CREATE INDEX IF NOT EXISTS idx_logs_created_at ON public.logs USING btree (created_at);

--
--

CREATE INDEX IF NOT EXISTS idx_logs_level ON public.logs USING btree (level);

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

CREATE INDEX IF NOT EXISTS idx_oauth_accounts_provider ON public.oauth_accounts USING btree (provider, provider_user_id);

--
--

CREATE INDEX IF NOT EXISTS idx_oauth_accounts_user_id ON public.oauth_accounts USING btree (user_id);

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

CREATE INDEX IF NOT EXISTS idx_pipeline_reviews_task ON public.pipeline_reviews USING btree (task_id);

--
--

CREATE INDEX IF NOT EXISTS idx_pipeline_reviews_task_decision ON public.pipeline_reviews USING btree (task_id, decision);

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

CREATE INDEX IF NOT EXISTS idx_post_approval_gates_pending ON public.post_approval_gates USING btree (post_id, ordinal) WHERE (state = 'pending'::text);

--
--

CREATE INDEX IF NOT EXISTS idx_post_approval_gates_post_id ON public.post_approval_gates USING btree (post_id);

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

CREATE INDEX IF NOT EXISTS idx_social_posts_scheduled ON public.social_posts USING btree (scheduled_at) WHERE ((status)::text = 'scheduled'::text);

--
--

CREATE INDEX IF NOT EXISTS idx_social_posts_status ON public.social_posts USING btree (status);

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

CREATE INDEX IF NOT EXISTS idx_tasks_created_at ON public.tasks USING btree (created_at);

--
--

CREATE INDEX IF NOT EXISTS idx_tasks_status ON public.tasks USING btree (status);

--
--

CREATE INDEX IF NOT EXISTS idx_tasks_task_id ON public.tasks USING btree (task_id);

--
--

CREATE INDEX IF NOT EXISTS idx_voice_messages_embedding_hnsw ON public.voice_messages USING hnsw (embedding public.vector_cosine_ops) WITH (m='16', ef_construction='64');

--
--

CREATE INDEX IF NOT EXISTS idx_voice_messages_recent ON public.voice_messages USING btree (created_at DESC);

--
--

CREATE INDEX IF NOT EXISTS idx_voice_messages_user_channel ON public.voice_messages USING btree (discord_user_id, discord_channel_id, created_at DESC);

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

CREATE INDEX IF NOT EXISTS idx_workflow_executions_created_at ON public.workflow_executions USING btree (created_at DESC);

--
--

CREATE INDEX IF NOT EXISTS idx_workflow_executions_execution_mode ON public.workflow_executions USING btree (execution_mode);

--
--

CREATE INDEX IF NOT EXISTS idx_workflow_executions_owner_id ON public.workflow_executions USING btree (owner_id);

--
--

CREATE INDEX IF NOT EXISTS idx_workflow_executions_selected_model ON public.workflow_executions USING btree (selected_model);

--
--

CREATE INDEX IF NOT EXISTS idx_workflow_executions_status ON public.workflow_executions USING btree (execution_status);

--
--

CREATE INDEX IF NOT EXISTS idx_workflow_executions_workflow_id ON public.workflow_executions USING btree (workflow_id);

--
--

CREATE INDEX IF NOT EXISTS idx_workflow_executions_workflow_owner ON public.workflow_executions USING btree (workflow_id, owner_id);

--
--

CREATE INDEX IF NOT EXISTS idx_writing_samples_user_id ON public.writing_samples USING btree (user_id);

--
--

CREATE INDEX IF NOT EXISTS ix_capability_executions_owner_id ON public.capability_executions USING btree (owner_id);

--
--

CREATE INDEX IF NOT EXISTS ix_capability_executions_owner_task ON public.capability_executions USING btree (owner_id, task_id);

--
--

CREATE INDEX IF NOT EXISTS ix_capability_executions_started_at ON public.capability_executions USING btree (started_at);

--
--

CREATE INDEX IF NOT EXISTS ix_capability_executions_status ON public.capability_executions USING btree (status);

--
--

CREATE INDEX IF NOT EXISTS ix_capability_executions_task_id ON public.capability_executions USING btree (task_id);

--
--

CREATE INDEX IF NOT EXISTS ix_capability_tasks_created_at ON public.capability_tasks USING btree (created_at);

--
--

CREATE INDEX IF NOT EXISTS ix_capability_tasks_is_active ON public.capability_tasks USING btree (is_active);

--
--

CREATE INDEX IF NOT EXISTS ix_capability_tasks_owner_id ON public.capability_tasks USING btree (owner_id);

--
--

CREATE INDEX IF NOT EXISTS ix_discovery_runs_niche_started ON public.discovery_runs USING btree (niche_id, started_at DESC);

--
--

CREATE INDEX IF NOT EXISTS ix_internal_topic_candidates_batch ON public.internal_topic_candidates USING btree (batch_id);

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

CREATE UNIQUE INDEX IF NOT EXISTS orchestrator_training_data_execution_id_key ON public.orchestrator_training_data USING btree (execution_id);

--
--

CREATE UNIQUE INDEX IF NOT EXISTS uq_one_open_batch_per_niche ON public.topic_batches USING btree (niche_id) WHERE (status = 'open'::text);

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

DROP TRIGGER IF EXISTS experiments_touch_updated_at_trg ON public.experiments;
CREATE TRIGGER experiments_touch_updated_at_trg BEFORE UPDATE ON public.experiments FOR EACH ROW EXECUTE FUNCTION public.experiments_touch_updated_at();

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

ALTER TABLE ONLY public.discovery_runs
    ADD CONSTRAINT discovery_runs_batch_id_fkey FOREIGN KEY (batch_id) REFERENCES public.topic_batches(id);

--
--

ALTER TABLE ONLY public.discovery_runs
    ADD CONSTRAINT discovery_runs_niche_id_fkey FOREIGN KEY (niche_id) REFERENCES public.niches(id) ON DELETE CASCADE;

--
--

ALTER TABLE ONLY public.distribution_channels
    ADD CONSTRAINT distribution_channels_site_id_fkey FOREIGN KEY (site_id) REFERENCES public.sites(id) ON DELETE SET NULL;

--
--

ALTER TABLE ONLY public.experiment_assignments
    ADD CONSTRAINT experiment_assignments_experiment_id_fkey FOREIGN KEY (experiment_id) REFERENCES public.experiments(id) ON DELETE CASCADE;

--
--

ALTER TABLE ONLY public.capability_executions
    ADD CONSTRAINT fk_capability_executions_task FOREIGN KEY (task_id) REFERENCES public.capability_tasks(id) ON DELETE CASCADE;

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

ALTER TABLE ONLY public.workflow_executions
    ADD CONSTRAINT fk_workflow_executions_workflow FOREIGN KEY (workflow_id) REFERENCES public.custom_workflows(id) ON DELETE CASCADE;

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

ALTER TABLE ONLY public.media_assets
    ADD CONSTRAINT media_assets_post_id_fkey FOREIGN KEY (post_id) REFERENCES public.posts(id) ON DELETE SET NULL;

--
--

ALTER TABLE ONLY public.media_assets
    ADD CONSTRAINT media_assets_site_id_fkey FOREIGN KEY (site_id) REFERENCES public.sites(id) ON DELETE SET NULL;

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

ALTER TABLE ONLY public.oauth_accounts
    ADD CONSTRAINT oauth_accounts_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;

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

ALTER TABLE ONLY public.pipeline_reviews
    ADD CONSTRAINT pipeline_reviews_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.pipeline_tasks(task_id) ON DELETE CASCADE;

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

ALTER TABLE ONLY public.post_approval_gates
    ADD CONSTRAINT post_approval_gates_post_id_fkey FOREIGN KEY (post_id) REFERENCES public.posts(id) ON DELETE CASCADE;

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

ALTER TABLE ONLY public.posts
    ADD CONSTRAINT posts_site_id_fkey FOREIGN KEY (site_id) REFERENCES public.sites(id) ON DELETE SET NULL;

--
--

ALTER TABLE ONLY public.sites
    ADD CONSTRAINT sites_default_category_id_fkey FOREIGN KEY (default_category_id) REFERENCES public.categories(id) ON DELETE SET NULL;

--
--

ALTER TABLE ONLY public.social_posts
    ADD CONSTRAINT social_posts_channel_id_fkey FOREIGN KEY (channel_id) REFERENCES public.distribution_channels(id) ON DELETE SET NULL;

--
--

ALTER TABLE ONLY public.social_posts
    ADD CONSTRAINT social_posts_site_id_fkey FOREIGN KEY (site_id) REFERENCES public.sites(id) ON DELETE SET NULL;

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

