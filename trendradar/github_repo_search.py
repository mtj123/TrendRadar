# coding=utf-8
"""
GitHub AI repository search

使用 GitHub REST Search API 拉取近期活跃的 AI 相关仓库，
用于飞书 Base 预览与同步。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
import math
import re
import time

import requests


@dataclass
class GitHubRepoSearchConfig:
    enabled: bool
    token: str
    queries: List[str]
    days: int
    per_query: int
    sort: str
    order: str
    api_base: str
    min_score: float

    @classmethod
    def from_config(cls, config: Dict[str, Any]) -> "GitHubRepoSearchConfig":
        github_cfg = config.get("GITHUB_REPO_SEARCH", {})
        queries = github_cfg.get("QUERIES", [])
        if isinstance(queries, str):
            queries = [item.strip() for item in queries.split(";") if item.strip()]
        return cls(
            enabled=bool(github_cfg.get("ENABLED", False)),
            token=github_cfg.get("TOKEN", ""),
            queries=queries,
            days=int(github_cfg.get("DAYS", 7) or 7),
            per_query=int(github_cfg.get("PER_QUERY", 20) or 20),
            sort=github_cfg.get("SORT", "updated") or "updated",
            order=github_cfg.get("ORDER", "desc") or "desc",
            api_base=(github_cfg.get("API_BASE", "https://api.github.com") or "https://api.github.com").rstrip("/"),
            min_score=float(github_cfg.get("MIN_SCORE", 18) or 18),
        )


class GitHubRepoSearchClient:
    def __init__(self, config: GitHubRepoSearchConfig, timeout: int = 30):
        self.config = config
        self.timeout = timeout

    CORE_TOPICS = {
        "artificial-intelligence",
        "llm",
        "agent",
        "agents",
        "ai-agent",
        "ai-agents",
        "agentic-ai",
        "mcp",
        "mcp-server",
        "model-context-protocol",
        "rag",
        "openai",
        "huggingface",
        "inference",
        "openai-api",
        "openai-compatible",
        "vllm",
        "sglang",
        "multimodal",
        "computer-use-agent",
        "cua",
        "benchmark",
    }

    CORE_KEYWORDS = {
        "agent", "agents", "agentic", "llm", "llms", "mcp", "rag", "inference",
        "model", "models", "openai", "anthropic", "claude", "codex", "chatgpt",
        "gemini", "qwen", "deepseek", "huggingface", "vllm", "sglang",
        "multimodal", "computer use", "benchmark", "reasoning",
    }

    EXCLUDE_KEYWORDS = {
        "awesome-", "awesome list", "tutorial", "beginner", "course", "interview",
        "trading", "finance", "wildlife", "bird", "bats", "soundscape",
        "android", "ios", "flutter", "swiftui", "semantic-segmentation",
        "earth-observation", "bioacoustics", "raspberry pi", "kubernetes sdk",
        "admin platform", "vue admin", "gin-vue-admin", "frontend", "ui-builder",
        "prompt-builder", "component-library", "practice repository", "collected ai repos",
        "chatbot", "prompt enhancer", "voice conversations", "video search",
        "airdrop", "penetration testing", "code reviewer", "analytics database",
        "crm-system", "crm ", "pull requests, docs, crm", "novel", "scripts, interactive games",
    }

    EXCLUDE_TOPIC_HINTS = {
        "awesome-list", "ai-news", "birds", "birdnet", "wildlife", "android",
        "ios", "semantic-segmentation", "earth-observation", "bioacoustics",
        "admin", "flutter", "ui-builder", "prompt-management", "fiction", "crm",
    }

    def search(self) -> List[Dict[str, Any]]:
        if not self.config.enabled or not self.config.queries:
            return []

        seen = set()
        repos: List[Dict[str, Any]] = []
        cutoff = (datetime.now(timezone.utc) - timedelta(days=self.config.days)).date().isoformat()
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.config.token:
            headers["Authorization"] = f"Bearer {self.config.token}"

        for query in self.config.queries:
            effective_query = query.replace("{days_ago}", cutoff)
            response = requests.get(
                f"{self.config.api_base}/search/repositories",
                headers=headers,
                params={
                    "q": effective_query,
                    "sort": self.config.sort,
                    "order": self.config.order,
                    "per_page": min(self.config.per_query, 100),
                    "page": 1,
                },
                timeout=self.timeout,
            )
            if response.status_code == 403 and "rate limit" in response.text.lower():
                reset_at = response.headers.get("X-RateLimit-Reset")
                if not repos:
                    raise requests.HTTPError(
                        f"GitHub Search API rate limit exceeded and no results collected yet: {effective_query}",
                        response=response,
                    )
                print(
                    "[GitHub Repo Search] rate limit exceeded, "
                    f"stop after partial results. query={effective_query}"
                )
                break
            response.raise_for_status()
            payload = response.json()
            for item in payload.get("items", []):
                full_name = item.get("full_name")
                if not full_name or full_name in seen:
                    continue
                if not self._is_relevant_repo(item):
                    continue
                item["_trendradar_repo_score"] = self._score_repo(item)
                if item["_trendradar_repo_score"] < self.config.min_score:
                    continue
                seen.add(full_name)
                repos.append(item)
            time.sleep(0.2)

        repos.sort(
            key=lambda item: (
                item.get("_trendradar_repo_score", 0),
                item.get("updated_at", ""),
            ),
            reverse=True,
        )
        return repos

    def _is_relevant_repo(self, item: Dict[str, Any]) -> bool:
        text = self._repo_text(item)
        topics = {topic.lower() for topic in (item.get("topics") or [])}

        positive_hit = any(keyword in text for keyword in self.CORE_KEYWORDS)
        topic_hit = any(topic in topics for topic in self.CORE_TOPICS)
        exclude_hit = any(keyword in text for keyword in self.EXCLUDE_KEYWORDS)
        exclude_topic_hit = any(topic in topics for topic in self.EXCLUDE_TOPIC_HINTS)
        stars = item.get("stargazers_count", 0) or 0
        forks = item.get("forks_count", 0) or 0

        core_signal_count = sum(keyword in text for keyword in self.CORE_KEYWORDS)
        if exclude_hit or exclude_topic_hit:
            return False

        if not (positive_hit or topic_hit):
            return False

        if core_signal_count < 2 and not topic_hit:
            return False

        if stars < 5 and forks < 2 and core_signal_count < 3 and not topic_hit:
            return False

        # 过滤“只带 AI 标签但主体并非 AI 工具/基础设施”的噪音项目
        strong_product_terms = (
            "model context protocol", "mcp", "llm", "agent", "agents", "inference",
            "reasoning", "benchmark", "copilot", "codex", "claude code", "openai",
            "huggingface", "multimodal", "ai coding", "assistant",
        )
        if not any(term in text for term in strong_product_terms) and not topic_hit:
            return False

        return True

    def _score_repo(self, item: Dict[str, Any]) -> float:
        stars = item.get("stargazers_count", 0) or 0
        forks = item.get("forks_count", 0) or 0
        watchers = item.get("watchers_count", 0) or 0
        topics = {topic.lower() for topic in (item.get("topics") or [])}
        text = self._repo_text(item)

        score = 0.0
        score += math.log1p(stars) * 2.0
        score += math.log1p(forks) * 1.2
        score += math.log1p(watchers) * 0.6

        topic_weights = {
            "artificial-intelligence": 6,
            "llm": 6,
            "agent": 5,
            "agents": 5,
            "mcp": 5,
            "rag": 4,
            "openai": 4,
            "huggingface": 4,
            "inference": 4,
            "openai-api": 3,
            "openai-compatible": 3,
            "model-context-protocol": 4,
            "mcp-server": 4,
            "ai-agent": 4,
            "ai-agents": 4,
            "agentic-ai": 4,
            "benchmark": 3,
            "computer-use-agent": 4,
            "cua": 3,
        }
        for topic, weight in topic_weights.items():
            if topic in topics:
                score += weight

        keyword_weights = {
            "agent": 3,
            "agents": 3,
            "llm": 4,
            "copilot": 4,
            "mcp": 4,
            "rag": 3,
            "inference": 2,
            "openai": 3,
            "qwen": 3,
            "deepseek": 3,
            "claude": 3,
            "gemini": 3,
            "gpt": 2,
            "codex": 3,
            "benchmark": 2,
            "computer use": 3,
            "multimodal": 2,
            "reasoning": 2,
        }
        for keyword, weight in keyword_weights.items():
            if keyword in text:
                score += weight

        penalties = {
            "awesome-ai-news": 20,
            "awesome-": 12,
            "collected ai repos": 14,
            "tutorial": 10,
            "beginner": 10,
            "course": 10,
            "interview": 10,
            "trading": 10,
            "finance": 8,
            "wildlife": 16,
            "bird": 16,
            "bats": 16,
            "bioacoustics": 16,
            "semantic-segmentation": 14,
            "earth-observation": 14,
            "android": 10,
            "ios": 10,
            "flutter": 8,
            "swiftui": 8,
            "frontend": 10,
            "ui-builder": 10,
            "prompt-builder": 12,
            "component-library": 8,
            "admin platform": 14,
            "vue admin": 14,
            "kubernetes sdk": 10,
            "practice repository": 14,
            "chatbot": 10,
            "prompt enhancer": 10,
            "voice conversations": 8,
            "video search": 8,
            "airdrop": 16,
            "penetration testing": 14,
            "code reviewer": 10,
        }
        for keyword, weight in penalties.items():
            if keyword in text:
                score -= weight

        if stars == 0:
            score -= 8
        elif stars < 5:
            score -= 4

        if forks == 0:
            score -= 3

        return score

    def _repo_text(self, item: Dict[str, Any]) -> str:
        text = " ".join(
            [
                item.get("name", "") or "",
                item.get("full_name", "") or "",
                item.get("description", "") or "",
                " ".join(item.get("topics", []) or []),
            ]
        ).lower()
        return re.sub(r"\s+", " ", text).strip()
