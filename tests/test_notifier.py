"""真实会话结构、发送失败和冷却行为回归，不向外发送消息。"""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from tests import MuteAlertConfig, MuteEvent, MuteNotifier, format_duration
from mute_alert.notifier import DeliveryResult


class TestNotifier(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.ctx = SimpleNamespace(
            chat=SimpleNamespace(
                get_stream_by_group_id=AsyncMock(return_value={"session_id": "group-session"}),
                get_stream_by_user_id=AsyncMock(return_value={"session_id": "private-session"}),
                open_session=AsyncMock(return_value={"success": True, "stream_id": "new-session"}),
            ),
            send=SimpleNamespace(text=AsyncMock(return_value=True)),
            maisaka=SimpleNamespace(proactive=SimpleNamespace(trigger=AsyncMock(
                return_value={"success": True, "queued": True, "task_id": "test-ai-task"}))),
        )
        self.notifier = MuteNotifier(MuteAlertConfig(target_groups=[1001], target_users=[2001], retry_attempts=0), self.ctx)
        self.event = MuteEvent(8888, 7777, 6666, 300, True, False)

    async def asyncTearDown(self):
        await self.notifier.close()

    def test_format_duration(self):
        for seconds, text in [(0, "0秒"), (30, "30秒"), (60, "1分钟"), (1800, "30分钟"),
                              (3600, "1小时"), (3660, "1小时1分钟"), (86400, "1天"), (2592000, "30天")]:
            self.assertEqual(format_duration(seconds), text)

    def test_render_and_cooldown(self):
        self.assertIn("5分钟", self.notifier.render_message(self.event))
        self.assertFalse(self.notifier.is_in_cooldown(8888))
        self.notifier.update_cooldown(8888)
        self.assertTrue(self.notifier.is_in_cooldown(8888))
        self.assertFalse(self.notifier.is_in_cooldown(8888, is_ban=False))

    def test_default_whole_group_templates_describe_group_state(self):
        for is_ban, action in [(True, "开启"), (False, "关闭")]:
            with self.subTest(is_ban=is_ban):
                text = self.notifier.render_message(MuteEvent(8888, 7777, 0, 0, is_ban, True))
                self.assertIn("全员禁言" + action, text)
                self.assertIn("8888", text)
                self.assertIn("个人禁言状态", text)
                self.assertNotIn("0秒", text)
                self.assertNotIn("机器人已恢复正常发言能力", text)
                if is_ban:
                    self.assertIn("未提供", text)

    def test_whole_group_custom_template_and_positive_duration(self):
        event = MuteEvent(8888, 7777, 0, 600, True, True)
        self.assertIn("10分钟", self.notifier.render_message(event))
        self.notifier.config.mute_template = "自定义：{group_id}/{user_id}/{duration_str}/{is_all}"
        self.assertEqual(self.notifier.render_message(event), "自定义：8888/全体成员/10分钟/是")

    def test_invalid_whole_group_template_has_safe_fallback(self):
        for is_ban in (True, False):
            self.notifier.config.mute_template = "{missing}"
            self.notifier.config.unmute_template = "{missing}"
            text = self.notifier.render_message(MuteEvent(8888, 7777, 0, 0, is_ban, True))
            self.assertIn("全员禁言" + ("开启" if is_ban else "关闭"), text)
            self.assertIn("个人禁言状态", text)

    def test_ai_whole_group_unmute_does_not_wait_for_unmute(self):
        text = self.notifier.render_ai_intent(MuteEvent(8888, 7777, 0, 0, False, True))
        self.assertIn("解除禁言", text)
        self.assertNotIn("等待管理员解除", text)

    async def test_dispatch_exact_sessions_and_duplicate(self):
        self.assertEqual(await self.notifier.dispatch_alert(self.event), 2)
        self.ctx.chat.get_stream_by_group_id.assert_awaited_once_with("1001")
        self.ctx.chat.get_stream_by_user_id.assert_awaited_once_with("2001")
        self.assertEqual([c.args[1] for c in self.ctx.send.text.await_args_list],
                         ["group-session", "private-session"])
        self.assertEqual(await self.notifier.dispatch_alert(self.event), 0)

    async def test_failed_send_does_not_count_or_consume_cooldown(self):
        self.ctx.send.text.return_value = False
        self.assertEqual(await self.notifier.dispatch_alert(self.event), 0)
        self.assertFalse(self.notifier.is_in_cooldown(self.event.group_id))
        self.ctx.send.text.return_value = True
        self.assertEqual(await self.notifier.dispatch_alert(self.event), 2)

    async def test_partial_failure_retries_only_failed_target(self):
        for outcomes, failed_stream in [([True, False], "private-session"),
                                        ([False, True], "group-session")]:
            with self.subTest(outcomes=outcomes):
                notifier = MuteNotifier(self.notifier.config, self.ctx)
                self.ctx.send.text.reset_mock()
                self.ctx.send.text.side_effect = outcomes
                self.assertEqual(await notifier.dispatch_alert(self.event), 1)
                self.ctx.send.text.side_effect = None
                self.assertEqual(await notifier.dispatch_alert(self.event), 1)
                self.assertEqual(self.ctx.send.text.await_count, 3)
                self.assertEqual(self.ctx.send.text.await_args.args[1], failed_stream)
                self.assertEqual(await notifier.dispatch_alert(self.event), 0)

    async def test_retried_target_has_its_own_cooldown_deadline(self):
        with patch("mute_alert.notifier.monotonic", return_value=100) as now:
            self.ctx.send.text.side_effect = [True, False]
            self.assertEqual(await self.notifier.dispatch_alert(self.event), 1)
            now.return_value = 105
            self.ctx.send.text.side_effect = None
            self.assertEqual(await self.notifier.dispatch_alert(self.event), 1)
            now.return_value = 111
            self.assertEqual(await self.notifier.dispatch_alert(self.event), 1)
            self.assertEqual(self.ctx.send.text.await_args.args[1], "group-session")
            self.assertEqual(self.ctx.send.text.await_count, 4)

    async def test_failed_unmute_still_resets_previous_mute_cooldown(self):
        await self.notifier.dispatch_alert(self.event)
        self.ctx.send.text.return_value = False
        self.assertEqual(await self.notifier.dispatch_alert(MuteEvent(8888, 7777, 6666, 0, False, False)), 0)
        self.ctx.send.text.return_value = True
        self.assertEqual(await self.notifier.dispatch_alert(self.event), 2)

    async def test_filtered_unmute_still_allows_new_mute(self):
        await self.notifier.dispatch_alert(self.event)
        self.notifier.config.alert_on_unmute = False
        self.assertEqual(await self.notifier.dispatch_alert(MuteEvent(8888, 7777, 6666, 0, False, False)), 0)
        self.assertEqual(await self.notifier.dispatch_alert(self.event), 2)

    async def test_failed_session_lookup_retries_without_resending_admin(self):
        self.ctx.chat.get_stream_by_group_id.return_value = None
        self.ctx.chat.open_session.return_value = {"success": False}
        self.assertEqual(await self.notifier.dispatch_alert(self.event), 1)
        self.ctx.chat.open_session.return_value = {"success": True, "stream_id": "new-group"}
        self.assertEqual(await self.notifier.dispatch_alert(self.event), 1)
        self.assertEqual(self.ctx.send.text.await_count, 2)
        self.assertEqual(self.ctx.send.text.await_args.args[1], "new-group")

    async def test_concurrent_partial_failure_retries_without_duplicate_success(self):
        self.ctx.send.text.side_effect = [True, False, True]
        self.assertEqual(await asyncio.gather(self.notifier.dispatch_alert(self.event),
                                              self.notifier.dispatch_alert(self.event)), [1, 0])
        self.ctx.send.text.side_effect = None
        self.assertEqual(await self.notifier.dispatch_alert(self.event), 1)
        self.assertEqual(self.ctx.send.text.await_count, 3)

    async def test_ai_queued_target_is_not_queued_again_when_group_retries(self):
        self.notifier.config.ai_notify_admins = True
        self.ctx.send.text.return_value = False
        self.assertEqual(await self.notifier.dispatch_alert(self.event), 1)
        self.ctx.send.text.return_value = True
        self.assertEqual(await self.notifier.dispatch_alert(self.event), 1)
        self.ctx.maisaka.proactive.trigger.assert_awaited_once()

    async def test_ai_failed_submission_can_retry_without_resending_group(self):
        self.notifier.config.ai_notify_admins = True
        self.notifier.config.ai_fallback_to_text = False
        self.ctx.maisaka.proactive.trigger.side_effect = [
            {"success": False}, {"success": True, "queued": True}]
        self.assertEqual(await self.notifier.dispatch_alert(self.event), 1)
        self.assertEqual(await self.notifier.dispatch_alert(self.event), 1)
        self.ctx.send.text.assert_awaited_once()

    async def test_new_target_is_not_suppressed_by_existing_target_cooldown(self):
        await self.notifier.dispatch_alert(self.event)
        self.notifier.config.target_users = [2001, 2002, 2002]
        self.assertEqual(await self.notifier.dispatch_alert(self.event), 1)
        self.assertEqual(self.ctx.send.text.await_count, 3)

    async def test_unmute_after_mute_is_sent(self):
        await self.notifier.dispatch_alert(self.event)
        unmute = MuteEvent(8888, 7777, 6666, 0, False, False)
        self.assertEqual(await self.notifier.dispatch_alert(unmute), 2)
        self.assertEqual(await self.notifier.dispatch_alert(self.event), 2)

    async def test_new_session_and_failed_creation(self):
        self.ctx.chat.get_stream_by_group_id.return_value = None
        self.assertEqual(await self.notifier._resolve_stream_id(1001, True), "new-session")
        self.ctx.chat.open_session.assert_awaited_once_with("qq", "group", group_id="1001")
        self.ctx.chat.open_session.return_value = {"success": False}
        self.assertIsNone(await self.notifier._resolve_stream_id(1001, True))

    async def test_concurrent_duplicates_are_sent_once(self):
        async def send(*args):
            await asyncio.sleep(0)
            return True
        self.ctx.send.text.side_effect = send
        self.assertEqual(await asyncio.gather(self.notifier.dispatch_alert(self.event),
                                              self.notifier.dispatch_alert(self.event)), [2, 0])

    async def test_options_and_empty_targets(self):
        self.notifier.config.alert_on_all_mute = False
        self.assertEqual(await self.notifier.dispatch_alert(MuteEvent(8888, 7777, 0, 0, True, True)), 0)
        self.notifier.config.alert_on_unmute = False
        self.assertEqual(await self.notifier.dispatch_alert(MuteEvent(8888, 7777, 6666, 0, False, False)), 0)
        self.notifier.config.target_groups = []
        self.notifier.config.target_users = []
        self.assertEqual(await self.notifier.dispatch_alert(self.event), 0)
        self.ctx.send.text.assert_not_awaited()

    async def test_ai_task_replaces_only_admin_fixed_alert(self):
        self.notifier.config.ai_notify_admins = True
        self.assertEqual(await self.notifier.dispatch_alert(self.event), 2)
        self.ctx.send.text.assert_awaited_once()
        self.assertEqual(self.ctx.send.text.await_args.args[1], "group-session")
        trigger = self.ctx.maisaka.proactive.trigger
        trigger.assert_awaited_once()
        stream_id, intent = trigger.await_args.args
        self.assertEqual(stream_id, "private-session")
        for text in ["8888", "7777", "5分钟", "你本人", "非管理员发言", "+08:00"]:
            self.assertIn(text, intent)
        self.assertEqual(trigger.await_args.kwargs["reason"], "mute_alert")
        self.assertEqual(await self.notifier.dispatch_alert(self.event), 0)
        self.assertEqual(trigger.await_count, 1)

    async def test_ai_rejected_falls_back_to_text(self):
        self.notifier.config.ai_notify_admins = True
        self.ctx.maisaka.proactive.trigger.return_value = {"success": False, "error": "policy denied"}
        self.assertEqual(await self.notifier._notify_admin(self.event, "fallback", "private-session"),
                         DeliveryResult.TEXT_SENT)
        self.ctx.send.text.assert_awaited_once_with("fallback", "private-session")

    async def test_ai_exception_is_unknown_without_fallback(self):
        self.notifier.config.ai_notify_admins = True
        self.ctx.maisaka.proactive.trigger.side_effect = RuntimeError("RPC failed")
        self.assertEqual(await self.notifier._notify_admin(self.event, "fallback", "private-session"),
                         DeliveryResult.UNKNOWN)
        self.ctx.send.text.assert_not_awaited()

    async def test_ai_not_queued_and_fallback_disabled_allows_retry(self):
        self.notifier.config.ai_notify_admins = True
        self.notifier.config.ai_fallback_to_text = False
        self.notifier.config.target_groups = []
        self.ctx.maisaka.proactive.trigger.return_value = {"success": True, "queued": False}
        self.assertEqual(await self.notifier.dispatch_alert(self.event), 0)
        self.ctx.send.text.assert_not_awaited()
        self.assertFalse(self.notifier.is_in_cooldown(self.event.group_id))

    def test_ai_intent_whole_group_and_unmute_facts(self):
        text = self.notifier.render_ai_intent(MuteEvent(8888, 0, 0, 0, True, True))
        self.assertIn("未确认你本人无法发言", text)
        self.assertIn("等待管理员解除", text)
        text = self.notifier.render_ai_intent(MuteEvent(8888, 0, 6666, 0, False, False))
        self.assertIn("解除禁言", text)
