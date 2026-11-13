"""告警消息格式化与分发推送模块

负责告警防抖冷却、时间换算、模板占位符渲染，以及向指定群聊和管理员发送通知。
"""

import logging
import asyncio
from time import monotonic
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Dict, Any, Optional, Tuple, List, Set

from .config import DEFAULT_MUTE_TEMPLATE, DEFAULT_UNMUTE_TEMPLATE, MuteAlertConfig
from .detector import MuteEvent

logger = logging.getLogger("maibot.plugin.mute_alert")
BEIJING = timezone(timedelta(hours=8))
RETRY_DELAYS = (3, 10)
Target = Tuple[bool, int]
JobKey = Tuple[int, bool, Target]


class DeliveryResult(Enum):
    TEXT_SENT = "text_sent"
    AI_QUEUED = "ai_queued"
    FAILED = "failed"
    UNKNOWN = "unknown"


@dataclass
class _DeliveryJob:
    event: MuteEvent
    target: Target
    config: MuteAlertConfig
    stop: asyncio.Event = field(default_factory=asyncio.Event)
    task: Optional[asyncio.Task] = None
    in_flight: bool = False
    side_effect_started: bool = False

    @property
    def key(self) -> JobKey:
        return (self.event.group_id, self.event.is_all, self.target)


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
        self._jobs: Dict[JobKey, _DeliveryJob] = {}
        self._tasks: Set[asyncio.Task] = set()
        self._target_locks: Dict[Tuple[int, Target], asyncio.Lock] = {}
        self._slots = asyncio.Semaphore(4)
        self._closed = False

    def is_in_cooldown(
        self, group_id: int, is_all: bool = False, is_ban: bool = True,
        target: Optional[Tuple[bool, int]] = None,
    ) -> bool:
        """检查成功或结果未知目标的冷却。"""
        previous = self._last_alert_time.get((group_id, is_all))
        if not previous or previous[0] != is_ban:
            return False
        timestamps = previous[1]
        times = timestamps.values() if target is None else [timestamps.get(target, timestamps.get(None, float("-inf")))]
        now = monotonic()
        return any(now - sent_at < self.config.cooldown_seconds for sent_at in times)

    def update_cooldown(
        self, group_id: int, is_all: bool = False, is_ban: bool = True,
        target: Optional[Tuple[bool, int]] = None,
    ) -> None:
        """各目标独立记录，不延长其他目标的冷却。"""
        key = (group_id, is_all)
        if key not in self._last_alert_time or self._last_alert_time[key][0] != is_ban:
            self._last_alert_time[key] = (is_ban, {})
        self._last_alert_time[key][1][target] = monotonic()

    def render_message(self, event: MuteEvent, config: Optional[MuteAlertConfig] = None) -> str:
        """根据事件渲染告警模板"""
        config = config or self.config
        now_str = datetime.fromtimestamp(event.timestamp, BEIJING).strftime("%Y-%m-%d %H:%M:%S")
        duration_desc = format_duration(event.duration)
        if event.is_all and event.is_ban and event.duration == 0:
            duration_desc = "未提供，等待管理员解除"
        
        template = config.mute_template if event.is_ban else config.unmute_template
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

    async def _send_text(self, text: str, stream_id: str) -> DeliveryResult:
        """调用 ctx.send 发送文本消息"""
        if not self.ctx:
            logger.warning("未检测到有效 ctx.send 接口，无法对外发送消息")
            return DeliveryResult.FAILED

        try:
            sent = await self.ctx.send.text(text, stream_id)
            if sent is True:
                return DeliveryResult.TEXT_SENT
            if sent is False:
                return DeliveryResult.FAILED
            logger.error("发送告警回执无法识别: stream=%s result=%r", stream_id, sent)
        except Exception as e:
            logger.error("发送告警结果未知: stream=%s error=%s", stream_id, e)
        return DeliveryResult.UNKNOWN

    def render_ai_intent(self, event: MuteEvent, config: Optional[MuteAlertConfig] = None) -> str:
        """将真实事件作为插件任务材料提供给管理员私聊的 AI。"""
        config = config or self.config
        action = "禁言" if event.is_ban else "解除禁言"
        scope = "群全员禁言状态" if event.is_all else "你本人"
        duration = (
            "未提供，等待管理员解除"
            if event.is_all and event.is_ban and event.duration == 0
            else format_duration(event.duration)
        )
        facts = (
            "【被禁言告警插件：已核验的跨群事件，非管理员发言】\n"
            f"事件时间：{datetime.fromtimestamp(event.timestamp, BEIJING).isoformat(timespec='seconds')}\n"
            f"发生群号：{event.group_id}\n"
            f"对象：{scope}\n状态：{action}\n"
            f"操作者QQ：{event.operator_id or '未知/系统'}\n"
            f"事件携带的时长：{duration}\n"
        )
        if event.is_all:
            facts += "全员禁言不一定限制管理员身份的你；未确认你本人无法发言，不要把这一点当成事实。\n"
        return facts + "\n回复要求：\n" + config.ai_instruction

    async def _notify_admin(
        self, event: MuteEvent, text: str, stream_id: str,
        config: Optional[MuteAlertConfig] = None,
        job: Optional[_DeliveryJob] = None,
    ) -> DeliveryResult:
        """成功表示固定文本送达，或 AI 任务成功入队，不等于 AI 已回复。"""
        config = config or self.config
        if config.ai_notify_admins and self.ctx:
            try:
                # trigger 自身会追加内部上下文并唤醒聊天流，无需伪造用户消息或重复 append。
                result = await self.ctx.maisaka.proactive.trigger(
                    stream_id,
                    self.render_ai_intent(event, config),
                    reason="mute_alert",
                    metadata={"source_group_id": event.group_id, "operator_id": event.operator_id,
                              "is_ban": event.is_ban, "is_all": event.is_all, "duration": event.duration},
                )
                if isinstance(result, dict) and result.get("success") is True and result.get("queued") is True:
                    logger.info("管理员AI告警任务已入队: stream=%s task=%s", stream_id, result.get("task_id"))
                    return DeliveryResult.AI_QUEUED
                if not isinstance(result, dict) or result.get("queued") is True:
                    return DeliveryResult.UNKNOWN
                if result.get("success") is not False and result.get("queued") is not False:
                    return DeliveryResult.UNKNOWN
                logger.error("管理员AI告警任务提交失败: stream=%s result=%r", stream_id, result)
            except Exception as exc:
                logger.error("管理员AI告警提交结果未知: stream=%s error=%s", stream_id, exc)
                return DeliveryResult.UNKNOWN
            if not config.ai_fallback_to_text:
                return DeliveryResult.FAILED
            if job is not None and not self._current(job):
                return DeliveryResult.FAILED
        return await self._send_text(text, stream_id)

    async def dispatch_alert(self, event: MuteEvent) -> int:
        """等待本次新建任务，返回文本发送或 AI 入队成功的目标数量。"""
        tasks = self.submit(event)
        if not tasks:
            return 0
        results = await asyncio.gather(*(asyncio.shield(task) for task in tasks), return_exceptions=True)
        return sum(result in (DeliveryResult.TEXT_SENT, DeliveryResult.AI_QUEUED) for result in results)

    @staticmethod
    def _normalize_ids(values: List[int], warn: bool = False) -> List[int]:
        result = []
        for value in values:
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                if warn:
                    logger.warning("已跳过无效告警目标号码: %r", value)
            elif value not in result:
                result.append(value)
        return result

    def _targets(self, warn: bool = False) -> List[Target]:
        return ([(True, gid) for gid in self._normalize_ids(self.config.target_groups, warn)]
                + [(False, uid) for uid in self._normalize_ids(self.config.target_users, warn)])

    def _allows(self, event: MuteEvent) -> bool:
        return (self.config.enabled
                and (not event.is_all or self.config.alert_on_all_mute)
                and (event.is_ban or self.config.alert_on_unmute))

    def _current(self, job: _DeliveryJob) -> bool:
        return (not self._closed and not job.stop.is_set()
                and self._jobs.get(job.key) is job and self._allows(job.event)
                and job.target in self._targets())

    @staticmethod
    def _stop_job(job: _DeliveryJob) -> None:
        job.stop.set()
        # 已发起的请求保留顺序并等待回执；等待锁、并发名额或重试中的任务立即退出。
        if job.task and not job.in_flight:
            job.task.cancel()

    def update_config(self, config: MuteAlertConfig) -> None:
        """立即停止被禁用或已移除接收者的后续投递。"""
        self.config = config
        targets = self._targets(warn=True)
        for job in list(self._jobs.values()):
            if job.target not in targets or not self._allows(job.event):
                self._stop_job(job)

    def submit(self, event: MuteEvent) -> List[asyncio.Task]:
        """同步入队；同类重复事件复用现有任务，不等待任何网络请求。"""
        if self._closed:
            return []
        source = (event.group_id, event.is_all)
        previous = self._last_alert_time.get(source)
        if not previous or previous[0] != event.is_ban:
            self._last_alert_time[source] = (event.is_ban, {})
            for job in list(self._jobs.values()):
                if job.key[:2] == source:
                    self._stop_job(job)
        if not self._allows(event):
            return []
        targets = self._targets(warn=True)
        if not targets:
            logger.warning("检测到群 %s 的事件，但没有有效告警接收者", event.group_id)
        tasks = []
        for target in targets:
            key = (event.group_id, event.is_all, target)
            previous_job = self._jobs.get(key)
            if previous_job and not previous_job.stop.is_set() and not previous_job.task.done():
                continue
            if self.is_in_cooldown(event.group_id, event.is_all, event.is_ban, target):
                continue
            job = _DeliveryJob(event, target, self.config.model_copy(deep=True))
            self._jobs[key] = job
            job.task = asyncio.create_task(self._run_job(job), name=f"mute-alert:{key}")
            self._tasks.add(job.task)
            job.task.add_done_callback(lambda task, item=job: self._job_done(item, task))
            tasks.append(job.task)
        return tasks

    def _job_done(self, job: _DeliveryJob, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if self._jobs.get(job.key) is job:
            del self._jobs[job.key]
        if task.cancelled():
            logger.info("告警任务已取消: source=%s target=%s", job.event.group_id, job.target)
        if not task.cancelled() and task.exception() is not None:
            logger.error("告警后台任务异常: source=%s target=%s error=%s",
                         job.event.group_id, job.target, task.exception())

    async def _deliver(self, job: _DeliveryJob) -> DeliveryResult:
        stream_id = await self._resolve_stream_id(job.target[1], job.target[0])
        if not stream_id:
            return DeliveryResult.FAILED
        if not self._current(job):
            return DeliveryResult.FAILED
        text = self.render_message(job.event, job.config)
        job.side_effect_started = True
        if job.target[0]:
            return await self._send_text(text, stream_id)
        return await self._notify_admin(job.event, text, stream_id, job.config, job)

    async def _run_job(self, job: _DeliveryJob) -> DeliveryResult:
        # 同一来源群和接收目标串行；重试等待不占全局四路并发名额。
        lock = self._target_locks.setdefault((job.event.group_id, job.target), asyncio.Lock())
        async with lock:
            for attempt in range(job.config.retry_attempts + 1):
                if not self._current(job) or attempt > self.config.retry_attempts:
                    return DeliveryResult.FAILED
                if attempt:
                    try:
                        await asyncio.wait_for(job.stop.wait(), RETRY_DELAYS[attempt - 1])
                        return DeliveryResult.FAILED
                    except asyncio.TimeoutError:
                        pass
                async with self._slots:
                    if not self._current(job) or attempt > self.config.retry_attempts:
                        return DeliveryResult.FAILED
                    job.in_flight = True
                    job.side_effect_started = False
                    try:
                        result = await asyncio.wait_for(
                            self._deliver(job), timeout=job.config.delivery_timeout_seconds)
                    except Exception as exc:
                        result = DeliveryResult.UNKNOWN if job.side_effect_started else DeliveryResult.FAILED
                        logger.warning("告警尝试异常: source=%s target=%s error=%r",
                                       job.event.group_id, job.target, exc)
                    finally:
                        job.in_flight = False
                log = logger.warning if result in (DeliveryResult.FAILED, DeliveryResult.UNKNOWN) else logger.info
                log("告警投递: source=%s scope=%s state=%s target=%s attempt=%s/%s result=%s",
                    job.event.group_id, "all" if job.event.is_all else "bot",
                    "ban" if job.event.is_ban else "unmute", job.target,
                    attempt + 1, job.config.retry_attempts + 1, result.value)
                if result != DeliveryResult.FAILED:
                    if self._current(job):
                        self.update_cooldown(job.event.group_id, job.event.is_all, job.event.is_ban, job.target)
                    return result
        return DeliveryResult.FAILED

    async def wait_idle(self) -> None:
        """等待当前后台投递结束，供集成验证使用。"""
        while self._tasks:
            await asyncio.gather(*(asyncio.shield(task) for task in list(self._tasks)), return_exceptions=True)

    async def close(self) -> None:
        """卸载时取消所有任务并回收，不保留后台重试。"""
        self._closed = True
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._jobs.clear()
        self._tasks.clear()
        self._target_locks.clear()
        self._last_alert_time.clear()
