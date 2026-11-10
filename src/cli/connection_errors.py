"""Actionable local-service errors without changing remote connection failures."""
import errno
from urllib.parse import urlsplit


def connection_error_message(reason, url: str, *, english: bool = False) -> str:
    refused = (isinstance(reason, ConnectionRefusedError) or
               getattr(reason, 'errno', None) in {errno.ECONNREFUSED, 10061} or
               getattr(reason, 'winerror', None) == 10061)
    if refused and urlsplit(url).hostname in {'localhost', '127.0.0.1', '::1'}:
        if english:
            return 'Local ClawCross service is not running. Run clawcross start; if startup fails, check its startup log.'
        return '本地 ClawCross 服务未运行，请先运行 clawcross start；若启动失败，请查看启动日志。'
    return f'connection failed: {reason}' if english else f'连接失败: {reason}'
