#!/usr/bin/env python3
"""Route production into the bundled workbench; direct/maintenance never start HTTP."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

SKILL = Path(__file__).resolve().parents[1]
BUNDLE = SKILL / 'scripts/workbench'


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--project', type=Path, default=Path.cwd())
    p.add_argument('--workspace', help='新任务的工作区目录，留空恢复项目目录')
    p.add_argument('--mode', choices=('workbench', 'direct', 'maintenance'), default='workbench')
    p.add_argument('--host', choices=('codex', 'workbuddy', 'local'))
    p.add_argument('--request', type=Path)
    p.add_argument('--invocation', help='同一次用户请求重试时沿用，新的制作请求换新值')
    p.add_argument('--no-open', action='store_true', help='仅供自动测试或无浏览器环境')
    args = p.parse_args()
    project = args.project.resolve()
    if args.mode == 'maintenance':
        return {'mode': 'maintenance', 'httpStarted': False, 'instruction': '仅按维护要求修改和验证；不启动制作、页面或监听。'}
    host = args.host or ('local' if args.mode == 'workbench' else 'codex')
    if args.mode == 'workbench' and host == 'workbuddy':
        raise ValueError('请在本机双击项目启动器，或从系统终端以 --host local 启动网页工作台。WorkBuddy 无页面制作仍可使用 --mode direct。')
    if host == 'local' and args.mode == 'direct':
        raise ValueError('独立制作需要本机工作台服务，请使用 --host local 启动。')
    if args.mode == 'direct' and not args.request:
        return {'mode': 'direct', 'httpStarted': False, 'instruction': '不打开页面。按 references/workbench.md 准备本次请求文件，再以 --mode direct --request FILE 提交到相同 FIFO；通过本地 bridge 回传真实阶段。'}
    os.environ['MONTAGE_SKILL_DIR'] = str(SKILL)
    sys.path.insert(0, str(BUNDLE))
    os.environ['MONTAGE_WORKBENCH_HOST'] = host
    if host == 'workbuddy':
        from runtime_compat import install
        install()
    from server import App, read, write
    from launch import launch
    result = {'mode': args.mode, 'httpStarted': args.mode == 'workbench'}
    app = App(project, SKILL, recover=False)
    with app.transaction():
        settings_file = app.data/'workspace.json'
        settings = read(settings_file, {})
        settings['defaultDirectory'] = str(Path.cwd().resolve())
        write(settings_file, settings)
        if args.workspace is not None:
            app.save_workspace(args.workspace)
        result['workspace'] = str(app.workspace())
    if args.mode == 'workbench':
        result.update(launch(project, args.no_open, host=args.host))
    app = App(project, SKILL)
    app.workbench_host = result.get('workbenchHost', host)
    if args.request:
        request = args.request.resolve()
        if request.stat().st_size > 100_000:
            raise ValueError('制作请求过大')
        values = read(request)
        if not isinstance(values, dict):
            raise ValueError('制作请求应为对象')
        invocation = args.invocation or hashlib.sha256(str(request).encode()+request.read_bytes()).hexdigest()
        if len(invocation) > 200:
            raise ValueError('请求标识过长')
        key = hashlib.sha256(invocation.encode()).hexdigest()
        with app.transaction():
            receipt = app.data / 'submissions' / (key + '.json')
            prior = read(receipt, {})
            if prior.get('taskId'):
                task = app.task(prior['taskId'])
            else:
                if values.get('source') and not values.get('institutionId'):
                    source = Path(values['source']).resolve()
                    name = str(values.get('institutionName') or source.name)
                    institution = next((r for r in app.library.records() if r['name'] == name), None)
                    institution = app.library.add_source(institution['id'], str(source)) if institution else app.library.save({'name': name, 'folder': str(source), 'description': values.get('facts', '')})
                    values['institutionId'] = institution['id']
                values.setdefault('person', app.pc.load().get('default_person'))
                try:
                    task = app.create(values)
                except ValueError as exc:
                    allowed = {'institutionId','requirements','title','person','people','count','music','facts','videoPreferences','musicTrackId'}
                    write(app.data/'creation-draft.json', {k:v for k,v in values.items() if k in allowed})
                    return {**result, 'taskStatus':'needs_input', 'message':app.redact(str(exc)), 'submitted':False, 'requestFile':str(request), 'instruction':'本次要求已保存为草稿，尚未入队。补齐页面配置或请求缺项后，沿用原文件和 invocation 重试。'}
                # Write the receipt before enqueue so interrupted retries never duplicate work.
                write(receipt, {'taskId': task['id']})
            task = app.start(task['id']) if task['status'] == 'draft' else task
        result['taskId'] = task['id']
        result['taskStatus'] = app.public_task(task)['status']
        if 'url' in result:
            result['taskUrl'] = result['url'] + '#task/' + task['id']
    if result.get('deferredUpgrade'):
        result['instruction'] = '当前工作台仍有待处理任务。任务结束后再次启动即可切换。'
        return result
    if result.get('workbenchHost', host) == 'local':
        result['settingsUrl'] = result['url'] + '#settings'
        result['instruction'] = '在设置里配置模型、语音和数字人。配置齐全后，本机工作台自动接单。'
        return result
    result['coordinatorGuide'] = str(BUNDLE / 'AGENT.md')
    result['instruction'] = '当前宿主主对话按 coordinatorGuide 协调队列；只有队列下发的单视频子 Agent 进入制作，不重复启动工作台。' if args.mode == 'workbench' else 'HTTP 未启动；当前宿主按同一协调协议派发队首，完成后报告待验收；验收前不清理中间资产。'
    return result


if __name__ == '__main__':
    try:
        print(json.dumps(main(), ensure_ascii=False, indent=2))
    except Exception as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=False))
        sys.exit(1)
