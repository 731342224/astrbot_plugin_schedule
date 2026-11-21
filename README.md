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

3. **🎓 多种课表一键导入与导出**
   - **教务处登录直连导入**：支持新版正方教务 (`/jwglxt`)、强智科技教务 (`/jsxsd`)、旧版正方、金智/树维/URP 等系统（支持自动拉取图形验证码、内置网页打开、Cookie 免密抓取、网页 HTML 源码/WakeUp 文本一键解析）。
   - **文件导入 (`.ics` / `.json` / `.html`)**：支持展开 iCalendar `RRULE` 重复规则并结合学期作息反推周次与节次，兼容 WakeUp 课程表、小爱课程表及教务处导出文件。
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
├── main.py                           # 插件主入口、@llm_tool 工具与 Web API 路由
├── core/
│   ├── models.py                     # SemesterConfig / Course / ScheduleEvent 数据模型
│   ├── storage.py                    # 多课表档案与 JSON 线程安全持久化存储
│   ├── parser.py                     # .ics / .json 课表解析器与导出器
│   ├── edu_scraper.py                # 教务处登录会话、验证码与 HTML 表格解析引擎
│   └── scheduler_engine.py           # 教学周/单双周推算与自然语言摘要引擎
└── views/
    └── schedule-manager/
        ├── index.html                # WebUI 侧边栏可视化管理入口
        ├── style.css                 # 亮/暗主题自适应样式表
        └── app.js                    # 基于 AstrBotPluginView Bridge SDK 的前端逻辑
```
