#!/usr/bin/env node
// Behavior tests for Blender curves and safe complete travel paths.
const fs=require('node:fs'), vm=require('node:vm'), assert=require('node:assert/strict');
const load=(file)=>{
    const scope=vm.createContext({});
    vm.runInContext(fs.readFileSync(file,'utf8').replace(/^\.pragma library\s*/m,''),scope);
    return scope;
};
const curves=load('modules/abyss/companion/AquaMotionData.js');
const placement=load('modules/abyss/companion/AquaSurfacePlacement.js');
for (const clip of Object.values(curves.clips)) {
    for (const key of Object.keys(clip.tracks)) {
        assert.equal(/^(arm|foot)/.test(key), false, 'limbless Aqua has no arm or foot tracks');
    }
}
for (let phase=0;phase<=1;phase+=.005) {
    const lift=curves.sample('walk','lift',phase);
    const sx=curves.sample('walk','scaleX',phase), sy=curves.sample('walk','scaleY',phase);
    assert.ok(Number.isFinite(lift)&&lift>=-11&&lift<=2);
    assert.ok(sx>=.93&&sx<=1.08);
    assert.ok(sy>=.92&&sy<=1.08);
}
assert.ok(curves.sample('walk','lift',.25)<-8);
assert.ok(curves.sample('walk','lift',.75)<-8);
assert.equal(curves.sample('walk','lift',0),0);
assert.equal(curves.sample('walk','lift',.5),0);
assert.ok(curves.sample('walk','scaleY',.25)>1.04);
assert.ok(curves.sample('walk','scaleY',0)<.97);
assert.equal(curves.sample('orbit','angle',0),0);
assert.ok(Math.abs(curves.sample('orbit','angle',1)-2*Math.PI)<0.000001);
for (const clip of Object.values(curves.clips)) {
    for (const track of Object.values(clip.tracks)) {
        assert.equal(track[0][0],0); assert.equal(track.at(-1)[0],1);
        for (let i=1;i<track.length;i++) assert.ok(track[i][0]>track[i-1][0]);
    }
}
const p={qualified:true,freeInterval:[260,580],center:420};
for (let fraction=0;fraction<=1;fraction+=.01) {
    const r=placement.wander(p,420,fraction,1);
    if (!r.qualified) continue;
    assert.ok(r.center>=260&&r.center<=580);
    assert.equal(r.duration,Math.round(Math.abs(r.center-420)/32*1000));
    for(let t=0;t<=1;t+=.01) assert.ok(420+(r.center-420)*t>=260 && 420+(r.center-420)*t<=580);
}
assert.equal(placement.wander(p,240,.9,1).qualified,false,'never cross the module separating slots');
assert.equal(placement.wander(p,420,NaN,1).qualified,false);
assert.equal(placement.wander(p,420,.9,0).qualified,false);
let found=0;
for (const edge of ['top','right','bottom','left']) {
    for (let x=20;x<900;x+=61) for(let y=20;y<600;y+=79) {
        const surface={x,y,width:230,height:190};
        const obstacle={x:400,y:300,width:240,height:100};
        const r=placement.besideSurface(surface,edge,1200,850,112,98,[obstacle]);
        if (!r.qualified) continue;
        found++;
        const host={x:r.x,y:r.y,width:112,height:98};
        assert.ok(r.x>=8&&r.y>=8&&r.x+112<=1192&&r.y+98<=842);
        assert.equal(placement.intersects(host,surface,11.999),false);
        assert.equal(placement.intersects(host,obstacle,8),false);
    }
}
assert.ok(found>100);
assert.equal(placement.besideSurface({x:8,y:8,width:984,height:684},'top',1000,700,112,98,[]).qualified,false);
assert.equal(placement.besideSurface({x:10,y:10,width:300,height:200},'top',1200,850,112,98,[{x:NaN}]).qualified,false);
if(process.argv[2]) {
    let max=0;
    for(const [clip,channel,phase,value] of JSON.parse(fs.readFileSync(process.argv[2],'utf8'))) {
        const error=Math.abs(curves.sample(clip,channel,phase)-value);
        max=Math.max(max,error);
        // Blender evaluates in float32. Bound input-frame rounding and the
        // float arithmetic separately; large spatial angles need a scaled
        // bound rather than the old constant tuned for small gait offsets.
        const keys=curves.clips[clip].tracks[channel],frames=curves.clips[clip].duration*60/1000;
        const upper=keys.findIndex((k,i)=>i>0 && phase<=k[0]);
        const a=keys[Math.max(0,upper-1)],b=keys[upper<0 ? keys.length-1 : upper];
        const slope=upper<0 ? 0 : Math.abs((b[1]-a[1])/((b[0]-a[0])*frames));
        const frame=1+phase*frames;
        const ulp=Math.pow(2,Math.floor(Math.log2(Math.max(1,Math.abs(a[1]),Math.abs(b[1]))))-23);
        const bound=slope*Math.abs(Math.fround(frame)-frame)+8*ulp;
        assert.ok(error<=bound,`Blender float32 parity ${clip}.${channel}@${phase}: ${error} > ${bound}`);
    }
    console.log(`AQUA_BLENDER_CURVE_PARITY_PASS max_error=${max}`);
}
console.log('AQUA_MOTION_CURVES_AND_SAFE_TRAVEL_PASS');
