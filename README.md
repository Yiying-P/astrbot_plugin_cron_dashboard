# astrbot_plugin_cron_dashboard

AstrBot 定时任务可视化插件（Cron Dashboard）。

在 AstrBot WebUI 中以图形化看板的方式查看和管理全部定时任务：任务统计、状态分布、今日执行概览、任务列表（新建 / 编辑 / 启停 / 立即运行 / 删除）与执行日志，一屏搞定。

> 依赖 AstrBot 核心的 `CronJobManager`（`context.cron_manager`），要求 **AstrBot >= v4.16**。

## 功能特性

- 📊 **总览统计**：任务总数、启用中、basic / AI 主动任务数量、运行中与失败数、今日执行次数
- 🍩 **状态分布环形图**：scheduled / running / completed / failed 一目了然
- 📈 **今日执行概览**：按小时统计 basic 任务执行情况（含失败高亮）
- 📋 **任务列表**：类型、触发器（cron 友好描述）、状态、上次/下次运行时间，支持搜索与筛选
- ⚙️ **任务管理**：新建、编辑、启用/停用、立即运行、删除（均通过 WebUI 或聊天指令完成）
- 🚀 **开箱即用的 basic 执行器**：
  - `send_message`：定时向指定会话发送文本消息
  - `webhook`：定时调用 HTTP 接口（GET / POST）
- 🤖 **active_agent 任务**：创建并运维 AstrBot 的 AI 主动任务
- 📝 **执行日志**：basic 任务每次执行的成功/失败、耗时与详情，持久化于插件数据目录
- 💬 **聊天指令**：`/cron list`、`/cron stats`、`/cron run`、`/cron toggle`、`/cron delete`

## 安装

### 方式一：AstrBot 插件市场

在 AstrBot WebUI 的「扩展功能 → 插件」中搜索 `cron_dashboard` 安装即可。

### 方式二：手动安装

```bash
# 进入 AstrBot 的 data/plugins 目录
cd <AstrBot 目录>/data/plugins
git clone https://github.com/Yiying-P/astrbot_plugin_cron_dashboard
```

安装后在 WebUI 的「插件」页面启用插件，然后重启 AstrBot 或重载插件。

## 使用

1. 启用插件后，在插件详情页打开 **「定时任务看板」** 视图。
2. 点击右上角 **「＋ 新建任务」** 创建任务：
   - **basic（内置执行器）**：选择执行器并填写参数
     - `send_message`：`session`（unified_msg_origin，如 `aiocqhttp:group_message:123456789`）与 `message`
     - `webhook`：`url`、`method`、`body`（可选 JSON）
   - **active_agent（AI 主动任务）**：在 JSON 参数中填写 `session`（结果回传会话）与 `note`（任务说明）
   - Cron 表达式为 **5 段式**：`分 时 日 月 周`，例如 `0 9 * * *` 表示每天 09:00
3. 在任务列表中可随时 **启停 / 编辑 / 立即运行 / 删除** 任务。

### 聊天指令

| 指令 | 说明 |
| --- | --- |
| `/cron list` | 列出所有定时任务 |
| `/cron stats` | 查看任务统计 |
| `/cron run <任务ID前缀>` | 立即运行任务 |
| `/cron toggle <任务ID前缀>` | 启用 / 停用任务 |
| `/cron delete <任务ID前缀>` | 删除任务 |

> 指令支持使用任务 ID 前 8 位进行匹配，无需完整 ID。

## 配置项（`_conf_schema.json`）

| 配置项 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `log_max_records` | int | 1000 | 执行日志在内存中保留的最大条数 |
| `default_timezone` | string | `Asia/Shanghai` | 新建任务默认时区 |

## 数据存储

- 定时任务本体由 AstrBot 核心数据库管理（`data/astrbot.db`），无需插件额外存储。
- 执行日志存储在 `data/plugins_global/astrbot_plugin_cron_dashboard/execution_logs.jsonl`（AstrBot 插件数据目录），卸载/更新插件不会丢失。

## 后端 Web API（插件视图内部使用）

| 方法 | 路由 | 说明 |
| --- | --- | --- |
| GET | `/astrbot_plugin_cron_dashboard/stats` | 任务统计 |
| GET | `/astrbot_plugin_cron_dashboard/jobs` | 任务列表 |
| POST | `/astrbot_plugin_cron_dashboard/jobs` | 新建任务 |
| POST | `/astrbot_plugin_cron_dashboard/jobs/<job_id>/update` | 更新任务 |
| POST | `/astrbot_plugin_cron_dashboard/jobs/<job_id>/delete` | 删除任务 |
| POST | `/astrbot_plugin_cron_dashboard/jobs/<job_id>/run` | 立即运行 |
| POST | `/astrbot_plugin_cron_dashboard/jobs/<job_id>/toggle` | 启用 / 停用 |
| GET | `/astrbot_plugin_cron_dashboard/logs` | 执行日志 |
| GET | `/astrbot_plugin_cron_dashboard/handlers` | 内置执行器元数据 |

## 开发

```bash
# 将插件 clone 到 AstrBot 的 data/plugins 目录后
# 修改代码，然后在 WebUI 插件页点击「重载插件」即可热更新
```

提交前请使用 [ruff](https://github.com/astral-sh/ruff) 格式化代码。

## 致谢

- 插件开发参考了 [AstrBot 插件开发指南](https://docs.astrbot.app/dev/star/plugin.html) 与 [Plugin Views](https://docs.astrbot.app/en/dev/star/guides/plugin-pages.html) 文档。

## 开源协议

[AGPL-3.0](./LICENSE)
