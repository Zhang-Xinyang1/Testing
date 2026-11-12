"""Small, explicit synthetic MES snapshot for reviewing production workflows."""
from datetime import datetime, timedelta, timezone


def demo_snapshot():
    now = datetime.now(timezone(timedelta(hours=8)))
    # Use the current calendar month, but a reproducible review time in it.
    anchor = now.replace(day=min(now.day, 20), hour=8, minute=0, second=0, microsecond=0)
    iso = lambda hours: (anchor + timedelta(hours=hours)).isoformat()
    products = ['DEMO01', 'DEMO02', 'DEMO03']
    lots = []
    for i in range(12):
        ppn = products[i % 3]
        lots.append({'uid': 'DEMO-LOT-%03d' % i, 'lotId': ppn + '-L%03d' % i,
                     'ppn': ppn, 'wafer': 25, 'platform': 'CP-A;CP-B',
                     'state': 'READY', 'availableAt': iso(0), 'shipDue': iso(36 + i * 8),
                     'location': '测试待排区', 'source': '仓库'})
    lots[0].update(state='DONE', actualComplete=iso(-2), location='成品区')
    lots[1].update(state='RUNNING', location='CP-A-01', current={
        'eqp':'CP-A-01', 'step':'CP1', 'recipe':'R1', 'card':'CARD1',
        'start':iso(-1), 'end':iso(3)})
    lots[2].update(state='FUTURE', availableAt=iso(30), location='Fab', source='Fab')
    lots[3].update(state='FUTURE', availableAt=None, location='Fab', source='Fab')
    lots[4].update(state='HOLD', holdReason='模拟品质待确认', location='HOLD 区')
    return {'schemaVersion':1, 'snapshotId':'DEMO-' + anchor.strftime('%Y%m%d'),
            'source':'demo', 'identityStable':True, 'asOf':iso(0), 'month':anchor.strftime('%Y-%m'),
            'demands':[{'ppn':p, 'wafer':125} for p in products], 'lots':lots,
            'equipment':[{'id':t + '-%02d' % i, 'type':t, 'status':'RUN', 'recipe':'R1',
                          'card':'CARD1', 'proberId':'PROBE-' + t + '-%02d' % i}
                         for t in ('CP-A', 'CP-B') for i in (1,2)] +
                        [{'id':'OVEN-01', 'type':'OVEN', 'status':'RUN'}],
            'bakeChambers':2,
            'flows':{p:[['CP1','R1',2 + j], ['BAKE','210C24H',24], ['CP2','R2',3 + j]]
                     for j,p in enumerate(products)},
            'recipes':[{'recipe':'R1','cards':['CARD1']}, {'recipe':'R2','cards':['CARD2']}],
            'pm':[{'eqp':'CP-B-02','targetKind':'EQP','early':iso(0)[:10],
                   'due':iso(0)[:10], 'over':iso(24)[:10], 'hours':4, 'start':iso(4)}],
            'borrow':[{'eqp':'CP-B-01','recipe':'R1','date':iso(0)[:10], 'start':iso(0), 'hours':2}],
            'constraints':[], 'externalBlocks':[]}
