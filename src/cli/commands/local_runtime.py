"""Local identities and the stdio ACP entrance to the existing WeBot runtime."""
import argparse
import asyncio
import json
import sys

from src.backend.common.local_accounts import accounts, default_account, select_account


def main(command, argv=None, *, user=None):
    parser = argparse.ArgumentParser(prog='clawcross '+command)
    parser.add_argument('-u', '--user', default=user)
    if command == 'accounts':
        parser.add_argument('--json', action='store_true')
        args = parser.parse_args(argv)
        default = default_account(create=True)
        rows = accounts()
        if args.json:
            print(json.dumps({'default':default,'accounts':rows}, ensure_ascii=False))
        else:
            for name in rows:
                print(name + (' · 默认' if name == default else ''))
            print('使用 --user <账号> 选择入群或 ACP 身份；记录按账号隔离。')
        return 0
    parser.add_argument('--agent', help='复用当前账号的已有 WeBot Agent')
    parser.add_argument('--name', default='我的伙伴', help='ACP session/new 的伙伴名称')
    parser.add_argument('--template', choices=['chat','group','personal'], default='group')
    args = parser.parse_args(argv)
    owner = select_account(args.user, create=True) if args.user else default_account(create=True)
    from src.cli.commands.webot_acp import serve
    try:
        asyncio.run(serve(owner=owner, agent_id=args.agent, name=args.name, template=args.template))
    except (OSError, ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0
