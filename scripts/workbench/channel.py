"""Native file-tool transport, independent of WorkBuddy's shell broker.

The local service accepts typed operations only. Connection tokens, execution
leases and real native Agent exit receipts remain separate capabilities.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import threading
import time


class AgentChannel:
    def __init__(self, app, read, write):
        self.app, self.read, self.write = app, read, write
        self.path = app.data / 'agent-channel.json'
        self.local_lock = threading.Lock()
        self.active = {}

    def state(self):
        value=self.read(self.path,{})
        fresh=time.time()-value.get('seen',0)<45
        return {'connected':fresh and value.get('connected',False),
                'lastSeen':value.get('seen'), 'transport':value.get('transport','native-files'),
                'host':value.get('host','workbuddy')}

    def authorize(self, request, allow_disconnected=False):
        record=self.read(self.path,{})
        token=request.get('connectionToken','')
        if (not record.get('connected') and not allow_disconnected) or not isinstance(token,str) or not secrets.compare_digest(token,record.get('token','')):
            raise ValueError('接单连接已失效，请重新连接本地文件通道')
        stop=self.read(self.app.data/'agent-control.json',{}).get('stopSequence',0)
        if stop!=record.get('stopSequence',0) and not allow_disconnected:
            record['connected']=False;self.write(self.path,record);raise ValueError('已按明确请求断开接单，等待用户再次连接')
        record['seen']=time.time();self.write(self.path,record)
        return record

    def connect(self, request):
        if request.get('host','workbuddy') not in ('workbuddy','codex'):raise ValueError('不支持这个宿主')
        client=request.get('clientId','')
        if not isinstance(client,str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,100}',client): raise ValueError('连接标识格式错误')
        old=self.read(self.path,{})
        if self.state()['connected'] and old.get('clientId')!=client: raise ValueError('已有主 Agent 接单，请勿重复连接')
        token=old.get('token') if old.get('clientId')==client and old.get('connected') else secrets.token_urlsafe(32)
        self.write(self.path,{'clientId':client,'token':token,'host':request.get('host','workbuddy'),
                             'transport':'native-files','connected':True,'seen':time.time(),'stopSequence':self.read(self.app.data/'agent-control.json',{}).get('stopSequence',0)})
        self.write(self.app.data/'agent-connection.json',{'state':'waiting','seen':time.time(),'transport':'native-files'})
        return {'connected':True,'connectionToken':token,'guide':str(Path(__file__).with_name('AGENT.md'))}

    def request_connection(self, reason):
        value=self.read(self.app.data/'connection-request.json',{})
        if value.get('status')=='pending' and time.time()-value.get('created',0)<60: return value
        value={'id':secrets.token_hex(6),'reason':reason,'status':'pending','created':time.time()}
        self.write(self.app.data/'connection-request.json',value)
        return value

    def handle(self, request):
        op=request.get('op')
        if op=='connect': return self.connect(request)
        if op=='poll':
            self.authorize(request)
            if self.app.workbench_host == 'local':
                return {'state':'waiting','instruction':'此工作台由本机程序接单。'}
            self.write(self.app.data/'agent-connection.json',{'state':'waiting','seen':time.time(),'transport':'native-files'})
            self.write(self.app.data/'connection-request.json',{'status':'connected','updated':time.time()})
            head=self.app.queue.head()
            if head and head.get('recoveryRequested') and not head.get('recoveryDispatched'):
                with self.app.transaction():
                    data=self.app.queue.data();head=self.app.queue.item(head['id'],data);head.update(recoveryDispatched=True,recoveryState='dispatched');self.app.queue.save(data)
                return {'state':'inspect','id':head['id'],'lease':head['lease'],'agentId':head.get('platformAgentId') or head.get('workerId'),'instruction':'用原生状态/停止工具确认真实执行者已结束，再以 recover 和实际 evidence 登记；无法确认就保持锁。'}
            row=self.app.queue.claim_next()
            if not row:
                result={'state':'waiting','queue':self.app.queue.public()}
                if head and head['status']=='needs_input':
                    result['waitingFor']='worker-exit' if head.get('answerPending') else 'workbench-answer'
                    result['instruction']='保持主对话接单，继续 poll。工作台负责收集补充，无需在聊天中再提问。'
                return result
            self.write(self.app.data/'agent-connection.json',{'state':'working','seen':time.time(),'transport':'native-files','item':row['id'],'task':row['taskId']})
            return {'state':'dispatch','id':row['id'],'kind':row['kind'],'lease':row['lease'],
                    'prompt':str(self.app.data/'queue-workers'/row['id']/'PROMPT.md'),
                    'instruction':self.app.queue.dispatch_text(row,native=True)}
        if op=='disconnect':
            record=self.authorize(request);record.update(connected=False,seen=0);self.write(self.path,record)
            control=self.read(self.app.data/'agent-control.json',{});self.write(self.app.data/'agent-control.json',{'stopSequence':control.get('stopSequence',0)+1})
            self.write(self.app.data/'agent-connection.json',{'state':'disconnected','seen':0})
            return {'disconnected':True}
        ident,lease=request.get('id'),request.get('lease')
        if op=='attach':
            self.authorize(request)
            with self.app.transaction():
                data,row=self.app.queue.checked(ident,lease)
                if row.get('localExecution'):raise ValueError('本条由本机独立执行')
                agent=request.get('agentId')
                if not isinstance(agent,str) or not 1<=len(agent)<=180:raise ValueError('必须提供真实原生 Agent 标识')
                if row.get('platformAgentId') and row['platformAgentId']!=agent:raise ValueError('本条已经绑定其他原生执行者')
                if any(x.get('platformAgentId')==agent and x['id']!=ident for x in data['items']):raise ValueError('不同视频不能复用原生执行者')
                row['platformAgentId']=agent;self.app.queue.save(data)
            return {'attached':True}
        if op=='settle':
            self.authorize(request,allow_disconnected=True)
            row=self.app.queue.settle(ident,lease,request.get('agentId'))
            return {'status':row['status'],'settled':row['settled']}
        if op=='recover':
            self.authorize(request,allow_disconnected=True)
            self.app.queue.recover(ident,lease,request.get('evidence'))
            return {'recovered':True}
        if op=='accept':
            row=self.app.queue.accept(ident,lease,request.get('workerId'))
            return {'status':row['status']}
        if op=='report':
            phase=request.get('phase')
            if not isinstance(phase,int) or isinstance(phase,bool) or not 0<=phase<=5: raise ValueError('阶段索引错误')
            if request.get('status','running') not in ('running','needs_input','failed','completed'): raise ValueError('作业状态错误')
            row=self.app.queue.report(ident,lease,phase,request.get('status','running'),request.get('message',''),request.get('question'))
            return {'status':row['status']}
        if op=='run-status':
            self.app.queue.checked(ident,lease)
            run=request.get('runId','')
            if not re.fullmatch(r'[a-f0-9]{24}',run): raise ValueError('执行记录不存在')
            value=self.read(self.app.data/'pipeline-runs'/ident/(run+'.json'),{})
            if value.get('leaseHash')!=hashlib.sha256(lease.encode()).hexdigest(): raise ValueError('执行记录与当前租约不匹配')
            return {k:v for k,v in value.items() if k!='leaseHash'}
        if op=='run': return self.start(request)
        raise ValueError('不支持这个本地通道操作')

    def has_active(self, ident):
        thread=self.active.get(ident)
        if thread and thread.is_alive():return True
        row=self.app.queue.item(ident)
        for folder in (Path(row['job']),self.app.data/'queue-workers'/ident):
            record=self.read(folder/'pipeline-process.json',{})
            if record.get('state')=='running':
                try:os.kill(record['pid'],0)
                except ProcessLookupError:pass
                else:return True
        return False

    def execution_restored(self, ident):
        path=self.app.data/'transport-fault.json';fault=self.read(path,{})
        if fault.get('state')=='blocked' and fault.get('item')==ident:
            self.write(path,{**fault,'state':'recovered','message':'独立本地执行服务已恢复响应','recoveredAt':time.time()})

    def start(self, request):
        _,row=self.app.queue.checked(request.get('id'),request.get('lease'))
        if row['status']!='running': raise ValueError('子 Agent 须先领取再执行')
        action=request.get('action')
        allowed=('prepare-planning','planning-check') if row['kind']=='planning' else ('prepare-video','advance','review','snapshot','check')
        if action not in allowed: raise ValueError('该任务不允许此阶段操作')
        with self.local_lock:
            active=self.active.get(row['id'])
            if active and active.is_alive(): raise ValueError('本条已有阶段运行，请先读取其执行结果')
            run=secrets.token_hex(12);path=self.app.data/'pipeline-runs'/row['id']/(run+'.json')
            initial={'runId':run,'state':'running','action':action,'started':time.time(),
                     'leaseHash':hashlib.sha256(request['lease'].encode()).hexdigest()}
            self.write(path,initial)
            thread=threading.Thread(target=self.execute,args=(dict(row),dict(request),path,initial),daemon=True)
            self.active[row['id']]=thread;thread.start()
        return {'runId':run,'state':'running'}

    def execute(self, row, request, path, initial):
        try:
            from pipeline import Pipeline
            runner=Pipeline(self.app,self.read,self.write)
            lease=request['lease'];action=request['action']
            if action in ('prepare-planning','planning-check','prepare-video'):
                task=self.app.task(row['taskId'])
                if action=='prepare-video':
                    folder=Path(row['job'])
                    if (folder/'runtime.json').is_file():
                        self.execution_restored(row['id'])
                        self.write(path,{**initial,'state':'finished','result':{'resuming':True,'job':str(folder)},'finished':time.time()});return
                else:folder,_=self.app.planning.initialize(task)
                if action in ('prepare-planning','prepare-video'):
                    # Inventory has a different CLI signature; no caller-controlled command.
                    args=['inventory',task['source'],'--out',folder]
                    if action=='prepare-video' and task.get('kind')=='revision' and not task.get('revisionFromArchive'):
                        plan=self.app.data/'queue-workers'/row['id']/'revised-plan.json'
                        if not plan.is_file():raise ValueError('请先写 revised-plan.json，再准备单条重做')
                        args=['revise',task['baseJob'],'--out',folder,'--plan',plan]
                    code,result=runner.invoke(row,lease,args[0],job=folder,cli_args=args)
                    if code: raise ValueError(str(result))
                    result['userFactsSource']=self.app.planning.append_facts(task,folder)
                else:
                    def validate(job):
                        code,value=runner.invoke(row,lease,'validate-plan',job=job)
                        if code: raise ValueError(str(value))
                    result=self.app.planning.check(task,validate)
            elif action=='advance': result=runner.advance(row['id'],lease)
            elif action=='review':
                result=runner.review(row['id'],lease,request.get('reviewKind'),request.get('note'),
                                     request.get('frames'),findings=request.get('findings'))
            else:
                code,result=runner.invoke(row,lease,action)
                if code: raise ValueError(str(result))
            if action=='prepare-planning':self.app.queue.report(row['id'],lease,1,'running','共享素材整理完成，正在统一策划文案与分镜')
            if action=='planning-check':self.app.queue.report(row['id'],lease,2,'running','整批计划校验通过，等待策划子 Agent 结束确认')
            self.execution_restored(row['id'])
            self.write(path,{**initial,'state':'finished','result':result,'finished':time.time()})
        except Exception as exc:
            text=self.app.redact(str(exc))[:2400]
            fault='host_unavailable' if any(x in text.lower() for x in ('econnrefused','sandbox-center','socket connection')) else 'stage_error'
            self.write(path,{**initial,'state':'failed','error':text,'fault':fault,'finished':time.time()})
            if fault=='host_unavailable':
                self.write(self.app.data/'transport-fault.json',{'state':'blocked','message':'宿主执行通道失联，请恢复原生后台执行会话；已有资产和云端任务 ID 保留','updated':time.time(),'item':row['id']})
