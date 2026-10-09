from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .config import ROOT, load_settings
from .clustering import cluster_keywords
from .crawl import CrawlError, crawl_site, normalize_url
from .intake import import_records
from .intents import generate_intents
from .seed_keywords import generate_seed_keywords, seed_facts
from .store import Store, encode, now, uid
from .understanding import Understanding, generate_understanding, validate_evidence


class ProjectInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    site_url: str = Field(max_length=2000)


class SourceInput(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=20, max_length=100000)


class JobInput(BaseModel):
    kind: Literal["discover", "crawl", "understand", "analyze", "intents", "clusters", "seed_keywords"]
    profile_version: int | None = Field(default=None, ge=1)
    crawl_max_pages: int | None = Field(default=None, ge=1, strict=True)
    source_ids: list[str] | None = None
    demand_ids: list[str] | None = None


class ProfileInput(BaseModel):
    payload: Understanding
    expected_version: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=1000)


class ConfirmInput(BaseModel):
    expected_version: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=1000)


class ReviewInput(BaseModel):
    expected_version: int = Field(ge=1)
    fact_index: int = Field(ge=0)
    decision: Literal["confirmed", "pending"]
    statement: str | None = Field(default=None, min_length=1, max_length=3000)
    scope: str | None = Field(default=None, max_length=2000)
    reason: str = Field(min_length=1, max_length=1000)


class IntakeInput(BaseModel):
    kind: Literal["keyword", "customer_question", "product_change"] = "keyword"
    format: Literal["lines", "csv", "xlsx"] = "lines"
    text: str = Field(min_length=1)
    market: str | None = Field(default=None, max_length=100)
    language: str | None = Field(default=None, max_length=100)
    request_id: str = Field(min_length=10, max_length=100)


class KeywordCompletionInput(BaseModel):
    run_id: str = Field(min_length=1)
    selected_group_ids: list[str]


class SeedKeywordEditInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    expected_revision: int = Field(ge=0, strict=True)
    keyword: str = Field(min_length=1, max_length=120, pattern=r"^[^&/,\n\r]+$")
    category: str = Field(min_length=1, max_length=80)
    kind: Literal["product", "service"]


def create_app(settings=None, store=None):
    settings = settings or load_settings()
    store = store or Store(ROOT / settings.database_path)

    @asynccontextmanager
    async def lifespan(app):
        store.recover_jobs()
        app.state.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="pagggle")
        yield
        app.state.executor.shutdown(wait=True, cancel_futures=True)

    app = FastAPI(title="pagggle", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])

    @app.middleware("http")
    async def local_boundary(request: Request, call_next):
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            origin = request.headers.get("origin")
            if origin and origin != str(request.base_url).rstrip("/"):
                return JSONResponse({"detail": "不接受跨站写入请求"}, status_code=403)
            if request.headers.get("content-type", "").split(";")[0] != "application/json":
                return JSONResponse({"detail": "仅接受 JSON 请求"}, status_code=415)
            path = request.url.path.strip("/").split("/")
            intake = request.method == "POST" and len(path) == 5 and path[:2] == ["api", "projects"] and path[3] == "demands" and path[4] in {"preview", "import"}
            if not intake and len(await request.body()) > 3000000:
                return JSONResponse({"detail": "资料过大，请拆分补充"}, status_code=413)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid_input(request, error):
        return JSONResponse({"detail": "输入格式不正确，请检查必填项、字段长度和版本号"}, status_code=422)

    @app.exception_handler(LookupError)
    async def missing(request, error):
        return JSONResponse({"detail": "项目或记录不存在"}, status_code=404)

    @app.exception_handler(ValueError)
    async def conflict(request, error):
        return JSONResponse({"detail": str(error)}, status_code=409)

    @app.exception_handler(CrawlError)
    async def bad_url(request, error):
        return JSONResponse({"detail": str(error)}, status_code=422)

    def work(project_id, job_id, kind, expected_version, job_settings, selected_sources, keyword_records=None):
        store.update_job(project_id, job_id, status="running", progress="开始执行")
        if store.cancelled(project_id, job_id):
            return
        try:
            if kind == "seed_keywords":
                profile = store.profiles(project_id)[0]
                if profile["version"] != expected_version:
                    raise ValueError("业务理解已有新版本，请刷新后重新提取基础词")
                store.update_job(project_id, job_id, progress="从已有产品与服务依据中提取行业基础词")
                payload, usage = generate_seed_keywords(job_settings, profile, store.sources(project_id), store.project(project_id)["site_url"],
                    cancelled=lambda: store.cancelled(project_id, job_id))
                store.save_seed_keywords(project_id, job_id, expected_version, payload, settings.model, usage)
                store.update_job(project_id, job_id, status="succeeded", progress=f"已提取 {len(payload['keywords'])} 个搜索基础词，请核对分类与依据")
                return
            if kind == "clusters":
                payload = cluster_keywords(job_settings, keyword_records,
                    cancelled=lambda: store.cancelled(project_id, job_id),
                    progress=lambda message: store.update_job(project_id, job_id, progress=message))
                store.save_clusters(project_id, job_id, payload)
                store.update_job(project_id, job_id, status="succeeded", progress=f"聚类完成：{payload['summary']['keywords']} 个关键词，{payload['summary']['groups']} 个候选组；等待审核")
                return
            if kind == "intents":
                profile = store.profiles(project_id)[0]
                if profile["version"] != expected_version or profile["status"] != "confirmed":
                    raise ValueError("业务理解已变化，请核对最新版本后重试")
                records = store.pending_intents(project_id)
                failures = 0
                for offset in range(0, len(records), settings.intent_batch_size):
                    if store.cancelled(project_id, job_id):
                        return
                    batch = records[offset:offset + settings.intent_batch_size]
                    store.update_job(project_id, job_id, progress=f"识别需求 {offset + 1}–{offset + len(batch)} / {len(records)}")
                    batch_error = None
                    try:
                        results, usage = generate_intents(settings, batch, profile)
                    except ValueError as error:
                        batch_error = str(error)
                        results = [{"input_id": r["id"], "status": "failed", "payload": None, "error": str(error)} for r in batch]
                        usage = None
                    store.save_intents(project_id, job_id, expected_version, results, settings.model, usage)
                    if batch_error:
                        raise ValueError(batch_error)
                    failures += sum(r["status"] == "failed" for r in results)
                store.update_job(project_id, job_id, status="failed" if failures else "succeeded", progress="意图识别完成，请查看候选解释", error=f"{failures} 条未通过校验；重试只处理失败及尚未分析的输入" if failures else None)
                return
            if kind in {"discover", "crawl"}:
                outcome = crawl_site(store, project_id, job_settings,
                    lambda: store.cancelled(project_id, job_id),
                    lambda message: store.update_job(project_id, job_id, progress=message),
                    [s["url"] for s in selected_sources] if selected_sources is not None else None,
                    kind == "discover")
                if store.cancelled(project_id, job_id):
                    return
                if outcome and outcome.get("error"):
                    raise ValueError(outcome["error"])
                if kind == "discover":
                    if not outcome or not outcome["sitemaps_read"]:
                        raise ValueError("未成功读取站点地图，请查看发现资料中的失败原因；尚未采集网页正文")
                    store.update_job(project_id, job_id, status="succeeded")
                    return
                selected_ids = {s["id"] for s in selected_sources}
                if not any(s["id"] in selected_ids and s["status"] == "read" for s in store.sources(project_id)):
                    raise ValueError("未成功读取网站正文，失败原因已记录；可重试或补充文本资料")
                store.update_job(project_id, job_id, status="succeeded", progress="选中页面采集完成，请先核对采集结果，再手动生成业务理解")
                return
            if not store.cancelled(project_id, job_id):
                store.update_job(project_id, job_id, progress="依据本项目资料提取画像并检查引用")
                sources = store.sources(project_id)
                if selected_sources is not None:
                    selected_ids = {s["id"] for s in selected_sources}
                    sources = [s for s in sources if s["id"] in selected_ids]
                payload, usage = generate_understanding(settings, sources,
                    progress=lambda message: store.update_job(project_id, job_id, progress=message),
                    cancelled=lambda: store.cancelled(project_id, job_id))
                with store.connect() as db:
                    db.execute("INSERT INTO model_runs VALUES (?,?,?,?,?,?)", (uid(), project_id, job_id, settings.model, encode(usage), now()))
                store.save_profile(project_id, payload, "AI 提取，等待人工核对", expected_version, job_id=job_id)
                store.update_job(project_id, job_id, status="succeeded", progress="画像已生成，请核对来源及业务理解")
        except ValueError as error:
            store.update_job(project_id, job_id, status="failed", error=str(error), progress="失败，可重试")
        except Exception:
            # Never propagate provider response bodies, request headers or raw input.
            store.update_job(project_id, job_id, status="failed", error="处理失败，已保留现有资料及画像，请重试", progress="失败，可重试")

    @app.get("/api/health")
    def health():
        return {"status": "ok", "model_configured": bool(settings.api_key.get_secret_value()), "mode": "local-pilot"}

    @app.get("/api/projects")
    def projects():
        return store.list_projects()

    @app.post("/api/projects", status_code=201)
    def create_project(data: ProjectInput):
        url = normalize_url(data.site_url)
        return store.create_project(data.name.strip(), url)

    @app.get("/api/projects/{project_id}")
    def detail(project_id: str):
        return store.snapshot(project_id)

    @app.post("/api/projects/{project_id}/sources", status_code=201)
    def add_source(project_id: str, data: SourceInput):
        return store.add_source(project_id, title=data.title, body=data.body)

    @app.post("/api/projects/{project_id}/jobs", status_code=202)
    def create_job(project_id: str, data: JobInput):
        profiles = store.profiles(project_id)
        # Old clients may still send analyze; discovery must never auto-read content.
        kind = "discover" if data.kind == "analyze" else data.kind
        keyword_records = None
        if kind == "seed_keywords":
            if not profiles or not seed_facts(profiles[0]):
                raise ValueError("请先生成并核对产品与服务的业务理解")
            if data.profile_version != profiles[0]["version"]:
                raise ValueError("业务理解已有新版本，请刷新后再提取基础词")
        if data.demand_ids is not None and kind != "clusters":
            raise ValueError("关键词选择仅用于聚类任务")
        if kind == "clusters":
            available = {r["id"]: r for r in store.demands(project_id) if r["kind"] == "keyword" and r["status"] == "pending"}
            ids = data.demand_ids if data.demand_ids is not None else list(available)
            if not ids:
                raise ValueError("请先导入有效关键词，再生成候选分组")
            if any(id not in available for id in ids):
                raise LookupError
            keyword_records = [available[id] for id in dict.fromkeys(ids)]
        if data.kind == "understand" and not any(s["status"] == "read" and s["kind"] != "discovery" for s in store.sources(project_id)):
            raise ValueError("请先采集页面或补充业务资料")
        if data.kind == "intents":
            if not profiles or profiles[0]["status"] != "confirmed":
                raise ValueError("请先核对并保存业务理解，再识别需求意图")
            if not store.pending_intents(project_id):
                raise ValueError("没有待分析或失败的有效输入")
        limit = data.crawl_max_pages if "crawl_max_pages" in data.model_fields_set else settings.crawl_max_pages
        selected_sources = None
        if data.source_ids is not None:
            records = {s["id"]: s for s in store.sources(project_id)}
            if not data.source_ids:
                raise ValueError("请至少选择一个页面")
            if any(source_id not in records for source_id in data.source_ids):
                raise LookupError
            selected_sources = [records[source_id] for source_id in dict.fromkeys(data.source_ids)]
        if kind == "crawl":
            if not selected_sources or any(s["kind"] != "page" or not s["url"] for s in selected_sources):
                raise ValueError("请先在页面清单中筛选并选择要采集的页面")
            if limit is not None and len(selected_sources) > limit:
                raise ValueError("选择数量超过本轮采集上限，请减少选择或调整数量参数")
        if kind == "understand" and selected_sources is not None and any(s["status"] != "read" or s["kind"] == "discovery" for s in selected_sources):
            raise ValueError("请选择已成功读取的正文资料")
        options = {"crawl_max_pages": limit} if kind in {"discover", "crawl"} else {}
        if kind == "seed_keywords":
            options = {"profile_version": profiles[0]["version"]}
        if kind == "clusters":
            options = {"demand_ids": [r["id"] for r in keyword_records], "clustering_model": settings.clustering_model,
                "clustering_distance_threshold": settings.clustering_distance_threshold, "clustering_serp_threshold": settings.clustering_serp_threshold}
        if selected_sources is not None:
            options["source_ids"] = [s["id"] for s in selected_sources]
        job_id = store.create_job(project_id, kind, options)
        job_settings = settings.model_copy(update={"crawl_max_pages": limit})
        app.state.executor.submit(work, project_id, job_id, kind, profiles[0]["version"] if profiles else 0, job_settings, selected_sources, keyword_records)
        return {"id": job_id, "status": "queued"}

    @app.post("/api/projects/{project_id}/jobs/{job_id}/cancel")
    def cancel(project_id: str, job_id: str):
        jobs = store.jobs(project_id)
        if not any(j["id"] == job_id for j in jobs):
            raise LookupError
        store.update_job(project_id, job_id, status="cancelled", progress="已取消；正在进行的单次网络请求结束后停止")
        return {"status": "cancelled"}

    @app.post("/api/projects/{project_id}/profiles", status_code=201)
    def revise(project_id: str, data: ProfileInput):
        validate_evidence(data.payload, store.sources(project_id))
        return store.save_profile(project_id, data.payload.model_dump(), data.reason, data.expected_version)

    @app.post("/api/projects/{project_id}/profiles/review", status_code=201)
    def review_fact(project_id: str, data: ReviewInput):
        profiles = store.profiles(project_id)
        if not profiles or profiles[0]["version"] != data.expected_version:
            raise ValueError("画像已有新版本，请刷新后重新核对")
        payload = profiles[0]["payload"]
        if data.fact_index >= len(payload["facts"]):
            raise LookupError
        fact = payload["facts"][data.fact_index]
        fact["status"] = data.decision
        if data.statement is not None:
            fact["statement"] = data.statement
        if data.scope is not None:
            fact["scope"] = data.scope
        validate_evidence(Understanding.model_validate(payload), store.sources(project_id))
        return store.save_profile(project_id, payload, data.reason, data.expected_version)

    @app.post("/api/projects/{project_id}/profiles/confirm")
    def confirm(project_id: str, data: ConfirmInput):
        store.project(project_id)
        return store.confirm_profile(project_id, data.expected_version, data.reason)

    @app.get("/api/projects/{project_id}/demands")
    def demands(project_id: str):
        return store.demands(project_id)

    @app.get("/api/projects/{project_id}/intents")
    def intents(project_id: str):
        return store.intents(project_id)

    @app.get("/api/projects/{project_id}/clusters")
    def clusters(project_id: str):
        return {"runs": store.cluster_runs(project_id), "settings": {"model": settings.clustering_model,
            "distance_threshold": settings.clustering_distance_threshold, "serp_match_threshold": settings.clustering_serp_threshold}}

    @app.post("/api/projects/{project_id}/demands/preview")
    def preview_demands(project_id: str, data: IntakeInput):
        if data.kind != "keyword" and not store.keyword_onboarding(project_id):
            raise ValueError("请先完成首次关键词分析，再添加客户问题或产品变化")
        return import_records(store, project_id, data.model_dump())

    @app.post("/api/projects/{project_id}/demands/import", status_code=201)
    def commit_demands(project_id: str, data: IntakeInput):
        if data.kind != "keyword" and not store.keyword_onboarding(project_id):
            raise ValueError("请先完成首次关键词分析，再添加客户问题或产品变化")
        return import_records(store, project_id, data.model_dump(), commit=True)

    @app.post("/api/projects/{project_id}/keyword-onboarding/complete")
    def complete_keyword_onboarding(project_id: str, data: KeywordCompletionInput):
        return store.complete_keyword_onboarding(project_id, data.run_id, data.selected_group_ids)

    @app.post("/api/projects/{project_id}/seed-keywords/{run_id}/terms/{index}")
    def edit_seed_keyword(project_id: str, run_id: str, index: int, data: SeedKeywordEditInput):
        return store.edit_seed_keyword(project_id, run_id, index, data.expected_revision, data.model_dump())

    @app.get("/")
    def home():
        return FileResponse(ROOT / "frontend" / "index.html")

    app.mount("/assets", StaticFiles(directory=ROOT / "frontend"), name="assets")
    return app
