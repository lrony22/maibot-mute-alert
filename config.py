"""配置管理模块

定义插件所需的配置字段与默认值，支持热重载与序列化。
"""

from typing import Any, Dict, List

from maibot_sdk import Field, PluginConfigBase


DEFAULT_MUTE_TEMPLATE = (
    "⚠️ 【MaiBot 被禁言告警】\n"
    "━━━━━━━━━━━━━━\n"
    "📍 发生群聊：{group_id}\n"
    "👮 禁言操作者：{operator_id}\n"
    "⏱️ 禁言时长：{duration_str}\n"
    "🕒 告警时间：{time}\n"
    "━━━━━━━━━━━━━━\n"
    "请管理员及时检查群内运行状态！"
)

DEFAULT_UNMUTE_TEMPLATE = (
    "✅ 【MaiBot 解除禁言通知】\n"
    "━━━━━━━━━━━━━━\n"
    "📍 发生群聊：{group_id}\n"
    "👮 解禁操作者：{operator_id}\n"
    "🕒 解禁时间：{time}\n"
    "━━━━━━━━━━━━━━\n"
    "机器人已恢复正常发言能力。"
)


class MuteAlertConfig(PluginConfigBase):
    """插件设置；由 SDK 校验并生成 WebUI 表单。"""

    __ui_label__ = "禁言告警"

    enabled: bool = Field(True, description="是否启用插件")
    config_version: str = Field("1.1.0", description="配置版本")
    bot_qq: int = Field(0, ge=0, description="机器人QQ号，0 表示从通知 self_id 自动识别")
    target_groups: List[int] = Field(default_factory=list, description="接收告警的群号列表")
    target_users: List[int] = Field(default_factory=list, description="接收告警的管理员QQ号列表")
    alert_on_all_mute: bool = Field(True, description="是否推送全员禁言通知")
    alert_on_unmute: bool = Field(True, description="是否推送解禁通知")
    cooldown_seconds: int = Field(10, ge=0, description="同一群同类告警冷却秒数")
    ai_notify_admins: bool = Field(False, description="向管理员私聊提交AI主动回复任务，替代固定文本告警")
    ai_fallback_to_text: bool = Field(True, description="AI任务提交失败时发送固定告警；不检测入队后的生成或投递失败")
    ai_instruction: str = Field(
        "请结合当前管理员私聊的关系、记忆和你的人设，用第一人称自然地向管理员说说这件事。"
        "简短回复一条，说明发生在哪个群、禁言或解禁状态及已知时长。"
        "可以表达符合人设的感受，不要编造被禁言的原因。"
        "只在当前管理员私聊里回复，不要去其他群发消息，也不要执行解禁、踢人等管理操作。",
        description="AI告警回复要求；事件事实会自动附在前面，无需模板占位符",
    )
    mute_template: str = Field(DEFAULT_MUTE_TEMPLATE, description="禁言模板")
    unmute_template: str = Field(DEFAULT_UNMUTE_TEMPLATE, description="解禁模板")

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MuteAlertConfig":
        return cls.model_validate(data)

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()


class MuteAlertPluginConfig(PluginConfigBase):
    plugin: MuteAlertConfig = Field(default_factory=MuteAlertConfig)
