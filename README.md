# 📅 智能课表与日程管家 (`astrbot_plugin_schedule`)

适用于 **AstrBot (>= 4.16)** 的智能课表与日程管理插件。告别静态课表截图，让 AI 自主推算当前教学周与单双周，查询每日课程、记录动态调课/停课/临时日程，并提供强大的 **WebUI 侧边栏可视化课表管理面板**。

---

## ✨ 核心功能亮点

1. **🤖 AI 大模型工具深度集成 (`@llm_tool`)**
   - **`query_schedule`**：支持查询今天 (`today`)、明天 (`tomorrow`)、后天 (`day_after_tomorrow`)、任意日期 (`YYYY-MM-DD`)、全部固定课程 (`all`) 或已保存的多份课表列表 (`profiles`)。自动推算教学周与单双周，自动剔除当日已标记停课的课程并合并临时日程。
   - **`manage_schedule_event`**：支持在聊天中直接让 AI 新增临时日程 (`add_event`)、标记某门课当日停课 (`cancel_course`)、删除日程 (`delete_event`)、永久删除某门课 (`delete_course`)、清空课表 (`clear_all_courses`) 或切换/新建多份课表 (`switch_profile` / `create_profile`)。
   - **可选上下文自动注入 & 主动提醒开关**：支持配置是否自动向对话上下文注入今日课表，以及是否开启课前主动提醒（可设置为“仅询问时回答，平时不打扰”）。

2. **🗓️ WebUI 侧边栏可视化管理 (`views/schedule-manager/`)**
   - **7 列周课表网格**：自动高亮今日所在列，支持第 1~N 周自由切换，同时间段多门不同周次课程采用 **「主卡 + 底部折叠抽屉条」** 设计，点击抽屉条或右上角 `⇄ 1/2` 即可丝滑切换。
   - **📚 全部课程聚合管理**：同名不同周次/不同星期的课程自动聚合为一张卡片，直观展示所有时段与周次，支持单时段编辑/移除、一键为该课增加新时段、整门删除、搜索筛选与一键清空课表。
   - **🗂️ 多份课表档案支持**：可创建并随时切换多份独立课表（如：大二上学期、大二下学期、室友课表、考研时间表）。

3. **📥 标准课表文件导入与导出**
   - **文件导入 (`.ics` / `.json`)**：支持展开 iCalendar `RRULE` 重复规则并结合学期作息反推周次与节次，完美兼容 WakeUp 课程表、小爱课程表及各类导出的 `.ics` / `.json` 文件。
   - **课表导出 (`.ics` / `.json`)**：支持将课表与临时日程导出为标准 `.ics` 日历文件（可直接导入 iPhone/iPad/安卓系统日历、Outlook、Google Calendar）或 `.json` 完整备份包。

---

## 📦 安装方式

### 方式一：通过 AstrBot WebUI 上传压缩包安装
1. 下载仓库打包好的 `astrbot_plugin_schedule.zip`。
2. 打开 AstrBot WebUI -> **插件管理** -> **上传插件**，选择 `.zip` 文件即可自动安装依赖并加载。

### 方式二：通过 GitHub 仓库地址安装 / Git Clone
在 AstrBot WebUI 插件市场填入本仓库地址，或在 `AstrBot/data/plugins/` 目录下执行：
```bash
git clone https://github.com/731342224/astrbot_plugin_schedule.git
pip install -r astrbot_plugin_schedule/requirements.txt
```

---

## 📁 目录结构

```text
astrbot_plugin_schedule/
├── metadata.yaml                     # 插件元数据与 views 视图声明
├── _conf_schema.json                 # 插件配置 Schema（上下文注入与提醒开关）
├── requirements.txt                  # Python 第三方依赖声明
├── CHANGELOG.md                      # 完整版本更新日志
├── main.py                           # 插件主入口、@llm_tool 工具与 Web API 路由
├── core/
│   ├── models.py                     # SemesterConfig / Course / ScheduleEvent 数据模型
│   ├── storage.py                    # 多课表档案与 JSON 线程安全持久化存储
│   ├── parser.py                     # .ics / .json 课表解析器与导出器
│   └── scheduler_engine.py           # 教学周/单双周推算与自然语言摘要引擎
└── views/
    └── schedule-manager/
        ├── index.html                # WebUI 侧边栏可视化管理入口
        ├── style.css                 # 亮/暗主题自适应样式表
        └── app.js                    # 基于 AstrBotPluginView Bridge SDK 的前端逻辑
```

---

## 📑 最近更新日志（完整历史见 [CHANGELOG.md](./CHANGELOG.md)）

- **v1.9.2**：移除周课表网格右上角的「显示非本周课程」开关，周课表网格始终仅展示当前所选教学周真正要上的课程。
- **v1.9.1**：将 `@filter.on_llm_request()` 中的动态今日课表注入方式由 `req.system_prompt` 升级为 `req.extra_user_content_parts` + `.mark_as_temp()`（保护大模型服务端 Prompt 缓存命中率，符合官方插件市场上架规范），并将 `auto_inject_today_context` 默认设为按需工具查询模式 (`false`)。
- **v1.9.0**：移除教务处登录导入模块及相关第三方依赖（`httpx`、`beautifulsoup4`、`pycryptodome`），仅保留干净轻量的 `.ics` / `.json` 文件导入与导出。
- **v1.8.2**：默认隐藏周课表网格中的非本周课程；新增 `this_week` / `next_week` AI 查询模式；优化单双周与分段周次描述并显式标注 `（本周有课/无课）`，彻底避免 AI 算错周次。
- **v1.8.1**：统一顶部课表下拉框与课程徽章按同名去重后的实际课程门数（如 `16 门课程 (27 时段)`）统计。
- **v1.8.0**：修复 WakeUp `.ics` 导入的教师与上课教室错位/重复问题，并支持对已入库 `courses.json` 自动纠偏落盘。
- **v1.7.0**：「📚 全部课程管理」升级为同名课程多时段聚合卡片，支持内嵌查看所有周次时段、一键为该课增加时段、单时段移除与整门删除。
- **v1.6.0**：周课表网格同时间段多门不同周次课程采用「主卡 + 底部折叠抽屉条」切换设计。
- **v1.5.0**：新增课表导出功能（支持导出 `.ics` 标准日历与 `.json` 备份包）。
- **v1.4.0**：新增多份课表档案管理与切换（WebUI 顶部下拉秒切 + AI 对话跨课表查询/切换）。
- **v1.3.0**：新增「📚 全部课程管理」Tab 页与「🗑️ 清空课表」功能。
- **v1.2.0**：新增「🎓 教务处自动登录与课表一键导入」（支持正方/强智/通用教务、验证码拉取、内置网页与 HTML/WakeUp 解析）。
- **v1.1.0**：新增「是否开启课前主动提醒」开关，修复 Bridge SDK 注入与同源请求回退。
- **v1.0.0**：初始版本发布。
