# coding=utf-8
"""
CLI 命令模块

独立的 CLI 子命令：doctor、test-notification、show-schedule、version-check、feishu-base
"""

from .doctor import run_doctor
from .feishu_base import (
    run_feishu_base_check,
    run_feishu_base_export,
    run_feishu_base_init,
    run_feishu_base_preview,
    run_feishu_base_sync,
    try_auto_sync_feishu_base,
)
from .test_notification import run_test_notification
from .status import handle_status_commands
from .version import check_all_versions

__all__ = [
    "run_doctor",
    "run_feishu_base_check",
    "run_feishu_base_export",
    "run_feishu_base_init",
    "run_feishu_base_preview",
    "run_feishu_base_sync",
    "run_test_notification",
    "handle_status_commands",
    "check_all_versions",
    "try_auto_sync_feishu_base",
]
