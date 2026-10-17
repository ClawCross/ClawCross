/* App-owned state, outside Agent workspaces. Failed writes never replace the last good state. */
'use strict';
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
class Store {
  constructor(directory,initial,name='state.json'){
    this.file=directory?path.join(directory,name):null;
    if(this.file){fs.mkdirSync(directory,{recursive:true,mode:0o700});}
    try{this.data=this.file?JSON.parse(fs.readFileSync(this.file,'utf8')):structuredClone(initial);}
    catch(error){if(error.code!=='ENOENT')throw error;this.data=structuredClone(initial);}
  }
  update(change){
    const next=structuredClone(this.data),result=change(next);
    if(this.file){
      const temporary=this.file+'.'+crypto.randomBytes(6).toString('hex')+'.tmp';
      try{fs.writeFileSync(temporary,JSON.stringify(next),{mode:0o600,flag:'wx'});fs.renameSync(temporary,this.file);}
      finally{try{fs.unlinkSync(temporary);}catch(error){if(error.code!=='ENOENT')throw error;}}
    }
    this.data=next;return result;
  }
}
module.exports={Store};
