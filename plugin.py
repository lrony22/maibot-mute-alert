"""MaiBot 被禁言告警插件入口

继承 MaiBotPlugin，通过入站 Hook 观察适配器转发的通知。
"""

from typing import Any, Dict, Optional

import logging

from maibot_sdk import CONFIG_RELOAD_SCOPE_SELF, HookHandler, MaiBotPlugin
from maibot_sdk.types import HookMode

from .config import MuteAlertPluginConfig
from .detector import parse_mute_event
from .notifier import MuteNotifier

logger = logging.getLogger("maibot.plugin.mute_alert")


class MuteAlertPlugin(MaiBotPlugin):
    """被禁言告警插件主类"""

    config_model = MuteAlertPluginConfig

    def __init__(self):
        super().__init__()
        self.notifier: Optional[MuteNotifier] = None

    async def on_load(self) -> None:
        """插件加载时的初始化逻辑"""
        settings = self.config.plugin
        self.notifier = MuteNotifier(settings, self.ctx)
        
        target_info = (
            f"群聊目标: {settings.target_groups or '无'}, "
            f"管理目标: {settings.target_users or '无'}"
        )
        logger.info(f"【被禁言告警插件】已加载成功！监控配置 -> {target_info}")
        if not settings.target_groups and not settings.target_users:
            logger.warning("尚未配置告警接收者，请在 WebUI 的禁言告警设置中填写目标")

    async def on_unload(self) -> None:
        """插件卸载清理逻辑"""
        logger.info("【被禁言告警插件】已安全卸载")

    async def on_config_update(
        self, scope: str, config_data: Dict[str, Any], version: str
    ) -> None:
        """配置更新回调（支持 WebUI 热重载）"""
        if scope == CONFIG_RELOAD_SCOPE_SELF:
            # Runner 在回调前已完成配置注入，避免覆盖 SDK 的只读 config 属性。
            if self.notifier:
                self.notifier.config = self.config.plugin
            logger.info(f"【被禁言告警插件】配置已动态重载 (版本: {version})")

    @HookHandler(
        "chat.receive.before_process",
        name="mute_alert_handler",
        description="监听群禁言与解禁通知并推送告警",
        mode=HookMode.OBSERVE,
        timeout_ms=30000,
    )
    async def handle_mute_event(self, **kwargs) -> None:
        """处理入站事件，检测是否有群禁言事件"""
        if not self.config.plugin.enabled:
            return
        if not self.notifier:
            self.notifier = MuteNotifier(self.config.plugin, self.ctx)

        configured_bot_qq = self.config.plugin.bot_qq

        # 解析禁言/解禁事件
        event = parse_mute_event(kwargs, configured_bot_qq=configured_bot_qq)
        if not event:
            return

        logger.warning(
            f"⚡ 捕获到群禁言/解禁事件: 群 {event.group_id}, "
            f"操作者 {event.operator_id}, 时长 {event.duration}s, 全员={event.is_all}"
        )

        # 触发告警推送
        await self.notifier.dispatch_alert(event)


def create_plugin() -> MuteAlertPlugin:
    """MaiBot 规范的插件工厂函数"""
    return MuteAlertPlugin()
