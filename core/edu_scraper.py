import base64
import json
import re
import time
import uuid
from typing import Dict, List, Optional, Tuple, Any
from urllib.parse import urljoin, urlparse

try:
    import httpx
except ImportError:
    httpx = None

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None

try:
    from Crypto.PublicKey import RSA
    from Crypto.Cipher import PKCS1_v1_5
except ImportError:
    RSA = None
    PKCS1_v1_5 = None

from .models import Course, COURSE_PALETTE


def parse_weeks_expression(expr: str, default_max: int = 20) -> List[int]:
    """
    解析教务系统中常见的周次字符串，例如：
    - "1-16周" -> [1..16]
    - "1-15周(单)" / "1-15单周" -> [1,3,5..15]
    - "2-16周(双)" -> [2,4,6..16]
    - "1-8周,10-16周(双),18周" -> 组合周次
    """
    if not expr:
        return list(range(1, 17))

    text = str(expr).strip()
    weeks_set = set()

    # 按逗号、顿号、分号、空格切分多个片段
    segments = re.split(r"[,，;；、\s]+", text)
    for seg in segments:
        if not seg:
            continue
        is_odd = "单" in seg
        is_even = "双" in seg

        # 匹配区间 a-b
        range_matches = re.findall(r"(\d+)\s*[-~至到]\s*(\d+)", seg)
        if range_matches:
            for s_str, e_str in range_matches:
                s_num, e_num = int(s_str), int(e_str)
                if s_num > e_num:
                    s_num, e_num = e_num, s_num
                for w in range(max(1, s_num), min(40, e_num) + 1):
                    if is_odd and w % 2 == 0:
                        continue
                    if is_even and w % 2 == 1:
                        continue
                    weeks_set.add(w)
        else:
            # 匹配单个数字
            single_matches = re.findall(r"(\d+)", seg)
            for m in single_matches:
                w = int(m)
                if 1 <= w <= 40:
                    weeks_set.add(w)

    if not weeks_set:
        return list(range(1, min(default_max, 16) + 1))
    return sorted(list(weeks_set))


def parse_periods_expression(expr: str) -> Tuple[int, int]:
    """
    解析节次表达式：
    - "1-2节" / "(1-2节)" / "01-02节" -> (1, 2)
    - "010203" / "0304节" -> (1, 3) / (3, 4)
    - "第3,4节" -> (3, 4)
    """
    if not expr:
        return 1, 2
    text = str(expr).strip()

    # 1. 匹配 a-b
    m = re.search(r"(\d{1,2})\s*[-~至到]\s*(\d{1,2})", text)
    if m:
        sp, ep = int(m.group(1)), int(m.group(2))
        return max(1, sp), max(sp, ep)

    # 2. 匹配强智常见的 "0102" 或 "010203" 格式
    m_qz = re.search(r"((?:\d{2}){2,4})节?", text)
    if m_qz:
        digits = m_qz.group(1)
        chunks = [int(digits[i : i + 2]) for i in range(0, len(digits), 2)]
        chunks = [c for c in chunks if 1 <= c <= 20]
        if chunks:
            return min(chunks), max(chunks)

    # 3. 匹配逗号分隔或连续数字
    nums = [int(x) for x in re.findall(r"(\d{1,2})", text) if 1 <= int(x) <= 20]
    if nums:
        return min(nums), max(nums)

    return 1, 2


def rsa_encrypt_zfnew(password: str, modulus_b64: str, exponent_b64: str) -> str:
    """新版正方教务系统 RSA 密码加密（兼容有/无 pycryptodome 环境，内置纯 Python 大数幂模实现）。"""
    m_bytes = base64.b64decode(modulus_b64)
    e_bytes = base64.b64decode(exponent_b64)
    n_int = int.from_bytes(m_bytes, byteorder="big")
    e_int = int.from_bytes(e_bytes, byteorder="big")

    if RSA is not None and PKCS1_v1_5 is not None:
        pub_key = RSA.construct((n_int, e_int))
        cipher = PKCS1_v1_5.new(pub_key)
        encrypted = cipher.encrypt(password.encode("utf-8"))
        return base64.b64encode(encrypted).decode("utf-8")

    # 纯 Python PKCS#1 v1.5 填充与 RSA 加密回退
    import os
    k = (n_int.bit_length() + 7) // 8
    msg = password.encode("utf-8")
    ps_len = k - len(msg) - 3
    if ps_len < 8:
        raise ValueError("RSA 密钥长度不足以加密该密码")
    ps = bytearray()
    while len(ps) < ps_len:
        b = os.urandom(1)
        if b != b"\x00":
            ps.extend(b)
    em = b"\x00\x02" + bytes(ps) + b"\x00" + msg
    m_int = int.from_bytes(em, byteorder="big")
    c_int = pow(m_int, e_int, n_int)
    c_bytes = c_int.to_bytes(k, byteorder="big")
    return base64.b64encode(c_bytes).decode("utf-8")


class EduHtmlTableParser:
    """
    通用教务处课表解析器：
    支持解析：
    1. 新版正方教务 JSON (kbList)
    2. 强智教务系统 HTML 表格 (id="kbtable" / class="kbcontent")
    3. 旧版正方教务 HTML 表格 (id="Table1")
    4. 金智/树维/URP 及任意包含“周一~周日”的 HTML 课表表格
    5. WakeUp 课程表导出的多段 JSON 文本格式
    """

    @staticmethod
    def parse_zfnew_json(data: Dict[str, Any]) -> List[Course]:
        """解析新版正方教务 xskbcx_cxXsKb.html 返回的 JSON 数据 (kbList)。"""
        kb_list = data.get("kbList")
        if not isinstance(kb_list, list):
            return []

        courses: List[Course] = []
        color_map: Dict[str, str] = {}

        for item in kb_list:
            if not isinstance(item, dict):
                continue
            name = str(item.get("kcmc", "")).strip()
            if not name:
                continue

            teacher = str(item.get("xm", "")).strip()
            location = str(item.get("cdmc", "")).strip()
            day_of_week = int(item.get("xqj", 1) or 1)

            # 节次字段通常是 jcor ("1-2") 或 jcs ("1-2")
            jc_str = str(item.get("jcor") or item.get("jcs") or item.get("jc") or "1-2")
            start_p, end_p = parse_periods_expression(jc_str)

            # 周次字段通常是 zcd ("1-16周")
            zcd_str = str(item.get("zcd") or "1-16周")
            weeks = parse_weeks_expression(zcd_str)

            if name not in color_map:
                color_map[name] = COURSE_PALETTE[len(color_map) % len(COURSE_PALETTE)]

            courses.append(
                Course(
                    id=f"course_{uuid.uuid4().hex[:8]}",
                    name=name,
                    teacher=teacher,
                    location=location,
                    day_of_week=day_of_week,
                    start_period=start_p,
                    end_period=end_p,
                    weeks=weeks,
                    color=color_map[name],
                )
            )

        return courses

    @staticmethod
    def parse_wakeup_text(content: str) -> List[Course]:
        """
        兼容 WakeUp 课程表导出的 5 行/多段 JSON 文本格式：
        第1行：课表配置 JSON
        第2行：时间段 JSON
        第3行：课程基本信息数组 [{"id": 0, "courseName": "高等数学", "color": "#xxx"}]
        第4行：课程安排数组 [{"id": 0, "day": 1, "startNode": 1, "step": 2, "startWeek": 1, "endWeek": 16, "type": 0, "room": "302", "teacher": "张老师"}]
        """
        lines = [line.strip() for line in content.strip().splitlines() if line.strip()]
        if len(lines) < 4:
            return []

        try:
            meta_list = json.loads(lines[-2])
            detail_list = json.loads(lines[-1])
            if not isinstance(meta_list, list) or not isinstance(detail_list, list):
                return []
        except Exception:
            return []

        name_map: Dict[Any, Tuple[str, str]] = {}
        for idx, m in enumerate(meta_list):
            if isinstance(m, dict) and "courseName" in m:
                cid = m.get("id", idx)
                c_name = str(m.get("courseName", "")).strip()
                c_color = str(m.get("color", "")).strip()
                if not c_color.startswith("#") or len(c_color) > 7:
                    c_color = COURSE_PALETTE[idx % len(COURSE_PALETTE)]
                name_map[cid] = (c_name, c_color)

        if not name_map:
            return []

        courses: List[Course] = []
        for item in detail_list:
            if not isinstance(item, dict):
                continue
            cid = item.get("id")
            if cid not in name_map:
                continue
            c_name, c_color = name_map[cid]
            day = int(item.get("day", 1) or 1)
            start_p = int(item.get("startNode", 1) or 1)
            step = int(item.get("step", 2) or 1)
            end_p = start_p + max(1, step) - 1
            start_w = int(item.get("startWeek", 1) or 1)
            end_w = int(item.get("endWeek", 16) or 16)
            w_type = int(item.get("type", 0) or 0)  # 0:全周, 1:单周, 2:双周

            weeks = []
            for w in range(start_w, end_w + 1):
                if w_type == 1 and w % 2 == 0:
                    continue
                if w_type == 2 and w % 2 == 1:
                    continue
                weeks.append(w)

            courses.append(
                Course(
                    id=f"course_{uuid.uuid4().hex[:8]}",
                    name=c_name,
                    teacher=str(item.get("teacher", "")).strip(),
                    location=str(item.get("room", "")).strip(),
                    day_of_week=day,
                    start_period=start_p,
                    end_period=end_p,
                    weeks=weeks,
                    color=c_color,
                )
            )
        return courses

    @classmethod
    def parse_any_content(cls, content: str) -> List[Course]:
        """智能识别并解析教务处 JSON / WakeUp多段文本 / 各类教务系统 HTML 课表网页。"""
        text = (content or "").strip()
        if not text:
            return []

        # 1. 尝试新版正方 JSON
        if text.startswith("{") and "kbList" in text:
            try:
                data = json.loads(text)
                res = cls.parse_zfnew_json(data)
                if res:
                    return res
            except Exception:
                pass

        # 2. 尝试 WakeUp 多行 JSON 格式
        if "courseName" in text and "startNode" in text:
            res = cls.parse_wakeup_text(text)
            if res:
                return res

        # 3. 解析 HTML 课表网页
        return cls.parse_html_table(text)

    @classmethod
    def parse_html_table(cls, html: str) -> List[Course]:
        """解析正方、强智、金智、树维、URP 等教务系统的 HTML 课表。"""
        if BeautifulSoup is None:
            raise RuntimeError("未安装 beautifulsoup4 依赖，请执行 pip install beautifulsoup4>=4.12.0")

        soup = BeautifulSoup(html, "html.parser")
        courses: List[Course] = []
        color_map: Dict[str, str] = {}

        def _get_color(c_name: str) -> str:
            if c_name not in color_map:
                color_map[c_name] = COURSE_PALETTE[len(color_map) % len(COURSE_PALETTE)]
            return color_map[c_name]

        # 定位主课表 Table（优先找 kbtable / Table1 / 含“星期一”或“周一”的表格）
        target_table = (
            soup.find("table", id=re.compile(r"(kbtable|Table1|timetable|courseTable)", re.I))
            or soup.find("table", class_=re.compile(r"(kb_table|timetable|course)", re.I))
        )
        if not target_table:
            for tbl in soup.find_all("table"):
                tbl_text = tbl.get_text()
                if ("星期一" in tbl_text or "周一" in tbl_text) and ("星期二" in tbl_text or "周二" in tbl_text):
                    target_table = tbl
                    break

        if not target_table:
            return []

        rows = target_table.find_all("tr")
        for row_idx, tr in enumerate(rows):
            cells = tr.find_all(["td", "th"])
            if len(cells) < 2:
                continue

            # 尝试从行首文字推断当前行的默认节次
            row_header_text = cells[0].get_text(" ", strip=True)
            if len(cells) > 1 and ("上午" in row_header_text or "下午" in row_header_text or "晚上" in row_header_text):
                row_header_text += " " + cells[1].get_text(" ", strip=True)
            default_sp, default_ep = parse_periods_expression(row_header_text)
            if default_sp == 1 and default_ep == 2 and row_idx > 1:
                # 若行首无明确节次，按行号估算大节（第1行表头，第2行1-2节，第3行3-4节...）
                est_start = max(1, (row_idx - 1) * 2 - 1)
                if est_start <= 12:
                    default_sp, default_ep = est_start, est_start + 1

            # 找出当前行中的数据单元格（排除表头列）
            day_col_idx = 0
            for cell in cells:
                if cell.name == "th":
                    continue
                cell_text_check = cell.get_text(strip=True)
                if re.match(r"^(上午|下午|晚上|第.+节|\d+[-~]\d+节?)$", cell_text_check):
                    continue

                day_col_idx += 1
                if day_col_idx > 7:
                    break

                # 检查强智教务系统的 div.kbcontent
                kb_div = cell.find("div", class_="kbcontent")
                target_node = kb_div if kb_div else cell

                # 将 <br> 替换为换行符以便逐行提取
                for br in target_node.find_all("br"):
                    br.replace_with("\n")
                raw_cell_text = target_node.get_text().strip()
                if not raw_cell_text or len(raw_cell_text) < 2:
                    continue

                # 同一格可能有多门课，常见分隔符为 "-----" 或空行
                blocks = re.split(r"-{3,}|\n\s*\n", raw_cell_text)
                for block in blocks:
                    lines = [
                        line.strip()
                        for line in block.splitlines()
                        if line.strip() and not re.match(r"^[-\s]+$", line.strip())
                    ]
                    if not lines:
                        continue

                    course_name = lines[0]
                    # 过滤无效表头
                    if course_name in ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日", "周一", "周二", "时间", "节次"):
                        continue

                    teacher = ""
                    location = ""
                    weeks = list(range(1, 17))
                    sp, ep = default_sp, default_ep

                    # 如果单元格有 rowspan，则利用 rowspan 修正节次跨度
                    rowspan = int(cell.get("rowspan", 1) or 1)
                    if rowspan > 1:
                        ep = sp + rowspan - 1

                    # 解析其余各行（教师、周次、节次、教室）
                    for info_line in lines[1:]:
                        # 匹配旧版正方格式： "周一第1,2节{第1-16周}" 或 "{第2-16周|2节/双周}"
                        if "周" in info_line or "节" in info_line:
                            if "周" in info_line:
                                weeks = parse_weeks_expression(info_line)
                            if "节" in info_line and re.search(r"\d", info_line):
                                p_s, p_e = parse_periods_expression(info_line)
                                if 1 <= p_s <= p_e <= 20:
                                    sp, ep = p_s, p_e
                            continue

                        if not teacher and len(info_line) <= 12 and not re.search(r"(\d{3}|楼|室|区|馆|教|堂)", info_line):
                            teacher = info_line
                        elif not location:
                            location = info_line

                    courses.append(
                        Course(
                            id=f"course_{uuid.uuid4().hex[:8]}",
                            name=course_name,
                            teacher=teacher,
                            location=location,
                            day_of_week=day_col_idx,
                            start_period=sp,
                            end_period=ep,
                            weeks=weeks,
                            color=_get_color(course_name),
                        )
                    )

        # 去重合并同一门课
        merged: List[Course] = []
        for c in courses:
            found = None
            for ex in merged:
                if (
                    ex.name == c.name
                    and ex.day_of_week == c.day_of_week
                    and ex.start_period == c.start_period
                    and ex.end_period == c.end_period
                    and ex.location == c.location
                ):
                    found = ex
                    break
            if found:
                found.weeks = sorted(list(set(found.weeks) | set(c.weeks)))
            else:
                merged.append(c)

        return merged


class EduSessionManager:
    """
    管理教务系统登录会话、图形验证码拉取、代理网页预览与课表一键抓取。
    支持：
    - zfnew: 新版正方教务系统 (/jwglxt/...)
    - qz: 强智科技教务系统 (/jsxsd/...)
    - zfold: 旧版正方教务系统 (/default2.aspx)
    - auto / generic: 通用教务网页与自定义 Cookie/URL 会话抓取
    """

    def __init__(self):
        self._sessions: Dict[str, Dict[str, Any]] = {}

    def _cleanup_expired(self) -> None:
        now = time.time()
        expired = [
            sid for sid, info in self._sessions.items() if now - info.get("created_at", 0) > 1800
        ]
        for sid in expired:
            client = self._sessions[sid].get("client")
            if client:
                try:
                    # 异步关闭在后台不阻塞
                    pass
                except Exception:
                    pass
            self._sessions.pop(sid, None)

    @staticmethod
    def detect_system_type(base_url: str, html_text: str = "") -> str:
        url_lower = base_url.lower()
        if "xtgl/login_slogin" in url_lower or "jwglxt" in url_lower or "modulus" in html_text:
            return "zfnew"
        if "jsxsd" in url_lower or "xk/login" in url_lower:
            return "qz"
        if "default2.aspx" in url_lower or "__VIEWSTATE" in html_text:
            return "zfold"
        return "generic"

    @staticmethod
    def normalize_base_url(raw_url: str) -> str:
        url = (raw_url or "").strip()
        if not url.startswith(("http://", "https://")):
            url = "http://" + url
        return url

    async def init_login_session(
        self, raw_url: str, sys_type: str = "auto"
    ) -> Dict[str, Any]:
        """
        第一步：访问教务处登录页，建立带 Cookie 的会话，自动探测教务系统类型并拉取图形验证码（如有）。
        """
        if httpx is None:
            raise RuntimeError("未安装 httpx 库，请先安装 httpx>=0.24.0")

        self._cleanup_expired()
        login_url = self.normalize_base_url(raw_url)
        client = httpx.AsyncClient(
            follow_redirects=True,
            verify=False,
            timeout=15.0,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                )
            },
        )

        resp = await client.get(login_url)
        final_url = str(resp.url)
        html_text = resp.text

        detected_type = sys_type if sys_type and sys_type != "auto" else self.detect_system_type(final_url, html_text)

        # 提取 csrftoken (新版正方) 或 __VIEWSTATE (旧版正方) 或隐藏表单字段
        csrf_token = ""
        viewstate = ""
        captcha_url = ""

        if BeautifulSoup is not None:
            soup = BeautifulSoup(html_text, "html.parser")
            csrf_input = soup.find("input", {"id": "csrftoken"}) or soup.find("input", {"name": "csrftoken"})
            if csrf_input:
                csrf_token = csrf_input.get("value", "")

            vs_input = soup.find("input", {"name": "__VIEWSTATE"})
            if vs_input:
                viewstate = vs_input.get("value", "")

            # 查找页面上的验证码 img 标签
            for img in soup.find_all("img"):
                src = img.get("src", "")
                img_id = (img.get("id", "") + " " + img.get("class", [""])[0] if isinstance(img.get("class"), list) and img.get("class") else img.get("id", "")).lower()
                if any(k in src.lower() or k in img_id for k in ("kaptcha", "verifycode", "checkcode", "captcha", "yzm", "validate", "rand")):
                    captcha_url = urljoin(final_url, src)
                    break

        # 根据不同教务系统补全默认验证码路径
        parsed = urlparse(final_url)
        origin = f"{parsed.scheme}://{parsed.netloc}"

        if not captcha_url:
            if detected_type == "zfnew" and ("yzm" in html_text or "kaptcha" in html_text):
                prefix = "/jwglxt" if "/jwglxt/" in final_url else ""
                captcha_url = f"{origin}{prefix}/kaptcha"
            elif detected_type == "qz":
                prefix = "/jsxsd" if "/jsxsd/" in final_url else ""
                captcha_url = f"{origin}{prefix}/verifycode.servlet"
            elif detected_type == "zfold":
                captcha_url = urljoin(final_url, "CheckCode.aspx")

        captcha_b64 = ""
        needs_captcha = False
        if captcha_url:
            try:
                cap_resp = await client.get(captcha_url)
                content_type = cap_resp.headers.get("content-type", "")
                if cap_resp.status_code == 200 and ("image" in content_type or len(cap_resp.content) > 100):
                    b64_data = base64.b64encode(cap_resp.content).decode("utf-8")
                    mime = content_type.split(";")[0].strip() if "image" in content_type else "image/jpeg"
                    captcha_b64 = f"data:{mime};base64,{b64_data}"
                    needs_captcha = True
            except Exception:
                pass

        session_id = f"edu_{uuid.uuid4().hex[:12]}"
        self._sessions[session_id] = {
            "client": client,
            "login_url": final_url,
            "origin": origin,
            "sys_type": detected_type,
            "csrf_token": csrf_token,
            "viewstate": viewstate,
            "captcha_url": captcha_url,
            "created_at": time.time(),
        }

        return {
            "session_id": session_id,
            "sys_type": detected_type,
            "login_url": final_url,
            "needs_captcha": needs_captcha,
            "captcha_base64": captcha_b64,
        }

    async def refresh_captcha(self, session_id: str) -> str:
        """刷新指定会话的图形验证码图片。"""
        session = self._sessions.get(session_id)
        if not session:
            raise ValueError("登录会话已过期，请重新点击连接教务处")
        client: httpx.AsyncClient = session["client"]
        captcha_url = session.get("captcha_url")
        if not captcha_url:
            return ""
        sep = "&" if "?" in captcha_url else "?"
        resp = await client.get(f"{captcha_url}{sep}t={int(time.time() * 1000)}")
        b64_data = base64.b64encode(resp.content).decode("utf-8")
        return f"data:image/jpeg;base64,{b64_data}"

    async def login_and_fetch_courses(
        self,
        session_id: str,
        username: str,
        password: str,
        captcha: str = "",
        academic_year: str = "2026",
        semester_term: str = "1",
        schedule_url: str = "",
    ) -> List[Course]:
        """
        第二步：提交账号、密码与验证码完成登录，并自动请求对应学年学期的课表数据返回解析后的 Course 列表。
        """
        session = self._sessions.get(session_id)
        if not session:
            raise ValueError("登录会话不存在或已超时，请重新初始化连接")

        client: httpx.AsyncClient = session["client"]
        sys_type = session["sys_type"]
        login_url = session["login_url"]
        origin = session["origin"]

        if sys_type == "zfnew":
            return await self._login_and_fetch_zfnew(
                client, session, username, password, captcha, academic_year, semester_term, schedule_url
            )
        elif sys_type == "qz":
            return await self._login_and_fetch_qz(
                client, session, username, password, captcha, academic_year, semester_term, schedule_url
            )
        elif sys_type == "zfold":
            return await self._login_and_fetch_zfold(
                client, session, username, password, captcha, academic_year, semester_term, schedule_url
            )
        else:
            return await self._login_and_fetch_generic(
                client, session, username, password, captcha, schedule_url
            )

    async def _login_and_fetch_zfnew(
        self,
        client: "httpx.AsyncClient",
        session: Dict[str, Any],
        username: str,
        password: str,
        captcha: str,
        academic_year: str,
        semester_term: str,
        schedule_url: str,
    ) -> List[Course]:
        """新版正方教务系统登录与课表拉取"""
        login_url = session["login_url"]
        origin = session["origin"]
        prefix = "/jwglxt" if "/jwglxt/" in login_url else ""
        base_prefix = f"{origin}{prefix}"

        # 1. 获取 RSA 公钥
        pub_url = f"{base_prefix}/xtgl/login_getPublicKey.html?time={int(time.time() * 1000)}"
        pub_resp = await client.get(pub_url)
        pub_data = pub_resp.json()
        modulus = pub_data.get("modulus")
        exponent = pub_data.get("exponent")
        if not modulus or not exponent:
            raise ValueError("无法从新版正方教务系统获取 RSA 公钥，请检查教务处地址是否正确")

        enc_pwd = rsa_encrypt_zfnew(password, modulus, exponent)

        # 2. 提交登录表单
        post_url = f"{base_prefix}/xtgl/login_slogin.html?time={int(time.time() * 1000)}"
        form_data = {
            "csrftoken": session.get("csrf_token", ""),
            "language": "zh_CN",
            "yhm": username,
            "mm": [enc_pwd, enc_pwd],
        }
        if captcha:
            form_data["yzm"] = captcha

        login_resp = await client.post(post_url, data=form_data)
        if "用户名或密码不正确" in login_resp.text or "验证码输入错误" in login_resp.text:
            err_msg = "验证码输入错误" if "验证码" in login_resp.text else "学号或密码不正确"
            raise ValueError(f"教务处登录失败：{err_msg}")

        # 3. 请求个人课表 JSON 接口
        # 新版正方中：xnm 为学年起始年（如 2026），xqm：第1学期为 "3"，第2学期为 "12"，第3学期为 "16"
        xqm_map = {"1": "3", "2": "12", "3": "16", "3": "3", "12": "12"}
        xqm = xqm_map.get(str(semester_term), "3")
        kb_url = schedule_url or f"{base_prefix}/kbcx/xskbcx_cxXsKb.html?gnmkdm=N2151"

        kb_resp = await client.post(kb_url, data={"xnm": str(academic_year), "xqm": xqm})
        courses = EduHtmlTableParser.parse_any_content(kb_resp.text)
        if not courses:
            raise ValueError(
                "已连接教务处，但未在所选学年学期下查到课程数据（请确认学年学期是否正确，或使用下方的 Cookie/网页 HTML 方式导入）。"
            )
        return courses

    async def _login_and_fetch_qz(
        self,
        client: "httpx.AsyncClient",
        session: Dict[str, Any],
        username: str,
        password: str,
        captcha: str,
        academic_year: str,
        semester_term: str,
        schedule_url: str,
    ) -> List[Course]:
        """强智科技教务系统登录与课表拉取"""
        login_url = session["login_url"]
        origin = session["origin"]
        prefix = "/jsxsd" if "/jsxsd/" in login_url else ""
        base_prefix = f"{origin}{prefix}"

        u_b64 = base64.b64encode(username.encode("utf-8")).decode("utf-8")
        p_b64 = base64.b64encode(password.encode("utf-8")).decode("utf-8")
        encoded = f"{u_b64}%%%{p_b64}"

        post_url = f"{base_prefix}/xk/LoginToXk"
        form_data = {
            "userAccount": username,
            "userPassword": "",
            "encoded": encoded,
        }
        if captcha:
            form_data["RANDOMCODE"] = captcha

        login_resp = await client.post(post_url, data=form_data)
        if "用户名或密码错误" in login_resp.text or "验证码错误" in login_resp.text:
            raise ValueError("强智教务登录失败：账号、密码或验证码错误")

        # 强智学期格式示例: "2026-2027-1"
        yr = int(academic_year) if str(academic_year).isdigit() else 2026
        xnxq = f"{yr}-{yr + 1}-{semester_term}"
        kb_url = schedule_url or f"{base_prefix}/xskb/xskb_list.do"
        kb_resp = await client.post(kb_url, data={"xnxq01id": xnxq})

        courses = EduHtmlTableParser.parse_any_content(kb_resp.text)
        if not courses:
            raise ValueError("登录成功但未解析到强智教务课表，请检查学年学期设置。")
        return courses

    async def _login_and_fetch_zfold(
        self,
        client: "httpx.AsyncClient",
        session: Dict[str, Any],
        username: str,
        password: str,
        captcha: str,
        academic_year: str,
        semester_term: str,
        schedule_url: str,
    ) -> List[Course]:
        """旧版正方教务系统登录与课表拉取"""
        login_url = session["login_url"]
        form_data = {
            "__VIEWSTATE": session.get("viewstate", ""),
            "txtUserName": username,
            "TextBox2": password,
            "txtSecretCode": captcha,
            "RadioButtonList1": "学生",
            "Button1": "",
        }
        await client.post(login_url, data=form_data)

        kb_url = schedule_url or urljoin(login_url, f"xskbcx.aspx?xh={username}&gnmkdm=N121603")
        kb_resp = await client.get(kb_url, headers={"Referer": login_url})
        courses = EduHtmlTableParser.parse_any_content(kb_resp.text)
        if not courses:
            raise ValueError("未能从旧版正方课表页面提取到课程，请确认验证码是否正确或使用网页 HTML 导入。")
        return courses

    async def _login_and_fetch_generic(
        self,
        client: "httpx.AsyncClient",
        session: Dict[str, Any],
        username: str,
        password: str,
        captcha: str,
        schedule_url: str,
    ) -> List[Course]:
        """通用教务系统表单登录 + 课表页抓取"""
        login_url = session["login_url"]
        if username and password:
            await client.post(
                login_url,
                data={
                    "username": username,
                    "password": password,
                    "yhm": username,
                    "mm": password,
                    "captcha": captcha,
                    "yzm": captcha,
                },
            )
        target_url = schedule_url or login_url
        resp = await client.get(target_url)
        courses = EduHtmlTableParser.parse_any_content(resp.text)
        if not courses:
            raise ValueError("未在该页面检测到课表结构，请填写具体的课表页面 URL 或直接粘贴课表网页 HTML 源码。")
        return courses

    async def fetch_by_cookie_or_url(
        self, target_url: str, cookie_str: str = "", post_data: Optional[Dict[str, Any]] = None
    ) -> Tuple[List[Course], str]:
        """
        支持内置网页预览 / 免验证码 Cookie 直连抓取课表页面：
        返回 (解析出的课程列表, 原始响应文本)
        """
        if httpx is None:
            raise RuntimeError("未安装 httpx 库，请先安装 httpx>=0.24.0")

        url = self.normalize_base_url(target_url)
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            )
        }
        if cookie_str and cookie_str.strip():
            headers["Cookie"] = cookie_str.strip()

        async with httpx.AsyncClient(follow_redirects=True, verify=False, timeout=15.0, headers=headers) as client:
            if post_data:
                resp = await client.post(url, data=post_data)
            else:
                resp = await client.get(url)
            text = resp.text
            courses = EduHtmlTableParser.parse_any_content(text)
            return courses, text
