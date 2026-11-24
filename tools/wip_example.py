"""Generate a synthetic, sequential MES WIP exchange for a separate test workspace."""
import argparse
import json
from pathlib import Path
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from production import identity


def exchange(anchor):
    def at(minutes):
        return (anchor+timedelta(minutes=minutes)).isoformat()
    base=dict(schemaVersion=1, source='mes', snapshotId='SAMPLE-'+anchor.strftime('%Y%m%d-%H%M'),
              month=anchor.strftime('%Y-%m'),asOf=at(0),identityStable=True,
              demands=[dict(ppn='SAMPLE',wafer=50)],
              equipment=[dict(id='CP-01',type='T5830',status='RUN',proberId='PR-01',recipe='R1',card='C1')],
              flows={'SAMPLE':[['CP1','R1',1]]},recipes=[dict(recipe='R1',cards=['C1'])],lots=[])
    for uid in ('0001','0002'):
        base['lots'].append(dict(uid=uid,lotId='SAMPLE-L',ppn='SAMPLE',wafer=25,
                                 platform='T5830',state='FUTURE',shipDue=at(24*60)))
    def event(key,kind,uid,minute,**fields):
        return dict(eventId=base['snapshotId']+'-'+key,version=1,kind=kind,uid=uid,recordedAt=at(minute),**fields)
    batches=[
        [event('E1','ETA','0002',0,step='CP1',at=at(20)),
         event('E2','AVAILABLE','0002',0,step='CP1',at=at(20)),
         event('E3','START','0001',0,current=dict(operationId='OP1',step='CP1',eqp='CP-01',
               recipe='R1',card='C1',start=at(0),end=at(30)))],
        [event('E4','RELEASE','0001',25,operationId='OP1',step='CP1',eqp='CP-01',processEnd=at(24),releasedAt=at(25)),
         event('E5','FORECAST','0001',25,at=at(40))],
        [event('E6','COMPLETE','0001',40,at=at(39))]]
    yield '00_baseline',base
    parent=base['snapshotId']
    for n,events in enumerate(batches,1):
        batch_id=base['snapshotId']+'-B'+str(n)
        pages=[dict(batchId=batch_id,page=i+1,events=[e]) for i,e in enumerate(events)]
        batch=dict(schemaVersion=1,batchId=batch_id,baseSnapshotId=parent,
                   asOf=events[-1]['recordedAt'],pageCount=len(pages),total=len(events),pages=pages)
        yield f'0{n}_wip',batch
        parent='WIP-'+identity(batch)[:32]


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory',type=Path)
    args=parser.parse_args()
    args.directory.mkdir(parents=True,exist_ok=True)
    today=datetime.now(timezone(timedelta(hours=8)))
    anchor=today.replace(day=min(today.day,20),hour=8,minute=0,second=0,microsecond=0)
    for name,data in exchange(anchor):
        path=args.directory/(name+'.json')
        with path.open('x',encoding='utf-8') as stream:
            json.dump(data,stream,ensure_ascii=False,indent=2)
        print(path)
