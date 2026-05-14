"""
群友人格克隆插件 - Clone Personality Plugin v2.0
================================================
- 群聊：克隆 @群友
- 私聊：克隆 <群号> <群友QQ>
- 管理员开关：admin_only_inject 控制注入权限
- 重复 ID 自动覆盖
"""

import json
import os
import re
import asyncio
from typing import Optional, Dict, List, Any
from datetime import datetime, timedelta

from astrbot.api.event import filter, AstrMessageEvent, MessageChain
from astrbot.api.message_components import Plain, At
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star, register
from astrbot.api import logger

# ─── 数据存储路径 ────────────────────────────────────────
PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
PERSONALITIES_FILE = os.path.join(PLUGIN_DIR, "personalities.json")
ACTIVE_PERSONA_FILE = os.path.join(PLUGIN_DIR, "active_persona.txt")
ACTIVE_SESSIONS_FILE = os.path.join(PLUGIN_DIR, "active_sessions.json")
CONFIG_FILE = os.path.join(PLUGIN_DIR, "config.json")


# ─── 配置管理 ────────────────────────────────────────────
DEFAULT_CONFIG = {
    "admin_only_inject": True,   # True=仅管理员可注入设定, False=所有人可注入
    "llm": {
        "provider_id": "",
    },
    "message": {
        "initial_days": 30,
        "fallback_days": 90,
        "history_slice": ":100",
        "max_fetch_rounds": 50,
        "per_query_count": 200,
        "max_analysis_messages": 80,
        "max_message_chars": 180,
        "max_prompt_chars": 12000,
    },
    "auto_update": {
        "frequency_days": 0,
        "check_interval_minutes": 60,
    },
    "persona": {
        "system_prompt_prefix": (
            "像真人群友一样短句回复，默认只回 1-3 句，尽量 80 字以内。\n"
            "别自我介绍，别解释设定，别科普腔。\n"
            "不要写作文，不要每轮都生成完整闭合段落，不要先总结再解释再升华。\n"
            "人类口语是开放的、有互动的、可以不完整的。\n"
            "不要用“简单说”“本质上”“总结一下”“对，这就像……”这类助手式承接。\n"
            "能一句话说完就别展开；除非用户明确要求详细、展开、分条、认真分析，否则禁止长篇、禁止 Markdown 分点、禁止总结式小作文。\n"
            "直接接话，少铺垫，少闭环，像活人。"
        ),
    },
}


def load_config() -> Dict:
    """加载插件配置"""
    config = DEFAULT_CONFIG.copy()
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
                config.update(saved)
        except (json.JSONDecodeError, IOError):
            logger.warning("插件配置文件损坏，使用默认配置")
    return config


def save_config(config: Dict):
    """保存插件配置"""
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


# ─── 人格数据管理 ────────────────────────────────────────
def load_personalities() -> Dict[str, Dict]:
    if os.path.exists(PERSONALITIES_FILE):
        try:
            with open(PERSONALITIES_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            logger.warning("人格数据文件损坏，重置为空")
    return {}


def save_personalities(data: Dict[str, Dict]):
    with open(PERSONALITIES_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_active_persona() -> Optional[str]:
    if os.path.exists(ACTIVE_PERSONA_FILE):
        try:
            with open(ACTIVE_PERSONA_FILE, "r", encoding="utf-8") as f:
                return f.read().strip()
        except IOError:
            pass
    return None


def set_active_persona(persona_id: Optional[str]):
    if persona_id:
        with open(ACTIVE_PERSONA_FILE, "w", encoding="utf-8") as f:
            f.write(persona_id)
    else:
        if os.path.exists(ACTIVE_PERSONA_FILE):
            os.remove(ACTIVE_PERSONA_FILE)


def load_active_sessions() -> Dict[str, str]:
    if os.path.exists(ACTIVE_SESSIONS_FILE):
        try:
            with open(ACTIVE_SESSIONS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            logger.warning("会话人格数据文件损坏，重置为空")
    return {}


def save_active_sessions(data: Dict[str, str]):
    with open(ACTIVE_SESSIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ─── 主插件类 ────────────────────────────────────────────
@register(
    "clone_personality",
    "AstrBot Plugin",
    "提取群友聊天记录克隆人格，支持群聊/私聊，管理员开关",
    "2.0.0",
)
class ClonePersonalityPlugin(Star):

    def __init__(self, context: Context, config: Optional[Any] = None) -> None:
        super().__init__(context)
        self.plugin_cfg = config
        self._auto_update_task = None
        self._last_event = None

    async def initialize(self):
        logger.info("群友人格克隆插件 v2.0 已加载")
        self._auto_update_task = asyncio.create_task(self._auto_update_loop())

    async def terminate(self):
        if self._auto_update_task:
            self._auto_update_task.cancel()
            try:
                await self._auto_update_task
            except asyncio.CancelledError:
                pass

    def _remember_event(self, event: AstrMessageEvent) -> None:
        if hasattr(event, "bot") and hasattr(event.bot, "api"):
            self._last_event = event

    def _get_setting(self, path: str, default: Any = None) -> Any:
        """读取 WebUI 插件配置，缺失时回退到本地 config.json/default。"""
        config_sources = [self.plugin_cfg, load_config(), DEFAULT_CONFIG]
        keys = path.split(".")
        for source in config_sources:
            current = source
            try:
                for key in keys:
                    if isinstance(current, dict):
                        current = current[key]
                    elif hasattr(current, "get"):
                        current = current.get(key)
                    else:
                        current = getattr(current, key)
                if current is not None:
                    return current
            except Exception:
                continue
        return default

    def _set_runtime_setting(self, key: str, value: Any) -> None:
        """尽量同步运行时插件配置；持久化仍交给 WebUI 或本地 config.json。"""
        try:
            if isinstance(self.plugin_cfg, dict):
                self.plugin_cfg[key] = value
            elif hasattr(self.plugin_cfg, "__setitem__"):
                self.plugin_cfg[key] = value
            elif hasattr(self.plugin_cfg, "set"):
                self.plugin_cfg.set(key, value)
        except Exception:
            pass

    async def _auto_update_loop(self):
        while True:
            try:
                interval = int(self._get_setting(
                    "auto_update.check_interval_minutes",
                    60,
                ))
                await asyncio.sleep(max(interval, 5) * 60)
                await self._auto_update_due_personalities()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"定期更新人格失败: {e}")

    async def _auto_update_due_personalities(self) -> None:
        frequency_days = int(self._get_setting("auto_update.frequency_days", 0))
        if frequency_days <= 0:
            return
        if not self._last_event:
            logger.debug("定期更新跳过：还没有可用的 bot 事件上下文")
            return

        personalities = load_personalities()
        if not personalities:
            return

        now = datetime.now()
        due_items = []
        for pid, data in personalities.items():
            group_id = str(data.get("group_id", "")).strip()
            user_id = str(data.get("user_id", "")).strip()
            if not group_id or not user_id:
                continue

            last_text = (
                data.get("last_auto_update_at")
                or data.get("updated_at")
                or data.get("created_at")
                or ""
            )
            try:
                last_at = datetime.fromisoformat(str(last_text))
            except Exception:
                last_at = datetime.min

            if now - last_at >= timedelta(days=frequency_days):
                due_items.append((pid, data))

        if not due_items:
            return

        logger.info(f"开始定期更新 {len(due_items)} 个已保存人格")
        updated_by_group: Dict[str, List[str]] = {}
        for pid, data in due_items:
            updated = await self._refresh_personality(pid, data)
            if not updated:
                continue
            personalities[pid] = updated
            group_id = str(updated.get("group_id", ""))
            name = updated.get("user_name", pid)
            updated_by_group.setdefault(group_id, []).append(str(name))

        if updated_by_group:
            save_personalities(personalities)
            for group_id, names in updated_by_group.items():
                await self._send_auto_update_notice(group_id, names)

    async def _refresh_personality(self, pid: str, data: Dict) -> Optional[Dict]:
        try:
            group_id = int(data.get("group_id"))
            user_id = int(data.get("user_id"))
            target_name = data.get("user_name", pid)

            end_time = datetime.now()
            initial_days = int(self._get_setting("message.initial_days", 30))
            history_slice = str(self._get_setting("message.history_slice", ":100"))

            messages = await self._fetch_user_messages(
                event=self._last_event,
                user_id=user_id,
                group_id=group_id,
                target_name=target_name,
                start=(end_time - timedelta(days=initial_days)).strftime("%Y-%m-%d"),
                end=end_time.strftime("%Y-%m-%d %H:%M"),
                slice=history_slice,
            )
            if isinstance(messages, dict):
                messages = messages.get("messages", [])
            if not messages:
                return None

            personality = await self._analyze_personality(
                self._last_event,
                messages,
                target_name,
            )
            if not personality:
                return None

            personality["user_id"] = str(user_id)
            personality["user_name"] = target_name
            personality["persona_name"] = data.get("persona_name", f"{group_id}{target_name}")
            personality["group_id"] = str(group_id)
            personality["created_at"] = data.get("created_at", datetime.now().isoformat())
            personality["updated_at"] = datetime.now().isoformat()
            personality["last_auto_update_at"] = datetime.now().isoformat()
            personality["message_count"] = len(messages)

            personalities = load_personalities()
            personalities[pid] = personality
            save_personalities(personalities)

            await self._inject_to_astrbot_persona(self._last_event, personality, target_name)
            return personality
        except Exception as e:
            logger.warning(f"定期更新人格 {pid} 失败: {e}")
            return None

    async def _send_auto_update_notice(self, group_id: str,
                                       names: List[str]) -> None:
        if not self._last_event or not hasattr(self._last_event, "bot"):
            return
        if not hasattr(self._last_event.bot, "api"):
            return

        display = "、".join(names[:5])
        if len(names) > 5:
            display += f" 等 {len(names)} 个"
        text = f"🧪 {display} 已定期蒸馏完毕，味儿续上了。"
        try:
            await self._last_event.bot.api.call_action(
                "send_group_msg",
                group_id=int(group_id),
                message=text,
            )
        except Exception as e:
            logger.warning(f"发送定期更新提醒失败: {e}")

    def _extract_clone_target_arg(self, parts: List[str],
                                  skip_values: Optional[List[str]] = None
                                  ) -> Optional[str]:
        skip_values = skip_values or []
        for part in parts[1:]:
            if part in ("-f", "--force"):
                continue
            normalized = part.strip()
            if normalized in skip_values:
                continue
            return part.strip()
        return None

    def _build_persona_id(self, target_name: str, target_uid: Any) -> str:
        base = str(target_name or "").strip()
        if not base or base.startswith("QQ"):
            base = str(target_uid)
        base = base.lstrip("@").strip()
        # AstrBot persona_id 是唯一 ID，避免空格和路径类字符带来配置/命令歧义。
        base = re.sub(r"[\\/:*?\"<>|\s]+", "_", base)
        return base or str(target_uid)

    def _get_event_group_id(self, event: AstrMessageEvent) -> Optional[str]:
        """兼容不同 AstrBot/适配器版本的群号位置。"""
        candidates = [
            getattr(event, "group_id", None),
            getattr(getattr(event, "message_obj", None), "group_id", None),
        ]

        if hasattr(event, "get_group_id"):
            try:
                candidates.append(event.get_group_id())
            except Exception:
                pass

        raw_message = getattr(getattr(event, "message_obj", None), "raw_message", None)
        if isinstance(raw_message, dict):
            candidates.append(raw_message.get("group_id"))

        for candidate in candidates:
            if candidate:
                value = str(candidate).strip()
                if value and value.lower() != "none":
                    return value

        umo = str(getattr(event, "unified_msg_origin", "") or "")
        parts = umo.split(":")
        if len(parts) >= 3 and "group" in parts[1].lower():
            return parts[-1]

        return None

    def _extract_at_targets(self, event: AstrMessageEvent) -> List[Dict[str, str]]:
        """从消息链或日志式文本中提取 At 目标。"""
        targets = []
        message_chain = getattr(getattr(event, "message_obj", None), "message", []) or []
        for comp in message_chain:
            if isinstance(comp, At):
                qq = getattr(comp, "qq", None)
                name = getattr(comp, "name", None)
            else:
                qq = (
                    getattr(comp, "qq", None)
                    or getattr(comp, "user_id", None)
                    or getattr(comp, "id", None)
                )
                name = getattr(comp, "name", None)

            if qq:
                targets.append({"qq": str(qq), "name": str(name or qq)})
                continue

            match = re.search(r"\[At:(\d+)\]", repr(comp))
            if match:
                qq = match.group(1)
                targets.append({"qq": qq, "name": qq})

        text = str(getattr(event, "message_str", "") or "")
        for qq in re.findall(r"\[At:(\d+)\]", text):
            if not any(t["qq"] == qq for t in targets):
                targets.append({"qq": qq, "name": qq})

        return targets

    def _get_bot_self_id(self, event: AstrMessageEvent) -> Optional[str]:
        candidates = [
            getattr(getattr(event, "message_obj", None), "self_id", None),
            getattr(event, "self_id", None),
        ]

        if hasattr(event, "get_self_id"):
            try:
                candidates.append(event.get_self_id())
            except Exception:
                pass

        raw_message = getattr(getattr(event, "message_obj", None), "raw_message", None)
        if isinstance(raw_message, dict):
            candidates.append(raw_message.get("self_id"))

        for candidate in candidates:
            if candidate:
                value = str(candidate).strip()
                if value and value.lower() != "none":
                    return value
        return None

    def _is_bot_mentioned(self, event: AstrMessageEvent) -> bool:
        bot_id = self._get_bot_self_id(event)
        if not bot_id:
            return False
        return any(target["qq"] == bot_id for target in self._extract_at_targets(event))

    def _get_session_keys(self, event: AstrMessageEvent) -> List[str]:
        keys = []
        unified = str(getattr(event, "unified_msg_origin", "") or "").strip()
        if unified:
            keys.append(f"umo:{unified}")

        group_id = self._get_event_group_id(event)
        if group_id:
            keys.append(f"group:{group_id}")
        else:
            try:
                sender_id = event.get_sender_id()
            except Exception:
                sender_id = None
            if sender_id:
                keys.append(f"private:{sender_id}")

        return list(dict.fromkeys(keys))

    def _get_session_active_persona(self, event: AstrMessageEvent) -> Optional[str]:
        sessions = load_active_sessions()
        for key in self._get_session_keys(event):
            persona_id = sessions.get(key)
            if persona_id:
                return persona_id
        return None

    def _set_session_active_persona(self, event: AstrMessageEvent,
                                    persona_id: Optional[str]) -> None:
        keys = self._get_session_keys(event)
        if not keys:
            logger.warning("无法生成会话 key，未保存会话人格")
            return
        sessions = load_active_sessions()
        for key in keys:
            if persona_id:
                sessions[key] = persona_id
            else:
                sessions.pop(key, None)
        save_active_sessions(sessions)
        logger.info(f"已保存会话人格: {keys} -> {persona_id or 'default'}")

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_plain_text_command(self, event: AstrMessageEvent):
        """
        兼容不带 AstrBot 唤醒前缀的中文指令。
        例如：克隆 477065120 2054716346
        """
        self._remember_event(event)
        text = event.message_str.strip()
        if not text or text.startswith("/"):
            return
        if self._get_event_group_id(event) and not self._is_bot_mentioned(event):
            return
        command_text = self._strip_bot_mentions(text)

        handlers = (
            ("克隆", self.clone_personality),
            ("管理员注入开关", self.toggle_admin_inject),
            ("人格切换", self.switch_personality),
            ("人格列表", self.list_personalities),
            ("人格详情", self.personality_detail),
            ("人格删除", self.delete_personality),
        )
        for command_name, handler in handlers:
            if command_text == command_name or command_text.startswith(f"{command_name} "):
                async for result in handler(event):
                    yield result
                event.stop_event()
                return

        async for result in self._handle_active_persona_chat(event):
            yield result

    @filter.on_llm_request(priority=10)
    async def apply_active_persona_to_llm(self, event: AstrMessageEvent,
                                          req: ProviderRequest):
        self._remember_event(event)
        persona_id = self._get_session_active_persona(event)
        if not persona_id:
            return
        logger.info(f"主动人格会话命中: {self._get_session_keys(event)} -> {persona_id}")

        personalities = load_personalities()
        personality = personalities.get(persona_id)
        if not personality:
            return

        target_name = personality.get("user_name", persona_id)
        persona_text = self._build_persona_text(personality, target_name)
        req.system_prompt = (
            f"{persona_text}\n\n"
            "【回复长度硬约束】默认 1-3 句，80 字以内；"
            "除非用户明确要求详细、展开、分条，否则不要 Markdown 分点，不要长篇锐评，不要总结式小作文。\n\n"
            f"{req.system_prompt or ''}"
        )
        logger.info(f"已将会话人格注入 LLM 请求: {persona_id}")

    # ════════════════════════════════════════════════════
    # 1. 克隆指令
    # ════════════════════════════════════════════════════
    @filter.command("clone")
    @filter.command("克隆")
    async def clone_personality(self, event: AstrMessageEvent):
        """
        克隆群友人格。
        群聊用法：克隆 @群友 / 克隆 <群名片或昵称>
        私聊用法：克隆 <群号> <群友QQ/@群友/群名片或昵称>
        """
        self._remember_event(event)
        text = event.message_str.strip()
        parts = text.split()
        force_refresh = "-f" in parts or "--force" in parts

        # ── 判断是群聊还是私聊 ──
        event_group_id = self._get_event_group_id(event)
        is_group = bool(event_group_id)
        if is_group and not self._is_bot_mentioned(event):
            return

        if is_group:
            # 群聊模式：从 @ 或昵称获取目标
            bot_id = self._get_bot_self_id(event)
            at_targets = [
                target for target in self._extract_at_targets(event)
                if not bot_id or target["qq"] != bot_id
            ]
            group_id = str(event_group_id)
            skip_values = [f"[At:{bot_id}]"] if bot_id else []
            target_arg = self._extract_clone_target_arg(parts, skip_values)

            if at_targets:
                target_uid = at_targets[0]["qq"]
                target_name = await self._get_group_member_display_name(
                    event,
                    int(group_id),
                    target_uid,
                ) or at_targets[0]["name"] or str(target_uid)
            elif target_arg:
                target_uid = await self._resolve_group_member_id(
                    event,
                    int(group_id),
                    target_arg.lstrip("@"),
                )
                if not target_uid:
                    yield event.plain_result(
                        f"❌ 未在本群找到「{target_arg}」，请改用：克隆 @群友 或 克隆 <QQ号>"
                    )
                    return
                target_name = target_arg.lstrip("@")
            else:
                yield event.plain_result(
                    "群聊用法：克隆 @群友 或 克隆 <群名片/昵称>\n"
                    "私聊用法：克隆 <群号> <群友QQ/@群友>"
                )
                return

            pid = self._build_persona_id(target_name, target_uid)
            persona_name = f"{group_id}{target_name}"

        else:
            # 私聊模式：克隆 <群号> <群友QQ/@群友>
            non_flag_parts = [p for p in parts if p != "-f" and p != "--force"]
            if len(non_flag_parts) < 3:
                yield event.plain_result(
                    "私聊用法：克隆 <群号> <群友QQ/@群友>\n"
                    "例如：克隆 123456789 987654321"
                )
                return

            group_id = non_flag_parts[1]
            target_uid_raw = non_flag_parts[2]
            at_targets = self._extract_at_targets(event)

            # 校验群号
            if not group_id.isdigit():
                yield event.plain_result(f"❌ 群号格式错误：{group_id}，应为纯数字")
                return
            if target_uid_raw.isdigit():
                target_uid = target_uid_raw
                target_name = await self._get_group_member_display_name(
                    event,
                    int(group_id),
                    target_uid,
                ) or f"QQ{target_uid}"
            elif at_targets:
                target_uid = str(at_targets[0]["qq"])
                target_name = await self._get_group_member_display_name(
                    event,
                    int(group_id),
                    target_uid,
                ) or at_targets[0]["name"] or str(target_uid)
            elif target_uid_raw.startswith("@"):
                target_uid = await self._resolve_group_member_id(
                    event,
                    int(group_id),
                    target_uid_raw[1:],
                )
                if not target_uid:
                    yield event.plain_result(
                        f"❌ 无法从 {target_uid_raw} 识别 QQ号，请改用：克隆 {group_id} <QQ号>"
                    )
                    return
                target_name = target_uid_raw[1:] or f"QQ{target_uid}"
            else:
                target_uid = await self._resolve_group_member_id(
                    event,
                    int(group_id),
                    target_uid_raw,
                )
                if target_uid:
                    target_name = target_uid_raw
                else:
                    yield event.plain_result(f"❌ QQ号或群名片错误：{target_uid_raw}")
                    return

            group_id = str(int(group_id))  # 标准化
            pid = self._build_persona_id(target_name, target_uid)
            persona_name = f"{group_id}{target_name}"

        # ── 重复 ID 直接覆盖（不弹提示） ──
        personalities = load_personalities()

        # ── 获取聊天记录 ──
        yield event.plain_result(f"🤏 一把抓住 {target_name}(群 {group_id})，顷刻炼化...")

        try:
            end_time = datetime.now()
            initial_days = int(self._get_setting("message.initial_days", 30))
            fallback_days = int(self._get_setting("message.fallback_days", 90))
            history_slice = str(self._get_setting("message.history_slice", ":100"))

            start_time = end_time - timedelta(days=initial_days)

            messages = await self._fetch_user_messages(
                event=event,
                user_id=int(target_uid),
                group_id=int(group_id),
                target_name=target_name,
                start=start_time.strftime("%Y-%m-%d"),
                end=end_time.strftime("%Y-%m-%d %H:%M"),
                slice=history_slice,
            )
            if isinstance(messages, dict):
                messages = messages.get("messages", [])

            if not messages:
                start_time = end_time - timedelta(days=fallback_days)
                messages = await self._fetch_user_messages(
                    event=event,
                    user_id=int(target_uid),
                    group_id=int(group_id),
                    target_name=target_name,
                    start=start_time.strftime("%Y-%m-%d"),
                    end=end_time.strftime("%Y-%m-%d %H:%M"),
                    slice=history_slice,
                )
                if isinstance(messages, dict):
                    messages = messages.get("messages", [])

            if not messages:
                yield event.plain_result(
                    f"❌ 未找到 {target_name} 在群 {group_id} 的聊天记录。\n"
                    f"请确保该群友在该群发过消息。"
                )
                return

            yield event.plain_result(f"😏 获取到 {len(messages)} 条消息，我可要好好蒸馏你了...")

        except Exception as e:
            logger.error(f"获取聊天记录失败: {e}")
            yield event.plain_result(f"❌ 获取聊天记录失败: {str(e)}")
            return

        # ── 调用大模型分析 ──
        personality = await self._analyze_personality(
            event,
            messages,
            target_name,
        )
        if not personality:
            yield event.plain_result("❌ 人格分析失败，请稍后重试。")
            return

        # ── 保存人格（覆盖旧数据） ──
        personality["user_id"] = str(target_uid)
        personality["user_name"] = target_name
        personality["persona_name"] = persona_name
        personality["group_id"] = str(group_id)
        personality["created_at"] = datetime.now().isoformat()
        personality["message_count"] = len(messages)

        personalities[pid] = personality
        save_personalities(personalities)

        # ── 判断是否注入设定 ──
        admin_only = bool(self._get_setting("admin_only_inject", True))
        is_admin = await self._is_admin(event)

        can_inject = (not admin_only) or (admin_only and is_admin)

        summary = self._build_persona_result_message(
            pid=pid,
            persona_name=persona_name,
            target_name=target_name,
            message_count=len(messages),
            personality=personality,
        )

        if can_inject:
            success = await self._inject_to_astrbot_persona(
                event, personality, target_name
            )
            if success:
                summary += f"\n\n[系统] 已创建/更新 AstrBot 人格：{pid}"
            else:
                summary += f"\n\n[系统] 人格已保存，但创建/更新 AstrBot 人格失败。"
        else:
            summary += f"\n\n[系统] 人格已保存，当前设置为仅管理员可创建/更新 AstrBot 人格。"

        if not await self._send_forward_message(event, group_id, summary):
            yield event.plain_result(summary)

    # ════════════════════════════════════════════════════
    # 2. 管理员注入开关
    # ════════════════════════════════════════════════════
    @filter.command("管理员注入开关")
    async def toggle_admin_inject(self, event: AstrMessageEvent):
        """
        切换「仅管理员可注入设定」开关（仅管理员可用）
        用法：管理员注入开关
             管理员注入开关 on
             管理员注入开关 off
        """
        is_admin = await self._is_admin(event)
        if not is_admin:
            yield event.plain_result("❌ 只有管理员可以设置此选项。")
            return

        text = event.message_str.strip()
        parts = text.split(maxsplit=1)
        arg = parts[1].strip().lower() if len(parts) > 1 else "toggle"

        config = load_config()
        current = bool(self._get_setting("admin_only_inject", True))

        if arg in ("on", "开启", "true", "1"):
            config["admin_only_inject"] = True
            new_status = "ON"
        elif arg in ("off", "关闭", "false", "0"):
            config["admin_only_inject"] = False
            new_status = "OFF"
        else:
            config["admin_only_inject"] = not current
            new_status = "ON" if config["admin_only_inject"] else "OFF"

        self._set_runtime_setting("admin_only_inject", config["admin_only_inject"])
        save_config(config)

        if config["admin_only_inject"]:
            yield event.plain_result(
                f"🔒 管理员注入开关已设为 {new_status}\n"
                f"当前状态：仅管理员可使用「克隆」将人格注入 AstrBot 设定。"
            )
        else:
            yield event.plain_result(
                f"🔓 管理员注入开关已设为 {new_status}\n"
                f"当前状态：所有用户使用「克隆」后都会自动将人格注入 AstrBot 设定。"
            )

    # ════════════════════════════════════════════════════
    # 3. 人格切换
    # ════════════════════════════════════════════════════
    @filter.command("人格切换")
    async def switch_personality(self, event: AstrMessageEvent):
        text = self._strip_bot_mentions(event.message_str.strip())
        parts = text.split(maxsplit=1)

        if len(parts) < 2:
            yield event.plain_result("用法：人格切换 <人格ID> 或 人格切换 default（恢复默认）")
            return

        arg = parts[1].strip()

        if arg.lower() in ("default", "默认"):
            set_active_persona(None)
            self._set_session_active_persona(event, None)
            yield event.plain_result("🔄 已恢复为默认人格。")
            return

        if arg.lower() in ("list", "列表"):
            yield await self.list_personalities(event)
            return

        personalities = load_personalities()
        if arg not in personalities:
            yield event.plain_result(f"❌ 未找到人格「{arg}」，使用「人格列表」查看所有人格。")
            return

        set_active_persona(arg)
        self._set_session_active_persona(event, arg)
        target_name = personalities[arg].get("user_name", arg)
        summary_text = personalities[arg].get("summary", "")

        success = await self._inject_to_astrbot_persona(
            event, personalities[arg], target_name
        )
        if success:
            success = await self._bind_current_conversation_persona(event, arg)

        if success:
            yield event.plain_result(
                f"🔄 已切换至人格「{target_name}」({arg})\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"{summary_text}\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"✅ 已绑定插件会话人格，后续 @bot 对话会注入该人格。"
            )
        else:
            yield event.plain_result(
                f"🔄 已切换至人格「{target_name}」({arg})\n"
                f"{summary_text}\n"
                f"⚠️ 未注入系统设定，仅在对话中参考此人格风格。"
            )

    # ════════════════════════════════════════════════════
    # 4. 人格列表
    # ════════════════════════════════════════════════════
    @filter.command("人格列表")
    async def list_personalities(self, event: AstrMessageEvent):
        personalities = load_personalities()
        if not personalities:
            yield event.plain_result(
                "📭 暂无已克隆的人格。\n"
                "💡 使用「克隆 @群友」来克隆一位群友的人格。"
            )
            return

        active = get_active_persona()
        lines = ["📋 已克隆的人格列表：", "━━━━━━━━━━━━━━━━"]
        for pid, data in personalities.items():
            marker = " 👈 当前" if pid == active else ""
            name = data.get("persona_name", data.get("user_name", "未知"))
            msg_cnt = data.get("message_count", 0)
            created = data.get("created_at", "")[:10]
            gid = data.get("group_id", "")
            lines.append(f"🆔 {pid}{marker}")
            lines.append(f"   👤 {name} | 群 {gid} | 📊 {msg_cnt}条 | 📅 {created}")
        lines.append("━━━━━━━━━━━━━━━━")
        lines.append("💡 使用「人格切换 <人格ID>」切换人格")

        yield event.plain_result("\n".join(lines))

    # ════════════════════════════════════════════════════
    # 5. 人格详情
    # ════════════════════════════════════════════════════
    @filter.command("人格详情")
    async def personality_detail(self, event: AstrMessageEvent):
        text = event.message_str.strip()
        parts = text.split(maxsplit=1)

        if len(parts) < 2:
            yield event.plain_result("用法：人格详情 <人格ID>")
            return

        pid = parts[1].strip()
        personalities = load_personalities()
        if pid not in personalities:
            yield event.plain_result(f"❌ 未找到人格「{pid}」")
            return

        data = personalities[pid]
        active = get_active_persona()
        is_active = " 👈 当前激活" if pid == active else ""

        lines = [
            f"🧬 人格详情{is_active}",
            f"━━━━━━━━━━━━━━━━",
            f"🆔 ID: {pid}",
            f"👤 名称: {data.get('persona_name', data.get('user_name', '未知'))}",
            f"🎯 目标: {data.get('user_name', '未知')}",
            f"📊 分析消息数: {data.get('message_count', 0)} 条",
            f"📅 创建时间: {data.get('created_at', '未知')[:19]}",
            f"━━━━━━━━━━━━━━━━",
            f"📝 人格摘要:",
            f"{data.get('summary', '无')}",
        ]

        traits = data.get("traits", {})
        if traits:
            lines.extend(["", "🎭 性格特征:"])
            for k, v in traits.items():
                lines.append(f"  • {k}: {v}")

        phrases = data.get("common_phrases", [])
        if phrases:
            lines.extend(["", "💬 常用表达:"])
            for p in phrases[:5]:
                lines.append(f"  • \"{p}\"")

        style = data.get("speaking_style", "")
        if style:
            lines.extend(["", "🎙️ 说话风格:", f"  {style}"])

        lines.append("")
        lines.append("💡 使用「人格切换 %s」切换" % pid)

        yield event.plain_result("\n".join(lines))

    # ════════════════════════════════════════════════════
    # 6. 人格删除（管理员）
    # ════════════════════════════════════════════════════
    @filter.command("人格删除")
    async def delete_personality(self, event: AstrMessageEvent):
        is_admin = await self._is_admin(event)
        if not is_admin:
            yield event.plain_result("❌ 只有管理员可以删除人格。")
            return

        text = event.message_str.strip()
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            yield event.plain_result("用法：人格删除 <人格ID>")
            return

        pid = parts[1].strip()
        personalities = load_personalities()
        if pid not in personalities:
            yield event.plain_result(f"❌ 未找到人格「{pid}」")
            return

        name = personalities[pid].get("user_name", pid)
        del personalities[pid]
        save_personalities(personalities)

        active = get_active_persona()
        if active == pid:
            set_active_persona(None)

        yield event.plain_result(f"🗑️ 已删除人格「{name}」({pid})")

    # ════════════════════════════════════════════════════
    # 辅助方法
    # ════════════════════════════════════════════════════

    async def _fetch_user_messages(self, event, user_id: int, group_id: int,
                                   target_name: str,
                                   start: str, end: str,
                                   slice: str = ":100") -> Any:
        """获取指定用户的聊天记录"""
        messages = []

        try:
            history_result = await self._fetch_user_messages_from_group_history(
                event,
                user_id=user_id,
                group_id=group_id,
                target_name=target_name,
            )
            messages = history_result.get("messages", [])
            if messages:
                return history_result

            result = await self._search_history(
                query=None,
                user_id=user_id,
                group_id=group_id,
                start=start,
                end=end,
                slice=slice
            )

            if result and isinstance(result, list):
                for item in result:
                    if isinstance(item, dict):
                        msg_text = (item.get("message", "")
                                    or item.get("content", ""))
                        if msg_text:
                            messages.append(str(msg_text))
                    elif isinstance(item, str):
                        messages.append(item)
            elif isinstance(result, str):
                for line in result.strip().split("\n"):
                    line = line.strip()
                    if line:
                        messages.append(line)
        except Exception as e:
            logger.error(f"获取用户消息失败: {e}")

        return messages

    async def _fetch_user_messages_from_group_history(self, event, user_id: int,
                                                      group_id: int,
                                                      target_name: str) -> Dict[str, List[str]]:
        """通过 aiocqhttp/OneBot 的 get_group_msg_history 扫描群历史。"""
        if not hasattr(event, "bot") or not hasattr(event.bot, "api"):
            return {"messages": []}

        max_rounds = int(self._get_setting("message.max_fetch_rounds", 50))
        per_query_count = int(self._get_setting("message.per_query_count", 200))
        max_count = int(self._get_setting("message.max_analysis_messages", 80))

        texts = []
        message_seq = 0
        bot_id = self._get_bot_self_id(event)
        for _ in range(max_rounds):
            try:
                result = await event.bot.api.call_action(
                    "get_group_msg_history",
                    group_id=group_id,
                    message_seq=message_seq,
                    count=per_query_count,
                    reverseOrder=True,
                )
            except Exception as e:
                logger.debug(f"get_group_msg_history 调用失败: {e}")
                return {"messages": []}

            group_messages = result.get("messages", []) if isinstance(result, dict) else []
            if not group_messages:
                break

            message_seq = group_messages[0].get("message_id", message_seq)
            for item in group_messages:
                sender = item.get("sender", {}) if isinstance(item, dict) else {}
                sender_id = str(sender.get("user_id", ""))
                if bot_id and sender_id == str(bot_id):
                    continue
                text = self._extract_plain_text_from_raw_message(item)
                if not text:
                    continue
                if self._is_plugin_command_text(text):
                    continue

                if sender_id == str(user_id):
                    texts.append(text)
                    if len(texts) >= max_count:
                        break

            if len(texts) >= max_count:
                break

        return {"messages": texts[:max_count]}

    def _extract_plain_text_from_raw_message(self, item: Dict[str, Any]) -> str:
        raw_message = item.get("message", "")
        if isinstance(raw_message, str):
            return raw_message.strip()
        if not isinstance(raw_message, list):
            return ""

        parts = []
        for seg in raw_message:
            if not isinstance(seg, dict):
                continue
            if seg.get("type") != "text":
                continue
            data = seg.get("data", {})
            text = data.get("text", "") if isinstance(data, dict) else ""
            if text:
                parts.append(str(text))
        return "".join(parts).strip()

    async def _resolve_group_member_id(self, event, group_id: int,
                                       name: str) -> Optional[str]:
        """在私聊中把 @昵称 文本尽力解析成群成员 QQ。"""
        name = name.strip()
        if not name:
            return None
        if name.isdigit():
            return name
        if not hasattr(event, "bot") or not hasattr(event.bot, "api"):
            return None

        try:
            result = await event.bot.api.call_action(
                "get_group_member_list",
                group_id=group_id,
            )
        except Exception as e:
            logger.debug(f"get_group_member_list 调用失败: {e}")
            return None

        if isinstance(result, list):
            members = result
        elif isinstance(result, dict):
            members = result.get("members", result.get("data", []))
        else:
            members = []
        candidates = []
        for member in members:
            if not isinstance(member, dict):
                continue
            user_id = member.get("user_id")
            names = [
                str(member.get("card", "")).strip(),
                str(member.get("nickname", "")).strip(),
                str(member.get("remark", "")).strip(),
            ]
            if user_id and name in names:
                return str(user_id)
            if user_id and any(n and name.lower() in n.lower() for n in names):
                candidates.append(str(user_id))

        return candidates[0] if len(candidates) == 1 else None

    async def _get_group_member_display_name(self, event, group_id: int,
                                             user_id: Any) -> Optional[str]:
        if not hasattr(event, "bot") or not hasattr(event.bot, "api"):
            return None

        try:
            result = await event.bot.api.call_action(
                "get_group_member_info",
                group_id=group_id,
                user_id=int(user_id),
                no_cache=False,
            )
        except Exception as e:
            logger.debug(f"get_group_member_info 调用失败: {e}")
            return None

        data = result.get("data", result) if isinstance(result, dict) else {}
        if not isinstance(data, dict):
            return None

        for key in ("card", "nickname", "remark"):
            value = str(data.get(key, "")).strip()
            if value:
                return value
        return None

    async def _search_history(self, query=None, user_id=None, group_id=None,
                              start=None, end=None, slice=":100"):
        """尝试调用 search_qq_chat_history 工具"""
        try:
            if (hasattr(self, 'agent')
                    and hasattr(self.agent, 'tool_manager')):
                tool_result = await self.agent.tool_manager.execute_tool(
                    tool_name="search_qq_chat_history",
                    params={
                        "query": query,
                        "user_id": user_id,
                        "group_id": group_id,
                        "start": start,
                        "end": end,
                        "slice": slice
                    }
                )
                if tool_result:
                    return tool_result
        except Exception as e:
            logger.debug(f"工具调用方式失败: {e}")

        return []

    async def _analyze_personality(self, event, messages: List[str],
                                   target_name: str) -> Optional[Dict]:
        """调用大模型分析人格特征"""
        sample = self._prepare_analysis_messages(messages)
        chat_text = "\n".join([f"- {m}" for m in sample])

        prompt = f"""你是一位人格分析专家。请分析以下 "{target_name}" 的聊天记录，提取其人格特征。

要求：
1. 严格基于提供的聊天记录进行分析
2. 分析要具体、生动、有血有肉，像一份可直接放进机器人人格设定里的模板
3. 不要泛泛而谈，要给出具体特征、触发条件、反应模式和边界
4. “骚话/爆点语录/行为范例”必须尽量摘原始聊天里的原话，不要为了好看自行编造
5. 骚话/爆点语录宁缺毋滥：只有明显有梗、有攻击性、有反差、有抽象感、有口癖或有传播感的句子才收录；普通陈述、无趣吐槽、泛泛观点不要硬凑
6. 如果原始聊天里没有足够爆点语录，signature_quotes 返回空数组 []，不要为了凑数量填普通句子

请按以下 JSON 格式输出（不要包含其他内容，只输出 JSON）：
{{
    "identity": "你现在的身份是QQ用户“{target_name}”。用一段 150-260 字描述TA是谁、圈层、性别/年龄感（只能推测时要含蓄）、核心气质、社交位置和最鲜明的人格反差。",
    "summary": "人格核心：一段 180-300 字的总述，描述其核心矛盾、价值取向、社交姿态、情绪底色和典型反应。",
    "speaking_style": [
        "说话风格与习惯 1",
        "说话风格与习惯 2",
        "说话风格与习惯 3",
        "说话风格与习惯 4",
        "说话风格与习惯 5"
    ],
    "values_and_boundaries": [
        "价值倾向与社交边界 1",
        "价值倾向与社交边界 2",
        "价值倾向与社交边界 3",
        "价值倾向与社交边界 4"
    ],
    "trigger_reactions": [
        "当遇到某类话题/情境时：会如何反应",
        "当遇到某类话题/情境时：会如何反应",
        "当遇到某类话题/情境时：会如何反应",
        "当遇到某类话题/情境时：会如何反应",
        "当遇到某类话题/情境时：会如何反应"
    ],
    "common_phrases": ["常用口头禅或高频短语（最多10个）"],
    "signature_quotes": ["从原始聊天记录中摘出的真正有记忆点的原话（0-6条，必须是原话，不要改写；没有足够爆点就返回空数组，不要硬凑）"],
    "behavior_examples": [
        "当某情境出现时，你会说：引用或贴近原话的行为范例",
        "当某情境出现时，你会说：引用或贴近原话的行为范例",
        "当某情境出现时，你会说：引用或贴近原话的行为范例"
    ],
    "avoidances": [
        "禁止项 1",
        "禁止项 2",
        "禁止项 3",
        "禁止项 4"
    ],
    "traits": {{}},
    "interests": ["从聊天中推断的兴趣爱好或话题偏好（最多8个）"],
    "emotional_pattern": "情绪模式：描述其情绪表达、脆弱点、攻击性、玩梗节奏或亲密关系里的反差。"
}}

以下是 "{target_name}" 的聊天记录：
{chat_text}
"""

        try:
            resp = await self._call_llm(event, prompt)
            if resp:
                return self._parse_llm_response(resp)

            logger.error("无法访问大模型接口")
            return None

        except Exception as e:
            logger.error(f"调用大模型分析人格失败: {e}")
            return None

    def _prepare_analysis_messages(self, messages: List[str]) -> List[str]:
        """按配置压缩聊天记录，避免超过模型上下文。"""
        max_count = int(self._get_setting("message.max_analysis_messages", 80))
        max_msg_chars = int(self._get_setting("message.max_message_chars", 180))
        max_prompt_chars = int(self._get_setting("message.max_prompt_chars", 12000))

        prepared = []
        used_chars = 0
        for raw in messages[:max_count]:
            msg = re.sub(r"\s+", " ", str(raw)).strip()
            if not msg:
                continue
            msg = re.sub(r"https?://\S+", "[链接]", msg)
            if len(msg) > max_msg_chars:
                msg = msg[:max_msg_chars] + "..."
            projected = used_chars + len(msg) + 3
            if projected > max_prompt_chars:
                break
            prepared.append(msg)
            used_chars = projected

        return prepared

    async def _call_llm(self, event, prompt: str):
        """优先使用 WebUI 指定 provider，缺失时回退当前会话/默认 LLM。"""
        provider_id = str(self._get_setting("llm.provider_id", "") or "").strip()

        if hasattr(self.context, "llm_generate"):
            chat_provider_id = provider_id or None
            if not chat_provider_id and hasattr(self.context, "get_current_chat_provider_id"):
                try:
                    chat_provider_id = await self.context.get_current_chat_provider_id(
                        event.unified_msg_origin
                    )
                except Exception:
                    chat_provider_id = None
            return await self.context.llm_generate(
                chat_provider_id=chat_provider_id,
                prompt=prompt,
            )

        provider = None
        if provider_id and hasattr(self.context, "get_provider_by_id"):
            provider = self.context.get_provider_by_id(provider_id)
        if provider is None and hasattr(self.context, "get_using_provider"):
            provider = self.context.get_using_provider()

        if provider and hasattr(provider, "text_chat"):
            try:
                return await provider.text_chat(
                    prompt=prompt,
                    session_id=None,
                    contexts=[],
                    image_urls=[],
                    func_tool=None,
                    system_prompt="",
                )
            except TypeError:
                return await provider.text_chat(prompt)

        if hasattr(self, 'llm') and self.llm:
            return await self.llm.text_chat(prompt)
        if hasattr(event, 'broadcast') and hasattr(event.broadcast, 'llm'):
            return await event.broadcast.llm.text_chat(prompt)
        if hasattr(event, 'llm'):
            return await event.llm.text_chat(prompt)
        return None

    async def _handle_active_persona_chat(self, event: AstrMessageEvent):
        event_group_id = self._get_event_group_id(event)
        if event_group_id and not self._is_bot_mentioned(event):
            return

        persona_id = self._get_session_active_persona(event)
        if not persona_id:
            return
        logger.info(f"LLM 请求人格会话命中: {self._get_session_keys(event)} -> {persona_id}")

        personalities = load_personalities()
        personality = personalities.get(persona_id)
        if not personality:
            return

        text = self._strip_bot_mentions(event.message_str.strip())
        if not text:
            return

        target_name = personality.get("user_name", persona_id)
        persona_prompt = self._build_persona_text(personality, target_name)
        prompt = (
            f"{persona_prompt}\n\n"
            f"用户消息：{text}\n\n"
            f"直接回这一句。默认 1-3 句，80 字以内，像群友聊天。"
            f"不要说明你是AI，不要解释人格设定，不要分点写小作文。"
        )

        resp = await self._call_llm(event, prompt)
        reply = self._extract_llm_text(resp)
        if reply:
            event.stop_event()
            yield event.plain_result(reply)

    def _strip_bot_mentions(self, text: str) -> str:
        return re.sub(r"\[At:\d+\]", "", text).strip()

    def _is_plugin_command_text(self, text: str) -> bool:
        text = self._strip_bot_mentions(str(text or "")).strip()
        command_names = (
            "克隆",
            "clone",
            "人格切换",
            "人格列表",
            "人格详情",
            "人格删除",
            "管理员注入开关",
        )
        return any(text == cmd or text.startswith(f"{cmd} ") for cmd in command_names)

    def _build_persona_result_message(self, pid: str, persona_name: str,
                                      target_name: str, message_count: int,
                                      personality: Dict) -> str:
        """用合并聊天记录风格输出人格描述，避免插件操作提示打断阅读。"""
        lines = [
            f"{persona_name}",
            f"人格ID：{pid}",
            f"目标：{target_name}",
            f"分析消息数：{message_count} 条",
        ]

        identity = str(personality.get("identity", "")).strip()
        if identity:
            lines.extend(["", identity])

        summary = str(personality.get("summary", "")).strip()
        if summary:
            lines.extend(["", summary])

        self._append_numbered_section(
            lines,
            "说话风格与习惯",
            personality.get("speaking_style", []),
        )
        self._append_numbered_section(
            lines,
            "价值倾向与社交边界",
            personality.get("values_and_boundaries", []),
        )
        self._append_numbered_section(
            lines,
            "触发条件与反应模式",
            personality.get("trigger_reactions", []),
        )

        phrases = personality.get("common_phrases", [])
        if phrases:
            lines.extend(["", "高频表达特征"])
            lines.extend([f"- {phrase}" for phrase in phrases])

        quotes = personality.get("signature_quotes", [])
        if quotes:
            lines.extend(["", "骚话 / 爆点语录"])
            lines.extend([f"- {quote}" for quote in quotes])

        self._append_numbered_section(
            lines,
            "行为范例",
            personality.get("behavior_examples", []),
        )
        self._append_numbered_section(
            lines,
            "禁止项",
            personality.get("avoidances", []),
        )

        return "\n".join(lines)

    def _append_numbered_section(self, lines: List[str], title: str,
                                 items: Any) -> None:
        if isinstance(items, str):
            items = [items] if items.strip() else []
        if not items:
            return

        lines.extend(["", title])
        for idx, item in enumerate(items, 1):
            lines.append(f"{idx}. {item}")

    async def _send_forward_message(self, event: AstrMessageEvent,
                                    group_id: str, text: str) -> bool:
        """优先用 QQ 合并转发发送长人格分析。"""
        if not hasattr(event, "bot") or not hasattr(event.bot, "api"):
            return False

        try:
            nodes = self._build_forward_nodes(text)
            event_group_id = self._get_event_group_id(event)

            if event_group_id:
                await event.bot.api.call_action(
                    "send_group_forward_msg",
                    group_id=int(group_id),
                    messages=nodes,
                )
                return True

            sender_id = event.get_sender_id() if hasattr(event, "get_sender_id") else None
            if sender_id:
                await event.bot.api.call_action(
                    "send_private_forward_msg",
                    user_id=int(sender_id),
                    messages=nodes,
                )
                return True
        except Exception as e:
            logger.warning(f"发送合并转发失败，回退普通文本: {e}")

        return False

    def _build_forward_nodes(self, text: str) -> List[Dict[str, Any]]:
        sections = [s.strip() for s in re.split(r"\n{2,}", text) if s.strip()]
        if not sections:
            sections = [text]

        nodes = []
        for idx, section in enumerate(sections, 1):
            title = "人格分析" if idx == 1 else "人格分析续"
            nodes.append({
                "type": "node",
                "data": {
                    "name": title,
                    "uin": "10000",
                    "content": [
                        {
                            "type": "text",
                            "data": {
                                "text": section,
                            },
                        }
                    ],
                },
            })
        return nodes

    def _parse_llm_response(self, resp) -> Optional[Dict]:
        """解析 LLM 返回的 JSON 人格数据"""
        text = self._extract_llm_text(resp)

        start = text.find('{')
        if start != -1:
            depth = 0
            for i in range(start, len(text)):
                if text[i] == '{':
                    depth += 1
                elif text[i] == '}':
                    depth -= 1
                    if depth == 0:
                        json_str = text[start:i+1]
                        try:
                            return self._normalize_personality(json.loads(json_str))
                        except json.JSONDecodeError:
                            logger.warning(f"JSON 解析失败")

        return {
            "summary": text[:200] if text else "分析失败",
            "identity": "",
            "traits": {},
            "speaking_style": "",
            "common_phrases": [],
            "signature_quotes": [],
            "values_and_boundaries": [],
            "trigger_reactions": [],
            "behavior_examples": [],
            "interests": [],
            "emotional_pattern": "",
            "reply_rules": [],
            "avoidances": [],
        }

    def _normalize_personality(self, data: Dict) -> Dict:
        data["signature_quotes"] = self._filter_signature_quotes(
            data.get("signature_quotes", [])
        )
        return data

    def _filter_signature_quotes(self, quotes: Any) -> List[str]:
        if not isinstance(quotes, list):
            return []

        kept = []
        seen = set()
        signal_patterns = (
            r"[？！!?]{1,}",
            r"(哈哈|笑死|绷|破防|发癫|封口费|V我|草|艹|操|妈的|卧槽|离谱|重口味|国宴|IRS|猫娘|黑丝|病娇)",
            r"(\d+\s*(块|元|万|k|K|w|W)|V\s*我\s*\d+)",
        )
        for raw in quotes:
            quote = re.sub(r"\s+", " ", str(raw)).strip(" -“”\"'")
            if len(quote) < 6 or len(quote) > 80:
                continue
            if quote in seen:
                continue
            if not any(re.search(pattern, quote, re.IGNORECASE) for pattern in signal_patterns):
                continue
            seen.add(quote)
            kept.append(quote)
            if len(kept) >= 6:
                break

        return kept

    def _extract_llm_text(self, resp) -> str:
        if resp is None:
            return ""

        for attr in ("completion_text", "text", "content"):
            value = getattr(resp, attr, None)
            if isinstance(value, str) and value.strip():
                return value.strip()

        result_chain = getattr(resp, "result_chain", None)
        chain = getattr(result_chain, "chain", None)
        if isinstance(chain, list):
            texts = []
            for comp in chain:
                value = getattr(comp, "text", None)
                if value:
                    texts.append(str(value))
            if texts:
                return "".join(texts).strip()

        if isinstance(resp, dict):
            for key in ("completion_text", "text", "content", "result"):
                value = resp.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()

        return str(resp).strip()

    async def _inject_to_astrbot_persona(self, event, personality: Dict,
                                          target_name: str) -> bool:
        """将人格注入 AstrBot 系统设定"""
        try:
            persona_text = self._build_persona_text(personality, target_name)

            persona_id = self._build_persona_id(
                personality.get("user_name", target_name),
                personality.get("user_id", target_name),
            )

            # 方法1: AstrBot v4 PersonaManager。只创建/更新，不自动切换会话人格。
            if hasattr(self.context, "persona_manager"):
                persona_mgr = self.context.persona_manager
                try:
                    existing = None
                    try:
                        existing = await self._maybe_await(
                            persona_mgr.get_persona(persona_id)
                        )
                    except Exception:
                        existing = None

                    if existing:
                        await self._maybe_await(
                            persona_mgr.update_persona(
                                persona_id=persona_id,
                                system_prompt=persona_text,
                                begin_dialogs=[],
                                tools=None,
                            )
                        )
                    else:
                        await self._maybe_await(
                            persona_mgr.create_persona(
                                persona_id=persona_id,
                                system_prompt=persona_text,
                                begin_dialogs=[],
                                tools=None,
                            )
                        )

                    logger.info(f"已创建/更新 AstrBot 人格: {persona_id}")
                    return True
                except Exception as e:
                    logger.warning(f"通过 PersonaManager 注入失败: {e}")

            # PersonaManager 不可用或失败时只保存本地文件，不声称已注入 AstrBot。
            persona_file = os.path.join(PLUGIN_DIR, "current_persona.txt")
            with open(persona_file, "w", encoding="utf-8") as f:
                f.write(persona_text)
            logger.info(f"人格已保存到 {persona_file}")
            return False

        except Exception as e:
            logger.error(f"注入人格失败: {e}")
            return False

    async def _maybe_await(self, value):
        if hasattr(value, "__await__"):
            return await value
        return value

    async def _bind_current_conversation_persona(self, event: AstrMessageEvent,
                                                 persona_id: str) -> bool:
        if not hasattr(self.context, "conversation_manager"):
            logger.warning("ConversationManager 不可用，无法绑定当前会话人格")
            return False

        try:
            conv_mgr = self.context.conversation_manager
            uid = event.unified_msg_origin
            try:
                await self._maybe_await(
                    conv_mgr.update_conversation(
                        unified_msg_origin=uid,
                        conversation_id=None,
                        persona_id=persona_id,
                    )
                )
            except Exception:
                await self._maybe_await(
                    conv_mgr.new_conversation(
                        unified_msg_origin=uid,
                        persona_id=persona_id,
                    )
                )
            logger.info(f"已绑定当前会话人格: {uid} -> {persona_id}")
            return True
        except Exception as e:
            logger.warning(f"绑定当前会话人格失败: {e}")
            return False

    def _build_persona_text(self, personality: Dict, target_name: str) -> str:
        prefix = str(self._get_setting("persona.system_prompt_prefix", "") or "").strip()
        lines = []
        if prefix:
            lines.append(prefix)
        lines.append(f"\n你现在就按 {target_name} 的群聊口吻说话。")

        summary = personality.get("summary", "")
        if summary:
            lines.append(f"\n【人格摘要】\n{summary}")

        identity = personality.get("identity", "")
        if identity:
            lines.append(f"\n【身份设定】\n{identity}")

        traits = personality.get("traits", {})
        if traits:
            lines.append("\n【性格特征】")
            for k, v in traits.items():
                lines.append(f"- {k}：{v}")

        style = personality.get("speaking_style", "")
        if style:
            lines.append("\n【说话风格与习惯】")
            if isinstance(style, list):
                for idx, item in enumerate(style, 1):
                    lines.append(f"{idx}. {item}")
            else:
                lines.append(str(style))

        values = personality.get("values_and_boundaries", [])
        if values:
            lines.append("\n【价值倾向与社交边界】")
            for idx, item in enumerate(values, 1):
                lines.append(f"{idx}. {item}")

        triggers = personality.get("trigger_reactions", [])
        if triggers:
            lines.append("\n【触发条件与反应模式】")
            for idx, item in enumerate(triggers, 1):
                lines.append(f"{idx}. {item}")

        phrases = personality.get("common_phrases", [])
        if phrases:
            lines.append(f"\n【常用表达】\n{' '.join(phrases)}")

        quotes = personality.get("signature_quotes", [])
        if quotes:
            lines.append("\n【骚话 / 爆点语录】")
            for item in quotes:
                lines.append(f"- {item}")

        examples = personality.get("behavior_examples", [])
        if examples:
            lines.append("\n【行为范例】")
            for idx, item in enumerate(examples, 1):
                lines.append(f"{idx}. {item}")

        interests = personality.get("interests", [])
        if interests:
            lines.append("\n【关注话题】")
            for item in interests:
                lines.append(f"- {item}")

        pattern = personality.get("emotional_pattern", "")
        if pattern:
            lines.append(f"\n【情绪模式】\n{pattern}")

        rules = personality.get("reply_rules", [])
        if rules:
            lines.append("\n【回复规则】")
            for item in rules:
                lines.append(f"- {item}")

        avoidances = personality.get("avoidances", [])
        if avoidances:
            lines.append("\n【避免事项】")
            for item in avoidances:
                lines.append(f"- {item}")

        lines.append("\n请在所有回复中保持该角色的稳定风格、措辞习惯和情绪节奏。")

        return "\n".join(lines)

    async def _is_admin(self, event) -> bool:
        """判断当前用户是否为管理员"""
        try:
            sender_id = event.get_sender_id()
            if not sender_id:
                return False

            admin_ids = []

            if hasattr(self, 'config') and hasattr(self.config, 'get'):
                try:
                    admins = await self.config.get("admins")
                    if admins:
                        admin_ids = admins if isinstance(admins, list) else [admins]
                except Exception:
                    pass

            config_paths = [
                "/AstrBot/data/config.json",
                "/AstrBot/astrbot/config.json",
            ]
            for cp in config_paths:
                if os.path.exists(cp):
                    try:
                        with open(cp, "r", encoding="utf-8") as f:
                            config = json.load(f)
                        admins = config.get("admins", config.get("admin", []))
                        if admins:
                            if isinstance(admins, list):
                                admin_ids.extend(admins)
                            else:
                                admin_ids.append(admins)
                    except Exception:
                        pass

            admin_ids = list(set(str(a) for a in admin_ids))
            return str(sender_id) in admin_ids

        except Exception as e:
            logger.error(f"检查管理员权限失败: {e}")
            return False
