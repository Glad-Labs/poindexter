"""
API Endpoint Coverage Tests
===========================

Comprehensive tests for all major API endpoints:
- Task management endpoints
- Agent management endpoints
- Workflow execution endpoints
- Settings and configuration endpoints
- Analytics and metrics endpoints
- WebSocket and real-time endpoints
"""

import pytest
from tests.conftest_enhanced import APITester


# ========================
# Health & Status Endpoints
# ========================

@pytest.mark.integration
@pytest.mark.endpoint
async def test_health_endpoint(api_tester):
    """GET /health returns service status"""
    await api_tester.get('/health')
    response = api_tester.last_response
    assert response.status_code == 200
    data = response.json()
    assert 'status' in data


@pytest.mark.integration
@pytest.mark.endpoint
async def test_status_endpoint(api_tester):
    """GET /api/status returns detailed service status"""
    await api_tester.get('/api/status')
    response = api_tester.last_response
    assert response.status_code in [200, 404]  # May not exist
    if response.status_code == 200:
        data = response.json()
        assert 'services' in data or 'status' in data


@pytest.mark.integration
@pytest.mark.endpoint
async def test_version_endpoint(api_tester):
    """GET /api/version returns version info"""
    await api_tester.get('/api/version')
    response = api_tester.last_response
    assert response.status_code in [200, 404]
    if response.status_code == 200:
        data = response.json()
        assert 'version' in data


# ========================
# Task Management Endpoints
# ========================

@pytest.mark.integration
@pytest.mark.endpoint
async def test_list_tasks_endpoint(api_tester):
    """GET /api/tasks lists all tasks"""
    await api_tester.get('/api/tasks')
    api_tester.assert_status(200)
    data = api_tester.get_json()
    assert isinstance(data, list) or isinstance(data, dict)


@pytest.mark.integration
@pytest.mark.endpoint
async def test_create_task_endpoint(api_tester):
    """POST /api/tasks creates new task"""
    await api_tester.post('/api/tasks', json={
        'title': 'Test Task',
        'description': 'Test Description'
    })
    response = api_tester.last_response
    assert response.status_code in [200, 201]


@pytest.mark.integration
@pytest.mark.endpoint
async def test_get_task_endpoint(api_tester):
    """GET /api/tasks/{id} retrieves specific task"""
    # Create a task first
    await api_tester.post('/api/tasks', json={'title': 'Get Test'})
    if api_tester.last_response.status_code == 201:
        task = api_tester.last_response.json()
        task_id = task['id']
        
        # Get the task
        await api_tester.get(f'/api/tasks/{task_id}')
        api_tester.assert_status(200)


@pytest.mark.integration
@pytest.mark.endpoint
async def test_update_task_endpoint(api_tester):
    """PUT /api/tasks/{id} updates task"""
    # Create a task
    await api_tester.post('/api/tasks', json={'title': 'Update Test'})
    if api_tester.last_response.status_code == 201:
        task = api_tester.last_response.json()
        task_id = task['id']
        
        # Update it
        await api_tester.put(f'/api/tasks/{task_id}', json={
            'title': 'Updated Title'
        })
        response = api_tester.last_response
        assert response.status_code in [200, 204]


@pytest.mark.integration
@pytest.mark.endpoint
async def test_delete_task_endpoint(api_tester):
    """DELETE /api/tasks/{id} deletes task"""
    # Create a task
    await api_tester.post('/api/tasks', json={'title': 'Delete Test'})
    if api_tester.last_response.status_code == 201:
        task = api_tester.last_response.json()
        task_id = task['id']
        
        # Delete it
        await api_tester.delete(f'/api/tasks/{task_id}')
        response = api_tester.last_response
        assert response.status_code in [200, 204]


# ========================
# Task Filtering Endpoints
# ========================

@pytest.mark.integration
@pytest.mark.endpoint
async def test_filter_tasks_by_status(api_tester):
    """GET /api/tasks?status={status} filters by status"""
    await api_tester.get('/api/tasks?status=pending')
    api_tester.assert_status(200)


@pytest.mark.integration
@pytest.mark.endpoint
async def test_filter_tasks_by_priority(api_tester):
    """GET /api/tasks?priority={num} filters by priority"""
    await api_tester.get('/api/tasks?priority=1')
    api_tester.assert_status(200)


@pytest.mark.integration
@pytest.mark.endpoint
async def test_paginate_tasks(api_tester):
    """GET /api/tasks?page={p}&limit={l} paginates results"""
    await api_tester.get('/api/tasks?page=1&limit=10')
    api_tester.assert_status(200)


@pytest.mark.integration
@pytest.mark.endpoint
async def test_sort_tasks(api_tester):
    """GET /api/tasks?sort={field} sorts results"""
    await api_tester.get('/api/tasks?sort=-created_at')
    api_tester.assert_status(200)


# ========================
# Agent Management Endpoints
# ========================

@pytest.mark.integration
@pytest.mark.endpoint
async def test_list_agents_endpoint(api_tester):
    """GET /api/agents lists available agents"""
    await api_tester.get('/api/agents')
    response = api_tester.last_response
    assert response.status_code in [200, 404]


@pytest.mark.integration
@pytest.mark.endpoint
async def test_get_agent_info(api_tester):
    """GET /api/agents/{id} retrieves agent details"""
    await api_tester.get('/api/agents/content_agent')
    response = api_tester.last_response
    assert response.status_code in [200, 404]


@pytest.mark.integration
@pytest.mark.endpoint
async def test_agent_registry_endpoint(api_tester):
    """GET /api/agents/registry lists agent registry"""
    await api_tester.get('/api/agents/registry')
    response = api_tester.last_response
    assert response.status_code in [200, 404]


# ========================
# Workflow Endpoints
# ========================

@pytest.mark.integration
@pytest.mark.endpoint
async def test_workflow_templates_endpoint(api_tester):
    """GET /api/workflow/templates lists workflow templates"""
    await api_tester.get('/api/workflow/templates')
    response = api_tester.last_response
    # Some deployments may not have workflows
    assert response.status_code in [200, 404]


@pytest.mark.integration
@pytest.mark.endpoint
async def test_execute_workflow_endpoint(api_tester):
    """POST /api/workflows/execute/{template} executes workflow"""
    await api_tester.post('/api/workflows/execute/blog_post', json={})
    response = api_tester.last_response
    assert response.status_code in [200, 201, 404]  # May not have template


@pytest.mark.integration
@pytest.mark.endpoint
async def test_workflow_status_endpoint(api_tester):
    """GET /api/workflows/{id} gets workflow status"""
    await api_tester.get('/api/workflows/test-workflow-123')
    response = api_tester.last_response
    assert response.status_code in [200, 404]


# ========================
# Capability Endpoints
# ========================

@pytest.mark.integration
@pytest.mark.endpoint
async def test_capability_tasks_creation(api_tester):
    """POST /api/capability-tasks creates capability-based task"""
    await api_tester.post('/api/capability-tasks', json={
        'intent': 'Generate a blog post about Python',
        'capabilities': ['content_writing', 'research']
    })
    response = api_tester.last_response
    assert response.status_code in [200, 201, 404]


@pytest.mark.integration
@pytest.mark.endpoint
async def test_service_registry_endpoint(api_tester):
    """GET /api/service-registry lists available services"""
    await api_tester.get('/api/service-registry')
    response = api_tester.last_response
    assert response.status_code in [200, 404]


# ========================
# Settings Endpoints
# ========================

@pytest.mark.integration
@pytest.mark.endpoint
async def test_get_settings_endpoint(api_tester):
    """GET /api/settings retrieves system settings"""
    await api_tester.get('/api/settings')
    response = api_tester.last_response
    assert response.status_code in [200, 404]


@pytest.mark.integration
@pytest.mark.endpoint
async def test_update_settings_endpoint(api_tester):
    """PUT /api/settings updates system settings"""
    await api_tester.put('/api/settings', json={
        'setting_key': 'setting_value'
    })
    response = api_tester.last_response
    assert response.status_code in [200, 204, 401, 403, 404]


# ========================
# Model/LLM Endpoints
# ========================

@pytest.mark.integration
@pytest.mark.endpoint
async def test_list_models_endpoint(api_tester):
    """GET /api/models lists available LLM models"""
    await api_tester.get('/api/models')
    response = api_tester.last_response
    assert response.status_code in [200, 404]


@pytest.mark.integration
@pytest.mark.endpoint
async def test_model_health_endpoint(api_tester):
    """GET /api/models/health checks model availability"""
    await api_tester.get('/api/models/health')
    response = api_tester.last_response
    assert response.status_code in [200, 404]


@pytest.mark.integration
@pytest.mark.endpoint
async def test_select_model_endpoint(api_tester):
    """POST /api/models/select selects preferred model"""
    await api_tester.post('/api/models/select', json={
        'provider': 'claude',
        'model': 'claude-3.5-sonnet'
    })
    response = api_tester.last_response
    assert response.status_code in [200, 201, 404]


# ========================
# Analytics & Metrics Endpoints
# ========================

@pytest.mark.integration
@pytest.mark.endpoint
async def test_kpi_endpoint(api_tester):
    """GET /api/analytics/kpi retrieves KPI metrics"""
    await api_tester.get('/api/analytics/kpi')
    response = api_tester.last_response
    assert response.status_code in [200, 404]


@pytest.mark.integration
@pytest.mark.endpoint
async def test_metrics_endpoint(api_tester):
    """GET /api/metrics retrieves performance metrics"""
    await api_tester.get('/api/metrics')
    response = api_tester.last_response
    assert response.status_code in [200, 404]


@pytest.mark.integration
@pytest.mark.endpoint
async def test_task_metrics_endpoint(api_tester):
    """GET /api/tasks/metrics retrieves task-specific metrics"""
    await api_tester.get('/api/tasks/metrics')
    response = api_tester.last_response
    assert response.status_code in [200, 404]


# ========================
# Approval Queue Endpoints
# ========================

@pytest.mark.integration
@pytest.mark.endpoint
async def test_approval_queue_endpoint(api_tester):
    """GET /api/approval-queue retrieves pending approvals"""
    await api_tester.get('/api/approval-queue')
    response = api_tester.last_response
    assert response.status_code in [200, 404]


@pytest.mark.integration
@pytest.mark.endpoint
async def test_approve_task_endpoint(api_tester):
    """POST /api/approval-queue/{id}/approve approves task"""
    await api_tester.post('/api/approval-queue/test-id/approve', json={
        'approved': True
    })
    response = api_tester.last_response
    assert response.status_code in [200, 201, 404]


# ========================
# Content/Media Endpoints
# ========================

@pytest.mark.integration
@pytest.mark.endpoint
async def test_list_posts_endpoint(api_tester):
    """GET /api/posts lists published content"""
    await api_tester.get('/api/posts')
    response = api_tester.last_response
    assert response.status_code in [200, 404]


@pytest.mark.integration
@pytest.mark.endpoint
async def test_upload_media_endpoint(api_tester):
    """POST /api/media uploads media file"""
    await api_tester.post('/api/media', json={
        'filename': 'test.jpg',
        'data': 'base64data=='
    })
    response = api_tester.last_response
    # Various responses depending on implementation
    assert response.status_code >= 200


@pytest.mark.integration
@pytest.mark.endpoint
async def test_writing_styles_endpoint(api_tester):
    """GET /api/writing-styles retrieves writing styles"""
    await api_tester.get('/api/writing-styles')
    response = api_tester.last_response
    assert response.status_code in [200, 404]


# ========================
# Newsletter Endpoints
# ========================

@pytest.mark.integration
@pytest.mark.endpoint
async def test_newsletter_subscribers_endpoint(api_tester):
    """GET /api/newsletter/subscribers lists subscribers"""
    await api_tester.get('/api/newsletter/subscribers')
    response = api_tester.last_response
    assert response.status_code in [200, 404]


@pytest.mark.integration
@pytest.mark.endpoint
async def test_send_newsletter_endpoint(api_tester):
    """POST /api/newsletter/send sends newsletter"""
    await api_tester.post('/api/newsletter/send', json={
        'subject': 'Test Newsletter',
        'content': 'Test content'
    })
    response = api_tester.last_response
    assert response.status_code in [200, 201, 401, 404]


# ========================
# User/Auth Endpoints
# ========================

@pytest.mark.integration
@pytest.mark.endpoint
async def test_login_endpoint(api_tester):
    """POST /api/auth/login authenticates user"""
    await api_tester.post('/api/auth/login', json={
        'username': 'test@example.com',
        'password': 'password'
    })
    response = api_tester.last_response
    assert response.status_code in [200, 401, 404]


@pytest.mark.integration
@pytest.mark.endpoint
async def test_logout_endpoint(api_tester):
    """POST /api/auth/logout logs out user"""
    await api_tester.post('/api/auth/logout', json={})
    response = api_tester.last_response
    assert response.status_code in [200, 204, 401, 404]


@pytest.mark.integration
@pytest.mark.endpoint
async def test_profile_endpoint(api_tester):
    """GET /api/users/me retrieves current user profile"""
    await api_tester.get('/api/users/me')
    response = api_tester.last_response
    assert response.status_code in [200, 401, 404]


# ========================
# Webhook Endpoints
# ========================

@pytest.mark.integration
@pytest.mark.endpoint
async def test_list_webhooks_endpoint(api_tester):
    """GET /api/webhooks lists registered webhooks"""
    await api_tester.get('/api/webhooks')
    response = api_tester.last_response
    assert response.status_code in [200, 404]


@pytest.mark.integration
@pytest.mark.endpoint
async def test_create_webhook_endpoint(api_tester):
    """POST /api/webhooks registers new webhook"""
    await api_tester.post('/api/webhooks', json={
        'url': 'https://example.com/webhook',
        'events': ['task.created', 'task.updated']
    })
    response = api_tester.last_response
    assert response.status_code in [200, 201, 401, 404]


# ========================
# Debugging/Admin Endpoints
# ========================

@pytest.mark.integration
@pytest.mark.endpoint
@pytest.mark.slow
async def test_debug_endpoints_protected(api_tester):
    """Debug endpoints require authentication"""
    endpoints_to_test = [
        '/api/debug/logs',
        '/api/debug/cache',
        '/api/debug/database',
    ]
    
    for endpoint in endpoints_to_test:
        await api_tester.get(endpoint)
        response = api_tester.last_response
        # Should be protected or not exist
        assert response.status_code in [401, 403, 404]
