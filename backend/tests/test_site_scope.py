import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from pagggle.app import create_app
from pagggle.config import Settings
from pagggle.crawl import crawl_site
from pagggle.store import Store
from pagggle.understanding import generate_understanding, understanding_batches


def settings(**kwargs):
    return Settings(BASE_URL="https://model.example/v1", model="test", api_key="test", **kwargs)


def test_unlimited_discovery_follows_all_sitemaps_without_reading_pages(monkeypatch, tmp_path):
    store = Store(tmp_path / "test.sqlite3")
    project = store.create_project("Synthetic", "https://example.com/")["id"]
    requests = []
    def fetch(url, *args):
        requests.append(url)
        if url.endswith("robots.txt"):
            return url, "text/plain", b"User-agent: *\nAllow: /"
        if url.endswith("sitemap.xml"):
            body = '<sitemapindex>' + ''.join(f'<sitemap><loc>https://example.com/map{i}.xml</loc></sitemap>' for i in range(6)) + '</sitemapindex>'
        elif "/map" in url:
            index = int(url.rsplit("map", 1)[1].split(".")[0])
            body = '<urlset>' + ''.join(f'<url><loc>https://example.com/product-{index * 100 + i}</loc><image><loc>https://example.com/photo.png</loc></image></url>' for i in range(100)) + '</urlset>'
        else:
            body = '<h1>Products</h1><p>We manufacture industrial silicone products, with verified specifications.</p>'
            return url, "text/html", body.encode()
        return url, "application/xml", body.encode()
    monkeypatch.setattr("pagggle.crawl.fetch", fetch)
    crawl_site(store, project, settings(), lambda: False, lambda message: None, discover_only=True)
    sources = store.sources(project)
    pages = [s for s in sources if s["kind"] == "page"]
    assert len(pages) == 601
    assert all(s["status"] == "uncovered" and not s["body"] for s in pages)
    assert len(requests) == 8
    assert not any(s["url"].endswith("photo.png") for s in sources)
    assert len([s for s in sources if s["kind"] == "discovery"]) == 8


def test_crawl_stores_full_body_and_honors_explicit_limit(monkeypatch, tmp_path):
    store = Store(tmp_path / "test.sqlite3")
    project = store.create_project("Synthetic", "https://example.com/")["id"]
    long_text = "正文资料 " * 20000 + "TAIL_EVIDENCE"
    def fetch(url, *args):
        if url.endswith("robots.txt"):
            return url, "text/plain", b"User-agent: *\nAllow: /"
        if url.endswith("sitemap.xml"):
            return url, "application/xml", b"<urlset/>"
        return url, "text/html", f'<p>{long_text}</p><a href="/product-next">Next</a>'.encode()
    monkeypatch.setattr("pagggle.crawl.fetch", fetch)
    crawl_site(store, project, settings(crawl_max_pages=1), lambda: False, lambda message: None)
    pages = [s for s in store.sources(project) if s["kind"] == "page"]
    assert len(pages) == 2
    assert "TAIL_EVIDENCE" in next(s["body"] for s in pages if s["status"] == "read")
    assert next(s for s in pages if s["status"] == "uncovered")["url"].endswith("product-next")


def source(source_id, body):
    return {"id": source_id, "title": "Synthetic", "body": body, "status": "read", "kind": "page"}


def test_batches_cover_full_text_and_keep_tail_evidence(monkeypatch):
    sources = [source("one", "HEAD " + ('Quotes " and slash \\ and newline\n' * 70) + "TAIL_MARKER"), source("two", "Second page with independent evidence.")]
    budget = 1000
    batches = understanding_batches(sources, budget)
    for batch in batches:
        assert len(json.dumps([{k: s[k] for k in ("id", "title", "body")} for s in batch], ensure_ascii=False)) <= budget
    for original in sources:
        ranges = []
        for batch in batches:
            for item in batch:
                if item["id"] == original["id"]:
                    ranges.append(item["body"])
        # Sequential overlap reconstructs the exact text, including its final characters.
        cursor = 0
        for text in ranges:
            start = original["body"].find(text, max(0, cursor - 200))
            assert 0 <= start <= cursor
            cursor = start + len(text)
        assert cursor == len(original["body"])
    observed = []
    real_client = httpx.Client
    def handler(request):
        batch = json.loads(json.loads(request.content)["messages"][1]["content"])
        observed.extend(batch)
        facts = [{"category": "product", "statement": "批次资料 " + str(len(observed)), "status": "stated", "scope": "本段", "citations": [{"source_id": row["id"], "quote": row["body"][-20:]}]} for row in batch]
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"facts": facts, "gaps": []})}}], "usage": {"total_tokens": 12}})
    monkeypatch.setattr("pagggle.understanding.httpx.Client", lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    payload, usage = generate_understanding(settings(model_input_chars=budget), sources)
    assert any("TAIL_MARKER" in r["body"] for r in observed)
    assert usage["truncated_sources"] == 0 and usage["source_count"] == 2
    assert usage["analyzed_characters"] == sum(len(s["body"]) for s in sources)
    assert usage["batch_count"] == len(batches)
    assert usage["total_tokens"] == len(batches) * 12
    assert payload["facts"]


def test_failed_batch_and_cancellation_never_return_partial_profile(monkeypatch):
    sources = [source("one", "Content " * 500)]
    calls = []
    def extract(settings, batch):
        calls.append(batch)
        if len(calls) == 2:
            raise ValueError("bad evidence")
        return {"facts": [], "gaps": []}, {}
    monkeypatch.setattr("pagggle.understanding.extract_batch", extract)
    with pytest.raises(ValueError, match="新画像未保存"):
        generate_understanding(settings(model_input_chars=1000), sources)
    calls.clear()
    with pytest.raises(ValueError, match="已取消"):
        generate_understanding(settings(model_input_chars=1000), sources, cancelled=lambda: bool(calls))
    assert len(calls) == 1


def test_page_limit_api_persists_scope_and_does_not_mutate_defaults(monkeypatch, tmp_path):
    store = Store(tmp_path / "test.sqlite3")
    project = store.create_project("Synthetic", "https://example.com/")["id"]
    page = store.add_source(project, title="Synthetic", kind="page", status="uncovered", url="https://example.com/")
    captured = []
    def crawl(store, project, config, *args):
        captured.append(config.crawl_max_pages)
        store.add_source(project, title="Synthetic", body="Synthetic full page content.", kind="page", url="https://example.com/")
    monkeypatch.setattr("pagggle.app.crawl_site", crawl)
    config = settings()
    with TestClient(create_app(config, store)) as client:
        for limit in [2, None, 501]:
            response = client.post(f"/api/projects/{project}/jobs", json={"kind": "crawl", "crawl_max_pages": limit, "source_ids": [page["id"]]})
            assert response.status_code == 202
            for _ in range(200):
                job = store.jobs(project)[0]
                if job["status"] not in {"queued", "running"}:
                    break
                time.sleep(.01)
            assert job["status"] == "succeeded"
            assert job["options"]["crawl_max_pages"] == limit
        for bad in [0, -1, 1.5, True]:
            assert client.post(f"/api/projects/{project}/jobs", json={"kind": "crawl", "crawl_max_pages": bad}).status_code == 422
    assert captured == [2, None, 501]
    assert config.crawl_max_pages is None


def test_selected_collection_does_not_expand_links_or_trigger_model(monkeypatch, tmp_path):
    store = Store(tmp_path / "selected.sqlite3")
    project = store.create_project("Synthetic", "https://example.com/")["id"]
    first = store.add_source(project, title="First", kind="page", status="uncovered", url="https://example.com/first")
    second = store.add_source(project, title="Second", kind="page", status="uncovered", url="https://example.com/second")
    other = store.create_project("Other", "https://other.example/")["id"]
    foreign = store.add_source(other, title="Foreign", kind="page", status="uncovered", url="https://other.example/a")
    requests = []
    def fetch(url, *args):
        requests.append(url)
        if url.endswith("robots.txt"):
            return url, "text/plain", b"User-agent: *\nAllow: /"
        return url, "text/html", b'<p>We manufacture industrial silicone products with full specifications.</p><a href="/do-not-follow">Next</a>'
    monkeypatch.setattr("pagggle.crawl.fetch", fetch)
    monkeypatch.setattr("pagggle.app.generate_understanding", lambda *a, **kw: pytest.fail("Collect must not call model"))
    with TestClient(create_app(settings(), store)) as client:
        base = f"/api/projects/{project}/jobs"
        assert client.post(base, json={"kind": "crawl"}).status_code == 409
        assert client.post(base, json={"kind": "crawl", "source_ids": [foreign["id"]]}).status_code == 404
        assert client.post(base, json={"kind": "crawl", "source_ids": [first["id"], second["id"]], "crawl_max_pages": 1}).status_code == 409
        assert client.post(base, json={"kind": "crawl", "source_ids": [first["id"]]}).status_code == 202
        for _ in range(200):
            if store.jobs(project)[0]["status"] not in {"queued", "running"}:
                break
            time.sleep(.01)
        assert store.jobs(project)[0]["status"] == "succeeded"
    assert requests == ["https://example.com/robots.txt", "https://example.com/first"]
    assert store.profiles(project) == []
    assert next(s for s in store.sources(project) if s["id"] == second["id"])["status"] == "uncovered"


@pytest.mark.parametrize("kind", ["discover", "crawl"])
def test_current_robots_failure_is_visible_even_with_saved_success(monkeypatch, tmp_path, kind):
    from pagggle.crawl import CrawlError
    store = Store(tmp_path / "robots-history.sqlite3")
    project = store.create_project("Historical sources", "https://example.com/")["id"]
    store.add_source(project, title="robots.txt", kind="discovery", url="https://example.com/robots.txt", body="User-agent: *\nAllow: /")
    page = store.add_source(project, title="Old page", kind="page", url="https://example.com/", body="Previously collected complete page content.")
    def blocked(*args):
        raise CrawlError("HTTP 202")
    monkeypatch.setattr("pagggle.crawl.fetch", lambda *a: pytest.fail("No direct fallback"))
    monkeypatch.setattr("pagggle.crawl.fetch_firecrawl", blocked)
    with TestClient(create_app(settings(crawl_backend="firecrawl"), store)) as client:
        payload = {"kind": kind}
        if kind == "crawl":
            payload["source_ids"] = [page["id"]]
        assert client.post(f"/api/projects/{project}/jobs", json=payload).status_code == 202
        for _ in range(200):
            job = store.jobs(project)[0]
            if job["status"] not in {"queued", "running"}:
                break
            time.sleep(.01)
        assert job["status"] == "failed"
        assert job["error"] == "robots.txt 读取失败：HTTP 202"
        assert all(s["status"] == "read" for s in store.sources(project))
        assert store.profiles(project) == []
