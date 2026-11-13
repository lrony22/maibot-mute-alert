# MaiBot 被禁言告警插件 (Mute-Alert)

适用于 **MaiBot（麦麦机器人）** 的群聊禁言实时监控与告警插件。

支持 MaiBot 1.3.14–1.4.x 及 SDK 2.16.0–2.22.x，具备后台异步并发投递、有限阶梯重试和管理员 AI 告警功能。依赖适配器通过 `napcat_notice_payload` 转发 `group_ban` 通知。

当机器人在群聊中被管理员禁言或群聊开启全员禁言时，本插件能第一时间捕获事件，并自动将告警消息精准推送到指定的**运维群**或**管理员私聊**中。

---

## ✨ 功能特性

- 🎯 **精准识别**：严格核验被禁言目标是否为当前 Bot，普通群员被禁言绝不误触发。
- 🤖 **自动探测**：自动识别自身 QQ 号，同时也支持在配置中手动指定作为兜底。
- 📢 **多端推送**：支持同时配置多个接收群聊（`target_groups`）与多个管理员私聊（`target_users`）。
- 🌐 **全员禁言监控**：群聊开启全员禁言时同步告警（可单独配置开关）。
- 🔓 **解禁通知**：管理员解除禁言或全员禁言关闭时自动发送恢复通知（可配置开关）。
- ⏳ **防刷独立冷却**：抑制同群同类事件重复通知；每个接收目标独立冷却，状态切换（禁言/解禁）时自动重置。
- 📬 **异步投递与重试**：后台异步并发投递（全局最多 4 路），失败支持阶梯重试；具备超时保护，慢会话不阻塞主流程。
- 📝 **自定义模板**：告警消息模板全面支持占位符定制（群号、操作人、时长、发生时间等）。
- 🔄 **配置即时热更**：配置更新立即生效，无需重启机器人。

---

## 📦 文件结构

```text
Mute-Alert/
├── _manifest.json       # 官方插件 Manifest v2 清单
├── config.py            # 配置字段定义与数据模型
├── detector.py          # 禁言通知提取与判定器
├── notifier.py          # 告警模板渲染与推送分发器
├── plugin.py            # 插件入口类与 chat.receive.before_process 观察 Hook
├── tests/               # 内存 RPC 回归测试
├── LICENSE              # MIT 许可证
├── CHANGELOG.md         # 更新记录
└── README.md            # 说明文档
```

---

## ⚙️ 配置说明

你可以在 MaiBot WebUI 插件管理面板中进行图形化配置，或编辑插件目录下 `config.toml` 的 `[plugin]` 节。配置包含 `config_version = "1.2.0"` 并支持热更新。新版 SDK 升级时会自动补齐新增字段，同时完整保留原有的接收目标、模板和 AI 相关设置。

| 配置字段 | 类型 | 默认值 | 说明 |
| :--- | :--- | :--- | :--- |
| `bot_qq` | `int` | `0` | 机器人自身 QQ 号（设为 0 时从通知 `self_id` 自动识别；无法识别时忽略个人禁言通知） |
| `target_groups` | `list[int]` | `[]` | 接收告警的群号列表（如 `[123456789]`） |
| `target_users` | `list[int]` | `[]` | 接收告警的管理员 QQ 列表（如 `[987654321]`） |
| `alert_on_all_mute` | `bool` | `true` | 群聊开启全员禁言时是否触发告警 |
| `alert_on_unmute` | `bool` | `true` | 解除禁言（个人解禁或全员解禁）时是否发送恢复通知 |
| `cooldown_seconds` | `int` | `10` | 同一群同类告警的独立冷却时间（秒）；状态切换时自动清除冷却 |
| `retry_attempts` | `int` | `2` | 明确投递失败后的追加重试次数（允许 0–2 次，分别间隔 3 秒、10 秒） |
| `delivery_timeout_seconds` | `int` | `10` | 单次投递尝试的超时时长（秒，须大于 0）；排队与重试等待不计入 |
| `ai_notify_admins` | `bool` | `false` | 向管理员私聊（`target_users`）提交 AI 主动任务替代固定文本；群聊目标始终使用固定文本 |
| `ai_fallback_to_text` | `bool` | `true` | AI 任务提交失败时是否降级为固定文本告警；超时或结果未知时不补发 |
| `ai_instruction` | `str` | 见默认值 | AI 回复要求提示词；群号、操作者、状态、时长由插件自动补充 |
| `mute_template` | `str` | 见下方说明 | 被禁言告警消息模板 |
| `unmute_template` | `str` | 见下方说明 | 解除禁言通知消息模板 |

### 模板占位符支持

在 `mute_template` 与 `unmute_template` 中支持以下动态占位符：
- `{group_id}`: 发生禁言的群号
- `{operator_id}`: 执行禁言的操作者 QQ 号
- `{user_id}`: 被禁言的目标（全体成员或 QQ 号）
- `{duration_str}`: 人性化时长（如：`30分钟`、`1天`；全员禁言时长为 0 时提示等待管理员解除）
- `{duration}`: 原始秒数（如：`1800`）
- `{time}`: 事件发生时间（北京时间 UTC+8，格式：`2026-10-25 12:00:00`）
- `{is_all}`: 是否为全员状态（`是` 或 `否`）

---

## 🚀 安装与使用

1. 将本仓库克隆或解压至 MaiBot 的 `plugins/Mute-Alert`，确保该目录下直接包含 `plugin.py` 与 `_manifest.json`。
2. 在 MaiBot Web 控制台启用本插件。
3. 在插件设置中填入**接收告警的群号**（`target_groups`）或**管理员 QQ 号**（`target_users`）即可。

配置示例（加入 `config.toml` 的 `[plugin]` 节）：

```toml
[plugin]
enabled = true
config_version = "1.2.0"
bot_qq = 0
target_groups = []
target_users = [123456789]
alert_on_all_mute = true
alert_on_unmute = true
cooldown_seconds = 10
retry_attempts = 2
delivery_timeout_seconds = 10
ai_notify_admins = false
ai_fallback_to_text = true
```

> **注意**：
> 1. 请确认当前适配器的群通知转发开关及 `enable_group_ban` 已开启。
> 2. 机器人需具备访问接收群或管理员私聊的会话权限。
> 3. 部分适配器的自然解禁通知可能缺失 `self_id`，建议在配置中显式填写 `bot_qq` 以确保能准确捕获个人解禁。

---

## 💡 运行机制与核心设计

### 1. 异步后台投递与阶梯重试
- **非阻塞并发**：事件解析后即提交后台异步任务，不阻塞机器人主消息流水线。全局最多支持 4 路并发投递，重试等待不占用并发槽位。
- **保序与隔离**：针对同一来源群与接收目标严格按事件时序投递；慢网络或个别异常目标不会阻塞其他目标的告警派发。
- **有限阶梯重试**：单次投递受 `delivery_timeout_seconds`（默认 10s）保护。仅在收到明确失败回执时才进行最多 2 次追加重试（分别等待 3s、10s）。若发生网络超时或未知异常，则标记为 `unknown` 并进入冷却，绝不盲目补发造成刷屏。

### 2. 状态感知与生命周期联动
- **独立冷却**：投递成功或未知超时的目标独立进入冷却，期间重复通知自动复用在途任务，不重复打扰。
- **状态切换清除**：当群内发生禁言与解禁状态切换时，旧状态的冷却记录自动重置，且旧状态未完成的重试任务将被立即取消。
- **即时响应热更**：动态修改配置（如禁用插件、移除接收人或关闭某类通知）时，相关目标的待执行任务立即终止；插件卸载时优雅清理所有后台任务。

### 3. 管理员 AI 模式特别说明
- 启用 `ai_notify_admins` 后，插件通过 `maisaka.proactive.trigger` 将禁言事件作为内部任务提交到管理员私聊流，由 AI 结合当前人设与记忆自然表达，不伪造管理员发言，也不跨群泄露聊天记录。
- **确定性建议**：AI 可能会根据人设精简告警语句（例如忽略具体群号或时长）。若需要绝对精确、严肃的运维状态同步，建议保持默认的固定文本模式。
- **兜底机制**：文本兜底（`ai_fallback_to_text`）仅在 AI 任务入队提交明确失败时触发；若任务已成功入队，后续由大模型生成的实际过程不属于兜底监测范畴。

---

## 🛠️ 开发验证与升级

在项目根目录下运行单元测试套件（基于真实 SDK 内存 RPC，无需网络收发）：

```powershell
uv run --no-project --with maibot-plugin-sdk==2.22.1 python -B -X utf8 -m unittest discover -s tests -t . -q
```

**版本升级说明**：
- 升级前建议备份插件目录与现有 `config.toml`。
- 新版本会通过 SDK 自动补齐新增字段并继承旧版参数；如需回退，恢复上一版本插件及对应配置文件即可。

---

## 📄 许可证与反馈

- **开源协议**：[MIT License](LICENSE)
- **作者**：[lrony22](https://github.com/lrony22)
- **问题反馈**：[GitHub Issues](https://github.com/lrony22/maibot-mute-alert/issues)（提交日志时请注意对敏感信息进行脱敏，勿包含完整私聊 Prompt 或账号数据）。
