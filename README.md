# Clone Personality

AstrBot 群友人格克隆插件。插件会读取目标群友的群聊历史，生成可用于 AstrBot 人格系统的人格模板，并以 QQ 合并转发形式输出分析结果。

## 功能

- 群聊和私聊双场景克隆人格。
- 群聊必须 `@bot` 才会触发，避免普通聊天误触发。
- 支持按 `@群友`、QQ 号、群名片或昵称定位目标。
- 从目标发言中生成身份设定、人格核心、说话风格、触发反应、禁止项等。
- 可选生成“骚话 / 爆点语录”，宁缺毋滥，不硬凑普通句子。
- 人格分析结果优先以 QQ 合并转发发送，失败时回退普通文本。
- 支持 AstrBot WebUI 选择用于分析的模型提供商。
- 重复人格 ID 直接覆盖。

## 指令

### 群聊

群聊场景必须先 `@bot`。

| 指令 | 说明 |
|------|------|
| `@bot 克隆 @群友` | 克隆被 @ 的群友 |
| `@bot 克隆 <群名片或昵称>` | 按群名片或昵称匹配并克隆 |
| `@bot 克隆 <QQ号>` | 按 QQ 号克隆 |
| `@bot 人格切换 <ID>` | 手动切换当前会话人格 |
| `@bot 人格切换 default` | 恢复默认人格 |
| `@bot 人格列表` | 查看已保存人格 |
| `@bot 人格详情 <ID>` | 查看人格详情 |
| `@bot 人格导入 <ID>` | 从 AstrBot 人格设定导入到当前群的人格列表 |
| `@bot 人格更新 <ID>` | 更新当前群的指定人格 |
| `@bot 人格删除 <ID>` | 删除人格，管理员可用 |

### 私聊

| 指令 | 说明 |
|------|------|
| `克隆 <群号> <QQ号>` | 私聊窗口克隆指定群友 |
| `克隆 <群号> <群名片或昵称>` | 私聊窗口按群名片或昵称匹配 |
| `克隆 <群号> @群友` | 私聊窗口按 @ 目标匹配，取决于平台是否提供 At 组件 |
| `人格切换 <ID>` | 手动切换当前私聊会话人格 |
| `人格切换 default` | 恢复默认人格 |
| `人格列表 <群号>` | 查看指定群的人格 |
| `人格导入 <群号> <ID>` | 从 AstrBot 人格设定导入到指定群 |
| `人格更新 <群号> <ID>` | 更新指定群的指定人格 |

## 人格 ID

人格 ID 默认使用目标的群名片或昵称，例如：

```text
@bot 克隆 cbaba
人格ID：cbaba
```

如果无法取得昵称，则回退到 QQ 号。

展示名称会带上群号，例如：

```text
477065120cbaba
```

## 人格切换

克隆完成后不会自动切换当前人格。你需要手动执行：

```text
@bot 人格切换 cbaba
```

切换后，插件会在当前会话的 LLM 请求阶段注入该人格 prompt。后续 `@bot` 对话会按该人格回复。

## 人格导入

如果更新插件后插件的 `人格列表` 为空，但 AstrBot 设置页里还保留了人格，可以执行：

```text
@bot 人格导入 cbaba
```

私聊导入需要指定群号：

```text
人格导入 477065120 cbaba
```

导入后即可再执行：

```text
@bot 人格切换 cbaba
```

导入只恢复 AstrBot 人格设定文本；如果要恢复群号、QQ、消息数等完整克隆元数据，需要重新克隆或从旧 `personalities.json` 恢复。

## WebUI 配置

插件提供 `_conf_schema.json`，可在 AstrBot WebUI 中配置：

| 配置 | 说明 |
|------|------|
| `llm.provider_id` | 选择用于人格分析的 AstrBot 模型提供商，留空使用当前默认模型 |
| `message.initial_days` | 优先查询最近多少天的群聊记录 |
| `message.fallback_days` | 首次无记录时扩大查询范围 |
| `message.history_slice` | 备用历史工具参数，仅 `search_qq_chat_history` fallback 使用；例如 `:100` 表示取前 100 条 |
| `message.max_fetch_rounds` | 群历史最大扫描轮数 |
| `message.per_query_count` | 每轮拉取群消息条数 |
| `message.initial_analysis_messages` | 首次克隆分析消息数，建议 200-300 |
| `message.update_analysis_messages` | 增量更新分析消息数，建议 80-150 |
| `message.max_analysis_messages` | 旧版兼容字段，新逻辑优先使用上面两个字段 |
| `message.max_message_chars` | 单条消息最大字符数 |
| `message.max_prompt_chars` | 送入模型的聊天记录总字符预算 |
| `admin_only_inject` | 打开后仅管理员克隆时会创建/更新 AstrBot 人格 |
| `persona.system_prompt_prefix` | 创建/更新人格时拼在人格设定最前面的 System Prompt |
| `auto_update.enabled` | 是否启用自动更新 |
| `auto_update.check_weekday` | 每周几检查；`0` 每天，`1` 周一，`7` 周日 |
| `auto_update.check_hour` | 几点检查；`0-23` |
| `auto_update.stale_days` | 更新频率，超过多少天未更新才更新；`0` 表示到检查时间更新所有人格 |

`persona.system_prompt_prefix` 适合放统一口吻约束，例如：

```text
像真人群友一样短句回复，默认只回 1-3 句，尽量 80 字以内。
别自我介绍，别解释设定，别科普腔。
不要写作文，不要每轮都生成完整闭合段落，不要先总结再解释再升华。
人类口语是开放的、有互动的、可以不完整的。
不要用“简单说”“本质上”“总结一下”“对，这就像……”这类助手式承接。
能一句话说完就别展开；除非用户明确要求详细、展开、分条、认真分析，否则禁止长篇、禁止 Markdown 分点、禁止总结式小作文。
直接接话，少铺垫，少闭环，像活人。
```

## 定期更新

在 WebUI 中打开 `auto_update.enabled` 后，插件会按 `check_weekday` 和 `check_hour` 定时检查已保存人格。

示例：

```text
enabled = true
check_weekday = 1
check_hour = 3
stale_days = 7
```

表示每周一凌晨 3 点检查一次，只更新超过 7 天没更新的人格。

如果 `stale_days = 0`，表示不限制天数，到检查时间就更新所有人格。

到期后会：

- 按人格保存的 `group_id` 和 `user_id` 重新抓取该群里的目标发言。
- 把旧人格作为长期画像，把最新聊天记录作为增量材料进行更新；旧画像权重更高，新消息只用于补充或修正明确变化。
- 覆盖 `personalities.json` 中的人格数据；如果模型没有输出某些稳定字段，会保留旧人格里的对应内容，避免最近少量消息把画像冲掉。
- 尝试同步更新 AstrBot 人格系统里的同名人格。
- 更新完后在对应群发一句提醒，例如：

```text
🧪 cbaba、creep 已定期蒸馏完毕，味儿续上了。
```

注意：后台更新需要插件运行期间至少收到过一次消息，才能拿到可用的 OneBot API 上下文。

## 同名人格

手动执行 `克隆 <目标>` 时：

- 如果人格 ID 已存在，且保存的 `group_id` 和 `user_id` 与本次目标一致，默认走增量更新。
- 如果加 `-f` 或 `--force`，会强制从零重建并覆盖旧人格。
- 如果人格 ID 已存在，但 `group_id` 或 `user_id` 不一致，会按新人格从零分析后覆盖，避免把两个同名群友混在一起。

## 上下文控制

插件不会把所有聊天记录无脑塞给模型。当前会做：

- 限制目标消息条数。
- 限制单条消息长度。
- 限制总字符预算。
- 链接替换为 `[链接]`。

## 生成文件

运行数据默认保存在 AstrBot 数据目录，避免插件更新时被插件目录覆盖：

```text
/AstrBot/data/plugin_data/clone_personality/
```

如果检测到旧版数据仍在插件目录下，插件启动时会自动迁移到上面的持久化目录。

| 文件 | 说明 |
|------|------|
| `personalities.json` | 已克隆的人格数据 |
| `active_sessions.json` | 当前会话绑定的人格 |
| `config.json` | 本地开关配置 |
| `active_persona.txt` | 旧版全局激活人格记录 |
| `current_persona.txt` | PersonaManager 不可用时保存的人格文本 |

## 安装

将插件目录放入：

```text
/AstrBot/data/plugins/
```

或者从 GitHub 安装：

```text
https://github.com/dandancow874/astrbot_plugin_clone_personality
```

安装后重启或热重载 AstrBot。
