"""AstrBot 定时任务可视化插件 (Cron Dashboard)。

在 AstrBot WebUI 中提供定时任务的图形化看板：
- 任务总览统计与状态分布
- 任务列表（查看/新建/编辑/启停/立即运行/删除）
- 基础任务（basic）内置两种开箱即用的执行器：定时发消息、定时调 HTTP Webhook
- 主动 Agent 任务（active_agent）的创建与运维
- 执行日志持久化到插件数据目录

依赖 AstrBot 核心的 CronJobManager（context.cron_manager），要求 AstrBot >= v4.16。
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiohttp

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star
from astrbot.api.web import error_response, json_response, request
from astrbot.core.db.po import CronJob
from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path

PLUGIN_NAME = "astrbot_plugin_cron_dashboard"
DEFAULT_TIMEZONE = "Asia/Shanghai"

# 内置 basic 任务执行器标识
HANDLER_SEND_MESSAGE = "send_message"
HANDLER_WEBHOOK = "webhook"

# 内置执行器的元数据，供前端表单与文档使用
BASIC_HANDLERS: dict[str, dict[str, Any]] = {
    HANDLER_SEND_MESSAGE: {
        "name": "定时发消息",
        "description": "按照 cron 表达式，定时向指定会话发送一条文本消息。",
        "params": [
            {
                "key": "session",
                "name": "会话 ID（unified_msg_origin）",
                "type": "string",
                "required": True,
                "placeholder": "例如 aiocqhttp:group_message:123456789",
            },
            {
                "key": "message",
                "name": "消息内容",
                "type": "text",
                "required": True,
                "placeholder": "要发送的文本内容",
            },
        ],
    },
    HANDLER_WEBHOOK: {
        "name": "HTTP Webhook",
        "description": "按照 cron 表达式，定时调用一个 HTTP 接口。",
        "params": [
            {"key": "url", "name": "请求 URL", "type": "string", "required": True, "placeholder": "https://example.com/hook"},
            {"key": "method", "name": "请求方法", "type": "select", "options": ["GET", "POST"], "required": False},
            {"key": "body", "name": "请求体（JSON）", "type": "text", "required": False, "placeholder": '{"key": "value"}'},
        ],
    },
}


def _utc_to_local(dt: datetime | None) -> datetime | None:
    """将 (可能 naive 的) UTC 时间转为本地时间，naive 视为 UTC。"""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone()


def _fmt_dt(dt: datetime | None) -> str | None:
    """时间序列化为 'YYYY-MM-DD HH:MM:SS' 本地时间字符串。"""
    local = _utc_to_local(dt)
    return local.strftime("%Y-%m-%d %H:%M:%S") if local else None


class CronDashboardPlugin(Star):
    """AstrBot 定时任务可视化插件主类。"""

    def __init__(self, context: Context, config: AstrBotConfig) -> None:
        super().__init__(context)
        self.config = config

        # 插件数据目录：执行日志等持久化数据存放在 data 目录，避免更新插件时丢失
        self.data_dir = Path(get_astrbot_plugin_data_path()) / PLUGIN_NAME
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.log_file = self.data_dir / "execution_logs.jsonl"
        self._log_buffer: list[dict] = []
        self._log_loaded = False

        # ---- 注册 WebUI 视图使用的后端 API ----
        context.register_web_api(f"/{PLUGIN_NAME}/stats", self.api_stats, ["GET"], "定时任务统计")
        context.register_web_api(f"/{PLUGIN_NAME}/jobs", self.api_list_jobs, ["GET"], "定时任务列表")
        context.register_web_api(f"/{PLUGIN_NAME}/jobs", self.api_create_job, ["POST"], "新建定时任务")
        context.register_web_api(f"/{PLUGIN_NAME}/jobs/<job_id>/update", self.api_update_job, ["POST"], "更新定时任务")
        context.register_web_api(f"/{PLUGIN_NAME}/jobs/<job_id>/delete", self.api_delete_job, ["POST"], "删除定时任务")
        context.register_web_api(f"/{PLUGIN_NAME}/jobs/<job_id>/run", self.api_run_job, ["POST"], "立即运行定时任务")
        context.register_web_api(f"/{PLUGIN_NAME}/jobs/<job_id>/toggle", self.api_toggle_job, ["POST"], "启用/停用定时任务")
        context.register_web_api(f"/{PLUGIN_NAME}/logs", self.api_logs, ["GET"], "任务执行日志")
        context.register_web_api(f"/{PLUGIN_NAME}/handlers", self.api_handlers, ["GET"], "内置任务执行器")

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        """插件激活时：恢复本插件 basic 任务的 handler 绑定。

        AstrBot 核心在启动 / 插件重载后，basic 任务必须重新注册执行器才会被调度。
        """
        cm = getattr(self.context, "cron_manager", None)
        if cm is None:
            self.logger.warning("当前 AstrBot 版本未提供 cron_manager，插件将不可用")
            return
        handlers_store = getattr(cm, "_basic_handlers", None)
        if not isinstance(handlers_store, dict):
            self.logger.warning("无法访问 cron_manager._basic_handlers，basic 任务恢复可能失败")
            return
        try:
            jobs = await cm.list_jobs("basic")
        except Exception as e:  # noqa: BLE001
            self.logger.error(f"读取 basic 任务失败: {e}")
            return
        for job in jobs:
            payload = job.payload or {}
            if payload.get("plugin") != PLUGIN_NAME:
                continue
            handler_type = str(payload.get("handler") or "")
            handler = self._make_handler(handler_type, job.job_id, job.name)
            if handler is None:
                self.logger.warning(f"跳过未知执行器 {handler_type!r} 的任务 {job.name}")
                continue
            handlers_store[job.job_id] = handler
            # 若核心启动时因 handler 缺失跳过了该任务，这里补调度
            if job.enabled and not cm.scheduler.get_job(job.job_id):
                try:
                    cm._schedule_job(job)
                except Exception as e:  # noqa: BLE001
                    self.logger.error(f"重新调度任务 {job.name} 失败: {e}")

    async def terminate(self) -> None:
        """插件卸载时清理。"""
        self._flush_logs()

    # ------------------------------------------------------------------
    # 内置 basic 执行器
    # ------------------------------------------------------------------

    def _make_handler(self, handler_type: str, job_id: str, job_name: str):
        """根据执行器类型生成绑定 job_id 的异步 handler。"""
        if handler_type == HANDLER_SEND_MESSAGE:
            return lambda **payload: self._run_and_log(HANDLER_SEND_MESSAGE, payload, job_id, job_name, self._handle_send_message)
        if handler_type == HANDLER_WEBHOOK:
            return lambda **payload: self._run_and_log(HANDLER_WEBHOOK, payload, job_id, job_name, self._handle_webhook)
        return None

    async def _run_and_log(
        self,
        handler_type: str,
        payload: dict,
        job_id: str,
        job_name: str,
        fn: Any,
    ) -> Any:
        """执行 basic handler 并记录执行日志，异常会继续抛出交给核心记录 last_error。"""
        start = time.monotonic()
        status = "success"
        detail = ""
        try:
            result = fn(**payload)
            if asyncio.iscoroutine(result):
                result = await result
            detail = str(result) if result else ""
            return result
        except Exception as e:  # noqa: BLE001
            status = "failed"
            detail = str(e)
            self.logger.error(f"定时任务 {job_name}({job_id}) 执行失败: {e}")
            raise
        finally:
            self._append_log(
                {
                    "job_id": job_id,
                    "name": job_name,
                    "handler": handler_type,
                    "status": status,
                    "detail": detail,
                    "cost_ms": int((time.monotonic() - start) * 1000),
                    "time": datetime.now(timezone.utc).isoformat(),
                }
            )

    async def _handle_send_message(self, **payload: Any) -> str:
        """定时发消息执行器。"""
        session = str(payload.get("session") or "").strip()
        message = str(payload.get("message") or "").strip()
        if not session:
            raise ValueError("send_message: 缺少 session 参数")
        if not message:
            raise ValueError("send_message: 缺少 message 参数")
        ok = await self.context.send_message(session, MessageChain().message(message))
        if not ok:
            raise RuntimeError(f"send_message: 未找到会话对应的平台: {session}")
        return f"已发送消息到 {session}"

    async def _handle_webhook(self, **payload: Any) -> str:
        """HTTP Webhook 执行器。"""
        url = str(payload.get("url") or "").strip()
        if not url:
            raise ValueError("webhook: 缺少 url 参数")
        method = str(payload.get("method") or "GET").upper()
        body_raw = payload.get("body")
        body_obj = None
        if body_raw is not None and str(body_raw).strip():
            try:
                body_obj = json.loads(str(body_raw))
            except json.JSONDecodeError as e:
                raise ValueError(f"webhook: body 不是合法 JSON: {e}") from e
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as sess:
            async with sess.request(
                method,
                url,
                json=body_obj,
                headers={"Content-Type": "application/json"} if body_obj is not None else {},
            ) as resp:
                text = await resp.text()
                if resp.status >= 400:
                    raise RuntimeError(f"webhook: HTTP {resp.status} {text[:200]}")
                return f"HTTP {resp.status}"

    # ------------------------------------------------------------------
    # 执行日志
    # ------------------------------------------------------------------

    def _load_logs(self) -> None:
        if self._log_loaded:
            return
        self._log_loaded = True
        if not self.log_file.exists():
            return
        try:
            for line in self.log_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    self._log_buffer.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        except OSError as e:
            self.logger.warning(f"读取执行日志失败: {e}")

    def _append_log(self, record: dict) -> None:
        self._load_logs()
        self._log_buffer.append(record)
        max_records = int(self.config.get("log_max_records", 1000) or 1000)
        if len(self._log_buffer) > max_records:
            self._log_buffer = self._log_buffer[-max_records:]

    def _flush_logs(self) -> None:
        if not self._log_buffer:
            return
        try:
            lines = "\n".join(json.dumps(r, ensure_ascii=False) for r in self._log_buffer) + "\n"
            self.log_file.write_text(lines, encoding="utf-8")
        except OSError as e:
            self.logger.warning(f"写入执行日志失败: {e}")

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------

    @property
    def cron_manager(self) -> Any:
        return getattr(self.context, "cron_manager", None)

    def _serialize_job(self, job: CronJob) -> dict:
        cm = self.cron_manager
        live_next = cm.get_next_run_time(job.job_id) if cm is not None else None
        next_run = live_next or job.next_run_time
        return {
            "job_id": job.job_id,
            "name": job.name,
            "description": job.description or "",
            "job_type": job.job_type,
            "cron_expression": job.cron_expression or "",
            "timezone": job.timezone or "",
            "enabled": job.enabled,
            "persistent": job.persistent,
            "run_once": job.run_once,
            "status": job.status,
            "last_run_at": _fmt_dt(job.last_run_at),
            "next_run_time": _fmt_dt(next_run),
            "last_error": job.last_error or "",
            "payload": job.payload or {},
            "created_at": _fmt_dt(job.created_at),
        }

    def _validate_cron(self, cron_expression: str) -> None:
        """校验 5 段式 crontab 表达式。"""
        parts = str(cron_expression or "").split()
        if len(parts) != 5:
            raise ValueError("cron 表达式必须为 5 段式，例如 '0 9 * * *'")

    def _build_payload(self, handler_type: str, params: dict) -> dict:
        """构建 basic 任务 payload，并做基本校验。"""
        meta = BASIC_HANDLERS.get(handler_type)
        if meta is None:
            raise ValueError(f"未知执行器类型: {handler_type}")
        payload: dict[str, Any] = {"plugin": PLUGIN_NAME, "handler": handler_type}
        for p in meta["params"]:
            key = p["key"]
            value = params.get(key)
            if p.get("required") and (value is None or str(value).strip() == ""):
                raise ValueError(f"参数 {p['name']} 为必填项")
            if value is not None:
                payload[key] = value
        return payload

    # ------------------------------------------------------------------
    # Web API
    # ------------------------------------------------------------------

    async def api_stats(self):
        """任务总览统计。"""
        cm = self.cron_manager
        if cm is None:
            return error_response("当前 AstrBot 版本不支持 cron_manager", status_code=500)
        jobs = await cm.list_jobs()
        self._load_logs()
        today = datetime.now().date()
        today_runs = 0
        recent_failed = 0
        for rec in reversed(self._log_buffer):
            try:
                t = datetime.fromisoformat(rec["time"]).astimezone()
            except (ValueError, KeyError):
                continue
            if t.date() == today:
                today_runs += 1
                if rec.get("status") == "failed":
                    recent_failed += 1
        status_counter: dict[str, int] = {}
        for job in jobs:
            status_counter[job.status] = status_counter.get(job.status, 0) + 1
        return json_response(
            {
                "total": len(jobs),
                "enabled": sum(1 for j in jobs if j.enabled),
                "running": status_counter.get("running", 0),
                "failed": status_counter.get("failed", 0),
                "completed": status_counter.get("completed", 0),
                "scheduled": status_counter.get("scheduled", 0),
                "basic": sum(1 for j in jobs if j.job_type == "basic"),
                "active_agent": sum(1 for j in jobs if j.job_type == "active_agent"),
                "today_runs": today_runs,
                "recent_failed": recent_failed,
                "log_total": len(self._log_buffer),
            }
        )

    async def api_list_jobs(self):
        """任务列表。"""
        cm = self.cron_manager
        if cm is None:
            return error_response("当前 AstrBot 版本不支持 cron_manager", status_code=500)
        job_type = request.query.get("job_type")
        jobs = await cm.list_jobs(job_type or None)
        return json_response({"jobs": [self._serialize_job(j) for j in jobs]})

    async def api_handlers(self):
        """内置 basic 执行器元数据。"""
        return json_response({"handlers": BASIC_HANDLERS})

    async def api_logs(self):
        """执行日志（倒序返回最新）。"""
        self._load_logs()
        limit = request.query.get("limit", 50, type=int)
        limit = max(1, min(limit, 500))
        logs = list(reversed(self._log_buffer))[:limit]
        return json_response({"logs": logs})

    async def api_create_job(self):
        """新建定时任务。"""
        cm = self.cron_manager
        if cm is None:
            return error_response("当前 AstrBot 版本不支持 cron_manager", status_code=500)
        payload = await request.json(default={})
        name = str(payload.get("name") or "").strip()
        if not name:
            return error_response("任务名称不能为空", status_code=400)
        job_type = str(payload.get("job_type") or "basic")
        if job_type not in ("basic", "active_agent"):
            return error_response("job_type 只能是 basic 或 active_agent", status_code=400)
        cron_expression = str(payload.get("cron_expression") or "").strip()
        run_once = bool(payload.get("run_once", False))
        if not run_once:
            try:
                self._validate_cron(cron_expression)
            except ValueError as e:
                return error_response(str(e), status_code=400)
        timezone_ = str(payload.get("timezone") or "").strip() or DEFAULT_TIMEZONE
        description = str(payload.get("description") or "").strip() or None
        enabled = bool(payload.get("enabled", True))
        try:
            if job_type == "basic":
                handler_type = str(payload.get("handler") or "")
                job_payload = self._build_payload(handler_type, payload.get("params") or {})
                job = await cm.add_basic_job(
                    name=name,
                    cron_expression=cron_expression,
                    handler=self._make_handler(handler_type, "", name),
                    description=description,
                    timezone=timezone_,
                    payload=job_payload,
                    enabled=enabled,
                    persistent=True,
                )
                # 重新绑定携带 job_id 的 handler，便于记录执行日志
                handlers_store = getattr(cm, "_basic_handlers", None)
                if isinstance(handlers_store, dict):
                    handlers_store[job.job_id] = self._make_handler(handler_type, job.job_id, job.name)
            else:
                job = await cm.add_active_job(
                    name=name,
                    cron_expression=cron_expression if not run_once else None,
                    payload=payload.get("params") or {},
                    description=description,
                    timezone=timezone_,
                    enabled=enabled,
                    persistent=True,
                    run_once=run_once,
                    run_at=None,
                )
        except Exception as e:  # noqa: BLE001
            self.logger.error(f"创建定时任务失败: {e}")
            return error_response(f"创建失败: {e}", status_code=400)
        return json_response({"job": self._serialize_job(job)})

    async def api_update_job(self, job_id: str):
        """更新定时任务（传入字段即更新，未传字段保持不变）。"""
        cm = self.cron_manager
        if cm is None:
            return error_response("当前 AstrBot 版本不支持 cron_manager", status_code=500)
        body = await request.json(default={})
        updates: dict[str, Any] = {}
        if "name" in body:
            updates["name"] = str(body["name"]).strip()
        if "description" in body:
            updates["description"] = str(body["description"]).strip() or None
        if "cron_expression" in body:
            expr = str(body["cron_expression"]).strip()
            try:
                self._validate_cron(expr)
            except ValueError as e:
                return error_response(str(e), status_code=400)
            updates["cron_expression"] = expr
        if "timezone" in body:
            updates["timezone"] = str(body["timezone"]).strip() or None
        if "enabled" in body:
            updates["enabled"] = bool(body["enabled"])
        # basic 任务的参数更新：以 params 整体替换 payload 的业务字段
        if "params" in body:
            current = await cm.list_jobs()
            current_job = next((j for j in current if j.job_id == job_id), None)
            if current_job is None:
                return error_response("任务不存在", status_code=404)
            if current_job.job_type == "basic":
                handler_type = str((current_job.payload or {}).get("handler") or "")
                updates["payload"] = self._build_payload(handler_type, body.get("params") or {})
            else:
                updates["payload"] = body.get("params") or {}
        try:
            job = await cm.update_job(job_id, **updates)
        except Exception as e:  # noqa: BLE001
            self.logger.error(f"更新定时任务 {job_id} 失败: {e}")
            return error_response(f"更新失败: {e}", status_code=400)
        if job is None:
            return error_response("任务不存在", status_code=404)
        # 更新后重新绑定 handler（update_job 会移除并重新调度）
        if job.job_type == "basic":
            handler_type = str((job.payload or {}).get("handler") or "")
            handlers_store = getattr(cm, "_basic_handlers", None)
            if isinstance(handlers_store, dict):
                handlers_store[job.job_id] = self._make_handler(handler_type, job.job_id, job.name)
        return json_response({"job": self._serialize_job(job)})

    async def api_delete_job(self, job_id: str):
        """删除定时任务。"""
        cm = self.cron_manager
        if cm is None:
            return error_response("当前 AstrBot 版本不支持 cron_manager", status_code=500)
        try:
            await cm.delete_job(job_id)
        except Exception as e:  # noqa: BLE001
            self.logger.error(f"删除定时任务 {job_id} 失败: {e}")
            return error_response(f"删除失败: {e}", status_code=400)
        return json_response({"deleted": True})

    async def api_run_job(self, job_id: str):
        """立即运行指定任务。"""
        cm = self.cron_manager
        if cm is None:
            return error_response("当前 AstrBot 版本不支持 cron_manager", status_code=500)
        try:
            await cm.run_job_now(job_id)
        except Exception as e:  # noqa: BLE001
            self.logger.error(f"立即运行任务 {job_id} 失败: {e}")
            return error_response(f"运行失败: {e}", status_code=400)
        return json_response({"started": True})

    async def api_toggle_job(self, job_id: str):
        """启用 / 停用指定任务。"""
        cm = self.cron_manager
        if cm is None:
            return error_response("当前 AstrBot 版本不支持 cron_manager", status_code=500)
        jobs = await cm.list_jobs()
        job = next((j for j in jobs if j.job_id == job_id), None)
        if job is None:
            return error_response("任务不存在", status_code=404)
        try:
            updated = await cm.update_job(job_id, enabled=not job.enabled)
        except Exception as e:  # noqa: BLE001
            self.logger.error(f"切换任务 {job_id} 状态失败: {e}")
            return error_response(f"操作失败: {e}", status_code=400)
        if updated is None:
            return error_response("任务不存在", status_code=404)
        if updated.job_type == "basic":
            handler_type = str((updated.payload or {}).get("handler") or "")
            handlers_store = getattr(cm, "_basic_handlers", None)
            if isinstance(handlers_store, dict):
                handlers_store[updated.job_id] = self._make_handler(handler_type, updated.job_id, updated.name)
        return json_response({"enabled": updated.enabled})

    # ------------------------------------------------------------------
    # 聊天指令
    # ------------------------------------------------------------------

    @filter.command_group("cron")
    def cron_group(self):
        """定时任务管理指令组。"""

    @cron_group.command("list")
    async def cron_list(self, event: AstrMessageEvent):
        """查看所有定时任务。"""
        cm = self.cron_manager
        if cm is None:
            yield event.plain_result("当前 AstrBot 版本不支持 cron_manager")
            return
        jobs = await cm.list_jobs()
        if not jobs:
            yield event.plain_result("当前没有任何定时任务。")
            return
        lines = [f"共 {len(jobs)} 个定时任务："]
        for job in jobs:
            status = "✅" if job.enabled else "⏸️"
            lines.append(
                f"{status} [{job.job_type}] {job.name} | {job.cron_expression or '一次性'} | "
                f"状态: {job.status} | 下次: {_fmt_dt(job.next_run_time) or '—'} | {job.job_id[:8]}"
            )
        yield event.plain_result("\n".join(lines))

    @cron_group.command("stats")
    async def cron_stats(self, event: AstrMessageEvent):
        """查看任务统计。"""
        cm = self.cron_manager
        if cm is None:
            yield event.plain_result("当前 AstrBot 版本不支持 cron_manager")
            return
        jobs = await cm.list_jobs()
        enabled = sum(1 for j in jobs if j.enabled)
        failed = sum(1 for j in jobs if j.status == "failed")
        basic = sum(1 for j in jobs if j.job_type == "basic")
        agent = sum(1 for j in jobs if j.job_type == "active_agent")
        yield event.plain_result(
            "📊 定时任务统计\n"
            f"总数: {len(jobs)} | 启用: {enabled} | 停用: {len(jobs) - enabled}\n"
            f"basic: {basic} | active_agent: {agent}\n"
            f"失败: {failed}"
        )

    @cron_group.command("run")
    async def cron_run(self, event: AstrMessageEvent, job_id: str):
        """立即运行任务。用法：/cron run <任务ID>"""
        cm = self.cron_manager
        if cm is None:
            yield event.plain_result("当前 AstrBot 版本不支持 cron_manager")
            return
        jobs = await cm.list_jobs()
        job = next((j for j in jobs if j.job_id.startswith(job_id)), None)
        if job is None:
            yield event.plain_result(f"未找到任务: {job_id}")
            return
        try:
            await cm.run_job_now(job.job_id)
            yield event.plain_result(f"已触发任务「{job.name}」立即运行。")
        except Exception as e:  # noqa: BLE001
            yield event.plain_result(f"触发失败: {e}")

    @cron_group.command("toggle")
    async def cron_toggle(self, event: AstrMessageEvent, job_id: str):
        """启用/停用任务。用法：/cron toggle <任务ID>"""
        cm = self.cron_manager
        if cm is None:
            yield event.plain_result("当前 AstrBot 版本不支持 cron_manager")
            return
        jobs = await cm.list_jobs()
        job = next((j for j in jobs if j.job_id.startswith(job_id)), None)
        if job is None:
            yield event.plain_result(f"未找到任务: {job_id}")
            return
        try:
            updated = await cm.update_job(job.job_id, enabled=not job.enabled)
        except Exception as e:  # noqa: BLE001
            yield event.plain_result(f"操作失败: {e}")
            return
        if updated is None:
            yield event.plain_result(f"未找到任务: {job_id}")
            return
        if updated.job_type == "basic":
            handler_type = str((updated.payload or {}).get("handler") or "")
            handlers_store = getattr(cm, "_basic_handlers", None)
            if isinstance(handlers_store, dict):
                handlers_store[updated.job_id] = self._make_handler(handler_type, updated.job_id, updated.name)
        yield event.plain_result(f"任务「{updated.name}」已{'启用' if updated.enabled else '停用'}。")

    @cron_group.command("delete")
    async def cron_delete(self, event: AstrMessageEvent, job_id: str):
        """删除任务。用法：/cron delete <任务ID>"""
        cm = self.cron_manager
        if cm is None:
            yield event.plain_result("当前 AstrBot 版本不支持 cron_manager")
            return
        jobs = await cm.list_jobs()
        job = next((j for j in jobs if j.job_id.startswith(job_id)), None)
        if job is None:
            yield event.plain_result(f"未找到任务: {job_id}")
            return
        try:
            await cm.delete_job(job.job_id)
            yield event.plain_result(f"已删除任务「{job.name}」。")
        except Exception as e:  # noqa: BLE001
            yield event.plain_result(f"删除失败: {e}")
