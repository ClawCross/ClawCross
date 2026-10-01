"""Host-only group administration. Does not need the Agent service."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
from common.runtime_paths import ENV_FILE
load_dotenv(ENV_FILE)
import httpx
from groups.config import service_key, service_url


def main():
    parser = argparse.ArgumentParser(description='本机群服务器管理（机器凭证，不经过 Agent）')
    parser.add_argument('action', choices=['list', 'create', 'patch', 'remove_member', 'member_patch', 'primary', 'delete'])
    parser.add_argument('--group-id')
    parser.add_argument('--data-file', help='JSON 文件，- 表示标准输入；避免把密码放入命令参数')
    args = parser.parse_args()
    if args.action not in {'list', 'create'} and not args.group_id:
        parser.error('需要 --group-id')
    data = json.load(sys.stdin) if args.data_file == '-' else json.loads(Path(args.data_file).read_text()) if args.data_file else {}
    path = '/relay/admin/groups' + (('/' + args.group_id + '/' + args.action) if args.action not in {'list', 'create'} else '')
    if args.action == 'create':
        path = '/relay/create'
        data = {'node_id': 'host-admin', 'user_id': 'host-admin', 'display_name': '服务器主机', **data}
    try:
        with httpx.Client(timeout=15, trust_env=False) as client:
            response = client.request('GET' if args.action == 'list' else 'POST', service_url() + path,
                                      headers={'X-Group-Service-Key': service_key()}, json=data if args.action != 'list' else None)
        result = response.json()
        if args.action == 'create' and response.is_success:
            result = result['group']
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if response.is_success else 1
    except httpx.HTTPError:
        print('无法连接本机群服务器', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
