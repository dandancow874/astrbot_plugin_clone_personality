# 精细增量更新方案

当前实现采用“旧人格长期画像 + 最新若干条消息”的增量更新方式。它能避免最近少量聊天记录直接冲掉旧人格，但仍可能重复分析已经分析过的消息。

后续如果需要进一步节省 Token、减少重复样本，可以改成基于 `last_message_id` 或 `last_update_at` 的精细增量。

## 目标

- 每个人格记录上次分析到哪一条消息。
- 下次更新时只抓取上次之后的新消息。
- 新消息不足时跳过更新，避免用重复内容消耗 Token。
- 保留旧人格作为长期画像，新消息只做补充和修正。

## 推荐数据字段

在 `personalities.json` 的每个人格中增加：

```json
{
  "last_message_id": "123456789",
  "last_message_time": "2026-05-15T02:30:00",
  "last_update_at": "2026-05-15T03:00:00",
  "update_message_count": 86
}
```

字段含义：

- `last_message_id`：本次分析过的最新群消息 ID，优先使用。
- `last_message_time`：最新消息时间，用于消息 ID 不可靠时兜底。
- `last_update_at`：人格文件上次完成更新的时间。
- `update_message_count`：本次增量实际使用的消息数，方便排查。

## 抓取逻辑

优先方案：

1. 从 `personalities.json` 读取 `last_message_id`。
2. 调用 `get_group_msg_history` 从最新消息往前扫。
3. 收集目标用户发言，直到遇到 `last_message_id` 停止。
4. 将收集到的新消息按时间正序送入分析。
5. 更新人格后写入新的 `last_message_id`。

兜底方案：

1. 如果没有 `last_message_id`，读取 `last_update_at` 或 `last_message_time`。
2. 只保留消息时间晚于该时间的目标用户发言。
3. 如果适配器拿不到消息时间，则退回当前“最新 N 条 + 旧人格”的方案。

## 跳过条件

建议增加最小更新样本数，例如：

```text
min_update_messages = 20
```

如果本次新增消息少于该值，直接跳过，不调用模型。

## 风险

- OneBot 不同实现里的 `message_id` / `message_seq` 语义可能不完全一致，需要实测。
- 如果历史接口只能按页往前翻，仍需要扫描一段历史才能找到断点。
- 用户撤回、消息清理、跨适配器迁移可能导致 `last_message_id` 找不到，需要兜底到时间或最新 N 条。

## 建议落地顺序

1. 先在抓取结果中保留原始消息的 `message_id`、`time`、纯文本。
2. 人格保存时写入本次使用的最新 `message_id` 和 `time`。
3. 自动更新时优先使用断点抓取。
4. 手动 `克隆 <目标>` 保持当前逻辑；必要时增加 `--since-last` 或默认同人增量只抓新消息。
