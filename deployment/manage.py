#!/usr/bin/env python3
"""Offline deployment and process management, using only Python's stdlib."""
import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / 'backend'
FRONTEND = ROOT / 'frontend'
STATE = ROOT / '.runtime'
PIDFILE = STATE / 'process.json'
LOG = STATE / 'server.log'
SERVER = BACKEND / 'server.py'
REQUIRED_BACKEND = ('server.py', 'server_core.py', 'data_sources.py', 'diagnostics.py',
                    'scheduler.py')
REQUIRED_FRONTEND = (
    'index.html',
    'assets/styles.css',
    'assets/enterprise.css',
    'assets/data.js',
    'assets/data-loader.js',
    'assets/schedule-client.js',
    'assets/js/core.js',
    'assets/js/diagnostics.js',
    'assets/js/mps.js',
    'assets/js/gantt.js',
    'assets/js/configuration.js',
    'assets/js/inputs.js',
    'assets/js/ui.js',
    'assets/brand/xmc-logo-official.png',
    'assets/brand/wafer-grid.svg',
)
FRONTEND_MARKERS = (
    'id="app"',
    'id="btn-import"',
    'assets/schedule-client.js',
    'assets/js/core.js',
    'assets/js/inputs.js',
    'assets/js/configuration.js',
)


def fail(message):
    raise RuntimeError(message)


def settings():
    config = Path(os.environ.get('CONFIG', str(ROOT / 'config/config.json'))).expanduser().resolve()
    try:
        with config.open(encoding='utf-8') as f:
            cfg = json.load(f)
    except FileNotFoundError:
        fail('配置文件不存在：' + str(config))
    except json.JSONDecodeError as error:
        fail('配置文件不是有效 JSON：%s（%s）' % (config, error))
    except OSError as error:
        fail('配置文件不可读：%s（%s）' % (config, error))
    if not isinstance(cfg, dict):
        fail('配置文件顶层必须是 JSON 对象：' + str(config))
    server = cfg.get('server') or {}
    if not isinstance(server, dict):
        fail('config/server 必须是 JSON 对象：' + str(config))
    host = str(os.environ.get('HOST', server.get('host', '0.0.0.0'))).strip()
    if not host:
        fail('监听地址不能为空；请检查 HOST 或 config/server.host。')
    try:
        port = int(os.environ.get('PORT', server.get('port', 8080)))
    except (TypeError, ValueError):
        fail('端口必须是 1024 到 65535 的整数；请检查 PORT 或 config/server.port。')
    if not 1024 <= port <= 65535:
        fail('端口必须为 1024 到 65535；如需 443，请配置反向代理。')
    return config, host, port


def backend_problems(root=None):
    base = Path(root or ROOT)
    problems = ['backend/' + name for name in REQUIRED_BACKEND if not (base / 'backend' / name).is_file()]
    if not (base / 'tools/build_data.py').is_file():
        problems.append('tools/build_data.py')
    return problems


def frontend_problems(root=None):
    base = Path(root or FRONTEND)
    problems = ['frontend/' + name for name in REQUIRED_FRONTEND if not (base / name).is_file()]
    index = base / 'index.html'
    if index.is_file():
        try:
            html = index.read_text(encoding='utf-8')
        except (OSError, UnicodeDecodeError) as error:
            problems.append('frontend/index.html 不可读（%s）' % error)
        else:
            for marker in FRONTEND_MARKERS:
                if marker not in html:
                    problems.append('frontend/index.html 缺少 %s' % marker)
    return problems


def data_problems(data):
    problems = []
    for key in ('mps', 'eqp', 'recipes', 'lots'):
        if not isinstance(data.get(key), list):
            problems.append('数据字段 %s 不是列表' % key)
    if not isinstance(data.get('flows'), dict):
        problems.append('数据字段 flows 不是对象')
    if not isinstance(data.get('meta'), dict) or not data['meta'].get('month'):
        problems.append('数据字段 meta.month 缺失')
    return problems


def check():
    if sys.version_info < (3, 9):
        fail('需要 Python 3.9+，请用批准的离线介质安装，或设置 PYTHON_BIN。')
    if platform.system() not in ('Linux', 'Darwin', 'Windows'):
        fail('支持 Linux、macOS 和 Windows。')
    problems = backend_problems() + frontend_problems()
    if problems:
        fail('部署文件不完整或前后端版本不一致：\n- ' + '\n- '.join(problems))
    config, host, port = settings()
    if not os.access(config.parent, os.W_OK):
        fail('配置目录不可写：' + str(config.parent))
    try:
        STATE.mkdir(mode=0o700, exist_ok=True)
        fd, probe = tempfile.mkstemp(prefix='.write-', dir=str(STATE))
        os.close(fd)
        os.unlink(probe)
    except OSError as error:
        fail('运行目录不可写：%s（%s）' % (STATE, error))
    sys.path.insert(0, str(BACKEND))
    from server_core import build_payload
    try:
        data = build_payload(str(config))
    except Exception as error:
        fail('读取数据源失败：' + str(error))
    issues = data_problems(data)
    if issues:
        fail('前后端数据契约不完整：\n- ' + '\n- '.join(issues))
    print('环境：%s / %s / Python %s' % (platform.system(), platform.machine(), platform.python_version()))
    print('Python：%s\n配置：%s\n监听：%s:%d' % (sys.executable, config, host, port))
    print('前端：frontend/index.html 及 %d 个静态资源；后端：backend/server.py' % (len(REQUIRED_FRONTEND) - 1))
    print('输入：%d wafer / %d lot / %d 台 CP；前端与 API 由同一服务同源提供。' % (sum(l[3] for l in data['lots']), len(data['lots']), len([e for e in data['eqp'] if 'OVEN' not in e['type'].upper()])))
    if data.get('planIssues'):
        print('注意：当前计划存在 %d 项冲突，启动后请在页面修正。' % len(data['planIssues']))
    return config, host, port


def running():
    if not PIDFILE.exists():
        return None
    try:
        record = json.loads(PIDFILE.read_text(encoding='utf-8'))
        pid = int(record['pid'])
        record['config']
    except (OSError, ValueError, KeyError, TypeError) as error:
        fail('进程记录损坏：%s（%s）；请人工检查，脚本不会终止未知进程。' % (PIDFILE, error))
    if pid <= 1:
        fail('无效 PID 记录，请人工检查 ' + str(PIDFILE))
    if platform.system() == 'Windows':
        info = windows_process(pid)
        if not info:
            PIDFILE.unlink(missing_ok=True)
            return None
        command = info.get('CommandLine') or ''
        if (str(SERVER).lower() not in command.lower()
                or record['config'].lower() not in command.lower()
                or info.get('CreationDate') != record.get('created')):
            fail('PID 不属于本应用，不会终止；请人工检查 ' + str(PIDFILE))
        return record
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        PIDFILE.unlink(missing_ok=True)
        return None
    command = subprocess.run(['ps', '-p', str(pid), '-o', 'args='], capture_output=True, text=True, check=False).stdout
    state = subprocess.run(['ps', '-p', str(pid), '-o', 'stat='], capture_output=True, text=True, check=False).stdout.strip()
    if not command.strip() or state.startswith('Z'):
        PIDFILE.unlink(missing_ok=True)
        return None
    if str(SERVER) not in command or '--config ' + record['config'] not in command:
        fail('PID 已被其他进程使用，不会终止；请人工核对 ' + str(PIDFILE))
    return record


def write_record(record):
    temp = PIDFILE.with_suffix('.json.tmp')
    temp.write_text(json.dumps(record), encoding='utf-8')
    os.replace(temp, PIDFILE)
    try:
        os.chmod(PIDFILE, 0o600)
    except OSError:
        pass


def terminate_process(process):
    try:
        process.terminate()
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def windows_process(pid):
    # CIM lookup avoids os.kill(pid, 0), which is unsafe on Windows.
    script = ('[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new(); '
              'Get-CimInstance Win32_Process -Filter "ProcessId = %d" | '
              'Select-Object CommandLine,@{Name="CreationDate";Expression={$_.CreationDate.ToString("o")}} | '
              'ConvertTo-Json -Compress') % int(pid)
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                            capture_output=True, encoding='utf-8', errors='replace', timeout=15)
    if result.returncode:
        fail('无法查询 Windows 进程，请检查 PowerShell/CIM 权限。')
    return json.loads(result.stdout.lstrip('\ufeff')) if result.stdout.strip() else None


def base_url(host, port):
    address = '127.0.0.1' if host in ('0.0.0.0', '::') else host
    if ':' in address and not address.startswith('['):
        address = '[%s]' % address
    return 'http://%s:%d' % (address, port)


def http_get(url, timeout=3):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url, timeout=timeout) as response:
        return response.status, response.read()


def health_report(host, port):
    url = base_url(host, port)
    report = {'ok': False, 'api': False, 'frontend': False, 'data': False, 'url': url, 'error': ''}
    try:
        status, body = http_get(url + '/api/health', timeout=2)
        report['api'] = status == 200 and json.loads(body.decode('utf-8')).get('ok') is True
    except Exception as error:
        report['error'] = '健康接口未通过：' + str(error)
        return report
    try:
        status, body = http_get(url + '/', timeout=3)
        html = body.decode('utf-8', 'replace')
        report['frontend'] = status == 200 and all(marker in html for marker in FRONTEND_MARKERS)
    except Exception as error:
        report['error'] = '前端首页未通过：' + str(error)
        return report
    try:
        status, body = http_get(url + '/api/data', timeout=8)
        data = json.loads(body.decode('utf-8'))
        report['data'] = status == 200 and bool(data.get('mps')) and bool(data.get('lots')) and bool(data.get('eqp'))
    except Exception as error:
        report['error'] = '数据接口未通过：' + str(error)
        return report
    report['ok'] = report['api'] and report['frontend'] and report['data']
    if not report['ok']:
        report['error'] = '未就绪：' + '、'.join(name for key, name in (('api', 'API'), ('frontend', '首页'), ('data', '数据接口')) if not report[key])
    return report


def wait_for_ready(host, port, timeout=25):
    deadline = time.time() + timeout
    report = {'ok': False, 'error': '尚未开始检查'}
    while time.time() < deadline:
        report = health_report(host, port)
        if report['ok']:
            return report
        time.sleep(.5)
    return report


def should_open_browser():
    setting = os.environ.get('XMC_OPEN_BROWSER', '').strip().lower()
    if setting in ('1', 'true', 'yes', 'on'):
        return True
    if setting in ('0', 'false', 'no', 'off'):
        return False
    if os.environ.get('SSH_CONNECTION') or os.environ.get('SSH_TTY'):
        return False
    if platform.system() in ('Windows', 'Darwin'):
        return True
    return bool(os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY'))


def open_browser(host, port):
    if not should_open_browser():
        return
    import webbrowser
    url = base_url(host, port)
    try:
        opened = webbrowser.open(url)
    except Exception:
        opened = False
    if not opened:
        print('未能自动打开浏览器，请手动访问 ' + url)


def start():
    record = running()
    if record:
        report = health_report(record['host'], record['port'])
        if report['ok']:
            print('已经运行：PID %d，端口 %d；前后端已联通。' % (record['pid'], record['port']))
            open_browser(record['host'], record['port'])
            return
        print('检测到本应用进程，但前后端校验未通过：%s' % report['error'])
        print('先校验当前部署文件，再自动重启；校验失败会保留原进程。')
        config, host, port = check()
        stop()
    else:
        config, host, port = check()
    family = socket.AF_INET6 if ':' in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as probe:
        try:
            if platform.system() != 'Windows':
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind((host, port))
        except OSError as error:
            fail('无法绑定端口：%s；可设置 PORT=8090 后重试。' % error)
    process_options = {'start_new_session': True}
    if platform.system() == 'Windows':
        process_options = {'creationflags': subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP}
    with LOG.open('ab') as output:
        process = subprocess.Popen([sys.executable, '-u', str(SERVER), '--config', str(config),
            '--host', host, '--port', str(port)], cwd=str(ROOT), stdin=subprocess.DEVNULL,
            stdout=output, stderr=subprocess.STDOUT, **process_options)
    record = {'pid': process.pid, 'config': str(config), 'host': host, 'port': port}
    if platform.system() == 'Windows':
        try:
            info = windows_process(process.pid)
            if not info:
                fail('启动进程已退出，请查看日志。')
            record['created'] = info['CreationDate']
        except Exception:
            terminate_process(process)
            raise
    write_record(record)
    report = wait_for_ready(host, port)
    if report['ok']:
        print('启动成功：PID %d\n访问：%s\n日志：%s' % (process.pid, base_url(host, port), LOG))
        print('前后端已联通：首页、/api/health、/api/data。')
        print('仅限受控内网；本脚本不修改防火墙，也不配置认证或 HTTPS。')
        try:
            addresses = sorted({entry[4][0] for entry in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)})
            for address in addresses:
                print('候选访问地址：http://%s:%d' % (address, port))
        except OSError:
            pass
        open_browser(host, port)
        return
    terminate_process(process)
    PIDFILE.unlink(missing_ok=True)
    fail('启动后前后端校验失败：%s；请查看 %s' % (report.get('error') or '未知错误', LOG))


def stop():
    record = running()
    if not record:
        print('服务未运行。')
        PIDFILE.unlink(missing_ok=True)
        return
    if platform.system() == 'Windows':
        # Revalidate creation time immediately before terminating the process.
        running()
        result = subprocess.run(['taskkill', '/PID', str(record['pid'])], capture_output=True)
        if result.returncode:
            fail('停止失败，请通过任务管理器核对本应用进程。')
        for _ in range(20):
            if not windows_process(record['pid']):
                PIDFILE.unlink(missing_ok=True)
                print('已停止。')
                return
            time.sleep(.2)
        fail('进程未退出，请人工检查。')
    os.kill(record['pid'], signal.SIGTERM)
    for _ in range(50):
        state = subprocess.run(['ps', '-p', str(record['pid']), '-o', 'stat='], capture_output=True, text=True, check=False).stdout.strip()
        if not state or state.startswith('Z'):
            PIDFILE.unlink(missing_ok=True)
            print('已停止。')
            return
        try:
            os.kill(record['pid'], 0)
        except ProcessLookupError:
            PIDFILE.unlink(missing_ok=True)
            print('已停止。')
            return
        time.sleep(.1)
    fail('进程未退出，请人工检查；不会强制杀死进程。')


def service():
    if platform.system() != 'Linux' or not shutil.which('systemctl'):
        fail('开机自启仅支持使用 systemd 的 Linux。')
    if os.geteuid() == 0:
        fail('请使用实际运行账号执行 python3 deploy.py install-service，不要 sudo 整个脚本。')
    if running():
        fail('请先运行 python3 deploy.py stop，避免与后台模式争用端口。')
    config, host, port = check()
    if any(c in str(ROOT) + str(config) + sys.executable for c in '\n\r%"\\'):
        fail('服务路径包含 systemd 不支持的特殊字符，请换用简单目录。')
    unit = STATE / 'xmc-scheduler.service'
    unit.write_text('''[Unit]
Description=XMC Testing Scheduler
After=network.target

[Service]
Type=simple
User=%s
WorkingDirectory="%s"
Environment=PYTHONUTF8=1
ExecStart="%s" -u "%s" --config "%s" --host %s --port %d
Restart=on-failure
RestartSec=5
UMask=0077
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
''' % (__import__('getpass').getuser(), ROOT, sys.executable, SERVER, config, host, port))
    if not shutil.which('sudo'):
        fail('已生成 %s；请管理员复制到 /etc/systemd/system/ 并启用。' % unit)
    print('将请求 sudo，仅安装并启用 xmc-scheduler.service；不改应用配置及防火墙。')
    subprocess.run(['sudo', 'install', '-m', '644', str(unit), '/etc/systemd/system/xmc-scheduler.service'], check=True)
    subprocess.run(['sudo', 'systemctl', 'daemon-reload'], check=True)
    subprocess.run(['sudo', 'systemctl', 'enable', '--now', 'xmc-scheduler.service'], check=True)
    subprocess.run(['sudo', 'systemctl', 'status', '--no-pager', 'xmc-scheduler.service'], check=True)
    report = wait_for_ready(host, port)
    if not report['ok']:
        fail('服务尚未通过前后端校验：%s；请用 journalctl -u xmc-scheduler 检查。' % report.get('error'))
    print('开机自启已安装，前后端校验通过。后续使用 sudo systemctl restart/status/stop xmc-scheduler。')


def main():
    parser = argparse.ArgumentParser(description='XMC 离线部署，无需安装第三方依赖')
    parser.add_argument('action', nargs='?', default='start', choices=['start', 'stop', 'restart', 'status', 'check', 'logs', 'install-service'])
    args = parser.parse_args()
    STATE.mkdir(mode=0o700, exist_ok=True)
    if args.action == 'logs':
        print(LOG.read_text(errors='replace')[-20000:] if LOG.exists() else '暂无日志。')
        return
    if args.action == 'status':
        record = running()
        if not record:
            print('后台模式未运行；systemd 模式请用 systemctl status xmc-scheduler。')
            return
        report = health_report(record['host'], record['port'])
        print('运行中：' + json.dumps(record, ensure_ascii=False))
        print('访问：' + report['url'])
        print('前后端：' + ('已联通（首页、/api/health、/api/data）' if report['ok'] else '异常：' + report['error']))
        return
    if args.action == 'check':
        check()
        return
    lock = STATE / 'deployment.lock'
    try:
        lock.mkdir()
    except FileExistsError:
        fail('有部署操作进行中；如上次被强制中断，请核实后删除 ' + str(lock))
    try:
        if args.action in ('stop', 'restart'):
            stop()
        if args.action in ('start', 'restart'):
            start()
        if args.action == 'install-service':
            service()
    finally:
        lock.rmdir()


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('部署失败：' + str(error), file=sys.stderr)
        sys.exit(1)
