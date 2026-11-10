"""Standalone MCP client for remote machines; only Python's standard library is needed."""
import json
import os
import sys
import urllib.request
import urllib.error


def request(body):
    with open(os.environ['CLAWCROSS_BRIDGE_CONFIG'],encoding='utf-8') as file:config=json.load(file)
    req=urllib.request.Request(config['url'],data=json.dumps(body).encode(),method='POST',
        headers={'Content-Type':'application/json','Authorization':'Bearer '+config['token']})
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req,timeout=10 if body.get('action')=='catalog' else None) as response:return json.load(response)
    except urllib.error.HTTPError as exc:
        return {'ok':False,'error':json.load(exc).get('detail','Tool request rejected')}


def main():
    for line in sys.stdin:
        try:
            packet=json.loads(line);method=packet.get('method');params=packet.get('params') or {}
            if 'id' not in packet:continue
            if method=='initialize':
                result={'protocolVersion':params.get('protocolVersion','2024-11-05'),'capabilities':{'tools':{}},
                        'serverInfo':{'name':'ClawCross','version':'1'}}
            elif method=='ping':result={}
            elif method=='tools/list':
                catalog=request({'action':'catalog'})
                result={'tools':[{'name':'tool_search','description':catalog.get('description') or 'Find ClawCross tools and exact parameters. Search first when unsure.',
                    'inputSchema':{'type':'object','properties':{'query':{'type':'string'}},'required':['query'],'additionalProperties':False}},
                    {'name':'tool_call','description':'Call an enabled ClawCross tool. Identity and review are enforced by the server.',
                    'inputSchema':{'type':'object','properties':{'tool_name':{'type':'string'},'arguments_json':{'type':'string'}},
                                   'required':['tool_name','arguments_json'],'additionalProperties':False}}]}
            elif method=='tools/call':
                args=params.get('arguments') or {}
                if params['name']=='tool_search':payload=request({'action':'search','query':args['query']})
                elif params['name']=='tool_call':
                    if len(args['arguments_json'])>200000:raise ValueError('Tool arguments too large')
                    arguments=json.loads(args['arguments_json'])
                    if not isinstance(arguments,dict):raise ValueError('Tool arguments must be an object')
                    payload=request({'action':'call','name':args['tool_name'],'arguments':arguments})
                else:raise ValueError('Unknown tool')
                result={'content':[{'type':'text','text':json.dumps(payload,ensure_ascii=False)}]}
            else:
                print(json.dumps({'jsonrpc':'2.0','id':packet['id'],'error':{'code':-32601,'message':'Unknown method'}}),flush=True);continue
            print(json.dumps({'jsonrpc':'2.0','id':packet['id'],'result':result}),flush=True)
        except Exception as exc:
            if isinstance(locals().get('packet'),dict) and 'id' in packet:
                print(json.dumps({'jsonrpc':'2.0','id':packet['id'],'error':{'code':-32603,'message':str(exc)}}),flush=True)


if __name__=='__main__':main()
