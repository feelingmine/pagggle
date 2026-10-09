import pytest
import httpx

from pagggle.config import Settings
from pagggle.crawl import CrawlError, crawl_site, fetch, fetch_firecrawl, normalize_url, parse_html, public_address
from pagggle.store import Store


@pytest.mark.parametrize("url", ["file:///etc/passwd", "https://user:pass@example.com", "http://example.com:9000", "javascript:alert(1)"])
def test_unsafe_urls_rejected(url):
    with pytest.raises(CrawlError):
        normalize_url(url)


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.2", "169.254.169.254", "::1", "::ffff:127.0.0.1"])
def test_private_dns_targets_rejected(monkeypatch, address):
    monkeypatch.setattr("socket.getaddrinfo", lambda *a, **kw: [(2, 1, 6, "", (address, 443))])
    with pytest.raises(CrawlError):
        public_address("example.com", 443)


def test_redirect_host_rejected_before_dns(monkeypatch):
    monkeypatch.setattr("socket.getaddrinfo", lambda *a, **kw: pytest.fail("DNS must not be used for off-site host"))
    with pytest.raises(CrawlError):
        fetch("https://other.example/", allowed_host="example.com")


def test_html_removes_scripts_and_only_discovers_same_site():
    title, body, links = parse_html(b'<title>Products</title><script>stolen()</script><h1>Silicone gaskets</h1><a href="/product?token=secret">Product</a><a href="https://outside.example/">Outside</a>', "https://example.com/")
    assert title == "Products"
    assert "stolen" not in body
    assert links == ["https://example.com/product"]


def test_crawl_records_failures_exclusions_and_uncovered(monkeypatch, tmp_path):
    settings = Settings(BASE_URL="https://provider.example/v1", model="test", api_key="", crawl_max_pages=3)
    store = Store(tmp_path / "test.sqlite3")
    project = store.create_project("Demo", "https://example.com/")
    def fake_fetch(url, *args):
        if url.endswith("robots.txt"):
            return url, "text/plain", b"User-agent: *\nDisallow: /product-private"
        if url.endswith("sitemap.xml"):
            return url, "application/xml", b"<urlset><url><loc>https://example.com/about</loc></url></urlset>"
        if url.endswith("/about"):
            raise CrawlError("HTTP 503")
        return url, "text/html", b'<title>Home</title><p>We manufacture custom silicone gaskets for industrial applications.</p><a href="/product-private">Private</a><a href="/blog">Blog</a>'
    monkeypatch.setattr("pagggle.crawl.fetch", fake_fetch)
    crawl_site(store, project["id"], settings, lambda: False, lambda msg: None)
    sources = {s["url"]: s for s in store.sources(project["id"])}
    assert sources["https://example.com/"]["status"] == "read"
    assert sources["https://example.com/about"]["status"] == "failed"
    assert sources["https://example.com/product-private"]["status"] == "excluded"
    assert sources["https://example.com/blog"]["status"] == "uncovered"


def test_robots_unavailable_preserves_failure(monkeypatch, tmp_path):
    store = Store(tmp_path / "test.sqlite3")
    p = store.create_project("Demo", "https://example.com/")["id"]
    monkeypatch.setattr("pagggle.crawl.fetch", lambda *a: (_ for _ in ()).throw(CrawlError("HTTP 403")))
    settings = Settings(BASE_URL="https://provider.example/v1", model="test", api_key="")
    crawl_site(store, p, settings, lambda: False, lambda msg: None)
    assert all(s["status"] == "failed" for s in store.sources(p))


@pytest.mark.parametrize("address,allowed", [("35.244.153.44", True), ("127.0.0.1", False), ("169.254.169.254", False), ("198.18.1.153", False)])
def test_encrypted_dns_still_validates_target_address(monkeypatch, address, allowed):
    original = httpx.Client
    def handler(request):
        assert request.url.host == "cloudflare-dns.com"
        assert "authorization" not in request.headers
        return httpx.Response(200, json={"Status": 0, "Answer": [{"type": 1, "data": address}]})
    monkeypatch.setattr("pagggle.crawl.httpx.Client", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    monkeypatch.setattr("socket.getaddrinfo", lambda *a, **kw: pytest.fail("DoH must not reuse system target resolution"))
    if allowed:
        assert public_address("example.com", 443, "cloudflare") == address
    else:
        with pytest.raises(CrawlError):
            public_address("example.com", 443, "cloudflare")


def test_encrypted_dns_failure_does_not_fallback_to_fake_ip(monkeypatch):
    original = httpx.Client
    monkeypatch.setattr("pagggle.crawl.httpx.Client", lambda **kwargs: original(transport=httpx.MockTransport(lambda r: httpx.Response(503)), **kwargs))
    monkeypatch.setattr("socket.getaddrinfo", lambda *a, **kw: pytest.fail("No unsafe fallback"))
    with pytest.raises(CrawlError, match="解析失败"):
        public_address("example.com", 443, "cloudflare")


@pytest.mark.parametrize("status,final_host,passes", [(200, "example.com", True), (202, "example.com", False), (200, "other.example", False)])
def test_firecrawl_uses_separate_credential_and_checks_target(monkeypatch, status, final_host, passes):
    settings = Settings(BASE_URL="https://model.example/v1", model="test", api_key="model-secret", firecrawl_api_key="scrape-secret")
    original = httpx.Client
    def handler(request):
        assert request.url.host == "api.firecrawl.dev"
        assert request.headers["Authorization"] == "Bearer scrape-secret"
        assert b"model-secret" not in request.content
        return httpx.Response(200, json={"success": True, "data": {"rawHtml": "<h1>Products</h1>", "metadata": {"url": f"https://{final_host}/", "statusCode": status}}})
    monkeypatch.setattr("pagggle.crawl.public_address", lambda *a: "93.184.215.14")
    monkeypatch.setattr("pagggle.crawl.httpx.Client", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    if passes:
        assert fetch_firecrawl("https://example.com/", settings, "example.com")[2] == b"<h1>Products</h1>"
    else:
        with pytest.raises(CrawlError):
            fetch_firecrawl("https://example.com/", settings, "example.com")


def test_captcha_page_is_not_business_evidence():
    with pytest.raises(CrawlError, match="中转页"):
        parse_html(b'<html><meta http-equiv="refresh" content="0;/.well-known/sgcaptcha/"></html>', "https://example.com/")


@pytest.mark.parametrize("backend", ["direct", "firecrawl"])
def test_discovery_uses_configured_backend_without_reading_pages(monkeypatch, tmp_path, backend):
    settings = Settings(BASE_URL="https://provider.example/v1", model="test", api_key="", crawl_backend=backend, crawl_dns="cloudflare", request_timeout_seconds=12)
    store = Store(tmp_path / "discovery.sqlite3")
    project = store.create_project("Discovery", "https://example.com/")["id"]
    requests = []
    def read(url, *args):
        requests.append(url)
        assert args == ((settings, "example.com") if backend == "firecrawl" else (12, "example.com", "cloudflare"))
        if url.endswith("robots.txt"):
            return url, "text/plain", b"User-agent: *\nAllow: /"
        if url.endswith("sitemap.xml"):
            return url, "application/xml", b"<sitemapindex><sitemap><loc>https://example.com/products.xml</loc></sitemap></sitemapindex>"
        assert url.endswith("products.xml")
        return url, "application/xml", b"<urlset><url><loc>https://example.com/product</loc></url></urlset>"
    monkeypatch.setattr("pagggle.crawl.fetch" if backend == "direct" else "pagggle.crawl.fetch_firecrawl", read)
    monkeypatch.setattr("pagggle.crawl.fetch_firecrawl" if backend == "direct" else "pagggle.crawl.fetch", lambda *a: pytest.fail("Must use configured backend"))
    outcome = crawl_site(store, project, settings, lambda: False, lambda msg: None, discover_only=True)
    assert outcome == {"sitemaps_read": 2, "pages_discovered": 2}
    assert requests == ["https://example.com/robots.txt", "https://example.com/sitemap.xml", "https://example.com/products.xml"]
    assert all(not s["body"] for s in store.sources(project) if s["kind"] == "page")


@pytest.mark.parametrize("failure_at", ["robots.txt", "sitemap.xml"])
@pytest.mark.parametrize("backend", ["direct", "firecrawl"])
def test_discovery_failure_never_silently_switches_backend(monkeypatch, tmp_path, failure_at, backend):
    settings = Settings(BASE_URL="https://provider.example/v1", model="test", api_key="", crawl_backend=backend)
    store = Store(tmp_path / "failed.sqlite3")
    project = store.create_project("Discovery failure", "https://example.com/")["id"]
    def read(url, *args):
        if url.endswith(failure_at):
            raise CrawlError("HTTP 403")
        return url, "text/plain", b"User-agent: *\nAllow: /"
    monkeypatch.setattr("pagggle.crawl.fetch" if backend == "direct" else "pagggle.crawl.fetch_firecrawl", read)
    monkeypatch.setattr("pagggle.crawl.fetch_firecrawl" if backend == "direct" else "pagggle.crawl.fetch", lambda *a: pytest.fail("No backend fallback"))
    crawl_site(store, project, settings, lambda: False, lambda msg: None, discover_only=True)
    failed = next(s for s in store.sources(project) if s["url"].endswith(failure_at))
    assert failed["status"] == "failed" and failed["error"] == "HTTP 403"


def test_selected_page_and_robots_both_use_firecrawl(monkeypatch, tmp_path):
    settings = Settings(BASE_URL="https://provider.example/v1", model="test", api_key="", crawl_backend="firecrawl")
    store = Store(tmp_path / "selected.sqlite3")
    project = store.create_project("Selected page", "https://example.com/")["id"]
    calls = []
    def firecrawl(url, config, host):
        calls.append(("firecrawl", url))
        if url.endswith("robots.txt"):
            return url, "text/plain", b"User-agent: *\nAllow: /"
        assert url == "https://example.com/product" and host == "example.com"
        return url, "text/html", b"<p>We manufacture custom silicone gaskets for industrial applications.</p>"
    monkeypatch.setattr("pagggle.crawl.fetch", lambda *a: pytest.fail("Must not directly request target website"))
    monkeypatch.setattr("pagggle.crawl.fetch_firecrawl", firecrawl)
    crawl_site(store, project, settings, lambda: False, lambda msg: None, selected_urls=["https://example.com/product"])
    assert calls == [("firecrawl", "https://example.com/robots.txt"), ("firecrawl", "https://example.com/product")]
    assert next(s for s in store.sources(project) if s["kind"] == "page")["status"] == "read"


@pytest.mark.parametrize("challenge,expected", [("challenge", "SiteGround 人机验证拦截"), ("", "HTTP 202：未成功读取")])
def test_202_challenge_is_identified_without_parsing_it_as_site_content(monkeypatch, challenge, expected):
    class Response:
        status = 202
        def getheader(self, name, default=None):
            return challenge if name == "SG-Captcha" else default
        def read(self, *args):
            pytest.fail("Challenge response must not become site content")
    class Connection:
        def __init__(self, *args, **kwargs):
            self.closed = False
            connections.append(self)
        def request(self, *args, **kwargs):
            assert "Authorization" not in kwargs['headers']
        def getresponse(self):
            return Response()
        def close(self):
            self.closed = True
    connections = []
    monkeypatch.setattr("pagggle.crawl.PinnedHTTPS", Connection)
    monkeypatch.setattr("pagggle.crawl.public_address", lambda *a: "93.184.215.14")
    with pytest.raises(CrawlError, match=expected):
        fetch("https://example.com/robots.txt")
    assert len(connections) == 1 and connections[0].closed
