/* The creature battle as a group stores it: one HTML page built from the example app. */
const fs=require('node:fs'),os=require('node:os'),path=require('node:path'),crypto=require('node:crypto');
const {execFileSync}=require('node:child_process');
function battleApp(){
  const directory=fs.mkdtempSync(path.join(os.tmpdir(),'group-app-'));
  try{
    const output=path.join(directory,'creature-battle.html');
    execFileSync(process.platform==='win32'?'python':'python3',['examples/room-apps/creature-battle/build.py','--output',output],{cwd:path.resolve(__dirname,'../..')});
    const html=fs.readFileSync(output,'utf8');
    return {name:'精灵对战',sha256:crypto.createHash('sha256').update(html).digest('hex'),html};
  }finally{fs.rmSync(directory,{recursive:true,force:true});}
}
module.exports={battleApp};
