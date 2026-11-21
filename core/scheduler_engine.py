from datetime import datetime, date, timedelta
from typing import List, Dict, Any, Tuple, Optional
from dateutil import parser as date_parser

from .models import SemesterConfig, Course, ScheduleEvent
from .storage import ScheduleStorage


WEEKDAY_CN = {
    1: "周一",
    2: "周二",
    3: "周三",
    4: "周四",
    5: "周五",
    6: "周六",
    7: "周日",
}

CN_WEEKDAY_MAP = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "日": 7,
    "天": 7,
}


class SchedulerEngine:
    """日程推算引擎：根据开学第一周周一日期计算教学周、单双周，合并固定课程与临时调课，输出自然语言摘要。"""

    def __init__(self, storage: ScheduleStorage):
        self.storage = storage

    @staticmethod
    def resolve_date_str(date_str: str, base_date: Optional[date] = None) -> date:
        """
        将输入字符串解析为目标日期 date。
        支持：
        - today / 今天 / 今日
        - tomorrow / 明天 / 明日
        - day_after_tomorrow / 后天
        - yesterday / 昨天
        - 周一~周日 / 下周一~下周日
        - YYYY-MM-DD 或其他常见日期格式
        """
        today = base_date or date.today()
        raw = (date_str or "today").strip().lower()

        if raw in ("", "today", "今天", "今日", "now"):
            return today
        if raw in ("tomorrow", "明天", "明日"):
            return today + timedelta(days=1)
        if raw in ("day_after_tomorrow", "后天", "后日"):
            return today + timedelta(days=2)
        if raw in ("yesterday", "昨天", "昨日"):
            return today - timedelta(days=1)

        # 兼容“本周X”、“周X”、“星期X”、“下周X”
        for prefix, week_offset in (("下周", 7), ("下星期", 7), ("本周", 0), ("这周", 0), ("周", 0), ("星期", 0)):
            if raw.startswith(prefix) and len(raw) > len(prefix):
                tail = raw[len(prefix):].strip()
                if tail in CN_WEEKDAY_MAP:
                    target_wd = CN_WEEKDAY_MAP[tail]
                    current_monday = today - timedelta(days=today.weekday())
                    return current_monday + timedelta(days=week_offset + (target_wd - 1))

        try:
            return datetime.strptime(raw, "%Y-%m-%d").date()
        except ValueError:
            pass

        try:
            dt = date_parser.parse(raw)
            return dt.date()
        except Exception:
            raise ValueError(
                f"无法解析日期参数 '{date_str}'，请使用 'today'、'tomorrow'、'day_after_tomorrow' 或 'YYYY-MM-DD' 格式。"
            )

    @staticmethod
    def calculate_teaching_week(target_date: date, config: SemesterConfig) -> int:
        """计算目标日期所在的教学周数 = (目标日期 - start_date).days // 7 + 1"""
        try:
            start_dt = datetime.strptime(config.start_date, "%Y-%m-%d").date()
        except Exception:
            start_dt = target_date - timedelta(days=target_date.weekday())
        return (target_date - start_dt).days // 7 + 1

    def get_current_teaching_week(self, base_date: Optional[date] = None) -> int:
        config = self.storage.load_config()
        target = base_date or date.today()
        return self.calculate_teaching_week(target, config)

    @staticmethod
    def get_course_time_range(
        course: Course, config: SemesterConfig
    ) -> Tuple[str, str]:
        """根据课程起止节次与学期作息表获取 (start_time, end_time)。"""
        p_times = config.period_times or {}
        start_info = p_times.get(str(course.start_period), {})
        end_info = p_times.get(str(course.end_period), {})
        start_time = start_info.get("start", "08:00")
        end_time = end_info.get("end", "09:40")
        return start_time, end_time

    def get_day_schedule_items(
        self, target_date: date, profile_id: Optional[str] = None
    ) -> Tuple[SemesterConfig, int, List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        获取指定日期的完整日程安排：
        返回 (config, week_num, active_items, cancelled_items)
        - active_items: 按时间排序的生效课程与临时日程列表
        - cancelled_items: 当日已停课的记录列表
        """
        pid = self.storage.resolve_profile_id(profile_id)
        config = self.storage.load_config(profile_id=pid)
        courses = self.storage.load_courses(profile_id=pid)
        events = self.storage.load_events(profile_id=pid)

        week_num = self.calculate_teaching_week(target_date, config)
        day_of_week = target_date.weekday() + 1  # 1 ~ 7
        target_date_str = target_date.strftime("%Y-%m-%d")

        # 1. 筛选当天 events
        day_events = [e for e in events if e.date == target_date_str]
        cancel_events = [e for e in day_events if e.type == "cancel_course"]
        custom_events = [e for e in day_events if e.type == "custom_event"]

        cancelled_course_ids = {
            e.target_course_id for e in cancel_events if e.target_course_id
        }
        cancelled_course_titles = {
            e.title.strip().lower() for e in cancel_events if e.title.strip()
        }

        # 2. 筛选当天星期匹配且当前周在 weeks 内的固定课程
        active_items: List[Dict[str, Any]] = []
        cancelled_items: List[Dict[str, Any]] = []

        if 1 <= week_num <= config.total_weeks:
            for course in courses:
                if course.day_of_week != day_of_week:
                    continue
                if week_num not in course.weeks:
                    continue

                start_time, end_time = self.get_course_time_range(course, config)
                is_cancelled = (
                    course.id in cancelled_course_ids
                    or course.name.strip().lower() in cancelled_course_titles
                )

                item_dict = {
                    "kind": "course",
                    "id": course.id,
                    "title": course.name,
                    "teacher": course.teacher,
                    "location": course.location,
                    "start_period": course.start_period,
                    "end_period": course.end_period,
                    "start_time": start_time,
                    "end_time": end_time,
                }
                if is_cancelled:
                    cancelled_items.append(item_dict)
                else:
                    active_items.append(item_dict)

        # 3. 合并 custom_event 临时日程
        for evt in custom_events:
            active_items.append(
                {
                    "kind": "custom_event",
                    "id": evt.id,
                    "title": evt.title,
                    "location": evt.location,
                    "start_time": evt.start_time or "00:00",
                    "end_time": evt.end_time or "",
                    "note": evt.note,
                }
            )

        # 按开始时间排序
        active_items.sort(key=lambda x: (x.get("start_time") or "00:00", 0 if x["kind"] == "course" else 1))
        return config, week_num, active_items, cancelled_items

    def format_all_courses_summary(self, profile_id: Optional[str] = None) -> str:
        """返回当前学期全部固定课程的汇总列表（按星期和节次排序）。"""
        pid = self.storage.resolve_profile_id(profile_id)
        config = self.storage.load_config(profile_id=pid)
        courses = self.storage.load_courses(profile_id=pid)
        profiles = self.storage.list_profiles()
        prof_name = next((p["name"] for p in profiles if p["id"] == pid), config.semester_name)

        if not courses:
            return f"【课表：{prof_name}（{config.semester_name}）｜全部固定课程】\n当前课表为空，尚未添加任何固定课程。"

        sorted_courses = sorted(
            courses, key=lambda c: (c.day_of_week, c.start_period, c.end_period, c.name)
        )
        lines = [f"【课表：{prof_name}（{config.semester_name}）｜全部固定课程列表（共 {len(sorted_courses)} 门）】"]
        for idx, c in enumerate(sorted_courses, start=1):
            wd = WEEKDAY_CN.get(c.day_of_week, f"周{c.day_of_week}")
            p_text = (
                f"第{c.start_period}节"
                if c.start_period == c.end_period
                else f"第{c.start_period}-{c.end_period}节"
            )
            start_t, end_t = self.get_course_time_range(c, config)
            w_min, w_max = (min(c.weeks), max(c.weeks)) if c.weeks else (1, 16)
            w_text = f"{w_min}-{w_max}周" if w_min != w_max else f"第{w_min}周"
            line = f"{idx}. [{wd} {p_text} ({start_t}-{end_t})] {c.name}｜周次：{w_text}"
            if c.location:
                line += f"｜地点：{c.location}"
            if c.teacher:
                line += f"｜教师：{c.teacher}"
            lines.append(line)
        return "\n".join(lines)

    def format_profiles_summary(self) -> str:
        """返回所有已保存的课表档案列表。"""
        profiles = self.storage.list_profiles()
        lines = [f"【已保存的课表列表（共 {len(profiles)} 份）】"]
        for idx, p in enumerate(profiles, start=1):
            active_tag = " [当前正在使用]" if p.get("is_active") else ""
            lines.append(
                f"{idx}. {p['name']}{active_tag}｜学期：{p['semester_name']}｜共 {p['course_count']} 门课"
            )
        return "\n".join(lines)

    def format_schedule_query(
        self, date_str: str, base_date: Optional[date] = None, profile_name: str = ""
    ) -> str:
        """
        实现 query_schedule 工具的返回格式：
        结构化自然语言文本：
        【2026-11-23（周一）｜第 12 教学周】
        1. 08:00-09:40（第1-2节）高等数学｜地点：致远楼302
        2. [临时日程] 16:30-17:30 班级团建会议
        """
        raw_q = (date_str or "").strip().lower()
        if raw_q in ("profiles", "list_profiles", "课表列表", "所有课表", "全部课表"):
            return self.format_profiles_summary()

        pid = self.storage.resolve_profile_id(profile_name) if profile_name else self.storage.get_active_profile_id()

        if raw_q in ("all", "all_courses", "全部", "所有课程", "全部课程"):
            return self.format_all_courses_summary(profile_id=pid)
        target_date = self.resolve_date_str(date_str, base_date=base_date)
        config, week_num, active_items, cancelled_items = self.get_day_schedule_items(
            target_date, profile_id=pid
        )

        date_fmt = target_date.strftime("%Y-%m-%d")
        weekday_str = WEEKDAY_CN.get(target_date.weekday() + 1, "")
        parity_str = "单周" if week_num % 2 == 1 else "双周"

        if week_num < 1:
            week_label = f"未开学（距第一周还有 {abs(week_num - 1)} 周）"
        elif week_num > config.total_weeks:
            week_label = f"已结课/假期（第 {week_num} 周，总 {config.total_weeks} 周）"
        else:
            week_label = f"第 {week_num} 教学周（{parity_str}）"

        lines = [f"【{date_fmt}（{weekday_str}）｜{week_label}】"]

        if not active_items:
            lines.append("今日暂无课程或临时日程安排。")
        else:
            for idx, item in enumerate(active_items, start=1):
                if item["kind"] == "course":
                    p_start = item["start_period"]
                    p_end = item["end_period"]
                    p_text = f"第{p_start}节" if p_start == p_end else f"第{p_start}-{p_end}节"
                    time_range = f"{item['start_time']}-{item['end_time']}"
                    line = f"{idx}. {time_range}（{p_text}）{item['title']}"
                    if item.get("location"):
                        line += f"｜地点：{item['location']}"
                    if item.get("teacher"):
                        line += f"｜教师：{item['teacher']}"
                    lines.append(line)
                else:
                    start_t = item.get("start_time") or ""
                    end_t = item.get("end_time") or ""
                    if start_t and end_t:
                        time_part = f"{start_t}-{end_t} "
                    elif start_t and start_t != "00:00":
                        time_part = f"{start_t} "
                    else:
                        time_part = ""
                    line = f"{idx}. [临时日程] {time_part}{item['title']}"
                    if item.get("location"):
                        line += f"｜地点：{item['location']}"
                    if item.get("note"):
                        line += f"｜备注：{item['note']}"
                    lines.append(line)

        if cancelled_items:
            cancelled_names = "、".join(c["title"] for c in cancelled_items)
            lines.append(f"（注：今日已停课：{cancelled_names}）")

        return "\n".join(lines)

    def handle_manage_event(
        self,
        action: str,
        date_str: str,
        title: str,
        start_time: str = "",
        end_time: str = "",
        location: str = "",
        note: str = "",
        base_date: Optional[date] = None,
    ) -> str:
        """
        处理 manage_schedule_event 工具调用：
        1. add_event：向 events.json 写入新的 custom_event。
        2. cancel_course：匹配当日对应课程名，写入 cancel_course 停课标记。
        3. delete_event：根据日期和标题删除指定事件。
        """
        target_date = self.resolve_date_str(date_str, base_date=base_date)
        target_date_str = target_date.strftime("%Y-%m-%d")
        act = (action or "").strip().lower()
        clean_title = (title or "").strip()

        if act == "clear_all_courses":
            count = self.storage.clear_all_courses()
            return f"已成功清空当前课表的全部固定课程（共删除 {count} 门课程）。"

        if act == "switch_profile":
            if not clean_title:
                return "切换课表失败：请在 title 参数中提供要切换的目标课表名称。"
            info = self.storage.switch_profile(clean_title)
            return f"已成功切换当前激活课表为：《{info['name']}》。"

        if act == "create_profile":
            if not clean_title:
                return "新建课表失败：请在 title 参数中提供新课表的名称。"
            info = self.storage.create_profile(name=clean_title, semester_name=clean_title, switch_to_new=True)
            return f"已成功创建并切换到新课表：《{info['name']}》。"

        if not clean_title:
            return "操作失败：请提供具体的课程名称或日程标题（title）。"

        if act == "add_event":
            evt = ScheduleEvent(
                date=target_date_str,
                type="custom_event",
                target_course_id=None,
                title=clean_title,
                start_time=(start_time or "").strip(),
                end_time=(end_time or "").strip(),
                location=(location or "").strip(),
                note=(note or "").strip(),
            )
            self.storage.upsert_event(evt.to_dict())

            time_desc = ""
            if evt.start_time and evt.end_time:
                time_desc = f"{evt.start_time}-{evt.end_time} "
            elif evt.start_time:
                time_desc = f"{evt.start_time} "

            loc_desc = f"（{evt.location}）" if evt.location else ""
            return f"已成功在 {target_date_str} 添加临时日程：{time_desc}{evt.title}{loc_desc}。"

        elif act == "cancel_course":
            config = self.storage.load_config()
            courses = self.storage.load_courses()
            week_num = self.calculate_teaching_week(target_date, config)
            day_of_week = target_date.weekday() + 1

            # 优先匹配当日生效的固定课程，其次匹配当日星期的固定课程，再次全局模糊匹配课程名
            day_courses = [
                c for c in courses if c.day_of_week == day_of_week and week_num in c.weeks
            ]
            if not day_courses:
                day_courses = [c for c in courses if c.day_of_week == day_of_week]

            matched_course: Optional[Course] = None
            for c in day_courses:
                if clean_title.lower() in c.name.lower() or c.name.lower() in clean_title.lower():
                    matched_course = c
                    break

            if matched_course is None:
                for c in courses:
                    if clean_title.lower() in c.name.lower() or c.name.lower() in clean_title.lower():
                        matched_course = c
                        break

            target_course_id = matched_course.id if matched_course else None
            course_display_name = matched_course.name if matched_course else clean_title

            # 避免重复添加同一天的同一课程停课记录
            existing_events = self.storage.load_events()
            for ex in existing_events:
                if (
                    ex.date == target_date_str
                    and ex.type == "cancel_course"
                    and (
                        (target_course_id and ex.target_course_id == target_course_id)
                        or ex.title.lower() == course_display_name.lower()
                    )
                ):
                    return f"{target_date_str} 的《{course_display_name}》已存在停课记录，无需重复标记。"

            evt = ScheduleEvent(
                date=target_date_str,
                type="cancel_course",
                target_course_id=target_course_id,
                title=course_display_name,
                start_time=(start_time or "").strip(),
                end_time=(end_time or "").strip(),
                location=(location or (matched_course.location if matched_course else "")).strip(),
                note=(note or "临时停课").strip(),
            )
            self.storage.upsert_event(evt.to_dict())
            return f"已成功在 {target_date_str} 标记课程停课：《{course_display_name}》。"

        elif act == "delete_event":
            events = self.storage.load_events()
            remaining: List[ScheduleEvent] = []
            deleted_titles: List[str] = []

            for e in events:
                if e.date == target_date_str and (
                    clean_title.lower() in e.title.lower() or e.title.lower() in clean_title.lower()
                ):
                    deleted_titles.append(e.title)
                else:
                    remaining.append(e)

            if not deleted_titles:
                return f"未在 {target_date_str} 找到标题包含“{clean_title}”的临时日程或停课记录。"

            self.storage.save_events(remaining)
            joined = "、".join(deleted_titles)
            return f"已成功删除 {target_date_str} 的日程/调课记录：{joined}。"

        elif act == "clear_all_courses":
            count = self.storage.clear_all_courses()
            return f"已成功清空全部固定课程（共删除 {count} 门课程）。"

        elif act == "delete_course":
            courses = self.storage.load_courses()
            remaining_courses: List[Course] = []
            deleted_names: List[str] = []
            for c in courses:
                if clean_title.lower() in c.name.lower() or c.name.lower() in clean_title.lower():
                    deleted_names.append(f"{c.name}({WEEKDAY_CN.get(c.day_of_week, '')}第{c.start_period}-{c.end_period}节)")
                else:
                    remaining_courses.append(c)
            if not deleted_names:
                return f"未在固定课表中找到名称包含“{clean_title}”的课程。"
            self.storage.save_courses(remaining_courses)
            return f"已从固定课表中永久删除课程：{'、'.join(deleted_names)}。"

        else:
            return (
                f"不支持的操作类型 '{action}'。请使用 'add_event'（新增临时日程）、"
                f"'cancel_course'（标记停课）、'delete_event'（删除事件）、"
                f"'delete_course'（删除某门固定课程）或 'clear_all_courses'（清空所有固定课程）。"
            )
