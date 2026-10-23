"""Publish the example as a standalone HTML miniapp; no client build is needed."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def build(output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    sdk = (ROOT / "src/frontend/static/js/room-sdk.js").read_text()
    game = (ROOT / "examples/room-apps/creature-battle/battle.js").read_text()
    entry = """<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>精灵对战</title><style>
html,body{height:100%;margin:0;font-family:system-ui,sans-serif;background:#fff;color:#17252d}
#room-root{height:100%;overflow:auto}.cc-room-battle{min-height:100%;box-sizing:border-box}
@media(prefers-color-scheme:dark){body{background:#15212b;color:#e0eaf1}}
</style></head><body><main id="room-root"></main>""" + "\n"
    for source in (sdk, game):
        entry += "<script>" + source.replace("</script", r"<\/script") + "</script>\n"
    entry += """<script>
(function(){
  const root=document.getElementById('room-root'),api=window.ClawCrossRoomApp;
  if(!api){root.textContent='这是一个独立的房间小程序，请通过房间分享链接打开。';return;}
  const client=new ClawCrossRooms.RoomClient({
    send:(content,mentions)=>api.send(content,mentions),
    configure:announcement=>api.configure(announcement),
    loadHistory:()=>api.history(),
    onComposer:value=>api.composer(value).catch(()=>{}),
    onSuggest:text=>api.suggest(text).catch(()=>{})
  });
  const game=ClawCrossBattle.mount(root,client);
  api.onSubmit(text=>game.submit(text));
  api.onSnapshot(value=>{client.update(value);game.update(client.snapshot);});
})();
</script></body></html>
"""
    raw = entry.encode()
    (output / "index.html").write_bytes(raw)
    manifest = {
        "protocol": "clawcross.room-app/1",
        "id": "creature-battle", "name": "精灵对战", "version": "1.0.0",
        "entry": "index.html", "sha256": hashlib.sha256(raw).hexdigest(),
        "description": "四技能精灵、属性克制与 SVG 对战",
    }
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(f"Published miniapp: {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    build(parser.parse_args().output)
