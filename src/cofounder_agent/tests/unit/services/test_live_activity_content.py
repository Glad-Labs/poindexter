"""Pure node → (step, honest progress %) mapper for the content seam."""
from services.live_activity_content import content_step_pct


class _Rec:
    def __init__(self, name):
        self.name = name  # mirrors TemplateRunRecord.name


def test_step_is_node_name_and_pct_is_position():
    step, pct = content_step_pct(_Rec("qa.critic"), seq=25, total=42)
    assert step == "qa.critic"
    assert pct == round(100 * 26 / 42)  # 1-based node position


def test_pct_capped_and_total_guarded():
    assert content_step_pct(_Rec("x"), seq=0, total=0)[1] is None  # no total → step-only
    assert content_step_pct(_Rec("x"), seq=99, total=42)[1] == 99  # never > 99 until finish
