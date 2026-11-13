"""测试 detector 模块的禁言判定逻辑"""

import unittest
from tests import parse_mute_event, MuteEvent


class TestDetector(unittest.TestCase):

    def setUp(self):
        self.bot_qq = 123456789
        self.other_qq = 987654321

    def test_bot_banned_with_configured_qq(self):
        """测试配置了 bot_qq 时，机器人本人被禁言"""
        payload = {
            "raw": {
                "post_type": "notice",
                "notice_type": "group_ban",
                "sub_type": "ban",
                "group_id": 11223344,
                "operator_id": 888888,
                "user_id": self.bot_qq,
                "duration": 600,
            }
        }
        event = parse_mute_event(payload, configured_bot_qq=self.bot_qq)
        self.assertIsNotNone(event)
        self.assertIsInstance(event, MuteEvent)
        self.assertEqual(event.group_id, 11223344)
        self.assertEqual(event.operator_id, 888888)
        self.assertEqual(event.user_id, self.bot_qq)
        self.assertEqual(event.duration, 600)
        self.assertTrue(event.is_ban)
        self.assertFalse(event.is_all)

    def test_other_member_banned_ignored(self):
        """测试其他群成员被禁言时，应被忽略"""
        payload = {
            "raw": {
                "post_type": "notice",
                "notice_type": "group_ban",
                "sub_type": "ban",
                "group_id": 11223344,
                "operator_id": 888888,
                "user_id": self.other_qq,
                "duration": 600,
            }
        }
        event = parse_mute_event(payload, configured_bot_qq=self.bot_qq)
        self.assertIsNone(event)

    def test_auto_detect_self_id(self):
        """测试未手动指定 bot_qq 时，自动通过 self_id 探测本机器人"""
        payload = {
            "raw": {
                "post_type": "notice",
                "notice_type": "group_ban",
                "sub_type": "ban",
                "group_id": 556677,
                "operator_id": 888888,
                "user_id": self.bot_qq,
                "self_id": self.bot_qq,
                "duration": 1800,
            }
        }
        # configured_bot_qq 留空
        event = parse_mute_event(payload, configured_bot_qq=None)
        self.assertIsNotNone(event)
        self.assertEqual(event.user_id, self.bot_qq)
        self.assertEqual(event.duration, 1800)

    def test_all_mute(self):
        """测试全员禁言事件 (user_id == 0)"""
        payload = {
            "raw": {
                "post_type": "notice",
                "notice_type": "group_ban",
                "sub_type": "ban",
                "group_id": 11223344,
                "operator_id": 888888,
                "user_id": 0,
                "duration": 0,
            }
        }
        event = parse_mute_event(payload, configured_bot_qq=self.bot_qq)
        self.assertIsNotNone(event)
        self.assertTrue(event.is_all)
        self.assertTrue(event.is_ban)

    def test_host_wrapped_notice(self):
        raw = dict(post_type="notice", notice_type="group_ban", sub_type="ban",
                   group_id=123, user_id=self.bot_qq, self_id=self.bot_qq, duration=60)
        event = parse_mute_event({"message": {"message_info": {
            "additional_config": {"napcat_notice_payload": raw}}}})
        self.assertIsNotNone(event)
        self.assertTrue(event.is_ban)

    def test_missing_identity_ignored(self):
        raw = dict(notice_type="group_ban", sub_type="ban", group_id=123,
                   user_id=self.other_qq, duration=60)
        self.assertIsNone(parse_mute_event(raw))

    def test_missing_target_is_not_whole_group(self):
        raw = dict(notice_type="group_ban", sub_type="ban", group_id=123, duration=60)
        self.assertIsNone(parse_mute_event(raw, self.bot_qq))

    def test_whole_lift_without_duration_or_self_id(self):
        raw = dict(notice_type="group_ban", sub_type="whole_lift_ban", group_id=123, user_id=0)
        event = parse_mute_event(raw)
        self.assertTrue(event.is_all)
        self.assertFalse(event.is_ban)

    def test_unknown_subtype_ignored(self):
        raw = dict(notice_type="group_ban", sub_type="unknown", group_id=123, user_id=0)
        self.assertIsNone(parse_mute_event(raw))

    def test_lift_ban(self):
        """测试解除禁言事件"""
        payload = {
            "raw": {
                "post_type": "notice",
                "notice_type": "group_ban",
                "sub_type": "lift_ban",
                "group_id": 11223344,
                "operator_id": 888888,
                "user_id": self.bot_qq,
                "duration": 0,
            }
        }
        event = parse_mute_event(payload, configured_bot_qq=self.bot_qq)
        self.assertIsNotNone(event)
        self.assertFalse(event.is_ban)

    def test_non_notice_event(self):
        """测试普通聊天消息或无关事件，不应触发解析"""
        payload = {
            "raw": {
                "post_type": "message",
                "message_type": "group",
                "group_id": 11223344,
                "user_id": self.bot_qq,
                "raw_message": "大家好",
            }
        }
        event = parse_mute_event(payload, configured_bot_qq=self.bot_qq)
        self.assertIsNone(event)


if __name__ == "__main__":
    unittest.main()
