"""调用 TinyPNG（Tinify）压缩单张图片。"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from pathlib import Path

import tinify

_lock = threading.Lock()
FREE_MONTHLY_LIMIT = 500


class CompressError(Exception):
    def __init__(self, message: str, *, stop_batch: bool = False):
        super().__init__(message)
        self.stop_batch = stop_batch


@dataclass(frozen=True)
class CompressResult:
    original_size: int
    compressed_size: int
    compression_count: int | None


def format_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / 1024 / 1024:.2f} MB"


def format_saved(original_size: int, compressed_size: int) -> str:
    if original_size <= 0:
        return "—"
    percent = (original_size - compressed_size) / original_size * 100
    return f"{percent:.1f}%"


def query_usage(api_key: str) -> int | None:
    """校验 Key 并返回本月已压缩次数。校验本身不计入额度。"""
    _prepare_key(api_key)
    with _lock:
        tinify.key = api_key.strip()
        try:
            tinify.validate()
        except tinify.AccountError as err:
            raise CompressError(_account_message(err), stop_batch=True) from err
        except tinify.ServerError as err:
            raise CompressError("TinyPNG 服务暂时不可用，请稍后重试。") from err
        except tinify.ConnectionError as err:
            raise CompressError("网络连接失败，请检查网络后重试。") from err
        except tinify.Error as err:
            raise CompressError(f"无法查询用量：{err.message}") from err
        return _current_count()


def compress_file(api_key: str, source: Path, dest: Path) -> CompressResult:
    _prepare_key(api_key)
    source = Path(source)
    dest = Path(dest)
    if _same_path(source, dest):
        raise CompressError("输出路径与原图相同，已跳过以保护原图。")
    try:
        original_size = source.stat().st_size
    except OSError as err:
        raise CompressError(f"无法读取原图：{err}") from err

    dest.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        tinify.key = api_key.strip()
        try:
            tinify.from_file(str(source)).to_file(str(dest))
        except tinify.AccountError as err:
            raise CompressError(_account_message(err), stop_batch=True) from err
        except tinify.ClientError as err:
            raise CompressError(f"这张图片无法压缩：{err.message}") from err
        except tinify.ServerError as err:
            raise CompressError("TinyPNG 服务暂时不可用，请稍后重试。") from err
        except tinify.ConnectionError as err:
            raise CompressError("网络连接失败，请检查网络后重试。") from err
        except tinify.Error as err:
            raise CompressError(f"压缩失败：{err.message}") from err
        except OSError as err:
            raise CompressError(f"无法写入压缩结果：{err}") from err
        count = _current_count()

    try:
        compressed_size = dest.stat().st_size
    except OSError as err:
        raise CompressError(f"压缩完成但无法读取结果大小：{err}") from err
    return CompressResult(original_size, compressed_size, count)


def _prepare_key(api_key: str) -> None:
    if not api_key or not api_key.strip():
        raise CompressError("请先填写 API Key。", stop_batch=True)


def _current_count() -> int | None:
    count = tinify.compression_count
    return int(count) if count is not None else None


def _account_message(err: tinify.AccountError) -> str:
    if err.status == 429:
        return "本月压缩额度已用尽，请等下月恢复或升级套餐。"
    if err.status == 401:
        return "API Key 无效，请检查后重新保存。"
    return f"账号异常：{err.message}"


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(str(left.resolve())) == os.path.normcase(str(right.resolve()))
