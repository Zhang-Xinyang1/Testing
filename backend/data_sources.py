"""Data-source boundary; Oracle remains explicitly disabled until site integration."""
import re


def oracle_settings(value):
    value = value or {}
    allowed = {'host', 'port', 'service', 'user', 'passwordEnv', 'views'}
    if not isinstance(value, dict) or set(value) - allowed:
        raise ValueError('Oracle 配置字段无效；密码只允许环境变量引用')
    result = dict(value)
    port = result.get('port', 1521)
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError('Oracle 端口无效')
    result['port'] = port
    if result.get('passwordEnv') and not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', result['passwordEnv']):
        raise ValueError('密码环境变量名称无效')
    views = result.get('views', {})
    if not isinstance(views, dict) or any(not re.fullmatch(r'[A-Za-z][A-Za-z0-9_$#]*(\.[A-Za-z][A-Za-z0-9_$#]*)?', str(v)) for v in views.values()):
        raise ValueError('Oracle 视图名称无效，不接受 SQL')
    return result


def read_oracle(config):
    """Future adapter must return the same sheet/row contract as read_workbook."""
    oracle_settings(config)
    raise ValueError('Oracle 接口预留，尚未接入；请使用 Excel 数据源')
