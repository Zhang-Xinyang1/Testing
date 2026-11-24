"""Collect one consistent WIP batch from a read-only MES adapter, or replay a JSON file."""
import argparse
import json
import os
from pathlib import Path
import sys
from urllib import error, parse, request

LIMIT = 20_000_000
HEADER = ('schemaVersion','batchId','baseSnapshotId','asOf','pageCount','total')


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('接口发生重定向，请直接配置最终 URL')


def get_json(req, limit):
    with request.build_opener(NoRedirect()).open(req, timeout=30) as response:
        data=response.read(limit+1)
    if len(data)>limit:
        raise ValueError('报文超过大小限制')
    return json.loads(data.decode('utf-8-sig')),len(data)


def pull(url, token=None):
    address=parse.urlsplit(url)
    if address.scheme not in ('https','http') or not address.hostname or address.username or address.password:
        raise ValueError('请输入不含凭据的 HTTP(S) 接口 URL')
    if token and address.scheme!='https':
        raise ValueError('使用 MES_WIP_TOKEN 时，接口须启用 HTTPS')
    query=dict(parse.parse_qsl(address.query,keep_blank_values=True))
    headers={'Accept':'application/json'}
    if token:
        headers['Authorization']='Bearer '+token
    remaining=LIMIT
    pages=[]
    meta=None
    page_number=1
    while meta is None or page_number<=meta['pageCount']:
        query['page']=str(page_number)
        if meta:
            query['batchId']=meta['batchId']
        endpoint=address._replace(query=parse.urlencode(query),fragment='').geturl()
        row,size=get_json(request.Request(endpoint,headers=headers),remaining)
        remaining-=size
        if not isinstance(row,dict) or any(k not in row for k in HEADER):
            raise ValueError('源分页缺少批次版本、时点或数量')
        actual={k:row[k] for k in HEADER}
        if meta is None:
            meta=actual
            if type(meta['pageCount']) is not int or not 1<=meta['pageCount']<=2000:
                raise ValueError('无效 pageCount')
        if meta!=actual or row.get('page')!=page_number or not isinstance(row.get('events'),list):
            raise ValueError('来源分页不一致，请重新读取完整批次')
        pages.append(dict(batchId=meta['batchId'],page=page_number,events=row['events']))
        page_number+=1
    if sum(len(p['events']) for p in pages)!=meta['total']:
        raise ValueError('来源分页的事件总数不符')
    return dict(meta,pages=pages)


def main():
    parser=argparse.ArgumentParser(description='只读采集 WIP，默认仅向排产程序提交校验')
    inputs=parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument('--url',help='MES 适配器的分页 GET URL')
    inputs.add_argument('--file',type=Path,help='已组装的完整 WIP 批次 JSON')
    parser.add_argument('--server',default='http://127.0.0.1:8080',help='排产程序地址')
    parser.add_argument('--publish',action='store_true',help='校验并接收批次')
    args=parser.parse_args()
    if args.file:
        if args.file.stat().st_size>LIMIT:
            raise ValueError('文件超过 20 MB')
        feed=json.loads(args.file.read_text(encoding='utf-8-sig'))
    else:
        feed=pull(args.url,os.getenv('MES_WIP_TOKEN'))
    action='import' if args.publish else 'preview'
    req=request.Request(args.server.rstrip('/')+'/api/production/wip-batch-'+action,
                        json.dumps(feed,ensure_ascii=False).encode(),{'Content-Type':'application/json'})
    receipt,_=get_json(req,LIMIT)
    print(json.dumps({k:v for k,v in receipt.items() if k!='rows'},ensure_ascii=False,indent=2))


if __name__=='__main__':
    try:
        main()
    except error.HTTPError as exc:
        print(f'HTTP {exc.code}: '+exc.read(2000).decode('utf-8','replace'),file=sys.stderr)
        sys.exit(1)
    except (ValueError,OSError) as exc:
        print(str(exc),file=sys.stderr)
        sys.exit(1)
