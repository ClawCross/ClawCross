"""Build the creature battle as one HTML page, the file a group owner uploads as the group's app.

    python examples/room-apps/creature-battle/build.py --output creature-battle.html
"""
from __future__ import annotations

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

PAGE = """<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>精灵对战</title><style>
html,body{height:100%;margin:0;font-family:system-ui,sans-serif;background:#fff;color:#17252d}
body{display:flex;flex-direction:column}#room-root{flex:1;min-height:0;overflow:auto}.cc-room-battle{min-height:100%;box-sizing:border-box}
#chat{max-height:28%;overflow:auto;padding:6px 14px;font-size:13px;line-height:1.5;border-top:1px solid #e3e9ee}#chat:empty{display:none}
#chat p{margin:3px 0;overflow-wrap:anywhere}#chat b{font-weight:600;margin-right:6px}
#bar{display:grid;grid-template-columns:1fr auto auto;gap:6px;padding:8px 10px;border-top:1px solid #e3e9ee}
#hint{grid-column:1/-1;font-size:11px;color:#64748b;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#text{font:inherit;font-size:14px;resize:none;border:1px solid #cbd5e1;border-radius:12px;padding:8px 10px;min-width:0;background:inherit;color:inherit}
#bar button{font:inherit;font-size:13px;border:0;border-radius:12px;padding:0 14px;cursor:pointer;background:#edf2f5;color:#405367}
#bar button[data-command]{background:#14745b;color:#fff}#bar button:disabled{opacity:.45;cursor:default}
@media(prefers-color-scheme:dark){body{background:#15212b;color:#e0eaf1}#chat,#bar{border-color:#263644}#bar button{background:#263644;color:#d6e2ed}}
</style></head><body><main id="room-root"></main><section id="chat" aria-label="群聊"></section>
<div id="bar"><span id="hint" role="status"></span><textarea id="text" rows="1" maxlength="2000" aria-label="消息"></textarea>
<button type="button" data-command disabled>出招</button><button type="button" data-chat>聊天</button></div>
"""

GLUE = """<script>
(function(){
  const root=document.getElementById('room-root'),api=window.ClawCrossRoomApp;
  if(!api){root.textContent='这是群聊的小程序，请在群聊里打开。';return;}
  const chat=document.getElementById('chat'),text=document.getElementById('text'),hint=document.getElementById('hint');
  const command=document.querySelector('[data-command]'),talk=document.querySelector('[data-chat]'),seen=new Set();
  let composer=null;
  // Plain group messages are the room's chat; protocol messages drive the battle.
  function show(rows){
    for(const row of rows||[]){
      if(seen.has(row.id)||ClawCrossRooms.parseEvent(row.content))continue;
      seen.add(row.id);const line=document.createElement('p'),who=document.createElement('b');
      who.textContent=row.sender_name;line.append(who,document.createTextNode(row.content));chat.append(line);
    }
    while(chat.children.length>50)chat.firstElementChild.remove();
    chat.scrollTop=chat.scrollHeight;
  }
  function paint(){
    command.disabled=!composer||!composer.canSubmit;
    hint.textContent=composer?composer.label:'';
    text.placeholder=composer&&composer.canSubmit?composer.placeholder:'和大家聊聊';
  }
  const client=new ClawCrossRooms.RoomClient({
    send:(content,mentions)=>api.send(content,mentions),
    configure:announcement=>api.configure(announcement),
    loadHistory:async()=>{const rows=await api.history();show(rows);return rows;},
    onComposer:value=>{composer=value;paint();},
    onSuggest:value=>{text.value=value;text.focus();}
  });
  const game=ClawCrossBattle.mount(root,client);
  api.onSnapshot(value=>{client.update(value);show(value.messages);game.update(client.snapshot);});
  async function run(button,task){
    const value=text.value.trim();if(!value)return;
    button.disabled=true;
    try{await task(value);text.value='';button.disabled=false;paint();}
    catch(error){button.disabled=false;paint();hint.textContent=error.message||String(error);}
  }
  // The sandbox has no forms, so the buttons act on click.
  command.addEventListener('click',()=>run(command,value=>game.submit(value)));
  talk.addEventListener('click',()=>run(talk,value=>api.send(value,[])));
  paint();
})();
</script>"""


def build(output: Path) -> None:
    page = PAGE
    for source in ("src/frontend/static/js/room-sdk.js", "examples/room-apps/creature-battle/battle.js"):
        page += "<script>" + (ROOT / source).read_text().replace("</script", r"<\/script") + "</script>\n"
    output.write_text(page + GLUE + "</body></html>\n")
    print(f"Built group app: {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", required=True, type=Path)
    build(parser.parse_args().output)
