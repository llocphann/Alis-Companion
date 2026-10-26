const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const travel=vm.createContext({});vm.runInContext(fs.readFileSync(path.join(__dirname,'../modules/abyss/companion/AquaTravel.js'),'utf8').replace(/^\.pragma.*$/mg,''),travel);
for(const scale of [.5,1,1.25,2,3]) {
 const source={x:30*scale,y:200*scale},near={x:400*scale,y:200*scale,grounded:true},far={x:800*scale,y:200*scale,grounded:true};
 assert(!travel.usePortal(source,near,scale));assert(travel.usePortal(source,far,scale));assert(!travel.usePortal(source,{...far,grounded:false},scale));
 for(const sign of [-1,1]) {
  for(let i=0;i<=100;i++)assert.equal(travel.rollingAngle(145*scale,i/100,sign,scale),0);
 }
}
for(const invalid of [NaN,Infinity,-Infinity,undefined])assert(!travel.usePortal({x:invalid,y:0},{x:800,y:0,grounded:true},1));
for(let i=0;i<=1000;i++) {
 const t=i/1000;
 assert(travel.reveal(t)>=0 && travel.reveal(t)<=1);
 assert(travel.opening(t)>=0 && travel.opening(t)<=1);
 assert(travel.approachProgress(t)>=0 && travel.approachProgress(t)<=1);
 assert(travel.exitProgress(t)>=0 && travel.exitProgress(t)<=1);
 if(t>=.4 && t<=.6)assert.equal(travel.reveal(t),0);
 if(t>=.4 && t<.5)assert.equal(travel.approachProgress(t),1);
 if(t>=.5 && t<=.6)assert.equal(travel.exitProgress(t),0);
}
assert.equal(travel.reveal(0),1);assert.equal(travel.reveal(1),1);assert.equal(travel.opening(0),0);assert.equal(travel.opening(1),0);
assert.equal(travel.approachProgress(0),0);assert.equal(travel.exitProgress(1),1);
assert.equal(travel.portalMoving(.2),true);assert.equal(travel.portalMoving(.5),false);assert.equal(travel.portalMoving(.75),true);
console.log('AQUA_TRAVEL_PASS finite portal threshold approach-exit choreography hidden handover bounded lifetime retired rolling generator');
