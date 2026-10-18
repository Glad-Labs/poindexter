"""The advisory taste review: a verdict in the approval message, never a decision.

2026-10-17: the operator's review time is the glad-labs publishing bottleneck,
and the score gate cannot go live because the operator rejected 9 of the 24 drafts it
would have published, for reasons a score cannot see. The review reads each
awaiting draft against the operator's own rubric and puts its verdict beside the preview.

Pinned here: it runs only for a listed niche with a rubric written; the prompt
carries the rubric, the date, recent titles and the whole draft; every verdict
is recorded so ``agreement`` can compare it with the operator's decision; a failed or
garbled call is no review at all (never a default verdict); and the approval
message carries the line, or goes out without it.
"""

from __future__ import annotations

import datetime as dt
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from poindexter.services import taste_review as tr
from poindexter.services.site_config import SiteConfig

DRAFT = "Intro paragraph.\n\n![chart](a.png)\n\nWe measured decode speed on two cards. " * 20
ROW = {"niche_slug": "glad-labs", "topic": "decode speed", "created_at": dt.datetime(2026, 10, 17, 12),
       "topic_subject": "AI and computer hardware", "title": "Decode Speed on Two Cards", "content": DRAFT}


def _sc(**extra):
    base = {"taste_review_niches": "glad-labs", "taste_review_rubric": "1. Says something new.",
            "taste_review_model": "anthropic/claude-sonnet-5-5"}
    base.update(extra)
    return SiteConfig(initial_config=base)


def _pool(row=ROW, titles=("Older Post A", "Older Post B")):
    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value=row)
    pool.fetch = AsyncMock(return_value=[{"title": t} for t in titles])
    return pool


@pytest.fixture
def chat(monkeypatch):
    import poindexter.services.llm_text as llm_text

    mock = AsyncMock(return_value='{"verdict": "reject", "reason": "Rubric 1: repeats the decode-speed post."}')
    monkeypatch.setattr(llm_text, "ollama_chat_text", mock)
    monkeypatch.setattr(llm_text, "resolve_local_writer_model", lambda **kw: "ollama/local-writer")
    return mock


@pytest.fixture
def audit(monkeypatch):
    from poindexter.services import audit_log

    rows: list = []

    class _Logger:
        def __init__(self, pool):
            pass

        async def log(self, event_type, source, details=None, task_id=None, severity="info"):
            rows.append((event_type, source, details, task_id))

    monkeypatch.setattr(audit_log, "AuditLogger", _Logger)
    return rows


class TestReviewDraft:
    @pytest.mark.asyncio
    async def test_a_verdict_is_recorded_and_returned(self, chat, audit):
        review = await tr.review_draft(pool=_pool(), site_config=_sc(), task_id="t-1")
        assert review == {"verdict": "reject", "reason": "Rubric 1: repeats the decode-speed post.",
                          "model": "anthropic/claude-sonnet-5-5"}
        assert audit == [("operator_taste_review", "services.taste_review",
                          {**review, "niche": "glad-labs"}, "t-1")]

    @pytest.mark.asyncio
    async def test_the_prompt_carries_the_rubric_date_recent_titles_and_whole_draft(self, chat, audit):
        await tr.review_draft(pool=_pool(), site_config=_sc(), task_id="t-1")
        prompt = chat.await_args.args[0]
        assert "1. Says something new." in prompt and "Today is 2026-10-17." in prompt
        assert "- Older Post A" in prompt and "Decode Speed on Two Cards" in prompt
        assert DRAFT.strip()[-60:] in prompt  # not cut short: a cut draft reads as truncated
        assert f"{len(DRAFT.split())} words, 20 images" in prompt
        assert chat.await_args.kwargs["model"] == "anthropic/claude-sonnet-5-5"
        assert chat.await_args.kwargs["phase"] == "qa_operator_taste"

    @pytest.mark.asyncio
    async def test_the_local_writer_is_the_default_model(self, chat, audit):
        await tr.review_draft(pool=_pool(), site_config=_sc(taste_review_model=""), task_id="t-1")
        assert chat.await_args.kwargs["model"] == "ollama/local-writer"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("extra", [{"taste_review_niches": ""}, {"taste_review_niches": "dev_diary"}])
    async def test_off_for_a_niche_that_is_not_listed(self, chat, audit, extra):
        assert await tr.review_draft(pool=_pool(), site_config=_sc(**extra), task_id="t-1") is None
        chat.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_listed_niche_without_a_rubric_says_so_and_skips(self, chat, audit, caplog):
        caplog.set_level(logging.WARNING, logger=tr.__name__)
        assert await tr.review_draft(pool=_pool(), site_config=_sc(taste_review_rubric=" "), task_id="t-1") is None
        chat.assert_not_awaited()
        assert "taste_review_rubric is empty" in caplog.text

    @pytest.mark.asyncio
    @pytest.mark.parametrize("reply", ["", "I think it is fine", '{"verdict": "maybe"}'])
    async def test_no_clear_verdict_is_no_review_and_nothing_recorded(self, chat, audit, reply):
        chat.return_value = reply
        assert await tr.review_draft(pool=_pool(), site_config=_sc(), task_id="t-1") is None
        assert audit == []

    @pytest.mark.asyncio
    async def test_a_failed_call_is_no_review(self, chat, audit):
        chat.side_effect = RuntimeError("provider down")
        assert await tr.review_draft(pool=_pool(), site_config=_sc(), task_id="t-1") is None
        assert audit == []

    @pytest.mark.asyncio
    async def test_a_missing_task_or_empty_draft_is_no_review(self, chat, audit):
        assert await tr.review_draft(pool=_pool(row=None), site_config=_sc(), task_id="t-1") is None
        assert await tr.review_draft(pool=_pool(row={**ROW, "content": " "}), site_config=_sc(), task_id="t-1") is None
        chat.assert_not_awaited()


def test_the_message_line():
    assert tr.note_for(None) == ""
    assert tr.note_for({"verdict": "approve", "reason": "New measurements."}) == "Taste check: approve: New measurements."
    assert tr.note_for({"verdict": "reject", "reason": ""}) == "Taste check: reject: (no reason given)"


@pytest.mark.asyncio
async def test_agreement_sorts_each_decided_review():
    rows = [
        {"task_id": "a1", "verdict": "approve", "reason": "", "status": "published", "topic": "A"},
        {"task_id": "b2", "verdict": "reject", "reason": "dull", "status": "rejected_final", "topic": "B"},
        {"task_id": "c3", "verdict": "approve", "reason": "", "status": "rejected_final", "topic": "C"},
        {"task_id": "d4", "verdict": "reject", "reason": "repeat", "status": "published", "topic": "D"},
        {"task_id": "e5", "verdict": "reject", "reason": "", "status": "awaiting_approval", "topic": "E"},
    ]
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=rows)
    out = await tr.agreement(pool, days=30)
    assert (out["both_approve"], out["both_reject"], out["pending"]) == (1, 1, 1)
    assert out["leaked"] == [{"task_id": "c3", "topic": "C"}]
    assert out["over_strict"] == [{"task_id": "d4", "topic": "D", "reason": "repeat"}]
    assert pool.fetch.await_args.args[1:] == ("operator_taste_review", 30)


def test_pack_prompt_renders():
    from poindexter.services.prompt_manager import get_prompt_manager

    text = get_prompt_manager().get_prompt(
        "qa.operator_taste", subject="s", rubric="r", today="2026-10-17", recent="- t",
        title="T", words=10, images=1, body="B",
    )
    assert '{"verdict": "approve" or "reject"' in text and "Today is 2026-10-17." in text


class TestApprovalMessage:
    async def _notify(self, review):
        from poindexter.services import post_pipeline_actions as ppa

        db = MagicMock()
        db.pool = MagicMock()
        with patch.object(tr, "review_draft", review), \
                patch.object(ppa, "_read_preview_token", AsyncMock(return_value=None)), \
                patch("poindexter.services.integrations.operator_notify.notify_operator",
                      new_callable=AsyncMock) as send:
            await ppa._notify_operator(database_service=db, site_config=_sc(), task_id="t-1234567890",
                                       topic="Decode speed", quality_score=88.0)
        return send.await_args.args[0]

    @pytest.mark.asyncio
    async def test_the_verdict_rides_in_the_message(self):
        msg = await self._notify(AsyncMock(return_value={"verdict": "reject", "reason": "Repeats a post."}))
        assert "Score: 88/100\nTaste check: reject: Repeats a post.\n" in msg

    @pytest.mark.asyncio
    async def test_a_broken_review_never_holds_the_message_back(self):
        msg = await self._notify(AsyncMock(side_effect=RuntimeError("boom")))
        assert msg.startswith('Awaiting approval: "Decode speed"') and "Taste check" not in msg


class TestScreenTopics:
    """The rubric one step earlier: ideas that can only become a repeat or a
    recap are dropped before a draft is written. Verdicts are cached per idea,
    because the open batch refreshes every 30 minutes."""

    IDEAS = [("a", "Best GPUs for Local LLMs in 2026", ""),
             ("b", "Homelab AI Server Build", "what a quiet 2-GPU box needs"),
             ("c", "Surface Laptop Ultra Review", "")]

    def _pool(self, cached=()):
        pool = MagicMock()

        async def fetch(sql, *args):
            if "audit_log" in sql:
                return [{"key": k, "verdict": v, "reason": r} for k, v, r in cached]
            return [{"title": "Best NVIDIA GPUs for Local LLMs in 2026, Ranked by Use Case"}]

        pool.fetch = AsyncMock(side_effect=fetch)
        return pool

    @pytest.mark.asyncio
    async def test_one_call_judges_every_new_idea_and_records_each(self, chat, audit):
        chat.return_value = ('{"topics": [{"id": "c0", "keep": false, "reason": "Rubric 3: repeats the GPU ranking."},'
                             ' {"id": "c1", "keep": true, "reason": "new"},'
                             ' {"id": "c2", "keep": false, "reason": "Rubric 7: a recap of outside reviews."}]}')
        drops = await tr.screen_topics(pool=self._pool(), site_config=_sc(), niche_slug="glad-labs",
                                       subject="AI and hardware", candidates=self.IDEAS, today="2026-10-18")
        assert drops == {"a": "Rubric 3: repeats the GPU ranking.", "c": "Rubric 7: a recap of outside reviews."}
        prompt = chat.await_args.args[0]
        assert "[c1] Homelab AI Server Build — what a quiet 2-GPU box needs" in prompt
        assert "- Best NVIDIA GPUs for Local LLMs in 2026, Ranked by Use Case" in prompt
        assert chat.await_args.kwargs["phase"] == "topic_taste_screen"
        assert [(e, d["key"], d["verdict"]) for e, _, d, _ in audit] == [
            ("topic_taste_screen", "best gpus for local llms in 2026", "drop"),
            ("topic_taste_screen", "homelab ai server build", "keep"),
            ("topic_taste_screen", "surface laptop ultra review", "drop"),
        ]

    @pytest.mark.asyncio
    async def test_a_cached_verdict_is_reused_without_a_call(self, chat, audit):
        cached = [("best gpus for local llms in 2026", "drop", "repeat"),
                  ("homelab ai server build", "keep", ""), ("surface laptop ultra review", "keep", "")]
        drops = await tr.screen_topics(pool=self._pool(cached), site_config=_sc(), niche_slug="glad-labs",
                                       subject="s", candidates=self.IDEAS)
        assert drops == {"a": "repeat"}
        chat.assert_not_awaited()
        assert audit == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize("extra", [{"taste_review_niches": "dev_diary"}, {"taste_review_rubric": ""},
                                       {"taste_review_screen_topics": "false"}])
    async def test_off_unless_listed_with_a_rubric_and_switched_on(self, chat, audit, extra):
        assert await tr.screen_topics(pool=self._pool(), site_config=_sc(**extra), niche_slug="glad-labs",
                                      subject="s", candidates=self.IDEAS) == {}
        chat.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("reply", ["no json here", '{"topics": [{"id": "c9", "keep": false}, {"id": "c0"}]}'])
    async def test_an_unusable_reply_drops_nothing(self, chat, audit, reply):
        chat.return_value = reply
        assert await tr.screen_topics(pool=self._pool(), site_config=_sc(), niche_slug="glad-labs",
                                      subject="s", candidates=self.IDEAS) == {}
        assert audit == []

    @pytest.mark.asyncio
    async def test_a_failed_call_keeps_every_new_idea(self, chat, audit):
        chat.side_effect = RuntimeError("provider down")
        cached = [("surface laptop ultra review", "drop", "recap")]
        drops = await tr.screen_topics(pool=self._pool(cached), site_config=_sc(), niche_slug="glad-labs",
                                       subject="s", candidates=self.IDEAS)
        assert drops == {"c": "recap"}


def test_screen_pack_prompt_renders():
    from poindexter.services.prompt_manager import get_prompt_manager

    text = get_prompt_manager().get_prompt(
        "topic.taste_screen", subject="s", rubric="r", today="2026-10-18", recent="- t", ideas="[c0] Idea",
    )
    assert '"keep": true' in text and "[c0] Idea" in text and "Today is 2026-10-18." in text


class TestSweepScreen:
    """In the sweep, a dropped idea is replaced from deeper in its pool."""

    def _service(self, **extra):
        from poindexter.services.topic_batch_service import TopicBatchService

        svc = TopicBatchService.__new__(TopicBatchService)
        svc._pool = MagicMock()
        svc._site_config = _sc(**extra)
        return svc

    @staticmethod
    def _cands(prefix, n):
        from poindexter.services.topic_ranking import ScoredCandidate

        return [ScoredCandidate(id=f"{prefix}{i}", title=f"{prefix} idea {i}", summary=None,
                                embedding_score=1.0 - i / 100) for i in range(n)]

    @pytest.mark.asyncio
    async def test_drops_are_backfilled_from_the_next_ideas(self, monkeypatch):
        ext, intl = self._cands("e", 6), self._cands("i", 6)
        calls = []

        async def screen(*, candidates, **kw):
            calls.append([c[0] for c in candidates])
            return {"e0": "repeat", "e1": "recap", "i2": "thin"} if len(calls) == 1 else {}

        monkeypatch.setattr(tr, "screen_topics", screen)
        niche = MagicMock(slug="glad-labs", topic_subject="s")
        kept = await self._service()._screen_by_taste(niche, ext, intl, 3)
        assert [c.id for c in kept] == ["e2", "e3", "e4", "i0", "i1", "i3"]
        assert calls == [["e0", "e1", "e2", "i0", "i1", "i2"], ["e3", "e4", "e5", "i3", "i4", "i5"]]

    @pytest.mark.asyncio
    async def test_a_raising_screen_keeps_the_plain_slice(self, monkeypatch):
        monkeypatch.setattr(tr, "screen_topics", AsyncMock(side_effect=RuntimeError("boom")))
        ext, intl = self._cands("e", 6), self._cands("i", 6)
        kept = await self._service()._screen_by_taste(MagicMock(slug="glad-labs"), ext, intl, 3)
        assert [c.id for c in kept] == ["e0", "e1", "e2", "i0", "i1", "i2"]
