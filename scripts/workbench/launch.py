#!/usr/bin/env python3
"""Start or reopen the local workbench, using only Python's standard library."""
import argparse
import errno
import http.client
import http.cookiejar
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import shlex
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
import webbrowser


UNAVAILABLE = "同项目的工作台正在启动、退出或暂时无响应。请稍后再打开启动器。"


@contextmanager
def project_lock(project, name, blocking=True):
    data = project / '.montage/workbench'
    data.mkdir(parents=True, exist_ok=True)
    path = data / name
    with path.open('a+b') as lockfile:
        if sys.platform == 'win32':
            import msvcrt
            if not path.stat().st_size:
                lockfile.write(b'0')
                lockfile.flush()
            lockfile.seek(0)
            mode = msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK
            try:
                msvcrt.locking(lockfile.fileno(), mode, 1)
            except OSError as exc:
                if blocking or exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                    raise
                yield False
                return
        else:
            import fcntl
            flags = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
            try:
                fcntl.flock(lockfile.fileno(), flags)
            except BlockingIOError:
                yield False
                return
        yield True  # Closing this handle releases the lock on Windows and POSIX.


def service_running(project):
    with project_lock(project, 'service.lock', blocking=False) as acquired:
        return not acquired


def port_listening(port):
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=.5):
            return True
    except ConnectionRefusedError:
        return False
    except OSError:
        # A listener with a full backlog can time out.
        return True


def write_shortcut(project):
    """Leave a project-specific entry that opens a browser without an Agent."""
    if sys.platform!='darwin': return
    target=Path(project)/'打开视频工作台.command'
    marker='# montage-workbench-project-shortcut'
    if target.exists() and marker not in target.read_text(): return
    command=shlex.quote(sys.executable)+' '+shlex.quote(str(Path(__file__).resolve()))+' --project '+shlex.quote(str(Path(project).resolve()))
    content='#!/bin/bash\n'+marker+'\nif command -v rtk >/dev/null 2>&1; then\n  rtk '+command+'\nelse\n  '+command+'\nfi\n'
    if not target.exists() or target.read_text()!=content: target.write_text(content)
    target.chmod(0o700)


def launch(project, no_open=False, host=None):
    project=Path(project).resolve()
    project.mkdir(parents=True,exist_ok=True)
    with project_lock(project, 'launch.lock'):
        write_shortcut(project)
        return _launch(project, no_open, host)


def browser_result(record, no_open, **details):
    opened = webbrowser.open(record['url']) if not no_open else False
    if not no_open and not opened:
        print('未能自动打开默认浏览器。可在浏览器中打开下方返回的 url。', file=sys.stderr)
    return {**record, 'browserOpened': bool(opened), **details}


def _launch(project, no_open, host):
    data = project / ".montage/workbench"
    record = data / "server.json"
    try: previous = json.loads(record.read_text())
    except (FileNotFoundError,json.JSONDecodeError): previous = None
    if not isinstance(previous, dict) or not isinstance(previous.get('port'), int) or not 0 < previous['port'] < 65536:
        previous = None
    restart_port = None
    if previous and previous.get('lifetimeLock') and not service_running(project):
        previous = None  # A released lock makes this record stale, even if the port was reused.
    if previous:
        port = previous['port']
        base = f'http://127.0.0.1:{port}'
        internal_token = str(previous.get('internalToken') or '')
        url_ok = str(previous.get('url', '')).startswith(base + '/')
        if url_ok:
            client = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
            internal_headers = {'X-Workbench-Internal-Token': internal_token} if internal_token else {}
            try:
                if not internal_token and '/?access=' in str(previous.get('url', '')):
                    with client.open(previous['url'], timeout=2):
                        pass
                with client.open(urllib.request.Request(base + '/api/runtime', headers=internal_headers), timeout=3) as response:
                    runtime = json.load(response)
                if runtime.get('project') != str(project):
                    raise RuntimeError('记录中的服务属于其他项目，请检查当前项目的工作台启动状态。')
                with client.open(urllib.request.Request(base + '/api/health', headers=internal_headers), timeout=3) as response:
                    healthy = json.load(response).get('ok') is True
            except (OSError, ValueError, http.client.HTTPException) as exc:
                if service_running(project) or port_listening(port):
                    raise RuntimeError(UNAVAILABLE) from exc
            else:
                if not healthy:
                    raise RuntimeError(UNAVAILABLE)
                from server import code_fingerprint
                upgrade = runtime.get('codeFingerprint') != code_fingerprint()
                switch_mode = host is not None and runtime.get('workbenchHost') != host
                if not upgrade and not switch_mode:
                    return browser_result(previous, no_open, reused=True)
                try:
                    queue = json.loads((data / 'queue.json').read_text())
                except FileNotFoundError:
                    queue = {'items': []}
                except (OSError, json.JSONDecodeError) as exc:
                    raise RuntimeError('无法核对执行锁，未自动重启服务') from exc
                if any(not row.get('settled', True) for row in queue.get('items', [])):
                    return browser_result(previous, no_open, reused=True, deferredUpgrade=True)
                request = urllib.request.Request(
                    base + '/api/shutdown',
                    data=b'{}',
                    headers={'Origin': base, 'Content-Type': 'application/json', **internal_headers},
                )
                try:
                    with client.open(request, timeout=5):
                        pass
                except urllib.error.HTTPError as exc:
                    if exc.code == 409:
                        return browser_result(previous, no_open, reused=True, deferredUpgrade=True)
                    raise RuntimeError(UNAVAILABLE) from exc
                except OSError as exc:
                    raise RuntimeError(UNAVAILABLE) from exc
                for _ in range(30):
                    if not service_running(project) and not port_listening(port):
                        restart_port = port
                        break
                    time.sleep(.1)
    recorded_listener = previous and restart_port is None and port_listening(previous['port'])
    if service_running(project) or recorded_listener:
        raise RuntimeError(UNAVAILABLE)
    cmd = [sys.executable, str(Path(__file__).with_name("server.py")), "--project", str(project),
           "--port", str(restart_port or 8765), "--fallback-port", "--host", host or "local"]
    if shutil.which("rtk"):
        cmd = ["rtk", "proxy", *cmd]
    log_path = data / "server.log"
    fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    # A double-clicked Windows batch window can close as soon as launch returns.
    background = {'creationflags': subprocess.DETACHED_PROCESS} if sys.platform == 'win32' else {'start_new_session': True}
    with os.fdopen(fd, "a") as log:
        launch_id = uuid.uuid4().hex
        process = subprocess.Popen(
            cmd, cwd=project, env={**os.environ, "MONTAGE_WORKBENCH_LAUNCH_ID": launch_id},
            stdin=subprocess.DEVNULL, stdout=log, stderr=log, **background)
    for _ in range(100):
        if process.poll() is not None:
            if service_running(project):
                raise RuntimeError(UNAVAILABLE)
            raise SystemExit("工作台启动失败，请查看 .montage/workbench/server.log")
        if record.exists():
            current = json.loads(record.read_text())
            if current.get("launchId") == launch_id:
                return browser_result(current, no_open)
        time.sleep(.1)
    raise SystemExit("工作台启动超时，请检查本地服务日志")


def main():
    p=argparse.ArgumentParser(description=__doc__)
    candidates=[Path.cwd(),*Path(__file__).resolve().parents]
    existing=next((x for x in candidates if (x/'.montage/project.json').is_file()),None)
    p.add_argument('--project',type=Path)
    p.add_argument('--ask-project',action='store_true',help='找不到已有项目时，询问用于存放素材和作品的项目目录')
    p.add_argument('--no-open',action='store_true')
    args=p.parse_args()
    project=args.project or existing
    if project is None and args.ask_project:
        try:
            entered=input('请输入养老视频项目目录（可粘贴完整路径）：').strip().strip('"')
        except EOFError:
            p.error('请使用 --project 指定项目目录。')
        if not entered:
            p.error('请输入项目目录，或使用 --project 指定路径。')
        project=Path(entered).expanduser()
    try:
        result = launch(project or Path.cwd(), args.no_open)
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
