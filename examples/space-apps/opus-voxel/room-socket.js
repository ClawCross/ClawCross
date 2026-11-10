/* A small WebSocket endpoint (RFC 6455) for an app server without dependencies: JSON text messages,
 * fragmented frames, ping/pong and close. Pages reach it through the space's group server. */
'use strict';
const crypto=require('node:crypto');
const {EventEmitter}=require('node:events');
const LIMIT=8*1024*1024;

function accept(req,socket,head){
 const key=req.headers['sec-websocket-key'];
 if(!key||String(req.headers.upgrade||'').toLowerCase()!=='websocket'){socket.end('HTTP/1.1 400 Bad Request\r\n\r\n');return null;}
 const answer=crypto.createHash('sha1').update(key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').digest('base64');
 socket.write('HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: '+answer+'\r\n\r\n');
 socket.setNoDelay(true);
 const conn=new EventEmitter();
 let buffer=head?.length?Buffer.from(head):Buffer.alloc(0),parts=[],size=0,closed=false;
 function frame(op,payload){
  if(closed)return;
  const n=payload.length,header=n<126?Buffer.from([0x80|op,n]):n<65536?Buffer.from([0x80|op,126,n>>8,n&255]):Buffer.alloc(10);
  if(n>=65536){header[0]=0x80|op;header[1]=127;header.writeBigUInt64BE(BigInt(n),2);}
  socket.write(Buffer.concat([header,payload]));
 }
 conn.send=message=>frame(1,Buffer.from(typeof message==='string'?message:JSON.stringify(message)));
 conn.close=(code=1000)=>{if(closed)return;const b=Buffer.alloc(2);b.writeUInt16BE(code);frame(8,b);closed=true;socket.end();};
 // Proxies drop quiet connections: ping now and then; browsers answer by themselves.
 const ping=setInterval(()=>frame(9,Buffer.alloc(0)),25000);
 socket.on('data',chunk=>{
  buffer=buffer.length?Buffer.concat([buffer,chunk]):chunk;
  while(buffer.length>=2){
   const fin=buffer[0]&0x80,op=buffer[0]&15,masked=buffer[1]&0x80;
   let n=buffer[1]&127,offset=2;
   if(n===126){if(buffer.length<4)return;n=buffer.readUInt16BE(2);offset=4;}
   else if(n===127){if(buffer.length<10)return;n=Number(buffer.readBigUInt64BE(2));offset=10;}
   if(n>LIMIT){conn.close(1009);return;}
   if(buffer.length<offset+(masked?4:0)+n)return;
   const mask=masked?buffer.subarray(offset,offset+4):null;if(masked)offset+=4;
   const payload=Buffer.from(buffer.subarray(offset,offset+n));buffer=buffer.subarray(offset+n);
   if(mask)for(let i=0;i<payload.length;i++)payload[i]^=mask[i&3];
   if(op===8){conn.close();return;}
   if(op===9){frame(10,payload);continue;}
   if(op===10)continue;
   parts.push(payload);size+=payload.length;
   if(size>LIMIT){conn.close(1009);return;}
   if(!fin)continue;
   const text=Buffer.concat(parts).toString();parts=[];size=0;
   conn.emit('message',text);
  }
 });
 const end=()=>{if(conn.ended)return;conn.ended=true;closed=true;clearInterval(ping);conn.emit('close');};
 socket.on('close',end);socket.on('error',()=>{socket.destroy();end();});
 return conn;
}
module.exports={accept};
