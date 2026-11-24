"""One final output per stable Lot identity, shared by scoring and all views."""
import math
from datetime import timedelta
from input_rules import parse_datetime

DAY=86400000


def time_ms(value):
    if value is None:return None
    if isinstance(value,(int,float)):
        return value if math.isfinite(value) else None
    return parse_datetime(value).timestamp()*1000


def output_ledger(snapshot, predictions=()):
    predicted={}
    for row in predictions:
        if row['uid'] in predicted:
            raise ValueError('产出候选 UID 重复：'+row['uid'])
        predicted[row['uid']]=row
    first=parse_datetime(snapshot['month']+'-01')
    last=(first.replace(day=28)+timedelta(days=4)).replace(day=1)
    start,end=first.timestamp()*1000,last.timestamp()*1000
    rows=[]
    ids=set()
    for lot in snapshot['lots']:
        uid=lot['uid']
        if uid in ids:raise ValueError('产出账本 UID 重复：'+uid)
        ids.add(uid)
        if lot['state']=='DONE':
            complete,source=time_ms(lot['actualComplete']),'actual'
        elif lot['state'] in ('RUNNING','EXTERNAL'):
            complete,source=time_ms(lot.get('externalComplete')),'external'
        elif lot['state']=='HOLD' or not lot.get('availableAt') or not lot.get('shipDue'):
            complete,source=None,'unconfirmed'
        else:
            prediction=predicted.get(uid,{})
            complete=time_ms(prediction.get('complete'))
            source='frozen' if prediction.get('frozen') else 'schedule'
        rows.append(dict(uid=uid,ppn=lot['ppn'],wafer=lot['wafer'],state=lot['state'],source=source,
                         complete=complete,shipDue=time_ms(lot.get('shipDue')),
                         inMonth=complete is not None and start<=complete<end))
    products=[]
    for demand in snapshot['demands']:
        selected=[r for r in rows if r['ppn']==demand['ppn']]
        actual=sum(r['wafer'] for r in selected if r['source']=='actual' and r['inMonth'])
        forecast=sum(r['wafer'] for r in selected if r['inMonth'])
        products.append(dict(ppn=demand['ppn'],target=demand['wafer'],actual=actual,forecast=forecast,
                             effective=min(demand['wafer'],forecast),shortage=max(0,demand['wafer']-forecast),
                             rate=min(1,forecast/demand['wafer']) if demand['wafer'] else 1,
                             externalUnknownWafers=sum(r['wafer'] for r in selected if r['state'] in ('RUNNING','EXTERNAL') and r['complete'] is None)))
    # Evaluation membership comes from input, never from tasks a candidate kept.
    late=unserved=weighted=0
    for row in rows:
        due=row['shipDue'];complete=row['complete']
        # The monthly service scope is fixed for both served and unserved rows.
        # A next-month deadline is normal carry-over in either candidate.
        if due is None or due>=end:continue
        if complete is None:
            late+=row['wafer'];unserved+=row['wafer']
        elif complete>due:
            late+=row['wafer'];weighted+=(complete-due)*row['wafer']
    return dict(rows=rows,products=products,lateWafers=late,unservedDueWafers=unserved,
                weightedDelayMs=weighted,monthStart=start,monthEnd=end)


def result_ledger(result):
    data=result['data'];source=data['productionLotUids']
    predictions=list(data.get('productionFrozen',[]))
    predictions += [dict(uid=source[int(l['key'].split('-')[1])-1],complete=l.get('complete')) for l in result['lots']]
    return output_ledger(data['productionSnapshot'],predictions)
