"""
Unit tests for config.constants module.

These tests verify that all constants have the expected types and
sensible values — catching regressions from accidental edits.
"""

import pytest

from config.constants import (
    API_TIMEOUT_HEALTH_CHECK,
    API_TIMEOUT_LLM_CALL,
    API_TIMEOUT_STANDARD,
    CACHE_TTL_API_RESPONSE,
    CACHE_TTL_METRICS,
    CACHE_TTL_SLUG_LOOKUP,
    CACHE_TTL_USER_DATA,
    CLOUDINARY_DELETE_TIMEOUT,
    CLOUDINARY_UPLOAD_TIMEOUT,
    CLOUDINARY_USAGE_TIMEOUT,
    CONTENT_GENERATION_TIMEOUT_SECONDS,
    CONTENT_PUBLISH_RETRY_ATTEMPTS,
    DB_CONNECTION_TIMEOUT,
    DB_QUERY_TIMEOUT,
    HTTP_STATUS_BAD_REQUEST,
    HTTP_STATUS_CREATED,
    HTTP_STATUS_FORBIDDEN,
    HTTP_STATUS_INTERNAL_ERROR,
    HTTP_STATUS_NOT_FOUND,
    HTTP_STATUS_OK,
    HTTP_STATUS_SERVICE_UNAVAILABLE,
    HUGGINGFACE_LONG_TIMEOUT,
    HUGGINGFACE_QUICK_TIMEOUT,
    HUGGINGFACE_STANDARD_TIMEOUT,
    IMAGE_MAX_DIMENSION,
    IMAGE_MAX_SIZE_BYTES,
    IMAGE_QUALITY_STANDARD,
    IMAGE_QUALITY_THUMBNAIL,
    LOG_LEVEL_DEVELOPMENT,
    LOG_LEVEL_PRODUCTION,
    MAX_CATEGORIES,
    MAX_DESCRIPTION_LENGTH,
    MAX_REQUEST_SIZE_BYTES,
    MAX_RETRIES,
    MAX_TAGS,
    MAX_TASK_NAME_LENGTH,
    MODEL_TIMEOUT_CLAUDE,
    MODEL_TIMEOUT_GEMINI,
    MODEL_TIMEOUT_GPT4,
    MODEL_TIMEOUT_OLLAMA,
    NEWSLETTER_GENERATION_TIMEOUT_SECONDS,
    RETRY_BACKOFF_FACTOR,
    TASK_BATCH_SIZE,
    TASK_POLL_INTERVAL,
    TASK_POLL_MAX_ATTEMPTS,
    TASK_STATUS_UPDATE_INTERVAL,
    TASK_TIMEOUT_MAX_SECONDS,
    WORKFLOW_AUTO_RETRY,
    WORKFLOW_PHASE_TIMEOUT_SECONDS,
    WORKFLOW_TIMEOUT_MINUTES,
)


# ---------------------------------------------------------------------------
# Type validation
# ---------------------------------------------------------------------------


class TestConstantTypes:
    """Verify all constants have the expected Python types."""

    def test_api_timeouts_are_floats(self):
        for val in [API_TIMEOUT_STANDARD, API_TIMEOUT_HEALTH_CHECK, API_TIMEOUT_LLM_CALL]:
            assert isinstance(val, float), f"Expected float, got {type(val)}"

    def test_model_timeouts_are_ints(self):
        for val in [MODEL_TIMEOUT_OLLAMA, MODEL_TIMEOUT_CLAUDE, MODEL_TIMEOUT_GPT4, MODEL_TIMEOUT_GEMINI]:
            assert isinstance(val, int), f"Expected int, got {type(val)}"

    def test_retry_config_types(self):
        assert isinstance(MAX_RETRIES, int)
        assert isinstance(RETRY_BACKOFF_FACTOR, int)

    def test_request_limits_are_ints(self):
        for val in [MAX_REQUEST_SIZE_BYTES, MAX_TAGS, MAX_CATEGORIES, MAX_TASK_NAME_LENGTH, MAX_DESCRIPTION_LENGTH]:
            assert isinstance(val, int), f"Expected int, got {type(val)}"

    def test_poll_config_types(self):
        assert isinstance(TASK_POLL_INTERVAL, int)
        assert isinstance(TASK_POLL_MAX_ATTEMPTS, int)

    def test_db_timeouts_are_floats(self):
        assert isinstance(DB_CONNECTION_TIMEOUT, float)
        assert isinstance(DB_QUERY_TIMEOUT, float)

    def test_log_levels_are_strings(self):
        assert isinstance(LOG_LEVEL_PRODUCTION, str)
        assert isinstance(LOG_LEVEL_DEVELOPMENT, str)

    def test_cache_ttls_are_ints(self):
        for val in [CACHE_TTL_SLUG_LOOKUP, CACHE_TTL_API_RESPONSE, CACHE_TTL_USER_DATA, CACHE_TTL_METRICS]:
            assert isinstance(val, int)

    def test_cloudinary_timeouts_are_floats(self):
        for val in [CLOUDINARY_UPLOAD_TIMEOUT, CLOUDINARY_DELETE_TIMEOUT, CLOUDINARY_USAGE_TIMEOUT]:
            assert isinstance(val, float)

    def test_huggingface_timeouts_are_floats(self):
        for val in [HUGGINGFACE_QUICK_TIMEOUT, HUGGINGFACE_STANDARD_TIMEOUT, HUGGINGFACE_LONG_TIMEOUT]:
            assert isinstance(val, float)

    def test_workflow_constants_types(self):
        assert isinstance(WORKFLOW_TIMEOUT_MINUTES, int)
        assert isinstance(WORKFLOW_AUTO_RETRY, bool)
        assert isinstance(WORKFLOW_PHASE_TIMEOUT_SECONDS, int)

    def test_content_timeouts_are_ints(self):
        assert isinstance(CONTENT_GENERATION_TIMEOUT_SECONDS, int)
        assert isinstance(NEWSLETTER_GENERATION_TIMEOUT_SECONDS, int)
        assert isinstance(CONTENT_PUBLISH_RETRY_ATTEMPTS, int)

    def test_image_constants_types(self):
        assert isinstance(IMAGE_MAX_SIZE_BYTES, int)
        assert isinstance(IMAGE_MAX_DIMENSION, int)
        assert isinstance(IMAGE_QUALITY_STANDARD, float)
        assert isinstance(IMAGE_QUALITY_THUMBNAIL, float)

    def test_task_execution_types(self):
        assert isinstance(TASK_TIMEOUT_MAX_SECONDS, int)
        assert isinstance(TASK_BATCH_SIZE, int)
        assert isinstance(TASK_STATUS_UPDATE_INTERVAL, int)

    def test_http_status_codes_are_ints(self):
        for val in [
            HTTP_STATUS_OK, HTTP_STATUS_CREATED, HTTP_STATUS_BAD_REQUEST,
            HTTP_STATUS_FORBIDDEN, HTTP_STATUS_NOT_FOUND,
            HTTP_STATUS_INTERNAL_ERROR, HTTP_STATUS_SERVICE_UNAVAILABLE,
        ]:
            assert isinstance(val, int)


# ---------------------------------------------------------------------------
# Value validation — sanity-check known values
# ---------------------------------------------------------------------------


class TestConstantValues:
    """Verify key constants have expected values."""

    def test_http_status_ok_is_200(self):
        assert HTTP_STATUS_OK == 200

    def test_http_status_created_is_201(self):
        assert HTTP_STATUS_CREATED == 201

    def test_http_status_bad_request_is_400(self):
        assert HTTP_STATUS_BAD_REQUEST == 400

    def test_http_status_forbidden_is_403(self):
        assert HTTP_STATUS_FORBIDDEN == 403

    def test_http_status_not_found_is_404(self):
        assert HTTP_STATUS_NOT_FOUND == 404

    def test_http_status_internal_error_is_500(self):
        assert HTTP_STATUS_INTERNAL_ERROR == 500

    def test_http_status_service_unavailable_is_503(self):
        assert HTTP_STATUS_SERVICE_UNAVAILABLE == 503

    def test_log_level_production_is_info(self):
        assert LOG_LEVEL_PRODUCTION == "INFO"

    def test_log_level_development_is_debug(self):
        assert LOG_LEVEL_DEVELOPMENT == "DEBUG"

    def test_max_retries_is_positive(self):
        assert MAX_RETRIES > 0

    def test_retry_backoff_factor_is_positive(self):
        assert RETRY_BACKOFF_FACTOR > 0

    def test_workflow_auto_retry_is_true(self):
        assert WORKFLOW_AUTO_RETRY is True

    def test_image_quality_standard_in_valid_range(self):
        assert 0.0 < IMAGE_QUALITY_STANDARD <= 1.0

    def test_image_quality_thumbnail_in_valid_range(self):
        assert 0.0 < IMAGE_QUALITY_THUMBNAIL <= 1.0

    def test_thumbnail_quality_less_than_standard(self):
        assert IMAGE_QUALITY_THUMBNAIL < IMAGE_QUALITY_STANDARD

    def test_api_timeout_llm_greater_than_standard(self):
        assert API_TIMEOUT_LLM_CALL > API_TIMEOUT_STANDARD

    def test_api_timeout_health_check_less_than_standard(self):
        assert API_TIMEOUT_HEALTH_CHECK < API_TIMEOUT_STANDARD

    def test_huggingface_long_timeout_greater_than_standard(self):
        assert HUGGINGFACE_LONG_TIMEOUT > HUGGINGFACE_STANDARD_TIMEOUT

    def test_huggingface_quick_timeout_less_than_standard(self):
        assert HUGGINGFACE_QUICK_TIMEOUT < HUGGINGFACE_STANDARD_TIMEOUT

    def test_cache_ttl_api_response_longer_than_slug(self):
        # API response cache (1 hour) should be longer than slug cache (5 min)
        assert CACHE_TTL_API_RESPONSE > CACHE_TTL_SLUG_LOOKUP

    def test_task_poll_interval_positive(self):
        assert TASK_POLL_INTERVAL > 0

    def test_task_poll_max_attempts_positive(self):
        assert TASK_POLL_MAX_ATTEMPTS > 0

    def test_max_request_size_is_one_megabyte(self):
        assert MAX_REQUEST_SIZE_BYTES == 1_000_000

    def test_image_max_size_is_ten_megabytes(self):
        assert IMAGE_MAX_SIZE_BYTES == 10_485_760

    def test_db_query_timeout_longer_than_connection_timeout(self):
        assert DB_QUERY_TIMEOUT >= DB_CONNECTION_TIMEOUT
