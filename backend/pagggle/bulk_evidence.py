"""Firecrawl batch transport; credentials never leave the fixed API origin."""
import hashlib
import time
from urllib.parse import urlsplit
from uuid import UUID

import httpx

from .crawl import CrawlError, MAX_BYTES, normalize_url, parse_html, public_address
from .store import now

API = 'https://api.firecrawl.dev/v2/batch/scrape'


def batch_evidence(wanted, settings, concurrency, checkpoint, on_start):
    owners = dict(wanted)
    for host in {urlsplit(u).hostname for u in owners}:
        public_address(host,443,settings.crawl_dns,settings.request_timeout_seconds)
    seen = set()
    batch_id = None
    terminal = False
    with httpx.Client(timeout=90,trust_env=False,follow_redirects=False,headers={'Authorization':'Bearer '+settings.firecrawl_api_key.get_secret_value()}) as client:
        def request(method,url,**kwargs):
            if urlsplit(url).netloc!='api.firecrawl.dev' or not url.startswith(API):
                raise CrawlError('批量采集返回了无效分页地址')
            for attempt in range(3):
                try:
                    response=client.request(method,url,**kwargs)
                except httpx.HTTPError:
                    raise CrawlError('批量采集连接失败；已保存页面可复用') from None
                if response.status_code==429 and attempt<2:
                    try:delay=min(120,max(10,int(response.headers.get('retry-after','30'))))
                    except ValueError:delay=30
                    for _ in range(delay):
                        checkpoint('2/5 采集服务限流，等待恢复；已读内容已保存')
                        time.sleep(1)
                    continue
                if response.status_code!=200:
                    raise CrawlError(f'批量采集服务返回 HTTP {response.status_code}；已保存页面可复用')
                try:return response.json()
                except ValueError:raise CrawlError('批量采集响应无效') from None
        try:
            checkpoint(f'2/5 提交 {len(owners)} 个未读页面至批量采集队列')
            created=request('POST',API,json={'urls':list(owners),'formats':['rawHtml'],'onlyMainContent':False,'maxAge':0,'maxConcurrency':concurrency,'timeout':settings.scrape_timeout_seconds*1000})
            if not created.get('success'):
                raise CrawlError('批量采集任务创建失败')
            batch_id=str(UUID(created['id']))
            on_start(batch_id)
            cursor=f'{API}/{batch_id}'
            while True:
                checkpoint(f'2/5 批量采集：已保存 {len(seen)}/{len(owners)} 页')
                payload=request('GET',cursor)
                for page in payload.get('data',[]):
                    meta=page.get('metadata',{})
                    source=meta.get('sourceURL') or meta.get('url')
                    if not source:
                        continue
                    url=normalize_url(source)
                    if url not in owners or url in seen:
                        continue
                    asset={'url':url,'title':url,'body':'','status':'failed','owner':owners[url],'fetched_at':now(),'batch_id':batch_id}
                    try:
                        final=normalize_url(meta.get('url') or url)
                        if urlsplit(final).hostname!=urlsplit(url).hostname:
                            raise CrawlError('采集结果跳转至其他域名')
                        if meta.get('statusCode')!=200:
                            raise CrawlError(f"目标页面返回 HTTP {meta.get('statusCode','未知')}")
                        raw=page.get('rawHtml','').encode()
                        if not raw or len(raw)>MAX_BYTES:
                            raise CrawlError('正文为空或超过单页大小限制')
                        title,body,links=parse_html(raw,final)
                        if len(body.strip())<40:
                            raise CrawlError('可读取正文不足')
                        asset.update(title=title,body=body,status='read',sha256=hashlib.sha256(raw).hexdigest(),links=links)
                    except CrawlError as error:
                        asset['error']=str(error)
                    seen.add(url)
                    yield asset
                next_url=payload.get('next')
                if payload.get('status') in {'completed','failed','cancelled'} and not next_url:
                    terminal=True
                    if payload.get('status')!='completed':
                        raise CrawlError('批量采集未完成；已保存页面可复用，请重试')
                    break
                if next_url:
                    cursor=next_url
                for _ in range(10):
                    checkpoint(f"2/5 批量采集：已保存 {len(seen)}/{len(owners)} 页，服务端完成 {payload.get('completed',0)} 页")
                    time.sleep(1)
            for url in sorted(set(owners)-seen):
                yield {'url':url,'title':url,'body':'','status':'failed','owner':owners[url],'fetched_at':now(),'batch_id':batch_id,'error':'批量任务未返回此 URL 的有效正文，需逐页复查'}
        finally:
            if batch_id and not terminal:
                try:client.delete(f'{API}/{batch_id}')
                except httpx.HTTPError:pass
