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
from typing import Optional, Dict, List, Any
from datetime import datetime, timedelta

from astrbot.api.event import filter, AstrMessageEvent, MessageChain
from astrbot.api.message_components import Plain, At
from astrbot.api.star import Context, Star, register
from astrbot.api import logger

# ─── 数据存储路径 ────────────────────────────────────────
PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
PERSONALITIES_FILE = os.path.join(PLUGIN_DIR, "personalities.json")
ACTIVE_PERSONA_FILE = os.path.join(PLUGIN_DIR, "active_persona.txt")
CONFIG_FILE = os.path.join(PLUGIN_DIR, "config.json")


# ─── 配置管理 ────────────────────────────────────────────
DEFAULT_CONFIG = {
    "admin_only_inject": True,   # True=仅管理员可注入设定, False=所有人可注入
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


# ─── 主插件类 ────────────────────────────────────────────
@register(
    "clone_personality",
    "AstrBot Plugin",
    "提取群友聊天记录克隆人格，支持群聊/私聊，管理员开关",
    "2.0.0",
)
class ClonePersonalityPlugin(Star):

    def __init__(self, context: Context) -> None:
        super().__init__(context)

    async def initialize(self):
        logger.info("群友人格克隆插件 v2.0 已加载")

    # ════════════════════════════════════════════════════
    # 1. 克隆指令
    # ════════════════════════════════════════════════════
    @filter.command("clone")
    @filter.command("克隆")
    async def clone_personality(self, event: AstrMessageEvent):
        """
        克隆群友人格。
        群聊用法：克隆 @群友          或   克隆 @群友 -f
        私聊用法：克隆 <群号> <群友QQ/@群友>  或   克隆 <群号> <群友QQ/@群友> -f
        """
        text = event.message_str.strip()
        parts = text.split()
        force_refresh = "-f" in parts or "--force" in parts

        # ── 判断是群聊还是私聊 ──
        is_group = hasattr(event, 'group_id') and event.group_id

        if is_group:
            # 群聊模式：从 @ 获取目标
            at_targets = [
                comp for comp in event.message_obj.message
                if isinstance(comp, At)
            ]
            if not at_targets:
                yield event.plain_result(
                    "群聊用法：克隆 @群友\n"
                    "私聊用法：克隆 <群号> <群友QQ/@群友>"
                )
                return

            target_uid = at_targets[0].qq
            target_name = at_targets[0].name or str(target_uid)
            group_id = str(event.group_id)
            pid = f"{group_id}_{target_uid}"

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
            at_targets = [
                comp for comp in event.message_obj.message
                if isinstance(comp, At)
            ]

            # 校验群号
            if not group_id.isdigit():
                yield event.plain_result(f"❌ 群号格式错误：{group_id}，应为纯数字")
                return
            if target_uid_raw.isdigit():
                target_uid = target_uid_raw
                target_name = f"QQ{target_uid}"
            elif at_targets:
                target_uid = str(at_targets[0].qq)
                target_name = at_targets[0].name or str(target_uid)
            else:
                yield event.plain_result(f"❌ QQ号格式错误：{target_uid_raw}，应为纯数字")
                return

            group_id = str(int(group_id))  # 标准化
            pid = f"{group_id}_{target_uid}"

        # ── 重复 ID 直接覆盖（不弹提示） ──
        personalities = load_personalities()

        # ── 获取聊天记录 ──
        yield event.plain_result(f"🔍 正在爬取 {target_name} 的聊天记录（群 {group_id}），请稍候...")

        try:
            end_time = datetime.now()
            start_time = end_time - timedelta(days=30)

            messages = await self._fetch_user_messages(
                user_id=int(target_uid),
                group_id=int(group_id),
                start=start_time.strftime("%Y-%m-%d"),
                end=end_time.strftime("%Y-%m-%d %H:%M")
            )

            if not messages:
                start_time = end_time - timedelta(days=90)
                messages = await self._fetch_user_messages(
                    user_id=int(target_uid),
                    group_id=int(group_id),
                    start=start_time.strftime("%Y-%m-%d"),
                    end=end_time.strftime("%Y-%m-%d %H:%M")
                )

            if not messages:
                yield event.plain_result(
                    f"❌ 未找到 {target_name} 在群 {group_id} 的聊天记录。\n"
                    f"请确保该群友在该群发过消息。"
                )
                return

            yield event.plain_result(f"✅ 获取到 {len(messages)} 条消息，正在分析人格特征...")

        except Exception as e:
            logger.error(f"获取聊天记录失败: {e}")
            yield event.plain_result(f"❌ 获取聊天记录失败: {str(e)}")
            return

        # ── 调用大模型分析 ──
        personality = await self._analyze_personality(event, messages, target_name)
        if not personality:
            yield event.plain_result("❌ 人格分析失败，请稍后重试。")
            return

        # ── 保存人格（覆盖旧数据） ──
        personality["user_id"] = str(target_uid)
        personality["user_name"] = target_name
        personality["group_id"] = str(group_id)
        personality["created_at"] = datetime.now().isoformat()
        personality["message_count"] = len(messages)

        personalities[pid] = personality
        save_personalities(personalities)

        # ── 判断是否注入设定 ──
        plugin_config = load_config()
        admin_only = plugin_config.get("admin_only_inject", True)
        is_admin = await self._is_admin(event)

        can_inject = (not admin_only) or (admin_only and is_admin)

        summary = (
            f"🧬 人格克隆完成！\n"
            f"🆔 人格ID: {pid}\n"
            f"👤 目标: {target_name}\n"
            f"📊 分析消息数: {len(messages)} 条\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"{personality.get('summary', '')}\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"💡 使用「人格切换 {pid}」切换为此人格"
        )

        if can_inject:
            success = await self._inject_to_astrbot_persona(
                event, personality, target_name
            )
            if success:
                summary += f"\n✅ 已自动将「{target_name}」人格注入 AstrBot 设定！"
            else:
                summary += f"\n⚠️ 人格已保存，但注入 AstrBot 设定失败。"
        else:
            summary += (
                f"\nℹ️ 当前为「仅管理员注入」模式。\n"
                f"   管理员可使用「管理员注入开关」关闭此限制。"
            )

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
        current = config.get("admin_only_inject", True)

        if arg in ("on", "开启", "true", "1"):
            config["admin_only_inject"] = True
            new_status = "ON"
        elif arg in ("off", "关闭", "false", "0"):
            config["admin_only_inject"] = False
            new_status = "OFF"
        else:
            config["admin_only_inject"] = not current
            new_status = "ON" if config["admin_only_inject"] else "OFF"

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
        text = event.message_str.strip()
        parts = text.split(maxsplit=1)

        if len(parts) < 2:
            yield event.plain_result("用法：人格切换 <人格ID> 或 人格切换 default（恢复默认）")
            return

        arg = parts[1].strip()

        if arg.lower() in ("default", "默认"):
            set_active_persona(None)
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
        target_name = personalities[arg].get("user_name", arg)
        summary_text = personalities[arg].get("summary", "")

        success = await self._inject_to_astrbot_persona(
            event, personalities[arg], target_name
        )

        if success:
            yield event.plain_result(
                f"🔄 已切换至人格「{target_name}」({arg})\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"{summary_text}\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"✅ 已生效！"
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
            name = data.get("user_name", "未知")
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
            f"👤 目标: {data.get('user_name', '未知')}",
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

    async def _fetch_user_messages(self, user_id: int, group_id: int,
                                   start: str, end: str) -> List[str]:
        """获取指定用户的聊天记录"""
        messages = []

        try:
            result = await self._search_history(
                query=None,
                user_id=user_id,
                group_id=group_id,
                start=start,
                end=end,
                slice=":100"
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
        sample = messages[:80]
        chat_text = "\n".join([f"- {m}" for m in sample])

        prompt = f"""你是一位人格分析专家。请分析以下 "{target_name}" 的聊天记录，提取其人格特征。

要求：
1. 严格基于提供的聊天记录进行分析
2. 分析要具体、生动、有血有肉
3. 不要泛泛而谈，要给出具体特征

请按以下 JSON 格式输出（不要包含其他内容，只输出 JSON）：
{{
    "summary": "一段生动的人格摘要（50-100字），描述此人的核心特点",
    "traits": {{
        "性格倾向": "如：外向开朗 / 内敛沉稳 / 毒舌幽默 / 温和友善 等",
        "情绪稳定性": "如：情绪稳定 / 容易激动 / 喜怒无常 等",
        "思维风格": "如：理性逻辑 / 感性发散 / 天马行空 / 务实接地气 等",
        "社交角色": "如：话题发起者 / 捧场王 / 冷场终结者 / 潜水窥屏 等"
    }},
    "speaking_style": "描述其独特的说话风格（30-50字），包括语气、用词习惯、句式特点等",
    "common_phrases": ["常用口头禅或高频短语（最多5个）"],
    "interests": ["从聊天中推断的兴趣爱好或话题偏好（最多5个）"],
    "emotional_pattern": "描述其情绪表达模式（20-40字），比如是否爱用表情包、语气词等"
}}

以下是 "{target_name}" 的聊天记录：
{chat_text}
"""

        try:
            if hasattr(self, 'llm') and self.llm:
                resp = await self.llm.text_chat(prompt)
                if resp:
                    return self._parse_llm_response(resp)
            elif hasattr(event, 'broadcast') and hasattr(event.broadcast, 'llm'):
                resp = await event.broadcast.llm.text_chat(prompt)
                if resp:
                    return self._parse_llm_response(resp)
            else:
                try:
                    resp = await event.llm.text_chat(prompt)
                    if resp:
                        return self._parse_llm_response(resp)
                except Exception:
                    pass

            logger.error("无法访问大模型接口")
            return None

        except Exception as e:
            logger.error(f"调用大模型分析人格失败: {e}")
            return None

    def _parse_llm_response(self, resp) -> Optional[Dict]:
        """解析 LLM 返回的 JSON 人格数据"""
        text = str(resp)

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
                            return json.loads(json_str)
                        except json.JSONDecodeError:
                            logger.warning(f"JSON 解析失败")

        return {
            "summary": text[:200] if text else "分析失败",
            "traits": {},
            "speaking_style": "",
            "common_phrases": [],
            "interests": [],
            "emotional_pattern": ""
        }

    async def _inject_to_astrbot_persona(self, event, personality: Dict,
                                          target_name: str) -> bool:
        """将人格注入 AstrBot 系统设定"""
        try:
            persona_text = self._build_persona_text(personality, target_name)

            # 方法1: 通过配置 API
            if hasattr(self, 'config') and hasattr(self.config, 'set'):
                await self.config.set("persona", persona_text)
                await self.config.save()
                logger.info(f"已通过 config API 注入人格: {target_name}")
                return True

            # 方法2: 写入配置文件
            config_paths = [
                "/AstrBot/data/config.json",
                "/AstrBot/astrbot/config.json",
                "data/config.json",
            ]
            for cp in config_paths:
                if os.path.exists(cp):
                    try:
                        with open(cp, "r", encoding="utf-8") as f:
                            config = json.load(f)
                        config["persona"] = persona_text
                        with open(cp, "w", encoding="utf-8") as f:
                            json.dump(config, f, ensure_ascii=False, indent=2)
                        logger.info(f"已写入配置文件: {cp}")
                        return True
                    except Exception as e:
                        logger.debug(f"写入 {cp} 失败: {e}")

            # 方法3: 本地保存
            persona_file = os.path.join(PLUGIN_DIR, "current_persona.txt")
            with open(persona_file, "w", encoding="utf-8") as f:
                f.write(persona_text)
            logger.info(f"人格已保存到 {persona_file}")
            return True

        except Exception as e:
            logger.error(f"注入人格失败: {e}")
            return False

    def _build_persona_text(self, personality: Dict, target_name: str) -> str:
        lines = [
            f"你现在是 {target_name} 的人格克隆体。"
            f"请以 {target_name} 的风格与用户交流。"
        ]

        summary = personality.get("summary", "")
        if summary:
            lines.append(f"\n【人格摘要】\n{summary}")

        traits = personality.get("traits", {})
        if traits:
            lines.append("\n【性格特征】")
            for k, v in traits.items():
                lines.append(f"- {k}：{v}")

        style = personality.get("speaking_style", "")
        if style:
            lines.append(f"\n【说话风格】\n{style}")

        phrases = personality.get("common_phrases", [])
        if phrases:
            lines.append(f"\n【常用表达】\n{' '.join(phrases)}")

        pattern = personality.get("emotional_pattern", "")
        if pattern:
            lines.append(f"\n【情绪模式】\n{pattern}")

        lines.append("\n请完全代入该角色，在所有回复中保持一致的风格和口吻。")

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
