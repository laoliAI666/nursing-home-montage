#!/usr/bin/env python3
"""A file-based progress channel for the host Agent; no server secret needed."""
import argparse
import json
import os
import shlex
import time
import re
import sys
from pathlib import Path
from server import App, read, write, python_command


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, required=True)
    sub = parser.add_subparsers(dest="action", required=True)
    report = sub.add_parser("report")
    report.add_argument("id")
    report.add_argument("--phase", type=int, choices=range(6), required=True)
    report.add_argument("--message", required=True)
    report.add_argument("--status", choices=["running", "needs_input", "failed", "completed"], default="running")
    report.add_argument("--question")
    report.add_argument('--lease')
    accept=sub.add_parser('accept');accept.add_argument('id');accept.add_argument('--lease',required=True);accept.add_argument('--worker-id',required=True)
    settle=sub.add_parser('settle');settle.add_argument('id');settle.add_argument('--lease',required=True);settle.add_argument('--agent-id',required=True)
    recover=sub.add_parser('recover');recover.add_argument('id');recover.add_argument('--lease',required=True);recover.add_argument('--confirmed-stopped',required=True,help='确认旧原生子Agent已结束的真实检查结果')
    execute=sub.add_parser('run');execute.add_argument('id');execute.add_argument('--lease',required=True)
    execute.add_argument('--action',dest='stage_action',choices=('prepare-planning','planning-check','prepare-video','advance','review','snapshot','check'),required=True)
    execute.add_argument('--review-kind',choices=('content','provider','layout','final','contrast'))
    execute.add_argument('--note-file',type=Path);execute.add_argument('--frames',nargs='*',default=[])
    execute.add_argument('--finding', action='append', dest='findings')
    wait = sub.add_parser("wait")
    wait.add_argument("--seconds", type=int, default=600)
    sub.add_parser("disconnect")
    ack = sub.add_parser("ack")
    ack.add_argument("probe")
    argv=sys.argv[1:]
    # Legacy URL-safe leases can begin with '-', which argparse treats as an option.
    for i in range(len(argv)-1):
        if argv[i]=='--lease' and re.fullmatch(r'-[A-Za-z0-9_-]{31}',argv[i+1]):
            argv[i:i+2]=['--lease='+argv[i+1]]
            break
    args = parser.parse_args(argv)
    app = App(args.project, recover=False)
    host_file = app.data / "agent-connection.json"
    if args.action=='run':
        # Explicit no-HTTP mode uses the same typed executor synchronously. Keep
        # this process alive until its own worker exits; never orphan a thread.
        request={'id':args.id,'lease':args.lease,'action':args.stage_action,'reviewKind':args.review_kind,
                 'note':args.note_file.read_text() if args.note_file else None,'frames':args.frames,
                 'findings':args.findings}
        result=app.channel.start(request)
        app.channel.active[args.id].join()
        value=app.channel.handle({'op':'run-status','id':args.id,'lease':args.lease,'runId':result['runId']})
        print(json.dumps(value,ensure_ascii=False));return 1 if value['state']=='failed' else 0
    if args.action=='accept':
        row=app.queue.accept(args.id,args.lease,args.worker_id)
        write(host_file,{'state':'working','seen':time.time(),'task':row['taskId'],'item':row['id']})
        print('本条已由独立子 Agent 领取，序号 '+str(row['sequence']))
        return
    if args.action=='settle':
        row=app.queue.settle(args.id,args.lease,args.agent_id)
        print('子 Agent 已回收：'+row['status']+'。重新 wait 等待下一条。')
        return
    if args.action=='recover':
        app.queue.recover(args.id,args.lease,args.confirmed_stopped)
        print('旧执行者检查已记录。页面可按原序号恢复，不会重复领取后面的作业。')
        return
    if args.action == "ack":
        record = read(app.data / "agent-probe.json", {})
        if record.get("id") != args.probe or record.get("status") != "claimed":
            raise SystemExit("连接检查已失效，请从页面重新检查")
        record.update(status="passed", completed=time.time())
        write(app.data / "agent-probe.json", record)
        print("当前宿主接收与进度回传已验证。请重新执行 wait 继续监听。")
        return
    if args.action == "disconnect":
        with app.transaction():
            control=read(app.data/'agent-control.json',{})
            write(app.data/'agent-control.json',{'stopSequence':control.get('stopSequence',0)+1})
            write(host_file, {"state": "disconnected", "seen": 0})
        print("工作台已断开，任务文件保留")
        return
    if args.action == "wait":
        deadline = time.monotonic() + min(max(args.seconds, 1), 600)
        stop_sequence=read(app.data/'agent-control.json',{}).get('stopSequence',0)
        from launch import service_running
        while time.monotonic() < deadline:
            if service_running(app.project) and read(app.data / 'server.json', {}).get('workbenchHost') == 'local':
                print('此项目的本地工作台正在运行，由本机程序接单。')
                return
            if read(app.data/'agent-control.json',{}).get('stopSequence',0)!=stop_sequence:
                write(host_file,{'state':'disconnected','seen':0})
                print('已按明确断开请求结束监听。不要自动重新 wait；等待用户再次连接。')
                return
            app.queue.reconcile();app.queue.launch_local()
            previous = read(host_file, {})
            if previous.get("state") == "waiting" and previous.get("pid") != os.getpid() and time.time() - previous.get("seen", 0) < 15:
                try:
                    os.kill(previous["pid"], 0)
                except OSError:
                    pass
                else:
                    raise SystemExit("已有 Agent 监听进程，不要重复启动")
            write(host_file, {"state": "waiting", "seen": time.time(), "pid": os.getpid()})
            with app.transaction():
                probe = read(app.data / "agent-probe.json", {})
                if probe.get("status") == "pending" and time.time() - probe.get("created", 0) < 300:
                    probe["status"] = "claimed"
                    write(app.data / "agent-probe.json", probe)
                    print(f"这是用户从网页发起的连接自检，不是视频制作，不调用付费服务。请运行 {python_command(Path(__file__).resolve())} --project {shlex.quote(str(app.project))} ack {probe['id']}。成功后重新执行 wait 继续监听。")
                    return
                data=app.queue.data();head=app.queue.head(data)
                if head and head.get('recoveryRequested') and not head.get('recoveryDispatched'):
                    head['recoveryDispatched']=True;app.queue.save(data)
                    print(f"队首 #{head['sequence']} 请求检查旧执行者。只能使用原生子Agent状态/停止工具确认 {head.get('platformAgentId') or head.get('workerId') or head['id']} 是否已结束；无法确认就保持锁，不自行创建新制作。确认已结束后运行 {python_command(Path(__file__).resolve())} --project {shlex.quote(str(app.project))} recover {head['id']} --lease {shlex.quote(head['lease'])} --confirmed-stopped '真实检查结果'。然后 wait。")
                    return
                row=app.queue.claim_next()
                if row:
                    write(host_file,{'state':'working','seen':time.time(),'task':row['taskId'],'item':row['id'],'pid':os.getpid()})
                    print(app.queue.dispatch_text(row));return
            time.sleep(2)
        print("暂时没有新任务。继续运行相同 wait 命令等待；不要把没有任务当作制作完成。")
        return
    if any(x['id']==args.id for x in app.queue.data()['items']):
        try:
            row=app.queue.report(args.id,args.lease,args.phase,args.status,args.message,args.question)
        except ValueError as exc:raise SystemExit(str(exc))
        write(host_file,{'state':'working','seen':time.time(),'task':row['taskId'],'item':row['id']})
        print('本条真实进度已保存。终态报告后立即结束子 Agent，由协调者 settle 后才执行下一条。');return
    task = app.task(args.id)
    if app.queue.for_task(args.id):raise SystemExit('这是批次ID。请只报告当前独立视频作业ID，不得整批冒报完成。')
    if task["status"] in ("cancelled", "stopping"):
        if task["status"] == "stopping":
            app.update(args.id, status="cancelled", message="Agent 已到达停止检查点，任务已停止。已有文件保留。")
        raise SystemExit("任务已停止，不再更新进度")
    if args.status == "completed":
        app.complete(args.id)
    else:
        if args.status == "needs_input" and not args.question:
            raise SystemExit("等待补充时必须提供具体问题")
        app.update(args.id, phase=args.phase, status=args.status, message=args.message, question=args.question)
        app.event(args.id, args.message)
    write(host_file, {"state": "working", "seen": time.time(), "task": args.id})
    print("工作台进度已更新")


if __name__ == "__main__":
    raise SystemExit(main())
