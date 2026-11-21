import json
import os
import threading
import uuid
from pathlib import Path
from typing import List, Optional, Dict, Any

from .models import SemesterConfig, Course, ScheduleEvent


class ScheduleStorage:
    """
    管理 data/plugin_data/astrbot_plugin_schedule/ 下的 JSON 文件线程安全读写。
    支持多份课表（Multiple Schedule Profiles）：
    - 默认主课表直接存储在根目录 config.json / courses.json / events.json（100% 兼容原有规范）
    - 新增 profiles.json 记录所有课表元数据与当前激活课表 ID (active_profile_id)
    - 其他新增课表存储在 profiles/<profile_id>/ 目录下（各自拥有独立的 config.json, courses.json, events.json）
    """

    def __init__(self, data_dir: Optional[Path] = None):
        if data_dir is None:
            plugin_dir = Path(__file__).resolve().parent.parent
            if plugin_dir.parent.name == "plugins":
                base_data_dir = plugin_dir.parent.parent / "plugin_data" / "astrbot_plugin_schedule"
            else:
                base_data_dir = Path("data") / "plugin_data" / "astrbot_plugin_schedule"
            self.data_dir = base_data_dir
        else:
            self.data_dir = Path(data_dir)

        self._lock = threading.RLock()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.profiles_dir = self.data_dir / "profiles"
        self.profiles_dir.mkdir(parents=True, exist_ok=True)

        self.profiles_file = self.data_dir / "profiles.json"
        self.config_file = self.data_dir / "config.json"
        self.courses_file = self.data_dir / "courses.json"
        self.events_file = self.data_dir / "events.json"

        self._ensure_files_initialized()

    def _ensure_files_initialized(self) -> None:
        with self._lock:
            if not self.config_file.exists():
                default_cfg = SemesterConfig()
                self._atomic_write_json(self.config_file, default_cfg.to_dict())
            if not self.courses_file.exists():
                self._atomic_write_json(self.courses_file, [])
            if not self.events_file.exists():
                self._atomic_write_json(self.events_file, [])
            if not self.profiles_file.exists():
                cfg = self._read_json(self.config_file, {})
                default_name = cfg.get("semester_name", "默认课表") if isinstance(cfg, dict) else "默认课表"
                init_meta = {
                    "active_profile_id": "default",
                    "profiles": [
                        {
                            "id": "default",
                            "name": default_name or "默认课表",
                        }
                    ],
                }
                self._atomic_write_json(self.profiles_file, init_meta)

    def _read_json(self, file_path: Path, default_value: Any) -> Any:
        with self._lock:
            if not file_path.exists():
                return default_value
            try:
                content = file_path.read_text(encoding="utf-8").strip()
                if not content:
                    return default_value
                return json.loads(content)
            except Exception:
                return default_value

    def _atomic_write_json(self, file_path: Path, data: Any) -> None:
        with self._lock:
            file_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = file_path.with_suffix(".tmp")
            temp_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(temp_path, file_path)

    # ------------------ 多课表 Profile 管理 ------------------

    def _load_profiles_meta(self) -> Dict[str, Any]:
        with self._lock:
            raw = self._read_json(self.profiles_file, {})
            if not isinstance(raw, dict) or not isinstance(raw.get("profiles"), list) or not raw["profiles"]:
                cfg = self._read_json(self.config_file, {})
                default_name = cfg.get("semester_name", "默认课表") if isinstance(cfg, dict) else "默认课表"
                raw = {
                    "active_profile_id": "default",
                    "profiles": [{"id": "default", "name": default_name or "默认课表"}],
                }
                self._atomic_write_json(self.profiles_file, raw)
            return raw

    def get_active_profile_id(self) -> str:
        meta = self._load_profiles_meta()
        active_id = str(meta.get("active_profile_id", "default")).strip() or "default"
        valid_ids = {p.get("id") for p in meta.get("profiles", []) if isinstance(p, dict)}
        if active_id not in valid_ids:
            active_id = "default" if "default" in valid_ids else next(iter(valid_ids), "default")
        return active_id

    def list_profiles(self) -> List[Dict[str, Any]]:
        with self._lock:
            meta = self._load_profiles_meta()
            active_id = self.get_active_profile_id()
            result = []
            for p in meta.get("profiles", []):
                if not isinstance(p, dict) or not p.get("id"):
                    continue
                pid = str(p["id"])
                p_cfg = self.load_config(profile_id=pid)
                p_courses = self.load_courses(profile_id=pid)
                result.append(
                    {
                        "id": pid,
                        "name": str(p.get("name") or p_cfg.semester_name or pid),
                        "semester_name": p_cfg.semester_name,
                        "course_count": len(p_courses),
                        "is_active": pid == active_id,
                    }
                )
            return result

    def resolve_profile_id(self, identifier: Optional[str] = None) -> str:
        """根据课表 ID 或课表名称解析对应的 profile_id；若未传则返回当前激活课表 ID。"""
        if not identifier or not str(identifier).strip():
            return self.get_active_profile_id()
        target = str(identifier).strip()
        meta = self._load_profiles_meta()
        profiles = meta.get("profiles", [])
        # 1. 精确匹配 id 或 name
        for p in profiles:
            if p.get("id") == target or str(p.get("name", "")).strip().lower() == target.lower():
                return str(p["id"])
        # 2. 模糊匹配 name
        for p in profiles:
            p_name = str(p.get("name", "")).strip().lower()
            if target.lower() in p_name or p_name in target.lower():
                return str(p["id"])
        raise ValueError(f"未找到名为 '{identifier}' 的课表")

    def _get_profile_files(self, profile_id: Optional[str] = None) -> tuple[Path, Path, Path]:
        pid = profile_id or self.get_active_profile_id()
        if pid == "default":
            return self.config_file, self.courses_file, self.events_file
        p_dir = self.profiles_dir / pid
        p_dir.mkdir(parents=True, exist_ok=True)
        cfg_f = p_dir / "config.json"
        crs_f = p_dir / "courses.json"
        evt_f = p_dir / "events.json"
        if not cfg_f.exists():
            self._atomic_write_json(cfg_f, SemesterConfig().to_dict())
        if not crs_f.exists():
            self._atomic_write_json(crs_f, [])
        if not evt_f.exists():
            self._atomic_write_json(evt_f, [])
        return cfg_f, crs_f, evt_f

    def create_profile(
        self,
        name: str,
        semester_name: str = "",
        start_date: str = "2026-09-07",
        total_weeks: int = 20,
        copy_from_current: bool = False,
        switch_to_new: bool = True,
    ) -> Dict[str, Any]:
        """新建一份课表（可选复制当前课表的作息与课程），并可选立即切换到新课表。"""
        with self._lock:
            clean_name = (name or "").strip() or "新课表"
            meta = self._load_profiles_meta()
            new_id = f"schedule_{uuid.uuid4().hex[:8]}"

            current_cfg = self.load_config()
            new_cfg = SemesterConfig(
                semester_name=(semester_name or clean_name).strip(),
                start_date=start_date or current_cfg.start_date,
                total_weeks=total_weeks or current_cfg.total_weeks,
                enable_reminder=current_cfg.enable_reminder,
                remind_advance_minutes=current_cfg.remind_advance_minutes,
                period_times=dict(current_cfg.period_times),
            )

            cfg_f, crs_f, evt_f = self._get_profile_files(new_id)
            self._atomic_write_json(cfg_f, new_cfg.to_dict())

            if copy_from_current:
                cur_courses = [c.to_dict() for c in self.load_courses()]
                self._atomic_write_json(crs_f, cur_courses)
            else:
                self._atomic_write_json(crs_f, [])
            self._atomic_write_json(evt_f, [])

            meta["profiles"].append({"id": new_id, "name": clean_name})
            if switch_to_new:
                meta["active_profile_id"] = new_id
            self._atomic_write_json(self.profiles_file, meta)
            return {"id": new_id, "name": clean_name}

    def switch_profile(self, identifier: str) -> Dict[str, Any]:
        """切换当前激活的课表（支持按 ID 或名称切换）。"""
        with self._lock:
            target_id = self.resolve_profile_id(identifier)
            meta = self._load_profiles_meta()
            meta["active_profile_id"] = target_id
            self._atomic_write_json(self.profiles_file, meta)
            # 确保目标课表文件已初始化
            self._get_profile_files(target_id)
            target_info = next((p for p in meta["profiles"] if p.get("id") == target_id), {"id": target_id, "name": target_id})
            return target_info

    def rename_profile(self, profile_id: str, new_name: str) -> Dict[str, Any]:
        """重命名指定课表。"""
        with self._lock:
            clean_name = (new_name or "").strip()
            if not clean_name:
                raise ValueError("课表名称不能为空")
            target_id = self.resolve_profile_id(profile_id)
            meta = self._load_profiles_meta()
            for p in meta["profiles"]:
                if p.get("id") == target_id:
                    p["name"] = clean_name
                    break
            self._atomic_write_json(self.profiles_file, meta)
            return {"id": target_id, "name": clean_name}

    def delete_profile(self, profile_id: str) -> bool:
        """删除指定课表（若只剩最后一份课表则不允许删除，自动清空或保留）。"""
        with self._lock:
            meta = self._load_profiles_meta()
            profiles = meta.get("profiles", [])
            if len(profiles) <= 1:
                raise ValueError("至少需要保留一份课表，无法删除最后一份课表（如需清空课程请点击「清空课表」）")

            target_id = self.resolve_profile_id(profile_id)
            new_profiles = [p for p in profiles if p.get("id") != target_id]
            if len(new_profiles) == len(profiles):
                return False

            meta["profiles"] = new_profiles
            if meta.get("active_profile_id") == target_id:
                meta["active_profile_id"] = str(new_profiles[0]["id"])
            self._atomic_write_json(self.profiles_file, meta)

            # 清理子目录文件（如果不是 default）
            if target_id != "default":
                p_dir = self.profiles_dir / target_id
                if p_dir.exists() and p_dir.is_dir():
                    for f in p_dir.glob("*"):
                        try:
                            f.unlink()
                        except Exception:
                            pass
                    try:
                        p_dir.rmdir()
                    except Exception:
                        pass
            return True

    # ------------------ Config (config.json) ------------------

    def load_config(self, profile_id: Optional[str] = None) -> SemesterConfig:
        cfg_f, _, _ = self._get_profile_files(profile_id)
        raw = self._read_json(cfg_f, {})
        if not isinstance(raw, dict):
            raw = {}
        return SemesterConfig.from_dict(raw)

    def save_config(self, config: SemesterConfig, profile_id: Optional[str] = None) -> SemesterConfig:
        cfg_f, _, _ = self._get_profile_files(profile_id)
        self._atomic_write_json(cfg_f, config.to_dict())
        return config

    def update_config(self, payload: Dict[str, Any], profile_id: Optional[str] = None) -> SemesterConfig:
        with self._lock:
            pid = profile_id or self.get_active_profile_id()
            current = self.load_config(profile_id=pid).to_dict()
            current.update(payload)
            updated = SemesterConfig.from_dict(current)
            self.save_config(updated, profile_id=pid)
            return updated

    # ------------------ Courses (courses.json) ------------------

    def load_courses(self, profile_id: Optional[str] = None) -> List[Course]:
        _, crs_f, _ = self._get_profile_files(profile_id)
        raw = self._read_json(crs_f, [])
        if not isinstance(raw, list):
            return []
        courses: List[Course] = []
        for item in raw:
            if isinstance(item, dict):
                try:
                    courses.append(Course.from_dict(item))
                except Exception:
                    continue
        return courses

    def save_courses(self, courses: List[Course], profile_id: Optional[str] = None) -> List[Course]:
        _, crs_f, _ = self._get_profile_files(profile_id)
        data = [c.to_dict() for c in courses]
        self._atomic_write_json(crs_f, data)
        return courses

    def upsert_course(self, course_data: Dict[str, Any], profile_id: Optional[str] = None) -> Course:
        with self._lock:
            pid = profile_id or self.get_active_profile_id()
            courses = self.load_courses(profile_id=pid)
            course = Course.from_dict(course_data)
            replaced = False
            for idx, existing in enumerate(courses):
                if existing.id == course.id:
                    courses[idx] = course
                    replaced = True
                    break
            if not replaced:
                courses.append(course)
            self.save_courses(courses, profile_id=pid)
            return course

    def delete_course(self, course_id: str, profile_id: Optional[str] = None) -> bool:
        with self._lock:
            pid = profile_id or self.get_active_profile_id()
            courses = self.load_courses(profile_id=pid)
            new_courses = [c for c in courses if c.id != course_id]
            if len(new_courses) == len(courses):
                return False
            self.save_courses(new_courses, profile_id=pid)
            return True

    def delete_courses_by_ids(self, course_ids: List[str], profile_id: Optional[str] = None) -> int:
        """批量按 ID 列表删除固定课程（如一键删除同一门课的所有时段）。"""
        with self._lock:
            pid = profile_id or self.get_active_profile_id()
            id_set = {str(cid).strip() for cid in course_ids if str(cid).strip()}
            if not id_set:
                return 0
            courses = self.load_courses(profile_id=pid)
            new_courses = [c for c in courses if c.id not in id_set]
            removed = len(courses) - len(new_courses)
            if removed > 0:
                self.save_courses(new_courses, profile_id=pid)
            return removed

    def clear_all_courses(self, profile_id: Optional[str] = None) -> int:
        """清空当前（或指定）课表的所有固定课程，返回被删除的课程数量。"""
        with self._lock:
            pid = profile_id or self.get_active_profile_id()
            courses = self.load_courses(profile_id=pid)
            count = len(courses)
            self.save_courses([], profile_id=pid)
            return count

    def import_courses(
        self, imported_courses: List[Course], mode: str = "overwrite", profile_id: Optional[str] = None
    ) -> List[Course]:
        with self._lock:
            pid = profile_id or self.get_active_profile_id()
            if mode == "overwrite":
                self.save_courses(imported_courses, profile_id=pid)
                return imported_courses

            existing_courses = self.load_courses(profile_id=pid)
            for inc in imported_courses:
                matched = None
                for ex in existing_courses:
                    if (
                        ex.name == inc.name
                        and ex.day_of_week == inc.day_of_week
                        and ex.start_period == inc.start_period
                        and ex.end_period == inc.end_period
                        and ex.location == inc.location
                    ):
                        matched = ex
                        break
                if matched:
                    merged_weeks = sorted(list(set(matched.weeks) | set(inc.weeks)))
                    matched.weeks = merged_weeks
                    if inc.teacher and not matched.teacher:
                        matched.teacher = inc.teacher
                else:
                    existing_courses.append(inc)
            self.save_courses(existing_courses, profile_id=pid)
            return existing_courses

    # ------------------ Events (events.json) ------------------

    def load_events(self, profile_id: Optional[str] = None) -> List[ScheduleEvent]:
        _, _, evt_f = self._get_profile_files(profile_id)
        raw = self._read_json(evt_f, [])
        if not isinstance(raw, list):
            return []
        events: List[ScheduleEvent] = []
        for item in raw:
            if isinstance(item, dict):
                try:
                    events.append(ScheduleEvent.from_dict(item))
                except Exception:
                    continue
        events.sort(key=lambda e: (e.date, e.start_time or "00:00", e.id))
        return events

    def save_events(self, events: List[ScheduleEvent], profile_id: Optional[str] = None) -> List[ScheduleEvent]:
        _, _, evt_f = self._get_profile_files(profile_id)
        events.sort(key=lambda e: (e.date, e.start_time or "00:00", e.id))
        data = [e.to_dict() for e in events]
        self._atomic_write_json(evt_f, data)
        return events

    def upsert_event(self, event_data: Dict[str, Any], profile_id: Optional[str] = None) -> ScheduleEvent:
        with self._lock:
            pid = profile_id or self.get_active_profile_id()
            events = self.load_events(profile_id=pid)
            evt = ScheduleEvent.from_dict(event_data)
            replaced = False
            for idx, existing in enumerate(events):
                if existing.id == evt.id:
                    events[idx] = evt
                    replaced = True
                    break
            if not replaced:
                events.append(evt)
            self.save_events(events, profile_id=pid)
            return evt

    def delete_event_by_id(self, event_id: str, profile_id: Optional[str] = None) -> bool:
        with self._lock:
            pid = profile_id or self.get_active_profile_id()
            events = self.load_events(profile_id=pid)
            new_events = [e for e in events if e.id != event_id]
            if len(new_events) == len(events):
                return False
            self.save_events(new_events, profile_id=pid)
            return True
