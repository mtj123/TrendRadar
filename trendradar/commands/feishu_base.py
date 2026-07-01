# coding=utf-8
"""
飞书多维表格命令
"""

import csv
import json
from pathlib import Path
from typing import Dict, Optional

from trendradar.feishu_base import FeishuBaseError, FeishuBaseSyncService
from trendradar.context import AppContext
from trendradar.crawler.rss import RSSFetcher


def run_feishu_base_init(config: Dict) -> bool:
    service = FeishuBaseSyncService(config)
    if not service.is_enabled():
        print("飞书 Base 未启用或配置不完整，请先配置 FEISHU_BASE_* 环境变量")
        return False

    table_id, created_fields = service.ensure_table_and_fields()
    print(f"飞书 Base 初始化完成，table_id: {table_id}")
    if created_fields:
        print(f"新增字段: {', '.join(created_fields)}")
    else:
        print("字段已齐全，无需新增")
    return True


def run_feishu_base_check(config: Dict) -> bool:
    service = FeishuBaseSyncService(config)
    cfg = service.cfg

    print("飞书 Base 配置检查：")
    print(f"  ENABLED: {cfg.enabled}")
    print(f"  APP_ID: {'已配置' if cfg.app_id else '未配置'}")
    print(f"  APP_SECRET: {'已配置' if cfg.app_secret else '未配置'}")
    print(f"  APP_TOKEN: {'已配置' if cfg.app_token else '未配置'}")
    print(f"  MAIN_TABLE_ID: {'已配置' if cfg.main_table_id else '未配置'}")
    print(f"  MAIN_TABLE_NAME: {cfg.main_table_name or '(空)'}")
    print(f"  AUTO_INIT_TABLE: {cfg.auto_init_table}")
    print(f"  OPEN_BASE: {cfg.open_base}")

    ok = cfg.is_usable()
    if cfg.enabled and not cfg.has_real_credentials():
        print("结果：检测到 FEISHU_BASE_* 仍是示例占位值，请替换为真实飞书凭证。")
        return False
    if ok:
        print("结果：基础凭证完整，可以继续初始化或同步。")
    else:
        print("结果：基础凭证不完整，无法初始化或同步。")
    return ok


def run_feishu_base_sync(config: Dict, storage_manager=None) -> bool:
    service = FeishuBaseSyncService(config)
    if not service.is_enabled():
        print("飞书 Base 未启用或配置不完整，跳过同步")
        return False

    if storage_manager is None:
        from trendradar.context import AppContext

        storage_manager = AppContext(config).get_storage_manager()

    news_data = storage_manager.get_latest_crawl_data()
    rss_data = _load_latest_rss_data(config, storage_manager)
    table_id, _ = service.ensure_table_and_fields()
    print(f"飞书 Base 表模式: {service._table_mode} (table_id={table_id})")
    github_repos = service.search_github_repos()
    result = service.sync_today(news_data, rss_data, github_repos=github_repos)
    print(
        "飞书 Base 同步完成: "
        f"table_id={result['table_id']}, rows={result['rows']}, "
        f"created={result['created']}, updated={result['updated']}"
    )
    if result["created_fields"]:
        print(f"本次新增字段: {', '.join(result['created_fields'])}")
    return True


def run_feishu_base_preview(config: Dict, storage_manager=None) -> bool:
    service = FeishuBaseSyncService(config)

    if storage_manager is None:
        storage_manager = AppContext(config).get_storage_manager()

    news_data = storage_manager.get_latest_crawl_data()
    rss_data = _load_latest_rss_data(config, storage_manager)
    table_id, _ = service.ensure_table_and_fields()
    github_repos = service.search_github_repos()
    preview = service.preview_today(news_data, rss_data, github_repos=github_repos)

    print(f"飞书 Base 预览: 共 {preview['rows']} 行待同步")
    print(f"飞书 Base 表模式: {preview.get('table_mode', service._table_mode)} (table_id={table_id})")
    if not preview["sample"]:
        print("当前没有可预览的数据。")
        return True

    source_type_counts = preview.get("by_source_type", {})
    rss_rows = source_type_counts.get("RSS", 0)
    repo_rows = source_type_counts.get("GitHubRepo", 0)
    print(f"GitHub 仓库结果: {len(github_repos)} 条")
    print(f"待同步构成: RSS={rss_rows}, GitHubRepo={repo_rows}")

    for index, row in enumerate(preview["sample"], 1):
        fields = row.get("fields", {})
        if service._table_mode == "business":
            print(
                f"[{index}] "
                f"{fields.get('来源', '')} | "
                f"{fields.get('标题', '')} | "
                f"{fields.get('重要程度', '')}"
            )
        else:
            print(
                f"[{index}] "
                f"{fields.get('动态ID', '')} | "
                f"{fields.get('来源类型', '')} | "
                f"{fields.get('来源', '')} | "
                f"{fields.get('标题', '')}"
            )
    if preview["rows"] > len(preview["sample"]):
        print(f"... 其余 {preview['rows'] - len(preview['sample'])} 行省略")

    if github_repos:
        print("GitHub 仓库样例:")
        for index, repo in enumerate(github_repos[:5], 1):
            print(
                f"  [{index}] {repo.get('full_name', '')} | "
                f"Stars={repo.get('stargazers_count', 0)} | "
                f"Forks={repo.get('forks_count', 0)} | "
                f"{repo.get('html_url', '')}"
            )
    return True


def run_feishu_base_export(config: Dict, storage_manager=None) -> bool:
    service = FeishuBaseSyncService(config)
    ctx = AppContext(config)

    if storage_manager is None:
        storage_manager = ctx.get_storage_manager()

    news_data = storage_manager.get_latest_crawl_data()
    rss_data = _load_latest_rss_data(config, storage_manager)
    service.ensure_table_and_fields()
    github_repos = service.search_github_repos()
    preview = service.preview_today(news_data, rss_data, github_repos=github_repos)

    rows = service._build_rows(news_data, rss_data, github_repos)  # reuse exact sync payload
    output_dir = Path(ctx.get_output_path("feishu_base_preview", "placeholder.json")).parent
    output_dir.mkdir(parents=True, exist_ok=True)

    json_path = output_dir / "payload.json"
    csv_path = output_dir / "payload.csv"

    json_path.write_text(
        json.dumps(
            {
                "summary": {
                    "rows": preview["rows"],
                    "by_source_type": preview.get("by_source_type", {}),
                    "github_repo_results": len(github_repos),
                },
                "records": rows,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    field_names = []
    if rows:
        ordered_keys = []
        seen = set()
        for row in rows:
            for key in row.get("fields", {}).keys():
                if key in seen:
                    continue
                seen.add(key)
                ordered_keys.append(key)
        field_names = ordered_keys
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=field_names)
        if field_names:
            writer.writeheader()
            for row in rows:
                writer.writerow(row.get("fields", {}))

    print(f"飞书 Base 预览已导出:")
    print(f"  JSON: {json_path}")
    print(f"  CSV:  {csv_path}")
    print(f"  总行数: {preview['rows']}")
    print(f"  构成: {preview.get('by_source_type', {})}")
    return True


def try_auto_sync_feishu_base(config: Dict, storage_manager=None) -> None:
    service = FeishuBaseSyncService(config)
    if not service.is_enabled():
        return

    try:
        run_feishu_base_sync(config, storage_manager=storage_manager)
    except FeishuBaseError as exc:
        print(f"[飞书 Base] 同步失败: {exc}")
    except Exception as exc:
        print(f"[飞书 Base] 同步异常: {exc}")


def _load_latest_rss_data(config: Dict, storage_manager):
    ctx = AppContext(config)
    if not ctx.rss_enabled or not ctx.rss_feeds:
        return storage_manager.get_latest_rss_data()

    rss_config = ctx.rss_config
    fetcher = RSSFetcher.from_config(
        {
            "feeds": rss_config.get("FEEDS", []),
            "request_interval": rss_config.get("REQUEST_INTERVAL", 2000),
            "timeout": rss_config.get("TIMEOUT", 15),
            "use_proxy": rss_config.get("USE_PROXY", False),
            "proxy_url": rss_config.get("PROXY_URL", ""),
            "timezone": ctx.timezone,
            "freshness_filter": {
                "enabled": rss_config.get("FRESHNESS_FILTER", {}).get("ENABLED", True),
                "max_age_days": rss_config.get("FRESHNESS_FILTER", {}).get("MAX_AGE_DAYS", 3),
            },
        }
    )
    try:
        rss_data = fetcher.fetch_all()
        storage_manager.save_rss_data(rss_data)
        return rss_data
    except Exception as exc:
        print(f"[RSS] 实时抓取失败，回退到本地已保存数据: {exc}")
        return storage_manager.get_latest_rss_data()
