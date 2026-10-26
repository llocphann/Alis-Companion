.pragma library

// A finite, inward-facing orbit. No clock, animation or full-screen hit target.
function overlaps(a, b, gap) {
    return a.x < b.x+b.width+gap && a.x+a.width+gap > b.x
        && a.y < b.y+b.height+gap && a.y+a.height+gap > b.y;
}

function layout(outputWidth, outputHeight, actor) {
    const scale = Math.max(.1, Number(actor.scale) || 1);
    const width = actor.width*scale, height = actor.height*scale;
    const cx = actor.x+actor.width/2+(Number(actor.sideAlignment)||0)*scale;
    const cy = actor.y+actor.height/2+(Number(actor.floorAlignment)||0)*scale;
    const nodeWidth = Math.max(38, Math.min(48, Math.min(width,height)*.62));
    const nodeHeight = nodeWidth*42/48;
    const body = {x:cx-width/2,y:cy-height/2,width:width,height:height};
    const empty = {available:false,nodes:[{x:0,y:0,width:nodeWidth,height:nodeHeight},
        {x:0,y:0,width:nodeWidth,height:nodeHeight}],pocketNode:{x:0,y:0,width:nodeWidth,height:nodeHeight},
        pomodoroNode:{x:0,y:0,width:nodeWidth,height:nodeHeight,expandedX:0,expandedY:0,expandedWidth:114,expandedHeight:36}};
    let edge = actor.edge;
    if (!["top","bottom","left","right"].includes(edge)) {
        const distances = [cy,outputHeight-cy,cx,outputWidth-cx];
        edge = ["top","bottom","left","right"][distances.indexOf(Math.min.apply(null,distances))];
    }
    const normal = edge==="top" ? Math.PI/2 : edge==="bottom" ? -Math.PI/2
        : edge==="right" ? Math.PI : 0;
    const baseRadius = Math.max(width/2+nodeWidth/2,height/2+nodeHeight/2)+14;
    function node(angle, radius) {
        return {x:cx+Math.cos(angle)*baseRadius*radius-nodeWidth/2,
            y:cy+Math.sin(angle)*baseRadius*radius-nodeHeight/2,
            width:nodeWidth,height:nodeHeight};
    }
    function fits(n) {
        const nx = n.x+n.width/2, ny = n.y+n.height/2;
        return n.x>=8 && n.y>=8 && n.x+n.width<=outputWidth-8
            && n.y+n.height<=outputHeight-8 && !overlaps(n,body,3)
            && (nx-cx)*Math.cos(normal)+(ny-cy)*Math.sin(normal)>0;
    }
    const pomW = Math.max(108, Math.min(122, Math.round(nodeWidth * 2.7)));
    const pomH = Math.max(34, Math.min(38, Math.round(nodeHeight * 0.95)));
    const centerPt = {x: outputWidth / 2 - 2, y: outputHeight / 2 - 2, width: 4, height: 4};
    // Keep equal distances and mirrored angles. Near a corner, turn the pair
    // inward together instead of shifting just one action out of symmetry.
    for (const turn of [0,-Math.PI/8,Math.PI/8,-Math.PI/4,Math.PI/4]) {
        for (const radius of [1,1.15,1.35]) {
            for (const spread of [1.12,1.02,.92,.8,.65,.5,.4]) {
                const axis = normal+turn;
                const a = node(axis-spread,radius), b = node(axis+spread,radius);
                if (fits(a) && fits(b) && !overlaps(a,b,4)) {
                    let pocketValid = null, pomValid = null;
                    const innerPairs = [
                        { rMult: 1.04, inSpread: 0.34 },
                        { rMult: 1.08, inSpread: 0.36 },
                        { rMult: 1.22, inSpread: 0.34 },
                        { rMult: 1.38, inSpread: 0.32 }
                    ];
                    for (let i = 0; i < innerPairs.length; ++i) {
                        const pair = innerPairs[i];
                        const pk = node(axis - pair.inSpread, radius * pair.rMult);
                        const pm = node(axis + pair.inSpread, radius * pair.rMult);
                        if (fits(pk) && fits(pm) && !overlaps(pk, pm, 3)
                            && !overlaps(a, pk, 2) && !overlaps(b, pk, 2)
                            && !overlaps(a, pm, 2) && !overlaps(b, pm, 2)
                            && !overlaps(pk, centerPt, 2) && !overlaps(pm, centerPt, 2)) {
                            pocketValid = pk;
                            pomValid = pm;
                            break;
                        }
                    }
                    if (!pocketValid) {
                        for (const rAdd of [0.02, 0.32, 0.55]) {
                            const pk = node(axis, radius + rAdd);
                            if (fits(pk) && !overlaps(a, pk, 2) && !overlaps(b, pk, 2) && !overlaps(pk, centerPt, 2)) {
                                pocketValid = pk;
                                break;
                            }
                        }
                    }
                    if (!pomValid) {
                        for (const angOff of [0.36, -0.36, 0, 0.65, -0.65]) {
                            for (const rAdd of [0.35, 0.6, 0.85]) {
                                const pm = node(axis + angOff, radius + rAdd);
                                if (fits(pm) && !overlaps(a, pm, 2) && !overlaps(b, pm, 2)
                                    && (!pocketValid || !overlaps(pocketValid, pm, 2))
                                    && !overlaps(pm, centerPt, 2)) {
                                    pomValid = pm;
                                    break;
                                }
                            }
                            if (pomValid) break;
                        }
                    }
                    if (pomValid) {
                        const pmCx = pomValid.x + pomValid.width / 2;
                        const pmCy = pomValid.y + pomValid.height / 2;
                        pomValid.expandedWidth = pomW;
                        pomValid.expandedHeight = pomH;
                        pomValid.expandedX = Math.max(8, Math.min(outputWidth - pomW - 8, pmCx - pomW / 2));
                        pomValid.expandedY = Math.max(8, Math.min(outputHeight - pomH - 8, pmCy - pomH / 2));
                    }
                    return {available:true,nodes:[a,b],pocketNode:pocketValid,pomodoroNode:pomValid,axisAngle:axis};
                }
            }
        }
    }
    // A tiny/occluded output must not stack buttons or spill onto another screen.
    return empty;
}
