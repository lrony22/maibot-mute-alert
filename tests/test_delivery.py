"""后台投递、有限重试、顺序及生命周期；所有 RPC 均在内存中模拟。"""

import asyncio
import unittest
from collections import Counter
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from maibot_sdk.config import rebuild_plugin_config_data

from tests import MuteAlertConfig, MuteEvent, MuteNotifier, parse_mute_event


class TestDelivery(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        async def group(gid):
            return {"session_id": "g" + gid}

        async def user(uid):
            return {"session_id": "u" + uid}

        self.ctx = SimpleNamespace(
            chat=SimpleNamespace(get_stream_by_group_id=AsyncMock(side_effect=group),
                                 get_stream_by_user_id=AsyncMock(side_effect=user)),
            send=SimpleNamespace(text=AsyncMock(return_value=True)),
            maisaka=SimpleNamespace(proactive=SimpleNamespace(trigger=AsyncMock(
                return_value={"success": True, "queued": True}))),
        )
        self.config = MuteAlertConfig(target_groups=[1001], target_users=[2001])
        self.notifier = MuteNotifier(self.config, self.ctx)
        self.event = MuteEvent(8888, 7777, 6666, 300, True, False)

    async def asyncTearDown(self):
        await self.notifier.close()

    async def until(self, predicate):
        async def spin():
            while not predicate():
                await asyncio.sleep(0)
        await asyncio.wait_for(spin(), 1)

    async def test_retries_only_failed_target_and_reuses_pending_job(self):
        attempts = Counter()

        async def send(text, stream):
            attempts[stream] += 1
            return stream == "u2001" or attempts[stream] == 3

        self.ctx.send.text.side_effect = send
        with patch("mute_alert.notifier.RETRY_DELAYS", (0, 0)):
            tasks = self.notifier.submit(self.event)
            self.assertEqual(self.notifier.submit(self.event), [])
            await asyncio.gather(*tasks)
        self.assertEqual(attempts, {"g1001": 3, "u2001": 1})
        self.assertEqual(await self.notifier.dispatch_alert(self.event), 0)

    async def test_explicit_failure_exhausts_retry_budget(self):
        self.ctx.send.text.return_value = False
        with patch("mute_alert.notifier.RETRY_DELAYS", (0, 0)):
            self.assertEqual(await self.notifier.dispatch_alert(self.event), 0)
        self.assertEqual(self.ctx.send.text.await_count, 6)
        self.assertFalse(self.notifier.is_in_cooldown(8888))

    async def test_slow_target_does_not_delay_other_targets(self):
        blocked, fast, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def send(text, stream):
            if stream == "g1001":
                blocked.set()
                await release.wait()
            else:
                fast.set()
            return True

        self.ctx.send.text.side_effect = send
        self.notifier.submit(self.event)
        await asyncio.wait_for(fast.wait(), 1)
        self.assertTrue(blocked.is_set())
        release.set()
        await self.notifier.wait_idle()

    async def test_global_concurrency_is_four(self):
        self.config.target_groups = list(range(1001, 1007))
        self.config.target_users = []
        active, peak = 0, 0
        four, release = asyncio.Event(), asyncio.Event()

        async def send(text, stream):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            if active == 4:
                four.set()
            await release.wait()
            active -= 1
            return True

        self.ctx.send.text.side_effect = send
        self.notifier.submit(self.event)
        await asyncio.wait_for(four.wait(), 1)
        self.assertEqual(self.ctx.send.text.await_count, 4)
        release.set()
        await self.notifier.wait_idle()
        self.assertEqual(peak, 4)
        self.assertEqual(self.ctx.send.text.await_count, 6)

    async def test_text_timeout_is_unknown_and_not_retried(self):
        self.config.delivery_timeout_seconds = 1

        async def send(*args):
            await asyncio.Event().wait()

        self.ctx.send.text.side_effect = send
        self.assertEqual(await self.notifier.dispatch_alert(self.event), 0)
        self.assertEqual(self.ctx.send.text.await_count, 2)
        self.assertTrue(self.notifier.is_in_cooldown(8888))
        self.assertEqual(await self.notifier.dispatch_alert(self.event), 0)

    async def test_ai_timeout_has_no_fallback_or_resubmission(self):
        self.config.target_groups = []
        self.config.ai_notify_admins = True
        self.config.delivery_timeout_seconds = 1

        async def trigger(*args, **kwargs):
            await asyncio.Event().wait()

        self.ctx.maisaka.proactive.trigger.side_effect = trigger
        self.assertEqual(await self.notifier.dispatch_alert(self.event), 0)
        self.assertEqual(await self.notifier.dispatch_alert(self.event), 0)
        self.ctx.maisaka.proactive.trigger.assert_awaited_once()
        self.ctx.send.text.assert_not_awaited()

    async def test_unmute_cancels_old_retry_even_when_unmute_notification_disabled(self):
        self.ctx.send.text.return_value = False
        self.config.alert_on_unmute = False
        self.notifier.submit(self.event)
        await self.until(lambda: self.ctx.send.text.await_count == 2)
        unmute = MuteEvent(8888, 7777, 6666, 0, False, False)
        self.assertEqual(self.notifier.submit(unmute), [])
        await self.notifier.wait_idle()
        self.assertEqual(self.ctx.send.text.await_count, 2)
        self.ctx.send.text.return_value = True
        self.assertEqual(await self.notifier.dispatch_alert(self.event), 2)

    async def test_inflight_mute_finishes_before_unmute_to_same_target(self):
        self.config.target_users = []
        started, release = asyncio.Event(), asyncio.Event()
        order = []

        async def send(text, stream):
            unmute = "解除禁言通知" in text
            order.append("unmute" if unmute else "ban-start")
            if not unmute:
                started.set()
                await release.wait()
                order.append("ban-end")
            return True

        self.ctx.send.text.side_effect = send
        self.notifier.submit(self.event)
        await started.wait()
        self.notifier.submit(MuteEvent(8888, 7777, 6666, 0, False, False))
        await asyncio.sleep(0)
        self.assertEqual(order, ["ban-start"])
        release.set()
        await self.notifier.wait_idle()
        self.assertEqual(order, ["ban-start", "ban-end", "unmute"])

    async def test_hot_reload_removes_targets_and_cancels_retries(self):
        self.ctx.send.text.return_value = False
        self.notifier.submit(self.event)
        await self.until(lambda: self.ctx.send.text.await_count == 2)
        config = self.config.model_copy(update={"target_groups": [], "target_users": []})
        self.notifier.update_config(config)
        await self.notifier.wait_idle()
        self.assertEqual(self.ctx.send.text.await_count, 2)

    async def test_disabling_during_ai_submission_prevents_text_fallback(self):
        self.config.target_groups = []
        self.config.ai_notify_admins = True
        started, release = asyncio.Event(), asyncio.Event()

        async def trigger(*args, **kwargs):
            started.set()
            await release.wait()
            return {"success": False}

        self.ctx.maisaka.proactive.trigger.side_effect = trigger
        self.notifier.submit(self.event)
        await started.wait()
        self.notifier.update_config(self.config.model_copy(update={"enabled": False}))
        release.set()
        await self.notifier.wait_idle()
        self.ctx.send.text.assert_not_awaited()

    async def test_close_cleans_inflight_jobs(self):
        async def send(*args):
            await asyncio.Event().wait()

        self.ctx.send.text.side_effect = send
        self.notifier.submit(self.event)
        await self.until(lambda: self.ctx.send.text.await_count == 2)
        await self.notifier.close()
        self.assertFalse(self.notifier._tasks)
        self.assertEqual(self.notifier.submit(self.event), [])

    async def test_targets_are_deduplicated_and_invalid_ids_skipped(self):
        self.config.target_groups = [1001, 1001, 0, -5]
        self.config.target_users = [2001, 2001, 0]
        self.assertEqual(await self.notifier.dispatch_alert(self.event), 2)
        self.assertEqual(self.ctx.send.text.await_count, 2)

    def test_notification_timestamp_is_shared_by_text_and_ai(self):
        event = parse_mute_event(dict(notice_type="group_ban", sub_type="ban",
                                     group_id=8888, user_id=0, time=1700000000))
        self.assertIn("2023-11-15 06:13:20", self.notifier.render_message(event))
        self.assertIn("2023-11-15T06:13:20+08:00", self.notifier.render_ai_intent(event))

    def test_sdk_config_upgrade_preserves_existing_settings(self):
        old = {"plugin": dict(config_version="1.1.0", target_users=[2001],
                              mute_template="custom", ai_notify_admins=True)}
        default = {"plugin": MuteAlertConfig().model_dump()}
        merged = rebuild_plugin_config_data(default, old)["plugin"]
        settings = MuteAlertConfig.model_validate(merged)
        self.assertEqual(settings.config_version, "1.2.0")
        self.assertEqual(settings.target_users, [2001])
        self.assertEqual(settings.mute_template, "custom")
        self.assertTrue(settings.ai_notify_admins)
        self.assertEqual(settings.retry_attempts, 2)
