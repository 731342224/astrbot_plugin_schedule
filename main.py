from pathlib import Path
from typing import Optional, Dict, Any

from astrbot.api import llm_tool, logger
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star, StarTools, register
from astrbot.api.web import json_response, error_response, request

try:
    from astrbot.api.agents import TextPart
except ImportError:
    try:
        from astrbot.core.agent.message import TextPart
    except ImportError:
        TextPart = None

from .core.models import SemesterConfig
from .core.storage import ScheduleStorage
from .core.parser import ScheduleParser
from .core.scheduler_engine import SchedulerEngine

PLUGIN_NAME = "astrbot_plugin_schedule"


@register(
    PLUGIN_NAME,
    "夜玄",
    "智能课表与日程管家：让 AI 自主推算当前教学周与单双周，查询每日课程并记录动态调课/临时日程",
    "1.9.2",
    "https://github.com/731342224/astrbot_plugin_schedule",
)
class SchedulePlugin(Star):
    """AstrBot 智能课表与日程管理插件主入口"""

    def __init__(self, context: Context, config: Optional[Dict[str, Any]] = None):
        super().__init__(context)
        self.plugin_config = config or {}

        # 获取 data/plugin_data/astrbot_plugin_schedule/ 数据目录
        data_dir = self._resolve_data_dir()
        self.storage = ScheduleStorage(data_dir=data_dir)
        self.engine = SchedulerEngine(self.storage)

        # 注册侧边栏 WebUI 通信路由
        self._register_web_apis()
        logger.info(f"[{PLUGIN_NAME}] 智能课表与日程管家已加载，数据目录: {self.storage.data_dir}")

    def _resolve_data_dir(self) -> Path:
        try:
            raw_dir = StarTools.get_data_dir(PLUGIN_NAME)
            if raw_dir:
                return Path(raw_dir)
        except TypeError:
            try:
                raw_dir = StarTools.get_data_dir()
                if raw_dir:
                    return Path(raw_dir)
            except Exception:
                pass
        except Exception:
            pass

        plugin_dir = Path(__file__).resolve().parent
        if plugin_dir.parent.name == "plugins":
            return plugin_dir.parent.parent / "plugin_data" / PLUGIN_NAME
        return Path("data") / "plugin_data" / PLUGIN_NAME

    async def _parse_request_json(self) -> Dict[str, Any]:
        """兼容不同 AstrBot 版本下 request.json() / request.get_json() 的读取。"""
        try:
            if hasattr(request, "json") and callable(request.json):
                try:
                    data = await request.json(default={})
                except TypeError:
                    data = await request.json()
                return data if isinstance(data, dict) else {}
            if hasattr(request, "get_json") and callable(request.get_json):
                data = await request.get_json()
                return data if isinstance(data, dict) else {}
        except Exception as e:
            logger.warning(f"[{PLUGIN_NAME}] 解析请求 JSON 失败: {e}")
        return {}

    def _register_web_apis(self) -> None:
        """注册五、侧边栏 WebUI 与后端 API 路由规范中定义的 7 个路由。"""
        if not hasattr(self.context, "register_web_api"):
            logger.warning(f"[{PLUGIN_NAME}] 当前 AstrBot 版本不支持 context.register_web_api()。")
            return

        routes = [
            (f"/{PLUGIN_NAME}/overview", self.api_overview, ["GET"], "获取学期配置、固定课程与当前教学周"),
            (f"/{PLUGIN_NAME}/config/save", self.api_config_save, ["POST"], "保存学期与作息设置"),
            (f"/{PLUGIN_NAME}/import", self.api_import, ["POST"], "导入 .ics 或 .json 课表"),
            (f"/{PLUGIN_NAME}/export", self.api_export, ["GET", "POST"], "导出当前课表为 .ics 或 .json 文件"),
            (f"/{PLUGIN_NAME}/course/save", self.api_course_save, ["POST"], "新增或编辑固定课程"),
            (f"/{PLUGIN_NAME}/course/delete", self.api_course_delete, ["POST"], "删除固定课程"),
            (f"/{PLUGIN_NAME}/events", self.api_events_list, ["GET"], "获取所有临时日程与停课记录"),
            (f"/{PLUGIN_NAME}/events/save", self.api_events_save, ["POST"], "新增、编辑或删除临时日程与调课"),
            (f"/{PLUGIN_NAME}/profiles", self.api_profiles_manage, ["GET", "POST"], "管理与切换多份课表档案"),
        ]

        for path, handler, methods, desc in routes:
            try:
                self.context.register_web_api(path, handler, methods, desc)
            except Exception as e:
                logger.error(f"[{PLUGIN_NAME}] 注册 Web API 路由失败 ({path}): {e}")

    # =========================================================================
    # 四、大模型工具调用（@llm_tool）接口实现
    # =========================================================================

    @llm_tool(name="query_schedule")
    async def query_schedule(
        self, event: AstrMessageEvent, date_str: str = "today", profile_name: str = ""
    ) -> str:
        """查询指定日期的课程表与临时日程安排、查看全部固定课程列表，或查看所有已保存的多份课表列表。系统会自动计算教学周次与单双周，并剔除当天已标记停课的课程。

        Args:
            date_str(string): 目标日期，支持 "today"（今天）、"tomorrow"（明天）、"day_after_tomorrow"（后天）、"YYYY-MM-DD"（如 "2026-11-23"）、"this_week"（查看本周有效周课表，不含非本周课程）、"next_week"（查看下周有效周课表）、"all"（查看全部固定课程总表）或 "profiles"（查看所有保存的课表列表）。
            profile_name(string): 可选，指定要查询哪一份课表的名称（例如 "大二下课表"、"室友课表"），留空则查询当前激活的默认课表。
        """
        try:
            return self.engine.format_schedule_query(date_str or "today", profile_name=profile_name)
        except Exception as e:
            logger.error(f"[{PLUGIN_NAME}] query_schedule 执行异常: {e}")
            return f"查询课表失败：{str(e)}"

    @llm_tool(name="manage_schedule_event")
    async def manage_schedule_event(
        self,
        event: AstrMessageEvent,
        action: str,
        date_str: str = "today",
        title: str = "",
        start_time: str = "",
        end_time: str = "",
        location: str = "",
        note: str = "",
    ) -> str:
        """管理临时日程、记录动态调课/停课、删除/清空课程，或切换/新建多份课表。

        Args:
            action(string): 操作类型，可选值："add_event"（新增临时日程/补课安排）、"cancel_course"（标记当日某门固定课程停课）、"delete_event"（根据日期和标题删除指定临时日程或停课记录）、"delete_course"（永久删除指定名称的固定课程）、"clear_all_courses"（清空当前课表的所有固定课程）、"switch_profile"（切换当前使用的课表，title 填目标课表名）、"create_profile"（新建一份课表并切换过去，title 填新课表名）。
            date_str(string): 生效日期，支持 "today"、"tomorrow"、"day_after_tomorrow" 或 "YYYY-MM-DD"。
            title(string): 日程标题、课程名称或目标课表名称（例如 "班级团建会议"、"高等数学" 或 "大三上课表"）。
            start_time(string): 开始时间（24小时制 HH:MM，例如 "16:30"，可选）。
            end_time(string): 结束时间（24小时制 HH:MM，例如 "17:30"，可选）。
            location(string): 教室或活动地点（例如 "活动中心 201"，可选）。
            note(string): 备注信息（例如 "班长通知"，可选）。
        """
        try:
            return self.engine.handle_manage_event(
                action=action,
                date_str=date_str,
                title=title,
                start_time=start_time,
                end_time=end_time,
                location=location,
                note=note,
            )
        except Exception as e:
            logger.error(f"[{PLUGIN_NAME}] manage_schedule_event 执行异常: {e}")
            return f"日程操作失败：{str(e)}"

    @filter.on_llm_request()
    async def inject_today_schedule_context(self, event: AstrMessageEvent, req: ProviderRequest):
        """当开启 auto_inject_today_context 时，在向 LLM 发起请求前通过 extra_user_content_parts 临时注入今日课表与日程摘要。"""
        auto_inject = self.plugin_config.get("auto_inject_today_context", False)
        if not auto_inject:
            return

        try:
            today_summary = self.engine.format_schedule_query("today")
            config = self.storage.load_config()
            enable_reminder = bool(
                self.plugin_config.get("enable_schedule_reminder", config.enable_reminder)
            )
            remind_mins = int(
                self.plugin_config.get("remind_advance_minutes", config.remind_advance_minutes)
            )
            if enable_reminder:
                reminder_note = f"当前已开启课前提醒（提前 {remind_mins} 分钟）。"
            else:
                reminder_note = "当前已关闭主动课前提醒，仅在用户主动询问课表或日程时回答，无需主动催促上课。"

            context_block = (
                f"<schedule_context>\n"
                f"[智能课表与日程管家 - 今日实时上下文]\n"
                f"{today_summary}\n"
                f"（提示：{reminder_note}若用户询问其他日期的课表或需要记录调课/停课/临时日程，请调用 query_schedule 或 manage_schedule_event 工具。）\n"
                f"</schedule_context>"
            )

            # 使用 req.extra_user_content_parts + .mark_as_temp() 注入本轮临时上下文，
            # 避免改写 req.system_prompt 破坏大模型服务端 Prompt 缓存命中率，也不污染长期对话历史
            if hasattr(req, "extra_user_content_parts") and isinstance(req.extra_user_content_parts, list):
                if TextPart is not None:
                    part = TextPart(text=context_block)
                    if hasattr(part, "mark_as_temp") and callable(part.mark_as_temp):
                        part.mark_as_temp()
                    elif hasattr(part, "make_as_temp") and callable(part.make_as_temp):
                        part.make_as_temp()
                    req.extra_user_content_parts.append(part)
        except Exception as e:
            logger.debug(f"[{PLUGIN_NAME}] 自动注入今日课表上下文跳过: {e}")

    # =========================================================================
    # 五、侧边栏 WebUI 与后端 API 路由实现
    # =========================================================================

    async def api_overview(self):
        """GET /astrbot_plugin_schedule/overview"""
        try:
            config = self.storage.load_config()
            courses = self.storage.load_courses()
            profiles = self.storage.list_profiles()
            active_profile_id = self.storage.get_active_profile_id()
            current_week = self.engine.calculate_teaching_week(
                self.engine.resolve_date_str("today"), config
            )
            return json_response(
                {
                    "status": "ok",
                    "active_profile_id": active_profile_id,
                    "profiles": profiles,
                    "config": config.to_dict(),
                    "courses": [c.to_dict() for c in courses],
                    "current_week": current_week,
                }
            )
        except Exception as e:
            logger.error(f"[{PLUGIN_NAME}] api_overview error: {e}")
            return error_response(str(e), status=500)

    async def api_profiles_manage(self):
        """GET | POST /astrbot_plugin_schedule/profiles
        支持操作：
        - GET: 返回所有课表列表与当前激活课表 ID
        - POST {action: 'switch'|'create'|'rename'|'delete', id, name, copy_from_current, ...}
        """
        try:
            if getattr(request, "method", "POST") == "GET":
                return json_response(
                    {
                        "status": "ok",
                        "active_profile_id": self.storage.get_active_profile_id(),
                        "profiles": self.storage.list_profiles(),
                    }
                )

            body = await self._parse_request_json()
            action = str(body.get("action", "switch")).strip().lower()
            target_id = str(body.get("id", "")).strip()
            name = str(body.get("name", "")).strip()

            if action == "switch":
                if not target_id and not name:
                    return error_response("缺少要切换的课表 id 或 name", status=400)
                info = self.storage.switch_profile(target_id or name)
                msg = f"已切换到课表：《{info['name']}》"
            elif action == "create":
                if not name:
                    return error_response("新建课表名称不能为空", status=400)
                copy_cur = bool(body.get("copy_from_current", False))
                semester_name = str(body.get("semester_name") or name).strip()
                start_date = str(body.get("start_date") or "").strip()
                total_weeks = int(body.get("total_weeks") or 20)
                info = self.storage.create_profile(
                    name=name,
                    semester_name=semester_name,
                    start_date=start_date,
                    total_weeks=total_weeks,
                    copy_from_current=copy_cur,
                    switch_to_new=True,
                )
                msg = f"已新建并切换到课表：《{info['name']}》"
            elif action == "rename":
                if not target_id or not name:
                    return error_response("重命名课表需要提供 id 与新的 name", status=400)
                info = self.storage.rename_profile(target_id, name)
                msg = f"已重命名课表为：《{info['name']}》"
            elif action == "delete":
                if not target_id:
                    return error_response("删除课表缺少 id 参数", status=400)
                self.storage.delete_profile(target_id)
                msg = "已删除该课表"
            else:
                return error_response(f"不支持的课表管理操作: {action}", status=400)

            config = self.storage.load_config()
            courses = self.storage.load_courses()
            events = self.storage.load_events()
            current_week = self.engine.calculate_teaching_week(
                self.engine.resolve_date_str("today"), config
            )
            return json_response(
                {
                    "status": "ok",
                    "message": msg,
                    "active_profile_id": self.storage.get_active_profile_id(),
                    "profiles": self.storage.list_profiles(),
                    "config": config.to_dict(),
                    "courses": [c.to_dict() for c in courses],
                    "events": [e.to_dict() for e in events],
                    "current_week": current_week,
                }
            )
        except Exception as e:
            logger.error(f"[{PLUGIN_NAME}] api_profiles_manage error: {e}")
            return error_response(str(e), status=400)

    async def api_config_save(self):
        """POST /astrbot_plugin_schedule/config/save"""
        try:
            body = await self._parse_request_json()
            updated = self.storage.update_config(body)
            current_week = self.engine.calculate_teaching_week(
                self.engine.resolve_date_str("today"), updated
            )
            return json_response(
                {
                    "status": "ok",
                    "message": "学期与作息配置已保存",
                    "config": updated.to_dict(),
                    "current_week": current_week,
                }
            )
        except Exception as e:
            logger.error(f"[{PLUGIN_NAME}] api_config_save error: {e}")
            return error_response(str(e), status=400)

    async def api_import(self):
        """POST /astrbot_plugin_schedule/import
        JSON Body: {file_type: 'ics'|'json', content: string, mode: 'overwrite'|'merge'}
        """
        try:
            body = await self._parse_request_json()
            file_type = str(body.get("file_type", "")).strip().lower().lstrip(".")
            content = str(body.get("content", ""))
            mode = str(body.get("mode", "overwrite")).strip().lower()
            if mode not in ("overwrite", "merge"):
                mode = "overwrite"

            if not content.strip():
                return error_response("导入的文件内容为空", status=400)

            config = self.storage.load_config()

            if file_type == "ics":
                parsed_courses = ScheduleParser.parse_ics_content(content, config)
            elif file_type == "json":
                parsed_courses, extracted_cfg = ScheduleParser.parse_json_content(content)
                if extracted_cfg:
                    config = self.storage.update_config(extracted_cfg)
            else:
                return error_response(f"不支持的文件类型: {file_type}（仅支持 ics 或 json）", status=400)

            final_courses = self.storage.import_courses(parsed_courses, mode=mode)
            return json_response(
                {
                    "status": "ok",
                    "message": f"成功解析 {len(parsed_courses)} 门课程（模式：{mode}）",
                    "imported_count": len(parsed_courses),
                    "total_count": len(final_courses),
                    "courses": [c.to_dict() for c in final_courses],
                    "config": config.to_dict(),
                }
            )
        except Exception as e:
            logger.error(f"[{PLUGIN_NAME}] api_import error: {e}")
            return error_response(f"导入失败：{str(e)}", status=400)

    async def api_export(self):
        """GET | POST /astrbot_plugin_schedule/export
        参数/Body: {file_type: 'ics'|'json'|'json_courses', profile_id: optional, include_events: bool}
        """
        try:
            body = {}
            if getattr(request, "method", "POST") == "POST":
                body = await self._parse_request_json()
            elif hasattr(request, "query"):
                body = {
                    "file_type": request.query.get("file_type", "ics"),
                    "profile_id": request.query.get("profile_id", ""),
                }

            file_type = str(body.get("file_type", "ics")).strip().lower()
            profile_id = str(body.get("profile_id", "")).strip() or None
            include_events = bool(body.get("include_events", True))

            pid = self.storage.resolve_profile_id(profile_id)
            config = self.storage.load_config(profile_id=pid)
            courses = self.storage.load_courses(profile_id=pid)
            events = self.storage.load_events(profile_id=pid) if include_events else []

            safe_name = config.semester_name.replace(" ", "_").replace("/", "-") or "schedule"

            if file_type == "ics":
                content = ScheduleParser.export_ics_content(
                    config=config, courses=courses, events=events, include_events=include_events
                )
                filename = f"{safe_name}.ics"
                mime = "text/calendar;charset=utf-8"
            elif file_type == "json_courses":
                content = ScheduleParser.export_json_content(
                    config=config, courses=courses, courses_only=True
                )
                filename = f"{safe_name}_courses.json"
                mime = "application/json;charset=utf-8"
            else:
                content = ScheduleParser.export_json_content(
                    config=config, courses=courses, events=events, courses_only=False
                )
                filename = f"{safe_name}_backup.json"
                mime = "application/json;charset=utf-8"

            return json_response(
                {
                    "status": "ok",
                    "filename": filename,
                    "mime": mime,
                    "content": content,
                    "course_count": len(courses),
                }
            )
        except Exception as e:
            logger.error(f"[{PLUGIN_NAME}] api_export error: {e}")
            return error_response(f"导出失败：{str(e)}", status=400)

    async def api_course_save(self):
        """POST /astrbot_plugin_schedule/course/save"""
        try:
            body = await self._parse_request_json()
            course_payload = body.get("course") if isinstance(body.get("course"), dict) else body
            if not str(course_payload.get("name", "")).strip():
                return error_response("课程名称不能为空", status=400)

            saved_course = self.storage.upsert_course(course_payload)
            courses = self.storage.load_courses()
            return json_response(
                {
                    "status": "ok",
                    "message": f"课程《{saved_course.name}》已保存",
                    "course": saved_course.to_dict(),
                    "courses": [c.to_dict() for c in courses],
                }
            )
        except Exception as e:
            logger.error(f"[{PLUGIN_NAME}] api_course_save error: {e}")
            return error_response(str(e), status=400)

    async def api_course_delete(self):
        """POST /astrbot_plugin_schedule/course/delete
        支持删除单门课程 ({id: string}) 或清空全部固定课程 ({clear_all: true} / {id: '__ALL__'})
        """
        try:
            body = await self._parse_request_json()
            clear_all = bool(body.get("clear_all", False))
            course_id = str(body.get("id", "")).strip()

            if clear_all or course_id == "__ALL__":
                removed_count = self.storage.clear_all_courses()
                return json_response(
                    {
                        "status": "ok",
                        "deleted": True,
                        "removed_count": removed_count,
                        "message": f"已清空全部固定课程（共删除 {removed_count} 门课程）",
                        "courses": [],
                    }
                )

            course_ids = body.get("ids")
            if isinstance(course_ids, list) and course_ids:
                removed_count = self.storage.delete_courses_by_ids(course_ids)
                courses = self.storage.load_courses()
                return json_response(
                    {
                        "status": "ok",
                        "deleted": removed_count > 0,
                        "removed_count": removed_count,
                        "message": f"已删除该课程的全部 {removed_count} 个时段",
                        "courses": [c.to_dict() for c in courses],
                    }
                )

            if not course_id:
                return error_response("缺少课程 id 参数", status=400)

            deleted = self.storage.delete_course(course_id)
            courses = self.storage.load_courses()
            return json_response(
                {
                    "status": "ok",
                    "deleted": deleted,
                    "message": "课程已删除" if deleted else "未找到对应课程",
                    "courses": [c.to_dict() for c in courses],
                }
            )
        except Exception as e:
            logger.error(f"[{PLUGIN_NAME}] api_course_delete error: {e}")
            return error_response(str(e), status=400)

    async def api_events_list(self):
        """GET /astrbot_plugin_schedule/events"""
        try:
            events = self.storage.load_events()
            return json_response(
                {
                    "status": "ok",
                    "events": [e.to_dict() for e in events],
                }
            )
        except Exception as e:
            logger.error(f"[{PLUGIN_NAME}] api_events_list error: {e}")
            return error_response(str(e), status=500)

    async def api_events_save(self):
        """POST /astrbot_plugin_schedule/events/save
        JSON Body: {action: 'save'|'delete', event: object}
        """
        try:
            body = await self._parse_request_json()
            action = str(body.get("action", "save")).strip().lower()
            event_payload = body.get("event") if isinstance(body.get("event"), dict) else body

            if action == "delete":
                event_id = str(event_payload.get("id") or body.get("id") or "").strip()
                if not event_id:
                    return error_response("删除日程缺少 id 参数", status=400)
                deleted = self.storage.delete_event_by_id(event_id)
                events = self.storage.load_events()
                return json_response(
                    {
                        "status": "ok",
                        "deleted": deleted,
                        "message": "日程记录已删除" if deleted else "未找到该日程记录",
                        "events": [e.to_dict() for e in events],
                    }
                )
            else:
                if not str(event_payload.get("date", "")).strip():
                    return error_response("日程日期不能为空", status=400)
                if not str(event_payload.get("title", "")).strip():
                    return error_response("日程标题或课程名不能为空", status=400)

                saved_evt = self.storage.upsert_event(event_payload)
                events = self.storage.load_events()
                return json_response(
                    {
                        "status": "ok",
                        "message": "日程记录已保存",
                        "event": saved_evt.to_dict(),
                        "events": [e.to_dict() for e in events],
                    }
                )
        except Exception as e:
            logger.error(f"[{PLUGIN_NAME}] api_events_save error: {e}")
            return error_response(str(e), status=400)

