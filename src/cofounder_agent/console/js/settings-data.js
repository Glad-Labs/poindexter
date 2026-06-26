/* ──────────────────────────────────────────────────────────────
   Poindexter Operator Console — app_settings dataset.
   Mirrors brain/seed_app_settings.json (key, value, category,
   description) + inferred type/options for type-aware editing.
   In production this comes from GET /api/settings.
   ────────────────────────────────────────────────────────────── */
(function () {
  // category → friendly label + accent
  const CATEGORIES = [
    { id: 'identity', label: 'Identity' },
    { id: 'pipeline', label: 'Pipeline' },
    { id: 'models', label: 'Models' },
    { id: 'model_roles', label: 'Model Roles' },
    { id: 'quality', label: 'Quality / QA' },
    { id: 'content', label: 'Content' },
    { id: 'cost', label: 'Cost Guard' },
    { id: 'image', label: 'Imagery' },
    { id: 'features', label: 'Features' },
    { id: 'observability', label: 'Observability' },
    { id: 'prometheus', label: 'Prometheus' },
    { id: 'infrastructure', label: 'Infrastructure' },
  ];

  // type: bool | int | float | text | textarea | select | secret
  const S = [
    // identity
    {
      key: 'site_name',
      value: 'Glad Labs',
      category: 'identity',
      type: 'text',
      description: 'Brand/site name used across all services',
    },
    {
      key: 'site_url',
      value: 'https://www.gladlabs.io',
      category: 'identity',
      type: 'text',
      description: 'Full production URL with protocol',
    },
    {
      key: 'site_tagline',
      value: 'Local-first AI publishing',
      category: 'identity',
      type: 'text',
      description: 'Short tagline used in metadata',
    },
    {
      key: 'company_name',
      value: 'Glad Labs LLC',
      category: 'identity',
      type: 'text',
      description: 'Legal company name',
    },
    {
      key: 'owner_email',
      value: 'matt@gladlabs.io',
      category: 'identity',
      type: 'text',
      description: 'Site owner email',
    },
    {
      key: 'support_email',
      value: 'support@gladlabs.io',
      category: 'identity',
      type: 'text',
      description: 'Support contact email',
    },

    // pipeline
    {
      key: 'require_human_approval',
      value: 'true',
      category: 'pipeline',
      type: 'bool',
      description: 'All publishes require human approval',
    },
    {
      key: 'staging_mode',
      value: 'true',
      category: 'pipeline',
      type: 'bool',
      description: 'Posts go to draft + preview token instead of publishing',
    },
    {
      key: 'auto_publish_threshold',
      value: '0',
      category: 'pipeline',
      type: 'int',
      description:
        'Auto-publish score threshold (0 = disabled, human approval required)',
    },
    {
      key: 'max_posts_per_day',
      value: '3',
      category: 'pipeline',
      type: 'int',
      description: 'Hard cap on publishes per day',
    },
    {
      key: 'daily_post_limit',
      value: '1',
      category: 'pipeline',
      type: 'int',
      description: 'Soft cap on generation per day',
    },
    {
      key: 'content_weekly_cap',
      value: '3',
      category: 'pipeline',
      type: 'int',
      description: 'Maximum new posts per week (0 = unlimited)',
    },
    {
      key: 'max_approval_queue',
      value: '10',
      category: 'pipeline',
      type: 'int',
      description: 'Queue size before generation throttles',
    },
    {
      key: 'max_task_retries',
      value: '3',
      category: 'pipeline',
      type: 'int',
      description: 'Max retry attempts for failed tasks',
    },
    {
      key: 'publish_spacing_hours',
      value: '4',
      category: 'pipeline',
      type: 'int',
      description: 'Minimum hours between publishes',
    },
    {
      key: 'approval_ttl_days',
      value: '7',
      category: 'pipeline',
      type: 'int',
      description: 'Days before unapproved posts auto-expire',
    },
    {
      key: 'stale_task_timeout_minutes',
      value: '180',
      category: 'pipeline',
      type: 'int',
      description: 'Task considered stale after this',
    },

    // models
    {
      key: 'pipeline_writer_model',
      value: 'ollama/glm-4.7-50b',
      category: 'models',
      type: 'text',
      description: 'Primary content generation model',
    },
    {
      key: 'pipeline_critic_model',
      value: 'ollama/glm-4.7-50b',
      category: 'models',
      type: 'text',
      description: 'QA/content review model',
    },
    {
      key: 'pipeline_fallback_model',
      value: 'ollama/gemma3:27b',
      category: 'models',
      type: 'text',
      description: 'Fallback model when primary is unavailable',
    },
    {
      key: 'pipeline_seo_model',
      value: 'ollama/qwen3:8b',
      category: 'models',
      type: 'text',
      description: 'SEO title/description generation (smaller, faster)',
    },
    {
      key: 'cloud_api_mode',
      value: 'disabled',
      category: 'models',
      type: 'select',
      options: ['disabled', 'emergency_only', 'fallback', 'always'],
      description: 'Cloud API mode. Free tier runs Ollama-only.',
    },
    {
      key: 'cloud_api_daily_limit',
      value: '0',
      category: 'models',
      type: 'int',
      description: 'Hard cap on cloud API calls per day in emergency mode',
    },

    // model_roles
    {
      key: 'model_role_writer',
      value: 'ollama/glm-4.7-50b',
      category: 'model_roles',
      type: 'text',
      description: 'Writer role model',
    },
    {
      key: 'model_role_critic',
      value: 'ollama/glm-4.7-50b',
      category: 'model_roles',
      type: 'text',
      description: 'Quality scoring / structured JSON output',
    },
    {
      key: 'model_role_image_prompt',
      value: 'ollama/qwen3:8b',
      category: 'model_roles',
      type: 'text',
      description: 'Generates AI image prompts from post content',
    },

    // quality
    {
      key: 'qa_overall_score_threshold',
      value: '70',
      category: 'quality',
      type: 'int',
      description: 'Minimum overall QA score (0-100) to pass',
    },
    {
      key: 'qa_final_score_threshold',
      value: '70',
      category: 'quality',
      type: 'int',
      description: 'Multi-model QA final approval threshold',
    },
    {
      key: 'qa_critical_dimension_floor',
      value: '50',
      category: 'quality',
      type: 'int',
      description: 'Minimum score on any single dimension',
    },
    {
      key: 'qa_validator_weight',
      value: '0.4',
      category: 'quality',
      type: 'float',
      description: 'Weight for programmatic validator',
    },
    {
      key: 'qa_critic_weight',
      value: '0.6',
      category: 'quality',
      type: 'float',
      description: 'Weight for LLM critic',
    },
    {
      key: 'min_curation_score',
      value: '70',
      category: 'quality',
      type: 'int',
      description: 'Minimum QA score to surface for review',
    },

    // content
    {
      key: 'content_min_word_count',
      value: '800',
      category: 'content',
      type: 'int',
      description: 'Minimum word count for blog posts',
    },
    {
      key: 'content_target_word_count',
      value: '1500',
      category: 'content',
      type: 'int',
      description: 'Target word count for blog posts',
    },
    {
      key: 'content_max_refinement_attempts',
      value: '3',
      category: 'content',
      type: 'int',
      description: 'Max refinement iterations',
    },
    {
      key: 'content_temperature',
      value: '0.7',
      category: 'content',
      type: 'float',
      description: 'Temperature for content generation',
    },

    // cost
    {
      key: 'daily_spend_limit_usd',
      value: '1.00',
      category: 'cost',
      type: 'float',
      description: 'Hard cap on daily AI spend in USD',
    },
    {
      key: 'monthly_spend_limit_usd',
      value: '20.00',
      category: 'cost',
      type: 'float',
      description: 'Hard cap on monthly AI spend in USD',
    },
    {
      key: 'cost_alert_threshold_pct',
      value: '80',
      category: 'cost',
      type: 'int',
      description: 'Alert when spend exceeds this % of limit',
    },

    // image
    {
      key: 'image_primary_source',
      value: 'ai_generation',
      category: 'image',
      type: 'select',
      options: ['pexels', 'ai_generation'],
      description: 'Primary image source',
    },
    {
      key: 'enable_featured_image',
      value: 'true',
      category: 'image',
      type: 'bool',
      description: 'Generate featured images for posts',
    },
    {
      key: 'image_generation_model',
      value: 'sdxl_lightning',
      category: 'image',
      type: 'text',
      description: 'AI image generation model',
    },
    {
      key: 'image_style_default',
      value:
        'professional digital art, abstract technology concept, blue and cyan color scheme, clean modern aesthetic, no people, no text',
      category: 'image',
      type: 'textarea',
      description: 'Default image-gen style for uncategorized posts',
    },

    // features
    {
      key: 'enable_memory_system',
      value: 'true',
      category: 'features',
      type: 'bool',
      description: 'Enable agent memory (pgvector)',
    },
    {
      key: 'enable_mcp_server',
      value: 'true',
      category: 'features',
      type: 'bool',
      description: 'Enable Model Context Protocol server',
    },
    {
      key: 'enable_training_capture',
      value: 'false',
      category: 'features',
      type: 'bool',
      description: 'Capture training data from pipeline runs',
    },
    {
      key: 'redis_enabled',
      value: 'false',
      category: 'features',
      type: 'bool',
      description: 'Enable Redis for caching and pub/sub',
    },

    // observability
    {
      key: 'enable_tracing',
      value: 'true',
      category: 'observability',
      type: 'bool',
      description: 'Enable OTel tracing to Tempo',
    },
    {
      key: 'otel_exporter_otlp_endpoint',
      value: 'http://tempo:4318/v1/traces',
      category: 'observability',
      type: 'text',
      description: 'Where traces are exported',
    },
    {
      key: 'enable_pyroscope',
      value: 'false',
      category: 'observability',
      type: 'bool',
      description: 'Continuous profiling via pyroscope agent',
    },
    {
      key: 'sentry_enabled',
      value: 'true',
      category: 'observability',
      type: 'bool',
      description: 'Master switch for GlitchTip/Sentry error tracking',
    },
    {
      key: 'sentry_dsn',
      value: 'enc:••••••••••••••••',
      category: 'observability',
      type: 'secret',
      description:
        'GlitchTip DSN. Masked — secrets never round-trip the read API.',
    },

    // prometheus
    {
      key: 'prometheus.threshold.embeddings_stale_seconds',
      value: '21600',
      category: 'prometheus',
      type: 'int',
      description:
        'Seconds without embeddings change before EmbeddingsStale fires (default 6h)',
    },
    {
      key: 'prometheus.threshold.daily_spend_warning_usd',
      value: '4.0',
      category: 'prometheus',
      type: 'float',
      description: 'Daily LLM spend (USD) that triggers a warning alert',
    },
    {
      key: 'prometheus.threshold.daily_spend_critical_usd',
      value: '5.0',
      category: 'prometheus',
      type: 'float',
      description: 'Daily LLM spend (USD) that triggers a critical alert',
    },

    // infrastructure
    {
      key: 'ollama_base_url',
      value: 'http://host.docker.internal:11434',
      category: 'infrastructure',
      type: 'text',
      description: 'Ollama API endpoint',
    },
    {
      key: 'gpu_temperature_high_threshold_c',
      value: '83',
      category: 'infrastructure',
      type: 'int',
      description: 'GPU temp (°C) that trips the gpu_temperature probe',
    },
  ];

  // stamp ids + meta (mirrors SettingResponse shape)
  S.forEach((s, i) => {
    s.id = i + 1;
    s.is_active = true;
    s.is_secret = s.type === 'secret';
    s.updated_at = '2026-06-08';
  });

  window.PX_SETTINGS = { categories: CATEGORIES, settings: S };
})();
