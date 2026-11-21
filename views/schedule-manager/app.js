/**
 * 智能课表与日程管家 - 前端视图脚本 (views/schedule-manager/app.js)
 * 严格遵循 AstrBot 插件可视化视图安全与主题规范：
 * 1. 通过 window.AstrBotPluginView Bridge SDK 与后端 API 通信，禁止直接访问父窗口 DOM。
 * 2. 监听 window.AstrBotPluginView.onContext() 动态切换亮/暗色 CSS 变量。
 */

const PALETTE = [
  "#4F46E5",
  "#0EA5E9",
  "#10B981",
  "#F59E0B",
  "#EC4899",
  "#8B5CF6",
  "#EF4444",
  "#14B8A6",
  "#F97316",
  "#3B82F6",
];

const WEEKDAY_NAMES = ["", "周一", "周二", "周三", "周四", "周五", "周六", "周日"];

const DEFAULT_PERIOD_TIMES = {
  "1": { start: "08:00", end: "08:45" },
  "2": { start: "08:55", end: "09:40" },
  "3": { start: "10:00", end: "10:45" },
  "4": { start: "10:55", end: "11:40" },
  "5": { start: "14:00", end: "14:45" },
  "6": { start: "14:55", end: "15:40" },
  "7": { start: "16:00", end: "16:45" },
  "8": { start: "16:55", end: "17:40" },
  "9": { start: "19:00", end: "19:45" },
  "10": { start: "19:55", end: "20:40" },
  "11": { start: "20:50", end: "21:35" },
  "12": { start: "21:45", end: "22:30" },
};

// 全局状态
const state = {
  config: {
    semester_name: "2026-2027第一学期",
    start_date: "2026-09-07",
    total_weeks: 20,
    enable_reminder: false,
    remind_advance_minutes: 15,
    period_times: { ...DEFAULT_PERIOD_TIMES },
  },
  courses: [],
  events: [],
  currentWeek: 1,
  selectedWeek: 1,
  showInactiveCourses: true,
  eventsSortDesc: true,
  editingPeriodsDraft: {},
  eduSessionId: "",
  activeProfileId: "default",
  profiles: [{ id: "default", name: "默认课表", course_count: 0, is_active: true }],
  slotFocusedCourseId: {},
};

// ==================== Bridge SDK 封装 ====================

const PLUGIN_NAME = "astrbot_plugin_schedule";

function getBridge() {
  return window.AstrBotPluginView || window.AstrBotPluginPage || null;
}

/**
 * 等待 Bridge SDK 注入就绪（最多等待 800ms）
 */
async function waitForBridge(timeoutMs = 800) {
  let b = getBridge();
  if (b) return b;
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    await new Promise((r) => setTimeout(r, 40));
    b = getBridge();
    if (b) return b;
  }
  return null;
}

/**
 * 统一通过 window.AstrBotPluginView 调用后端 API
 * 1. 优先使用 window.AstrBotPluginView / window.AstrBotPluginPage Bridge SDK
 * 2. 若宿主环境未及时暴露 Bridge 对象，自动回退到同源 Dashboard 插件网关 (/api/plug/astrbot_plugin_schedule/<endpoint>)
 */
async function callBridgeApi(endpoint, method = "GET", payload = null) {
  const bridge = await waitForBridge(400);
  const cleanEndpoint = String(endpoint).replace(/^\/+/, "");
  const upperMethod = String(method).toUpperCase();

  if (bridge) {
    if (upperMethod === "GET" && typeof bridge.apiGet === "function") {
      return await bridge.apiGet(cleanEndpoint, payload || undefined);
    }
    if (upperMethod === "POST" && typeof bridge.apiPost === "function") {
      return await bridge.apiPost(cleanEndpoint, payload || {});
    }
    if (typeof bridge.request === "function") {
      return await bridge.request(cleanEndpoint, {
        method: upperMethod,
        body: payload,
        data: payload,
      });
    }
  }

  // 同源 HTTP 网关回退（兼容直接访问或 Bridge 延迟挂载场景）
  const fallbackUrls = [
    `/api/plug/${PLUGIN_NAME}/${cleanEndpoint}`,
    `/api/v1/plugins/extensions/${PLUGIN_NAME}/${cleanEndpoint}`,
    `/${PLUGIN_NAME}/${cleanEndpoint}`,
  ];

  const headers = { "Content-Type": "application/json" };
  try {
    const token = localStorage.getItem("token") || localStorage.getItem("astrbot_token");
    if (token) {
      headers["Authorization"] = token.startsWith("Bearer ") ? token : `Bearer ${token}`;
    }
  } catch (_) {
    // 受限 sandbox iframe 下忽略 localStorage 访问异常
  }

  let lastError = null;
  for (const url of fallbackUrls) {
    try {
      const fetchOpts = {
        method: upperMethod,
        headers,
        credentials: "include",
      };
      if (upperMethod !== "GET" && payload !== null) {
        fetchOpts.body = JSON.stringify(payload);
      }
      const resp = await fetch(url, fetchOpts);
      if (resp.status === 404) {
        continue;
      }
      const data = await resp.json();
      if (!resp.ok || data?.status === "error") {
        throw new Error(data?.message || `HTTP ${resp.status}`);
      }
      return data;
    } catch (err) {
      lastError = err;
    }
  }

  throw lastError || new Error("请求后端接口失败，请确认插件已在 AstrBot 中启用");
}

function applyThemeFromContext(ctx) {
  if (!ctx || typeof ctx !== "object") return;
  let theme = ctx.theme || ctx.colorMode || ctx.mode || "";
  if (typeof ctx.isDark === "boolean") {
    theme = ctx.isDark ? "dark" : "light";
  }
  const normalized = String(theme).toLowerCase().includes("dark") ? "dark" : "light";
  document.documentElement.setAttribute("data-theme", normalized);
}

// ==================== 工具函数 ====================

function showToast(message, type = "info") {
  const container = document.getElementById("toastContainer");
  if (!container) return;
  const item = document.createElement("div");
  item.className = `toast-item ${type}`;
  item.textContent = message;
  container.appendChild(item);
  setTimeout(() => {
    item.remove();
  }, 3200);
}

function formatDateYMD(dateObj) {
  const y = dateObj.getFullYear();
  const m = String(dateObj.getMonth() + 1).padStart(2, "0");
  const d = String(dateObj.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

function parseDateYMD(ymdStr) {
  if (!ymdStr) return new Date();
  const parts = String(ymdStr).split("-").map(Number);
  if (parts.length !== 3 || parts.some(isNaN)) return new Date();
  return new Date(parts[0], parts[1] - 1, parts[2]);
}

function getSortedPeriodNumbers(periodTimesObj) {
  const pt = periodTimesObj || state.config.period_times || DEFAULT_PERIOD_TIMES;
  const nums = Object.keys(pt)
    .map((k) => parseInt(k, 10))
    .filter((n) => !isNaN(n) && n >= 1);
  nums.sort((a, b) => a - b);
  return nums.length ? nums : [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12];
}

function formatWeeksSummary(weeks) {
  if (!Array.isArray(weeks) || weeks.length === 0) return "";
  const sorted = [...new Set(weeks.map(Number))].sort((a, b) => a - b);
  if (sorted.length === 1) return `第${sorted[0]}周`;

  const minW = sorted[0];
  const maxW = sorted[sorted.length - 1];
  const isAllOdd = sorted.every((w) => w % 2 === 1);
  const isAllEven = sorted.every((w) => w % 2 === 0);

  const expectedOddCount = Math.floor((maxW - minW) / 2) + 1;
  if (isAllOdd && sorted.length === expectedOddCount && sorted.length >= 3) {
    return `${minW}-${maxW}单周`;
  }
  if (isAllEven && sorted.length === expectedOddCount && sorted.length >= 3) {
    return `${minW}-${maxW}双周`;
  }

  // 合并连续区间
  const ranges = [];
  let start = sorted[0];
  let prev = sorted[0];
  for (let i = 1; i < sorted.length; i++) {
    if (sorted[i] === prev + 1) {
      prev = sorted[i];
    } else {
      ranges.push(start === prev ? `${start}` : `${start}-${prev}`);
      start = sorted[i];
      prev = sorted[i];
    }
  }
  ranges.push(start === prev ? `${start}` : `${start}-${prev}`);
  return `${ranges.join(",")}周`;
}

// ==================== 数据加载与同步 ====================

async function loadOverview(preserveSelectedWeek = false) {
  try {
    const res = await callBridgeApi("overview", "GET");
    const payload = res?.data && res.data.config ? res.data : res;
    if (payload && payload.config) {
      state.config = payload.config;
    }
    if (payload && Array.isArray(payload.courses)) {
      state.courses = payload.courses;
    }
    if (payload && typeof payload.current_week === "number") {
      state.currentWeek = payload.current_week;
    }
    if (payload && Array.isArray(payload.profiles)) {
      state.profiles = payload.profiles;
    }
    if (payload && payload.active_profile_id) {
      state.activeProfileId = payload.active_profile_id;
    }
    const totalW = Math.max(1, Number(state.config.total_weeks) || 20);
    if (!preserveSelectedWeek) {
      state.selectedWeek = Math.min(Math.max(1, state.currentWeek), totalW);
    } else {
      state.selectedWeek = Math.min(Math.max(1, state.selectedWeek), totalW);
    }

    renderTopbar();
    renderProfilesList();
    renderScheduleGrid();
    renderAllCoursesList();
    populateQuickCoursePeriods();
    populateTargetCoursesSelect();
  } catch (err) {
    console.error("加载 overview 失败:", err);
    showToast(`加载课表数据失败: ${err.message || err}`, "error");
    renderTopbar();
    renderScheduleGrid();
    renderAllCoursesList();
    populateQuickCoursePeriods();
  }
}

async function loadEvents() {
  try {
    const res = await callBridgeApi("events", "GET");
    const payload = res?.data && Array.isArray(res.data.events) ? res.data : res;
    if (payload && Array.isArray(payload.events)) {
      state.events = payload.events;
    }
    renderEventsList();
  } catch (err) {
    console.error("加载 events 失败:", err);
    showToast(`加载临时日程失败: ${err.message || err}`, "error");
    renderEventsList();
  }
}

// ==================== 顶部栏渲染 ====================

function renderTopbar() {
  const semesterTitleEl = document.getElementById("semesterTitle");
  const currentWeekBadgeEl = document.getElementById("currentWeekBadge");
  const courseCountBadgeEl = document.getElementById("courseCountBadge");
  const weekSelectEl = document.getElementById("weekSelect");

  semesterTitleEl.textContent = state.config.semester_name || "未命名学期";

  // 渲染顶部课表档案切换下拉框
  const profileSelectEl = document.getElementById("profileSelect");
  if (profileSelectEl && Array.isArray(state.profiles)) {
    profileSelectEl.innerHTML = "";
    for (const p of state.profiles) {
      const opt = document.createElement("option");
      opt.value = p.id;
      opt.textContent = `🗂️ ${p.name} (${p.course_count ?? 0}门课)`;
      if (p.id === state.activeProfileId || p.is_active) {
        opt.selected = true;
      }
      profileSelectEl.appendChild(opt);
    }
  }

  const totalW = Math.max(1, Number(state.config.total_weeks) || 20);
  const cw = state.currentWeek;
  const parity = cw % 2 === 1 ? "单周" : "双周";
  if (cw < 1) {
    currentWeekBadgeEl.textContent = `未开学 (距第一周还有 ${Math.abs(cw - 1)} 周)`;
  } else if (cw > totalW) {
    currentWeekBadgeEl.textContent = `已结课 (第 ${cw}/${totalW} 周)`;
  } else {
    currentWeekBadgeEl.textContent = `当前：第 ${cw} 教学周 (${parity})`;
  }

  courseCountBadgeEl.textContent = `${state.courses.length} 门固定课程`;

  // 同步顶部是否提醒快捷开关状态
  const topReminderToggle = document.getElementById("topReminderToggle");
  const topReminderLabel = document.getElementById("topReminderLabel");
  const isRemindOn = Boolean(state.config.enable_reminder);
  const remindMins = Number(state.config.remind_advance_minutes) || 15;
  if (topReminderToggle) {
    topReminderToggle.checked = isRemindOn;
  }
  if (topReminderLabel) {
    topReminderLabel.textContent = isRemindOn
      ? `🔔 课前提醒：开（提前${remindMins}分钟）`
      : "🔕 课前提醒：关（仅询问时回答）";
  }

  // 渲染 1~total_weeks 下拉选项
  weekSelectEl.innerHTML = "";
  for (let w = 1; w <= totalW; w++) {
    const opt = document.createElement("option");
    opt.value = String(w);
    const wParity = w % 2 === 1 ? "单周" : "双周";
    const isCur = w === cw ? " (本周)" : "";
    opt.textContent = `第 ${w} 周 · ${wParity}${isCur}`;
    if (w === state.selectedWeek) {
      opt.selected = true;
    }
    weekSelectEl.appendChild(opt);
  }
}

// ==================== Tab 1：周课表 7列网格渲染 ====================

function renderWeekDatesHeader() {
  const startMonday = parseDateYMD(state.config.start_date);
  const weekOffsetDays = (state.selectedWeek - 1) * 7;
  const todayStr = formatDateYMD(new Date());

  for (let day = 1; day <= 7; day++) {
    const cellDate = new Date(startMonday);
    cellDate.setDate(startMonday.getDate() + weekOffsetDays + (day - 1));
    const m = cellDate.getMonth() + 1;
    const d = cellDate.getDate();
    const dateSpan = document.getElementById(`thDate${day}`);
    const thEl = dateSpan ? dateSpan.closest("th") : null;
    if (dateSpan) {
      dateSpan.textContent = `${m}/${d}`;
    }
    if (thEl) {
      const cellYmd = formatDateYMD(cellDate);
      if (cellYmd === todayStr) {
        thEl.classList.add("today-col");
      } else {
        thEl.classList.remove("today-col");
      }
    }
  }
}

function renderScheduleGrid() {
  renderWeekDatesHeader();

  const tbody = document.getElementById("scheduleGridBody");
  tbody.innerHTML = "";

  const periodNums = getSortedPeriodNumbers(state.config.period_times);
  const maxPeriod = periodNums[periodNums.length - 1] || 12;
  const selWeek = state.selectedWeek;

  // 构建 [day][period] 的课程映射与被跨行占用的标记
  const skipMap = {}; // key: `${day}-${period}` -> true
  const slotCoursesMap = {}; // key: `${day}-${period}` -> { span, courses: [] }

  // 过滤出需要展示的课程（当前周生效在前，非当前周在后）
  const relevantCourses = state.courses.filter((c) => {
    const isThisWeek = Array.isArray(c.weeks) && c.weeks.map(Number).includes(selWeek);
    return isThisWeek || state.showInactiveCourses;
  });

  relevantCourses.sort((a, b) => {
    const aActive = Array.isArray(a.weeks) && a.weeks.map(Number).includes(selWeek) ? 1 : 0;
    const bActive = Array.isArray(b.weeks) && b.weeks.map(Number).includes(selWeek) ? 1 : 0;
    if (aActive !== bActive) return bActive - aActive;
    return (a.start_period || 1) - (b.start_period || 1);
  });

  for (const course of relevantCourses) {
    const day = Math.min(7, Math.max(1, Number(course.day_of_week) || 1));
    const sp = Math.max(1, Number(course.start_period) || 1);
    const ep = Math.min(maxPeriod, Math.max(sp, Number(course.end_period) || sp));

    // 检查该区间是否已被之前的卡片跨行覆盖
    let conflictWithSpan = false;
    for (let p = sp; p <= ep; p++) {
      if (skipMap[`${day}-${p}`]) {
        conflictWithSpan = true;
        break;
      }
    }

    const slotKey = `${day}-${sp}`;
    if (!slotCoursesMap[slotKey]) {
      if (!conflictWithSpan) {
        const span = ep - sp + 1;
        slotCoursesMap[slotKey] = { span, courses: [course] };
        for (let p = sp + 1; p <= ep; p++) {
          skipMap[`${day}-${p}`] = true;
        }
      } else {
        // 如果部分节次冲突，降级为单节显示
        if (!skipMap[slotKey]) {
          slotCoursesMap[slotKey] = { span: 1, courses: [course] };
        }
      }
    } else {
      // 同一大节起始位置有多门课程（如单双周同位置不同课）
      slotCoursesMap[slotKey].courses.push(course);
    }
  }

  for (const pNum of periodNums) {
    const tr = document.createElement("tr");

    // 左侧节次时间列
    const pInfo = (state.config.period_times && state.config.period_times[String(pNum)]) || {
      start: "08:00",
      end: "08:45",
    };
    const tdPeriod = document.createElement("td");
    tdPeriod.className = "period-cell";
    tdPeriod.innerHTML = `
      <div class="period-num">第 ${pNum} 节</div>
      <div class="period-time">${pInfo.start} - ${pInfo.end}</div>
    `;
    tr.appendChild(tdPeriod);

    // 周一至周日 7 列
    for (let day = 1; day <= 7; day++) {
      const key = `${day}-${pNum}`;
      if (skipMap[key]) {
        continue;
      }

      const td = document.createElement("td");
      td.className = "grid-slot-cell";
      const slotData = slotCoursesMap[key];

      if (slotData && slotData.courses.length > 0) {
        if (slotData.span > 1) {
          td.rowSpan = slotData.span;
          td.style.height = `${slotData.span * 64}px`;
        }

        const totalInSlot = slotData.courses.length;
        const stack = document.createElement("div");
        stack.className =
          totalInSlot > 1 ? "slot-cards-stack multi-course-drawer" : "slot-cards-stack";

        // 确定哪一门课作为主卡展开（优先用户手动切换选中的课，否则默认第 1 门即本周生效课）
        const savedFocusId = state.slotFocusedCourseId[key];
        let focusedIdx = 0;
        if (savedFocusId) {
          const foundIdx = slotData.courses.findIndex((c) => c.id === savedFocusId);
          if (foundIdx !== -1) focusedIdx = foundIdx;
        }

        slotData.courses.forEach((course, idx) => {
          const isActive =
            Array.isArray(course.weeks) && course.weeks.map(Number).includes(selWeek);
          const isPrimary = totalInSlot === 1 || idx === focusedIdx;
          const weeksText = formatWeeksSummary(course.weeks);

          // 规避旧数据中 teacher 与 location 重复显示的问题
          let cleanLoc = (course.location || "").trim();
          let cleanTeacher = (course.teacher || "").trim();
          if (cleanLoc && cleanTeacher && cleanTeacher === cleanLoc) {
            cleanTeacher = "";
          } else if (cleanLoc && cleanLoc.includes(" ") && !cleanTeacher) {
            const parts = cleanLoc.split(/\s+/);
            if (parts.length === 2 && parts[1].length <= 5) {
              cleanLoc = parts[0];
              cleanTeacher = parts[1];
            }
          }

          const card = document.createElement("div");
          card.style.backgroundColor = course.color || "#4F46E5";

          if (isPrimary) {
            card.className = `course-card primary-card ${
              isActive ? "active-week" : "inactive-week"
            }`;
            const teacherHtml = cleanTeacher ? `<span>👤 ${escapeHtml(cleanTeacher)}</span>` : "";
            const locHtml = cleanLoc ? `<span>📍 ${escapeHtml(cleanLoc)}</span>` : "";
            const badgeLabel = isActive ? weeksText : `[非本周] ${weeksText}`;
            const switchPillHtml =
              totalInSlot > 1
                ? `<span class="drawer-switch-hint" title="点击切换到同一时间段的另一门课">⇄ ${
                    idx + 1
                  }/${totalInSlot}</span>`
                : "";

            card.innerHTML = `
              ${switchPillHtml}
              <div>
                <div class="course-card-title">${escapeHtml(course.name)}</div>
                <div class="course-card-meta">
                  ${locHtml}
                  ${teacherHtml}
                </div>
              </div>
              <span class="course-card-badge">${escapeHtml(badgeLabel)}</span>
            `;

            if (totalInSlot > 1) {
              const switchBtn = card.querySelector(".drawer-switch-hint");
              if (switchBtn) {
                switchBtn.addEventListener("click", (e) => {
                  e.stopPropagation();
                  const nextCourse = slotData.courses[(idx + 1) % totalInSlot];
                  state.slotFocusedCourseId[key] = nextCourse.id;
                  renderScheduleGrid();
                });
              }
            }

            card.addEventListener("click", (e) => {
              e.stopPropagation();
              openCourseModal(course);
            });
          } else {
            // 底部折叠抽屉窄条：悬停或点击第 1 下即可展开为主卡，展开后再点可编辑
            card.className = `course-card drawer-strip ${
              isActive ? "active-week" : "inactive-week"
            }`;
            card.title = `点击展开查看《${course.name}》(${weeksText})`;
            card.innerHTML = `
              <span class="drawer-strip-title">▸ ${escapeHtml(course.name)}</span>
              <span class="drawer-strip-week">${escapeHtml(weeksText)}</span>
            `;

            card.addEventListener("click", (e) => {
              e.stopPropagation();
              state.slotFocusedCourseId[key] = course.id;
              renderScheduleGrid();
            });
          }

          stack.appendChild(card);
        });

        td.appendChild(stack);
      } else {
        td.classList.add("empty-slot");
        td.title = `点击添加 ${WEEKDAY_NAMES[day]} 第${pNum}节 课程`;
        td.addEventListener("click", () => {
          const defaultEnd = Math.min(maxPeriod, pNum + 1);
          openCourseModal(null, {
            day_of_week: day,
            start_period: pNum,
            end_period: defaultEnd,
          });
        });
      }

      tr.appendChild(td);
    }

    tbody.appendChild(tr);
  }
}

function normalizeLocationAndTeacher(rawLoc, rawTeacher) {
  let cleanLoc = String(rawLoc || "").trim();
  let cleanTeacher = String(rawTeacher || "").trim();
  if (cleanLoc && cleanTeacher && cleanTeacher === cleanLoc) {
    cleanTeacher = "";
  }
  if (cleanLoc && cleanLoc.includes(" ") && !cleanTeacher) {
    const parts = cleanLoc.split(/\s+/);
    if (parts.length === 2 && parts[1].length <= 6 && !/\d/.test(parts[1])) {
      cleanLoc = parts[0];
      cleanTeacher = parts[1];
    }
  }
  return { cleanLoc, cleanTeacher };
}

function escapeHtml(str) {
  return String(str || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// ==================== 课程新增/编辑弹窗 ====================

function renderColorPresets(selectedColor) {
  const container = document.getElementById("colorPresets");
  const colorInput = document.getElementById("courseColorInput");
  container.innerHTML = "";
  for (const hex of PALETTE) {
    const swatch = document.createElement("button");
    swatch.type = "button";
    swatch.className = `color-swatch ${
      hex.toLowerCase() === String(selectedColor).toLowerCase() ? "active" : ""
    }`;
    swatch.style.backgroundColor = hex;
    swatch.addEventListener("click", () => {
      colorInput.value = hex;
      container.querySelectorAll(".color-swatch").forEach((el) => el.classList.remove("active"));
      swatch.classList.add("active");
    });
    container.appendChild(swatch);
  }
}

function populatePeriodSelects(startVal = 1, endVal = 2) {
  const startSel = document.getElementById("courseStartPeriodSelect");
  const endSel = document.getElementById("courseEndPeriodSelect");
  const periodNums = getSortedPeriodNumbers(state.config.period_times);

  startSel.innerHTML = "";
  endSel.innerHTML = "";

  for (const p of periodNums) {
    const opt1 = document.createElement("option");
    opt1.value = String(p);
    opt1.textContent = `第 ${p} 节`;
    if (p === Number(startVal)) opt1.selected = true;
    startSel.appendChild(opt1);

    const opt2 = document.createElement("option");
    opt2.value = String(p);
    opt2.textContent = `第 ${p} 节`;
    if (p === Number(endVal)) opt2.selected = true;
    endSel.appendChild(opt2);
  }
}

function renderWeekPills(selectedWeeks = []) {
  const container = document.getElementById("weekPillsContainer");
  container.innerHTML = "";
  const totalW = Math.max(1, Number(state.config.total_weeks) || 20);
  const selectedSet = new Set(selectedWeeks.map(Number));

  for (let w = 1; w <= totalW; w++) {
    const pill = document.createElement("div");
    pill.className = `week-pill ${selectedSet.has(w) ? "selected" : ""}`;
    pill.dataset.week = String(w);
    pill.textContent = `${w}周`;
    pill.addEventListener("click", () => {
      pill.classList.toggle("selected");
    });
    container.appendChild(pill);
  }
}

function getSelectedWeekPills() {
  const pills = document.querySelectorAll("#weekPillsContainer .week-pill.selected");
  return Array.from(pills)
    .map((el) => parseInt(el.dataset.week, 10))
    .filter((n) => !isNaN(n))
    .sort((a, b) => a - b);
}

function openCourseModal(course = null, prefill = {}) {
  const modal = document.getElementById("courseModal");
  const titleEl = document.getElementById("courseModalTitle");
  const deleteBtn = document.getElementById("deleteCourseBtn");

  const totalW = Math.max(1, Number(state.config.total_weeks) || 20);
  const allWeeks = Array.from({ length: totalW }, (_, i) => i + 1);

  if (course) {
    titleEl.textContent = "编辑固定课程";
    const { cleanLoc, cleanTeacher } = normalizeLocationAndTeacher(
      course.location,
      course.teacher
    );
    document.getElementById("courseIdInput").value = course.id || "";
    document.getElementById("courseNameInput").value = course.name || "";
    document.getElementById("courseTeacherInput").value = cleanTeacher;
    document.getElementById("courseLocationInput").value = cleanLoc;
    document.getElementById("courseDaySelect").value = String(course.day_of_week || 1);
    const color = course.color || PALETTE[0];
    document.getElementById("courseColorInput").value = color;
    renderColorPresets(color);
    populatePeriodSelects(course.start_period || 1, course.end_period || course.start_period || 2);
    renderWeekPills(Array.isArray(course.weeks) ? course.weeks : allWeeks);
    deleteBtn.style.display = "inline-flex";
  } else {
    titleEl.textContent = prefill.name ? `为《${prefill.name}》增加上课时段` : "添加固定课程";
    document.getElementById("courseIdInput").value = "";
    document.getElementById("courseNameInput").value = prefill.name || "";
    document.getElementById("courseTeacherInput").value = prefill.teacher || "";
    document.getElementById("courseLocationInput").value = prefill.location || "";
    document.getElementById("courseDaySelect").value = String(prefill.day_of_week || 1);
    const randomColor = prefill.color || PALETTE[state.courses.length % PALETTE.length];
    document.getElementById("courseColorInput").value = randomColor;
    renderColorPresets(randomColor);
    populatePeriodSelects(prefill.start_period || 1, prefill.end_period || 2);
    renderWeekPills(allWeeks);
    deleteBtn.style.display = "none";
  }

  modal.style.display = "flex";
}

async function handleSaveCourse(e) {
  e.preventDefault();
  const id = document.getElementById("courseIdInput").value.trim();
  const name = document.getElementById("courseNameInput").value.trim();
  const teacher = document.getElementById("courseTeacherInput").value.trim();
  const location = document.getElementById("courseLocationInput").value.trim();
  const day_of_week = parseInt(document.getElementById("courseDaySelect").value, 10) || 1;
  const start_period = parseInt(document.getElementById("courseStartPeriodSelect").value, 10) || 1;
  const end_period = parseInt(document.getElementById("courseEndPeriodSelect").value, 10) || start_period;
  const color = document.getElementById("courseColorInput").value || "#4F46E5";
  const weeks = getSelectedWeekPills();

  if (!name) {
    showToast("请输入课程名称", "error");
    return;
  }
  if (end_period < start_period) {
    showToast("结束节次不能小于起始节次", "error");
    return;
  }
  if (weeks.length === 0) {
    showToast("请至少选择一个上课教学周次", "error");
    return;
  }

  const coursePayload = {
    name,
    teacher,
    location,
    day_of_week,
    start_period,
    end_period,
    weeks,
    color,
  };
  if (id) {
    coursePayload.id = id;
  }

  try {
    await callBridgeApi("course/save", "POST", coursePayload);
    showToast(`课程《${name}》已保存`, "success");
    closeModal("courseModal");
    await loadOverview(true);
  } catch (err) {
    showToast(`保存课程失败: ${err.message || err}`, "error");
  }
}

async function handleDeleteCourse() {
  const id = document.getElementById("courseIdInput").value.trim();
  const name = document.getElementById("courseNameInput").value.trim();
  if (!id) return;

  try {
    await callBridgeApi("course/delete", "POST", { id });
    showToast(`已删除课程《${name}》`, "success");
    closeModal("courseModal");
    await loadOverview(true);
  } catch (err) {
    showToast(`删除课程失败: ${err.message || err}`, "error");
  }
}

let clearConfirmTimer = null;

async function handleClearAllCourses(triggerBtnId = "clearAllCoursesBtn") {
  const btn = document.getElementById(triggerBtnId) || document.getElementById("clearAllCoursesBtn");
  if (!state.courses.length) {
    showToast("当前课表已经是空的", "info");
    return;
  }

  // 采用按钮二次点击确认（兼容受限 iframe 禁用 window.confirm 的情况）
  if (btn && btn.dataset.confirming !== "true") {
    btn.dataset.confirming = "true";
    const origText = btn.textContent;
    btn.textContent = `⚠️ 确认清空 ${state.courses.length} 门课？`;
    if (clearConfirmTimer) clearTimeout(clearConfirmTimer);
    clearConfirmTimer = setTimeout(() => {
      btn.dataset.confirming = "false";
      btn.textContent = origText;
    }, 3500);
    return;
  }

  if (btn) {
    btn.dataset.confirming = "false";
    btn.textContent = triggerBtnId === "clearAllCoursesInListBtn" ? "🗑️ 清空全部" : "🗑️ 清空课表";
  }

  try {
    const res = await callBridgeApi("course/delete", "POST", { clear_all: true });
    const msg = res?.message || res?.data?.message || "已清空全部固定课程";
    showToast(msg, "success");
    await loadOverview(true);
  } catch (err) {
    showToast(`清空课表失败: ${err.message || err}`, "error");
  }
}

// ==================== Tab 2：全部固定课程总表（查看、搜索、快速添加、编辑、删除） ====================

function parseWeeksStringJS(expr, maxWeek = 20) {
  const text = String(expr || "").trim();
  if (!text) return Array.from({ length: Math.min(16, maxWeek) }, (_, i) => i + 1);
  const weeksSet = new Set();
  const segments = text.split(/[,，;；、\s]+/);
  for (const seg of segments) {
    if (!seg) continue;
    const isOdd = seg.includes("单");
    const isEven = seg.includes("双");
    const rangeMatch = seg.match(/(\d+)\s*[-~至到]\s*(\d+)/);
    if (rangeMatch) {
      let s = parseInt(rangeMatch[1], 10);
      let e = parseInt(rangeMatch[2], 10);
      if (s > e) [s, e] = [e, s];
      for (let w = Math.max(1, s); w <= Math.min(40, e); w++) {
        if (isOdd && w % 2 === 0) continue;
        if (isEven && w % 2 === 1) continue;
        weeksSet.add(w);
      }
    } else {
      const nums = seg.match(/\d+/g);
      if (nums) {
        nums.forEach((n) => {
          const w = parseInt(n, 10);
          if (w >= 1 && w <= 40) weeksSet.add(w);
        });
      }
    }
  }
  const arr = Array.from(weeksSet).sort((a, b) => a - b);
  return arr.length ? arr : Array.from({ length: Math.min(16, maxWeek) }, (_, i) => i + 1);
}

function populateQuickCoursePeriods() {
  const startSel = document.getElementById("qcStartPeriod");
  const endSel = document.getElementById("qcEndPeriod");
  if (!startSel || !endSel) return;

  const periodNums = getSortedPeriodNumbers(state.config.period_times);
  startSel.innerHTML = "";
  endSel.innerHTML = "";
  for (const p of periodNums) {
    const opt1 = document.createElement("option");
    opt1.value = String(p);
    opt1.textContent = `第 ${p} 节`;
    if (p === 1) opt1.selected = true;
    startSel.appendChild(opt1);

    const opt2 = document.createElement("option");
    opt2.value = String(p);
    opt2.textContent = `第 ${p} 节`;
    if (p === 2) opt2.selected = true;
    endSel.appendChild(opt2);
  }
}

async function handleQuickCourseSubmit(e) {
  e.preventDefault();
  const name = document.getElementById("qcName").value.trim();
  const teacher = document.getElementById("qcTeacher").value.trim();
  const location = document.getElementById("qcLocation").value.trim();
  const day_of_week = parseInt(document.getElementById("qcDay").value, 10) || 1;
  const start_period = parseInt(document.getElementById("qcStartPeriod").value, 10) || 1;
  const end_period = parseInt(document.getElementById("qcEndPeriod").value, 10) || start_period;
  const weeksExpr = document.getElementById("qcWeeksExpr").value.trim();

  if (!name) {
    showToast("请输入课程名称", "error");
    return;
  }
  if (end_period < start_period) {
    showToast("结束节次不能小于起始节次", "error");
    return;
  }

  const totalW = Math.max(1, Number(state.config.total_weeks) || 20);
  const weeks = parseWeeksStringJS(weeksExpr, totalW);
  const color = PALETTE[state.courses.length % PALETTE.length];

  try {
    await callBridgeApi("course/save", "POST", {
      name,
      teacher,
      location,
      day_of_week,
      start_period,
      end_period,
      weeks,
      color,
    });
    showToast(`已添加课程《${name}》`, "success");
    document.getElementById("qcName").value = "";
    await loadOverview(true);
  } catch (err) {
    showToast(`添加课程失败: ${err.message || err}`, "error");
  }
}

function renderAllCoursesList() {
  const container = document.getElementById("allCoursesListContainer");
  const counterEl = document.getElementById("coursesCounter");
  const uniqueCourseCount = new Set(state.courses.map((c) => (c.name || "").trim())).size;
  if (counterEl) {
    counterEl.textContent =
      uniqueCourseCount !== state.courses.length
        ? `${uniqueCourseCount}门 / ${state.courses.length}时段`
        : String(state.courses.length);
  }
  if (!container) return;

  const keyword = (document.getElementById("courseSearchInput")?.value || "").trim().toLowerCase();
  const dayFilter = parseInt(document.getElementById("courseDayFilterSelect")?.value || "0", 10);

  const filtered = state.courses
    .filter((c) => {
      if (dayFilter >= 1 && dayFilter <= 7 && Number(c.day_of_week) !== dayFilter) {
        return false;
      }
      if (keyword) {
        const hay = `${c.name || ""} ${c.teacher || ""} ${c.location || ""}`.toLowerCase();
        return hay.includes(keyword);
      }
      return true;
    })
    .sort((a, b) => {
      if (a.day_of_week !== b.day_of_week) return a.day_of_week - b.day_of_week;
      if (a.start_period !== b.start_period) return a.start_period - b.start_period;
      return String(a.name).localeCompare(String(b.name));
    });

  if (filtered.length === 0) {
    container.innerHTML = `<div class="empty-state">${
      state.courses.length === 0
        ? "当前还没有添加任何固定课程。可以在上方表单直接添加，或点击顶部「🎓 教务处登录导入」/「📥 文件导入」！"
        : "没有匹配筛选条件的课程。"
    }</div>`;
    return;
  }

  // 按课程名称聚合同一门课的所有时段（方案 A：聚合卡片 + 内嵌全部时段列表）
  const groupedMap = new Map();
  for (const c of filtered) {
    const keyName = (c.name || "未命名课程").trim();
    if (!groupedMap.has(keyName)) {
      groupedMap.set(keyName, []);
    }
    groupedMap.get(keyName).push(c);
  }

  container.innerHTML = "";
  const pt = state.config.period_times || DEFAULT_PERIOD_TIMES;

  for (const [courseName, slots] of groupedMap.entries()) {
    const firstSlot = slots[0];
    const color = firstSlot.color || "#4F46E5";

    // 提取这门课汇总的教师与总周次范围
    const teachersSet = new Set();
    const locationsSet = new Set();
    const allWeeksSet = new Set();

    slots.forEach((s) => {
      const { cleanLoc, cleanTeacher } = normalizeLocationAndTeacher(s.location, s.teacher);
      if (cleanTeacher) teachersSet.add(cleanTeacher);
      if (cleanLoc) locationsSet.add(cleanLoc);
      if (Array.isArray(s.weeks)) s.weeks.forEach((w) => allWeeksSet.add(Number(w)));
    });

    const teachersStr = Array.from(teachersSet).join("、") || "未填";
    const defaultLoc = Array.from(locationsSet)[0] || "";
    const defaultTeacher = Array.from(teachersSet)[0] || "";
    const totalWeeksSummary = formatWeeksSummary(Array.from(allWeeksSet));

    const groupCard = document.createElement("div");
    groupCard.className = "course-group-card";
    groupCard.style.borderLeft = `4px solid ${color}`;

    groupCard.innerHTML = `
      <div class="course-group-header">
        <div class="course-group-title-area">
          <span class="course-color-dot" style="background-color: ${escapeHtml(color)};"></span>
          <span class="event-title-text">${escapeHtml(courseName)}</span>
          <span class="course-slot-count-badge">共 ${slots.length} 个时段</span>
          <span class="course-total-weeks-badge">📅 总周次：${escapeHtml(totalWeeksSummary)}</span>
          <span class="course-teacher-summary">👤 教师：${escapeHtml(teachersStr)}</span>
        </div>
        <div class="course-group-actions">
          <button type="button" class="btn btn-outline btn-xs add-slot-for-course-btn" title="为该课程新增一个上课时段/不同周次">
            ＋ 增加时段/周次
          </button>
          <button type="button" class="btn btn-danger btn-xs delete-whole-course-btn" title="删除该课程下的全部时段">
            🗑️ 整门删除
          </button>
        </div>
      </div>
      <div class="course-group-slots-list"></div>
    `;

    // 点击「＋ 增加时段/周次」预填该课程名、教师、地点和颜色
    groupCard.querySelector(".add-slot-for-course-btn").addEventListener("click", () => {
      openCourseModal(null, {
        name: courseName,
        teacher: defaultTeacher,
        location: defaultLoc,
        color,
        day_of_week: firstSlot.day_of_week || 1,
        start_period: firstSlot.start_period || 1,
        end_period: firstSlot.end_period || 2,
      });
    });

    // 点击「🗑️ 整门删除」二次确认删除该门课的所有时段
    const delWholeBtn = groupCard.querySelector(".delete-whole-course-btn");
    delWholeBtn.addEventListener("click", async () => {
      if (slots.length > 1 && delWholeBtn.dataset.confirming !== "true") {
        delWholeBtn.dataset.confirming = "true";
        delWholeBtn.textContent = `⚠️ 确认删 ${slots.length} 个时段？`;
        setTimeout(() => {
          delWholeBtn.dataset.confirming = "false";
          delWholeBtn.textContent = "🗑️ 整门删除";
        }, 3000);
        return;
      }
      try {
        const ids = slots.map((s) => s.id);
        await callBridgeApi("course/delete", "POST", { ids });
        showToast(`已删除《${courseName}》的全部 ${slots.length} 个时段`, "success");
        await loadOverview(true);
      } catch (err) {
        showToast(`删除失败: ${err.message || err}`, "error");
      }
    });

    // 渲染该门课程下属的每一个时段胶囊行
    const slotsContainer = groupCard.querySelector(".course-group-slots-list");
    for (const c of slots) {
      const dayName = WEEKDAY_NAMES[c.day_of_week] || `周${c.day_of_week}`;
      const pText =
        c.start_period === c.end_period
          ? `第 ${c.start_period} 节`
          : `第 ${c.start_period}-${c.end_period} 节`;
      const startT = pt[String(c.start_period)]?.start || "08:00";
      const endT = pt[String(c.end_period)]?.end || "09:40";
      const weeksText = formatWeeksSummary(c.weeks);
      const isCurWeekActive =
        Array.isArray(c.weeks) && c.weeks.map(Number).includes(state.selectedWeek);
      const { cleanLoc, cleanTeacher } = normalizeLocationAndTeacher(c.location, c.teacher);

      const slotItem = document.createElement("div");
      slotItem.className = "course-slot-subrow";
      slotItem.innerHTML = `
        <div class="course-slot-left">
          <span class="slot-time-badge">
            <strong>${escapeHtml(dayName)} · ${escapeHtml(pText)}</strong>
            <span class="slot-clock-sub">(${escapeHtml(startT)}-${escapeHtml(endT)})</span>
          </span>
          <span class="event-type-tag ${isCurWeekActive ? "custom" : ""}" style="${
        !isCurWeekActive
          ? "background: var(--bg-surface); color: var(--text-muted); border: 1px solid var(--border-color);"
          : ""
      }">
            📅 ${escapeHtml(weeksText)}${isCurWeekActive ? " (本周上课)" : ""}
          </span>
          <span class="slot-meta-text">📍 地点：${escapeHtml(cleanLoc || "未填")}</span>
          ${
            teachersSet.size > 1 && cleanTeacher
              ? `<span class="slot-meta-text">👤 教师：${escapeHtml(cleanTeacher)}</span>`
              : ""
          }
        </div>
        <div class="course-slot-actions">
          <button type="button" class="btn btn-outline btn-xs edit-slot-btn">
            ✏️ 编辑时段/周次
          </button>
          <button type="button" class="btn btn-ghost btn-xs delete-slot-btn" title="仅删除这一个时段">
            ✕ 移除时段
          </button>
        </div>
      `;

      slotItem.querySelector(".edit-slot-btn").addEventListener("click", () => {
        openCourseModal(c);
      });

      slotItem.querySelector(".delete-slot-btn").addEventListener("click", async () => {
        try {
          await callBridgeApi("course/delete", "POST", { id: c.id });
          showToast(`已移除《${courseName}》的 ${dayName}${pText} 时段`, "success");
          await loadOverview(true);
        } catch (err) {
          showToast(`删除失败: ${err.message || err}`, "error");
        }
      });

      slotsContainer.appendChild(slotItem);
    }

    container.appendChild(groupCard);
  }
}

// ==================== 课表导入 (.ics / .json) ====================

function handleLocalFileSelected(file) {
  if (!file) return;
  const nameEl = document.getElementById("selectedFileName");
  const typeSelect = document.getElementById("importFileTypeSelect");
  const textarea = document.getElementById("importContentTextarea");

  nameEl.textContent = `已选择文件：${file.name} (${(file.size / 1024).toFixed(1)} KB)`;

  const lower = file.name.toLowerCase();
  if (lower.endsWith(".json")) {
    typeSelect.value = "json";
  } else if (lower.endsWith(".ics")) {
    typeSelect.value = "ics";
  } else if (lower.endsWith(".html") || lower.endsWith(".htm") || lower.endsWith(".txt")) {
    typeSelect.value = "html";
  }

  const reader = new FileReader();
  reader.onload = (ev) => {
    textarea.value = String(ev.target?.result || "");
  };
  reader.onerror = () => {
    showToast("读取本地文件失败", "error");
  };
  reader.readAsText(file, "utf-8");
}

async function handleConfirmImport() {
  const file_type = document.getElementById("importFileTypeSelect").value;
  const mode = document.getElementById("importModeSelect").value;
  const content = document.getElementById("importContentTextarea").value.trim();

  if (!content) {
    showToast("请先选择本地 .ics / .json 文件或粘贴课表内容", "error");
    return;
  }

  const btn = document.getElementById("confirmImportBtn");
  const origText = btn.textContent;
  btn.disabled = true;
  btn.textContent = "⏳ 正在解析导入...";

  try {
    const res = await callBridgeApi("import", "POST", {
      file_type,
      content,
      mode,
    });
    const msg = res?.message || res?.data?.message || "课表导入成功";
    showToast(msg, "success");
    closeModal("importModal");
    await loadOverview(true);
  } catch (err) {
    showToast(`导入失败: ${err.message || err}`, "error");
  } finally {
    btn.disabled = false;
    btn.textContent = origText;
  }
}

// ==================== 教务处自动登录 / 内置网页与 HTML 一键导入 ====================

const SYS_TYPE_LABELS = {
  zfnew: "新版正方教务系统",
  qz: "强智科技教务系统",
  zfold: "旧版正方教务系统",
  generic: "通用教务系统",
};

async function handleEduConnect() {
  const urlInput = document.getElementById("eduLoginUrl");
  const sysSelect = document.getElementById("eduSysType");
  const statusEl = document.getElementById("eduSessionStatus");
  const btn = document.getElementById("eduConnectBtn");
  const captchaImg = document.getElementById("eduCaptchaImg");
  const captchaPlaceholder = document.getElementById("eduCaptchaPlaceholder");
  const refreshBtn = document.getElementById("eduRefreshCaptchaBtn");

  const url = urlInput.value.trim();
  if (!url) {
    showToast("请先输入学校教务处登录网址 URL", "error");
    return;
  }

  const origText = btn.textContent;
  btn.disabled = true;
  btn.textContent = "⏳ 正在连接...";

  try {
    const res = await callBridgeApi("edu/init", "POST", {
      url,
      sys_type: sysSelect.value,
    });
    const data = res?.data && res.data.session_id ? res.data : res;
    state.eduSessionId = data.session_id || "";

    const sysLabel = SYS_TYPE_LABELS[data.sys_type] || data.sys_type || "教务系统";
    statusEl.textContent = `✅ 已连接（识别为：${sysLabel}）`;
    statusEl.classList.add("connected");

    if (data.captcha_base64) {
      captchaImg.src = data.captcha_base64;
      captchaImg.style.display = "inline-block";
      captchaPlaceholder.style.display = "none";
      refreshBtn.style.display = "inline-flex";
      showToast("已连接教务处并获取图形验证码", "success");
    } else {
      captchaImg.style.display = "none";
      refreshBtn.style.display = "none";
      captchaPlaceholder.style.display = "inline";
      captchaPlaceholder.textContent = "当前登录页未检测到图形验证码，可直接输入账号密码登录";
      showToast(`已成功连接教务处（${sysLabel}）`, "success");
    }
  } catch (err) {
    statusEl.textContent = `❌ 连接失败：${err.message || err}`;
    statusEl.classList.remove("connected");
    showToast(`连接教务处失败: ${err.message || err}`, "error");
  } finally {
    btn.disabled = false;
    btn.textContent = origText;
  }
}

async function handleEduRefreshCaptcha() {
  if (!state.eduSessionId) {
    await handleEduConnect();
    return;
  }
  try {
    const res = await callBridgeApi("edu/captcha", "POST", {
      session_id: state.eduSessionId,
    });
    const data = res?.data && res.data.captcha_base64 ? res.data : res;
    if (data.captcha_base64) {
      const captchaImg = document.getElementById("eduCaptchaImg");
      captchaImg.src = data.captcha_base64;
      captchaImg.style.display = "inline-block";
    }
  } catch (err) {
    showToast(`刷新验证码失败: ${err.message || err}`, "error");
  }
}

async function handleEduLoginImport() {
  const username = document.getElementById("eduUsername").value.trim();
  const password = document.getElementById("eduPassword").value.trim();
  const captcha = document.getElementById("eduCaptchaInput").value.trim();
  const academic_year = document.getElementById("eduAcademicYear").value.trim() || "2026";
  const semester_term = document.getElementById("eduSemesterTerm").value || "1";
  const schedule_url = document.getElementById("eduCustomScheduleUrl").value.trim();
  const mode = document.getElementById("eduImportMode").value || "overwrite";

  if (!state.eduSessionId) {
    await handleEduConnect();
    if (!state.eduSessionId) return;
  }

  if (!username || !password) {
    showToast("请填写学号与教务处密码", "error");
    return;
  }

  const btn = document.getElementById("eduLoginImportBtn");
  const origText = btn.textContent;
  btn.disabled = true;
  btn.textContent = "⏳ 正在登录并抓取课表...";

  try {
    const res = await callBridgeApi("edu/login_import", "POST", {
      session_id: state.eduSessionId,
      username,
      password,
      captcha,
      academic_year,
      semester_term,
      schedule_url,
      mode,
    });
    const msg = res?.message || res?.data?.message || "教务处课表导入成功！";
    showToast(msg, "success");
    closeModal("eduModal");
    await loadOverview(true);
  } catch (err) {
    showToast(`教务处导入失败: ${err.message || err}`, "error");
    // 验证码可能失效，自动尝试刷新验证码
    if (state.eduSessionId) {
      handleEduRefreshCaptcha();
    }
  } finally {
    btn.disabled = false;
    btn.textContent = origText;
  }
}

async function handleEduWebviewImport(previewOnly = false) {
  const url = document.getElementById("eduWebviewUrl").value.trim();
  const cookie = document.getElementById("eduCookieInput").value.trim();
  const html_content = document.getElementById("eduRawHtmlTextarea").value.trim();
  const mode = document.getElementById("eduHtmlImportMode").value || "overwrite";
  const previewBox = document.getElementById("eduParsePreviewResult");

  if (!url && !html_content) {
    showToast("请填写课表页面 URL 或在下方粘贴课表网页 HTML / JSON 内容", "error");
    return;
  }

  const btn = document.getElementById(previewOnly ? "eduPreviewParseBtn" : "eduWebviewImportBtn");
  const origText = btn.textContent;
  btn.disabled = true;
  btn.textContent = "⏳ 正在解析...";

  try {
    const res = await callBridgeApi("edu/fetch_url", "POST", {
      url,
      cookie,
      html_content,
      mode,
      preview_only: previewOnly,
    });
    const data = res?.data && typeof res.data === "object" ? res.data : res;

    if (previewOnly) {
      const list = Array.isArray(data.courses_preview) ? data.courses_preview : [];
      previewBox.style.display = "block";
      if (list.length === 0) {
        previewBox.innerHTML = `⚠️ 未在该内容中检测到课程，请确认粘贴的是包含课表表格的 HTML 源码或正方 JSON。`;
      } else {
        const names = list
          .slice(0, 8)
          .map(
            (c) =>
              `《${escapeHtml(c.name)}》(${WEEKDAY_NAMES[c.day_of_week]} 第${c.start_period}-${c.end_period}节)`
          )
          .join("、");
        previewBox.innerHTML = `✅ 成功识别出 <strong>${list.length}</strong> 门课程：${names}${
          list.length > 8 ? " 等..." : ""
        }`;
      }
    } else {
      const msg = data?.message || "成功解析并导入教务课表！";
      showToast(msg, "success");
      closeModal("eduModal");
      await loadOverview(true);
    }
  } catch (err) {
    showToast(`${previewOnly ? "预解析" : "导入"}失败: ${err.message || err}`, "error");
  } finally {
    btn.disabled = false;
    btn.textContent = origText;
  }
}

// ==================== 学期与作息时间设置 ====================

function openConfigModal() {
  document.getElementById("cfgSemesterName").value = state.config.semester_name || "";
  document.getElementById("cfgStartDate").value = state.config.start_date || "2026-09-07";
  document.getElementById("cfgTotalWeeks").value = String(state.config.total_weeks || 20);
  document.getElementById("cfgEnableReminder").checked = Boolean(state.config.enable_reminder);
  document.getElementById("cfgRemindMinutes").value = String(
    state.config.remind_advance_minutes || 15
  );

  // 深拷贝一份当前 period_times 到草稿
  state.editingPeriodsDraft = JSON.parse(
    JSON.stringify(state.config.period_times || DEFAULT_PERIOD_TIMES)
  );
  renderPeriodsConfigInputs();

  document.getElementById("configModal").style.display = "flex";
}

function renderPeriodsConfigInputs() {
  const container = document.getElementById("periodsConfigList");
  container.innerHTML = "";

  const nums = getSortedPeriodNumbers(state.editingPeriodsDraft);
  for (const p of nums) {
    const item = state.editingPeriodsDraft[String(p)] || { start: "08:00", end: "08:45" };
    const row = document.createElement("div");
    row.className = "period-config-item";
    row.dataset.period = String(p);
    row.innerHTML = `
      <div class="period-config-label">第 ${p} 节</div>
      <input type="time" class="form-input period-start-input" value="${item.start}" required />
      <span>~</span>
      <input type="time" class="form-input period-end-input" value="${item.end}" required />
    `;
    container.appendChild(row);
  }
}

function syncPeriodsDraftFromInputs() {
  const rows = document.querySelectorAll("#periodsConfigList .period-config-item");
  const updated = {};
  rows.forEach((row) => {
    const p = row.dataset.period;
    const start = row.querySelector(".period-start-input")?.value || "08:00";
    const end = row.querySelector(".period-end-input")?.value || "08:45";
    updated[String(p)] = { start, end };
  });
  state.editingPeriodsDraft = updated;
}

async function handleSaveConfig(e) {
  e.preventDefault();
  syncPeriodsDraftFromInputs();

  const semester_name = document.getElementById("cfgSemesterName").value.trim();
  const start_date = document.getElementById("cfgStartDate").value;
  const total_weeks = parseInt(document.getElementById("cfgTotalWeeks").value, 10) || 20;
  const enable_reminder = document.getElementById("cfgEnableReminder").checked;
  const remind_advance_minutes =
    parseInt(document.getElementById("cfgRemindMinutes").value, 10) || 15;

  if (!semester_name || !start_date) {
    showToast("请完整填写学期名称与第一周周一日期", "error");
    return;
  }

  try {
    await callBridgeApi("config/save", "POST", {
      semester_name,
      start_date,
      total_weeks,
      enable_reminder,
      remind_advance_minutes,
      period_times: state.editingPeriodsDraft,
    });
    showToast("学期与作息时间已保存", "success");
    closeModal("configModal");
    await loadOverview(false);
  } catch (err) {
    showToast(`保存配置失败: ${err.message || err}`, "error");
  }
}

// ==================== Tab 2：临时日程与调课管理 ====================

function populateTargetCoursesSelect() {
  const sel = document.getElementById("evtTargetCourse");
  if (!sel) return;
  sel.innerHTML = `<option value="">-- 选择要标记停课的固定课程 --</option>`;

  for (const c of state.courses) {
    const opt = document.createElement("option");
    opt.value = c.id;
    opt.dataset.name = c.name;
    opt.dataset.location = c.location || "";
    opt.textContent = `${c.name} (${WEEKDAY_NAMES[c.day_of_week]} 第${c.start_period}-${c.end_period}节)`;
    sel.appendChild(opt);
  }
}

function calculateWeekFromDateStr(ymdStr) {
  const target = parseDateYMD(ymdStr);
  const start = parseDateYMD(state.config.start_date);
  const diffDays = Math.floor((target.getTime() - start.getTime()) / (1000 * 3600 * 24));
  const weekNum = Math.floor(diffDays / 7) + 1;
  const jsDay = target.getDay();
  const isoDay = jsDay === 0 ? 7 : jsDay;
  return { weekNum, weekdayName: WEEKDAY_NAMES[isoDay] };
}

function renderEventsList() {
  const container = document.getElementById("eventsListContainer");
  const counterEl = document.getElementById("eventsCounter");
  counterEl.textContent = String(state.events.length);

  if (!state.events.length) {
    container.innerHTML = `<div class="empty-state">暂无临时日程或停课调课记录。可在上方表单添加，或直接在聊天中让 AI 帮你记录！</div>`;
    return;
  }

  const sorted = [...state.events].sort((a, b) => {
    const keyA = `${a.date || ""} ${a.start_time || "00:00"}`;
    const keyB = `${b.date || ""} ${b.start_time || "00:00"}`;
    return state.eventsSortDesc ? keyB.localeCompare(keyA) : keyA.localeCompare(keyB);
  });

  container.innerHTML = "";
  for (const evt of sorted) {
    const { weekNum, weekdayName } = calculateWeekFromDateStr(evt.date);
    const isCancel = evt.type === "cancel_course";
    const tagClass = isCancel ? "cancel" : "custom";
    const tagLabel = isCancel ? "🚫 停课标记" : "📌 临时日程";

    const timePart =
      evt.start_time && evt.end_time
        ? `🕒 ${escapeHtml(evt.start_time)} - ${escapeHtml(evt.end_time)}`
        : evt.start_time
        ? `🕒 ${escapeHtml(evt.start_time)}`
        : "";
    const locPart = evt.location ? `📍 ${escapeHtml(evt.location)}` : "";
    const notePart = evt.note ? `💬 ${escapeHtml(evt.note)}` : "";

    const row = document.createElement("div");
    row.className = "event-row";
    row.innerHTML = `
      <div class="event-row-left">
        <div class="event-date-pill">
          ${escapeHtml(evt.date)}
          <span class="event-week-sub">${weekdayName} · 第${weekNum}周</span>
        </div>
        <div class="event-info-main">
          <div class="event-title-line">
            <span class="event-type-tag ${tagClass}">${tagLabel}</span>
            <span class="event-title-text">${escapeHtml(evt.title)}</span>
          </div>
          <div class="event-meta-line">
            ${timePart ? `<span>${timePart}</span>` : ""}
            ${locPart ? `<span>${locPart}</span>` : ""}
            ${notePart ? `<span>${notePart}</span>` : ""}
          </div>
        </div>
      </div>
      <div>
        <button type="button" class="btn btn-danger btn-sm delete-evt-btn" data-id="${escapeHtml(evt.id)}">
          删除
        </button>
      </div>
    `;

    row.querySelector(".delete-evt-btn").addEventListener("click", async () => {
      try {
        await callBridgeApi("events/save", "POST", {
          action: "delete",
          event: { id: evt.id },
        });
        showToast(`已删除记录：${evt.title}`, "success");
        await loadEvents();
      } catch (err) {
        showToast(`删除失败: ${err.message || err}`, "error");
      }
    });

    container.appendChild(row);
  }
}

async function handleQuickEventSubmit(e) {
  e.preventDefault();
  const date = document.getElementById("evtDate").value;
  const type = document.getElementById("evtType").value;
  const target_course_id = document.getElementById("evtTargetCourse").value || null;
  const title = document.getElementById("evtTitle").value.trim();
  const start_time = document.getElementById("evtStartTime").value.trim();
  const end_time = document.getElementById("evtEndTime").value.trim();
  const location = document.getElementById("evtLocation").value.trim();
  const note = document.getElementById("evtNote").value.trim();

  if (!date || !title) {
    showToast("请填写生效日期与标题", "error");
    return;
  }

  try {
    await callBridgeApi("events/save", "POST", {
      action: "save",
      event: {
        date,
        type,
        target_course_id: type === "cancel_course" ? target_course_id : null,
        title,
        start_time,
        end_time,
        location,
        note,
      },
    });
    showToast("日程/调课记录已保存", "success");
    document.getElementById("evtTitle").value = "";
    document.getElementById("evtNote").value = "";
    await loadEvents();
  } catch (err) {
    showToast(`保存日程失败: ${err.message || err}`, "error");
  }
}

// ==================== 模态框与事件绑定 ====================

// ==================== 多份课表档案管理 (Profiles) ====================

function applyProfilesResponse(res) {
  const payload = res?.data && res.data.profiles ? res.data : res;
  if (payload && Array.isArray(payload.profiles)) {
    state.profiles = payload.profiles;
  }
  if (payload && payload.active_profile_id) {
    state.activeProfileId = payload.active_profile_id;
  }
  if (payload && payload.config) {
    state.config = payload.config;
  }
  if (payload && Array.isArray(payload.courses)) {
    state.courses = payload.courses;
  }
  if (payload && Array.isArray(payload.events)) {
    state.events = payload.events;
  }
  if (payload && typeof payload.current_week === "number") {
    state.currentWeek = payload.current_week;
  }
  const totalW = Math.max(1, Number(state.config.total_weeks) || 20);
  state.selectedWeek = Math.min(Math.max(1, state.currentWeek), totalW);

  renderTopbar();
  renderProfilesList();
  renderScheduleGrid();
  renderAllCoursesList();
  populateQuickCoursePeriods();
  populateTargetCoursesSelect();
  renderEventsList();
}

async function handleSwitchProfile(profileId) {
  if (!profileId || profileId === state.activeProfileId) return;
  try {
    const res = await callBridgeApi("profiles", "POST", {
      action: "switch",
      id: profileId,
    });
    const msg = res?.message || res?.data?.message || "已切换课表";
    showToast(msg, "success");
    applyProfilesResponse(res);
  } catch (err) {
    showToast(`切换课表失败: ${err.message || err}`, "error");
  }
}

async function handleCreateProfileSubmit(e) {
  e.preventDefault();
  const name = document.getElementById("newProfileName").value.trim();
  const start_date = document.getElementById("newProfileStartDate").value || "2026-09-07";
  const total_weeks = parseInt(document.getElementById("newProfileTotalWeeks").value, 10) || 20;
  const copy_from_current = document.getElementById("newProfileCopyCurrent").checked;

  if (!name) {
    showToast("请输入新课表的名称", "error");
    return;
  }

  try {
    const res = await callBridgeApi("profiles", "POST", {
      action: "create",
      name,
      semester_name: name,
      start_date,
      total_weeks,
      copy_from_current,
    });
    const msg = res?.message || res?.data?.message || `已新建并切换到课表《${name}》`;
    showToast(msg, "success");
    document.getElementById("newProfileName").value = "";
    document.getElementById("newProfileCopyCurrent").checked = false;
    applyProfilesResponse(res);
  } catch (err) {
    showToast(`新建课表失败: ${err.message || err}`, "error");
  }
}

function renderProfilesList() {
  const container = document.getElementById("profilesListContainer");
  if (!container) return;

  container.innerHTML = "";
  for (const p of state.profiles) {
    const isActive = p.id === state.activeProfileId || p.is_active;
    const row = document.createElement("div");
    row.className = "event-row";
    row.innerHTML = `
      <div class="event-row-left" style="align-items: center;">
        <div class="event-date-pill" style="border-left: 4px solid ${
          isActive ? "var(--primary)" : "var(--border-strong)"
        };">
          ${isActive ? "✅ 当前使用" : "🗂️ 课表档案"}
          <span class="event-week-sub">${p.course_count ?? 0} 门课程</span>
        </div>
        <div class="event-info-main" style="flex: 1;">
          <div class="event-title-line">
            <input
              type="text"
              class="form-input profile-rename-input"
              value="${escapeHtml(p.name)}"
              style="max-width: 220px; font-weight: 700;"
              title="修改课表名称后点击右侧「保存名称」"
            />
            <button type="button" class="btn btn-ghost btn-xs save-profile-name-btn">
              💾 保存名称
            </button>
          </div>
          <div class="event-meta-line">
            <span>学期标识：${escapeHtml(p.semester_name || p.name)}</span>
          </div>
        </div>
      </div>
      <div style="display: flex; gap: 6px; align-items: center;">
        ${
          !isActive
            ? `<button type="button" class="btn btn-primary btn-sm switch-profile-btn">切换使用</button>`
            : `<span class="week-badge">正在使用中</span>`
        }
        ${
          state.profiles.length > 1
            ? `<button type="button" class="btn btn-danger btn-sm delete-profile-btn">🗑️ 删除</button>`
            : ""
        }
      </div>
    `;

    const renameInput = row.querySelector(".profile-rename-input");
    row.querySelector(".save-profile-name-btn").addEventListener("click", async () => {
      const newName = renameInput.value.trim();
      if (!newName) {
        showToast("课表名称不能为空", "error");
        return;
      }
      try {
        const res = await callBridgeApi("profiles", "POST", {
          action: "rename",
          id: p.id,
          name: newName,
        });
        showToast(`已重命名为《${newName}》`, "success");
        applyProfilesResponse(res);
      } catch (err) {
        showToast(`重命名失败: ${err.message || err}`, "error");
      }
    });

    const switchBtn = row.querySelector(".switch-profile-btn");
    if (switchBtn) {
      switchBtn.addEventListener("click", () => handleSwitchProfile(p.id));
    }

    const delBtn = row.querySelector(".delete-profile-btn");
    if (delBtn) {
      delBtn.addEventListener("click", async () => {
        if (delBtn.dataset.confirming !== "true") {
          delBtn.dataset.confirming = "true";
          delBtn.textContent = "⚠️ 确认删除？";
          setTimeout(() => {
            delBtn.dataset.confirming = "false";
            delBtn.textContent = "🗑️ 删除";
          }, 3000);
          return;
        }
        try {
          const res = await callBridgeApi("profiles", "POST", {
            action: "delete",
            id: p.id,
          });
          showToast(`已删除课表《${p.name}》`, "success");
          applyProfilesResponse(res);
        } catch (err) {
          showToast(`删除课表失败: ${err.message || err}`, "error");
        }
      });
    }

    container.appendChild(row);
  }
}

// ==================== 导出课表 (.ics / .json) ====================

async function fetchExportData(profileId = "") {
  const file_type = document.getElementById("exportFormatSelect")?.value || "ics";
  const include_events = document.getElementById("exportIncludeEvents")?.checked ?? true;
  const res = await callBridgeApi("export", "POST", {
    file_type,
    profile_id: profileId || state.activeProfileId,
    include_events,
  });
  return res?.data && res.data.content !== undefined ? res.data : res;
}

function triggerBrowserDownload(filename, content, mime = "text/plain;charset=utf-8") {
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  setTimeout(() => {
    a.remove();
    URL.revokeObjectURL(url);
  }, 200);
}

async function handleGenerateExportPreview() {
  const textarea = document.getElementById("exportPreviewTextarea");
  if (!textarea) return null;
  try {
    textarea.value = "正在生成导出内容...";
    const data = await fetchExportData();
    textarea.value = data.content || "";
    return data;
  } catch (err) {
    textarea.value = "";
    showToast(`生成导出内容失败: ${err.message || err}`, "error");
    return null;
  }
}

async function handleDownloadExportFile() {
  const data = await handleGenerateExportPreview();
  if (!data || !data.content) return;
  triggerBrowserDownload(
    data.filename || "schedule.ics",
    data.content,
    data.mime || "text/plain;charset=utf-8"
  );
  showToast(`已生成并开始下载：${data.filename}`, "success");
}

async function handleCopyExportContent() {
  const textarea = document.getElementById("exportPreviewTextarea");
  if (!textarea.value || textarea.value === "正在生成导出内容...") {
    await handleGenerateExportPreview();
  }
  const text = textarea.value;
  if (!text) return;

  try {
    await navigator.clipboard.writeText(text);
    showToast("导出内容已复制到剪贴板！", "success");
  } catch (_) {
    textarea.select();
    document.execCommand("copy");
    showToast("导出内容已复制到剪贴板！", "success");
  }
}

function closeModal(modalId) {
  const el = document.getElementById(modalId);
  if (el) el.style.display = "none";
}

function bindUIEvents() {
  // 关闭模态框按钮
  document.querySelectorAll("[data-close]").forEach((btn) => {
    btn.addEventListener("click", () => {
      closeModal(btn.dataset.close);
    });
  });

  // Tab 切换
  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
      document.querySelectorAll(".tab-panel").forEach((p) => p.classList.remove("active"));
      btn.classList.add("active");
      const targetTab = document.getElementById(btn.dataset.tab);
      if (targetTab) targetTab.classList.add("active");

      const filterBar = document.getElementById("gridFilterBar");
      if (filterBar) {
        filterBar.style.display = btn.dataset.tab === "tab-grid" ? "block" : "none";
      }
    });
  });

  // 周次切换
  document.getElementById("weekSelect").addEventListener("change", (e) => {
    state.selectedWeek = parseInt(e.target.value, 10) || 1;
    state.slotFocusedCourseId = {};
    renderScheduleGrid();
  });

  document.getElementById("prevWeekBtn").addEventListener("click", () => {
    if (state.selectedWeek > 1) {
      state.selectedWeek -= 1;
      state.slotFocusedCourseId = {};
      document.getElementById("weekSelect").value = String(state.selectedWeek);
      renderScheduleGrid();
    }
  });

  document.getElementById("nextWeekBtn").addEventListener("click", () => {
    const totalW = Math.max(1, Number(state.config.total_weeks) || 20);
    if (state.selectedWeek < totalW) {
      state.selectedWeek += 1;
      state.slotFocusedCourseId = {};
      document.getElementById("weekSelect").value = String(state.selectedWeek);
      renderScheduleGrid();
    }
  });

  document.getElementById("jumpCurrentWeekBtn").addEventListener("click", () => {
    const totalW = Math.max(1, Number(state.config.total_weeks) || 20);
    state.selectedWeek = Math.min(Math.max(1, state.currentWeek), totalW);
    state.slotFocusedCourseId = {};
    document.getElementById("weekSelect").value = String(state.selectedWeek);
    renderScheduleGrid();
  });

  // 非本周课程显示开关
  document.getElementById("showInactiveCoursesToggle").addEventListener("change", (e) => {
    state.showInactiveCourses = e.target.checked;
    renderScheduleGrid();
  });

  // 顶部“是否开启课前主动提醒”快捷开关
  const topReminderToggle = document.getElementById("topReminderToggle");
  if (topReminderToggle) {
    topReminderToggle.addEventListener("change", async (e) => {
      const enable_reminder = e.target.checked;
      state.config.enable_reminder = enable_reminder;
      renderTopbar();
      try {
        await callBridgeApi("config/save", "POST", {
          ...state.config,
          enable_reminder,
        });
        showToast(
          enable_reminder
            ? "已开启课前提醒"
            : "已关闭主动提醒（机器人仅在你询问时回答课表）",
          "success"
        );
      } catch (err) {
        showToast(`切换提醒状态失败: ${err.message || err}`, "error");
      }
    });
  }

  // 顶部快捷按钮
  document.getElementById("profileSelect").addEventListener("change", (e) => {
    handleSwitchProfile(e.target.value);
  });

  document.getElementById("openProfilesModalBtn").addEventListener("click", () => {
    document.getElementById("newProfileStartDate").value =
      state.config.start_date || "2026-09-07";
    document.getElementById("newProfileTotalWeeks").value = String(
      state.config.total_weeks || 20
    );
    renderProfilesList();
    document.getElementById("profilesModal").style.display = "flex";
  });

  document
    .getElementById("createProfileForm")
    .addEventListener("submit", handleCreateProfileSubmit);

  document.getElementById("openAddCourseModalBtn").addEventListener("click", () => {
    openCourseModal(null);
  });

  document.getElementById("openEduModalBtn").addEventListener("click", () => {
    document.getElementById("eduModal").style.display = "flex";
  });

  document.getElementById("openImportModalBtn").addEventListener("click", () => {
    document.getElementById("importModal").style.display = "flex";
  });

  document.getElementById("openExportModalBtn").addEventListener("click", () => {
    document.getElementById("exportModal").style.display = "flex";
    handleGenerateExportPreview();
  });

  document.getElementById("exportFormatSelect").addEventListener("change", () => {
    handleGenerateExportPreview();
  });

  document.getElementById("exportIncludeEvents").addEventListener("change", () => {
    handleGenerateExportPreview();
  });

  document
    .getElementById("generateExportPreviewBtn")
    .addEventListener("click", handleGenerateExportPreview);
  document
    .getElementById("downloadExportFileBtn")
    .addEventListener("click", handleDownloadExportFile);
  document
    .getElementById("copyExportContentBtn")
    .addEventListener("click", handleCopyExportContent);

  document.getElementById("openConfigModalBtn").addEventListener("click", () => {
    openConfigModal();
  });

  document.getElementById("clearAllCoursesBtn").addEventListener("click", () => {
    handleClearAllCourses("clearAllCoursesBtn");
  });

  // Tab 2：全部课程管理 表单与筛选事件
  document.getElementById("quickCourseForm").addEventListener("submit", handleQuickCourseSubmit);
  document
    .getElementById("openFullCourseModalFromTabBtn")
    .addEventListener("click", () => openCourseModal(null));
  document
    .getElementById("addCourseInListBtn")
    .addEventListener("click", () => openCourseModal(null));
  document.getElementById("clearAllCoursesInListBtn").addEventListener("click", () => {
    handleClearAllCourses("clearAllCoursesInListBtn");
  });
  document.getElementById("courseSearchInput").addEventListener("input", renderAllCoursesList);
  document.getElementById("courseDayFilterSelect").addEventListener("change", renderAllCoursesList);

  // 教务处弹窗内子 Tab 切换与按钮事件
  document.querySelectorAll(".edu-subtab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".edu-subtab-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      const targetId = btn.dataset.edutab;
      document.getElementById("edu-tab-login").style.display =
        targetId === "edu-tab-login" ? "flex" : "none";
      document.getElementById("edu-tab-webview").style.display =
        targetId === "edu-tab-webview" ? "flex" : "none";
    });
  });

  document.getElementById("eduConnectBtn").addEventListener("click", handleEduConnect);
  document.getElementById("eduCaptchaImg").addEventListener("click", handleEduRefreshCaptcha);
  document
    .getElementById("eduRefreshCaptchaBtn")
    .addEventListener("click", handleEduRefreshCaptcha);
  document.getElementById("eduLoginImportBtn").addEventListener("click", handleEduLoginImport);

  // 内置网页 / 新窗口打开 / HTML & Cookie 导入
  document.getElementById("eduLoadIframeBtn").addEventListener("click", () => {
    let url = document.getElementById("eduWebviewUrl").value.trim();
    if (!url) {
      showToast("请先输入学校教务处网址", "error");
      return;
    }
    if (!/^https?:\/\//i.test(url)) {
      url = "http://" + url;
      document.getElementById("eduWebviewUrl").value = url;
    }
    const container = document.getElementById("eduIframeContainer");
    const iframe = document.getElementById("eduEmbeddedIframe");
    container.style.display = "block";
    iframe.src = url;
  });

  document.getElementById("eduCloseIframeBtn").addEventListener("click", () => {
    document.getElementById("eduIframeContainer").style.display = "none";
  });

  document.getElementById("eduOpenExternalBtn").addEventListener("click", () => {
    let url = document.getElementById("eduWebviewUrl").value.trim();
    if (!url) {
      showToast("请先输入学校教务处网址", "error");
      return;
    }
    if (!/^https?:\/\//i.test(url)) {
      url = "http://" + url;
      document.getElementById("eduWebviewUrl").value = url;
    }
    window.open(url, "_blank", "noopener,noreferrer");
  });

  document.getElementById("eduPreviewParseBtn").addEventListener("click", () => {
    handleEduWebviewImport(true);
  });

  document.getElementById("eduWebviewImportBtn").addEventListener("click", () => {
    handleEduWebviewImport(false);
  });

  // 课程弹窗内周次快捷选择按钮
  document.getElementById("weeksAllBtn").addEventListener("click", () => {
    document
      .querySelectorAll("#weekPillsContainer .week-pill")
      .forEach((el) => el.classList.add("selected"));
  });

  document.getElementById("weeksOddBtn").addEventListener("click", () => {
    document.querySelectorAll("#weekPillsContainer .week-pill").forEach((el) => {
      const w = parseInt(el.dataset.week, 10);
      el.classList.toggle("selected", w % 2 === 1);
    });
  });

  document.getElementById("weeksEvenBtn").addEventListener("click", () => {
    document.querySelectorAll("#weekPillsContainer .week-pill").forEach((el) => {
      const w = parseInt(el.dataset.week, 10);
      el.classList.toggle("selected", w % 2 === 0);
    });
  });

  document.getElementById("weeksClearBtn").addEventListener("click", () => {
    document
      .querySelectorAll("#weekPillsContainer .week-pill")
      .forEach((el) => el.classList.remove("selected"));
  });

  // 课程保存与删除
  document.getElementById("courseForm").addEventListener("submit", handleSaveCourse);
  document.getElementById("deleteCourseBtn").addEventListener("click", handleDeleteCourse);

  // 本地文件导入 (.ics / .json)
  const fileInput = document.getElementById("hiddenFileInput");
  const dropzone = document.getElementById("fileDropzone");

  document.getElementById("triggerFileSelectBtn").addEventListener("click", (e) => {
    e.stopPropagation();
    fileInput.value = "";
    fileInput.click();
  });

  dropzone.addEventListener("click", () => {
    fileInput.value = "";
    fileInput.click();
  });

  fileInput.addEventListener("change", (e) => {
    const file = e.target.files?.[0];
    if (file) handleLocalFileSelected(file);
  });

  dropzone.addEventListener("dragover", (e) => {
    e.preventDefault();
    dropzone.classList.add("dragover");
  });

  dropzone.addEventListener("dragleave", () => {
    dropzone.classList.remove("dragover");
  });

  dropzone.addEventListener("drop", (e) => {
    e.preventDefault();
    dropzone.classList.remove("dragover");
    const file = e.dataTransfer?.files?.[0];
    if (file) handleLocalFileSelected(file);
  });

  document.getElementById("confirmImportBtn").addEventListener("click", handleConfirmImport);

  // 学期与作息配置弹窗操作
  document.getElementById("addPeriodRowBtn").addEventListener("click", () => {
    syncPeriodsDraftFromInputs();
    const nums = getSortedPeriodNumbers(state.editingPeriodsDraft);
    const nextNum = (nums[nums.length - 1] || 0) + 1;
    state.editingPeriodsDraft[String(nextNum)] = { start: "20:00", end: "20:45" };
    renderPeriodsConfigInputs();
  });

  document.getElementById("removePeriodRowBtn").addEventListener("click", () => {
    syncPeriodsDraftFromInputs();
    const nums = getSortedPeriodNumbers(state.editingPeriodsDraft);
    if (nums.length <= 1) return;
    const lastNum = nums[nums.length - 1];
    delete state.editingPeriodsDraft[String(lastNum)];
    renderPeriodsConfigInputs();
  });

  document.getElementById("configForm").addEventListener("submit", handleSaveConfig);

  // 临时日程与调课表单
  document.getElementById("evtDate").value = formatDateYMD(new Date());

  document.getElementById("evtType").addEventListener("change", (e) => {
    const isCancel = e.target.value === "cancel_course";
    document.getElementById("evtTargetCourseGroup").style.display = isCancel ? "flex" : "none";
    document.getElementById("evtTitleLabel").textContent = isCancel
      ? "停课课程名称 *"
      : "日程标题 *";
  });

  document.getElementById("evtTargetCourse").addEventListener("change", (e) => {
    const opt = e.target.selectedOptions?.[0];
    if (opt && opt.dataset.name) {
      document.getElementById("evtTitle").value = opt.dataset.name;
      if (opt.dataset.location) {
        document.getElementById("evtLocation").value = opt.dataset.location;
      }
    }
  });

  document.getElementById("quickEventForm").addEventListener("submit", handleQuickEventSubmit);

  document.getElementById("toggleEventSortBtn").addEventListener("click", (e) => {
    state.eventsSortDesc = !state.eventsSortDesc;
    e.currentTarget.textContent = state.eventsSortDesc
      ? "⇅ 排序：按日期倒序"
      : "⇅ 排序：按日期正序";
    renderEventsList();
  });

  document.getElementById("refreshEventsBtn").addEventListener("click", () => {
    loadEvents();
  });
}

// ==================== 初始化入口 ====================

async function initApp() {
  bindUIEvents();

  const bridge = await waitForBridge(600);
  if (bridge) {
    try {
      if (typeof bridge.ready === "function") {
        const ctx = await bridge.ready();
        applyThemeFromContext(ctx);
      }
      if (typeof bridge.onContext === "function") {
        bridge.onContext((ctx) => {
          applyThemeFromContext(ctx);
        });
      }
    } catch (err) {
      console.warn("Bridge 初始化警告:", err);
    }
  }

  await Promise.all([loadOverview(false), loadEvents()]);
}

initApp();
