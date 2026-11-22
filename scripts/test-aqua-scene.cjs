#!/usr/bin/env node
// Exercise actual production geometry and attention, including every path point.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const load=(name,imports={})=>{
    const scope=vm.createContext(imports);
    vm.runInContext(fs.readFileSync(`modules/abyss/companion/${name}.js`,'utf8')
        .replace(/^\.(?:pragma|import).*\n/gm,''),scope);
    return scope;
};
const Slots=load('AquaSurfacePlacement'), api=load('AquaScene',{Slots}), attention=load('AquaAttention');
let cases=0, paths=0;
const scene=(scale=1,width=1280,height=820)=>({width,height,scale,hostWidth:112*scale,hostHeight:98*scale,
    insets:{top:44,right:20,bottom:20,left:20},records:[],blockers:[],surfaces:[]});
const checkPath=(s,from,to,route)=>{
    assert.equal(route.qualified,true);let previous=from;
    for(const next of route.points){
        for(let i=0;i<=100;i++){
            const t=i/100,p={x:previous.x+(next.x-previous.x)*t,y:previous.y+(next.y-previous.y)*t};
            assert.equal(api.clearAt(s,p),true,JSON.stringify({p,route}));cases++;
        }
        previous=next;
    }
    assert.equal(previous.x,to.x);assert.equal(previous.y,to.y);paths++;
};
const seen=new Set();
for(const scale of [.65,1,1.5]) for(const [w,h] of [[800,600],[1280,820],[1920,1080]]) {
    const s=scene(scale,w,h);
    for(let i=0;i<80;i++){
        const p=api.appearance(s,.8,(i%4+.1)/4,(i+.5)/80);
        assert.equal(p.qualified,true);assert.equal(api.clearAt(s,p),true);
        assert.equal(p.grounded,true,'all four water rims support an oriented walk');
        assert.equal(p.support.edge,p.edge);
        seen.add(p.edge);cases++;
    }
}
assert.deepEqual([...seen].sort(),['bottom','left','right','top']);
const s=scene();
const left={rect:{x:20,y:130,width:230,height:500},edge:'left',key:'leftPanel'};
const right={rect:{x:1030,y:130,width:230,height:500},edge:'right',key:'rightPanel'};
const popup={rect:{x:510,y:320,width:250,height:230},edge:'top',key:'popup'};
s.surfaces=[left,right,popup];s.blockers=s.surfaces.map(v=>v.rect);
const points=api.surfacePoints(s);
assert.ok(points.some(p=>p.key==='leftPanel'));assert.ok(points.some(p=>p.key==='rightPanel'));
assert.ok(points.some(p=>p.key==='popup'&&p.grounded));
let surfaces=0;
for(let i=0;i<100;i++){
    const p=api.appearance(s,(i+.5)/100,.3,.7);
    assert.equal(api.clearAt(s,p),true);if(p.kind==='surface')surfaces++;cases++;
}
assert.equal(surfaces,35,'35% initial surface chance, with free edges still eligible');
const ground=api.edgePoint(s,'bottom',.35),groundTo=api.edgePoint(s,'bottom',.65);
for (const edge of ['top','right','bottom','left']) {
    const home=api.homePoint(s,edge,.72);
    assert.equal(home.qualified,true);
    assert.equal(home.edge,edge);
    assert.equal(home.grounded,true);
    assert.equal(api.clearAt(s,home),true);
}
const walking=api.path(s,ground,groundTo);
assert.equal(walking.mode,'walk');checkPath(s,ground,groundTo,walking);
const air={x:300,y:80},airTo={x:810,y:620};
const flight=api.path(s,air,airTo);
assert.equal(flight.mode,'fly');assert.ok(flight.points.length>1,'route goes around the actual popup');
checkPath(s,air,airTo,flight);
for(const point of points) if(api.clearAt(s,air)){
    const route=api.path(s,air,point);if(route.qualified)checkPath(s,air,point,route);
}
const empty=scene();
const drop=api.drop(empty,38,330,{x:100,y:100});
assert.equal(drop.grounded,false);
const exit=api.nearestWater(empty,drop);
assert.equal(exit.qualified,true);assert.equal(exit.placement.edge,'left','hide uses nearby water after a drag');
checkPath(empty,drop,exit.placement,exit.route);
const landed=api.drop(empty,340,empty.height-empty.insets.bottom-empty.hostHeight-5,{x:100,y:100});
assert.equal(landed.grounded,true,'dropping near the lower rim lands on it');
const retained=api.drop(s,popup.rect.x,popup.rect.y,air);
assert.equal(retained.x,air.x);assert.equal(retained.y,air.y,'occupied drop keeps the last safe position');
for(const bad of [NaN,Infinity,-Infinity]) assert.equal(api.drop(empty,bad,5,air).qualified,false);
for(const bad of [null,{...empty,records:[{edge:'top',along:NaN,span:20}]},
    {...empty,blockers:[{x:0,y:0,width:NaN,height:5}]},{...empty,width:70},
    {...empty,surfaces:[{rect:popup.rect,edge:'diagonal'}]}]) {
    assert.equal(api.valid(bad),false);assert.equal(api.appearance(bad,.4,.5,.6).qualified,false);
}
// Both endpoints may be on the floor, yet crossing an obstacle must fly.
const obstruction=scene();obstruction.blockers=[{x:540,y:640,width:190,height:160}];
const from=api.edgePoint(obstruction,'bottom',.25),to=api.edgePoint(obstruction,'bottom',.75);
const around=api.path(obstruction,from,to);
assert.equal(around.mode,'fly');checkPath(obstruction,from,to,around);
// Tall bottom Settings surface with top-bar modules supports standing, riding, walking, homePoint and drop at all scales.
for (const scale of [.65,1,1.25,1.5]) {
    const baseCfg={width:1920,height:1200,scale,hostWidth:112*scale,hostHeight:98*scale,
        insets:{top:10,right:17,bottom:17,left:17},rimRadius:14,excludeKeys:['dock']};
    const barModules=[{edge:'top',along:25,span:294},{edge:'top',along:740,span:440},{edge:'top',along:1229,span:666}];
    const closedScene=api.fromParticipants(baseCfg,{},barModules);
    const edgeHome=api.homePoint(closedScene,'bottom',.8333);
    assert.equal(edgeHome.qualified,true);assert.equal(edgeHome.kind,'edge');
    const openScene=api.fromParticipants(baseCfg,{
        settings:{surfaceSettled:true,geometry:{edge:'bottom',progress:1,surface:{x:160,y:103,width:1600,height:1097}}}
    },barModules);
    const ridden=api.ride(openScene,edgeHome,edgeHome);
    assert.equal(ridden.qualified,true);assert.equal(ridden.grounded,true);assert.equal(ridden.key,'settings');
    const openHome=api.homePoint(openScene,'bottom',.8333);
    assert.equal(openHome.qualified,true);assert.equal(openHome.grounded,true);assert.equal(openHome.key,'settings');
    const dropped=api.drop(openScene,900,openHome.y+8*scale,edgeHome);
    assert.equal(dropped.qualified,true);assert.equal(dropped.grounded,true);assert.equal(dropped.support.key,'settings');
    const walkRoute=api.path(openScene,openHome,dropped);
    assert.equal(walkRoute.qualified,true);assert.equal(walkRoute.mode,'walk');
    checkPath(openScene,openHome,dropped,walkRoute);
    // Right sidebar / edgebar sliding out from right edge pushes bottom-edge Companion horizontally along bottom edge, not onto top of popup.
    for (const [key,geom] of [
        ['rightPanel',{edge:'right',progress:1,surface:{x:1480,y:420,width:423,height:763}}],
        ['notificationCenter',{edge:'bottom',joinedEdge:'right',progress:1,surface:{x:1480,y:500,width:423,height:683}}]
    ]) {
        const pushScene=api.fromParticipants(baseCfg,{[key]:{surfaceSettled:false,geometry:geom}},barModules);
        const pushed=api.ride(pushScene,edgeHome,edgeHome);
        assert.equal(pushed.qualified,true);
        assert.equal(pushed.grounded,true);
        assert.equal(pushed.edge,'bottom');
        assert.equal(pushed.kind,'edge');
        assert.equal(pushed.key,'bottom');
        assert.equal(pushed.y,edgeHome.y,'perpendicular right sidebar/edgebar must push along bottom edge, not lift onto popup top');
        assert.ok(pushed.x+pushScene.hostWidth<=geom.surface.x,'pushed horizontally clear of right sidebar/edgebar');
    }
    // High popup closing after Companion has fallen (or while shrinking away) never pulls Companion back onto the closing popup.
    const highOpen=api.fromParticipants(baseCfg,{
        styledPopup0:{surfaceSettled:true,geometry:{edge:'top',progress:1,surface:{x:700,y:10,width:360,height:240}}}
    },barModules);
    const highRim=api.surfacePoints(highOpen).find(p=>p.key==='styledPopup0'&&p.edge==='top');
    assert.ok(highRim&&highRim.grounded);
    const highClosing=api.fromParticipants(baseCfg,{
        styledPopup0:{surfaceSettled:false,geometry:{edge:'top',progress:.5,surface:{x:700,y:10,width:360,height:120}}}
    },barModules);
    assert.equal(api.ride(highClosing,highRim,highRim).qualified,false,'shrinking high popup must not pull Companion back up');
    const fallen=api.landingBelow(highClosing,highRim);
    assert.equal(fallen.qualified,true);assert.equal(fallen.key,'bottom');
    assert.equal(api.ride(highClosing,fallen,highRim).qualified,false,'closing high popup must not alter position after falling');
    // Dock open while an app is focused (excludeKeys:['dock']) remains a blocker so Companion never overlaps inside Dock.
    const dockRect={x:748,y:1118,width:424,height:82};
    const dockAppFocused=api.fromParticipants(baseCfg,{
        dock:{surfaceSettled:true,geometry:{edge:'bottom',progress:1,surface:dockRect}}
    },barModules);
    assert.equal(dockAppFocused.surfaces.some(s=>s.key==='dock'),false,'excluded dock is not a walkable surface when app is focused');
    assert.equal(dockAppFocused.blockers.some(b=>b.x===dockRect.x&&b.width===dockRect.width),true,'excluded dock remains a collision blocker');
    const centerOnBottom={x:960-dockAppFocused.hostWidth/2,y:dockAppFocused.height-dockAppFocused.insets.bottom-dockAppFocused.hostHeight};
    assert.equal(api.clearAt(dockAppFocused,centerOnBottom),false,'point overlapping inside open dock is blocked');
    const bottomNearCenter=api.edgePoint(dockAppFocused,'bottom',.5);
    assert.equal(bottomNearCenter.qualified,true);assert.equal(bottomNearCenter.grounded,true);
    assert.equal(api.clearAt(dockAppFocused,bottomNearCenter),true);
    assert.ok(bottomNearCenter.x+dockAppFocused.hostWidth<=dockRect.x||bottomNearCenter.x>=dockRect.x+dockRect.width,'edgePoint places Companion outside the Dock');
    const displacedFromDock=api.ride(dockAppFocused,centerOnBottom,{...centerOnBottom,support:{key:'bottom',edge:'bottom'}});
    assert.equal(displacedFromDock.qualified,true);assert.equal(displacedFromDock.key,'bottom');
    assert.equal(api.clearAt(dockAppFocused,displacedFromDock),true,'opening dock while app is focused pushes Companion outside the dock along bottom edge');
    // When no app is focused (excludeKeys:[]), Dock is walkable; focusing an app moves Companion off the Dock to the bottom edge beside the Dock (never inside the Dock).
    const dockNoApp=api.fromParticipants({...baseCfg,excludeKeys:[]},{
        dock:{surfaceSettled:true,geometry:{edge:'bottom',progress:1,surface:dockRect}}
    },barModules);
    const onDock=api.ride(dockNoApp,centerOnBottom,{...centerOnBottom,support:{key:'bottom',edge:'bottom'}});
    assert.equal(onDock.qualified,true);assert.equal(onDock.key,'dock');
    const offDockWhenAppFocused=api.ride(dockAppFocused,onDock,onDock);
    assert.equal(offDockWhenAppFocused.qualified,true);assert.equal(offDockWhenAppFocused.key,'bottom');
    assert.equal(api.clearAt(dockAppFocused,offDockWhenAppFocused),true,'focusing app moves Companion from Dock top to bottom edge beside Dock, never inside Dock');
    assert.ok(offDockWhenAppFocused.x+dockAppFocused.hostWidth<=dockRect.x||offDockWhenAppFocused.x>=dockRect.x+dockRect.width);
    // When Dock closes while no app is focused, Companion rides down to the bottom edge.
    const dockClosedNoApp=api.fromParticipants({...baseCfg,excludeKeys:[]},{
        dock:{surfaceSettled:false,geometry:{edge:'bottom',progress:0,surface:{x:748,y:1200,width:424,height:0}},
            restingRecord:{edge:'bottom',surface:dockRect}}
    },barModules);
    const downToEdge=api.ride(dockClosedNoApp,onDock,onDock);
    assert.equal(downToEdge.qualified,true);assert.equal(downToEdge.key,'bottom');assert.equal(downToEdge.grounded,true);
    // When an app is focused and Dock is in hover-to-reveal (geometry: null, restingRecord: dockRect), Dock zone remains a blocker so Companion never stands inside the Dock zone.
    const dockHoverRevealAppFocused=api.fromParticipants(baseCfg,{
        dock:{surfaceSettled:false,geometry:null,restingRecord:{edge:'bottom',surface:dockRect}}
    },barModules);
    assert.equal(dockHoverRevealAppFocused.surfaces.some(s=>s.key==='dock'),false);
    assert.equal(dockHoverRevealAppFocused.blockers.some(b=>b.x===dockRect.x&&b.width===dockRect.width),true);
    assert.equal(api.clearAt(dockHoverRevealAppFocused,centerOnBottom),false);
    // Airborne point above excluded Dock (e.g. mid-jump or released from drag) lands on bottom edge beside the Dock via landingBelow + routed flight path.
    const airborneAboveDock={x:904,y:dockRect.y-dockAppFocused.hostHeight-2};
    const landedBesideDock=api.landingBelow(dockAppFocused,airborneAboveDock);
    assert.equal(landedBesideDock.qualified,true,'landingBelow from above excluded Dock qualifies');
    assert.equal(landedBesideDock.key,'bottom');assert.equal(landedBesideDock.grounded,true);
    assert.ok(landedBesideDock.x+dockAppFocused.hostWidth<=dockRect.x||landedBesideDock.x>=dockRect.x+dockRect.width);
    assert.equal(api.path(dockAppFocused,airborneAboveDock,landedBesideDock).qualified,true);
    // Clinging to the top rim of a left Dock or side rim of a tall Dock and focusing an app slots onto the Dock's attached edge (never across the screen to an opposite edge).
    const leftDockRect={x:0,y:300,width:220,height:400};
    const leftDockNoApp=api.fromParticipants({...baseCfg,excludeKeys:[]},{
        dock:{surfaceSettled:true,geometry:{edge:'left',progress:1,surface:leftDockRect}}
    },barModules);
    const onLeftDockTopRim=api.annotate(leftDockNoApp,{x:30,y:leftDockRect.y-leftDockNoApp.hostHeight-2},'bottom','surface','dock');
    assert.equal(onLeftDockTopRim.grounded,true);
    const leftDockAppFocused=api.fromParticipants(baseCfg,{
        dock:{surfaceSettled:true,geometry:{edge:'left',progress:1,surface:leftDockRect}}
    },barModules);
    const offLeftDockRim=api.ride(leftDockAppFocused,onLeftDockTopRim,onLeftDockTopRim);
    assert.equal(offLeftDockRim.qualified,true);assert.equal(offLeftDockRim.edge,'left');assert.equal(offLeftDockRim.key,'left');
    // Left/Right/Top Dock closing rides onto the Dock's attached edge.
    const onLeftDock=api.annotate(leftDockNoApp,{x:leftDockRect.width+2,y:450},'left','surface','dock');
    assert.equal(onLeftDock.grounded,true);
    const leftDockClosed=api.fromParticipants({...baseCfg,excludeKeys:[]},{
        dock:{surfaceSettled:false,geometry:{edge:'left',progress:0,surface:{x:0,y:300,width:0,height:400}},
            restingRecord:{edge:'left',surface:leftDockRect}}
    },barModules);
    const downToLeftEdge=api.ride(leftDockClosed,onLeftDock,onLeftDock);
    assert.equal(downToLeftEdge.qualified,true);assert.equal(downToLeftEdge.key,'left');assert.equal(downToLeftEdge.grounded,true);
    // Suppressed top Dock (restingRecord present, geometry null) while Settings is open on bottom does not block standing on Settings top rim.
    const settingsWithTopDockResting=api.fromParticipants(baseCfg,{
        dock:{surfaceSettled:false,geometry:null,restingRecord:{edge:'top',surface:{x:748,y:0,width:424,height:134}}},
        settings:{surfaceSettled:true,geometry:{edge:'bottom',progress:1,surface:{x:160,y:103,width:1600,height:1097}}}
    },barModules);
    const onSettingsHome=api.homePoint(settingsWithTopDockResting,'bottom',.5);
    assert.equal(onSettingsHome.qualified,true);assert.equal(onSettingsHome.key,'settings');assert.equal(onSettingsHome.grounded,true);
}
// Contour terrain: Companion stands ON TOP of corner-wrap Cava & Bar modules, never overlaps inside them,
// and walks smoothly along diagonal concave module<->edge fillet ramps and corner-wrap curves.
{
    const contourScene=api.fromParticipants({
        width:1920,height:1080,scale:1,hostWidth:112,hostHeight:98,
        insets:{top:10,right:10,bottom:10,left:10},rimRadius:14,perimeterRadius:28,connectionDepth:18,
        moduleSurfaces:[
            {edge:'bottom',cornerWrapArm:true,kind:'cava',surface:{x:-50,y:1032,width:220,height:98},radius:18,depth:48,along:0,span:170},
            {edge:'left',cornerWrapArm:true,kind:'cava',surface:{x:-50,y:910,width:98,height:220},radius:18,depth:48,along:910,span:170},
            {edge:'bottom',cornerWrapArm:false,kind:'clock',surface:{x:820,y:1034,width:180,height:96},radius:14,depth:46,along:820,span:180}
        ],
        detachedModules:[]
    },{},[]);
    assert.equal(api.valid(contourScene),true,'contour scene valid');
    assert.equal(contourScene.hasContour,true,'contour mode active');
    // Inside corner-wrap bottom Cava arm (standing on bare 10px edge instead of 48px Cava top) MUST be blocked!
    const insideCava={x:24,y:1080-10-contourScene.hostHeight};
    assert.equal(api.clearAt(contourScene,insideCava),false,'Companion must never overlap inside Cava module');
    // edgePoint at the bottom-left Cava arm places Companion ON TOP of Cava (y <= 1032 - hostHeight + 2)
    const onCavaTop=api.edgePoint(contourScene,'bottom',0.06);
    assert.equal(onCavaTop.qualified,true);assert.equal(onCavaTop.grounded,true);
    assert.equal(api.clearAt(contourScene,onCavaTop),true);
    assert.ok(onCavaTop.support.contourY<=1036,'edgePoint places Companion on top of corner-wrap Cava arm, not inside it');
    // landingBelow and drop above Cava land ON TOP of Cava, not inside it
    const airAboveCava={x:onCavaTop.x,y:760};
    const landedCava=api.landingBelow(contourScene,airAboveCava);
    assert.equal(landedCava.qualified,true);assert.equal(landedCava.grounded,true);
    assert.ok(landedCava.support.contourY<=1036,'landingBelow lands on Cava top');
    const droppedCava=api.drop(contourScene,onCavaTop.x,onCavaTop.y-6,onCavaTop);
    assert.equal(droppedCava.qualified,true);assert.equal(droppedCava.grounded,true);
    assert.ok(droppedCava.y<=onCavaTop.y+4,'drop snaps onto Cava top');
    // Diagonal fillet ramps between raised bottom clock module (x: 820..1000, top y=1034) and 10px bottom edge (y=1070)
    const slopedPts=contourScene.contour.filter(cp=>Math.abs(cp.angle)>8 && Math.abs(cp.angle)<78 && cp.by>900 && cp.bx>600 && cp.bx<1200);
    assert.ok(slopedPts.length>=2,`module<->edge concave fillet has diagonal ramp samples, got ${slopedPts.length}`);
    const onBareEdge=api.edgePoint(contourScene,'bottom',0.36);
    const onModuleTop=api.edgePoint(contourScene,'bottom',0.474);
    assert.equal(onBareEdge.qualified,true);assert.equal(onModuleTop.qualified,true);
    assert.ok(onBareEdge.support.contourY>1060,'bare bottom edge at ~1070px');
    assert.ok(onModuleTop.support.contourY<1042,'clock module top raised at ~1034px');
    const rampRoute=api.path(contourScene,onBareEdge,onModuleTop);
    assert.equal(rampRoute.qualified,true);assert.equal(rampRoute.mode,'walk','edge<->module climb uses continuous walk mode');
    assert.ok(rampRoute.points.some(pt=>Math.abs(pt.angle)>6),'walk waypoints include tilted diagonal ramp angles');
    checkPath(contourScene,onBareEdge,onModuleTop,rampRoute);
    // Contour exploration step climbs across diagonal ramps and around corner-wrap Cava bend onto left edge
    const stepped=api.contourStep(contourScene,onBareEdge.support.contourIndex,-180);
    assert.equal(stepped.qualified,true);assert.equal(stepped.grounded,true);
    assert.ok(stepped.support.contourY<1052,'contourStep walks up onto the raised module surface');
    const aroundCorner=api.contourStep(contourScene,onCavaTop.support.contourIndex,180);
    assert.equal(aroundCorner.qualified,true);assert.equal(aroundCorner.grounded,true);
    assert.equal(aroundCorner.edge,'left','contourStep walks smoothly around corner-wrap Cava onto left edge');
    for (let i=0;i<contourScene.contour.length;i++) {
        const a=contourScene.contour[i], b=contourScene.contour[(i+1)%contourScene.contour.length];
        const seg=Math.hypot(b.bx-a.bx,b.by-a.by);
        assert.ok(seg<=36,`contour segment ${i} stays continuous without corner jumps, got ${seg.toFixed(1)}`);
    }
    const zeroInsetScene=api.fromParticipants({
        width:1920,height:1080,scale:1,hostWidth:112,hostHeight:98,
        insets:{top:0,right:0,bottom:0,left:0},rimRadius:14,perimeterRadius:28,connectionDepth:18,
        moduleSurfaces:[
            {edge:'bottom',cornerWrapArm:true,kind:'cava',surface:{x:-50,y:1032,width:220,height:98},radius:18,depth:48,along:0,span:170},
            {edge:'left',cornerWrapArm:true,kind:'cava',surface:{x:-50,y:910,width:98,height:220},radius:18,depth:48,along:910,span:170}
        ],
        detachedModules:[]
    },{},[]);
    assert.equal(api.valid(zeroInsetScene),true,'zero-inset contour scene valid');
    const onZeroCava=api.edgePoint(zeroInsetScene,'bottom',0.06);
    assert.equal(onZeroCava.qualified,true);assert.equal(onZeroCava.grounded,true);
    assert.ok(onZeroCava.support.contourY<=1036,'zero-inset edgePoint stands on top of corner-wrap Cava arm');
    const zeroAround=api.contourStep(zeroInsetScene,onZeroCava.support.contourIndex,180);
    assert.equal(zeroAround.qualified,true);assert.equal(zeroAround.grounded,true);
    assert.equal(zeroAround.edge,'left','zero-inset corner-wrap contourStep reaches left arm cleanly');
}
const plain=v=>JSON.parse(JSON.stringify(v));
assert.deepEqual(plain(attention.resolve(true,1,0,true,-1,0,0,0,'idle')),{x:.82,y:0,source:'travel'});
assert.equal(attention.resolve(true,0,-1,true,1,1,0,0,'idle').y,-.62);
for(const expression of ['sleepy','working','thinking','sad'])
    assert.equal(attention.resolve(false,0,0,true,1,1,.1,.2,expression).source,'activity');
assert.equal(attention.resolve(false,0,0,false,-1,0,.1,.2,'idle').source,'curiosity');
assert.equal(attention.resolve(false,0,0,true,5,-5,0,0,'idle').x,1);
assert.equal(attention.resolve(false,0,0,true,-1,0,.4,0,'idle',1).source,'curiosity');
assert.equal(attention.resolve(true,1,0,true,1,0,0,0,'idle',1).source,'pointer');
assert.equal(attention.resolve(false,0,0,true,NaN,Infinity,0,0,'idle').y,0);
console.log(`AQUA_FOUR_EDGE_SURFACE_WALK_FLIGHT_DROP_ATTENTION_PASS cases=${cases} paths=${paths}`);
