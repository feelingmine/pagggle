import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


def now():
    return datetime.now(timezone.utc).isoformat()


def uid():
    return uuid4().hex


def encode(value):
    return json.dumps(value, ensure_ascii=False)


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.parent.chmod(0o700)
        descriptor = os.open(self.path, os.O_CREAT | os.O_WRONLY, 0o600)
        os.close(descriptor)
        self.path.chmod(0o600)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, site_url TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS sources (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
                    url TEXT, title TEXT NOT NULL, kind TEXT NOT NULL,
                    status TEXT NOT NULL, body TEXT NOT NULL DEFAULT '', error TEXT,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
                    UNIQUE(project_id, url)
                );
                CREATE TABLE IF NOT EXISTS profiles (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
                    version INTEGER NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL,
                    reason TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    UNIQUE(project_id, version)
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
                    kind TEXT NOT NULL, status TEXT NOT NULL, progress TEXT NOT NULL,
                    error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 1
                );
                CREATE UNIQUE INDEX IF NOT EXISTS active_project_job
                    ON jobs(project_id) WHERE status IN ('queued', 'running');
                CREATE TABLE IF NOT EXISTS events (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
                    name TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS model_runs (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
                    job_id TEXT NOT NULL REFERENCES jobs(id), model TEXT NOT NULL,
                    usage TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS intake_batches (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
                    request_id TEXT NOT NULL, content_hash TEXT NOT NULL, result TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
                    UNIQUE(project_id, request_id)
                );
                CREATE TABLE IF NOT EXISTS demands (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
                    batch_id TEXT NOT NULL REFERENCES intake_batches(id), kind TEXT NOT NULL,
                    fingerprint TEXT NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS intent_results (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
                    demand_id TEXT NOT NULL REFERENCES demands(id), profile_version INTEGER NOT NULL,
                    job_id TEXT NOT NULL REFERENCES jobs(id), status TEXT NOT NULL,
                    payload TEXT, error TEXT, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS cluster_runs (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
                    job_id TEXT NOT NULL REFERENCES jobs(id), payload TEXT NOT NULL,
                    created_at TEXT NOT NULL, version INTEGER NOT NULL,
                    UNIQUE(project_id, job_id), UNIQUE(project_id, version)
                );
                CREATE TABLE IF NOT EXISTS keyword_onboarding (
                    project_id TEXT PRIMARY KEY REFERENCES projects(id),
                    run_id TEXT NOT NULL REFERENCES cluster_runs(id),
                    selected_group_ids TEXT NOT NULL, completed_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS seed_keyword_runs (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
                    job_id TEXT NOT NULL REFERENCES jobs(id), profile_version INTEGER NOT NULL,
                    payload TEXT NOT NULL, created_at TEXT NOT NULL,
                    UNIQUE(project_id, job_id)
                );
                CREATE TABLE IF NOT EXISTS seed_keyword_revisions (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
                    run_id TEXT NOT NULL REFERENCES seed_keyword_runs(id), revision INTEGER NOT NULL,
                    payload TEXT NOT NULL, created_at TEXT NOT NULL,
                    UNIQUE(project_id, run_id, revision)
                );
                CREATE TABLE IF NOT EXISTS content_runs (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
                    job_id TEXT NOT NULL REFERENCES jobs(id), version TEXT NOT NULL,
                    input_json TEXT, result_json TEXT, input_hash TEXT, result_hash TEXT,
                    replay_of TEXT, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS content_assets (
                    project_id TEXT NOT NULL REFERENCES projects(id), url TEXT NOT NULL,
                    payload TEXT NOT NULL, PRIMARY KEY(project_id, url)
                );
                CREATE TABLE IF NOT EXISTS content_skill_calls (
                    project_id TEXT NOT NULL REFERENCES projects(id),
                    run_id TEXT NOT NULL REFERENCES content_runs(id),
                    request_hash TEXT NOT NULL, stage TEXT NOT NULL,
                    payload TEXT NOT NULL, created_at TEXT NOT NULL,
                    PRIMARY KEY(project_id, run_id, request_hash)
                );
            """)
            if "options" not in {r["name"] for r in db.execute("PRAGMA table_info(jobs)")}:
                db.execute("ALTER TABLE jobs ADD COLUMN options TEXT NOT NULL DEFAULT '{}'")
            if "analysis_job_id" not in {r["name"] for r in db.execute("PRAGMA table_info(profiles)")}:
                db.execute("ALTER TABLE profiles ADD COLUMN analysis_job_id TEXT")
            db.execute("PRAGMA user_version=10")
        self.path.chmod(0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def project(self, project_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        if not row:
            raise LookupError("项目不存在")
        return dict(row)

    def create_project(self, name, site_url):
        project_id, timestamp = uid(), now()
        with self.connect() as db:
            db.execute("INSERT INTO projects VALUES (?,?,?,?,?,1)", (project_id, name, site_url, timestamp, timestamp))
            self.event(db, project_id, "project_initialized", {})
        return self.project(project_id)

    def list_projects(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM projects ORDER BY created_at DESC")]

    def snapshot(self, project_id):
        # A single read transaction avoids mixing pre-completion usage with a completed job.
        with self.connect() as db:
            db.execute("BEGIN")
            project = db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
            if not project:
                raise LookupError("项目不存在")
            sources = [dict(r) for r in db.execute("SELECT * FROM sources WHERE project_id=? ORDER BY created_at", (project_id,))]
            profiles = [{**dict(r), "payload": json.loads(r["payload"])} for r in db.execute("SELECT * FROM profiles WHERE project_id=? ORDER BY version DESC", (project_id,))]
            jobs = [{**dict(r), "options": json.loads(r["options"])} for r in db.execute("SELECT * FROM jobs WHERE project_id=? ORDER BY created_at DESC LIMIT 30", (project_id,))]
            runs = [{**dict(r), "usage": json.loads(r["usage"])} for r in db.execute("SELECT id,job_id,model,usage,created_at FROM model_runs WHERE project_id=? ORDER BY created_at DESC", (project_id,))]
            onboarding = db.execute("SELECT * FROM keyword_onboarding WHERE project_id=?", (project_id,)).fetchone()
            seeds = [{**dict(r), "payload": json.loads(r["payload"])} for r in db.execute("SELECT * FROM seed_keyword_runs WHERE project_id=? ORDER BY rowid DESC", (project_id,))]
            for seed in seeds:
                edit = db.execute("SELECT revision,payload,created_at FROM seed_keyword_revisions WHERE project_id=? AND run_id=? ORDER BY revision DESC LIMIT 1", (project_id, seed["id"])).fetchone()
                seed.update(original_payload=seed["payload"], revision=edit["revision"] if edit else 0, updated_at=edit["created_at"] if edit else seed["created_at"])
                if edit:
                    seed["payload"] = json.loads(edit["payload"])
        return {"project": dict(project), "sources": sources, "profiles": profiles, "jobs": jobs, "model_runs": runs,
            "seed_keyword_runs": seeds,
            "keyword_onboarding": {**dict(onboarding), "selected_group_ids": json.loads(onboarding["selected_group_ids"])} if onboarding else None}

    def save_seed_keywords(self, project_id, job_id, profile_version, payload, model, usage):
        from .seed_keywords import SeedKeywords, validate_seed_keywords

        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            job = db.execute("SELECT status,kind,options FROM jobs WHERE project_id=? AND id=?", (project_id, job_id)).fetchone()
            if not job or job["status"] != "running" or job["kind"] != "seed_keywords":
                raise ValueError("基础词任务已取消或不存在，结果未保存")
            if json.loads(job["options"]).get("profile_version") != profile_version:
                raise ValueError("基础词任务的业务理解版本不匹配")
            profile = db.execute("SELECT version,payload FROM profiles WHERE project_id=? ORDER BY version DESC LIMIT 1", (project_id,)).fetchone()
            if not profile or profile["version"] != profile_version:
                raise ValueError("业务理解已有新版本，请刷新后重新提取基础词")
            validate_seed_keywords(SeedKeywords.model_validate(payload), {"payload": json.loads(profile["payload"])})
            db.execute("INSERT INTO seed_keyword_runs VALUES (?,?,?,?,?,?)", (uid(), project_id, job_id, profile_version, encode(payload), now()))
            db.execute("INSERT INTO model_runs VALUES (?,?,?,?,?,?)", (uid(), project_id, job_id, model, encode(usage), now()))
            self.event(db, project_id, "seed_keywords_extracted", {"job_id": job_id, "profile_version": profile_version, "keywords": len(payload["keywords"])})

    def edit_seed_keyword(self, project_id, run_id, index, expected_revision, changes):
        from .seed_keywords import SeedKeywords, validate_seed_keywords

        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            run = db.execute("SELECT * FROM seed_keyword_runs WHERE project_id=? AND id=?", (project_id, run_id)).fetchone()
            if not run:
                raise LookupError("基础词记录不存在")
            profile = db.execute("SELECT version,payload FROM profiles WHERE project_id=? ORDER BY version DESC LIMIT 1", (project_id,)).fetchone()
            newest = db.execute("SELECT id FROM seed_keyword_runs WHERE project_id=? AND profile_version=? ORDER BY rowid DESC LIMIT 1", (project_id, run["profile_version"])).fetchone()
            if not profile or profile["version"] != run["profile_version"] or newest["id"] != run_id:
                raise ValueError("正在编辑的基础词已不是当前版本，请刷新后再修改")
            if db.execute("SELECT 1 FROM jobs WHERE project_id=? AND status IN ('queued','running')", (project_id,)).fetchone():
                raise ValueError("请等待当前任务完成后再编辑基础词")
            edit = db.execute("SELECT revision,payload FROM seed_keyword_revisions WHERE project_id=? AND run_id=? ORDER BY revision DESC LIMIT 1", (project_id, run_id)).fetchone()
            revision = edit["revision"] if edit else 0
            if revision != expected_revision:
                raise ValueError("基础词已被修改，请刷新后再编辑；本次输入未覆盖新版本")
            payload = json.loads(edit["payload"] if edit else run["payload"])
            if index < 0 or index >= len(payload["keywords"]):
                raise LookupError("基础词不存在")
            original = payload["keywords"][index]
            updated = {**original, **{key: changes[key] for key in ("keyword", "category", "kind")}}
            payload["keywords"][index] = updated
            validate_seed_keywords(SeedKeywords.model_validate(payload), {"payload": json.loads(profile["payload"])})
            if updated == original:
                return {"revision": revision, "changed": False}
            revision += 1
            db.execute("INSERT INTO seed_keyword_revisions VALUES (?,?,?,?,?,?)", (uid(), project_id, run_id, revision, encode(payload), now()))
            self.event(db, project_id, "seed_keyword_edited", {"run_id": run_id, "revision": revision, "keyword_index": index})
            return {"revision": revision, "changed": True}

    def sources(self, project_id):
        self.project(project_id)
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM sources WHERE project_id=? ORDER BY created_at", (project_id,))]

    def demands(self, project_id):
        self.project(project_id)
        with self.connect() as db:
            return [{**json.loads(r["payload"]), "created_at": r["created_at"], "batch_id": r["batch_id"]} for r in db.execute("SELECT * FROM demands WHERE project_id=? ORDER BY created_at DESC, rowid", (project_id,))]

    def add_source(self, project_id, *, title, body="", url=None, kind="manual", status="read", error=None):
        self.project(project_id)
        timestamp, source_id = now(), uid()
        with self.connect() as db:
            # Read snapshots are immutable: retries may only replace unread discoveries.
            existing = db.execute("SELECT * FROM sources WHERE project_id=? AND url=?", (project_id, url)).fetchone() if url else None
            if existing and existing["status"] == "read":
                return dict(existing)
            if existing:
                source_id = existing["id"]
                db.execute("UPDATE sources SET title=?, body=?, status=?, error=?, updated_at=?, version=version+1 WHERE id=? AND project_id=?", (title, body, status, error, timestamp, source_id, project_id))
            else:
                db.execute("INSERT INTO sources VALUES (?,?,?,?,?,?,?,?,?,?,1)", (source_id, project_id, url, title, kind, status, body, error, timestamp, timestamp))
            row = db.execute("SELECT * FROM sources WHERE id=? AND project_id=?", (source_id, project_id)).fetchone()
        return dict(row)

    def intents(self, project_id):
        self.project(project_id)
        with self.connect() as db:
            rows = db.execute("SELECT * FROM intent_results WHERE project_id=? ORDER BY rowid DESC", (project_id,)).fetchall()
        latest = {}
        for row in rows:
            if row["demand_id"] not in latest:
                latest[row["demand_id"]] = {**dict(row), "payload": json.loads(row["payload"]) if row["payload"] else None}
        return list(latest.values())

    def pending_intents(self, project_id):
        results = {r["demand_id"]: r for r in self.intents(project_id)}
        return [r for r in self.demands(project_id) if r["status"] == "pending" and (r["id"] not in results or results[r["id"]]["status"] == "failed")]

    def cluster_runs(self, project_id):
        self.project(project_id)
        with self.connect() as db:
            rows = db.execute("SELECT * FROM cluster_runs WHERE project_id=? ORDER BY version DESC", (project_id,)).fetchall()
        return [{**dict(r), "payload": json.loads(r["payload"])} for r in rows]

    def save_clusters(self, project_id, job_id, payload):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            job = db.execute("SELECT status,options FROM jobs WHERE project_id=? AND id=?", (project_id, job_id)).fetchone()
            if not job or job["status"] != "running":
                raise ValueError("任务已取消，聚类结果未写入")
            expected = json.loads(job["options"])["demand_ids"]
            actual = [m["demand_id"] for g in payload["groups"] for m in g["members"]]
            known = {r["id"] for r in db.execute("SELECT id FROM demands WHERE project_id=? AND kind='keyword' AND status='pending'", (project_id,))}
            if len(actual) != len(set(actual)) or set(actual) != set(expected) or not set(actual) <= known:
                raise ValueError("聚类成员遗漏、重复或不属于本项目，结果未保存")
            version = db.execute("SELECT COALESCE(MAX(version),0)+1 FROM cluster_runs WHERE project_id=?", (project_id,)).fetchone()[0]
            db.execute("INSERT INTO cluster_runs VALUES (?,?,?,?,?,?)", (uid(), project_id, job_id, encode(payload), now(), version))
            self.event(db, project_id, "keyword_clustered", {"job_id": job_id, "version": version, **payload["summary"]})

    def keyword_onboarding(self, project_id):
        self.project(project_id)
        with self.connect() as db:
            row = db.execute("SELECT * FROM keyword_onboarding WHERE project_id=?", (project_id,)).fetchone()
        return {**dict(row), "selected_group_ids": json.loads(row["selected_group_ids"])} if row else None

    def complete_keyword_onboarding(self, project_id, run_id, selected_group_ids):
        self.project(project_id)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            run = db.execute("SELECT * FROM cluster_runs WHERE project_id=? AND id=?", (project_id, run_id)).fetchone()
            if not run:
                raise LookupError("本项目没有该分析结果")
            existing = db.execute("SELECT * FROM keyword_onboarding WHERE project_id=?", (project_id,)).fetchone()
            if existing:
                if existing["run_id"] == run_id and sorted(json.loads(existing["selected_group_ids"])) == sorted(selected_group_ids):
                    return {**dict(existing), "selected_group_ids": json.loads(existing["selected_group_ids"])}
                raise ValueError("首次关键词分析已经完成，原筛选记录保留")
            if db.execute("SELECT 1 FROM jobs WHERE project_id=? AND status IN ('queued','running')", (project_id,)).fetchone():
                raise ValueError("请等待当前任务结束，再确认完成")
            latest = db.execute("SELECT MAX(version) FROM cluster_runs WHERE project_id=?", (project_id,)).fetchone()[0]
            job = db.execute("SELECT status FROM jobs WHERE project_id=? AND id=?", (project_id, run["job_id"])).fetchone()
            if run["version"] != latest or not job or job["status"] != "succeeded":
                raise ValueError("请使用最新成功的全量分析结果")
            groups = json.loads(run["payload"])["groups"]
            members = [m["demand_id"] for g in groups for m in g["members"]]
            current = {r[0] for r in db.execute("SELECT id FROM demands WHERE project_id=? AND kind='keyword' AND status='pending'", (project_id,))}
            if not current or set(members) != current or len(members) != len(current):
                raise ValueError("还有有效关键词未纳入此结果，请重新分析全部关键词")
            allowed = {g["target_page_id"] for g in groups}
            if len(set(selected_group_ids)) != len(selected_group_ids) or not set(selected_group_ids) <= allowed:
                raise ValueError("所选候选组不属于此分析结果或重复，请刷新后重试")
            completed_at = now()
            db.execute("INSERT INTO keyword_onboarding VALUES (?,?,?,?)", (project_id, run_id, encode(selected_group_ids), completed_at))
            self.event(db, project_id, "keyword_onboarding_completed", {"run_id": run_id, "keywords": len(current), "selected_groups": len(selected_group_ids)})
        return {"project_id": project_id, "run_id": run_id, "selected_group_ids": selected_group_ids, "completed_at": completed_at}

    def save_intents(self, project_id, job_id, profile_version, results, model, usage):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            job = db.execute("SELECT status FROM jobs WHERE project_id=? AND id=?", (project_id, job_id)).fetchone()
            if not job or job["status"] != "running":
                raise ValueError("任务已取消，意图结果未写入")
            profile = db.execute("SELECT version,status FROM profiles WHERE project_id=? ORDER BY version DESC LIMIT 1", (project_id,)).fetchone()
            if not profile or profile["version"] != profile_version or profile["status"] != "confirmed":
                raise ValueError("业务理解已变化，请核对最新版本后重试")
            for result in results:
                demand = db.execute("SELECT id FROM demands WHERE project_id=? AND id=? AND status='pending'", (project_id, result["input_id"])).fetchone()
                if not demand:
                    raise ValueError("需求不属于本项目的有效输入")
                db.execute("INSERT INTO intent_results VALUES (?,?,?,?,?,?,?,?,?)", (uid(), project_id, demand["id"], profile_version, job_id, result["status"], encode(result["payload"]) if result["payload"] else None, result["error"], now()))
            if usage is not None:
                db.execute("INSERT INTO model_runs VALUES (?,?,?,?,?,?)", (uid(), project_id, job_id, model, encode(usage), now()))
            self.event(db, project_id, "intent_extracted", {"job_id": job_id, "profile_version": profile_version, "count": len(results), "failed": sum(r["status"] == "failed" for r in results)})

    @staticmethod
    def event(db, project_id, name, payload):
        db.execute("INSERT INTO events VALUES (?,?,?,?,?)", (uid(), project_id, name, encode(payload), now()))

    def profiles(self, project_id):
        self.project(project_id)
        with self.connect() as db:
            rows = db.execute("SELECT * FROM profiles WHERE project_id=? ORDER BY version DESC", (project_id,)).fetchall()
        return [{**dict(row), "payload": json.loads(row["payload"])} for row in rows]

    def save_profile(self, project_id, payload, reason, expected_version, status="draft", job_id=None):
        self.project(project_id)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if job_id:
                job = db.execute("SELECT status FROM jobs WHERE id=? AND project_id=?", (job_id, project_id)).fetchone()
                if not job or job["status"] != "running":
                    raise ValueError("任务已取消，画像未写入")
            version = db.execute("SELECT COALESCE(MAX(version),0) FROM profiles WHERE project_id=?", (project_id,)).fetchone()[0]
            if version != expected_version:
                raise ValueError("画像已有新版本，请刷新后重试")
            timestamp = now()
            previous = db.execute("SELECT analysis_job_id FROM profiles WHERE project_id=? ORDER BY version DESC LIMIT 1", (project_id,)).fetchone()
            analysis_job_id = job_id or (previous[0] if previous else None)
            db.execute("INSERT INTO profiles VALUES (?,?,?,?,?,?,?,?,?)", (uid(), project_id, version+1, status, encode(payload), reason, timestamp, timestamp, analysis_job_id))
            self.event(db, project_id, "site_understanding_confirmed" if status == "confirmed" else "site_understanding_revised", {"version": version+1})
        return self.profiles(project_id)[0]

    def confirm_profile(self, project_id, expected_version, reason):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM profiles WHERE project_id=? ORDER BY version DESC LIMIT 1", (project_id,)).fetchone()
            if not row or row["version"] != expected_version:
                raise ValueError("画像版本已变化，请刷新后重新核对")
            if not json.loads(row["payload"])["facts"]:
                raise ValueError("空画像不能确认")
            db.execute("UPDATE profiles SET status='confirmed', reason=?, updated_at=? WHERE id=? AND project_id=?", (reason, now(), row["id"], project_id))
            self.event(db, project_id, "site_understanding_confirmed", {"version": expected_version})
        return self.profiles(project_id)[0]

    def jobs(self, project_id):
        self.project(project_id)
        with self.connect() as db:
            return [{**dict(r), "options": json.loads(r["options"])} for r in db.execute("SELECT * FROM jobs WHERE project_id=? ORDER BY created_at DESC LIMIT 30", (project_id,))]

    def create_job(self, project_id, kind, options=None):
        self.project(project_id)
        job_id, timestamp = uid(), now()
        try:
            with self.connect() as db:
                db.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,1,?)", (job_id, project_id, kind, "queued", "等待执行", None, timestamp, timestamp, encode(options or {})))
        except sqlite3.IntegrityError:
            raise ValueError("本项目已有进行中的任务，请等待或取消后重试") from None
        return job_id

    def update_job(self, project_id, job_id, *, status=None, progress=None, error=None):
        with self.connect() as db:
            db.execute("UPDATE jobs SET status=COALESCE(?,status), progress=COALESCE(?,progress), error=?, updated_at=?, version=version+1 WHERE id=? AND project_id=? AND status IN ('queued','running')", (status, progress, error, now(), job_id, project_id))

    def cancelled(self, project_id, job_id):
        with self.connect() as db:
            row = db.execute("SELECT status FROM jobs WHERE id=? AND project_id=?", (job_id, project_id)).fetchone()
        return not row or row["status"] not in {"queued", "running"}

    def recover_jobs(self):
        with self.connect() as db:
            db.execute("UPDATE jobs SET status='failed', error='服务重启导致任务中断，请重试', updated_at=?, version=version+1 WHERE status IN ('queued','running')", (now(),))
