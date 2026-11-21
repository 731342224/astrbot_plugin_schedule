from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Any
import uuid


DEFAULT_PERIOD_TIMES: Dict[str, Dict[str, str]] = {
    "1": {"start": "08:00", "end": "08:45"},
    "2": {"start": "08:55", "end": "09:40"},
    "3": {"start": "10:00", "end": "10:45"},
    "4": {"start": "10:55", "end": "11:40"},
    "5": {"start": "14:00", "end": "14:45"},
    "6": {"start": "14:55", "end": "15:40"},
    "7": {"start": "16:00", "end": "16:45"},
    "8": {"start": "16:55", "end": "17:40"},
    "9": {"start": "19:00", "end": "19:45"},
    "10": {"start": "19:55", "end": "20:40"},
    "11": {"start": "20:50", "end": "21:35"},
    "12": {"start": "21:45", "end": "22:30"},
}

COURSE_PALETTE = [
    "#4F46E5",  # Indigo
    "#0EA5E9",  # Sky
    "#10B981",  # Emerald
    "#F59E0B",  # Amber
    "#EC4899",  # Pink
    "#8B5CF6",  # Violet
    "#EF4444",  # Red
    "#14B8A6",  # Teal
    "#F97316",  # Orange
    "#3B82F6",  # Blue
]


@dataclass
class SemesterConfig:
    """学期与作息配置模型 (config.json)"""
    semester_name: str = "2026-2027第一学期"
    start_date: str = "2026-09-07"
    total_weeks: int = 20
    enable_reminder: bool = False
    remind_advance_minutes: int = 15
    period_times: Dict[str, Dict[str, str]] = field(
        default_factory=lambda: {k: dict(v) for k, v in DEFAULT_PERIOD_TIMES.items()}
    )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SemesterConfig":
        if not data:
            return cls()
        raw_periods = data.get("period_times")
        if not isinstance(raw_periods, dict) or not raw_periods:
            period_times = {k: dict(v) for k, v in DEFAULT_PERIOD_TIMES.items()}
        else:
            # 规范化键名为字符串 "1", "2", ...
            period_times = {}
            for k, v in raw_periods.items():
                if isinstance(v, dict):
                    period_times[str(k)] = {
                        "start": str(v.get("start", "08:00")),
                        "end": str(v.get("end", "08:45")),
                    }
        return cls(
            semester_name=str(data.get("semester_name", "2026-2027第一学期")),
            start_date=str(data.get("start_date", "2026-09-07")),
            total_weeks=max(1, int(data.get("total_weeks", 20))),
            enable_reminder=bool(data.get("enable_reminder", False)),
            remind_advance_minutes=max(1, int(data.get("remind_advance_minutes", 15))),
            period_times=period_times,
        )


@dataclass
class Course:
    """固定课程模型 (courses.json)"""
    id: str = ""
    name: str = ""
    teacher: str = ""
    location: str = ""
    day_of_week: int = 1  # 1 ~ 7 (周一至周日)
    start_period: int = 1
    end_period: int = 2
    weeks: List[int] = field(default_factory=lambda: list(range(1, 17)))
    color: str = "#4F46E5"

    def __post_init__(self):
        if not self.id:
            self.id = f"course_{uuid.uuid4().hex[:8]}"
        self.day_of_week = max(1, min(7, int(self.day_of_week)))
        self.start_period = max(1, int(self.start_period))
        self.end_period = max(self.start_period, int(self.end_period))
        if isinstance(self.weeks, list):
            self.weeks = sorted(list({int(w) for w in self.weeks if int(w) >= 1}))
        else:
            self.weeks = list(range(1, 17))
        if not self.color:
            color_idx = abs(hash(self.name)) % len(COURSE_PALETTE)
            self.color = COURSE_PALETTE[color_idx]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Course":
        weeks_raw = data.get("weeks", list(range(1, 17)))
        if isinstance(weeks_raw, str):
            weeks_list = [int(x.strip()) for x in weeks_raw.split(",") if x.strip().isdigit()]
        elif isinstance(weeks_raw, list):
            weeks_list = [int(w) for w in weeks_raw if str(w).isdigit()]
        else:
            weeks_list = list(range(1, 17))

        name = str(data.get("name", "")).strip()
        teacher = str(data.get("teacher", "")).strip()
        location = str(data.get("location", "")).strip()

        # 自动修复旧版本导入或 WakeUp ICS 导致的 location="天工南212 王新蕾", teacher="天工南212" 错位问题
        if location and " " in location:
            loc_parts = [p.strip() for p in location.split() if p.strip()]
            if len(loc_parts) == 2:
                part_room, part_teacher = loc_parts[0], loc_parts[1]
                # 若 teacher 为空，或者 teacher 误等于前面的教室名（如 "天工南212"），则纠正
                if not teacher or teacher == part_room or teacher == location:
                    location = part_room
                    teacher = part_teacher
        elif location and teacher and teacher == location:
            teacher = ""

        color = str(data.get("color", "")).strip()
        if not color:
            color = COURSE_PALETTE[abs(hash(name)) % len(COURSE_PALETTE)]

        start_p = int(data.get("start_period", 1))
        end_p = int(data.get("end_period", start_p))

        return cls(
            id=str(data.get("id", "")).strip() or f"course_{uuid.uuid4().hex[:8]}",
            name=name,
            teacher=teacher,
            location=location,
            day_of_week=int(data.get("day_of_week", 1)),
            start_period=start_p,
            end_period=max(start_p, end_p),
            weeks=weeks_list,
            color=color,
        )


@dataclass
class ScheduleEvent:
    """调课与临时日程模型 (events.json)"""
    id: str = ""
    date: str = ""  # YYYY-MM-DD
    type: str = "custom_event"  # "cancel_course" 或 "custom_event"
    target_course_id: Optional[str] = None
    title: str = ""
    start_time: str = ""
    end_time: str = ""
    location: str = ""
    note: str = ""

    def __post_init__(self):
        if not self.id:
            self.id = f"evt_{uuid.uuid4().hex[:8]}"
        if self.type not in ("cancel_course", "custom_event"):
            self.type = "custom_event"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ScheduleEvent":
        target_id = data.get("target_course_id")
        if target_id is not None:
            target_id = str(target_id).strip() or None
        return cls(
            id=str(data.get("id", "")).strip() or f"evt_{uuid.uuid4().hex[:8]}",
            date=str(data.get("date", "")).strip(),
            type=str(data.get("type", "custom_event")).strip(),
            target_course_id=target_id,
            title=str(data.get("title", "")).strip(),
            start_time=str(data.get("start_time", "")).strip(),
            end_time=str(data.get("end_time", "")).strip(),
            location=str(data.get("location", "")).strip(),
            note=str(data.get("note", "")).strip(),
        )
