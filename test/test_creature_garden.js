/* Progress is server-owned, mutation retries are idempotent, saves survive restart. */
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {Store}=require('../sdk/js/space-store');
const {Garden}=require('../examples/space-apps/creature-battle/garden');
const dir=fs.mkdtempSync(path.join(os.tmpdir(),'garden-test-'));
try{
 let garden=new Garden(new Store(dir,{rows:[],config:null}));
 let alice=garden.act('alice','adopt',{species:'spark',level:999,xp:999999,owner:'bob'});
 const id=alice.selected;assert.equal(alice.creatures[0].level,1);assert.equal(alice.creatures[0].xp,0);assert.equal(alice.coins,60);
 alice=garden.act('alice','train',{creature:id,focus:'attack',requestId:'once'});
 const once=structuredClone(alice);assert.deepEqual(garden.act('alice','train',{creature:id,focus:'attack',requestId:'once'}),once);
 alice=garden.act('alice','train',{creature:id,focus:'attack',requestId:'twice'});
 assert.equal(alice.creatures[0].level,2);assert.equal(alice.creatures[0].xp,30);assert.equal(alice.coins,44);
 const before=JSON.stringify(garden.store.data);assert.throws(()=>garden.act('alice','train',{creature:id,focus:'arbitrary'}));assert.equal(JSON.stringify(garden.store.data),before);
 assert.equal(garden.view('bob').creatures.length,0);assert.throws(()=>garden.act('bob','select',{creature:id}));
 alice=garden.act('alice','encounter',{creature:id});let n=0;
 alice=garden.act('alice','move',{creature:id,slot:2});
 assert.deepEqual(alice.encounter.events.map(event=>event.side),[0,1]);
 assert.equal(alice.encounter.events[0].type,'guard');assert.ok(alice.encounter.events[1].move);
 assert.equal(alice.encounter.events[1].hp,alice.encounter.hp);
 const effects=structuredClone(alice.encounter.events);
 alice=garden.act('alice','move',{creature:id,slot:0});
 assert.notEqual(alice.encounter.events[0].id,effects[0].id);
 assert.equal(alice.encounter.events[0].wildHp,alice.encounter.wildHp);
 while(!alice.encounter.done&&n++<30){const slot=alice.encounter.pp[0]>0?0:1;alice=garden.act('alice','move',{creature:id,slot,requestId:'move-'+n});}
 assert.ok(alice.encounter.done);assert.ok(['won','lost'].includes(alice.encounter.result));assert.ok(alice.coins>44);
 const saved=structuredClone(alice);garden=new Garden(new Store(dir,{rows:[],config:null}));assert.deepEqual(garden.view('alice'),saved);
 // Evolution and unlocks come from experience calculation, not client-supplied levels.
 garden.store.update(d=>{const c=d.garden.players.alice.creatures[0];c.level=4;c.xp=155;});
 alice=garden.act('alice','train',{creature:id,focus:'speed'});assert.equal(alice.creatures[0].level,5);assert.equal(alice.creatures[0].form,1);assert.ok(alice.creatures[0].unlocked.includes('喷射火焰'));
 alice=garden.act('alice','learn',{creature:id,move:'喷射火焰',slot:1});assert.equal(alice.creatures[0].skills[1],'喷射火焰');
 // Agent-designed fighters keep earned growth; a completed arena match pays once, including after restart.
 const fighter={name:'原创伙伴',svg:'<svg/>',elements:['fire'],baseStats:{hp:100,attack:100,defense:100,speed:100},moves:['火花','撞击','守住','自我再生'],skills:[]};
 garden.capture('alice','alpha',fighter);garden.capture('bob','beta',fighter);
 const result={winner:0,fighters:[{agent:{key:'alpha'}},{agent:{key:'beta'}}]};
 garden.reward('match-1',result,['alice','bob']);const rewarded=structuredClone(garden.store.data);
 garden=new Garden(new Store(dir,{rows:[],config:null}));garden.reward('match-1',result,['alice','bob']);
 assert.deepEqual(garden.store.data,rewarded);assert.equal(garden.levelFor('alpha').level,2);
 console.log('PASS: protected progression, retry deduplication, training, battle rewards, member isolation, reload, evolution and skill unlocks');
}finally{fs.rmSync(dir,{recursive:true,force:true});}
