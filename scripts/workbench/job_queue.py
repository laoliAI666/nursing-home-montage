"""Durable FIFO for isolated native WorkBuddy workers and local cover operations."""
from pathlib import Path
import hashlib
import json
import os
import secrets
import subprocess
import sys
import time
import uuid

TERMINAL={'completed','cancelled','timed_out'}
BLOCKED={'failed','needs_input','interrupted'}


class JobQueue:
    def __init__(self,app,read,write):
        self.app,self.read,self.write=app,read,write
        self.path=app.data/'queue.json'
        self.inbox=app.data/'agent-inbox'
        if not self.inbox.is_dir():
            app.ensure_dir(self.inbox)
            os.chmod(self.inbox,0o700)

    def data(self):
        return self.read(self.path,{'version':1,'nextSequence':1,'items':[]})

    def save(self,data):
        self.write(self.path,data)

    def head(self,data=None):
        return next((x for x in (data or self.data())['items'] if not(x['status'] in TERMINAL and x.get('settled'))),None)

    def item(self,ident,data=None):
        row=next((x for x in (data or self.data())['items'] if x['id']==ident),None)
        if not row:raise ValueError('队列作业不存在')
        return row

    def enqueue(self,task):
        with self.app.transaction():
            self.app.archive.ensure_available(task['id'])
            data=self.data()
            rows=[x for x in data['items'] if x.get('taskId')==task['id'] and x['kind']=='video']
            if rows:return self.app.task(task['id'])
            specs=task.get('videoSpecs') or [{'person':task['person'],'personName':task['personName'],'number':i+1} for i in range(task['count'])]
            if task.get('planningRequired'):
                folder,_=self.app.planning.initialize(task)
                data['items'].append({'id':uuid.uuid4().hex[:12],'taskId':task['id'],'index':-1,'kind':'planning','sequence':data['nextSequence'],'personName':'整批策划','title':task['title'],'job':str(folder),'status':'queued','phase':0,'message':'等待统一整理素材与策划全批','created':time.time(),'updated':time.time(),'settled':True,'generation':0})
                data['nextSequence']+=1
            for i,spec in enumerate(specs):
                key=uuid.uuid4().hex[:12]
                from workspace import video_job
                job=video_job(task,i)
                data['items'].append({'id':key,'taskId':task['id'],'index':i,'kind':'video','sequence':data['nextSequence'],'person':spec['person'],'personName':spec['personName'],'number':spec.get('number',i+1),'title':task['title'],'job':str(job),'status':'queued','phase':0,'message':'等待按提交顺序制作','created':time.time(),'updated':time.time(),'settled':True,'generation':0})
                data['nextSequence']+=1
            self.save(data)
            self.app.channel.request_connection('新任务已提交，等待主 Agent 接单')
            self.app.update(task['id'],queueVersion=1,submitted=time.time(),status='queued',message='已提交到队尾，按顺序逐条制作')
            self.app.event(task['id'],f"本批 {len(specs)} 条已进入队尾，序号 {data['nextSequence']-len(specs)}–{data['nextSequence']-1}")
            return self.app.task(task['id'])

    def enqueue_cover(self,ident,index,operation,source,out):
        with self.app.transaction():
            data=self.data()
            row={'id':operation['id'],'taskId':ident,'index':index,'kind':'cover','title':'更新视频首帧','personName':'','sequence':data['nextSequence'],'status':'queued','phase':0,'message':'等待更新视频首帧','created':time.time(),'updated':time.time(),'settled':True,'generation':0,'sourceVideo':str(source),'output':str(out),'operation':dict(operation)}
            data['items'].append(row);data['nextSequence']+=1;self.save(data)
            return row

    def public(self):
        data=self.data();head=self.head(data)
        pending=[x for x in data['items'] if not(x['status'] in TERMINAL and x.get('settled'))]
        positions = {row['id']: i for i, row in enumerate(
            (row for row in pending if row['kind'] != 'planning'), start=1)}
        fields=('id','taskId','index','kind','sequence','personName','number','title','status','phase','message','created','updated','deadline','stopReason','platformAgentId','settled','recoveryRequested','answerPending','recoveryRequestedAt','recoveryDispatched','recoveryState','recoveryResult')
        return {'items':[{k:x.get(k) for k in fields}|{'position':positions.get(x['id']),'answerSaved':bool(x.get('answer'))} for x in data['items']],
                'headId':head['id'] if head else None,'waiting':sum(x['status']=='queued' and x['id'] in positions for x in pending),'paused':bool(head and head['status'] in BLOCKED),
                'busy':bool(head and (head['status'] in ('assigned','running','stopping') or not head.get('settled'))),
                'coordinator':'local' if self.app.workbench_host == 'local' else 'native-subagent','total':len(positions)}

    def for_task(self,ident):
        return [x for x in self.data()['items'] if x.get('taskId')==ident and x['kind']=='video']

    def sync_task(self,ident):
        if ident=='reference':return
        rows=self.for_task(ident)
        if not rows:return
        original=self.app.task(ident)
        planner=next((x for x in self.data()['items'] if x['taskId']==ident and x['kind']=='planning'),None)
        if planner and not(planner['status'] in TERMINAL and planner.get('settled')):
            status='running' if planner['status'] in ('assigned','completed') else planner['status']
            self.app.update(ident,status=status,phase=min(1,planner.get('phase',0)),message=planner['message'],completedCount=0,activeItemId=planner['id'],question=planner.get('question'))
            return
        completed=sum(x['status']=='completed' for x in rows)
        if all(x['status'] in TERMINAL and x.get('settled') for x in rows):
            status='completed' if completed==len(rows) else 'partial' if completed else 'timed_out' if any(x['status']=='timed_out' for x in rows) else 'cancelled'
        else:
            current=next((x for x in rows if not(x['status'] in TERMINAL and x.get('settled'))),rows[-1])
            status='running' if current['status'] in ('assigned','completed') else 'stopping' if current['status']=='cancelled' and not current.get('settled') else current['status']
        current=next((x for x in rows if not(x['status'] in TERMINAL and x.get('settled'))),rows[-1])
        if status=='timed_out':
            current=next(x for x in reversed(rows) if x['status']=='timed_out')
        message=f"已完成 {completed}/{len(rows)} 条 · "+current['message']
        patch={'status':status,'message':message,'phase':current.get('phase',0),'completedCount':completed,'activeItemId':current['id'],'question':current.get('question') if status=='needs_input' else None}
        if any(original.get(k)!=v for k,v in patch.items()):self.app.update(ident,**patch)

    def claim_next(self, local=None):
        head=self.head()
        if not head or head['kind'] not in ('video','planning'):return None
        return self.claim(head['kind'], local=local)

    def claim(self,kind='video',local=None):
        with self.app.transaction():
            data=self.data();head=self.head(data)
            if not head or head['kind']!=kind or head['status']!='queued':return None
            if kind in ('video','planning') and bool(local) != (self.app.workbench_host == 'local'):return None
            if kind=='video' and self.app.task(head['taskId']).get('planningRequired'):
                self.app.planning.verify(self.app.task(head['taskId']))
            if head.get('answer') and 'answerQuestion' not in head:
                head['answerQuestion']=self.previous_answer_question(head)
            head.update(status='assigned',settled=False,lease='run_'+secrets.token_urlsafe(24),generation=head.get('generation',0)+1,assigned=time.time(),updated=time.time(),message='正在派发整批策划子 Agent' if kind=='planning' else '正在交给 Agent 制作' if kind=='video' else '正在启动首帧更新')
            head.pop('platformAgentId',None);head.pop('workerId',None)
            head.pop('started',None)
            head.pop('localExecution',None)
            if local:
                head['localExecution']=local
                head['message']='独立制作已领取队首'
            self.save(data);self.sync_task(head['taskId'])
            if kind in ('video','planning') and not local:self.write_dispatch(head)
            return dict(head)

    def previous_answer_question(self,row):
        if row.get('question'):
            return row['question']
        # Older native jobs cleared the question when storing a page answer.
        for path in self.inbox.glob('*.request.json'):
            try:
                request=self.read(path,{})
                if not isinstance(request,dict) or (request.get('op'),request.get('id'),request.get('lease'),request.get('status'))!=('report',row['id'],row.get('lease'),'needs_input'):
                    continue
                receipt=self.read(path.with_name(path.name.removesuffix('.request.json')+'.result.json'),{})
                if isinstance(receipt,dict) and receipt.get('ok') and isinstance(request.get('question'),str):
                    return request['question']
            except (OSError,ValueError):
                continue
        return None

    def checked(self,ident,lease,data=None):
        data=data or self.data();row=self.item(ident,data);head=self.head(data)
        if not head or head['id']!=ident or not lease or row.get('lease')!=lease or row.get('settled'):
            raise ValueError('执行租约已失效，禁止旧 Agent 回写或越过队首')
        return data,row

    def accept(self,ident,lease,worker_id):
        with self.app.transaction():
            data,row=self.checked(ident,lease)
            if row['status']=='stopping':raise ValueError('已请求取消，不再开始制作')
            if row['status'] not in ('assigned','running'):raise ValueError('当前作业不能领取')
            if row.get('workerId') and row['workerId']!=worker_id:raise ValueError('已有其他子 Agent 领取这条视频')
            if not isinstance(worker_id,str) or not worker_id.strip():raise ValueError('必须提供独立子 Agent 持有者标识')
            now=time.time()
            row.update(status='running',workerId=worker_id,started=row.get('started',now),seen=now,updated=now,message='Agent 已接手这一条视频')
            if row['kind']=='video' and 'deadline' not in row:
                row['timeoutMinutes']=self.app.models.production_timeout()
                row['deadline']=row['started']+row['timeoutMinutes']*60
            self.mark_expired(row,now)
            self.save(data);self.sync_task(row['taskId']);return row

    def mark_expired(self,row,now):
        if row['status'] not in ('assigned','running') or row.get('settled') or now<row.get('deadline',float('inf')):
            return False
        note=f"单条制作达到 {row['timeoutMinutes']} 分钟时限"
        row.update(status='stopping',stopReason='timeout',message=note+'，正在停止；已有文件保留',updated=now)
        return True

    def expire_head(self):
        head=self.head()
        if not head or head['status'] not in ('assigned','running') or time.time()<head.get('deadline',float('inf')):
            return
        with self.app.transaction():
            data=self.data();row=self.head(data)
            if row and self.mark_expired(row,time.time()):
                self.save(data);self.sync_task(row['taskId'])
                self.app.event(row['taskId'],row['message'])

    def finish_stop(self,row,settling=False):
        if row.get('stopReason')=='timeout':
            row.update(status='timed_out',message=f"单条制作超时（{row['timeoutMinutes']} 分钟），已停止；已有文件保留")
        else:
            row.update(status='cancelled',message='当前制作已结束，这一条已取消' if settling else '已到停止检查点，正在确认制作结束')

    def reject_stopped_report(self,data,row):
        self.mark_expired(row,time.time())
        if row['status']=='stopping':
            self.finish_stop(row)
            row['updated']=time.time()
            self.save(data);self.sync_task(row['taskId'])
            self.app.event(row['taskId'],row['message'])
            raise ValueError('已停止本条，立即结束子 Agent 并返回协调者')

    def report(self,ident,lease,phase,status,message,question=None):
        with self.app.transaction():
            data,row=self.checked(ident,lease)
            self.reject_stopped_report(data,row)
            if row['status']!='running':raise ValueError('请由独立子 Agent 先 accept 领取，或结束已报告的作业')
            if status=='needs_input' and not question:raise ValueError('需要补充时必须提供具体问题')
            if status=='completed':
                if row['kind']=='planning':self.app.planning.verify(self.app.task(row['taskId']))
                else:self.app.verify_job(Path(row['job']))
                self.reject_stopped_report(data,row)
                phase=6
            row.update(status=status,phase=phase,message=self.app.redact(message)[:1000],question=question,seen=time.time(),updated=time.time())
            if row.get('recoveryRequested'):
                row.update(recoveryRequested=False,recoveryDispatched=False,recoveryState='responded',recoveryCompletedAt=time.time(),recoveryResult='已收到本执行者的新进度回传；当前执行锁保持不变')
            self.save(data);self.sync_task(row['taskId'])
            self.app.event(row['taskId'],('整批策划：' if row['kind']=='planning' else f"第 {row['index']+1} 条 · {row['personName']}：")+row['message'])
            return row

    def settle(self,ident,lease,agent_id=None,local=None):
        with self.app.transaction():
            data,row=self.checked(ident,lease)
            if self.app.channel.has_active(ident):raise ValueError('本地执行步骤尚未退出，暂不能回收；请读取执行结果后重试结束登记')
            if row['kind'] not in ('video','planning'):raise ValueError('首帧更新由本地进程确认结束，不能由 Agent 回收')
            if row.get('localExecution')!=local:
                raise ValueError('结束回执与本条执行方式不符')
            if local:
                agent_id=local['id']
            else:
                if not agent_id or len(agent_id)>180:raise ValueError('必须回传原生子 Agent 标识，不能主对话直接制作')
                if row.get('platformAgentId') and row['platformAgentId']!=agent_id:raise ValueError('结束回执与已绑定的原生执行者不一致')
                if any((x.get('platformAgentId')==agent_id and x['id']!=ident) or any(a.get('agentId')==agent_id for a in x.get('attempts',[])) for x in data['items']):raise ValueError('每次制作或恢复必须使用全新子 Agent，不能复用旧上下文')
                row['platformAgentId']=agent_id
            self.mark_expired(row,time.time())
            if row['status']=='stopping':
                self.finish_stop(row,settling=True)
                self.app.event(row['taskId'],row['message'])
            elif row['status'] in ('assigned','running'):row.update(status='failed',message='当前制作已结束，交付核验尚未通过；队列已暂停')
            elif row['status']=='completed':
                try:
                    if row['kind']=='planning':self.app.planning.verify(self.app.task(row['taskId']))
                    else:self.app.verify_job(Path(row['job']))
                except (OSError,ValueError):row.update(status='failed',message='交付文件在结束确认前发生变化，请重新核验；队列已暂停')
            row.update(settled=True,updated=time.time())
            row.setdefault('attempts',[]).append({'generation':row['generation'],'agentId':agent_id,'executor':'local' if local else 'native','leaseHash':hashlib.sha256(lease.encode()).hexdigest(),'status':row['status'],'assigned':row.get('assigned'),'started':row.get('started'),'ended':time.time()})
            if row.get('answerPending') and row['status']=='needs_input':
                row.setdefault('answerQuestion',row.get('question'))
                row.update(status='queued',message='补充已保存，继续当前序号',answerPending=False,question=None)
                row.pop('deadline',None);row.pop('timeoutMinutes',None)
            self.save(data);self.sync_task(row['taskId']);return row

    def action(self,ident,action,answer=None):
        with self.app.transaction():
            data=self.data();row=self.item(ident,data);head=self.head(data)
            if action=='cancel':
                # A verified delivery remains a delivery while its child is being reclaimed.
                if row['status']=='completed' or row['status'] in TERMINAL and row.get('settled') or row.get('stopReason')=='timeout':return row
                row.update(status='cancelled' if row.get('settled') else 'stopping',message='已取消排队' if row.get('settled') else '已请求停止，等待当前制作结束；已有文件保留',updated=time.time())
                if row['kind']=='cover':self.cover_state(row,row['status'],row['message'])
            elif action in ('retry','answer'):
                self.app.archive.ensure_available(row['taskId'])
                if not head or head['id']!=ident:raise ValueError('只能处理队首，不能插队或越过更早的作业')
                if row['status'] not in BLOCKED:raise ValueError('当前作业无需重试')
                if action=='retry' and row['status']=='needs_input':raise ValueError('请先在页面答复当前问题，再继续这一条')
                if action=='answer':
                    if row['status']!='needs_input' or not str(answer or '').strip():raise ValueError('请填写当前问题的补充信息')
                    if row.get('answerPending'):raise ValueError('补充已保存，正在等待本条执行者结束')
                    row['answer']=str(answer).strip()[:20000]
                    row['answerQuestion']=row.get('question')
                    if not row.get('settled'):row['answerPending']=True
                elif not row.get('settled'):raise ValueError('子 Agent 尚未结束，请先等待或请求检查旧执行者')
                if row.get('settled'):
                    row.update(status='queued',message='按原序号继续当前作业',question=None)
                    for field in ('deadline','timeoutMinutes','stopReason'):
                        row.pop(field,None)
                    if row['kind']=='cover':self.cover_state(row,'queued',row['message'])
                row['updated']=time.time()
                self.app.channel.request_connection('用户已重试队首，等待恢复接单')
            elif action=='recover':
                if not head or head['id']!=ident:raise ValueError('只能检查当前队首')
                if row.get('settled'):raise ValueError('旧执行者已经结束，可直接重试或取消当前条')
                if row['kind']=='cover':
                    self.reconcile()
                    current=self.item(ident)
                    if not current.get('settled'):raise ValueError('本地首帧进程仍在运行；可请求停止并等待当前检查点')
                    return current
                row.update(recoveryRequested=True,recoveryDispatched=False,recoveryRequestedAt=time.time(),recoveryState='pending',updated=time.time())
            else:raise ValueError('不支持这个队列操作')
            self.save(data);self.sync_task(row['taskId']);return row

    def recover(self,ident,lease,evidence,local=None):
        with self.app.transaction():
            data,row=self.checked(ident,lease)
            if row['kind'] not in ('video','planning'):raise ValueError('首帧更新请由本地进程检查状态')
            if not evidence:raise ValueError('必须先确认旧子 Agent 已结束，并记录检查结果')
            if row.get('localExecution')!=local:raise ValueError('恢复请求与本条执行方式不符')
            if self.app.channel.has_active(ident):raise ValueError('本地执行步骤仍在运行，不能回收')
            self.mark_expired(row,time.time())
            cancelled=row['status'] in ('stopping','cancelled','timed_out')
            status='cancelled' if cancelled else 'interrupted'
            message='已取消，原生执行者已确认退出' if cancelled else '旧执行者已确认停止，可以从当前序号继续'
            if row.get('stopReason')=='timeout':
                self.finish_stop(row,settling=True)
                status,message=row['status'],row['message']
            if row['status']=='completed':
                try:
                    if row['kind']=='planning':self.app.planning.verify(self.app.task(row['taskId']))
                    else:self.app.verify_job(Path(row['job']))
                except (OSError,ValueError):message='结束已确认，但交付核验发生变化，请检查后重试'
                else:status='completed';message='制作与退出均已确认，可继续后续队列'
            if local and row['status']=='needs_input':
                status='queued' if row.get('answerPending') else 'needs_input'
                message='补充已保存，继续当前序号' if row.get('answerPending') else row['message']
                if row.get('answerPending'):
                    row.update(answerPending=False,question=None)
                    row.pop('deadline',None);row.pop('timeoutMinutes',None)
            if local and row['status']=='failed':
                status='failed';message=row['message']
            if local and status=='interrupted' and row['status'] in ('assigned','running'):
                status='queued';message='本地服务重启，按原序号恢复检查点'
            row.update(status=status,settled=True,recoveryRequested=False,recoveryState='responded',message=message,recoveryEvidence=str(evidence)[:1000],updated=time.time())
            row.setdefault('attempts',[]).append({'generation':row['generation'],'agentId':local['id'] if local else row.get('platformAgentId'),'executor':'local' if local else 'native','leaseHash':hashlib.sha256(lease.encode()).hexdigest(),'status':status,'ended':time.time(),'recovered':True})
            self.save(data);self.sync_task(row['taskId']);return row

    def task_action(self,task_id,action,answer=None):
        rows=[x for x in self.data()['items'] if x['taskId']==task_id and x['kind'] in ('video','planning')]
        if not rows:return None
        if action=='cancel':
            for row in rows:
                if row['status'] not in TERMINAL or not row.get('settled'):self.action(row['id'],'cancel')
        else:
            row=next((x for x in rows if not(x['status'] in TERMINAL and x.get('settled'))),None)
            if not row:raise ValueError('本批次已经结束；重做请从单条成品进入')
            self.action(row['id'],'answer' if answer else 'retry',answer)
        return self.app.task(task_id)

    def worker_task(self,row):
        parent=self.app.task(row['taskId']);i=row['index']
        task={**parent,'id':row['id'],'parentTaskId':parent['id'],'job':row['job'],'count':1,'person':row['person'],'personName':row['personName'],'videoPreferences':[(parent.get('videoPreferences') or ['']*parent['count'])[i]],'session':row['id']}
        for field in ('videoSpecs','queueItems','events','results','activeItemId'):task.pop(field,None)
        return task

    def write_dispatch(self,row):
        folder=self.app.data/'queue-workers'/row['id']
        self.app.ensure_dir(folder)
        task=self.app.task(row['taskId']) if row['kind']=='planning' else self.worker_task(row)
        task={**task,'answer':row.get('answer'),'answerQuestion':row.get('answerQuestion')}
        self.write(folder/'request.json',task)
        prompt=self.app.planning.prompt(task,folder) if row['kind']=='planning' else self.app.prompt(task,row.get('answer'),request_path=folder/'request.json')
        bridge=Path(__file__).parent/'bridge.py'
        import shlex
        prefix=f"{self.app.python_command(bridge)} --project {shlex.quote(str(self.app.project))}"
        prompt+=f"\n本条由独立原生子 Agent 执行，只处理这一条；不能派生 Agent。先读取 {self.app.skill/'references/workbench-channel.md'}，用原生文件 accept 领取，再用 run 请求执行。普通制作步骤与接口轮询由本地连续执行器完成；遇到内容、原片、排版及成片关卡时必须实际检查再记录审核。阶段0素材、1文案分镜、2配音数字人、3画面封面、4渲染检查、5打包。手工进度用本条带租约 op=report，不虚增百分比。\n"
        prompt+=f"\n仅在用户明确指定无页面且没有 HTTP 文件接收服务时，使用相同队列的 CLI：{prefix} accept {row['id']} --lease {shlex.quote(row['lease'])} --worker-id {row['id']}-g{row['generation']}；随后 {prefix} run {row['id']} --lease {shlex.quote(row['lease'])} --action ACTION。ACTION 与原生文件通道相同。需要审核时增加 --review-kind KIND --note-file 实际复核说明.txt --frames 实际检查帧。无页面模式不启动 HTTP。不要在有原生文件服务时先试 Bash。\n"
        prompt += '\n画面复核优先用联系图批量检查整体，再单独打开有疑点或文字看不清的原尺寸帧；合并可独立进行的读图与查询，避免重复成功步骤。仍保存并检查规范要求的素材、字幕与成片检查帧，不降低核验标准。\n'
        prompt += '\n工作台任务需要用户决定时，用 needs_input 和 question 把完整问题及选项发到任务页，收到回执后立即退出。不要在宿主聊天里另开提问弹窗。恢复时读取 request.json 的 answer 和 answerQuestion，结合原问题理解数字选项或简短答复。\n'
        if row['kind']=='video' and self.app.task(row['taskId']).get('planningRequired'):
            prompt += f"\n整批策划已批准。本条 plan.json 与 inventory.json 已准备，不重新 inventory、不另写一套文案；读取 {self.app.planning.folder(self.app.task(row['taskId']))/'approved.json'} 的索引及本条计划即可。需要改变已批准内容时报告问题，不静默改写。\n"
        prompt += f"\n优先使用本地原生文件通道：读取 {self.app.skill/'references/workbench-channel.md'}。本条 id={row['id']}，lease={row['lease']}，workerId={row['id']}-g{row['generation']}。先发送 accept。阶段执行用 op=run，逐视频 action=advance；只在返回 needs_review 时实际检查并记录对应审核，再继续 advance。策划使用 prepare-planning / planning-check。普通轮询由本地连续执行器完成。无需依赖 WorkBuddy 的 Bash/命令代理，不能自行绕过任何权限拒绝。\n"
        (folder/'PROMPT.md').write_text(prompt,encoding='utf-8')

    def dispatch_text(self,row,native=False):
        import shlex
        bridge=Path(__file__).parent/'bridge.py';folder=self.app.data/'queue-workers'/row['id']
        prefix=f"{self.app.python_command(bridge)} --project {shlex.quote(str(self.app.project))}"
        fallback=self.inbox/f"{row['id']}-g{row['generation']}-settle.request.json"
        label='整批统一策划' if row['kind']=='planning' else row['personName']+'，本批第 '+str(row['index']+1)+' 条'
        if native:
            return f"队首作业 {row['id']}（批次 {row['taskId']}）：{label}。你是主协调者，使用宿主原生工具创建全新子 Agent，仅传 {folder/'PROMPT.md'}。取得真实 agentId 后按原生文件通道 attach；等它真实退出再以主连接令牌、当前 id/lease 和真实标识 settle，然后重新 poll。needs_input 也先登记退出，再继续 poll 等工作台答复，不打开聊天提问弹窗。失败也必须登记退出。不要使用 Bash 领取或回收，不伪造标识，不把已取消任务重新排队。"
        return f"队首 #{row['sequence']}（批次 {row['taskId']}）：{label}。你是协调者，不得自己制作。使用刚才已验证的原生子 Agent 工具创建一个全新上下文，任务仅为：读取 {folder/'PROMPT.md'}，按该文件执行并返回短摘要。不要把其他视频上下文传入。子 Agent 结束后，取得其真实标识，运行 {prefix} settle {row['id']} --lease {shlex.quote(row['lease'])} --agent-id '真实子Agent标识'，然后重新 wait。即使子 Agent 失败或没有正式交付也必须 settle，队列会保留失败并暂停。不能先 wait 再回收旧 Agent，不能使用同一子 Agent 制作两条。若宿主 shell 代理断开但原生文件工具可用，确认子 Agent 已结束后，按 AGENT.md 的结束回执格式，用原生 Write 写入 {fallback}，本条 id={row['id']}，lease={row['lease']}；agentId 必须填真实宿主标识。读取同名 .result.json 确认 ok，不手改 queue.json。"

    def cover_state(self,row,status,message):
        folder=self.app.edits.root/row['taskId']/str(row['index'])
        op=self.read(folder/'operation.json',{})
        if op.get('id')==row['id']:self.write(folder/'operation.json',{**op,'status':status,'message':message,'sequence':row['sequence']})

    def process_inbox(self):
        """Settlement only: native file tools still work if the host's shell broker is down.

        The same lease/identity checks apply. This never runs commands, reports success,
        or force-releases an unknown worker. The coordinator must observe its real exit.
        """
        for path in self.inbox.glob('*.request.json'):
            result=path.with_name(path.name.replace('.request.json','.result.json'))
            if result.exists():continue
            try:
                if path.stat().st_size>16384:raise ValueError('结束回执过大')
                try:command=self.read(path)
                except json.JSONDecodeError:continue  # A native Write tool may still be writing.
                if isinstance(command,dict) and command.get('version')==2:
                    response={'ok':True,'result':self.app.channel.handle(command)}
                    self.write(result,response);continue
                if not isinstance(command,dict) or command.get('version')!=1:raise ValueError('不支持的结束回执格式')
                ident,lease,agent_id=(command.get(k) for k in ('id','lease','agentId'))
                if not all(isinstance(x,str) and x for x in (ident,lease,agent_id)):raise ValueError('结束回执缺少作业、租约或真实子 Agent 标识')
                with self.app.transaction():
                    row=self.item(ident)
                    lease_hash=hashlib.sha256(lease.encode()).hexdigest()
                    prior=next((x for x in row.get('attempts',[]) if x.get('agentId')==agent_id and x.get('leaseHash')==lease_hash),None)
                    if prior:
                        response={'ok':True,'status':prior['status'],'replayed':True}
                    else:
                        row=self.settle(ident,lease,agent_id)
                        note=self.app.redact(str(command.get('hostError','')))[:1000]
                        if note and row['status']=='failed':
                            data=self.data();row=self.item(ident,data);row.update(message=note,updated=time.time());self.save(data);self.sync_task(row['taskId']);self.app.event(row['taskId'],note,'error')
                        response={'ok':True,'status':row['status'],'replayed':False}
                self.write(result,response)
            except Exception as exc:self.write(result,{'ok':False,'error':self.app.redact(str(exc))[:1000]})

    def local_finish(self,ident,lease,status,message):
        with self.app.transaction():
            data,row=self.checked(ident,lease)
            row.update(status='cancelled' if row['status']=='stopping' else status,settled=True,message=message,phase=6 if status=='completed' else row.get('phase',0),updated=time.time())
            self.save(data)
            self.cover_state(row,row['status'],message)

    def launch_local(self):
        row=self.claim('cover')
        if not row:return
        try:
            path=self.app.data/'queue-workers'/row['id'];self.app.ensure_dir(path)
            with (path/'local.log').open('ab') as log:
                p=subprocess.Popen([sys.executable,str(Path(__file__).parent/'local_worker.py'),'--project',str(self.app.project),'--item',row['id'],'--lease',row['lease']],cwd=self.app.project,env={**os.environ,'MONTAGE_SKILL_DIR':str(self.app.skill)},stdout=log,stderr=log,start_new_session=True)
            with self.app.transaction():
                data=self.data();current=self.item(row['id'],data);current['localPid']=p.pid;self.save(data)
        except Exception as exc:self.local_finish(row['id'],row['lease'],'failed',str(exc))

    def reconcile(self):
        with self.app.transaction():
            data=self.data();row=self.head(data)
            if not row or row.get('settled'):return
            if row['kind']=='cover' and row.get('localPid'):
                try:os.kill(row['localPid'],0)
                except ProcessLookupError:
                    folder=self.app.edits.root/row['taskId']/str(row['index']);out=Path(row['output'])
                    edition=self.read(out/'edition.json',{});verify=self.read(out/'verification.json',{})
                    committed=self.read(folder/'current.json',{}).get('version')==row['id'] and edition.get('status')=='completed'
                    if committed and verify.get('fullDecode') and verify.get('audioUnchanged') and (out/'final.mp4').is_file():
                        from video_edits import digest
                        committed=verify.get('sha256')==digest(out/'final.mp4')
                    else:committed=False
                    if committed:
                        row.update(status='completed',settled=True,phase=6,message='首帧更新已核验完成，视频与发布包已保存',updated=time.time());self.save(data);self.cover_state(row,'completed',row['message'])
                    else:
                        row.update(status='interrupted',settled=True,message='本地首帧进程已结束但未完成交付，可重试当前序号',updated=time.time());self.save(data);self.cover_state(row,'failed',row['message'])

    def migrate(self):
        for task in sorted(self.app.tasks(),key=lambda t:t.get('submitted',t['created'])):
            if task.get('queueVersion') or task['status'] not in ('queued','running','stopping'):continue
            active=task['status'] in ('running','stopping')
            self.enqueue(task)
            if active:
                with self.app.transaction():
                    data=self.data();rows=[x for x in data['items'] if x['taskId']==task['id'] and x['kind']=='video'];unfinished=[]
                    for row in rows:
                        try:self.app.verify_job(Path(row['job']))
                        except (ValueError,FileNotFoundError):unfinished.append(row)
                        else:row.update(status='completed',settled=True,phase=6,message='已保留旧工程核验通过的成品')
                    if unfinished:unfinished[0].update(status='interrupted',settled=False,lease='run_'+secrets.token_urlsafe(24),message='旧协议制作被迁移，需先检查旧执行者是否已结束')
                    self.save(data);self.sync_task(task['id'])
        # queue.json is the scheduling authority if a process stopped between two atomic files.
        with self.app.transaction():
            for ident in {x['taskId'] for x in self.data()['items'] if x['kind']=='video'}:self.sync_task(ident)
