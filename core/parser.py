import json
import re
import uuid
from datetime import datetime, date, time, timedelta
from typing import List, Dict, Any, Tuple, Optional

from dateutil import rrule
from dateutil import parser as date_parser

try:
    from icalendar import Calendar
except ImportError:
    Calendar = None

from .models import Course, SemesterConfig, COURSE_PALETTE


def _time_to_minutes(t_str: str) -> int:
    """将 'HH:MM' 转换为从 00:00 起的分钟数。"""
    try:
        parts = t_str.strip().split(":")
        return int(parts[0]) * 60 + int(parts[1])
    except Exception:
        return 8 * 60


def _match_periods(
    start_dt: datetime, end_dt: datetime, period_times: Dict[str, Dict[str, str]]
) -> Tuple[int, int]:
    """根据事件的起止时间与学期作息表反推对应的起止节次 (start_period, end_period)。"""
    if not period_times:
        return 1, 2

    evt_start_min = start_dt.hour * 60 + start_dt.minute
    evt_end_min = end_dt.hour * 60 + end_dt.minute
    if evt_end_min <= evt_start_min:
        evt_end_min = evt_start_min + 45

    sorted_periods: List[Tuple[int, int, int]] = []
    for p_str, p_val in period_times.items():
        if not str(p_str).isdigit():
            continue
        p_num = int(p_str)
        p_start = _time_to_minutes(p_val.get("start", "08:00"))
        p_end = _time_to_minutes(p_val.get("end", "08:45"))
        sorted_periods.append((p_num, p_start, p_end))

    if not sorted_periods:
        return 1, 2

    sorted_periods.sort(key=lambda x: x[0])

    # 找最接近的起始节次
    best_start_p = sorted_periods[0][0]
    min_start_diff = 99999
    for p_num, p_start, p_end in sorted_periods:
        diff = abs(evt_start_min - p_start)
        if diff < min_start_diff:
            min_start_diff = diff
            best_start_p = p_num

    # 找最接近的结束节次
    best_end_p = best_start_p
    min_end_diff = 99999
    for p_num, p_start, p_end in sorted_periods:
        if p_num < best_start_p:
            continue
        diff = abs(evt_end_min - p_end)
        if diff < min_end_diff:
            min_end_diff = diff
            best_end_p = p_num

    return best_start_p, max(best_start_p, best_end_p)


def _extract_teacher_and_location(
    summary: str, location: str, description: str
) -> Tuple[str, str, str]:
    """
    从 ICS 的 SUMMARY / LOCATION / DESCRIPTION 中提取课程名、教师、地点。
    兼容 WakeUp课程表、小爱课程表、教务处导出格式。
    """
    course_name = (summary or "").strip()
    loc = (location or "").strip()
    desc = (description or "").strip()
    teacher = ""

    # 一些导出会把课名写成 "高等数学@致远楼302"
    if not loc and "@" in course_name:
        parts = course_name.split("@", 1)
        course_name = parts[0].strip()
        loc = parts[1].strip()

    # WakeUp 导出的 LOCATION 常常是 "天工南212 王新蕾"（教室 + 空格 + 教师姓名）或 "王新蕾 天工南212"
    if loc and " " in loc and not teacher:
        loc_parts = [p.strip() for p in loc.split() if p.strip()]
        if len(loc_parts) == 2:
            p0, p1 = loc_parts[0], loc_parts[1]
            p0_is_room = bool(re.search(r"(\d|楼|室|馆|区|场|教|院|堂|实验室|中心)", p0))
            p1_is_room = bool(re.search(r"(\d|楼|室|馆|区|场|教|院|堂|实验室|中心)", p1))
            if p0_is_room and not p1_is_room:
                loc, teacher = p0, p1
            elif p1_is_room and not p0_is_room:
                loc, teacher = p1, p0
            elif len(p1) <= 6 and not re.search(r"\d", p1):
                loc, teacher = p0, p1

    # 解析 description 中的教师与地点信息
    # WakeUp / 小爱常见格式：
    # "教师: 张三\n地点: 致远楼302" 或 "第1-2节 | 天工102 | 周继振"
    if desc:
        teacher_match = re.search(
            r"(?:教师|老师|任课教师|Teacher)[：:\s]+([^\n\r|，,;；]+)", desc, re.IGNORECASE
        )
        if teacher_match:
            teacher = teacher_match.group(1).strip()

        if not loc:
            loc_match = re.search(
                r"(?:教室|地点|位置|Location)[：:\s]+([^\n\r|]+)", desc, re.IGNORECASE
            )
            if loc_match:
                loc = loc_match.group(1).strip()

        # 如果依然未提取出教师，检查竖线或换行分割的简短字段
        if not teacher:
            lines = [line.strip() for line in re.split(r"[\n\r|]+", desc) if line.strip()]
            for item in lines:
                # 排除周次、节次描述或已包含在地点中的教室名
                if re.search(r"(第.+节|第.+周|\d+-\d+周|周[一二三四五六日天]|\d{2}:\d{2})", item):
                    continue
                if loc and (item == loc or item in loc or loc in item):
                    continue
                if 1 < len(item) <= 15 and not item.startswith("http"):
                    teacher = item
                    break

    return course_name or "未命名课程", teacher, loc


def _to_naive_local_datetime(dt_val: Any) -> Optional[datetime]:
    """将 icalendar 的 vDDDTypes / date / datetime 转为本地无时区 datetime。"""
    if hasattr(dt_val, "dt"):
        dt_val = dt_val.dt
    if isinstance(dt_val, datetime):
        if dt_val.tzinfo is not None:
            dt_val = dt_val.astimezone().replace(tzinfo=None)
        return dt_val
    if isinstance(dt_val, date):
        return datetime.combine(dt_val, time(8, 0))
    return None


def _expand_vevent_dates(component: Any, max_end_date: date) -> List[Tuple[datetime, datetime]]:
    """展开单个 VEVENT 的所有发生时间段（处理 RRULE, RDATE, EXDATE）。"""
    dtstart_raw = component.get("DTSTART")
    if not dtstart_raw:
        return []
    start_dt = _to_naive_local_datetime(dtstart_raw)
    if not start_dt:
        return []

    dtend_raw = component.get("DTEND")
    end_dt = _to_naive_local_datetime(dtend_raw) if dtend_raw else None
    if not end_dt or end_dt <= start_dt:
        duration_raw = component.get("DURATION")
        if duration_raw and hasattr(duration_raw, "dt") and isinstance(duration_raw.dt, timedelta):
            end_dt = start_dt + duration_raw.dt
        else:
            end_dt = start_dt + timedelta(minutes=90)

    duration = end_dt - start_dt

    # 收集排除日期 EXDATE
    exdates_set = set()
    exdate_prop = component.get("EXDATE")
    if exdate_prop:
        if not isinstance(exdate_prop, list):
            exdate_prop = [exdate_prop]
        for ex_item in exdate_prop:
            dts = getattr(ex_item, "dts", [ex_item])
            for d in dts:
                ex_dt = _to_naive_local_datetime(d)
                if ex_dt:
                    exdates_set.add(ex_dt.date())

    rrule_prop = component.get("RRULE")
    if not rrule_prop:
        if start_dt.date() in exdates_set:
            return []
        return [(start_dt, end_dt)]

    # 将 vRecur 转换为 RFC 字符串并用 dateutil.rrule 展开
    try:
        rrule_str = rrule_prop.to_ical().decode("utf-8")
        # 如果 UNTIL 带了 Z (UTC)，转为无时区格式以避免与 naive start_dt 冲突
        if "UNTIL=" in rrule_str:
            def _clean_until(m: re.Match) -> str:
                val = m.group(1).rstrip("Z")
                return f"UNTIL={val}"
            rrule_str = re.sub(r"UNTIL=([0-9TZ]+)", _clean_until, rrule_str)

        rule = rrule.rrulestr(rrule_str, dtstart=start_dt, ignoretz=True)
        limit_dt = datetime.combine(max_end_date, time(23, 59, 59))
        occurrences = []
        for occ_start in rule:
            if occ_start > limit_dt:
                break
            if occ_start.date() in exdates_set:
                continue
            occurrences.append((occ_start, occ_start + duration))
            if len(occurrences) >= 200:
                break
        return occurrences if occurrences else [(start_dt, end_dt)]
    except Exception:
        return [(start_dt, end_dt)]


class ScheduleParser:
    """解析上传的 .ics（展开 RRULE 重复规则并反推周次与节次）和 .json 课表文件。"""

    @staticmethod
    def parse_json_content(content: str) -> Tuple[List[Course], Optional[Dict[str, Any]]]:
        """
        解析 JSON 课表文件。
        兼容格式：
        1. 标准 Course 数组：[{"name": "高等数学", "day_of_week": 1, "start_period": 1, "end_period": 2, "weeks": [1,2,...]}]
        2. 包含 config 与 courses 的完整导出对象：{"config": {...}, "courses": [...]}
        3. 常见第三方字段别名（如 course_name / weekday / day / start_section / end_section / room）。
        """
        data = json.loads(content.strip())
        extracted_config: Optional[Dict[str, Any]] = None

        if isinstance(data, dict):
            if "config" in data and isinstance(data["config"], dict):
                extracted_config = data["config"]
            raw_list = data.get("courses") or data.get("data") or data.get("schedule") or []
        elif isinstance(data, list):
            raw_list = data
        else:
            raise ValueError("JSON 课表格式无效：根节点必须是数组或包含 courses 字段的对象。")

        if not isinstance(raw_list, list):
            raise ValueError("JSON 课表 courses 字段必须是数组。")

        courses: List[Course] = []
        for idx, item in enumerate(raw_list):
            if not isinstance(item, dict):
                continue
            name = (
                item.get("name")
                or item.get("course_name")
                or item.get("courseName")
                or item.get("title")
                or ""
            )
            if not str(name).strip():
                continue

            teacher = item.get("teacher") or item.get("teacher_name") or item.get("instructor") or ""
            location = item.get("location") or item.get("room") or item.get("classroom") or item.get("place") or ""
            day_of_week = (
                item.get("day_of_week")
                or item.get("dayOfWeek")
                or item.get("weekday")
                or item.get("day")
                or 1
            )
            start_period = (
                item.get("start_period")
                or item.get("startPeriod")
                or item.get("start_section")
                or item.get("start_node")
                or 1
            )
            end_period = (
                item.get("end_period")
                or item.get("endPeriod")
                or item.get("end_section")
                or item.get("end_node")
                or start_period
            )
            weeks = item.get("weeks") or item.get("week_list") or list(range(1, 17))
            color = item.get("color") or COURSE_PALETTE[idx % len(COURSE_PALETTE)]

            course_dict = {
                "id": item.get("id") or f"course_{uuid.uuid4().hex[:8]}",
                "name": name,
                "teacher": teacher,
                "location": location,
                "day_of_week": day_of_week,
                "start_period": start_period,
                "end_period": end_period,
                "weeks": weeks,
                "color": color,
            }
            courses.append(Course.from_dict(course_dict))

        return courses, extracted_config

    @staticmethod
    def parse_ics_content(content: str, config: SemesterConfig) -> List[Course]:
        """
        解析 iCalendar (.ics) 课表文件：
        1. 展开所有 VEVENT 的 RRULE 重复规则（兼容单次事件与每周/隔周重复事件）。
        2. 根据 config.start_date（开学第一周周一）反推每次课所在的教学周次与星期几。
        3. 若所有事件均早于或远晚于当前 config.start_date，则智能对齐第一周周一。
        4. 根据 config.period_times 反推起止节次，并将同一门课程合并周次列表。
        """
        if Calendar is None:
            raise RuntimeError("未安装 icalendar 依赖库，请先执行 pip install icalendar>=5.0.0")

        cal = Calendar.from_ical(content)

        try:
            semester_start = datetime.strptime(config.start_date, "%Y-%m-%d").date()
        except Exception:
            today = date.today()
            semester_start = today - timedelta(days=today.weekday())

        # 对齐到周一
        semester_start_monday = semester_start - timedelta(days=semester_start.weekday())
        max_end_date = semester_start_monday + timedelta(days=max(config.total_weeks, 30) * 7)

        raw_occurrences: List[Dict[str, Any]] = []
        earliest_dt: Optional[date] = None

        for component in cal.walk():
            if component.name != "VEVENT":
                continue

            summary = str(component.get("SUMMARY", "")).strip()
            location = str(component.get("LOCATION", "")).strip()
            description = str(component.get("DESCRIPTION", "")).strip()

            course_name, teacher, loc = _extract_teacher_and_location(summary, location, description)

            # 第一遍粗略获取最早日期以设定合理展开上限
            dtstart_raw = component.get("DTSTART")
            first_dt = _to_naive_local_datetime(dtstart_raw) if dtstart_raw else None
            if not first_dt:
                continue

            local_max_end = max(max_end_date, first_dt.date() + timedelta(days=30 * 7))
            occ_list = _expand_vevent_dates(component, local_max_end)

            for occ_start, occ_end in occ_list:
                occ_date = occ_start.date()
                if earliest_dt is None or occ_date < earliest_dt:
                    earliest_dt = occ_date
                raw_occurrences.append(
                    {
                        "name": course_name,
                        "teacher": teacher,
                        "location": loc,
                        "start_dt": occ_start,
                        "end_dt": occ_end,
                        "description": description,
                    }
                )

        if not raw_occurrences:
            return []

        # 判断是否所有事件的日期落在当前学期范围内；若完全偏离，则以最早事件所在周的周一作为参考基准
        in_range_count = 0
        for occ in raw_occurrences:
            delta_days = (occ["start_dt"].date() - semester_start_monday).days
            week_num = delta_days // 7 + 1
            if 1 <= week_num <= config.total_weeks:
                in_range_count += 1

        ref_monday = semester_start_monday
        if in_range_count == 0 and earliest_dt is not None:
            ref_monday = earliest_dt - timedelta(days=earliest_dt.weekday())

        # 聚合相同 (name, teacher, location, day_of_week, start_period, end_period) 的周次
        grouped: Dict[Tuple[str, str, str, int, int, int], List[int]] = {}

        for occ in raw_occurrences:
            occ_date = occ["start_dt"].date()
            day_of_week = occ_date.weekday() + 1  # 1=周一 ... 7=周日
            week_num = (occ_date - ref_monday).days // 7 + 1

            if week_num < 1 or week_num > max(config.total_weeks, 30):
                continue

            start_p, end_p = _match_periods(occ["start_dt"], occ["end_dt"], config.period_times)

            # 如果 DESCRIPTION 中明确标明了 "第X-Y节"，可作为校准参考
            sec_match = re.search(r"第\s*(\d+)\s*[-~至到]\s*(\d+)\s*节", occ["description"])
            if sec_match:
                sp, ep = int(sec_match.group(1)), int(sec_match.group(2))
                if 1 <= sp <= ep <= 20:
                    start_p, end_p = sp, ep

            key = (
                occ["name"],
                occ["teacher"],
                occ["location"],
                day_of_week,
                start_p,
                end_p,
            )
            grouped.setdefault(key, []).append(week_num)

        courses: List[Course] = []
        color_map: Dict[str, str] = {}

        for (name, teacher, loc, day_of_week, start_p, end_p), weeks in grouped.items():
            if name not in color_map:
                color_map[name] = COURSE_PALETTE[len(color_map) % len(COURSE_PALETTE)]
            courses.append(
                Course(
                    id=f"course_{uuid.uuid4().hex[:8]}",
                    name=name,
                    teacher=teacher,
                    location=loc,
                    day_of_week=day_of_week,
                    start_period=start_p,
                    end_period=end_p,
                    weeks=sorted(list(set(weeks))),
                    color=color_map[name],
                )
            )

        courses.sort(key=lambda c: (c.day_of_week, c.start_period, c.name))
        return courses

    @staticmethod
    def export_json_content(
        config: SemesterConfig,
        courses: List[Course],
        events: Optional[List[Any]] = None,
        courses_only: bool = False,
    ) -> str:
        """导出为标准 JSON 课表格式（支持仅导出课程数组，或导出包含 config + courses + events 的完整备份包）。"""
        course_dicts = [c.to_dict() for c in courses]
        if courses_only:
            return json.dumps(course_dicts, ensure_ascii=False, indent=2)

        payload: Dict[str, Any] = {
            "config": config.to_dict(),
            "courses": course_dicts,
        }
        if events is not None:
            payload["events"] = [e.to_dict() if hasattr(e, "to_dict") else e for e in events]
        return json.dumps(payload, ensure_ascii=False, indent=2)

    @staticmethod
    def export_ics_content(
        config: SemesterConfig,
        courses: List[Course],
        events: Optional[List[Any]] = None,
        include_events: bool = True,
    ) -> str:
        """
        将当前课表与临时日程导出为标准 iCalendar (.ics) 格式，
        可直接导入到手机系统日历（iOS 日历、华为/小米/OPPO日历）、Outlook、Google Calendar、WakeUp课程表等。
        """
        try:
            semester_start = datetime.strptime(config.start_date, "%Y-%m-%d").date()
        except Exception:
            today = date.today()
            semester_start = today - timedelta(days=today.weekday())

        monday_start = semester_start - timedelta(days=semester_start.weekday())
        p_times = config.period_times or {}
        now_utc_str = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")

        def _escape_ics_text(val: str) -> str:
            return (
                str(val or "")
                .replace("\\", "\\\\")
                .replace(";", "\\;")
                .replace(",", "\\,")
                .replace("\r\n", "\\n")
                .replace("\n", "\\n")
            )

        def _parse_hhmm(hhmm: str, default_h: int = 8, default_m: int = 0) -> time:
            try:
                parts = hhmm.strip().split(":")
                return time(int(parts[0]), int(parts[1]))
            except Exception:
                return time(default_h, default_m)

        lines = [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//AstrBot//astrbot_plugin_schedule//CN",
            "CALSCALE:GREGORIAN",
            "METHOD:PUBLISH",
            f"X-WR-CALNAME:{_escape_ics_text(config.semester_name)}",
            "X-WR-TIMEZONE:Asia/Shanghai",
        ]

        # 收集當天已停课的 (date_str, course_id / course_title)
        cancelled_keys = set()
        if events:
            for e in events:
                e_type = getattr(e, "type", "") or (e.get("type") if isinstance(e, dict) else "")
                if e_type == "cancel_course":
                    e_date = getattr(e, "date", "") or (e.get("date") if isinstance(e, dict) else "")
                    e_cid = getattr(e, "target_course_id", "") or (e.get("target_course_id") if isinstance(e, dict) else "")
                    e_title = getattr(e, "title", "") or (e.get("title") if isinstance(e, dict) else "")
                    if e_date and e_cid:
                        cancelled_keys.add((e_date, str(e_cid)))
                    if e_date and e_title:
                        cancelled_keys.add((e_date, str(e_title).strip().lower()))

        # 1. 导出固定课程每个教学周的上课日历事件
        for course in courses:
            start_str = p_times.get(str(course.start_period), {}).get("start", "08:00")
            end_str = p_times.get(str(course.end_period), {}).get("end", "09:40")
            t_start = _parse_hhmm(start_str, 8, 0)
            t_end = _parse_hhmm(end_str, 9, 40)

            p_label = (
                f"第{course.start_period}节"
                if course.start_period == course.end_period
                else f"第{course.start_period}-{course.end_period}节"
            )

            for w in sorted(set(course.weeks)):
                if w < 1 or w > max(config.total_weeks, 30):
                    continue
                class_date = monday_start + timedelta(days=(w - 1) * 7 + (course.day_of_week - 1))
                class_date_str = class_date.strftime("%Y-%m-%d")

                # 如果当天该课程已标记停课，则跳过导出
                if (class_date_str, course.id) in cancelled_keys or (
                    class_date_str,
                    course.name.strip().lower(),
                ) in cancelled_keys:
                    continue

                dt_start = datetime.combine(class_date, t_start).strftime("%Y%m%dT%H%M%S")
                dt_end = datetime.combine(class_date, t_end).strftime("%Y%m%dT%H%M%S")
                uid = f"{course.id}_w{w}_{class_date.strftime('%Y%m%d')}@astrbot.schedule"

                desc_parts = [f"教学周：第{w}周", f"节次：{p_label}"]
                if course.teacher:
                    desc_parts.append(f"教师：{course.teacher}")
                if course.location:
                    desc_parts.append(f"教室：{course.location}")
                desc_text = "\\n".join(_escape_ics_text(p) for p in desc_parts)

                lines.extend(
                    [
                        "BEGIN:VEVENT",
                        f"UID:{uid}",
                        f"DTSTAMP:{now_utc_str}",
                        f"DTSTART;TZID=Asia/Shanghai:{dt_start}",
                        f"DTEND;TZID=Asia/Shanghai:{dt_end}",
                        f"SUMMARY:{_escape_ics_text(course.name)}",
                        f"LOCATION:{_escape_ics_text(course.location)}",
                        f"DESCRIPTION:{desc_text}",
                        "END:VEVENT",
                    ]
                )

        # 2. 导出临时日程 (custom_event)
        if include_events and events:
            for e in events:
                e_dict = e.to_dict() if hasattr(e, "to_dict") else (e if isinstance(e, dict) else {})
                if e_dict.get("type") != "custom_event":
                    continue
                e_date_str = str(e_dict.get("date", "")).strip()
                if not e_date_str:
                    continue
                try:
                    e_date = datetime.strptime(e_date_str, "%Y-%m-%d").date()
                except Exception:
                    continue

                t_start = _parse_hhmm(e_dict.get("start_time") or "09:00", 9, 0)
                t_end = _parse_hhmm(e_dict.get("end_time") or "10:00", 10, 0)
                if t_end <= t_start:
                    t_end = (datetime.combine(e_date, t_start) + timedelta(hours=1)).time()

                dt_start = datetime.combine(e_date, t_start).strftime("%Y%m%dT%H%M%S")
                dt_end = datetime.combine(e_date, t_end).strftime("%Y%m%dT%H%M%S")
                uid = f"{e_dict.get('id', uuid.uuid4().hex[:8])}@astrbot.schedule"

                lines.extend(
                    [
                        "BEGIN:VEVENT",
                        f"UID:{uid}",
                        f"DTSTAMP:{now_utc_str}",
                        f"DTSTART;TZID=Asia/Shanghai:{dt_start}",
                        f"DTEND;TZID=Asia/Shanghai:{dt_end}",
                        f"SUMMARY:[临时日程] {_escape_ics_text(e_dict.get('title', '临时日程'))}",
                        f"LOCATION:{_escape_ics_text(e_dict.get('location', ''))}",
                        f"DESCRIPTION:{_escape_ics_text(e_dict.get('note', ''))}",
                        "END:VEVENT",
                    ]
                )

        lines.append("END:VCALENDAR")
        return "\r\n".join(lines)
