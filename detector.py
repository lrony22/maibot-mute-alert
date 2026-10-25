"""禁言通知检测与解析模块

负责从适配器或事件总线传递的原始数据中准确提取群禁言、解除禁言及全员禁言事件。
"""

import time
from typing import Dict, Any, Optional


class MuteEvent:
    """标准化的禁言/解禁事件对象"""

    def __init__(
        self,
        group_id: int,
        operator_id: int,
        user_id: int,
        duration: int,
        is_ban: bool,
        is_all: bool,
    ):
        self.group_id = group_id
        self.operator_id = operator_id
        self.user_id = user_id
        self.duration = duration
        self.is_ban = is_ban
        self.is_all = is_all
        self.timestamp = time.time()

    def __repr__(self) -> str:
        action = "禁言" if self.is_ban else "解禁"
        scope = "全员" if self.is_all else f"用户 {self.user_id}"
        return (
            f"<MuteEvent group={self.group_id} operator={self.operator_id} "
            f"target={scope} action={action} duration={self.duration}s>"
        )


def _safe_int(val: Any, default: int = 0) -> int:
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def parse_mute_event(
    event_payload: Dict[str, Any],
    configured_bot_qq: Optional[int] = None,
) -> Optional[MuteEvent]:
    """解析事件载荷，判断是否为当前机器人的禁言/解禁事件

    Args:
        event_payload: 来自 EventHandler 的 kwargs 或原始字典
        configured_bot_qq: 用户配置的机器人QQ号（可选，留空则从事件中自动探测 self_id）

    Returns:
        如果命中当前机器人的禁言/解禁事件，返回 MuteEvent，否则返回 None
    """
    if not isinstance(event_payload, dict):
        return None

    # 1. Host 的入站 Hook 通过 message_info.additional_config 转发原始通知。
    message = event_payload.get("message", event_payload)
    if isinstance(message, dict):
        info = message.get("message_info", {})
        if isinstance(info, dict):
            additional = info.get("additional_config", {})
            if isinstance(additional, dict) and isinstance(additional.get("napcat_notice_payload"), dict):
                event_payload = additional["napcat_notice_payload"]

    # 同时支持直接的原始通知，便于独立验证。
    raw = event_payload.get("raw")
    if not isinstance(raw, dict):
        raw = event_payload

    # 2. 检查事件类型是否为 notice 且 notice_type 为 group_ban
    post_type = raw.get("post_type")
    notice_type = raw.get("notice_type")

    # 某些适配器可能直接在顶层包装 notice_type
    if post_type is not None and post_type != "notice":
        return None

    if notice_type != "group_ban":
        return None

    # 3. 提取关键字段
    sub_type = str(raw.get("sub_type", "")).strip()
    if sub_type not in ("ban", "lift_ban", "whole_lift_ban"):
        return None
    group_id = _safe_int(raw.get("group_id"))
    operator_id = _safe_int(raw.get("operator_id"))
    user_id = _safe_int(raw.get("user_id"), -1)
    duration = _safe_int(raw.get("duration", 0))

    if group_id <= 0 or duration < 0:
        return None
    if user_id < 0 and sub_type != "whole_lift_ban":
        return None

    # 4. 判断全员禁言与动作状态
    # OneBot 规范：user_id == 0 代表全员禁言
    is_all = (user_id == 0) or (sub_type == "whole_lift_ban")
    
    # 动作状态：ban=禁言, lift_ban/whole_lift_ban=解除禁言
    # 全员禁言开启时 duration 也可能为 0，动作必须以 sub_type 为准。
    is_ban = sub_type == "ban"
    if is_all:
        user_id = 0

    # 5. 校验目标是否为本机器人
    # 优先使用配置的 bot_qq，其次尝试从 raw 中探测 self_id
    effective_bot_qq = configured_bot_qq or _safe_int(raw.get("self_id"))

    if not is_all:
        # 单人禁言情况下，必须是被禁言目标为机器人本人
        # 无法确认机器人身份时不能把普通群成员的事件当作机器人告警。
        if not effective_bot_qq or user_id != effective_bot_qq:
            return None

    return MuteEvent(
        group_id=group_id,
        operator_id=operator_id,
        user_id=user_id,
        duration=duration,
        is_ban=is_ban,
        is_all=is_all,
    )
