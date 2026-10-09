"""Bounded public-site collection. Pin DNS results for each HTTP connection."""

import http.client
import ipaddress
import socket
import httpx
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser
from xml.etree import ElementTree

from bs4 import BeautifulSoup

MAX_BYTES = 2_000_000
AGENT = "PagggleBot"


class CrawlError(Exception):
    pass


def normalize_url(value):
    try:
        p = urlsplit(value.strip())
        if p.scheme not in {"http", "https"} or not p.hostname or p.username or p.password or p.port not in {None, 80, 443}:
            raise ValueError
        host = p.hostname.encode("idna").decode("ascii").lower()
        if ":" in host:
            host = f"[{host}]"
        authority = host + (f":{p.port}" if p.port else "")
        return urlunsplit((p.scheme, authority, p.path or "/", "", ""))
    except (ValueError, UnicodeError):
        raise CrawlError("请输入不含账号密码的 HTTP(S) 网站地址，仅支持标准端口") from None


def public_address(host, port, dns_mode="system", timeout=30):
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    try:
        if literal:
            ips = [str(literal)]
        elif dns_mode == "cloudflare":
            ips = []
            with httpx.Client(timeout=timeout, follow_redirects=False, trust_env=False) as client:
                for record_type in (1, 28):
                    response = client.get("https://cloudflare-dns.com/dns-query", params={"name": host, "type": record_type}, headers={"Accept": "application/dns-json"})
                    response.raise_for_status()
                    data = response.json()
                    if data.get("Status") != 0 or data.get("TC"):
                        raise ValueError
                    ips = [str(ipaddress.ip_address(r["data"])) for r in data.get("Answer", []) if r["type"] == record_type]
                    if ips:
                        break
        else:
            addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
            ips = [entry[4][0] for entry in addresses]
    except (OSError, httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
        raise CrawlError("域名解析失败，可重试或补充文本资料") from None
    if any(ipaddress.ip_address(ip) in ipaddress.ip_network("198.18.0.0/15") for ip in ips):
        raise CrawlError("域名解析到了 198.18.0.0/15 测试地址，可能启用了代理 Fake-IP；请让目标域名使用真实 DNS 地址后重试，或先补充文本资料")
    if not ips or any(not ipaddress.ip_address(ip).is_global for ip in ips):
        raise CrawlError("只允许采集公网网站地址")
    return ips[0]


class PinnedHTTP(http.client.HTTPConnection):
    def connect(self):
        self.sock = socket.create_connection((self.address, self.port), self.timeout)


class PinnedHTTPS(http.client.HTTPSConnection):
    def connect(self):
        raw = socket.create_connection((self.address, self.port), self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except Exception:
            raw.close()
            raise


def fetch(url, timeout=30, allowed_host=None, dns_mode="system"):
    """Never follow redirects outside the selected hostname; never send credentials."""
    current = normalize_url(url)
    host = allowed_host or urlsplit(current).hostname
    for _ in range(6):
        p = urlsplit(current)
        if p.hostname != host:
            raise CrawlError("重定向到其他域名，请核对网站地址后重新创建项目")
        port = p.port or (443 if p.scheme == "https" else 80)
        address = public_address(p.hostname, port, dns_mode, timeout)
        conn = (PinnedHTTPS if p.scheme == "https" else PinnedHTTP)(p.hostname, port, timeout=timeout)
        conn.address = address
        try:
            conn.request("GET", p.path or "/", headers={"User-Agent": f"{AGENT}/0.1", "Accept-Encoding": "identity"})
            response = conn.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                location = response.getheader("Location")
                if not location:
                    raise CrawlError("重定向缺少目标地址")
                current = normalize_url(urljoin(current, location))
                continue
            if response.status != 200:
                if response.status == 202 and response.getheader("SG-Captcha", "").lower() == "challenge":
                    raise CrawlError("SiteGround 人机验证拦截（HTTP 202）：请先在浏览器完成该站点验证，或联系站点管理员放行采集来源；尚未读取到网站文件")
                raise CrawlError(f"HTTP {response.status}：未成功读取，可重试或补充文本资料")
            raw = response.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise CrawlError("页面超出 2 MB 限制，请补充关键内容")
            return current, response.getheader("Content-Type", ""), raw
        except (OSError, http.client.HTTPException):
            raise CrawlError("网络连接、证书或读取失败，可重试或补充文本资料") from None
        finally:
            conn.close()
    raise CrawlError("重定向次数超过限制")


def fetch_firecrawl(url, settings, allowed_host):
    url = normalize_url(url)
    if urlsplit(url).hostname != allowed_host:
        raise CrawlError("不采集其他域名")
    public_address(allowed_host, urlsplit(url).port or 443, settings.crawl_dns, settings.request_timeout_seconds)
    if not settings.firecrawl_api_key.get_secret_value():
        raise CrawlError("请在 config.json 中配置 firecrawl_api_key")
    try:
        with httpx.Client(timeout=settings.scrape_timeout_seconds + 5, trust_env=False, follow_redirects=False) as client:
            response = client.post("https://api.firecrawl.dev/v2/scrape", headers={"Authorization": "Bearer " + settings.firecrawl_api_key.get_secret_value()}, json={
                "url": url, "formats": ["rawHtml"], "onlyMainContent": False,
                "timeout": settings.scrape_timeout_seconds * 1000,
            })
        if response.status_code != 200:
            raise CrawlError(f"采集服务返回 HTTP {response.status_code}，请检查配置、额度或稍后重试")
        data = response.json()
        if data.get("success") is not True:
            raise CrawlError("采集服务未成功读取页面，请重试")
        page = data["data"]
        metadata = page["metadata"]
        if metadata.get("statusCode") != 200:
            raise CrawlError("目标页面未返回 HTTP 200，请重试或补充文本资料")
        final_url = normalize_url(metadata.get("url") or metadata["sourceURL"])
        if urlsplit(final_url).hostname != allowed_host:
            raise CrawlError("采集服务返回了其他域名的页面，结果未保存")
        raw = page["rawHtml"].encode("utf-8")
        if len(raw) > MAX_BYTES:
            raise CrawlError("页面超出 2 MB 限制，请补充关键内容")
        return final_url, metadata.get("contentType", "text/html"), raw
    except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
        raise CrawlError("采集服务连接失败或响应无效；凭据及服务响应未写入日志") from None


def parse_html(raw, base):
    soup = BeautifulSoup(raw, "html.parser")
    refresh = soup.find("meta", attrs={"http-equiv": lambda value: value and value.lower() == "refresh"})
    if refresh or soup.find("title", string=lambda value: value and any(x in value.lower() for x in ("just a moment", "captcha", "access denied"))):
        raise CrawlError("页面返回验证或跳转中转页，未作为业务内容保存；请调整采集方式或补充文本资料")
    title = soup.title.get_text(" ", strip=True) if soup.title else base
    links = []
    for tag in soup.find_all("a", href=True):
        try:
            url = normalize_url(urljoin(base, tag["href"]))
        except CrawlError:
            continue
        if urlsplit(url).hostname == urlsplit(base).hostname:
            links.append(url)
    for tag in soup(["script", "style", "noscript", "svg", "template"]):
        tag.decompose()
    text = "\n".join(line.strip() for line in soup.get_text("\n").splitlines() if line.strip())
    return title, text, list(dict.fromkeys(links))


def priority(url):
    path = urlsplit(url).path.lower()
    if any(word in path for word in ("product", "custom", "capabil", "case", "application", "about", "contact")):
        return 0
    return 2 if any(word in path for word in ("blog", "news")) else 1


def crawl_site(store, project_id, settings, cancelled, progress, selected_urls=None, discover_only=False):
    site = normalize_url(store.project(project_id)["site_url"])
    host = urlsplit(site).hostname
    def read_url(url):
        if settings.crawl_backend == "firecrawl":
            return fetch_firecrawl(url, settings, host)
        return fetch(url, settings.request_timeout_seconds, host, settings.crawl_dns)
    queue = list(selected_urls) if selected_urls is not None else [site]
    robots_url = urljoin(site, "/robots.txt")
    robots = RobotFileParser()
    sitemaps = [urljoin(site, "/sitemap.xml")] if selected_urls is None else []
    try:
        _, content_type, raw = read_url(robots_url)
        if "html" in content_type.lower():
            raise CrawlError("robots.txt 返回 HTML 中转页，无法核对采集规则；请检查网站访问限制后重试")
        robots.parse(raw.decode("utf-8", "replace").splitlines())
        if selected_urls is None:
            sitemaps = (robots.site_maps() or []) + sitemaps
        store.add_source(project_id, title="robots.txt", url=robots_url, body=raw.decode("utf-8", "replace"), kind="discovery")
    except CrawlError as e:
        store.add_source(project_id, title="robots.txt", url=robots_url, status="failed", error=str(e), kind="discovery")
        if "HTTP 404" not in str(e):
            store.add_source(project_id, title=site, url=site, status="failed", error="无法核对 robots.txt；请重试或提供文本资料", kind="page")
            return {"sitemaps_read": 0, "pages_discovered": 0, "error": f"robots.txt 读取失败：{e}"}
        robots.parse([])
    seen_maps = set()
    maps_read = 0
    while sitemaps and not cancelled():
        try:
            map_url = normalize_url(sitemaps.pop(0))
        except CrawlError:
            continue
        if map_url in seen_maps or urlsplit(map_url).hostname != host:
            continue
        seen_maps.add(map_url)
        progress(f"正在发现页面：已检查 {len(seen_maps)} 份站点地图，已发现 {len(set(queue))} 个页面链接")
        try:
            if not robots.can_fetch(AGENT, map_url):
                raise CrawlError("robots.txt 不允许读取此站点地图")
            _, _, raw = read_url(map_url)
            root = ElementTree.fromstring(raw)
            if root.tag.rsplit("}", 1)[-1] not in {"sitemapindex", "urlset"}:
                raise CrawlError("此文件不是 XML sitemap 索引或页面清单，未用于发现网页")
            urls = []
            for entry in root:
                if entry.tag.rsplit("}", 1)[-1] not in {"sitemap", "url"}:
                    continue
                for node in entry:
                    if node.tag.rsplit("}", 1)[-1] != "loc" or not node.text:
                        continue
                    try:
                        found = normalize_url(node.text)
                        if urlsplit(found).hostname == host:
                            urls.append(found)
                    except CrawlError:
                        continue
            if root.tag.rsplit("}", 1)[-1] == "sitemapindex":
                sitemaps.extend(urls)
            else:
                queue.extend(sorted(urls, key=priority))
            store.add_source(project_id, title="站点地图", url=map_url, kind="discovery", body=raw.decode("utf-8", "replace"))
            maps_read += 1
        except (CrawlError, ElementTree.ParseError) as e:
            store.add_source(project_id, title="站点地图", url=map_url, kind="discovery", status="failed", error=str(e) if isinstance(e, CrawlError) else "站点地图不是有效 XML")
    if discover_only:
        for url in dict.fromkeys(queue):
            allowed = robots.can_fetch(AGENT, url)
            store.add_source(project_id, title=url, url=url, kind="page", status="uncovered" if allowed else "excluded", error="已发现链接，等待用户筛选后采集正文" if allowed else "robots.txt 不允许采集")
        progress(f"已发现 {len(set(queue))} 个页面链接；请筛选页面，尚未采集正文或调用模型")
        return {"sitemaps_read": maps_read, "pages_discovered": len(set(queue))}
    seen, count = set(), 0
    while queue and (settings.crawl_max_pages is None or count < settings.crawl_max_pages) and not cancelled():
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        count += 1
        scope = "数量不限" if settings.crawl_max_pages is None else f"上限 {settings.crawl_max_pages} 页"
        progress(f"正在读取第 {count} 页，{scope}；已发现 {len(seen | set(queue))} 个页面链接")
        if not robots.can_fetch(AGENT, url):
            store.add_source(project_id, title=url, url=url, kind="page", status="excluded", error="robots.txt 不允许采集")
            continue
        try:
            final_url, content_type, raw = read_url(url)
            if "html" not in content_type.lower():
                raise CrawlError("此资源不是 HTML 页面，请通过文本补充资料")
            title, body, links = parse_html(raw, final_url)
            if len(body) < 40:
                raise CrawlError("可读取正文不足，可能需要动态渲染；请补充文本资料")
            store.add_source(project_id, title=title, url=url, kind="page", body=body)
            if selected_urls is None:
                queue = sorted(list(dict.fromkeys(queue + [link for link in links if link not in seen])), key=priority)
        except CrawlError as e:
            store.add_source(project_id, title=url, url=url, kind="page", status="failed", error=str(e))
    for url in dict.fromkeys(queue):
        if url not in seen:
            store.add_source(project_id, title=url, url=url, kind="page", status="uncovered", error="本轮页面上限或任务取消，尚未读取")
    progress(f"本轮处理 {count} 页；未覆盖与失败页面已单独记录")
