.pragma library

function unit(value) { return Number.isFinite(value) ? Math.max(0, Math.min(1, value)) : 0 }
function smooth(from, to, value) {
    const t = unit((value-from)/(to-from))
    return t*t*(3-2*t)
}
function usePortal(from, to, scale) {
    return scale>0 && Number.isFinite(scale) && to?.grounded===true
        && [from?.x,from?.y,to?.x,to?.y].every(Number.isFinite)
        && Math.hypot(to.x-from.x,to.y-from.y)>=440*scale
}
function reveal(progress) {
    const t=unit(progress)
    return t<.5 ? 1-smooth(.24,.40,t) : smooth(.60,.76,t)
}
function approachProgress(progress) {
    return smooth(.04,.40,unit(progress))
}
function exitProgress(progress) {
    return smooth(.60,.90,unit(progress))
}
function portalMoving(progress) {
    const t=unit(progress)
    return (t>=.04 && t<.40) || (t>.60 && t<=.90)
}
function opening(progress) {
    const t=unit(progress)
    return smooth(0,.16,t)*(1-smooth(.84,1,t))
}
function rollingAngle(distance, progress, direction, scale) {
    // Compatibility for old callers. No phase, distance or interrupted travel
    // may generate a full-body turn; current travel uses a stable Run instead.
    return 0
}
