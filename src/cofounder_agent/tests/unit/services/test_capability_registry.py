"""
Unit tests for CapabilityRegistry and related data classes.

All tests are pure in-memory — no I/O, no DB, no LLM calls.
"""

import pytest

from services.capability_registry import (
    Capability,
    CapabilityMetadata,
    CapabilityRegistry,
    InputSchema,
    OutputSchema,
    ParameterSchema,
    ParameterType,
    get_registry,
    set_registry,
)

# ---------------------------------------------------------------------------
# Minimal concrete Capability for tests
# ---------------------------------------------------------------------------


class _EchoCapability(Capability):
    """Simple capability that echoes its input."""

    @property
    def metadata(self) -> CapabilityMetadata:
        return CapabilityMetadata(
            name="echo",
            description="Echoes the input value",
            tags=["utility", "test"],
            cost_tier="ultra_cheap",
        )

    @property
    def input_schema(self) -> InputSchema:
        return InputSchema(
            parameters=[
                ParameterSchema(
                    name="value", type=ParameterType.STRING, description="Value to echo"
                )
            ]
        )

    @property
    def output_schema(self) -> OutputSchema:
        return OutputSchema(
            return_type=ParameterType.STRING,
            description="Echoed value",
        )

    async def execute(self, **inputs):
        return inputs.get("value", "")


class _MultiplyCapability(Capability):
    """Capability with multiple required inputs."""

    @property
    def metadata(self) -> CapabilityMetadata:
        return CapabilityMetadata(
            name="multiply",
            description="Multiplies two numbers",
            tags=["math"],
            cost_tier="cheap",
        )

    @property
    def input_schema(self) -> InputSchema:
        return InputSchema(
            parameters=[
                ParameterSchema(name="a", type=ParameterType.FLOAT),
                ParameterSchema(name="b", type=ParameterType.FLOAT),
            ]
        )

    @property
    def output_schema(self) -> OutputSchema:
        return OutputSchema(return_type=ParameterType.FLOAT)

    async def execute(self, **inputs):
        return inputs["a"] * inputs["b"]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def registry() -> CapabilityRegistry:
    """Fresh registry for each test."""
    return CapabilityRegistry()


@pytest.fixture
def populated_registry(registry) -> CapabilityRegistry:
    """Registry pre-loaded with echo + multiply capabilities."""
    registry.register(_EchoCapability())
    registry.register(_MultiplyCapability())
    return registry


# ---------------------------------------------------------------------------
# ParameterSchema
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestParameterSchema:
    def test_to_dict_contains_all_fields(self):
        param = ParameterSchema(
            name="my_param",
            type=ParameterType.INTEGER,
            description="A test param",
            required=False,
            default=42,
            example=10,
            enum_values=[1, 2, 3],
        )
        d = param.to_dict()
        assert d["name"] == "my_param"
        assert d["type"] == "integer"
        assert d["required"] is False
        assert d["default"] == 42
        assert d["enum_values"] == [1, 2, 3]

    def test_required_defaults_to_true(self):
        param = ParameterSchema(name="x", type=ParameterType.STRING)
        assert param.required is True


# ---------------------------------------------------------------------------
# InputSchema.validate
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestInputSchemaValidate:
    def test_valid_inputs_returns_true(self):
        schema = InputSchema(
            parameters=[
                ParameterSchema(name="text", type=ParameterType.STRING),
            ]
        )
        ok, err = schema.validate({"text": "hello"})
        assert ok is True
        assert err is None

    def test_missing_required_returns_false_with_message(self):
        schema = InputSchema(
            parameters=[
                ParameterSchema(name="text", type=ParameterType.STRING, required=True),
            ]
        )
        ok, err = schema.validate({})
        assert ok is False
        assert err is not None and "text" in err

    def test_optional_param_not_in_inputs_still_valid(self):
        schema = InputSchema(
            parameters=[
                ParameterSchema(name="opt", type=ParameterType.STRING, required=False),
            ]
        )
        ok, err = schema.validate({})
        assert ok is True

    def test_empty_schema_always_valid(self):
        schema = InputSchema(parameters=[])
        ok, err = schema.validate({})
        assert ok is True


# ---------------------------------------------------------------------------
# CapabilityRegistry.register
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRegistryRegister:
    def test_register_adds_capability(self, registry):
        registry.register(_EchoCapability())
        assert registry.get("echo") is not None

    def test_duplicate_name_raises_value_error(self, registry):
        registry.register(_EchoCapability())
        with pytest.raises(ValueError, match="echo"):
            registry.register(_EchoCapability())

    def test_metadata_stored_on_register(self, registry):
        registry.register(_EchoCapability())
        meta = registry.get_metadata("echo")
        assert meta is not None
        assert meta.name == "echo"
        assert meta.cost_tier == "ultra_cheap"


# ---------------------------------------------------------------------------
# CapabilityRegistry.register_function
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRegistryRegisterFunction:
    def test_register_sync_function(self, registry):
        def add(a, b):
            return a + b

        registry.register_function(
            func=add,
            name="add",
            description="Adds two numbers",
            input_schema=InputSchema(),
            output_schema=OutputSchema(),
            tags=["math"],
            cost_tier="ultra_cheap",
        )
        assert registry.get_function("add") is add

    def test_duplicate_function_name_raises(self, registry):
        def noop():
            pass

        registry.register_function(
            func=noop,
            name="noop",
            description="Does nothing",
            input_schema=InputSchema(),
            output_schema=OutputSchema(),
        )
        with pytest.raises(ValueError, match="noop"):
            registry.register_function(
                func=noop,
                name="noop",
                description="Does nothing again",
                input_schema=InputSchema(),
                output_schema=OutputSchema(),
            )

    def test_function_metadata_stored(self, registry):
        def my_func():
            pass

        registry.register_function(
            func=my_func,
            name="my_func",
            description="My function",
            input_schema=InputSchema(),
            output_schema=OutputSchema(),
            tags=["custom"],
            cost_tier="premium",
        )
        meta = registry.get_metadata("my_func")
        assert meta.cost_tier == "premium"
        assert "custom" in meta.tags


# ---------------------------------------------------------------------------
# CapabilityRegistry list methods
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRegistryListMethods:
    def test_list_capabilities_returns_all(self, populated_registry):
        caps = populated_registry.list_capabilities()
        assert "echo" in caps
        assert "multiply" in caps

    def test_list_by_tag_returns_matching(self, populated_registry):
        names = populated_registry.list_by_tag("math")
        assert "multiply" in names
        assert "echo" not in names

    def test_list_by_tag_no_match_returns_empty(self, populated_registry):
        names = populated_registry.list_by_tag("nonexistent_tag")
        assert names == []

    def test_list_by_cost_tier_returns_matching(self, populated_registry):
        names = populated_registry.list_by_cost_tier("ultra_cheap")
        assert "echo" in names
        assert "multiply" not in names

    def test_list_capabilities_is_a_copy(self, populated_registry):
        """Mutating the returned dict does not affect the registry."""
        caps = populated_registry.list_capabilities()
        caps["injected"] = None
        assert "injected" not in populated_registry.list_capabilities()


# ---------------------------------------------------------------------------
# CapabilityRegistry.get / get_function / get_metadata
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRegistryGet:
    def test_get_unknown_returns_none(self, registry):
        assert registry.get("unknown") is None

    def test_get_function_unknown_returns_none(self, registry):
        assert registry.get_function("unknown") is None

    def test_get_metadata_unknown_returns_none(self, registry):
        assert registry.get_metadata("unknown") is None


# ---------------------------------------------------------------------------
# CapabilityRegistry.execute (async)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRegistryExecute:
    @pytest.mark.asyncio
    async def test_execute_capability(self, populated_registry):
        result = await populated_registry.execute("echo", value="hello")
        assert result == "hello"

    @pytest.mark.asyncio
    async def test_execute_multiply_capability(self, populated_registry):
        result = await populated_registry.execute("multiply", a=3.0, b=4.0)
        assert result == 12.0

    @pytest.mark.asyncio
    async def test_execute_sync_function(self, registry):
        def double(n):
            return n * 2

        registry.register_function(
            func=double,
            name="double",
            description="Doubles a number",
            input_schema=InputSchema(),
            output_schema=OutputSchema(),
        )
        result = await registry.execute("double", n=5)
        assert result == 10

    @pytest.mark.asyncio
    async def test_execute_async_function(self, registry):
        async def greet(person):
            return f"Hello, {person}"

        registry.register_function(
            func=greet,
            name="greet",
            description="Greets a person",
            input_schema=InputSchema(),
            output_schema=OutputSchema(),
        )
        result = await registry.execute("greet", person="World")
        assert result == "Hello, World"

    @pytest.mark.asyncio
    async def test_execute_unknown_raises_value_error(self, registry):
        with pytest.raises(ValueError, match="not found"):
            await registry.execute("no_such_capability")


# ---------------------------------------------------------------------------
# CapabilityRegistry.to_dict
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRegistryToDict:
    def test_to_dict_includes_all_capabilities(self, populated_registry):
        d = populated_registry.to_dict()
        assert "echo" in d
        assert "multiply" in d

    def test_to_dict_structure_per_capability(self, populated_registry):
        d = populated_registry.to_dict()
        echo_entry = d["echo"]
        assert "metadata" in echo_entry
        assert "input_schema" in echo_entry
        assert "output_schema" in echo_entry

    def test_function_based_capability_has_empty_input_schema(self, registry):
        def noop():
            pass

        registry.register_function(
            func=noop,
            name="noop",
            description="noop",
            input_schema=InputSchema(),
            output_schema=OutputSchema(),
        )
        d = registry.to_dict()
        assert d["noop"]["input_schema"] == {"parameters": []}


# ---------------------------------------------------------------------------
# Global registry helpers
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGlobalRegistry:
    def test_get_registry_creates_instance_if_none(self):
        set_registry(None)  # type: ignore[arg-type]
        reg = get_registry()
        assert isinstance(reg, CapabilityRegistry)

    def test_set_registry_replaces_global(self):
        new_reg = CapabilityRegistry()
        set_registry(new_reg)
        assert get_registry() is new_reg
