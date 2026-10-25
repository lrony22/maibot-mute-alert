# MaiBot 被禁言告警插件 (Mute-Alert)

适用于 **MaiBot（麦麦机器人）** 的群禁言实时监控与告警插件。

版本 1.1.0 在 MaiBot 1.3.14 / SDK 2.16.0 的入站 Hook 基础上支持管理员 AI 告警。适配器需要通过 `napcat_notice_payload` 转发 `group_ban` 通知。

当机器人在群聊中被群主/管理员禁言，或者群聊开启全员禁言时，本插件能第一时间捕获通知，并自动将告警消息精准推送到指定的**运维管理群**或**管理员私聊**中。

---

## ✨ 功能特性

- 🎯 **精准判定**：严格识别被禁言目标是否为当前 Bot 本人，普通群员被禁言不会误触发。
- 🤖 **智能探测**：支持自动识别自身 QQ 号，同时也支持在配置文件中手动指定以保万无一失。
- 📢 **多端推送**：支持同时配置多个接收群号（`target_groups`）与多个管理员 QQ 号（`target_users`）。
- 🌐 **全员禁言监控**：群聊开启全员禁言时同步告警（可配置开关）；该状态不证明管理员身份的 Bot 也无法发言。
- 🔓 **解禁通知**：管理员解除禁言时自动发送解禁通知（可配置开关）。
- ⏳ **重复事件冷却**：抑制同一群同类重复通知；禁言后立即解禁仍会发送，不限制交替事件的总速率。
- 📝 **自定义模板**：告警消息模板全面支持占位符定制（群号、操作人、时长、发生时间等）。
- 🔄 **热重载支持**：配置更新即时生效，无需重启机器人。

---

## 📦 文件结构

```text
Mute-Alert/
├── _manifest.json       # 官方插件 Manifest v2 清单
├── config.py            # 配置字段定义与数据模型
├── detector.py          # 禁言通知提取与判定器
├── notifier.py          # 告警模板渲染与推送分发器
├── plugin.py            # 插件入口类与 chat.receive.before_process 观察 Hook
├── LICENSE              # MIT 许可证
├── CHANGELOG.md         # 更新记录
└── README.md            # 说明文档
```

---

## ⚙️ 配置说明

你可以在 MaiBot WebUI 插件管理面板中进行图形化配置，或编辑插件目录下 `config.toml` 的 `[plugin]` 节。配置由 SDK 生成，包含必需的 `config_version = "1.1.0"`，支持热更新。

| 配置字段 | 类型 | 默认值 | 说明 |
| :--- | :--- | :--- | :--- |
| `bot_qq` | `int` | `0` | 机器人自身QQ号（0 则从通知中提取 `self_id`；无法识别身份时忽略个人禁言通知） |
| `target_groups` | `list[int]` | `[]` | 接收被禁言告警的群号列表（如 `[123456789]`） |
| `target_users` | `list[int]` | `[]` | 接收被禁言告警的管理员QQ列表（如 `[987654321]`） |
| `alert_on_all_mute` | `bool` | `true` | 全员禁言时是否触发告警 |
| `alert_on_unmute` | `bool` | `true` | 解除禁言时是否发送恢复通知 |
| `cooldown_seconds` | `int` | `10` | 同一群同类告警按接收目标独立冷却；失败目标可在重复通知到来时重试；禁言、解禁切换仍发送 |
| `ai_notify_admins` | `bool` | `false` | 向 `target_users` 的私聊提交 AI 主动任务，替代固定告警；群接收者仍使用固定文本 |
| `ai_fallback_to_text` | `bool` | `true` | AI 任务提交失败时发送固定告警 |
| `ai_instruction` | `str` | 人设简短回复要求 | AI 回复要求；群号、操作者、状态、时长由插件自动补充 |
| `mute_template` | `str` | 见下方说明 | 被禁言告警消息模板 |
| `unmute_template` | `str` | 见下方说明 | 解除禁言通知消息模板 |

### 模板占位符支持

在 `mute_template` 与 `unmute_template` 中支持以下动态占位符：
- `{group_id}`: 发生禁言的群号
- `{operator_id}`: 执行禁言的操作者 QQ 号
- `{user_id}`: 被禁言的目标（全体成员或QQ号）
- `{duration_str}`: 人性化时长（如：`30分钟`、`1天`）；全员禁言开启且时长为 0 时显示未提供时长
- `{duration}`: 原始秒数（如：`1800`）
- `{time}`: 事件发生时间（如：`2026-10-25 12:00:00`）
- `{is_all}`: 是否为全员状态（`是` 或 `否`）

---

## 🚀 安装与使用

1. 将本仓库克隆或解压到 MaiBot 的 `plugins/Mute-Alert`，确保该目录直接包含 `plugin.py` 与 `_manifest.json`。
2. 在 MaiBot Web 控制台启用本插件。
3. 在插件设置中填入**目标群号**或**管理员QQ号**即可。

要求 MaiBot ≥1.3.14、`maibot-plugin-sdk` ≥2.16.0，且在 Manifest 声明的兼容范围内。适配器必须提供上述通知载荷格式。

以下示例合并到 SDK 生成的 `config.toml`，目标号码请替换为自己的值，模板可保留生成的默认值：

```toml
[plugin]
enabled = true
config_version = "1.1.0"
bot_qq = 0
target_groups = []
target_users = [123456789]
alert_on_all_mute = true
alert_on_unmute = true
cooldown_seconds = 10
ai_notify_admins = false
ai_fallback_to_text = true
```

请同时确认当前适配器的通知开关及 `enable_group_ban` 已开启。机器人须能访问接收群或管理员私聊；Host 会按目标查找或创建真实会话，发送失败会写入错误日志。自然解禁通知缺少 `self_id` 时，需要手动填写 `bot_qq` 才能识别个人解禁。

AI 模式通过 `maisaka.proactive.trigger` 将事件作为内部任务加入管理员聊天流，并唤醒原有 Maisaka 对话循环，沿用当前人设、关系、记忆和模型配置。它不伪造管理员发言，也不把其他群的聊天记录复制过去。开启后，真实事件可能触发正常的模型调用及费用。

任务成功入队后由 AI 决定是否回复、如何表达；日志中的“AI告警任务已入队”不表示已经生成或送达。固定文本兜底只覆盖提交失败，不监测入队后的模型失败、AI 选择不回复或发送失败。全员禁言只描述群状态，提示词不会据此断言管理员身份的机器人也被禁言。

## 使用限制

AI 模式不能保证告警文案完整，可能遗漏群号、时长或解禁状态。需要确定性状态告警时保持默认 `ai_notify_admins = false`。

默认固定模板已区分个人与全员状态，全员解禁不再声称机器人已恢复发言；已有配置中与内置默认文本相同的模板也会使用新文案。自定义模板保持原样，请按 `{is_all}` 等占位符自行准确描述状态。

冷却只记录成功发送或 AI 入队的目标；后续重复通知会重试失败目标，冷却内不重复投递已成功目标。重试不会延长其他目标的冷却，禁言与解禁切换时清除旧状态记录；没有后台定时重试，AI 入队后的生成和投递失败也不在重试范围内。

## 许可证与反馈

作者：[lrony22](https://github.com/lrony22)。采用 [MIT License](LICENSE)。
问题反馈：[GitHub Issues](https://github.com/lrony22/maibot-mute-alert/issues)；请脱敏后提交日志，不上传完整私聊 prompt、账号资料或运行配置。
