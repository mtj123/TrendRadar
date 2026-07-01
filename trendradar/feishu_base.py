# coding=utf-8
"""
飞书多维表格同步

为 TrendRadar 提供一个轻量的飞书 Base 同步器：
- 在现有 Base 中初始化/复用主表
- 补齐同步所需字段
- 将当天热榜和 RSS 数据 upsert 到主表

说明：
- 这里只同步一张主表，定位为“业务可读表”
- 字段类型以文本/数字为主，避免复杂字段配置导致初始化失败
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
import re
from zoneinfo import ZoneInfo
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests

from trendradar.core.frequency import load_frequency_words, matches_word_groups
from trendradar.github_repo_search import GitHubRepoSearchClient, GitHubRepoSearchConfig
from trendradar.storage.base import NewsData, NewsItem, RSSData, RSSItem


DEFAULT_FEISHU_OPEN_BASE = "https://open.feishu.cn"
PLACEHOLDER_MARKERS = ("xxx", "example", "your_", "replace_me")
TABLE_MODE_STANDARD = "standard"
TABLE_MODE_BUSINESS = "business"
BUSINESS_REQUIRED_FIELDS = {"标题", "来源", "发布时间", "标签", "摘要", "链接", "重要程度"}
BUSINESS_OPTIONAL_FIELD_SPECS: List[Dict[str, Any]] = [
    {"field_name": "Stars", "type": 2},
    {"field_name": "Forks", "type": 2},
]


@dataclass
class FeishuBaseConfig:
    enabled: bool
    app_id: str
    app_secret: str
    app_token: str
    main_table_id: str
    main_table_name: str
    auto_init_table: bool
    open_base: str = DEFAULT_FEISHU_OPEN_BASE

    @classmethod
    def from_config(cls, config: Dict[str, Any]) -> "FeishuBaseConfig":
        base_cfg = config.get("FEISHU_BASE", {})
        return cls(
            enabled=bool(base_cfg.get("ENABLED", False)),
            app_id=base_cfg.get("APP_ID", ""),
            app_secret=base_cfg.get("APP_SECRET", ""),
            app_token=base_cfg.get("APP_TOKEN", ""),
            main_table_id=base_cfg.get("MAIN_TABLE_ID", ""),
            main_table_name=base_cfg.get("MAIN_TABLE_NAME", "AI行业动态表"),
            auto_init_table=bool(base_cfg.get("AUTO_INIT_TABLE", True)),
            open_base=base_cfg.get("OPEN_BASE", DEFAULT_FEISHU_OPEN_BASE).rstrip("/"),
        )

    def is_usable(self) -> bool:
        return self.enabled and self.has_real_credentials()

    def has_real_credentials(self) -> bool:
        return all(
            [
                _looks_like_real_value(self.app_id),
                _looks_like_real_value(self.app_secret),
                _looks_like_real_value(self.app_token),
            ]
        )


FIELD_SPECS: List[Dict[str, Any]] = [
    {"field_name": "动态ID", "type": 1},
    {"field_name": "抓取日期", "type": 1},
    {"field_name": "标题", "type": 1},
    {"field_name": "来源", "type": 1},
    {"field_name": "来源类型", "type": 1},
    {"field_name": "来源ID", "type": 1},
    {"field_name": "发布时间", "type": 1},
    {"field_name": "标签", "type": 1},
    {"field_name": "摘要", "type": 1},
    {"field_name": "作者", "type": 1},
    {"field_name": "原始链接", "type": 1},
    {"field_name": "移动端链接", "type": 1},
    {"field_name": "仓库名", "type": 1},
    {"field_name": "仓库链接", "type": 1},
    {"field_name": "语言", "type": 1},
    {"field_name": "平台内排名", "type": 2},
    {"field_name": "抓取时间", "type": 1},
    {"field_name": "首次出现时间", "type": 1},
    {"field_name": "最后出现时间", "type": 1},
    {"field_name": "抓取次数", "type": 2},
    {"field_name": "Stars", "type": 2},
    {"field_name": "Forks", "type": 2},
    {"field_name": "状态", "type": 1},
]


class FeishuBaseError(RuntimeError):
    pass


class FeishuBaseClient:
    def __init__(self, cfg: FeishuBaseConfig, timeout: int = 30):
        self.cfg = cfg
        self.timeout = timeout
        self._tenant_access_token: Optional[str] = None

    def _tenant_token(self) -> str:
        if self._tenant_access_token:
            return self._tenant_access_token

        url = f"{self.cfg.open_base}/open-apis/auth/v3/tenant_access_token/internal"
        response = requests.post(
            url,
            json={
                "app_id": self.cfg.app_id,
                "app_secret": self.cfg.app_secret,
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") not in (0, None):
            raise FeishuBaseError(f"获取 tenant_access_token 失败: {payload}")
        token = payload.get("tenant_access_token")
        if not token:
            raise FeishuBaseError(f"飞书响应中缺少 tenant_access_token: {payload}")
        self._tenant_access_token = token
        return token

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json_data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        url = f"{self.cfg.open_base}{path}"
        headers = {
            "Authorization": f"Bearer {self._tenant_token()}",
            "Content-Type": "application/json; charset=utf-8",
        }
        response = requests.request(
            method=method,
            url=url,
            headers=headers,
            params=params,
            json=json_data,
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") not in (0, None):
            raise FeishuBaseError(f"飞书 API 调用失败: {path} -> {payload}")
        return payload.get("data", {})

    def list_tables(self) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        page_token = None
        while True:
            params: Dict[str, Any] = {"page_size": 100}
            if page_token:
                params["page_token"] = page_token
            data = self._request(
                "GET",
                f"/open-apis/bitable/v1/apps/{self.cfg.app_token}/tables",
                params=params,
            )
            items.extend(data.get("items") or [])
            if not data.get("has_more"):
                break
            page_token = data.get("page_token")
        return items

    def create_table(self, table_name: str) -> Dict[str, Any]:
        return self._request(
            "POST",
            f"/open-apis/bitable/v1/apps/{self.cfg.app_token}/tables",
            json_data={"table": {"name": table_name}},
        ).get("table", {})

    def list_fields(self, table_id: str) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        page_token = None
        while True:
            params: Dict[str, Any] = {"page_size": 100}
            if page_token:
                params["page_token"] = page_token
            data = self._request(
                "GET",
                f"/open-apis/bitable/v1/apps/{self.cfg.app_token}/tables/{table_id}/fields",
                params=params,
            )
            items.extend(data.get("items") or [])
            if not data.get("has_more"):
                break
            page_token = data.get("page_token")
        return items

    def create_field(self, table_id: str, field_spec: Dict[str, Any]) -> Dict[str, Any]:
        return self._request(
            "POST",
            f"/open-apis/bitable/v1/apps/{self.cfg.app_token}/tables/{table_id}/fields",
            json_data=field_spec,
        ).get("field", {})

    def list_records(self, table_id: str, field_names: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        page_token = None
        while True:
            params: Dict[str, Any] = {"page_size": 500}
            if page_token:
                params["page_token"] = page_token
            if field_names:
                params["field_names"] = field_names
            data = self._request(
                "GET",
                f"/open-apis/bitable/v1/apps/{self.cfg.app_token}/tables/{table_id}/records",
                params=params,
            )
            items.extend(data.get("items") or [])
            if not data.get("has_more"):
                break
            page_token = data.get("page_token")
        return items

    def batch_create_records(self, table_id: str, records: List[Dict[str, Any]]) -> None:
        if not records:
            return
        self._request(
            "POST",
            f"/open-apis/bitable/v1/apps/{self.cfg.app_token}/tables/{table_id}/records/batch_create",
            json_data={"records": records},
        )

    def batch_update_records(self, table_id: str, records: List[Dict[str, Any]]) -> None:
        if not records:
            return
        self._request(
            "POST",
            f"/open-apis/bitable/v1/apps/{self.cfg.app_token}/tables/{table_id}/records/batch_update",
            json_data={"records": records},
        )


class FeishuBaseSyncService:
    def __init__(self, config: Dict[str, Any]):
        self.raw_config = config
        self.cfg = FeishuBaseConfig.from_config(config)
        self.client = FeishuBaseClient(self.cfg)
        self._table_mode = TABLE_MODE_STANDARD
        self._field_map: Dict[str, Dict[str, Any]] = {}
        self._timezone = self.raw_config.get("TIMEZONE", "Asia/Shanghai") or "Asia/Shanghai"

    def is_enabled(self) -> bool:
        return self.cfg.is_usable()

    def ensure_table_and_fields(self) -> Tuple[str, List[str]]:
        if not self.is_enabled():
            raise FeishuBaseError("飞书 Base 配置不完整，无法初始化")

        table_id = self.cfg.main_table_id
        if not table_id:
            for table in self.client.list_tables():
                if table.get("name") == self.cfg.main_table_name:
                    table_id = table.get("table_id", "")
                    break

        if not table_id:
            if not self.cfg.auto_init_table:
                raise FeishuBaseError(
                    f"未找到数据表 {self.cfg.main_table_name}，且已关闭 auto_init_table"
                )
            table = self.client.create_table(self.cfg.main_table_name)
            table_id = table.get("table_id", "")
            if not table_id:
                raise FeishuBaseError("创建飞书数据表失败，未返回 table_id")

        fields = self.client.list_fields(table_id)
        self._field_map = {field.get("field_name", ""): field for field in fields}
        existing_names = set(self._field_map.keys())
        self._table_mode = self._detect_table_mode(existing_names)

        # 现有业务展示表优先复用已有字段，仅补充业务表专用的可选字段
        if self._table_mode == TABLE_MODE_BUSINESS:
            created_fields: List[str] = []
            for field_spec in BUSINESS_OPTIONAL_FIELD_SPECS:
                if field_spec["field_name"] in existing_names:
                    continue
                try:
                    self.client.create_field(table_id, field_spec)
                    created_fields.append(field_spec["field_name"])
                    existing_names.add(field_spec["field_name"])
                except requests.exceptions.HTTPError as exc:
                    response = getattr(exc, "response", None)
                    payload = {}
                    if response is not None:
                        try:
                            payload = response.json()
                        except Exception:
                            payload = {}
                    if payload.get("code") == 1254014:
                        existing_names.add(field_spec["field_name"])
                        continue
                    raise
            self.cfg.main_table_id = table_id
            return table_id, created_fields

        created_fields: List[str] = []
        for field_spec in FIELD_SPECS:
            if field_spec["field_name"] in existing_names:
                continue
            try:
                self.client.create_field(table_id, field_spec)
                created_fields.append(field_spec["field_name"])
                existing_names.add(field_spec["field_name"])
            except requests.exceptions.HTTPError as exc:
                response = getattr(exc, "response", None)
                payload = {}
                if response is not None:
                    try:
                        payload = response.json()
                    except Exception:
                        payload = {}
                if payload.get("code") == 1254014:
                    existing_names.add(field_spec["field_name"])
                    continue
                raise

        self.cfg.main_table_id = table_id
        return table_id, created_fields

    def sync_today(
        self,
        news_data: Optional[NewsData],
        rss_data: Optional[RSSData],
        github_repos: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        table_id, created_fields = self.ensure_table_and_fields()

        all_rows = self._build_rows(news_data, rss_data, github_repos)
        if not all_rows:
            return {
                "table_id": table_id,
                "created_fields": created_fields,
                "created": 0,
                "updated": 0,
                "rows": 0,
            }

        existing = self.client.list_records(table_id)
        record_map: Dict[str, str] = {}
        for item in existing:
            fields = item.get("fields", {})
            row_key = self._record_key(fields)
            if row_key:
                record_map[row_key] = item.get("record_id", "")

        creates: List[Dict[str, Any]] = []
        updates: List[Dict[str, Any]] = []
        for row in all_rows:
            row_key = self._record_key(row["fields"])
            if not row_key:
                continue
            record_id = record_map.get(row_key)
            if record_id:
                updates.append({"record_id": record_id, "fields": row["fields"]})
            else:
                creates.append(row)

        for batch in _chunked(creates, 1000):
            self.client.batch_create_records(table_id, batch)
        for batch in _chunked(updates, 1000):
            self.client.batch_update_records(table_id, batch)

        return {
            "table_id": table_id,
            "created_fields": created_fields,
            "created": len(creates),
            "updated": len(updates),
            "rows": len(all_rows),
        }

    def preview_today(
        self,
        news_data: Optional[NewsData],
        rss_data: Optional[RSSData],
        github_repos: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        rows, by_source_type = self._collect_rows(news_data, rss_data, github_repos)
        return {
            "rows": len(rows),
            "sample": rows[:10],
            "by_source_type": by_source_type,
            "table_mode": self._table_mode,
        }

    def _build_rows(
        self,
        news_data: Optional[NewsData],
        rss_data: Optional[RSSData],
        github_repos: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        rows, _ = self._collect_rows(news_data, rss_data, github_repos)
        return rows

    def _collect_rows(
        self,
        news_data: Optional[NewsData],
        rss_data: Optional[RSSData],
        github_repos: Optional[List[Dict[str, Any]]] = None,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
        rows: List[Dict[str, Any]] = []
        by_source_type: Dict[str, int] = {}
        today_local = _today_local_date(self._timezone)
        allowed_platform_ids = {
            item.get("id", "")
            for item in self.raw_config.get("PLATFORMS", [])
            if item.get("enabled", True)
        }
        allowed_feed_ids = {
            item.get("id", "")
            for item in self.raw_config.get("RSS", {}).get("FEEDS", [])
            if item.get("enabled", True)
        }
        frequency_file = self.raw_config.get("FREQUENCY_FILE")
        word_groups, filter_words, global_filters = load_frequency_words(frequency_file)

        if news_data:
            for source_id, items in news_data.items.items():
                if allowed_platform_ids and source_id not in allowed_platform_ids:
                    continue
                if not allowed_platform_ids:
                    continue
                source_name = news_data.id_to_name.get(source_id, source_id)
                for item in items:
                    if not matches_word_groups(item.title, word_groups, filter_words, global_filters):
                        continue
                    if self._table_mode == TABLE_MODE_BUSINESS:
                        published_candidate = _to_feishu_timestamp(_merge_date_time(news_data.date, item.crawl_time))
                        if not _is_same_local_day(published_candidate, today_local, self._timezone):
                            continue
                    row = {"fields": self._build_hotlist_fields(news_data.date, source_id, source_name, item)}
                    rows.append(row)
                    by_source_type["热榜"] = by_source_type.get("热榜", 0) + 1

        if rss_data:
            for feed_id, items in rss_data.items.items():
                if allowed_feed_ids and feed_id not in allowed_feed_ids:
                    continue
                if not allowed_feed_ids:
                    continue
                feed_name = rss_data.id_to_name.get(feed_id, feed_id)
                for item in items:
                    if not matches_word_groups(item.title, word_groups, filter_words, global_filters):
                        continue
                    if self._table_mode == TABLE_MODE_BUSINESS:
                        published_candidate = _to_feishu_timestamp(item.published_at) or _to_feishu_timestamp(
                            _merge_date_time(rss_data.date, item.crawl_time)
                        )
                        if not _is_same_local_day(published_candidate, today_local, self._timezone):
                            continue
                    row = {"fields": self._build_rss_fields(rss_data.date, feed_id, feed_name, item)}
                    rows.append(row)
                    by_source_type["RSS"] = by_source_type.get("RSS", 0) + 1

        if github_repos:
            repo_date = datetime.now().strftime("%Y-%m-%d")
            for repo in github_repos:
                if self._table_mode == TABLE_MODE_BUSINESS:
                    published_candidate = _to_feishu_timestamp(
                        repo.get("pushed_at", "") or repo.get("updated_at", "") or repo.get("created_at", "")
                    )
                    if not _is_same_local_day(published_candidate, today_local, self._timezone):
                        continue
                row = {"fields": self._build_github_repo_fields(repo_date, repo)}
                rows.append(row)
                by_source_type["GitHubRepo"] = by_source_type.get("GitHubRepo", 0) + 1

        if self._table_mode == TABLE_MODE_BUSINESS:
            rows.sort(key=lambda row: _business_sort_key(row.get("fields", {})), reverse=True)

        return rows, by_source_type

    def _build_hotlist_fields(
        self,
        date_str: str,
        source_id: str,
        source_name: str,
        item: NewsItem,
    ) -> Dict[str, Any]:
        if self._table_mode == TABLE_MODE_BUSINESS:
            published = _to_feishu_timestamp(_merge_date_time(date_str, item.crawl_time))
            return self._build_business_fields(
                title=item.title,
                source=source_name,
                published_at=published,
                summary="",
                url=item.url or item.mobile_url or "",
                tags=_infer_business_tags(item.title, source_name, ""),
                importance=_infer_importance(item.title, source_name, "", stars=0),
                stars=None,
                forks=None,
            )
        latest_rank = _latest_rank(item)
        return _strip_none(
            {
                "动态ID": _dynamic_id("hotlist", date_str, source_id, item.title),
                "抓取日期": date_str,
                "标题": item.title,
                "来源": source_name,
                "来源类型": "热榜",
                "来源ID": source_id,
                "发布时间": "",
                "标签": "",
                "摘要": "",
                "作者": "",
                "原始链接": item.url or "",
                "移动端链接": item.mobile_url or "",
                "仓库名": "",
                "仓库链接": "",
                "语言": "",
                "平台内排名": latest_rank,
                "抓取时间": _merge_date_time(date_str, item.crawl_time),
                "首次出现时间": _merge_date_time(date_str, item.first_time or item.crawl_time),
                "最后出现时间": _merge_date_time(date_str, item.last_time or item.crawl_time),
                "抓取次数": item.count or 1,
                "Stars": "",
                "Forks": "",
                "状态": "跟踪中",
            }
        )

    def _build_rss_fields(
        self,
        date_str: str,
        feed_id: str,
        feed_name: str,
        item: RSSItem,
    ) -> Dict[str, Any]:
        if self._table_mode == TABLE_MODE_BUSINESS:
            published = _to_feishu_timestamp(item.published_at) or _to_feishu_timestamp(
                _merge_date_time(date_str, item.crawl_time)
            )
            title = _translate_to_chinese(item.title)
            summary = _summarize_rss_in_chinese(item.title, item.summary or "")
            return self._build_business_fields(
                title=title,
                source=_humanize_feed_source(feed_name),
                published_at=published,
                summary=summary,
                url=item.url or "",
                tags=_infer_business_tags(title, feed_name, summary),
                importance=_infer_importance(title, feed_name, summary, stars=0),
                stars=None,
                forks=None,
            )
        stable_ref = item.guid or item.url or item.title
        return _strip_none(
            {
                "动态ID": _dynamic_id("rss", date_str, feed_id, stable_ref),
                "抓取日期": date_str,
                "标题": item.title,
                "来源": feed_name,
                "来源类型": "RSS",
                "来源ID": feed_id,
                "发布时间": item.published_at or "",
                "标签": "",
                "摘要": item.summary or "",
                "作者": item.author or "",
                "原始链接": item.url or "",
                "移动端链接": "",
                "仓库名": "",
                "仓库链接": "",
                "语言": "",
                "平台内排名": "",
                "抓取时间": _merge_date_time(date_str, item.crawl_time),
                "首次出现时间": _merge_date_time(date_str, item.first_time or item.crawl_time),
                "最后出现时间": _merge_date_time(date_str, item.last_time or item.crawl_time),
                "抓取次数": item.count or 1,
                "Stars": "",
                "Forks": "",
                "状态": "待研判",
            }
        )

    def _build_github_repo_fields(self, date_str: str, repo: Dict[str, Any]) -> Dict[str, Any]:
        full_name = repo.get("full_name", "")
        owner = (repo.get("owner") or {}).get("login", "")
        pushed_at = repo.get("pushed_at", "") or repo.get("updated_at", "")
        if self._table_mode == TABLE_MODE_BUSINESS:
            summary = _humanize_repo_summary(repo)
            title = _humanize_repo_title(repo)
            source = _humanize_repo_source(repo)
            published = _to_feishu_timestamp(pushed_at or repo.get("created_at", ""))
            url = repo.get("html_url", "") or ""
            tags = _infer_business_tags(title, "GitHub", summary, repo.get("topics") or [])
            importance = _infer_importance(title, source, summary, stars=repo.get("stargazers_count", 0) or 0)
            return self._build_business_fields(
                title=title,
                source=source,
                published_at=published,
                summary=summary,
                url=url,
                tags=tags,
                importance=importance,
                stars=repo.get("stargazers_count", 0) or 0,
                forks=repo.get("forks_count", 0) or 0,
            )
        return _strip_none(
            {
                "动态ID": _dynamic_id("github_repo", date_str, "github", full_name or repo.get("html_url", "")),
                "抓取日期": date_str,
                "标题": repo.get("name", full_name),
                "来源": "GitHub",
                "来源类型": "GitHubRepo",
                "来源ID": "github",
                "发布时间": repo.get("created_at", ""),
                "标签": ",".join(repo.get("topics", [])[:8]) if repo.get("topics") else "",
                "摘要": repo.get("description", "") or "",
                "作者": owner,
                "原始链接": repo.get("html_url", "") or "",
                "移动端链接": "",
                "仓库名": full_name,
                "仓库链接": repo.get("html_url", "") or "",
                "语言": repo.get("language", "") or "",
                "平台内排名": "",
                "抓取时间": _merge_date_time(date_str, datetime.now().strftime("%H:%M")),
                "首次出现时间": pushed_at or date_str,
                "最后出现时间": pushed_at or date_str,
                "抓取次数": 1,
                "Stars": repo.get("stargazers_count", 0) or 0,
                "Forks": repo.get("forks_count", 0) or 0,
                "状态": "仓库跟踪",
            }
        )

    def search_github_repos(self) -> List[Dict[str, Any]]:
        cfg = GitHubRepoSearchConfig.from_config(self.raw_config)
        if not cfg.enabled:
            return []
        return GitHubRepoSearchClient(cfg).search()

    def _detect_table_mode(self, existing_names: set[str]) -> str:
        if BUSINESS_REQUIRED_FIELDS.issubset(existing_names) and "来源类型" not in existing_names:
            return TABLE_MODE_BUSINESS
        return TABLE_MODE_STANDARD

    def _record_key(self, fields: Dict[str, Any]) -> Optional[str]:
        if self._table_mode == TABLE_MODE_BUSINESS:
            link = _extract_link_url(fields.get("链接"))
            if link:
                return f"url|{link}"
            title = str(fields.get("标题", "") or "").strip()
            source = str(fields.get("来源", "") or "").strip()
            published = fields.get("发布时间")
            if isinstance(published, dict):
                published = published.get("value") or published.get("timestamp")
            if not title:
                return None
            return f"{title}|{source}|{published or ''}"

        dynamic_id = fields.get("动态ID")
        if dynamic_id is None or dynamic_id == "":
            return None
        return str(dynamic_id)

    def _build_business_fields(
        self,
        *,
        title: str,
        source: str,
        published_at: Optional[int],
        summary: str,
        url: str,
        tags: List[str],
        importance: str,
        stars: Optional[int],
        forks: Optional[int],
    ) -> Dict[str, Any]:
        fields: Dict[str, Any] = {
            "标题": title,
            "来源": source,
            "摘要": summary,
            "重要程度": importance,
        }
        if published_at is not None:
            fields["发布时间"] = published_at
        if tags:
            fields["标签"] = tags
        if url:
            fields["链接"] = {"text": url, "link": url}
        if stars is not None:
            fields["Stars"] = stars
        if forks is not None:
            fields["Forks"] = forks
        return fields


def _dynamic_id(source_type: str, date_str: str, source_id: str, raw_key: str) -> str:
    digest = hashlib.sha1(raw_key.encode("utf-8")).hexdigest()[:12]
    return f"{date_str}:{source_type}:{source_id}:{digest}"


def _merge_date_time(date_str: str, time_str: str) -> str:
    if not time_str:
        return date_str
    normalized = time_str.replace("时", ":").replace("分", "").replace("-", ":")
    return f"{date_str} {normalized}"


def _latest_rank(item: NewsItem) -> Optional[int]:
    if item.rank_timeline:
        for node in reversed(item.rank_timeline):
            rank = node.get("rank")
            if isinstance(rank, int):
                return rank
    if item.ranks:
        return item.ranks[-1]
    if item.rank:
        return item.rank
    return None


def _strip_none(fields: Dict[str, Any]) -> Dict[str, Any]:
    cleaned: Dict[str, Any] = {}
    for key, value in fields.items():
        if value is None:
            continue
        if value == "":
            continue
        if isinstance(value, datetime):
            cleaned[key] = value.isoformat()
        else:
            cleaned[key] = value
    return cleaned


def _chunked(items: List[Dict[str, Any]], size: int) -> Iterable[List[Dict[str, Any]]]:
    for index in range(0, len(items), size):
        yield items[index:index + size]


def _looks_like_real_value(value: str) -> bool:
    if not value:
        return False
    normalized = value.strip().lower()
    if len(normalized) < 8:
        return False
    return not any(marker in normalized for marker in PLACEHOLDER_MARKERS)


def _to_feishu_timestamp(value: str) -> Optional[int]:
    if not value:
        return None

    normalized = value.strip().replace("T", " ").replace("Z", "+00:00")
    try:
        if "+" in normalized[-6:] or normalized.endswith("00:00"):
            dt = datetime.fromisoformat(normalized)
        else:
            for fmt in (
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d %H:%M",
                "%Y-%m-%d",
                "%Y/%m/%d %H:%M:%S",
                "%Y/%m/%d %H:%M",
                "%Y/%m/%d",
            ):
                try:
                    dt = datetime.strptime(normalized, fmt)
                    break
                except ValueError:
                    dt = None
            if dt is None:
                return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except ValueError:
        return None


def _infer_business_tags(
    title: str,
    source: str,
    summary: str,
    extra_topics: Optional[List[str]] = None,
) -> List[str]:
    text = " ".join(filter(None, [title, source, summary, " ".join(extra_topics or [])])).lower()

    rules = [
        ("大模型", ["gpt", "claude", "gemini", "qwen", "deepseek", "大模型", "foundation model"]),
        ("生成式AI", ["生成式", "generative", "diffusion"]),
        ("AIGC", ["aigc", "content generation", "创作"]),
        ("LLM", ["llm", "language model", "langchain"]),
        ("多模态", ["multimodal", "多模态", "vision", "audio", "video"]),
        ("AI应用", ["agent", "应用", "workflow", "mcp", "assistant", "automation"]),
        ("AI伦理", ["ethic", "safety", "alignment", "bias", "responsible ai"]),
        ("AI监管", ["监管", "policy", "compliance", "regulation", "法案"]),
    ]

    tags = [label for label, keywords in rules if any(keyword in text for keyword in keywords)]
    return tags[:3] if tags else ["其他"]


def _infer_importance(title: str, source: str, summary: str, stars: int = 0) -> str:
    text = " ".join(filter(None, [title, source, summary])).lower()
    action_keywords = {
        "发布": 2,
        "release": 2,
        "launched": 2,
        "推出": 2,
        "开源": 2,
        "funding": 3,
        "融资": 3,
        "收购": 3,
        "并购": 3,
        "regulation": 3,
        "监管": 3,
        "法案": 3,
        "benchmark": 2,
        "评测": 2,
        "安全": 2,
    }
    major_sources = ["openai", "anthropic", "google", "meta", "microsoft", "hugging face", "github", "nvidia"]
    medium_topics = ["agent", "mcp", "llm", "多模态", "workflow", "copilot", "rag", "推理", "部署"]

    score = 0
    for keyword, weight in action_keywords.items():
        if keyword in text:
            score += weight
    if any(keyword in text for keyword in major_sources):
        score += 2
    if any(keyword in text for keyword in medium_topics):
        score += 1

    if stars >= 20000:
        score += 3
    elif stars >= 5000:
        score += 2
    elif stars >= 1000:
        score += 1

    if score >= 5:
        return "高"
    if score >= 2:
        return "中"
    return "低"


def _humanize_repo_title(repo: Dict[str, Any]) -> str:
    full_name = repo.get("full_name", "") or repo.get("name", "") or "GitHub项目"
    repo_name = repo.get("name", "") or full_name
    summary = _clean_text(repo.get("description") or "")
    text = _repo_signal_text(repo)
    stars = repo.get("stargazers_count", 0) or 0

    if not summary:
        return f"{repo_name} 项目更新"

    if any(keyword in text for keyword in ("claude code", "codex", "ai coding", "coding agent", "coding assistant")):
        return f"{repo_name} 发布 AI 编程助手能力"
    if any(keyword in text for keyword in ("model context protocol", "mcp server", "mcp", "context protocol")):
        return f"{repo_name} 更新 MCP 接入能力"
    if any(keyword in text for keyword in ("workflow", "orchestrator", "langgraph", "multi-agent")):
        return f"{repo_name} 发布多智能体工作流方案"
    if any(keyword in text for keyword in ("inference", "serving", "deployment toolkit", "vlm", "llm-serving")):
        return f"{repo_name} 更新模型推理部署能力"
    if any(keyword in text for keyword in ("security", "safety", "firewall", "default-deny")):
        return f"{repo_name} 发布 AI Agent 安全方案"
    if any(keyword in text for keyword in ("memory", "semantic memory", "contextual knowledge")):
        return f"{repo_name} 发布 Agent 记忆组件"
    if any(keyword in text for keyword in ("story", "novel", "script", "creative-writing", "fiction")):
        return f"{repo_name} 发布 AI 内容创作工具"
    if any(keyword in text for keyword in ("assistant", "desktop", "all-in-one")):
        return f"{repo_name} 发布 AI 助手平台"
    if stars >= 10000:
        return f"{repo_name} 热度上升，关注度持续走高"
    return f"{repo_name} 发布 AI 工具更新"


def _humanize_repo_source(repo: Dict[str, Any]) -> str:
    owner = (repo.get("owner") or {}).get("login", "") or ""
    if owner:
        return f"GitHub开源 / {owner}"
    full_name = repo.get("full_name", "")
    if "/" in full_name:
        return f"GitHub开源 / {full_name.split('/', 1)[0]}"
    return "GitHub开源"


def _humanize_feed_source(feed_name: str) -> str:
    normalized = (feed_name or "").strip().lower()
    mapping = {
        "openai news": "OpenAI / 官方动态",
        "github blog": "GitHub / 官方博客",
        "hugging face blog": "Hugging Face / 官方博客",
        "hacker news": "Hacker News",
    }
    return mapping.get(normalized, feed_name or "RSS")


def _humanize_repo_summary(repo: Dict[str, Any]) -> str:
    repo_name = repo.get("name", "") or repo.get("full_name", "") or "该项目"
    text = _repo_signal_text(repo)
    stars = repo.get("stargazers_count", 0) or 0
    star_text = ""
    if stars >= 10000:
        star_text = "，社区关注度较高"
    elif stars >= 1000:
        star_text = "，已有一定开源关注度"

    if any(keyword in text for keyword in ("claude code", "codex", "ai coding", "coding agent", "coding assistant")):
        return f"{repo_name} 聚焦 AI 编程助手与开发流程自动化{star_text}。"
    if any(keyword in text for keyword in ("model context protocol", "mcp server", "mcp", "context protocol")):
        return f"{repo_name} 提供 MCP 协议接入或扩展能力，适合连接模型与外部工具{star_text}。"
    if any(keyword in text for keyword in ("workflow", "orchestrator", "langgraph", "multi-agent")):
        return f"{repo_name} 面向多智能体编排与工作流自动化场景{star_text}。"
    if any(keyword in text for keyword in ("inference", "serving", "deployment toolkit", "vlm", "llm-serving")):
        return f"{repo_name} 关注大模型推理、服务化或部署效率优化{star_text}。"
    if any(keyword in text for keyword in ("security", "safety", "firewall", "default-deny")):
        return f"{repo_name} 关注 AI Agent 安全治理、权限控制或防护能力{star_text}。"
    if any(keyword in text for keyword in ("memory", "semantic memory", "contextual knowledge")):
        return f"{repo_name} 侧重 Agent 记忆管理与上下文检索能力{star_text}。"
    if any(keyword in text for keyword in ("story", "novel", "script", "creative-writing", "fiction")):
        return f"{repo_name} 面向 AI 内容创作、剧本或长文本生成场景{star_text}。"
    return f"{repo_name} 是近期活跃的 AI 开源项目，覆盖模型应用或工具链能力{star_text}。"


def _summarize_rss_in_chinese(title: str, summary: str) -> str:
    clean_summary = _clean_text(summary or "")
    if not clean_summary:
        translated_title = _translate_to_chinese(title)
        return f"{translated_title}。"
    translated = _translate_to_chinese(clean_summary[:420])
    if len(translated) > 88:
        translated = translated[:88].rstrip("，。；、 ") + "。"
    return translated


def _business_sort_key(fields: Dict[str, Any]) -> int:
    source = str(fields.get("来源", "") or "")
    source_priority = 1
    if "OpenAI / 官方动态" in source:
        source_priority = 5
    elif "GitHub / 官方博客" in source or "Hugging Face / 官方博客" in source:
        source_priority = 4
    elif "Hacker News" in source:
        source_priority = 3

    published = fields.get("发布时间")
    if isinstance(published, int):
        return source_priority * 10**15 + published
    if isinstance(published, str):
        return source_priority * 10**15 + (_to_feishu_timestamp(published) or 0)
    if isinstance(published, dict):
        raw_value = published.get("timestamp") or published.get("value")
        if isinstance(raw_value, int):
            return source_priority * 10**15 + raw_value
        if isinstance(raw_value, str):
            return source_priority * 10**15 + (_to_feishu_timestamp(raw_value) or 0)
    return source_priority * 10**15


def _repo_signal_text(repo: Dict[str, Any]) -> str:
    return " ".join(
        filter(
            None,
            [
                repo.get("name", "") or "",
                repo.get("full_name", "") or "",
                repo.get("description", "") or "",
                " ".join(repo.get("topics", []) or []),
            ],
        )
    ).lower()


def _clean_text(text: str) -> str:
    cleaned = re.sub(r"[^\w\s\u4e00-\u9fff\-:,.()/+]+", " ", text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -:,.")
    return cleaned


def _extract_link_url(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("link") or value.get("text") or "").strip()
    if isinstance(value, str):
        return value.strip()
    return ""


def _today_local_date(timezone_name: str) -> str:
    return datetime.now(ZoneInfo(timezone_name)).strftime("%Y-%m-%d")


def _is_same_local_day(value: Any, expected_date: str, timezone_name: str) -> bool:
    if value is None or value == "":
        return False
    tz = ZoneInfo(timezone_name)
    if isinstance(value, int):
        dt = datetime.fromtimestamp(value / 1000, timezone.utc).astimezone(tz)
        return dt.strftime("%Y-%m-%d") == expected_date
    if isinstance(value, str):
        timestamp = _to_feishu_timestamp(value)
        if timestamp is None:
            return False
        dt = datetime.fromtimestamp(timestamp / 1000, timezone.utc).astimezone(tz)
        return dt.strftime("%Y-%m-%d") == expected_date
    if isinstance(value, dict):
        raw = value.get("timestamp") or value.get("value")
        return _is_same_local_day(raw, expected_date, timezone_name)
    return False


_TRANSLATION_CACHE: Dict[str, str] = {}


def _translate_to_chinese(text: str) -> str:
    clean = (text or "").strip()
    if not clean:
        return ""
    if clean in _TRANSLATION_CACHE:
        return _TRANSLATION_CACHE[clean]
    if re.search(r"[\u4e00-\u9fff]", clean) and not re.search(r"[A-Za-z]{4,}", clean):
        _TRANSLATION_CACHE[clean] = clean
        return clean
    try:
        response = requests.get(
            "https://translate.googleapis.com/translate_a/single",
            params={
                "client": "gtx",
                "sl": "auto",
                "tl": "zh-CN",
                "dt": "t",
                "q": clean,
            },
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
        translated = "".join(part[0] for part in payload[0] if part and part[0]).strip()
        if translated:
            _TRANSLATION_CACHE[clean] = translated
            return translated
    except Exception:
        pass
    _TRANSLATION_CACHE[clean] = clean
    return clean
