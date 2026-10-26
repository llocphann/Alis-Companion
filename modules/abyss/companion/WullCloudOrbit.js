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
        pomodoroNode:{x:0,y:0,width:114,height:36}};
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
    const pomW = Math.max(106, Math.min(122, Math.round(nodeWidth * 2.65)));
    const pomH = Math.max(32, Math.min(36, Math.round(nodeHeight * 0.90)));
    const centerPt = {x: outputWidth / 2 - 2, y: outputHeight / 2 - 2, width: 4, height: 4};
    // Keep equal distances and mirrored angles. Near a corner, turn the pair
    // inward together instead of shifting just one action out of symmetry.
    for (const turn of [0,-Math.PI/8,Math.PI/8,-Math.PI/4,Math.PI/4]) {
        for (const radius of [1,1.15,1.35]) {
            for (const spread of [.92,.8,1.05,.65,.5,.4]) {
                const axis = normal+turn;
                const a = node(axis-spread,radius), b = node(axis+spread,radius);
                if (fits(a) && fits(b) && !overlaps(a,b,4)) {
                    let pocket = node(axis, radius * 1.02);
                    if (!fits(pocket) || overlaps(a,pocket,3) || overlaps(b,pocket,3) || overlaps(pocket,centerPt,2))
                        pocket = node(axis, radius + 0.32);
                    const pocketValid = fits(pocket) && !overlaps(a,pocket,3) && !overlaps(b,pocket,3) && !overlaps(pocket,centerPt,2) ? pocket : null;
                    let pomValid = null;
                    for (const angOff of [0, -0.42, 0.42, -0.65, 0.65]) {
                        const pAng = axis + angOff;
                        const step = Math.abs(Math.cos(pAng))*(nodeWidth+pomW)/2 + Math.abs(Math.sin(pAng))*(nodeHeight+pomH)/2 + 8;
                        const dist = baseRadius * radius * 1.02 + step;
                        const cand = {
                            x: Math.max(8, Math.min(outputWidth - pomW - 8, cx + Math.cos(pAng) * dist - pomW / 2)),
                            y: Math.max(8, Math.min(outputHeight - pomH - 8, cy + Math.sin(pAng) * dist - pomH / 2)),
                            width: pomW,
                            height: pomH
                        };
                        if (fits(cand) && !overlaps(a,cand,3) && !overlaps(b,cand,3)
                            && (!pocketValid || !overlaps(pocketValid,cand,3))
                            && !overlaps(cand,centerPt,2)) {
                            pomValid = cand;
                            break;
                        }
                    }
                    return {available:true,nodes:[a,b],pocketNode:pocketValid,pomodoroNode:pomValid,axisAngle:axis};
                }
            }
        }
    }
    // A tiny/occluded output must not stack buttons or spill onto another screen.
    return empty;
}
