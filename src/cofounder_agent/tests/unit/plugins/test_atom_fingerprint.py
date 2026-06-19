"""contract_fingerprint() — structural drift tripwire (poindexter#755)."""
from plugins.atom import AtomMeta, FieldSpec


def _meta(
    *,
    requires: tuple[str, ...] = (),
    produces: tuple[str, ...] = (),
    inputs: tuple[FieldSpec, ...] = (),
    outputs: tuple[FieldSpec, ...] = (),
    description: str = "d",
) -> AtomMeta:
    return AtomMeta(
        name="atoms.x",
        type="atom",
        version="1.0.0",
        description=description,
        requires=requires,
        produces=produces,
        inputs=inputs,
        outputs=outputs,
    )


class TestContractFingerprint:
    def test_stable_for_identical_contract(self):
        a = _meta(requires=("draft",), produces=("title",))
        b = _meta(requires=("draft",), produces=("title",))
        assert a.contract_fingerprint() == b.contract_fingerprint()

    def test_is_short_hex(self):
        fp = _meta().contract_fingerprint()
        assert len(fp) == 12 and all(c in "0123456789abcdef" for c in fp)

    def test_changes_when_requires_changes(self):
        a = _meta(requires=("draft",))
        b = _meta(requires=("draft", "context_bundle"))
        assert a.contract_fingerprint() != b.contract_fingerprint()

    def test_changes_when_produces_changes(self):
        a = _meta(produces=("title",))
        b = _meta(produces=("title", "slug"))
        assert a.contract_fingerprint() != b.contract_fingerprint()

    def test_changes_when_input_field_type_changes(self):
        a = _meta(inputs=(FieldSpec(name="n", type="str"),))
        b = _meta(inputs=(FieldSpec(name="n", type="int"),))
        assert a.contract_fingerprint() != b.contract_fingerprint()

    def test_unchanged_when_only_description_changes(self):
        a = _meta(description="old", requires=("draft",))
        b = _meta(description="totally different", requires=("draft",))
        assert a.contract_fingerprint() == b.contract_fingerprint()

    def test_unchanged_when_only_field_description_changes(self):
        a = _meta(inputs=(FieldSpec(name="n", type="str", description="x"),))
        b = _meta(inputs=(FieldSpec(name="n", type="str", description="y"),))
        assert a.contract_fingerprint() == b.contract_fingerprint()

    def test_requires_order_independent(self):
        a = _meta(requires=("a", "b"))
        b = _meta(requires=("b", "a"))
        assert a.contract_fingerprint() == b.contract_fingerprint()
