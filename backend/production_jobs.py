"""Bounded, cancellable production trials in a separate Python process."""
import copy
import multiprocessing as mp
import threading
import time
import uuid
from production import trial


def _worker(state, request, connection):
    try:
        def progress(plan, count):
            connection.send({'kind':'progress','plan':plan,'evaluations':count})
        plan = trial(state, request, progress)
        connection.send({'kind':'done','plan':plan})
    except Exception as error:
        connection.send({'kind':'error','message':str(error)})
    finally:
        connection.close()


class TrialJobs:
    def __init__(self):
        self.lock = threading.RLock()
        self.job = None

    def start(self, state, request):
        with self.lock:
            if self.job and self.job['status'] == 'running':
                raise ValueError('当前已有试排任务，请等待或取消')
            if not state.get('snapshot'):
                raise ValueError('请先载入演示数据或接收 MES 快照')
            receiver, sender = mp.get_context('spawn').Pipe(duplex=False)
            process = mp.get_context('spawn').Process(target=_worker,args=(copy.deepcopy(state),request,sender),daemon=True)
            budget = state['rules']['maxRuntimeSeconds']
            job = {'id':uuid.uuid4().hex,'status':'running','started':time.monotonic(),
                   'limitSeconds':budget,'evaluations':0,'plan':None,'message':'正在检查输入与搜索可行方案',
                   'process':process,'cancel':False}
            self.job = job
            process.start()
            sender.close()
            threading.Thread(target=self._watch,args=(job,receiver),daemon=True).start()
            return self.read()

    def _watch(self, job, receiver):
        try:
            while True:
                if receiver.poll(0.1):
                    try:
                        message = receiver.recv()
                    except EOFError:
                        break
                    with self.lock:
                        if message.get('plan'):
                            job['plan'] = message['plan']
                        job['evaluations'] = message.get('evaluations',job['evaluations'])
                        if message['kind'] == 'progress':
                            job['message'] = '已比较 %d 个方案' % job['evaluations']
                        elif message['kind'] == 'done':
                            job.update(status='completed',message='试排完成，尚未选用')
                            break
                        else:
                            job.update(status='failed',message=message['message'],plan=None)
                            break
                with self.lock:
                    elapsed = time.monotonic()-job['started']
                    if job['cancel'] or elapsed >= job['limitSeconds']:
                        job.update(status='cancelled' if job['cancel'] else 'timed_out',
                                   message='已停止计算；已找到的最好方案可查看并手动选用' if job['plan'] else '已停止计算，未找到可行方案')
                        break
                if not job['process'].is_alive():
                    break
        finally:
            if job['process'].is_alive():
                job['process'].terminate()
            job['process'].join(timeout=2)
            receiver.close()
            with self.lock:
                if job['status']=='running':
                    job.update(status='failed',message='计算进程提前结束，请重试',plan=None)
                job['elapsedSeconds']=round(time.monotonic()-job['started'],1)

    def read(self):
        with self.lock:
            if not self.job:
                return None
            job = {k:v for k,v in self.job.items() if k not in ('process','started','cancel')}
            if self.job['status']=='running' or 'elapsedSeconds' not in job:
                job['elapsedSeconds']=round(time.monotonic()-self.job['started'],1)
            return copy.deepcopy(job)

    def cancel(self, job_id):
        with self.lock:
            if not self.job or self.job['id'] != job_id:
                raise ValueError('试排任务已变化，请刷新')
            self.job['cancel'] = True
        return self.read()

    def plan(self, job_id):
        job = self.read()
        if not job or job['id'] != job_id or job['status'] == 'running' or not job.get('plan'):
            raise ValueError('请先完成试排，再选用候选方案')
        return job['plan']

    def close(self):
        with self.lock:
            if self.job and self.job['status']=='running':
                self.job['cancel']=True

    def clear(self):
        with self.lock:
            if self.job and self.job['status']=='running':
                raise ValueError('请先取消当前试排，再切换演示场景')
            self.job=None
