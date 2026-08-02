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
import shutil
from typing import Optional, Dict, List, Any
from datetime import datetime, timedelta

from astrbot.api.event import filter, AstrMessageEvent, MessageChain
from astrbot.api.message_components import Plain, At
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star, register
from astrbot.api import logger

# ─── 数据存储路径 ────────────────────────────────────────
PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
PLUGIN_NAME = "clone_personality"


def _resolve_data_dir() -> str:
    """运行数据必须放在 AstrBot data 下，避免插件更新时被覆盖。"""
    env_data_dir = os.environ.get("ASTRBOT_DATA_DIR")
    if env_data_dir:
        base_dir = env_data_dir
    elif os.path.isdir("/AstrBot/data") or PLUGIN_DIR.startswith("/AstrBot/"):
        base_dir = "/AstrBot/data"
    elif f"{os.sep}plugins{os.sep}" in PLUGIN_DIR:
        base_dir = PLUGIN_DIR.split(f"{os.sep}plugins{os.sep}", 1)[0]
    else:
        base_dir = os.path.join(PLUGIN_DIR, "data")

    return os.path.join(base_dir, "plugin_data", PLUGIN_NAME)


DATA_DIR = _resolve_data_dir()
os.makedirs(DATA_DIR, exist_ok=True)

PERSONALITIES_FILE = os.path.join(DATA_DIR, "personalities.json")
ACTIVE_PERSONA_FILE = os.path.join(DATA_DIR, "active_persona.txt")
ACTIVE_SESSIONS_FILE = os.path.join(DATA_DIR, "active_sessions.json")
CONFIG_FILE = os.path.join(DATA_DIR, "config.json")


def _migrate_legacy_data_file(filename: str) -> None:
    legacy_path = os.path.join(PLUGIN_DIR, filename)
    target_path = os.path.join(DATA_DIR, filename)
    if os.path.exists(target_path) or not os.path.exists(legacy_path):
        return
    try:
        shutil.copy2(legacy_path, target_path)
        logger.info(f"已迁移插件数据: {legacy_path} -> {target_path}")
    except Exception as e:
        logger.warning(f"迁移插件数据失败 {filename}: {e}")


for _data_filename in (
    "personalities.json",
    "active_persona.txt",
    "active_sessions.json",
    "config.json",
):
    _migrate_legacy_data_file(_data_filename)


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
        "initial_analysis_messages": 240,
        "update_analysis_messages": 120,
        "max_analysis_messages": 80,
        "max_message_chars": 180,
        "max_prompt_chars": 12000,
    },
    "auto_update": {
        "enabled": False,
        "check_weekday": 0,
        "check_hour": 0,
        "stale_days": 7,
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
    "bypass": {
        "skip_image_tasks": True,
        "skip_tool_tasks": True,
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
    tmp_file = f"{CONFIG_FILE}.tmp"
    with open(tmp_file, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    _replace_json_file(tmp_file, CONFIG_FILE)


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
    tmp_file = f"{PERSONALITIES_FILE}.tmp"
    with open(tmp_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    _replace_json_file(tmp_file, PERSONALITIES_FILE)


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
    tmp_file = f"{ACTIVE_SESSIONS_FILE}.tmp"
    with open(tmp_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    _replace_json_file(tmp_file, ACTIVE_SESSIONS_FILE)


def _replace_json_file(tmp_file: str, target_file: str) -> None:
    try:
        os.replace(tmp_file, target_file)
    except PermissionError:
        shutil.copy2(tmp_file, target_file)
        try:
            os.remove(tmp_file)
        except OSError:
            pass


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
        self._last_auto_update_check_key = None

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
                await asyncio.sleep(60)
                if self._should_run_auto_update_now():
                    await self._auto_update_due_personalities()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"定期更新人格失败: {e}")

    def _should_run_auto_update_now(self) -> bool:
        if not bool(self._get_setting("auto_update.enabled", False)):
            return False

        now = datetime.now()
        check_weekday = int(self._get_setting("auto_update.check_weekday", 0))
        check_hour = int(self._get_setting("auto_update.check_hour", 0))

        if now.hour != check_hour:
            return False
        if check_weekday not in range(0, 8):
            check_weekday = 0
        if check_weekday != 0 and now.isoweekday() != check_weekday:
            return False

        check_key = f"{now.date().isoformat()}:{check_hour}"
        if self._last_auto_update_check_key == check_key:
            return False
        self._last_auto_update_check_key = check_key
        return True

    async def _auto_update_due_personalities(self) -> None:
        stale_days = int(self._get_setting("auto_update.stale_days", 7))
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
            if not group_id or not user_id.isdigit():
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

            if stale_days <= 0 or now - last_at >= timedelta(days=stale_days):
                due_items.append((pid, data))

        if not due_items:
            return

        logger.info(f"开始定期更新 {len(due_items)} 个已保存人格")
        updated_by_group: Dict[str, List[str]] = {}
        for pid, data in due_items:
            updated = await self._refresh_personality(pid, data)
            if not updated:
                continue
            canonical_id = updated.get("persona_id", pid)
            if canonical_id != pid:
                personalities.pop(pid, None)
            personalities[canonical_id] = updated
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
            update_limit = self._get_analysis_message_limit("update")
            history_slice = self._expand_history_slice(
                str(self._get_setting("message.history_slice", ":100")),
                update_limit,
            )

            messages = await self._fetch_user_messages(
                event=self._last_event,
                user_id=user_id,
                group_id=group_id,
                target_name=target_name,
                start=(end_time - timedelta(days=initial_days)).strftime("%Y-%m-%d"),
                end=end_time.strftime("%Y-%m-%d %H:%M"),
                slice=history_slice,
                max_messages=update_limit,
            )
            if isinstance(messages, dict):
                messages = messages.get("messages", [])
            if not messages:
                return None

            personality = await self._analyze_personality(
                self._last_event,
                messages,
                target_name,
                existing_personality=data,
                max_messages=update_limit,
            )
            if not personality:
                return None

            personality = self._merge_personality_update(data, personality)
            personality["user_id"] = str(user_id)
            personality["user_name"] = target_name
            personality["persona_name"] = data.get(
                "persona_name",
                self._build_persona_display_name(group_id, target_name, user_id),
            )
            personality["group_id"] = str(group_id)
            personality["created_at"] = data.get("created_at", datetime.now().isoformat())
            personality["updated_at"] = datetime.now().isoformat()
            personality["last_auto_update_at"] = datetime.now().isoformat()
            personality["update_mode"] = "incremental"
            personality["message_count"] = len(messages)
            personality = self._normalize_personality_metadata(pid, personality, group_id)
            personality = self._refresh_runtime_profile(personality)

            personalities = load_personalities()
            canonical_id = personality.get("persona_id", pid)
            if canonical_id != pid:
                personalities.pop(pid, None)
            personalities[canonical_id] = personality
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
        # QQ 号是人格的稳定唯一标识。群名片可能因群而异，不能参与主键生成，
        # 否则同一个人在不同群会被错误地创建为多个人格。
        uid = str(target_uid or "").strip()
        if uid.isdigit():
            return uid

        base = str(target_name or "").strip()
        if not base:
            base = uid
        base = base.lstrip("@").strip()
        base = re.sub(r"[\\/:*?\"<>|\s]+", "_", base)
        return base or uid

    def _build_persona_display_name(self, group_id: Any, target_name: str,
                                    target_uid: Any) -> str:
        uid = str(target_uid or "").strip()
        name = str(target_name or "").lstrip("@").strip()
        if name and name not in {uid, f"QQ{uid}"}:
            display_name = f"{name}_{uid}" if uid else name
        else:
            display_name = uid or name
        group_text = str(group_id or "").strip()
        return f"{group_text}-{display_name}" if group_text else display_name

    def _find_personality_by_user_id(self, personalities: Dict[str, Dict],
                                     user_id: Any) -> Optional[str]:
        """跨群按 QQ 号查找人格；存在旧重复数据时优先取最近更新的一条。"""
        uid = str(user_id or "").strip()
        if not uid:
            return None
        matches = [
            (pid, data)
            for pid, data in personalities.items()
            if isinstance(data, dict)
            and str(data.get("user_id", "")).strip() == uid
        ]
        if not matches:
            return None
        matches.sort(
            key=lambda item: str(
                item[1].get("updated_at")
                or item[1].get("created_at")
                or ""
            ),
            reverse=True,
        )
        return matches[0][0]

    def _resolve_personality_id(self, personalities: Dict[str, Dict],
                                arg: str,
                                group_id: Optional[Any] = None) -> Optional[str]:
        arg = str(arg or "").strip()
        if not arg:
            return None
        if arg in personalities:
            return arg

        # 数字参数视为 QQ 号。QQ 身份是全局的，不受当前群号过滤。
        if arg.isdigit():
            matched_by_uid = self._find_personality_by_user_id(personalities, arg)
            if matched_by_uid:
                return matched_by_uid

        group_text = str(group_id or "").strip()
        matches = []
        for pid, data in personalities.items():
            if group_text and str(data.get("group_id", "")).strip() != group_text:
                continue

            user_name = str(data.get("user_name", "") or "").strip()
            user_id = str(data.get("user_id", "") or "").strip()
            canonical = self._build_persona_id(user_name or pid, user_id or pid)
            candidates = {
                pid,
                canonical,
                user_name,
                user_id,
                str(data.get("persona_name", "") or "").strip(),
                str(data.get("display_name", "") or "").strip(),
                str(data.get("astrbot_persona_id", "") or "").strip(),
            }
            legacy_ids = data.get("legacy_persona_ids", [])
            if isinstance(legacy_ids, list):
                candidates.update(str(item).strip() for item in legacy_ids)
            if arg in {item for item in candidates if item}:
                matches.append(canonical if canonical in personalities else pid)

        return matches[0] if len(set(matches)) == 1 else None

    def _normalize_personality_metadata(self, pid: str, data: Dict,
                                        group_id: Optional[Any] = None) -> Dict:
        if not isinstance(data, dict):
            data = {}
        user_name = str(data.get("user_name", "") or pid).strip()
        user_id = str(data.get("user_id", "") or "").strip()
        canonical = self._build_persona_id(user_name, user_id or pid)
        group_text = str(group_id if group_id is not None else data.get("group_id", "")).strip()
        legacy_ids = data.get("legacy_persona_ids", [])
        if not isinstance(legacy_ids, list):
            legacy_ids = []
        for old_id in (pid, data.get("astrbot_persona_id")):
            old_id = str(old_id or "").strip()
            if old_id and old_id != canonical and old_id not in legacy_ids:
                legacy_ids.append(old_id)
        data["persona_id"] = canonical
        data["astrbot_persona_id"] = canonical
        uid = user_id or str(pid)
        data["display_name"] = self._build_persona_display_name(
            None, user_name, uid
        )
        data["persona_name"] = self._build_persona_display_name(group_text, user_name, user_id or pid)
        data["legacy_persona_ids"] = legacy_ids
        return data

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
        personalities = load_personalities()
        changed = False
        for key in self._get_session_keys(event):
            persona_id = sessions.get(key)
            if persona_id:
                resolved = self._resolve_personality_id(
                    personalities,
                    persona_id,
                    self._get_event_group_id(event),
                )
                if resolved and resolved != persona_id:
                    sessions[key] = resolved
                    changed = True
                if changed:
                    save_active_sessions(sessions)
                return resolved or persona_id
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
            ("人格导入", self.import_personality),
            ("人格更新", self.update_personality),
            ("人格纠正", self.correct_personality),
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
        if self._should_skip_persona_for_task(event):
            logger.info(f"人格注入跳过工具/图片任务: {self._get_session_keys(event)}")
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
        text = self._strip_bot_mentions(event.message_str.strip())
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
            persona_name = self._build_persona_display_name(group_id, target_name, target_uid)

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
            persona_name = self._build_persona_display_name(group_id, target_name, target_uid)

        # ── 同名同人默认增量更新；-f/--force 强制从零重建 ──
        personalities = load_personalities()
        # 跨群严格按 QQ 号定位已有记录，不让群名片变化制造重复人格。
        existing_pid = (
            self._find_personality_by_user_id(personalities, target_uid)
            or self._resolve_personality_id(personalities, pid, group_id)
            or pid
        )
        existing_personality = personalities.get(existing_pid)
        same_saved_person = (
            isinstance(existing_personality, dict)
            and str(existing_personality.get("user_id", "")) == str(target_uid)
        )
        incremental_update = bool(same_saved_person and not force_refresh)
        analysis_limit = self._get_analysis_message_limit(
            "update" if incremental_update else "initial"
        )

        # ── 获取聊天记录 ──
        yield event.plain_result(f"🤏 一把抓住 {target_name}(群 {group_id})，顷刻炼化...")

        try:
            end_time = datetime.now()
            initial_days = int(self._get_setting("message.initial_days", 30))
            fallback_days = int(self._get_setting("message.fallback_days", 90))
            history_slice = self._expand_history_slice(
                str(self._get_setting("message.history_slice", ":100")),
                analysis_limit,
            )

            start_time = end_time - timedelta(days=initial_days)

            messages = await self._fetch_user_messages(
                event=event,
                user_id=int(target_uid),
                group_id=int(group_id),
                target_name=target_name,
                start=start_time.strftime("%Y-%m-%d"),
                end=end_time.strftime("%Y-%m-%d %H:%M"),
                slice=history_slice,
                max_messages=analysis_limit,
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
                    max_messages=analysis_limit,
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
            existing_personality=existing_personality if incremental_update else None,
            max_messages=analysis_limit,
        )
        if not personality:
            yield event.plain_result("❌ 人格分析失败，请稍后重试。")
            return

        if incremental_update:
            personality = self._merge_personality_update(existing_personality, personality)

        # ── 保存人格（覆盖旧数据） ──
        personality["user_id"] = str(target_uid)
        personality["user_name"] = target_name
        personality["persona_name"] = persona_name
        personality["group_id"] = str(group_id)
        personality["created_at"] = (
            existing_personality.get("created_at")
            if incremental_update and isinstance(existing_personality, dict)
            else datetime.now().isoformat()
        )
        personality["updated_at"] = datetime.now().isoformat()
        personality["update_mode"] = "incremental" if incremental_update else "full"
        personality["message_count"] = len(messages)
        personality = self._normalize_personality_metadata(pid, personality, group_id)
        personality = self._refresh_runtime_profile(personality)

        # 收拢历史版本中由不同群名片产生的重复记录，并保留旧 ID 作为别名，
        # 让已经保存的会话引用可以自动解析到新的 QQ 主键。
        duplicate_pids = [
            saved_pid
            for saved_pid, saved_data in personalities.items()
            if isinstance(saved_data, dict)
            and str(saved_data.get("user_id", "")).strip() == str(target_uid)
            and saved_pid != pid
        ]
        legacy_ids = personality.get("legacy_persona_ids", [])
        if not isinstance(legacy_ids, list):
            legacy_ids = []
        for old_pid in duplicate_pids:
            old_data = personalities.get(old_pid, {})
            old_legacy_ids = old_data.get("legacy_persona_ids", [])
            if not isinstance(old_legacy_ids, list):
                old_legacy_ids = []
            for legacy_id in [old_pid, *old_legacy_ids]:
                legacy_id = str(legacy_id or "").strip()
                if legacy_id and legacy_id != pid and legacy_id not in legacy_ids:
                    legacy_ids.append(legacy_id)
            personalities.pop(old_pid, None)
        personality["legacy_persona_ids"] = legacy_ids
        personalities[pid] = personality
        save_personalities(personalities)

        if duplicate_pids:
            sessions = load_active_sessions()
            sessions_changed = False
            for session_key, active_pid in list(sessions.items()):
                if active_pid in duplicate_pids:
                    sessions[session_key] = pid
                    sessions_changed = True
            if sessions_changed:
                save_active_sessions(sessions)
            if get_active_persona() in duplicate_pids:
                set_active_persona(pid)

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

        text = self._strip_bot_mentions(event.message_str.strip())
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
        pid = self._resolve_personality_id(
            personalities,
            arg,
            self._get_event_group_id(event),
        )
        if not pid:
            yield event.plain_result(f"❌ 未找到人格「{arg}」，使用「人格列表」查看所有人格。")
            return

        personality = self._normalize_personality_metadata(pid, personalities[pid])
        personalities[pid] = personality
        save_personalities(personalities)

        set_active_persona(pid)
        self._set_session_active_persona(event, pid)
        target_name = personality.get("user_name", pid)
        summary_text = personality.get("summary", "")

        success = await self._inject_to_astrbot_persona(
            event, personality, target_name
        )
        if success:
            success = await self._bind_current_conversation_persona(event, pid)

        if success:
            yield event.plain_result(
                f"🔄 已切换至人格「{target_name}」({pid})\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"{summary_text}\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"✅ 已绑定插件会话人格，后续 @bot 对话会注入该人格。"
            )
        else:
            yield event.plain_result(
                f"🔄 已切换至人格「{target_name}」({pid})\n"
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

        text = self._strip_bot_mentions(event.message_str.strip())
        parts = text.split(maxsplit=1)
        event_group_id = self._get_event_group_id(event)
        group_filter = str(event_group_id).strip() if event_group_id else ""
        if not group_filter and len(parts) > 1:
            arg = parts[1].strip()
            if arg.isdigit():
                group_filter = str(int(arg))

        filtered = self._filter_personalities_for_group(
            personalities,
            group_filter or None,
        )
        if not filtered:
            if group_filter:
                yield event.plain_result(
                    f"📭 群 {group_filter} 暂无已克隆的人格。\n"
                    f"💡 私聊可用「克隆 {group_filter} <QQ号/群名片>」，群聊可用「@bot 克隆 @群友」。"
                )
            else:
                yield event.plain_result(
                    "📭 暂无可显示的人格。\n"
                    "💡 私聊查看指定群可用「人格列表 <群号>」。"
                )
            return

        active = get_active_persona()
        title = f"📋 群 {group_filter} 人格" if group_filter else "📋 已克隆人格"
        lines = [title]
        for pid, data in filtered.items():
            marker = " 👈 当前" if pid == active else ""
            user_id = data.get("user_id", "")
            name = data.get(
                "user_name",
                pid,
            )
            msg_cnt = data.get("message_count", 0)
            lines.append(
                f"{marker} {name} | QQ {user_id or '未知'} | {msg_cnt}条"
            )
        lines.append("切换：人格切换 <ID>")

        yield event.plain_result("\n".join(lines))

    def _filter_personalities_for_group(self, personalities: Dict[str, Dict],
                                        group_id: Optional[str]) -> Dict[str, Dict]:
        if not group_id:
            return personalities
        group_id = str(group_id).strip()
        return {
            pid: data
            for pid, data in personalities.items()
            if str(data.get("group_id", "")).strip() == group_id
        }

    # ════════════════════════════════════════════════════
    # 5. 人格详情
    # ════════════════════════════════════════════════════
    @filter.command("人格详情")
    async def personality_detail(self, event: AstrMessageEvent):
        text = self._strip_bot_mentions(event.message_str.strip())
        parts = text.split(maxsplit=1)

        if len(parts) < 2:
            yield event.plain_result("用法：人格详情 <人格ID>")
            return

        pid = parts[1].strip()
        personalities = load_personalities()
        resolved_pid = self._resolve_personality_id(
            personalities,
            pid,
            self._get_event_group_id(event),
        )
        if not resolved_pid:
            yield event.plain_result(f"❌ 未找到人格「{pid}」")
            return
        pid = resolved_pid

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

        phrases = data.get("common_phrases", [])
        if phrases:
            lines.extend(["", "💬 常用表达:"])
            for p in phrases[:5]:
                lines.append(f"  • \"{p}\"")

        style = data.get("speaking_style", "")
        if style:
            lines.extend(["", "🎙️ 说话风格:", f"  {style}"])

        interests = data.get("interests", [])
        if interests:
            lines.extend(["", "🎮 兴趣偏好:"])
            for item in interests[:5]:
                lines.append(f"  • {item}")

        aliases = data.get("aliases", [])
        if aliases:
            lines.extend(["", "🏷️ 群内别称:"])
            for item in aliases[:8]:
                if isinstance(item, dict) and item.get("name"):
                    lines.append(f"  • {item.get('name')}")

        corrections = data.get("corrections", [])
        if corrections:
            lines.extend(["", "🛠️ 人工纠正:"])
            for item in corrections[-5:]:
                if isinstance(item, dict) and item.get("text"):
                    lines.append(f"  • {item.get('text')}")

        lines.append("")
        lines.append("💡 使用「人格切换 %s」切换" % pid)

        yield event.plain_result("\n".join(lines))

    # ════════════════════════════════════════════════════
    # 6. 从 AstrBot 人格库导入
    # ════════════════════════════════════════════════════
    @filter.command("人格导入")
    async def import_personality(self, event: AstrMessageEvent):
        text = self._strip_bot_mentions(event.message_str.strip())
        parts = text.split()
        event_group_id = self._get_event_group_id(event)
        group_id = str(event_group_id).strip() if event_group_id else ""

        if event_group_id:
            persona_id = parts[1].strip() if len(parts) > 1 else ""
        else:
            if len(parts) >= 3 and parts[1].isdigit():
                group_id = str(int(parts[1]))
                persona_id = parts[2].strip()
            else:
                persona_id = parts[1].strip() if len(parts) > 1 else ""

        if not persona_id:
            yield event.plain_result(
                "群聊用法：人格导入 <人格ID>\n"
                "私聊用法：人格导入 <群号> <人格ID>\n"
                "例如：人格导入 477065120 cbaba"
            )
            return

        imported = await self._import_astrbot_persona(persona_id)
        if not imported:
            yield event.plain_result(
                f"❌ 未能从 AstrBot 人格设定中读取「{persona_id}」。\n"
                f"请确认设置页里的人格ID完全一致。"
            )
            return

        personalities = load_personalities()
        existing_pid = self._resolve_personality_id(personalities, persona_id, group_id)
        existing = personalities.get(existing_pid) if existing_pid else None
        if group_id:
            imported["group_id"] = group_id
            if (
                isinstance(existing, dict)
                and str(existing.get("group_id", "")).strip() == group_id
                and str(existing.get("user_id", "")).strip().isdigit()
            ):
                imported["user_id"] = str(existing.get("user_id")).strip()
            else:
                resolved_user_id = await self._resolve_persona_user_id(
                    event, group_id, persona_id, imported
                )
                if resolved_user_id:
                    imported["user_id"] = resolved_user_id

        canonical_id = self._build_persona_id(
            imported.get("user_name", persona_id),
            imported.get("user_id", persona_id),
        )
        imported = self._normalize_personality_metadata(canonical_id, imported, group_id)
        imported = self._refresh_runtime_profile(imported)
        existed = canonical_id in personalities or bool(existing_pid)
        if existing_pid and existing_pid != canonical_id:
            personalities.pop(existing_pid, None)
        personalities[canonical_id] = imported
        save_personalities(personalities)

        status = "更新" if existed else "导入"
        group_text = f"群 {group_id} " if group_id else ""
        yield event.plain_result(
            f"✅ 已{status}{group_text}人格「{canonical_id}」到插件列表。\n"
            f"现在可以使用「人格切换 {canonical_id}」。"
        )

    # ════════════════════════════════════════════════════
    # 7. 人格更新
    # ════════════════════════════════════════════════════
    @filter.command("人格更新")
    async def update_personality(self, event: AstrMessageEvent):
        self._remember_event(event)
        text = self._strip_bot_mentions(event.message_str.strip())
        parts = text.split()
        event_group_id = self._get_event_group_id(event)

        if event_group_id:
            if len(parts) < 2:
                yield event.plain_result("群聊用法：人格更新 <人格ID>")
                return
            group_id = str(event_group_id)
            pid = parts[1].strip()
        else:
            if len(parts) < 3 or not parts[1].isdigit():
                yield event.plain_result(
                    "私聊用法：人格更新 <群号> <人格ID>\n"
                    "例如：人格更新 477065120 cbaba"
                )
                return
            group_id = str(int(parts[1]))
            pid = parts[2].strip()

        personalities = load_personalities()
        resolved_pid = self._resolve_personality_id(personalities, pid, group_id)
        if resolved_pid:
            pid = resolved_pid
        data = personalities.get(pid)
        if not data:
            yield event.plain_result(f"❌ 未找到人格「{pid}」。")
            return

        saved_group_id = str(data.get("group_id", "")).strip()
        if saved_group_id != group_id:
            yield event.plain_result(
                f"❌ 人格「{pid}」不属于群 {group_id}。\n"
                f"当前记录所属群：{saved_group_id or '未知'}"
            )
            return

        user_id_text = str(data.get("user_id", "")).strip()
        if not user_id_text.isdigit():
            resolved_user_id = await self._resolve_persona_user_id(
                event, group_id, pid, data
            )
            if resolved_user_id:
                data["user_id"] = resolved_user_id
                new_pid = self._build_persona_id(data.get("user_name", pid), resolved_user_id)
                data = self._normalize_personality_metadata(new_pid, data, group_id)
                if new_pid != pid:
                    personalities.pop(pid, None)
                    pid = new_pid
                personalities[pid] = data
                save_personalities(personalities)
                user_id_text = resolved_user_id

        if not user_id_text.isdigit():
            yield event.plain_result(
                f"❌ 人格「{pid}」缺少真实 QQ 号，无法抓取聊天记录更新。\n"
                f"它可能是从 AstrBot 人格设定导入的，但没有匹配到同名群成员。"
            )
            return

        yield event.plain_result(f"😏 开始重新蒸馏「{pid}」，我看看你最近又进化成什么味了...")
        updated = await self._refresh_personality(pid, data)
        if not updated:
            yield event.plain_result(f"❌ 人格「{pid}」更新失败，可能没有抓到新聊天记录。")
            return

        personalities = load_personalities()
        personalities[pid] = updated
        save_personalities(personalities)
        yield event.plain_result(
            f"✅ 人格「{pid}」已更新。\n"
            f"分析消息数：{updated.get('message_count', 0)} 条"
        )

    # ════════════════════════════════════════════════════
    # 8. 人格纠正
    # ════════════════════════════════════════════════════
    @filter.command("人格纠正")
    async def correct_personality(self, event: AstrMessageEvent):
        text = self._strip_bot_mentions(event.message_str.strip())
        parts = text.split(maxsplit=2)
        if len(parts) < 3:
            yield event.plain_result("用法：人格纠正 <人格ID/昵称> <纠正内容>")
            return

        arg = parts[1].strip()
        correction_text = parts[2].strip()
        if not correction_text:
            yield event.plain_result("纠正内容不能为空。")
            return

        personalities = load_personalities()
        pid = self._resolve_personality_id(
            personalities,
            arg,
            self._get_event_group_id(event),
        )
        if not pid:
            yield event.plain_result(f"❌ 未找到人格「{arg}」。")
            return

        data = self._normalize_personality_metadata(pid, personalities[pid])
        corrections = data.get("corrections", [])
        if not isinstance(corrections, list):
            corrections = []
        corrections.append({
            "text": correction_text,
            "created_at": datetime.now().isoformat(),
            "source": "manual",
        })
        data["corrections"] = corrections[-20:]
        data = self._refresh_runtime_profile(data)
        personalities[pid] = data
        save_personalities(personalities)

        await self._inject_to_astrbot_persona(event, data, data.get("user_name", pid))
        yield event.plain_result(
            f"✅ 已记录人格纠正：{pid}\n"
            f"{correction_text}"
        )

    # ════════════════════════════════════════════════════
    # 9. 人格删除（管理员）
    # ════════════════════════════════════════════════════
    @filter.command("人格删除")
    async def delete_personality(self, event: AstrMessageEvent):
        is_admin = await self._is_admin(event)
        if not is_admin:
            yield event.plain_result("❌ 只有管理员可以删除人格。")
            return

        text = self._strip_bot_mentions(event.message_str.strip())
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            yield event.plain_result("用法：人格删除 <人格ID>")
            return

        pid = parts[1].strip()
        personalities = load_personalities()
        resolved_pid = self._resolve_personality_id(
            personalities,
            pid,
            self._get_event_group_id(event),
        )
        if not resolved_pid:
            yield event.plain_result(f"❌ 未找到人格「{pid}」")
            return
        pid = resolved_pid

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
                                   slice: str = ":100",
                                   max_messages: Optional[int] = None) -> Any:
        """获取指定用户的聊天记录"""
        messages = []

        try:
            history_result = await self._fetch_user_messages_from_group_history(
                event,
                user_id=user_id,
                group_id=group_id,
                target_name=target_name,
                max_messages=max_messages,
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
                            if max_messages and len(messages) >= max_messages:
                                break
                    elif isinstance(item, str):
                        messages.append(item)
                        if max_messages and len(messages) >= max_messages:
                            break
            elif isinstance(result, str):
                for line in result.strip().split("\n"):
                    line = line.strip()
                    if line:
                        messages.append(line)
                        if max_messages and len(messages) >= max_messages:
                            break
        except Exception as e:
            logger.error(f"获取用户消息失败: {e}")

        return messages[:max_messages] if max_messages else messages

    async def _fetch_user_messages_from_group_history(self, event, user_id: int,
                                                      group_id: int,
                                                      target_name: str,
                                                      max_messages: Optional[int] = None
                                                      ) -> Dict[str, List[str]]:
        """通过 aiocqhttp/OneBot 的 get_group_msg_history 扫描群历史。"""
        if not hasattr(event, "bot") or not hasattr(event.bot, "api"):
            return {"messages": []}

        max_rounds = int(self._get_setting("message.max_fetch_rounds", 50))
        per_query_count = int(self._get_setting("message.per_query_count", 200))
        max_count = max_messages or self._get_analysis_message_limit("initial")

        texts = []
        alias_hints = []
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
                elif self._is_target_alias_hint(item, text, user_id, target_name):
                    alias_hints.append(f"群友提到 {target_name}：{text}")

            if len(texts) >= max_count:
                break

        hint_limit = min(30, max(5, max_count // 10))
        return {"messages": (alias_hints[:hint_limit] + texts)[:max_count]}

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

    def _is_target_alias_hint(self, item: Dict[str, Any], text: str,
                              user_id: Any, target_name: str) -> bool:
        text = str(text or "").strip()
        if not text:
            return False
        user_text = str(user_id or "").strip()
        target = str(target_name or "").strip()
        mentions_target = False
        if user_text and user_text in text:
            mentions_target = True
        if target and target in text:
            mentions_target = True

        raw_message = item.get("message", "") if isinstance(item, dict) else ""
        if isinstance(raw_message, list):
            for seg in raw_message:
                if not isinstance(seg, dict):
                    continue
                if seg.get("type") != "at":
                    continue
                data = seg.get("data", {})
                qq = data.get("qq") if isinstance(data, dict) else None
                if str(qq or "") == user_text:
                    mentions_target = True
                    break

        if not mentions_target:
            return False

        alias_patterns = (
            r"(也就是|又叫|外号|别称|叫他|叫她|叫你|称呼)",
            r"(是.+?(他爸|她爸|爹|妈|哥|姐|儿子|女儿|老婆|老公))",
            r"(以后.*叫|就叫|可以叫)",
        )
        return any(re.search(pattern, text) for pattern in alias_patterns)

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

    async def _resolve_persona_user_id(self, event, group_id: Any, pid: str,
                                       data: Dict) -> Optional[str]:
        """导入的 AstrBot 人格可能没有 QQ 元数据，按群名片/昵称尽力补齐。"""
        if not group_id:
            return None

        candidates = [
            str(data.get("user_name", "")).strip(),
            str(data.get("persona_name", "")).strip(),
            str(pid or "").strip(),
        ]

        seen = set()
        for name in candidates:
            if not name or name in seen:
                continue
            seen.add(name)
            try:
                resolved = await self._resolve_group_member_id(
                    event, int(group_id), name
                )
            except Exception:
                resolved = None
            if resolved and str(resolved).isdigit():
                return str(resolved)

        return None

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
                                   target_name: str,
                                   existing_personality: Optional[Dict] = None,
                                   max_messages: Optional[int] = None
                                   ) -> Optional[Dict]:
        """调用大模型分析人格特征"""
        sample = self._prepare_analysis_messages(messages, max_messages=max_messages)
        chat_text = "\n".join([f"- {m}" for m in sample])
        mode_instruction = self._build_analysis_mode_instruction(
            target_name,
            existing_personality,
        )

        prompt = f"""你是一位人格分析专家。请分析 "{target_name}" 的聊天记录，提取其人格特征。

{mode_instruction}

要求：
1. 严格基于提供的聊天记录进行分析
2. 分析要具体、生动、有血有肉，像一份可直接放进机器人人格设定里的模板
3. 不要泛泛而谈，但也不要做百科档案；只保留会直接影响回复口吻的特征
4. “骚话/爆点语录/行为范例”必须尽量摘原始聊天里的原话，不要为了好看自行编造
5. 骚话/爆点语录宁缺毋滥：只有明显有梗、有攻击性、有反差、有抽象感、有口癖或有传播感的句子才收录；普通陈述、无趣吐槽、泛泛观点不要硬凑
6. 如果原始聊天里没有足够爆点语录，signature_quotes 返回空数组 []，不要为了凑数量填普通句子
7. 游戏偏好、政治/社会议题倾向、消费观等只在聊天记录证据明显时写入 interests 或 values_and_boundaries；证据不足不要强行归类
8. 输出必须是完整的新版人格 JSON，不要只输出本次变化
9. 如果聊天记录里出现“也就是/又叫/外号/叫他/是某某他爸”等称呼、玩梗关系，请写入 aliases 或 relations；必须标注为群聊称呼/玩梗关系，不要当真实身份事实
10. reply_rules 只写可直接控制回复的短规则，不要写抽象评价

请按以下 JSON 格式输出（不要包含其他内容，只输出 JSON）：
{{
    "identity": "你现在的身份是QQ用户“{target_name}”。用一段 80-160 字描述TA是谁、圈层、核心气质、社交位置和最鲜明的人格反差。",
    "summary": "人格核心：一段 120-220 字的总述，描述其核心矛盾、价值取向、社交姿态、情绪底色和典型反应。",
    "speaking_style": [
        "说话风格与习惯 1",
        "说话风格与习惯 2",
        "说话风格与习惯 3",
        "说话风格与习惯 4"
    ],
    "interests": [
        "兴趣偏好或常聊话题 1；可包含游戏、二游、技术、硬件、音乐、消费等，但必须有聊天证据",
        "兴趣偏好或常聊话题 2"
    ],
    "values_and_boundaries": [
        "价值判断 1；可包含公共议题/商业观/技术观/消费观，但不要强行贴政治光谱标签",
        "价值判断 2",
        "社交边界或雷区 1"
    ],
    "social_mode": [
        "对熟人/陌生人的不同说话方式",
        "接梗、反喷、装死、锐评、求助或带新人时的典型社交模式"
    ],
    "trigger_reactions": [
        "当遇到某类话题/情境时：会如何反应",
        "当遇到某类话题/情境时：会如何反应",
        "当遇到某类话题/情境时：会如何反应"
    ],
    "common_phrases": ["常用口头禅或高频短语（最多8个）"],
    "signature_quotes": ["从原始聊天记录中摘出的真正有记忆点的原话（0-6条，必须是原话，不要改写；没有足够爆点就返回空数组，不要硬凑）"],
    "aliases": [
        {{"name": "群里叫TA的别称或外号", "type": "nickname/relation_nickname", "confidence": "high/medium/low", "evidence": "对应原话或空字符串"}}
    ],
    "relations": [
        {{"target": "对方称呼", "relation": "群聊关系称呼", "meaning": "群友玩梗关系，不一定是真实亲属/现实关系", "confidence": "high/medium/low"}}
    ],
    "reply_rules": [
        "运行时必须遵守的具体短规则 1",
        "运行时必须遵守的具体短规则 2"
    ],
    "avoidances": [
        "禁止项 1",
        "禁止项 2",
        "禁止项 3"
    ],
    "recent_changes": ["定期更新时发现的近期变化（0-5条）；从零创建时返回空数组"],
    "last_update_summary": "定期更新摘要；从零创建时返回空字符串"
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

    def _get_analysis_message_limit(self, mode: str = "initial") -> int:
        legacy = int(self._get_setting("message.max_analysis_messages", 80))
        if mode == "update":
            return int(self._get_setting("message.update_analysis_messages", 120) or legacy)
        return int(self._get_setting("message.initial_analysis_messages", 240) or legacy)

    def _expand_history_slice(self, slice_value: str, min_count: int) -> str:
        """备用搜索工具使用 Python slice 语法；纯 :N 时按分析条数自动抬高。"""
        match = re.fullmatch(r":(\d+)", str(slice_value or "").strip())
        if not match:
            return slice_value
        count = max(int(match.group(1)), int(min_count))
        return f":{count}"

    def _build_analysis_mode_instruction(self, target_name: str,
                                         existing_personality: Optional[Dict]
                                         ) -> str:
        if not existing_personality:
            return (
                f"你正在从零创建 QQ 用户“{target_name}”的人格画像。"
                "请只根据本次聊天记录归纳，不要编造记录里没有的事实。"
            )

        old_persona = self._format_existing_personality_for_update(
            existing_personality
        )
        return f"""你正在更新 QQ 用户“{target_name}”的已有人格画像。
旧人格是长期画像，权重高于本次新消息；本次聊天记录只用于补充、修正和发现近期变化。
不要因为短期话题、临时情绪或少量新消息推翻旧人格；只有反复出现、非常明确的新特征才写入主画像。
如果新消息信息量不足，保留旧人格主体，只补充少量近期变化。
输出完整新版 JSON，并在 recent_changes / last_update_summary 里简短说明本次更新改了什么。

以下是旧人格长期画像：
{old_persona}"""

    def _format_existing_personality_for_update(self, personality: Dict) -> str:
        keys = (
            "identity",
            "summary",
            "speaking_style",
            "values_and_boundaries",
            "interests",
            "social_mode",
            "trigger_reactions",
            "common_phrases",
            "signature_quotes",
            "avoidances",
        )
        compact = {
            key: personality.get(key)
            for key in keys
            if personality.get(key) not in (None, "", [], {})
        }
        return json.dumps(compact, ensure_ascii=False, indent=2)

    def _merge_personality_update(self, old: Dict, new: Dict) -> Dict:
        """自动更新时保留旧画像的稳定字段，避免少量新消息把人格清空。"""
        stable_fields = (
            "identity",
            "summary",
            "speaking_style",
            "values_and_boundaries",
            "interests",
            "social_mode",
            "trigger_reactions",
            "common_phrases",
            "signature_quotes",
            "avoidances",
        )
        for key in stable_fields:
            if new.get(key) in (None, "", [], {}) and old.get(key) not in (None, "", [], {}):
                new[key] = old.get(key)
        return new

    def _prepare_analysis_messages(self, messages: List[str],
                                   max_messages: Optional[int] = None) -> List[str]:
        """按配置压缩聊天记录，避免超过模型上下文。"""
        max_count = max_messages or self._get_analysis_message_limit("initial")
        max_msg_chars = int(self._get_setting("message.max_message_chars", 180))
        max_prompt_chars = int(self._get_setting("message.max_prompt_chars", 12000))

        prepared = []
        used_chars = 0
        for raw in messages[:max_count]:
            msg = re.sub(r"\s+", " ", str(raw)).strip()
            if not msg:
                continue
            msg = re.sub(r"https?://\S+", "[链接]", msg)
            msg = re.sub(r"\[CQ:(image|record|video|mface|face|json|xml),[^\]]+\]", "[非文本消息]", msg, flags=re.IGNORECASE)
            msg = re.sub(r"base64://[A-Za-z0-9+/=]+", "[base64]", msg)
            if len(msg) > max_msg_chars:
                msg = msg[:max_msg_chars] + "..."
            projected = used_chars + len(msg) + 3
            if projected > max_prompt_chars:
                break
            prepared.append(msg)
            used_chars = projected

        return prepared

    async def _call_llm(self, event, prompt: str):
        """优先使用纯净 provider 调用，避免分析请求带入会话上下文。"""
        provider_id = str(self._get_setting("llm.provider_id", "") or "").strip()

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
        # 主动人格回复使用的是独立的纯文本 LLM 请求（见 _call_llm），无法
        # 安全复用 AstrBot 已解析好的图片输入。图片消息必须交回 AstrBot 的
        # 原生多模态链路；apply_active_persona_to_llm 仍会负责注入人格。
        if self._event_has_image(event):
            logger.info(
                f"人格主动回复跳过图片消息，交由 AstrBot 多模态链路: "
                f"{self._get_session_keys(event)}"
            )
            return
        if self._should_skip_persona_for_task(event):
            logger.info(f"人格主动回复跳过工具/图片任务: {self._get_session_keys(event)}")
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
        text = str(text or "")
        text = re.sub(r"\[At:\d+\]", "", text)
        text = re.sub(r"\[MSG_ID:\d+\]", "", text)
        return text.strip()

    def _should_skip_persona_for_task(self, event: AstrMessageEvent) -> bool:
        text = self._strip_bot_mentions(getattr(event, "message_str", "")).lower()

        if bool(self._get_setting("bypass.skip_image_tasks", True)):
            if self._event_has_image(event) and self._is_image_task_text(text):
                return True

        if bool(self._get_setting("bypass.skip_tool_tasks", True)):
            if self._is_tool_task_text(text):
                return True

        return False

    def _event_has_image(self, event: AstrMessageEvent) -> bool:
        message_obj = getattr(event, "message_obj", None)
        message_chain = getattr(message_obj, "message", []) or []
        raw_message = getattr(message_obj, "raw_message", None)

        candidates = list(message_chain)
        if isinstance(raw_message, dict):
            raw = raw_message.get("message", [])
            if isinstance(raw, list):
                candidates.extend(raw)
            elif raw:
                candidates.append(raw)
        elif isinstance(raw_message, list):
            candidates.extend(raw_message)

        for comp in candidates:
            if isinstance(comp, dict):
                comp_type = str(comp.get("type", "")).lower()
                if comp_type in ("image", "mface", "face"):
                    return True
                data = comp.get("data", {})
                if isinstance(data, dict) and any(k in data for k in ("url", "file", "image")):
                    if comp_type in ("image", "mface") or "image" in str(data).lower():
                        return True
                continue

            comp_type = type(comp).__name__.lower()
            if "image" in comp_type:
                return True
            if any(hasattr(comp, attr) for attr in ("url", "file", "image")) and "plain" not in comp_type:
                rep = repr(comp).lower()
                if "image" in rep or "图片" in rep:
                    return True

        text = str(getattr(event, "message_str", "") or "")
        return "[图片]" in text or "[image" in text.lower()

    def _is_image_task_text(self, text: str) -> bool:
        patterns = (
            r"(参考|照着|按这张|用这张|把这张|这张图|这个图|原图)",
            r"(转成|变成|改成|生成|画|绘制|重绘|修图|改图|图生图|换风格|真人|照片|写实|二次元|扩图|抠图|去水印)",
            r"(image|photo|realistic|img2img|edit image)",
        )
        return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)

    def _is_tool_task_text(self, text: str) -> bool:
        tool_patterns = (
            r"(画图|生成图|生成图片|出图|图生图|改图|修图|重绘|换风格|转真人|真人照片)",
            r"(识图|看图|图片分析|ocr|提取文字)",
            r"(搜索|搜一下|查一下|查资料|联网|浏览网页|打开网页)",
            r"(运行命令|执行命令|跑命令|读文件|读取文件|打开文件|处理文件)",
            r"(skill|插件|工具调用|调用工具)",
        )
        return any(re.search(pattern, text, re.IGNORECASE) for pattern in tool_patterns)

    def _is_plugin_command_text(self, text: str) -> bool:
        text = self._strip_bot_mentions(str(text or "")).strip()
        command_names = (
            "克隆",
            "clone",
            "人格切换",
            "人格列表",
            "人格详情",
            "人格导入",
            "人格更新",
            "人格纠正",
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
            "近期变化",
            personality.get("recent_changes", []),
        )

        update_summary = str(personality.get("last_update_summary", "")).strip()
        if update_summary:
            lines.extend(["", "本次更新", update_summary])

        self._append_numbered_section(
            lines,
            "说话风格与习惯",
            personality.get("speaking_style", []),
        )
        self._append_numbered_section(
            lines,
            "兴趣偏好",
            personality.get("interests", []),
        )
        self._append_numbered_section(
            lines,
            "价值判断与社交边界",
            personality.get("values_and_boundaries", []),
        )
        self._append_numbered_section(
            lines,
            "社交模式",
            personality.get("social_mode", []),
        )
        self._append_numbered_section(
            lines,
            "触发条件与反应模式",
            personality.get("trigger_reactions", []),
        )

        aliases = personality.get("aliases", [])
        if aliases:
            lines.extend(["", "群内别称 / 外号"])
            for item in aliases:
                if isinstance(item, dict):
                    name = item.get("name", "")
                    confidence = item.get("confidence", "")
                    if name:
                        suffix = f"（{confidence}）" if confidence else ""
                        lines.append(f"- {name}{suffix}")

        relations = personality.get("relations", [])
        if relations:
            lines.extend(["", "群内关系称呼"])
            for item in relations:
                if isinstance(item, dict):
                    target = item.get("target", "")
                    relation = item.get("relation", "")
                    meaning = item.get("meaning", "")
                    text = f"{target}：{relation}" if target else relation
                    if meaning:
                        text += f"；{meaning}"
                    if text.strip():
                        lines.append(f"- {text}")

        self._append_numbered_section(
            lines,
            "运行时回复规则",
            personality.get("reply_rules", []),
        )

        corrections = personality.get("corrections", [])
        if corrections:
            lines.extend(["", "人工纠正"])
            for item in corrections[-5:]:
                if isinstance(item, dict) and item.get("text"):
                    lines.append(f"- {item.get('text')}")

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
            "speaking_style": "",
            "common_phrases": [],
            "signature_quotes": [],
            "values_and_boundaries": [],
            "interests": [],
            "social_mode": [],
            "trigger_reactions": [],
            "recent_changes": [],
            "last_update_summary": "",
            "reply_rules": [],
            "avoidances": [],
            "aliases": [],
            "relations": [],
            "corrections": [],
        }

    def _normalize_personality(self, data: Dict) -> Dict:
        for key in (
            "speaking_style",
            "values_and_boundaries",
            "interests",
            "social_mode",
            "trigger_reactions",
            "common_phrases",
            "avoidances",
            "reply_rules",
        ):
            value = data.get(key, [])
            if isinstance(value, str):
                value = [value] if value.strip() else []
            if not isinstance(value, list):
                value = []
            data[key] = [str(item).strip() for item in value if str(item).strip()]

        data["aliases"] = self._normalize_aliases(data.get("aliases", []))
        data["relations"] = self._normalize_relations(data.get("relations", []))
        corrections = data.get("corrections", [])
        if isinstance(corrections, str):
            corrections = [{"text": corrections}]
        if not isinstance(corrections, list):
            corrections = []
        data["corrections"] = [
            item if isinstance(item, dict) else {"text": str(item)}
            for item in corrections
            if str(item.get("text", "") if isinstance(item, dict) else item).strip()
        ][-20:]

        data["signature_quotes"] = self._filter_signature_quotes(
            data.get("signature_quotes", [])
        )
        recent_changes = data.get("recent_changes", [])
        if isinstance(recent_changes, str):
            recent_changes = [recent_changes] if recent_changes.strip() else []
        if not isinstance(recent_changes, list):
            recent_changes = []
        data["recent_changes"] = [
            str(item).strip()
            for item in recent_changes
            if str(item).strip()
        ][:5]
        data["last_update_summary"] = str(
            data.get("last_update_summary", "") or ""
        ).strip()
        data = self._refresh_runtime_profile(data)
        return data

    def _normalize_aliases(self, aliases: Any) -> List[Dict[str, str]]:
        if isinstance(aliases, str):
            aliases = [aliases]
        if not isinstance(aliases, list):
            return []
        normalized = []
        seen = set()
        for item in aliases:
            if isinstance(item, dict):
                name = str(item.get("name", "") or item.get("alias", "")).strip()
                alias_type = str(item.get("type", "nickname") or "nickname").strip()
                evidence = str(item.get("evidence", "") or "").strip()
                confidence = str(item.get("confidence", "medium") or "medium").strip()
            else:
                name = str(item).strip()
                alias_type = "nickname"
                evidence = ""
                confidence = "medium"
            if not name or name in seen:
                continue
            seen.add(name)
            normalized.append({
                "name": name,
                "type": alias_type,
                "confidence": confidence,
                "evidence": evidence,
            })
        return normalized[:12]

    def _normalize_relations(self, relations: Any) -> List[Dict[str, str]]:
        if isinstance(relations, str):
            relations = [relations]
        if not isinstance(relations, list):
            return []
        normalized = []
        for item in relations:
            if isinstance(item, dict):
                target = str(item.get("target", "") or "").strip()
                relation = str(item.get("relation", "") or item.get("name", "")).strip()
                meaning = str(item.get("meaning", "") or "").strip()
                confidence = str(item.get("confidence", "medium") or "medium").strip()
            else:
                text = str(item).strip()
                target = ""
                relation = text
                meaning = "群聊语境关系"
                confidence = "medium"
            if relation:
                normalized.append({
                    "target": target,
                    "relation": relation,
                    "meaning": meaning,
                    "confidence": confidence,
                })
        return normalized[:12]

    def _refresh_runtime_profile(self, data: Dict) -> Dict:
        full_profile = {
            "summary": data.get("summary", ""),
            "identity": data.get("identity", ""),
            "speaking_style": data.get("speaking_style", []),
            "interests": data.get("interests", []),
            "values_and_boundaries": data.get("values_and_boundaries", []),
            "social_mode": data.get("social_mode", []),
            "trigger_reactions": data.get("trigger_reactions", []),
            "common_phrases": data.get("common_phrases", []),
            "signature_quotes": data.get("signature_quotes", []),
            "avoidances": data.get("avoidances", []),
            "recent_changes": data.get("recent_changes", []),
        }
        data["full_profile"] = full_profile

        rules = []
        rules.extend(data.get("reply_rules", [])[:4])
        rules.extend(data.get("speaking_style", [])[:4])
        rules.extend(data.get("social_mode", [])[:3])
        rules.extend(data.get("trigger_reactions", [])[:3])
        rules = [re.sub(r"\s+", " ", str(item)).strip() for item in rules]
        rules = [item for item in rules if item][:10]

        corrections = [
            str(item.get("text", "")).strip()
            for item in data.get("corrections", [])
            if isinstance(item, dict) and str(item.get("text", "")).strip()
        ]
        aliases = [
            str(item.get("name", "")).strip()
            for item in data.get("aliases", [])
            if isinstance(item, dict) and str(item.get("name", "")).strip()
        ]
        phrases = [str(item).strip() for item in data.get("common_phrases", []) if str(item).strip()]
        avoidances = [str(item).strip() for item in data.get("avoidances", []) if str(item).strip()]

        prompt_lines = []
        if aliases:
            prompt_lines.append(f"群里也可能用这些称呼叫你：{'、'.join(aliases[:6])}。")
        if corrections:
            prompt_lines.append("人工纠正优先遵守：" + "；".join(corrections[-5:]))
        if rules:
            prompt_lines.append("该群友的核心说话规则：" + "；".join(rules[:8]))
        if phrases:
            prompt_lines.append(f"可自然使用的高频表达：{'、'.join(phrases[:8])}。")
        if avoidances:
            prompt_lines.append("避免：" + "；".join(avoidances[:6]))

        data["runtime"] = {
            "layer0_rules": rules,
            "runtime_prompt": "\n".join(prompt_lines),
            "corrections": data.get("corrections", []),
            "hard_avoidances": avoidances,
        }
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

    async def _import_astrbot_persona(self, persona_id: str) -> Optional[Dict]:
        if not hasattr(self.context, "persona_manager"):
            return None

        persona_mgr = self.context.persona_manager
        try:
            persona = await self._maybe_await(persona_mgr.get_persona(persona_id))
        except Exception as e:
            logger.warning(f"读取 AstrBot 人格失败 {persona_id}: {e}")
            return None

        if not persona:
            return None

        system_prompt = self._extract_persona_field(
            persona,
            "system_prompt",
            "prompt",
            "persona",
            "content",
        )
        if not system_prompt:
            system_prompt = str(persona)

        name = (
            self._extract_persona_field(persona, "name", "persona_id", "id")
            or persona_id
        )
        imported_at = datetime.now().isoformat()
        return {
            "identity": "",
            "summary": system_prompt,
            "speaking_style": [],
            "values_and_boundaries": [],
            "interests": [],
            "social_mode": [],
            "trigger_reactions": [],
            "common_phrases": [],
            "signature_quotes": [],
            "avoidances": [],
            "recent_changes": [],
            "last_update_summary": "",
            "user_id": persona_id,
            "user_name": str(name),
            "persona_name": str(name),
            "group_id": "",
            "created_at": imported_at,
            "updated_at": imported_at,
            "message_count": 0,
            "imported_from": "astrbot_persona",
            "raw_system_prompt": system_prompt,
        }

    def _extract_persona_field(self, persona: Any, *names: str) -> str:
        for name in names:
            value = None
            if isinstance(persona, dict):
                value = persona.get(name)
            else:
                value = getattr(persona, name, None)

            if isinstance(value, str) and value.strip():
                return value.strip()
            if value is not None and not isinstance(value, (dict, list, tuple)):
                text = str(value).strip()
                if text:
                    return text
        return ""

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
            persona_file = os.path.join(DATA_DIR, "current_persona.txt")
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
        personality = self._refresh_runtime_profile(personality)
        prefix = str(self._get_setting("persona.system_prompt_prefix", "") or "").strip()
        lines = []
        if prefix:
            lines.append(prefix)
        lines.append(f"\n你现在就按 {target_name} 的群聊口吻说话。")

        raw_system_prompt = personality.get("raw_system_prompt", "")
        if raw_system_prompt and not personality.get("runtime", {}).get("runtime_prompt"):
            lines.append(f"\n【导入的 AstrBot 人格设定】\n{raw_system_prompt}")

        runtime_prompt = str(
            personality.get("runtime", {}).get("runtime_prompt", "") or ""
        ).strip()
        if runtime_prompt:
            lines.append(f"\n【该群友运行时规则】\n{runtime_prompt}")
        else:
            summary = personality.get("summary", "")
            if summary:
                lines.append(f"\n【人格摘要】\n{summary}")
            style = personality.get("speaking_style", [])
            if style:
                lines.append("\n【说话风格与习惯】")
                for idx, item in enumerate(style[:6], 1):
                    lines.append(f"{idx}. {item}")
            avoidances = personality.get("avoidances", [])
            if avoidances:
                lines.append("\n【避免事项】")
                for item in avoidances[:6]:
                    lines.append(f"- {item}")

        lines.append("\n请在所有回复中保持该角色的稳定风格、措辞习惯和情绪节奏。")

        return "\n".join(lines)

    async def _is_admin(self, event) -> bool:
        """判断当前用户是否为管理员"""
        try:
            sender_id = str(event.get_sender_id() or "").strip()
            if not sender_id:
                return False

            for attr in ("is_admin", "is_superuser", "is_admin_event"):
                value = getattr(event, attr, None)
                if isinstance(value, bool) and value:
                    return True
                if callable(value):
                    try:
                        result = await self._maybe_await(value())
                        if bool(result):
                            return True
                    except Exception:
                        pass

            message_obj = getattr(event, "message_obj", None)
            raw_message = getattr(message_obj, "raw_message", None)
            sender = None
            if isinstance(raw_message, dict):
                sender = raw_message.get("sender")
            if sender is None:
                sender = getattr(message_obj, "sender", None)
            role = ""
            if isinstance(sender, dict):
                role = str(sender.get("role") or sender.get("user_role") or "").lower()
            elif sender is not None:
                role = str(getattr(sender, "role", "") or getattr(sender, "user_role", "")).lower()
            if role in ("owner", "admin", "administrator"):
                return True

            admin_ids = set()

            if hasattr(self, 'config') and hasattr(self.config, 'get'):
                try:
                    admins = await self.config.get("admins")
                    if admins:
                        admin_ids.update(self._extract_admin_ids(admins))
                except Exception:
                    pass

            for source in (self.plugin_cfg, load_config(), DEFAULT_CONFIG):
                admin_ids.update(self._extract_admin_ids(source))

            config_paths = [
                "/AstrBot/data/config.json",
                "/AstrBot/data/cmd_config.json",
                "/AstrBot/data/config/astrbot_config.json",
                "/AstrBot/astrbot/config.json",
            ]
            for cp in config_paths:
                if os.path.exists(cp):
                    try:
                        with open(cp, "r", encoding="utf-8") as f:
                            config = json.load(f)
                        admin_ids.update(self._extract_admin_ids(config))
                    except Exception:
                        pass

            normalized_admin_ids = {str(a).strip() for a in admin_ids if str(a).strip()}
            is_admin = sender_id in normalized_admin_ids
            if not is_admin:
                logger.debug(
                    f"管理员检查未命中: sender={sender_id}, admins={sorted(normalized_admin_ids)}"
                )
            return is_admin

        except Exception as e:
            logger.error(f"检查管理员权限失败: {e}")
            return False

    def _extract_admin_ids(self, value: Any) -> set:
        """从不同 AstrBot 配置结构里递归提取管理员 QQ。"""
        admin_keys = {
            "admin",
            "admins",
            "admin_id",
            "admin_ids",
            "admins_id",
            "admin_user",
            "admin_users",
            "admin_list",
            "admin_qq",
            "admin_qqs",
            "administrator",
            "administrators",
            "superuser",
            "superusers",
            "superuser_id",
            "superuser_ids",
            "owner",
            "owners",
            "owner_id",
            "owner_ids",
            "manager",
            "managers",
            "master",
            "masters",
        }

        found = set()
        if value is None:
            return found
        if isinstance(value, (str, int)):
            text = str(value).strip()
            if text.isdigit():
                found.add(text)
            else:
                found.update(re.findall(r"\d{5,}", text))
            return found
        if isinstance(value, (list, tuple, set)):
            for item in value:
                found.update(self._extract_admin_ids(item))
            return found
        if isinstance(value, dict):
            for key, item in value.items():
                key_text = str(key).lower()
                if key_text in admin_keys:
                    found.update(self._extract_admin_ids(item))
                elif isinstance(item, dict):
                    found.update(self._extract_admin_ids(item))
            return found

        return found
