"""真实 SDK 的工厂、配置注入、Hook 和完整通知链路回归。"""

import unittest

from maibot_sdk.context import PluginContext

from tests import MuteAlertPlugin, create_plugin


class TestPlugin(unittest.IsolatedAsyncioTestCase):
    def apply_settings(self, settings):
        config = self.plugin.get_default_config()
        config["plugin"].update(settings)
        self.plugin.set_plugin_config(config)
        return config

    async def asyncSetUp(self):
        self.calls = []

        async def rpc(method, plugin_id="", payload=None, **kwargs):
            self.calls.append(payload)
            cap = payload["capability"]
            if cap.startswith("chat.get_stream_by_"):
                return {"success": True, "stream": {"session_id": "real-session-id"}}
            if cap == "send.text":
                return {"success": True}
            if cap == "maisaka.proactive.trigger":
                return {"success": True, "queued": True, "task_id": "memory-only-task"}
            raise AssertionError(cap)

        self.plugin = create_plugin()
        self.plugin._set_context(PluginContext("maibot-mute-alert", rpc_call=rpc))
        self.apply_settings({"bot_qq": 111111, "target_users": [999999]})
        await self.plugin.on_load()

    async def asyncTearDown(self):
        await self.plugin.on_unload()

    async def test_factory_schema_and_hook(self):
        self.assertIsInstance(self.plugin, MuteAlertPlugin)
        metadata = self.plugin.get_components()[0]["metadata"]
        self.assertEqual(metadata["hook"], "chat.receive.before_process")
        self.assertEqual(metadata["mode"], "observe")
        fields = self.plugin.get_webui_config_schema()["sections"]["plugin"]["fields"]
        self.assertIn("target_users", fields)
        self.assertEqual(self.plugin.get_default_config()["plugin"]["config_version"], "1.2.0")

    async def test_hook_notice_and_hot_reload(self):
        raw = dict(post_type="notice", notice_type="group_ban", sub_type="ban",
                   group_id=123456, operator_id=555555, user_id=111111, duration=1800)
        await self.plugin.handle_mute_event(message={"message_info": {
            "additional_config": {"napcat_notice_payload": raw}}})
        self.assertEqual(self.calls, [])
        await self.plugin.notifier.wait_idle()
        sends = [c for c in self.calls if c["capability"] == "send.text"]
        self.assertEqual(len(sends), 1)
        self.assertEqual(sends[0]["args"]["stream_id"], "real-session-id")
        self.assertIn("30分钟", sends[0]["args"]["text"])
        updated = self.apply_settings({"bot_qq": 111111, "target_groups": [888888]})
        await self.plugin.on_config_update("self", updated, "1.0.1")
        self.assertEqual(self.plugin.notifier.config.target_groups, [888888])
        await self.plugin.on_unload()

    async def test_unrelated_message_and_disabled_plugin(self):
        await self.plugin.handle_mute_event(message={"processed_plain_text": "hello"})
        updated = self.apply_settings({"enabled": False, "target_users": [999999]})
        await self.plugin.on_config_update("self", updated, "1.0.1")
        await self.plugin.handle_mute_event(raw=dict(notice_type="group_ban", sub_type="ban",
                                                   group_id=123, user_id=0, duration=0))
        self.assertEqual(self.calls, [])

    async def test_ai_notice_through_real_sdk_rpc(self):
        self.apply_settings({"bot_qq": 111111, "target_users": [999999], "ai_notify_admins": True})
        await self.plugin.on_config_update("self", self.plugin.get_plugin_config_data(), "1.1.0")
        await self.plugin.handle_mute_event(raw=dict(notice_type="group_ban", sub_type="ban",
                                                   group_id=123456, user_id=111111, duration=600))
        await self.plugin.notifier.wait_idle()
        tasks = [c for c in self.calls if c["capability"] == "maisaka.proactive.trigger"]
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["args"]["stream_id"], "real-session-id")
        self.assertIn("123456", tasks[0]["args"]["intent"])
        self.assertFalse(any(c["capability"] == "send.text" for c in self.calls))
