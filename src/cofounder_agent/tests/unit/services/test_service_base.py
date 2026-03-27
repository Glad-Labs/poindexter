"""
Unit tests for ServiceBase, ServiceRegistry, ServiceAction, ActionResult,
JsonSchema, and ServiceError.

All tests are pure in-process — no LLM, DB, or network calls.
"""

import pytest

from services.service_base import (
    ActionResult,
    ActionStatus,
    JsonSchema,
    ServiceAction,
    ServiceBase,
    ServiceError,
    ServiceRegistry,
    get_service_registry,
    set_service_registry,
)

# ---------------------------------------------------------------------------
# Concrete test implementations
# ---------------------------------------------------------------------------


class EchoService(ServiceBase):
    """Minimal concrete service for testing."""

    name = "echo"
    version = "1.0.0"
    description = "Echo service for testing"

    def get_actions(self):
        return [
            ServiceAction(
                name="say_hello",
                description="Returns a greeting",
                input_schema=JsonSchema(
                    type="object",
                    properties={"name": {"type": "string"}},
                    required=["name"],
                ),
                output_schema=JsonSchema(
                    type="object",
                    properties={"greeting": {"type": "string"}},
                ),
            ),
            ServiceAction(
                name="no_required_params",
                description="Action with no required params",
                input_schema=JsonSchema(type="object"),
                output_schema=JsonSchema(type="object"),
            ),
        ]

    async def action_say_hello(self, name: str) -> dict:
        return {"greeting": f"Hello, {name}!"}

    async def action_no_required_params(self) -> dict:
        return {"result": "ok"}


class FailingService(ServiceBase):
    """Service whose action always raises an exception."""

    name = "failing"
    version = "0.1.0"
    description = "Always fails"

    def get_actions(self):
        return [
            ServiceAction(
                name="always_fail",
                description="Raises an exception",
                input_schema=JsonSchema(type="object"),
                output_schema=JsonSchema(type="object"),
            ),
        ]

    async def action_always_fail(self) -> dict:
        raise RuntimeError("Intentional failure for testing")


class ServiceErrorService(ServiceBase):
    """Service whose action raises ServiceError."""

    name = "service_error"
    version = "0.1.0"
    description = "Raises ServiceError"

    def get_actions(self):
        return [
            ServiceAction(
                name="raise_service_error",
                description="Raises ServiceError",
                input_schema=JsonSchema(type="object"),
                output_schema=JsonSchema(type="object"),
            ),
        ]

    async def action_raise_service_error(self) -> dict:
        raise ServiceError(
            error_code="CUSTOM_ERROR",
            message="Custom service error",
            details={"key": "value"},
        )


class NoMethodService(ServiceBase):
    """Service registers an action but has no action_ method."""

    name = "no_method"
    version = "0.1.0"
    description = "Missing action method"

    def get_actions(self):
        return [
            ServiceAction(
                name="missing_impl",
                description="No backing method",
                input_schema=JsonSchema(type="object"),
                output_schema=JsonSchema(type="object"),
            ),
        ]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def echo_svc() -> EchoService:
    return EchoService()


@pytest.fixture
def registry() -> ServiceRegistry:
    reg = ServiceRegistry()
    reg.register(EchoService())
    reg.register(FailingService())
    return reg


@pytest.fixture(autouse=True)
def reset_global_registry():
    """Avoid global singleton leaking between tests."""
    set_service_registry(None)  # type: ignore[arg-type]
    yield
    set_service_registry(None)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# JsonSchema
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestJsonSchema:
    def test_to_dict_includes_type_and_properties(self):
        schema = JsonSchema(type="object", properties={"x": {"type": "integer"}})
        d = schema.to_dict()
        assert d["type"] == "object"
        assert "x" in d["properties"]

    def test_to_dict_includes_required_when_set(self):
        schema = JsonSchema(type="object", required=["x"])
        d = schema.to_dict()
        assert d["required"] == ["x"]

    def test_to_dict_omits_required_when_empty(self):
        schema = JsonSchema(type="object")
        d = schema.to_dict()
        assert "required" not in d

    def test_to_dict_includes_description_when_set(self):
        schema = JsonSchema(type="object", description="My schema")
        assert schema.to_dict()["description"] == "My schema"

    def test_to_dict_omits_description_when_none(self):
        schema = JsonSchema(type="object")
        assert "description" not in schema.to_dict()


# ---------------------------------------------------------------------------
# ServiceAction
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestServiceAction:
    def test_to_dict_contains_all_fields(self):
        action = ServiceAction(
            name="my_action",
            description="Does something",
            input_schema=JsonSchema(type="object"),
            output_schema=JsonSchema(type="object"),
            error_codes=["ERR_A"],
            requires_auth=False,
            is_async=True,
        )
        d = action.to_dict()
        assert d["name"] == "my_action"
        assert d["description"] == "Does something"
        assert d["error_codes"] == ["ERR_A"]
        assert d["requires_auth"] is False
        assert d["is_async"] is True
        assert "input_schema" in d
        assert "output_schema" in d

    def test_default_requires_auth_true(self):
        action = ServiceAction(
            name="a",
            description="b",
            input_schema=JsonSchema(type="object"),
            output_schema=JsonSchema(type="object"),
        )
        assert action.to_dict()["requires_auth"] is True


# ---------------------------------------------------------------------------
# ServiceError
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestServiceError:
    def test_message_propagated(self):
        err = ServiceError(error_code="E001", message="Something went wrong")
        # err.message is always the plain message text
        assert err.message == "Something went wrong"
        # str(err) includes the error code prefix for structured logging
        assert "Something went wrong" in str(err)

    def test_error_code_stored(self):
        err = ServiceError(error_code="E001", message="msg")
        assert err.error_code == "E001"

    def test_details_default_empty_dict(self):
        err = ServiceError(error_code="E001", message="msg")
        assert err.details == {}

    def test_details_stored_when_provided(self):
        err = ServiceError(error_code="E001", message="msg", details={"field": "value"})
        assert err.details["field"] == "value"


# ---------------------------------------------------------------------------
# ServiceBase — _load_actions / get_all_actions / get_action
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestServiceBaseActions:
    def test_actions_loaded_on_init(self, echo_svc: EchoService):
        actions = echo_svc.get_all_actions()
        assert len(actions) == 2

    def test_get_action_known_returns_service_action(self, echo_svc: EchoService):
        action = echo_svc.get_action("say_hello")
        assert action is not None
        assert action.name == "say_hello"

    def test_get_action_unknown_returns_none(self, echo_svc: EchoService):
        assert echo_svc.get_action("nonexistent") is None

    def test_get_service_metadata_structure(self, echo_svc: EchoService):
        meta = echo_svc.get_service_metadata()
        assert meta["name"] == "echo"
        assert meta["version"] == "1.0.0"
        assert meta["action_count"] == 2
        assert "say_hello" in meta["actions"]


# ---------------------------------------------------------------------------
# ServiceBase — execute_action happy path
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExecuteActionSuccess:
    @pytest.mark.asyncio
    async def test_successful_action_returns_completed_status(self, echo_svc: EchoService):
        result = await echo_svc.execute_action("say_hello", {"name": "World"})
        assert result.status == ActionStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_successful_action_returns_correct_data(self, echo_svc: EchoService):
        result = await echo_svc.execute_action("say_hello", {"name": "World"})
        assert result.data == {"greeting": "Hello, World!"}

    @pytest.mark.asyncio
    async def test_action_without_required_params_succeeds(self, echo_svc: EchoService):
        result = await echo_svc.execute_action("no_required_params", {})
        assert result.status == ActionStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_execution_time_is_set(self, echo_svc: EchoService):
        result = await echo_svc.execute_action("say_hello", {"name": "Test"})
        assert result.execution_time_ms is not None
        assert result.execution_time_ms >= 0

    @pytest.mark.asyncio
    async def test_result_action_name_matches(self, echo_svc: EchoService):
        result = await echo_svc.execute_action("say_hello", {"name": "Alice"})
        assert result.action == "say_hello"

    @pytest.mark.asyncio
    async def test_metadata_includes_service_name(self, echo_svc: EchoService):
        result = await echo_svc.execute_action("say_hello", {"name": "Bob"})
        assert result.metadata.get("service") == "echo"


# ---------------------------------------------------------------------------
# ServiceBase — execute_action error paths
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExecuteActionErrors:
    @pytest.mark.asyncio
    async def test_unknown_action_returns_failed(self, echo_svc: EchoService):
        result = await echo_svc.execute_action("nonexistent", {})
        assert result.status == ActionStatus.FAILED
        assert result.error_code == "ACTION_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_missing_required_param_returns_failed(self, echo_svc: EchoService):
        result = await echo_svc.execute_action("say_hello", {})  # missing 'name'
        assert result.status == ActionStatus.FAILED
        assert result.error_code == "VALIDATION_ERROR"

    @pytest.mark.asyncio
    async def test_missing_action_method_returns_failed(self):
        svc = NoMethodService()
        result = await svc.execute_action("missing_impl", {})
        assert result.status == ActionStatus.FAILED
        assert result.error_code == "ACTION_IMPLEMENTATION_MISSING"

    @pytest.mark.asyncio
    async def test_service_error_in_action_returns_failed(self):
        svc = ServiceErrorService()
        result = await svc.execute_action("raise_service_error", {})
        assert result.status == ActionStatus.FAILED
        assert result.error_code == "CUSTOM_ERROR"
        assert result.error is not None
        assert "Custom service error" in result.error

    @pytest.mark.asyncio
    async def test_unexpected_exception_returns_failed(self):
        svc = FailingService()
        result = await svc.execute_action("always_fail", {})
        assert result.status == ActionStatus.FAILED
        assert result.error_code == "UNEXPECTED_ERROR"
        assert result.error is not None
        assert "Intentional failure" in result.error


# ---------------------------------------------------------------------------
# ServiceBase — call_service
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCallService:
    @pytest.mark.asyncio
    async def test_call_service_without_registry_raises_service_error(self):
        svc = EchoService()  # No registry injected
        with pytest.raises(ServiceError) as exc_info:
            await svc.call_service("other_service", "some_action", {})
        assert exc_info.value.error_code == "REGISTRY_NOT_AVAILABLE"


# ---------------------------------------------------------------------------
# ServiceRegistry
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestServiceRegistry:
    def test_register_and_retrieve_service(self, registry: ServiceRegistry):
        svc = registry.get_service("echo")
        assert svc is not None
        assert svc.name == "echo"

    def test_get_unknown_service_returns_none(self, registry: ServiceRegistry):
        assert registry.get_service("does_not_exist") is None

    def test_registry_injects_itself_into_service(self, registry: ServiceRegistry):
        svc = registry.get_service("echo")
        assert svc is not None
        assert svc.service_registry is registry

    def test_list_services_returns_all(self, registry: ServiceRegistry):
        services = registry.list_services()
        names = {s["name"] for s in services}
        assert "echo" in names
        assert "failing" in names

    def test_list_actions_for_known_service(self, registry: ServiceRegistry):
        actions = registry.list_actions("echo")
        names = {a["name"] for a in actions}
        assert "say_hello" in names

    def test_list_actions_for_unknown_service_returns_empty(self, registry: ServiceRegistry):
        assert registry.list_actions("nonexistent") == []

    def test_get_registry_schema_structure(self, registry: ServiceRegistry):
        schema = registry.get_registry_schema()
        assert "services" in schema
        assert "total_services" in schema
        assert "total_actions" in schema
        assert schema["total_services"] == 2

    def test_get_registry_schema_total_actions_correct(self, registry: ServiceRegistry):
        schema = registry.get_registry_schema()
        # echo: 2 actions, failing: 1 action
        assert schema["total_actions"] == 3

    @pytest.mark.asyncio
    async def test_execute_action_on_known_service(self, registry: ServiceRegistry):
        result = await registry.execute_action("echo", "say_hello", {"name": "Test"})
        assert result.status == ActionStatus.COMPLETED
        assert result.data is not None
        assert result.data["greeting"] == "Hello, Test!"

    @pytest.mark.asyncio
    async def test_execute_action_on_unknown_service_returns_failed(
        self, registry: ServiceRegistry
    ):
        result = await registry.execute_action("ghost", "phantom", {})
        assert result.status == ActionStatus.FAILED
        assert result.error_code == "SERVICE_NOT_FOUND"


# ---------------------------------------------------------------------------
# Global get/set service registry
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGlobalRegistry:
    def test_get_service_registry_returns_service_registry(self):
        reg = get_service_registry()
        assert isinstance(reg, ServiceRegistry)

    def test_repeated_calls_return_same_instance(self):
        reg1 = get_service_registry()
        reg2 = get_service_registry()
        assert reg1 is reg2

    def test_set_service_registry_replaces_instance(self):
        custom_reg = ServiceRegistry()
        set_service_registry(custom_reg)
        assert get_service_registry() is custom_reg


# ---------------------------------------------------------------------------
# ActionResult
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestActionResult:
    def test_default_status_not_set(self):
        result = ActionResult(action="test", status=ActionStatus.COMPLETED)
        assert result.status == ActionStatus.COMPLETED

    def test_failed_result_has_error(self):
        result = ActionResult(
            action="test",
            status=ActionStatus.FAILED,
            error="Something broke",
            error_code="E001",
        )
        assert result.error == "Something broke"
        assert result.error_code == "E001"

    def test_metadata_defaults_to_empty_dict(self):
        result = ActionResult(action="test", status=ActionStatus.COMPLETED)
        assert isinstance(result.metadata, dict)

    def test_data_defaults_to_none(self):
        result = ActionResult(action="test", status=ActionStatus.PENDING)
        assert result.data is None
