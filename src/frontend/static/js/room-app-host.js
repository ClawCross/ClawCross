/* Shows a group's app. The app is one HTML page stored on the group server; the
 * group card's `app` names it by sha256. This file hands the page to
 * room-app-frame.html, which this site serves into an opaque-origin sandbox under
 * its own locked policy. The app never receives host cookies or API tokens; it
 * reaches the group only through the calls below. This file is also vendored by
 * ClawCrossHub/lib/rooms.
 */
(function(root,factory){
  const Rooms=typeof module==='object'&&module.exports?require('./room-sdk.js'):root.ClawCrossRooms;
  const api=factory(Rooms);
  if(typeof module==='object'&&module.exports)module.exports=api;
  if(root)root.ClawCrossRoomHost=api;
})(typeof globalThis!=='undefined'?globalThis:this,function(Rooms){
  'use strict';
  /** What the app sees of the group: who is in it, who is looking, and messages it has not seen yet. */
  function snapshot(value,messages){
    const devices=new Map();
    const members=Rooms.normalizeMembers(value.members).map(member=>({
      principal:member.key,name:member.name,is_agent:member.is_agent,controller:member.controller,
      device:(()=>{if(!devices.has(member.device_key))devices.set(member.device_key,devices.size);return devices.get(member.device_key);})(),
      platform:member.platform||member.agent?.platform||''
    }));
    return {roomId:value.roomId,title:value.title||'',announcement:value.announcement||'',
      actor:{key:value.actor?.key||'',name:value.actor?.name||''},isOwner:!!value.isOwner,members,
      messages:(messages||[]).map(message=>({
        id:message.id,sender:message.sender,sender_name:message.sender_name||'',
        content:message.content,created_at:message.created_at,reply_to:message.reply_to||null
      }))};
  }
  function notice(container,text){
    const status=document.createElement('p');status.textContent=text;status.setAttribute('role','status');
    status.style.cssText='margin:0;padding:18px;font-size:13px;color:inherit';container.replaceChildren(status);
  }
  function mount(container,transport,page,frameUrl){
    let closed=false,port,current=null,loads=0,metadata='';
    const sent=new Set();
    const frame=document.createElement('iframe');frame.title=page.name;
    frame.setAttribute('sandbox','allow-scripts');frame.referrerPolicy='no-referrer';
    frame.setAttribute('allow',"camera 'none'; microphone 'none'; geolocation 'none'; clipboard-read 'none'; clipboard-write 'none'");
    frame.style.cssText='display:block;width:100%;height:100%;min-height:0;flex:1;border:0;background:transparent';
    function push(){
      if(!port||closed||!current)return;
      const delta=(current.messages||[]).filter(message=>!sent.has(message.id));
      const safe=snapshot(current,delta),key=JSON.stringify({...safe,messages:[]});
      if(!delta.length&&key===metadata)return;
      metadata=key;for(const message of delta)sent.add(message.id);
      port.postMessage({event:'snapshot',value:safe});
    }
    async function receive({data}){
      if(closed||typeof data?.id!=='string'||!data.id.startsWith('app:'))return;
      try{
        const args=data.args||{};let result={ok:true};
        switch(data.method){
          case 'send':
            if(typeof args.content!=='string'||args.content.length>65536||!Array.isArray(args.mentions)||
              args.mentions.length>128||args.mentions.some(id=>typeof id!=='string'))throw new Error('消息参数无效');
            await transport.send(args.content,args.mentions);break;
          case 'configure':
            if(!current?.isOwner)throw new Error('只有群主可以修改群公告');
            if(typeof args.announcement!=='string')throw new Error('群公告无效');
            await transport.configure(args.announcement);break;
          case 'history':
            result=snapshot(current||{},await transport.loadHistory()).messages;break;
          default:throw new Error('不支持的调用：'+String(data.method));
        }
        port?.postMessage({reply:data.id,result});
      }catch(error){port?.postMessage({reply:data.id,error:error.message||String(error)});}
    }
    frame.addEventListener('load',()=>{
      if(closed)return;
      // Writing the app into the frame page loads it once more; any later load is a navigation.
      if(++loads>2){destroy();notice(container,'小程序发生页面跳转，请重新打开群聊');return;}
      if(loads>1)return;
      const channel=new MessageChannel();port=channel.port1;port.onmessage=receive;port.start();
      frame.contentWindow.postMessage({type:'clawcross.room-app.connect',html:page.html},'*',[channel.port2]);push();
    });
    frame.src=frameUrl;container.replaceChildren(frame);
    function destroy(){if(closed)return;closed=true;port?.close();frame.remove();}
    return {update(value){current=value;push();},destroy};
  }
  /** Follows a group: shows its app while the card names one. `transport.loadApp()` returns the
   * page; `frameUrl` is this site's copy of room-app-frame.html. */
  function mountApp(container,transport,frameUrl){
    let handle=null,key='',current=null,ticket=0;
    function close(){ticket++;handle?.destroy();handle=null;container.replaceChildren();}
    return {
      update(value){
        current=value;
        const next=value.app?value.app.sha256:'';
        if(next!==key){
          close();key=next;
          if(next){
            const mine=ticket;notice(container,'正在打开小程序…');
            transport.loadApp().then(page=>{if(mine===ticket){handle=mount(container,transport,page,frameUrl);handle.update(current);}},
              error=>{if(mine===ticket)notice(container,'小程序加载失败：'+(error.message||String(error)));});
          }
        }
        handle?.update(value);
      },
      destroy(){close();key='';}
    };
  }
  return {mountApp};
});
