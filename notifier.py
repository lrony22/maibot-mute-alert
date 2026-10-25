"""告警消息格式化与分发推送模块

负责告警防抖冷却、时间换算、模板占位符渲染，以及向指定群聊和管理员发送通知。
"""

import time
import logging
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Optional, Tuple

from .config import DEFAULT_MUTE_TEMPLATE, DEFAULT_UNMUTE_TEMPLATE, MuteAlertConfig
from .detector import MuteEvent

logger = logging.getLogger("maibot.plugin.mute_alert")


def format_duration(seconds: int) -> str:
    """将秒数转换为人性化的时长字符串描述"""
    if seconds <= 0:
        return "0秒"
    
    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60

    parts = []
    if days > 0:
        parts.append(f"{days}天")
    if hours > 0:
        parts.append(f"{hours}小时")
    if minutes > 0:
        parts.append(f"{minutes}分钟")
    if secs > 0 and not (days or hours):  # 超过1小时则通常忽略零散秒数
        parts.append(f"{secs}秒")

    return "".join(parts) if parts else f"{seconds}秒"


class MuteNotifier:
    """告警推送管理中心"""

    def __init__(self, config: MuteAlertConfig, ctx: Optional[Any] = None):
        self.config = config
        self.ctx = ctx
        self._last_alert_time: Dict[
            Tuple[int, bool], Tuple[bool, Dict[Optional[Tuple[bool, int]], float]]
        ] = {}
        self._dispatch_lock = asyncio.Lock()

    def is_in_cooldown(
        self, group_id: int, is_all: bool = False, is_ban: bool = True,
        target: Optional[Tuple[bool, int]] = None,
    ) -> bool:
        """检查目标的成功投递冷却；未指定目标时查询是否有目标冷却。"""
        previous = self._last_alert_time.get((group_id, is_all))
        if not previous or previous[0] != is_ban:
            return False
        timestamps = previous[1]
        times = timestamps.values() if target is None else [timestamps.get(target, timestamps.get(None, float("-inf")))]
        now = time.monotonic()
        return any(now - sent_at < self.config.cooldown_seconds for sent_at in times)

    def update_cooldown(
        self, group_id: int, is_all: bool = False, is_ban: bool = True,
        target: Optional[Tuple[bool, int]] = None,
    ) -> None:
        """只记录成功目标，重试成功不延长其他目标的冷却。"""
        key = (group_id, is_all)
        if key not in self._last_alert_time or self._last_alert_time[key][0] != is_ban:
            self._last_alert_time[key] = (is_ban, {})
        self._last_alert_time[key][1][target] = time.monotonic()

    def render_message(self, event: MuteEvent) -> str:
        """根据事件渲染告警模板"""
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        duration_desc = format_duration(event.duration)
        if event.is_all and event.is_ban and event.duration == 0:
            duration_desc = "未提供，等待管理员解除"
        
        template = self.config.mute_template if event.is_ban else self.config.unmute_template
        default_template = DEFAULT_MUTE_TEMPLATE if event.is_ban else DEFAULT_UNMUTE_TEMPLATE
        if event.is_all and template == default_template:
            template = (
                f"📢 【群全员禁言{'开启' if event.is_ban else '关闭'}通知】\n"
                "群号：{group_id}\n操作者：{operator_id}\n"
                + ("时长：{duration_str}\n" if event.is_ban else "")
                + "时间：{time}\n群状态不代表机器人个人禁言状态，请确认实际发言权限。"
            )
        
        # 兼容性变量映射
        variables = {
            "group_id": event.group_id,
            "operator_id": event.operator_id or "系统/管理员",
            "user_id": "全体成员" if event.is_all else event.user_id,
            "duration": event.duration,
            "duration_str": duration_desc,
            "time": now_str,
            "is_all": "是" if event.is_all else "否",
        }
        
        try:
            return template.format(**variables)
        except Exception as e:
            logger.warning(f"模板渲染失败，使用默认格式: {e}")
            action = "被群禁言" if event.is_ban else "解除禁言"
            if event.is_all:
                action = "全员禁言开启" if event.is_ban else "全员禁言关闭"
            return (
                f"⚠️ 【MaiBot {action}告警】\n"
                f"群号：{event.group_id}\n"
                f"操作者：{event.operator_id}\n"
                + (f"时长：{duration_desc}\n" if event.is_ban else "")
                + f"时间：{now_str}"
                + ("\n群状态不代表机器人个人禁言状态，请确认实际发言权限。" if event.is_all else "")
            )

    async def _resolve_stream_id(self, target_id: int, is_group: bool) -> Optional[str]:
        """按目标精确查找真实会话；首次联系时由 Host 创建会话。"""
        if not self.ctx or target_id <= 0:
            return None
        try:
            if is_group:
                stream = await self.ctx.chat.get_stream_by_group_id(str(target_id))
            else:
                stream = await self.ctx.chat.get_stream_by_user_id(str(target_id))
            if not stream:
                target = {"group_id" if is_group else "user_id": str(target_id)}
                opened = await self.ctx.chat.open_session("qq", "group" if is_group else "private", **target)
                if not isinstance(opened, dict) or not opened.get("success"):
                    logger.error("无法为告警目标 %s 创建会话", target_id)
                    return None
                stream = opened
            if isinstance(stream, dict):
                stream_id = stream.get("session_id") or stream.get("stream_id")
                if isinstance(stream_id, str) and stream_id:
                    return stream_id
        except Exception as ex:
            logger.error("解析告警目标 %s 的会话失败: %s", target_id, ex)
        return None

    async def _send_text(self, text: str, stream_id: str) -> bool:
        """调用 ctx.send 发送文本消息"""
        if not self.ctx:
            logger.warning("未检测到有效 ctx.send 接口，无法对外发送消息")
            return False

        try:
            sent = await self.ctx.send.text(text, stream_id)
            if sent is True:
                return True
            logger.error("发送告警到流 [%s] 失败，Host 返回: %r", stream_id, sent)
        except Exception as e:
            logger.error(f"发送消息到流 [{stream_id}] 发生异常: {e}")
            return False
        return False

    def render_ai_intent(self, event: MuteEvent) -> str:
        """将真实事件作为插件任务材料提供给管理员私聊的 AI。"""
        action = "禁言" if event.is_ban else "解除禁言"
        scope = "群全员禁言状态" if event.is_all else "你本人"
        duration = (
            "未提供，等待管理员解除"
            if event.is_all and event.is_ban and event.duration == 0
            else format_duration(event.duration)
        )
        facts = (
            "【被禁言告警插件：已核验的跨群事件，非管理员发言】\n"
            f"事件时间：{datetime.fromtimestamp(event.timestamp, timezone(timedelta(hours=8))).isoformat(timespec='seconds')}\n"
            f"发生群号：{event.group_id}\n"
            f"对象：{scope}\n状态：{action}\n"
            f"操作者QQ：{event.operator_id or '未知/系统'}\n"
            f"事件携带的时长：{duration}\n"
        )
        if event.is_all:
            facts += "全员禁言不一定限制管理员身份的你；未确认你本人无法发言，不要把这一点当成事实。\n"
        return facts + "\n回复要求：\n" + self.config.ai_instruction

    async def _notify_admin(self, event: MuteEvent, text: str, stream_id: str) -> bool:
        """成功表示固定文本送达，或 AI 任务成功入队，不等于 AI 已回复。"""
        if self.config.ai_notify_admins and self.ctx:
            try:
                # trigger 自身会追加内部上下文并唤醒聊天流，无需伪造用户消息或重复 append。
                result = await self.ctx.maisaka.proactive.trigger(
                    stream_id,
                    self.render_ai_intent(event),
                    reason="mute_alert",
                    metadata={"source_group_id": event.group_id, "operator_id": event.operator_id,
                              "is_ban": event.is_ban, "is_all": event.is_all, "duration": event.duration},
                )
                if isinstance(result, dict) and result.get("success") is True and result.get("queued") is True:
                    logger.info("管理员AI告警任务已入队: stream=%s task=%s", stream_id, result.get("task_id"))
                    return True
                logger.error("管理员AI告警任务提交失败: stream=%s result=%r", stream_id, result)
            except Exception as exc:
                logger.error("管理员AI告警任务提交异常: stream=%s error=%s", stream_id, exc)
            if not self.config.ai_fallback_to_text:
                return False
        return await self._send_text(text, stream_id)

    async def dispatch_alert(self, event: MuteEvent) -> int:
        """执行告警分发

        Returns:
            固定文本发送成功或 AI 任务入队成功的目标数量
        """
        # 同一批并发通知在发送完成后再检查冷却，避免重复投递。
        async with self._dispatch_lock:
            return await self._dispatch_alert(event)

    async def _dispatch_alert(self, event: MuteEvent) -> int:
        # 状态切换即清除旧状态投递记录，即使新状态不通知或全部投递失败。
        key = (event.group_id, event.is_all)
        previous = self._last_alert_time.get(key)
        if not previous or previous[0] != event.is_ban:
            self._last_alert_time[key] = (event.is_ban, {})

        # 1. 判定是否满足发送配置条件
        if event.is_all and not self.config.alert_on_all_mute:
            logger.info("已忽略全员禁言事件（配置 alert_on_all_mute=False）")
            return 0

        if not event.is_ban and not self.config.alert_on_unmute:
            logger.info("已忽略解除禁言事件（配置 alert_on_unmute=False）")
            return 0

        # 3. 检查是否有接收目标
        target_groups = self.config.target_groups
        target_users = self.config.target_users
        if not target_groups and not target_users:
            logger.warning(
                f"检测到群 {event.group_id} 禁言事件，但尚未配置 target_groups 或 target_users，跳过推送"
            )
            return 0

        # 4. 生成通知内容
        message_text = self.render_message(event)
        success_count = 0

        # 5. 推送至指定群
        for gid in target_groups:
            target = (True, gid)
            if self.is_in_cooldown(event.group_id, event.is_all, event.is_ban, target):
                continue
            stream_id = await self._resolve_stream_id(gid, is_group=True)
            if stream_id:
                ok = await self._send_text(message_text, stream_id)
                if ok:
                    self.update_cooldown(event.group_id, event.is_all, event.is_ban, target)
                    success_count += 1
                    logger.info(f"被禁言告警已发送到指定群: {gid} (流: {stream_id})")

        # 6. 推送至指定管理员
        for uid in target_users:
            target = (False, uid)
            if self.is_in_cooldown(event.group_id, event.is_all, event.is_ban, target):
                continue
            stream_id = await self._resolve_stream_id(uid, is_group=False)
            if stream_id:
                ok = await self._notify_admin(event, message_text, stream_id)
                if ok:
                    self.update_cooldown(event.group_id, event.is_all, event.is_ban, target)
                    success_count += 1
                    logger.info(f"被禁言告警已交付处理到指定管理员: {uid} (流: {stream_id})")

        return success_count
