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
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / '.runtime'
PIDFILE = STATE / 'process.json'
LOG = STATE / 'server.log'
SERVER = ROOT / 'backend/server.py'


def fail(message):
    raise RuntimeError(message)


def settings():
    config = Path(os.environ.get('CONFIG', str(ROOT / 'config/config.json'))).expanduser().resolve()
    with config.open(encoding='utf-8') as f:
        cfg = json.load(f)
    host = os.environ.get('HOST', cfg.get('server', {}).get('host', '0.0.0.0'))
    port = int(os.environ.get('PORT', cfg.get('server', {}).get('port', 8080)))
    if not 1024 <= port <= 65535:
        fail('端口必须为 1024 到 65535；如需 443，请配置反向代理。')
    return config, host, port


def check():
    if sys.version_info < (3, 9):
        fail('需要 Python 3.9+，请用批准的离线介质安装，或设置 PYTHON_BIN。')
    if platform.system() not in ('Linux', 'Darwin', 'Windows'):
        fail('支持 Linux、macOS 和 Windows。')
    config, host, port = settings()
    if not os.access(config.parent, os.W_OK):
        fail('配置目录不可写：' + str(config.parent))
    STATE.mkdir(mode=0o700, exist_ok=True)
    sys.path.insert(0, str(ROOT / 'backend'))
    from server_core import build_payload
    data = build_payload(str(config))
    print('环境：%s / %s / Python %s' % (platform.system(), platform.machine(), platform.python_version()))
    print('Python：%s\n配置：%s\n监听：%s:%d' % (sys.executable, config, host, port))
    print('输入：%d lot / %d 台 CP；无需 pip、Node、Docker 或联网。' % (len(data['lots']), len(data['eqp'])))
    if data.get('planIssues'):
        print('注意：当前计划存在 %d 项冲突，启动后请在页面修正。' % len(data['planIssues']))
    return config, host, port


def running():
    if not PIDFILE.exists():
        return None
    record = json.loads(PIDFILE.read_text())
    pid = int(record['pid'])
    if pid <= 1:
        fail('无效 PID 记录，请人工检查 ' + str(PIDFILE))
    if platform.system() == 'Windows':
        info = windows_process(pid)
        if not info:
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
        return None
    command = subprocess.run(['ps', '-p', str(pid), '-o', 'args='], capture_output=True, text=True, check=False).stdout
    state = subprocess.run(['ps', '-p', str(pid), '-o', 'stat='], capture_output=True, text=True, check=False).stdout.strip()
    if not command.strip() or state.startswith('Z'):
        return None
    if str(SERVER) not in command or '--config ' + record['config'] not in command:
        fail('PID 已被其他进程使用，不会终止；请人工核对 ' + str(PIDFILE))
    return record


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


def open_browser(host, port):
    if os.environ.get('XMC_OPEN_BROWSER') == '1':
        import webbrowser
        address = '127.0.0.1' if host == '0.0.0.0' else host
        webbrowser.open('http://%s:%d' % (address, port))


def health(host, port):
    host = '127.0.0.1' if host == '0.0.0.0' else host
    host = '[%s]' % host if ':' in host else host
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open('http://%s:%d/api/health' % (host, port), timeout=2) as response:
        return json.load(response).get('ok') is True


def start():
    record = running()
    if record:
        print('已经运行：PID %d，端口 %d' % (record['pid'], record['port']))
        open_browser(record['host'], record['port'])
        return
    config, host, port = check()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
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
            process.terminate()
            process.wait(timeout=10)
            raise
    PIDFILE.write_text(json.dumps(record))
    for _ in range(30):
        if process.poll() is not None:
            PIDFILE.unlink(missing_ok=True)
            fail('服务启动失败，请查看 ' + str(LOG))
        try:
            if health(host, port):
                print('启动成功：PID %d\n访问：http://服务器地址:%d\n日志：%s' % (process.pid, port, LOG))
                print('仅限受控内网；本脚本不修改防火墙，也不配置认证或 HTTPS。')
                try:
                    addresses = sorted({entry[4][0] for entry in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)})
                    for address in addresses:
                        print('候选访问地址：http://%s:%d' % (address, port))
                except OSError:
                    pass
                open_browser(host, port)
                return
        except Exception:
            pass
        time.sleep(.3)
    process.terminate()
    process.wait(timeout=10)
    PIDFILE.unlink(missing_ok=True)
    fail('健康检查超时，请查看 ' + str(LOG))


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
        fail('请使用实际运行账号执行 deploy.sh install-service，不要 sudo 整个脚本。')
    if running():
        fail('请先运行 ./deploy.sh stop，避免与后台模式争用端口。')
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
    if not health(host, port):
        fail('服务尚未通过健康检查，请用 journalctl 检查。')
    print('开机自启已安装。后续使用 sudo systemctl restart/status/stop xmc-scheduler。')


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
        print('运行中：' + json.dumps(record, ensure_ascii=False) if record else '后台模式未运行；systemd 模式请用 systemctl status xmc-scheduler。')
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
