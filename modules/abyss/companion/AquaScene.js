.pragma library
.import "AquaSurfacePlacement.js" as Slots

// Output-local geometry only. Decisions run on semantic events, never frames.
var edges = ["top", "right", "bottom", "left"]
var FOOT_OFFSET = 31.5
var CLEAR_RADIUS = 24.0
function finite(value) { return typeof value === "number" && Number.isFinite(value) }
function clamp(value, minimum, maximum) { return Math.max(minimum, Math.min(maximum, value)) }
function roundedBoxD(px, py, rect, radius) {
    const r = Math.min(Math.max(0, Number(radius) || 0), Math.min(rect.width, rect.height) * 0.5)
    const cx = rect.x + rect.width * 0.5, cy = rect.y + rect.height * 0.5
    const qx = Math.abs(px - cx) - rect.width * 0.5 + r
    const qy = Math.abs(py - cy) - rect.height * 0.5 + r
    return Math.hypot(Math.max(qx, 0), Math.max(qy, 0)) + Math.min(Math.max(qx, qy), 0) - r
}
function fuseSdf(a, b, k) {
    const kk = Math.max(0.1, k)
    const h = Math.max(kk - Math.abs(a - b), 0) / kk
    return Math.min(a, b) - h * h * kk * 0.25
}
function workspaceBox(scene) {
    const il = scene.insets.left > 0.001 ? scene.insets.left : -64
    const it = scene.insets.top > 0.001 ? scene.insets.top : -64
    const ir = scene.insets.right > 0.001 ? scene.insets.right : -64
    const ib = scene.insets.bottom > 0.001 ? scene.insets.bottom : -64
    return {x:il,y:it,width:scene.width-il-ir,height:scene.height-it-ib}
}
function roundedBoxDXY(px, py, cx, cy, hx, hy, r) {
    const qx = Math.abs(px - cx) - hx + r
    const qy = Math.abs(py - cy) - hy + r
    return (qx <= 0 || qy <= 0)
        ? (Math.max(qx, qy) - r)
        : (Math.sqrt(qx * qx + qy * qy) - r)
}
function walkScreenRadius(scene) {
    const s = scene.scale || 1
    return Math.min(6 * s, FOOT_OFFSET * s * 0.25)
}
function makeContourGeom(scene) {
    const pr = scene.perimeterRadius !== undefined ? scene.perimeterRadius : (scene.rimRadius || 28)
    const k = scene.connectionDepth !== undefined ? scene.connectionDepth : 14
    const kk = Math.max(0.1, k)
    const wr = scene._workspaceBox || workspaceBox(scene)
    const whx = wr.width * 0.5, why = wr.height * 0.5
    const wcx = wr.x + whx, wcy = wr.y + why
    const wrad = Math.min(Math.max(0, Number(pr) || 0), Math.min(wr.width, wr.height) * 0.5)
    const sw = scene.width, sh = scene.height
    const shx = sw * 0.5, shy = sh * 0.5
    const srad = Math.min(Math.max(0, Number(walkScreenRadius(scene)) || 0), Math.min(sw, sh) * 0.5)
    const srcMods = scene.moduleSurfaces || []
    const modCount = srcMods.length
    const mx1 = new Float64Array(modCount), my1 = new Float64Array(modCount)
    const mx2 = new Float64Array(modCount), my2 = new Float64Array(modCount)
    const mcx = new Float64Array(modCount), mcy = new Float64Array(modCount)
    const mhx = new Float64Array(modCount), mhy = new Float64Array(modCount)
    const mrad = new Float64Array(modCount)
    for (let i = 0; i < modCount; i++) {
        const ms = srcMods[i], r = ms.surface
        const hx = r.width * 0.5, hy = r.height * 0.5
        mx1[i] = r.x; my1[i] = r.y
        mx2[i] = r.x + r.width; my2[i] = r.y + r.height
        mhx[i] = hx; mhy[i] = hy
        mcx[i] = r.x + hx; mcy[i] = r.y + hy
        const minHalf = hx < hy ? hx : hy
        const radRaw = Number(ms.radius) || 0
        const radPos = radRaw > 0 ? radRaw : 0
        mrad[i] = radPos < minHalf ? radPos : minHalf
    }
    return {
        w: sw, h: sh, scale: scene.scale || 1,
        pr: pr, k: k, kk: kk, invKk: 1 / kk, kkQuarter: kk * 0.25, pad: pr + k + 1,
        wcx: wcx, wcy: wcy, whx: whx, why: why, wrad: wrad,
        scx: shx, scy: shy, shx: shx, shy: shy, srad: srad,
        modCount: modCount,
        mx1: mx1, my1: my1, mx2: mx2, my2: my2,
        mcx: mcx, mcy: mcy, mhx: mhx, mhy: mhy, mrad: mrad
    }
}
function abyssFieldDFast(g, px, py) {
    let qx = px - g.wcx; if (qx < 0) qx = -qx; qx = qx - g.whx + g.wrad
    let qy = py - g.wcy; if (qy < 0) qy = -qy; qy = qy - g.why + g.wrad
    let d = -((qx <= 0 || qy <= 0)
        ? ((qx > qy ? qx : qy) - g.wrad)
        : (Math.sqrt(qx * qx + qy * qy) - g.wrad))
    const n = g.modCount
    if (n === 0) return d
    const pad = g.pad, kk = g.kk, invKk = g.invKk, kkQuarter = g.kkQuarter
    const mx1 = g.mx1, my1 = g.my1, mx2 = g.mx2, my2 = g.my2
    const mcx = g.mcx, mcy = g.mcy, mhx = g.mhx, mhy = g.mhy, mrad = g.mrad
    let thresh = d + pad
    if (thresh < 0) return d
    for (let i = 0; i < n; i++) {
        if (py < my1[i] - thresh || py > my2[i] + thresh || px < mx1[i] - thresh || px > mx2[i] + thresh)
            continue
        const mr = mrad[i]
        let rx = px - mcx[i]; if (rx < 0) rx = -rx; rx = rx - mhx[i] + mr
        let ry = py - mcy[i]; if (ry < 0) ry = -ry; ry = ry - mhy[i] + mr
        const b = (rx <= 0 || ry <= 0)
            ? ((rx > ry ? rx : ry) - mr)
            : (Math.sqrt(rx * rx + ry * ry) - mr)
        let diff = d - b
        if (diff < 0) diff = -diff
        if (diff < kk) {
            const h = (kk - diff) * invKk
            d = (d < b ? d : b) - h * h * kkQuarter
            thresh = d + pad
            if (thresh < 0) break
        } else if (b < d) {
            d = b
            thresh = d + pad
            if (thresh < 0) break
        }
    }
    return d
}
function walkDFast(g, px, py) {
    const fieldD = abyssFieldDFast(g, px, py)
    let sx = px - g.scx; if (sx < 0) sx = -sx; sx = sx - g.shx + g.srad
    let sy = py - g.scy; if (sy < 0) sy = -sy; sy = sy - g.shy + g.srad
    const screenD = -((sx <= 0 || sy <= 0)
        ? ((sx > sy ? sx : sy) - g.srad)
        : (Math.sqrt(sx * sx + sy * sy) - g.srad))
    return fieldD < screenD ? fieldD : screenD
}
function abyssFieldD(scene, px, py) {
    const pr = scene.perimeterRadius !== undefined ? scene.perimeterRadius : (scene.rimRadius || 28)
    const k = scene.connectionDepth !== undefined ? scene.connectionDepth : 14
    const wr = scene._workspaceBox || workspaceBox(scene)
    const wrx = wr.width * 0.5, wry = wr.height * 0.5
    const wrad = Math.min(Math.max(0, Number(pr) || 0), Math.min(wr.width, wr.height) * 0.5)
    let d = -roundedBoxDXY(px, py, wr.x + wrx, wr.y + wry, wrx, wry, wrad)
    const mods = scene.moduleSurfaces
    if (!mods || mods.length === 0) return d
    const support = k + 1, kk = Math.max(0.1, k)
    for (let i = 0; i < mods.length; i++) {
        const ms = mods[i], r = ms.surface
        const outX = Math.max(r.x - px, px - (r.x + r.width), 0)
        const outY = Math.max(r.y - py, py - (r.y + r.height), 0)
        if (Math.max(outX, outY) - pr > d + support) continue
        const hx = r.width * 0.5, hy = r.height * 0.5
        const rad = Math.min(Math.max(0, Number(ms.radius) || 0), Math.min(r.width, r.height) * 0.5)
        const b = roundedBoxDXY(px, py, r.x + hx, r.y + hy, hx, hy, rad)
        const h = Math.max(kk - Math.abs(d - b), 0) / kk
        d = Math.min(d, b) - h * h * kk * 0.25
    }
    return d
}
function abyssFieldNormal(scene, px, py, geom) {
    const eps = 1.0
    const gx = geom
        ? abyssFieldDFast(geom, px + eps, py) - abyssFieldDFast(geom, px - eps, py)
        : abyssFieldD(scene, px + eps, py) - abyssFieldD(scene, px - eps, py)
    const gy = geom
        ? abyssFieldDFast(geom, px, py + eps) - abyssFieldDFast(geom, px, py - eps)
        : abyssFieldD(scene, px, py + eps) - abyssFieldD(scene, px, py - eps)
    const len = Math.sqrt(gx * gx + gy * gy)
    if (!finite(len) || len < 1e-6) {
        const dl = px, dr = scene.width - px, dt = py, db = scene.height - py
        const m = Math.min(dl, dr, dt, db)
        return m === dt ? {nx:0,ny:1,angle:180} : m === db ? {nx:0,ny:-1,angle:0}
            : m === dl ? {nx:1,ny:0,angle:90} : {nx:-1,ny:0,angle:-90}
    }
    const nx = gx / len, ny = gy / len
    return {nx:nx,ny:ny,angle:Math.atan2(nx, -ny) * 180 / Math.PI}
}
function walkD(scene, px, py) {
    const fieldD = abyssFieldD(scene, px, py)
    const sw = scene.width, sh = scene.height
    const srad = Math.min(Math.max(0, Number(walkScreenRadius(scene)) || 0), Math.min(sw, sh) * 0.5)
    const screenD = -roundedBoxDXY(px, py, sw * 0.5, sh * 0.5, sw * 0.5, sh * 0.5, srad)
    return Math.min(fieldD, screenD)
}
function walkNormal(scene, px, py, geom) {
    const eps = 1.0
    const gx = geom
        ? walkDFast(geom, px + eps, py) - walkDFast(geom, px - eps, py)
        : walkD(scene, px + eps, py) - walkD(scene, px - eps, py)
    const gy = geom
        ? walkDFast(geom, px, py + eps) - walkDFast(geom, px, py - eps)
        : walkD(scene, px, py + eps) - walkD(scene, px, py - eps)
    const len = Math.sqrt(gx * gx + gy * gy)
    if (!finite(len) || len < 1e-6) return abyssFieldNormal(scene, px, py, geom)
    const nx = gx / len, ny = gy / len
    return {nx:nx,ny:ny,angle:Math.atan2(nx, -ny) * 180 / Math.PI}
}
function dominantEdgeFromNormal(nx, ny) {
    return Math.abs(ny) >= Math.abs(nx) ? (ny <= 0 ? "bottom" : "top") : (nx >= 0 ? "left" : "right")
}
function edgeForContourContact(scene, cx, cy, nx, ny) {
    const dt = Math.max(0, cy)
    const dr = Math.max(0, scene.width - cx)
    const db = Math.max(0, scene.height - cy)
    const dl = Math.max(0, cx)
    // Preserve [top, right, bottom, left] tie order without array+sort allocations.
    let e1 = "top", d1 = dt
    let e2 = "right", d2 = dr
    if (dr < dt) { e1 = "right"; d1 = dr; e2 = "top"; d2 = dt }
    if (db < d1) { e2 = e1; d2 = d1; e1 = "bottom"; d1 = db }
    else if (db < d2) { e2 = "bottom"; d2 = db }
    if (dl < d1) { e2 = e1; d2 = d1; e1 = "left"; d1 = dl }
    else if (dl < d2) { e2 = "left"; d2 = dl }
    const tieTol = Math.max(10 * (scene.scale || 1), 10)
    if (d2 - d1 <= tieTol) {
        const dom = dominantEdgeFromNormal(nx, ny)
        if (dom === e1 || dom === e2) return dom
    }
    return e1
}
function hostPointFromBody(scene, bx, by, nx, ny) {
    const W = scene.hostWidth, H = scene.hostHeight, F = FOOT_OFFSET * scene.scale
    return {
        x: bx - W * 0.5 + (W * 0.5 - F) * nx,
        y: by - H * 0.5 + (H * 0.5 - F) * ny
    }
}
function contourSampleAtBody(scene, bx, by, preferredEdge = "", geom = null) {
    let nx, ny, angle
    if (geom) {
        let gx = walkDFast(geom, bx + 1.0, by) - walkDFast(geom, bx - 1.0, by)
        let gy = walkDFast(geom, bx, by + 1.0) - walkDFast(geom, bx, by - 1.0)
        let len = Math.sqrt(gx * gx + gy * gy)
        if (!finite(len) || len < 1e-6) {
            gx = abyssFieldDFast(geom, bx + 1.0, by) - abyssFieldDFast(geom, bx - 1.0, by)
            gy = abyssFieldDFast(geom, bx, by + 1.0) - abyssFieldDFast(geom, bx, by - 1.0)
            len = Math.sqrt(gx * gx + gy * gy)
        }
        if (!finite(len) || len < 1e-6) {
            const dl = bx, dr = geom.w - bx, dt = by, db = geom.h - by
            let m = dl; if (dr < m) m = dr; if (dt < m) m = dt; if (db < m) m = db
            if (m === dt) { nx = 0; ny = 1; angle = 180 }
            else if (m === db) { nx = 0; ny = -1; angle = 0 }
            else if (m === dl) { nx = 1; ny = 0; angle = 90 }
            else { nx = -1; ny = 0; angle = -90 }
        } else {
            nx = gx / len; ny = gy / len
            angle = Math.atan2(nx, -ny) * 180 / Math.PI
        }
    } else {
        const norm = walkNormal(scene, bx, by, null)
        nx = norm.nx; ny = norm.ny; angle = norm.angle
    }
    const F = FOOT_OFFSET * scene.scale
    const W = scene.hostWidth, H = scene.hostHeight
    const hpX = bx - W * 0.5 + (W * 0.5 - F) * nx
    const hpY = by - H * 0.5 + (H * 0.5 - F) * ny
    const limX = scene.width - W - 2 > 2 ? scene.width - W - 2 : 2
    const limY = scene.height - H - 2 > 2 ? scene.height - H - 2 : 2
    const clampedX = hpX < 2 ? 2 : (hpX > limX ? limX : hpX)
    const clampedY = hpY < 2 ? 2 : (hpY > limY ? limY : hpY)
    const cx = bx - F * nx, cy = by - F * ny
    const edge = (preferredEdge && ((preferredEdge === "top" && ny > 0.2) || (preferredEdge === "bottom" && ny < -0.2)
        || (preferredEdge === "left" && nx > 0.2) || (preferredEdge === "right" && nx < -0.2)))
        ? preferredEdge : edgeForContourContact(scene, cx, cy, nx, ny)
    return {
        bx: bx, by: by,
        cx: cx, cy: cy,
        x: clampedX, y: clampedY,
        nx: nx, ny: ny, angle: angle,
        edge: edge, s: 0
    }
}
function newtonProjectWalk(scene, px, py, targetOffset, geom = null) {
    const g = geom || makeContourGeom(scene)
    const s = g.scale
    const margin = targetOffset * 0.4 > 1 ? targetOffset * 0.4 : 1
    const maxBx = g.w - margin > margin ? g.w - margin : margin
    const maxBy = g.h - margin > margin ? g.h - margin : margin
    let bx = px < margin ? margin : (px > maxBx ? maxBx : px)
    let by = py < margin ? margin : (py > maxBy ? maxBy : py)
    const maxStep = 14 * s
    const tol = 0.12 * s
    const limX = g.w - 1 > 1 ? g.w - 1 : 1
    const limY = g.h - 1 > 1 ? g.h - 1 : 1
    let stepped = false
    let finalErr = 0
    for (let it = 0; it < 6; it++) {
        const d = walkDFast(g, bx, by)
        const err = targetOffset - d
        const absErr = err < 0 ? -err : err
        if (absErr < tol) {
            finalErr = absErr
            stepped = false
            break
        }
        let gx = walkDFast(g, bx + 1.0, by) - walkDFast(g, bx - 1.0, by)
        let gy = walkDFast(g, bx, by + 1.0) - walkDFast(g, bx, by - 1.0)
        let len = Math.sqrt(gx * gx + gy * gy)
        if (!finite(len) || len < 1e-6) {
            gx = abyssFieldDFast(g, bx + 1.0, by) - abyssFieldDFast(g, bx - 1.0, by)
            gy = abyssFieldDFast(g, bx, by + 1.0) - abyssFieldDFast(g, bx, by - 1.0)
            len = Math.sqrt(gx * gx + gy * gy)
        }
        let nx, ny
        if (!finite(len) || len < 1e-6) {
            const dl = bx, dr = g.w - bx, dt = by, db = g.h - by
            let m = dl; if (dr < m) m = dr; if (dt < m) m = dt; if (db < m) m = db
            if (m === dt) { nx = 0; ny = 1 }
            else if (m === db) { nx = 0; ny = -1 }
            else if (m === dl) { nx = 1; ny = 0 }
            else { nx = -1; ny = 0 }
        } else {
            nx = gx / len; ny = gy / len
        }
        const step = err < -maxStep ? -maxStep : (err > maxStep ? maxStep : err)
        const nbx = bx + nx * step
        const nby = by + ny * step
        bx = nbx < 1 ? 1 : (nbx > limX ? limX : nbx)
        by = nby < 1 ? 1 : (nby > limY ? limY : nby)
        stepped = true
    }
    if (stepped) {
        const diff = targetOffset - walkDFast(g, bx, by)
        finalErr = diff < 0 ? -diff : diff
    }
    return {bx:bx, by:by, err:finalErr}
}
function projectRayToWalkOffset(scene, startX, startY, dirX, dirY, maxReach, targetOffset, geom = null) {
    const g = geom || makeContourGeom(scene)
    const d0 = walkDFast(g, startX, startY)
    if (d0 >= targetOffset) return contourSampleAtBody(scene, startX + dirX * targetOffset, startY + dirY * targetOffset, "", g)
    let lo = 0, hi = Math.min(maxReach, Math.max(g.w, g.h) * 0.45)
    for (let k = 0; k < 6 && walkDFast(g, startX + dirX * hi, startY + dirY * hi) < targetOffset; k++)
        hi = Math.min(Math.max(g.w, g.h) * 0.65, hi * 1.6)
    if (walkDFast(g, startX + dirX * hi, startY + dirY * hi) < targetOffset) return null
    for (let it = 0; it < 14; it++) {
        const mid = (lo + hi) * 0.5
        if (walkDFast(g, startX + dirX * mid, startY + dirY * mid) < targetOffset) lo = mid
        else hi = mid
    }
    const dist = (lo + hi) * 0.5
    const proj = newtonProjectWalk(scene, startX + dirX * dist, startY + dirY * dist, targetOffset, g)
    return contourSampleAtBody(scene, proj.bx, proj.by, "", g)
}
var _contourCache = []
var CONTOUR_CACHE_MAX = 8
function contourInputsEqual(entry, w, h, hw, hh, s, il, it, ir, ib, pr, k, mods) {
    if (entry.w !== w || entry.h !== h || entry.hw !== hw || entry.hh !== hh || entry.s !== s
            || entry.il !== il || entry.it !== it || entry.ir !== ir || entry.ib !== ib
            || entry.pr !== pr || entry.k !== k)
        return false
    const em = entry.mods
    const n = mods ? mods.length : 0
    if (em.length !== n) return false
    for (let i = 0; i < n; i++) {
        const a = em[i], b = mods[i], r = b.surface
        if (a.x !== r.x || a.y !== r.y || a.width !== r.width || a.height !== r.height
                || a.radius !== (Number(b.radius) || 0) || a.depth !== (Number(b.depth) || 0))
            return false
    }
    return true
}
function buildContour(scene) {
    const W = scene.width, H = scene.height, s = scene.scale || 1
    const hw = scene.hostWidth, hh = scene.hostHeight
    const ins = scene.insets || {}
    const il = Number(ins.left) || 0, it = Number(ins.top) || 0
    const ir = Number(ins.right) || 0, ib = Number(ins.bottom) || 0
    const pr = scene.perimeterRadius !== undefined ? scene.perimeterRadius : (scene.rimRadius || 28)
    const k = scene.connectionDepth !== undefined ? scene.connectionDepth : 14
    const mods = scene.moduleSurfaces || []
    for (let ci = 0; ci < _contourCache.length; ci++) {
        const entry = _contourCache[ci]
        if (contourInputsEqual(entry, W, H, hw, hh, s, il, it, ir, ib, pr, k, mods)) {
            if (ci > 0) {
                _contourCache.splice(ci, 1)
                _contourCache.unshift(entry)
            }
            return entry.value
        }
    }
    const g = makeContourGeom(scene)
    const targetOffset = FOOT_OFFSET * s
    let maxModDepth = 0
    for (let mi = 0; mi < mods.length; mi++) {
        const ms = mods[mi], r = ms.surface
        maxModDepth = Math.max(maxModDepth, ms.depth || 0, Math.min(r.width, r.height))
    }
    const maxReach = Math.max(it, ir, ib, il, maxModDepth) + targetOffset + pr + 48
    let seed = null
    const seedFracs = [0.5, 0.25, 0.75, 0.15, 0.85, 0.35, 0.65]
    for (let i = 0; i < seedFracs.length && !seed; i++)
        seed = projectRayToWalkOffset(scene, W * seedFracs[i], 0, 0, 1, maxReach, targetOffset, g)
    let result
    if (!seed) {
        result = {contour:[],contourLength:0}
    } else {
        const baseStep = 10 * s
        const minStep = 3.5 * s
        const maxPts = 1400
        const maxArc = (W + H) * 3.5
        const pts = [seed]
        let totalS = 0
        let cur = seed
        let closed = false
        for (let iter = 0; iter < maxPts && totalS < maxArc; iter++) {
            let tx = cur.ny, ty = -cur.nx
            const tLen = Math.sqrt(tx * tx + ty * ty)
            if (!finite(tLen) || tLen < 1e-6) break
            tx /= tLen; ty /= tLen
            let h = baseStep
            let nextSample = null
            let seg = 0
            for (let attempt = 0; attempt < 3; attempt++) {
                const predX = cur.bx + tx * h, predY = cur.by + ty * h
                const proj = newtonProjectWalk(scene, predX, predY, targetOffset, g)
                if (proj.err > 3.5 * s && attempt < 2) {
                    h = Math.max(minStep, h * 0.5)
                    continue
                }
                const cand = contourSampleAtBody(scene, proj.bx, proj.by, "", g)
                const sdx = cand.bx - cur.bx, sdy = cand.by - cur.by
                seg = Math.sqrt(sdx * sdx + sdy * sdy)
                const angDiff = Math.abs(((cand.angle - cur.angle + 540) % 360) - 180)
                nextSample = cand
                if ((angDiff > 11 || seg > 15 * s) && h > minStep && attempt < 2) {
                    h = Math.max(minStep, h * 0.5)
                    continue
                }
                break
            }
            if (!nextSample || seg < 0.25 * s) break
            if (pts.length >= 16 && totalS > 120 * s) {
                const dx0 = nextSample.bx - seed.bx, dy0 = nextSample.by - seed.by
                const dist0 = Math.sqrt(dx0 * dx0 + dy0 * dy0)
                const normalDot = nextSample.nx * seed.nx + nextSample.ny * seed.ny
                if (dist0 <= baseStep * 1.15 && normalDot > 0.7) {
                    const fwd = dx0 * seed.ny + dy0 * (-seed.nx)
                    if (fwd >= -minStep * 0.5) {
                        closed = true
                        break
                    }
                    totalS += seg
                    nextSample.s = totalS
                    pts.push(nextSample)
                    closed = true
                    break
                }
            }
            totalS += seg
            nextSample.s = totalS
            pts.push(nextSample)
            cur = nextSample
        }
        if (!closed && pts.length > 16) {
            const last = pts[pts.length - 1]
            const cgx = seed.bx - last.bx, cgy = seed.by - last.by
            const closingGap = Math.sqrt(cgx * cgx + cgy * cgy)
            if (closingGap > maxArc * 0.5) {
                result = {contour:[],contourLength:0}
            } else {
                const clx = pts.length > 1 ? pts[0].bx - pts[pts.length - 1].bx : 0
                const cly = pts.length > 1 ? pts[0].by - pts[pts.length - 1].by : 0
                const closingLen = pts.length > 1 ? Math.sqrt(clx * clx + cly * cly) : 0
                result = {contour:pts,contourLength:totalS + closingLen}
            }
        } else {
            const clx = pts.length > 1 ? pts[0].bx - pts[pts.length - 1].bx : 0
            const cly = pts.length > 1 ? pts[0].by - pts[pts.length - 1].by : 0
            const closingLen = pts.length > 1 ? Math.sqrt(clx * clx + cly * cly) : 0
            result = {contour:pts,contourLength:totalS + closingLen}
        }
    }
    const modsSnap = []
    for (let mi = 0; mi < mods.length; mi++) {
        const ms = mods[mi], r = ms.surface
        modsSnap.push({x:r.x,y:r.y,width:r.width,height:r.height,radius:Number(ms.radius) || 0,depth:Number(ms.depth) || 0})
    }
    _contourCache.unshift({w:W,h:H,hw:hw,hh:hh,s:s,il:il,it:it,ir:ir,ib:ib,pr:pr,k:k,mods:modsSnap,value:result})
    if (_contourCache.length > CONTOUR_CACHE_MAX) _contourCache.pop()
    return result
}
function bodyCenterFromPoint(scene, point, fallbackNx, fallbackNy) {
    const W = scene.hostWidth, H = scene.hostHeight, F = FOOT_OFFSET * scene.scale
    const nx = finite(point.nx) ? point.nx : fallbackNx
    const ny = finite(point.ny) ? point.ny : fallbackNy
    return {
        bx: point.x + W * 0.5 - (W * 0.5 - F) * nx,
        by: point.y + H * 0.5 - (H * 0.5 - F) * ny
    }
}
function nearestContourPoint(scene, point, preferredEdge) {
    if (!scene.hasContour || !scene.contour || !scene.contour.length || !point) return null
    const contour = scene.contour
    let bestIdx = -1, bestScore = Infinity, bestDist = Infinity
    for (let i = 0; i < contour.length; i++) {
        const cp = contour[i]
        const dist = Math.hypot(point.x - cp.x, point.y - cp.y)
        const penalty = (preferredEdge && cp.edge !== preferredEdge) ? 1.5 * scene.scale : 0
        if (dist + penalty < bestScore) {
            bestScore = dist + penalty
            bestIdx = i
            bestDist = dist
        }
    }
    return bestIdx >= 0 ? {sample:contour[bestIdx],index:bestIdx,dist:bestDist} : null
}
function valid(scene) {
    return !!scene && [scene.width,scene.height,scene.hostWidth,scene.hostHeight,scene.scale].every(finite)
        && scene.hostWidth > 0 && scene.hostHeight > 0 && scene.scale > 0
        && scene.width >= scene.hostWidth+4 && scene.height >= scene.hostHeight+4
        && scene.insets && edges.every(e=>finite(scene.insets[e]) && scene.insets[e]>=0)
        && scene.insets.left+scene.insets.right<scene.width
        && scene.insets.top+scene.insets.bottom<scene.height
        && Array.isArray(scene.records) && scene.records.length <= 256
        && scene.records.every(r=>r && edges.includes(r.edge) && finite(r.along) && finite(r.span) && r.span>=0)
        && Array.isArray(scene.blockers) && scene.blockers.length <= 128
        && scene.blockers.every(Slots.validRect)
        && Array.isArray(scene.surfaces) && scene.surfaces.length <= 40
        && scene.surfaces.every(s=>s && Slots.validRect(s.rect) && edges.includes(s.edge))
}
// Every registered Abyss body participates: Dock, Settings, Sidebars, ordinary
// and StyledPopup/IPC hosts, OSD, notifications and future body-host surfaces.
// Use the actual painted silhouette, never the inset content/input rectangle.
function fromParticipants(base, participants, modules) {
    const records=Array.isArray(modules) ? modules : [], blockers=[], surfaces=[]
    const hasContour=Array.isArray(base?.moduleSurfaces)
    const validModuleSurfaces=[]
    if (hasContour) {
        for (const ms of base.moduleSurfaces) {
            const r=ms?.surface
            if (!ms || !r || ![r.x,r.y,r.width,r.height].every(finite) || r.width<=0 || r.height<=0)
                return Object.assign({},base,{hasContour:true,moduleSurfaces:[],records:records,blockers:null,surfaces:[]})
            validModuleSurfaces.push({
                edge:ms.edge ?? "",
                surface:{x:r.x,y:r.y,width:r.width,height:r.height},
                radius:Math.max(0,Number(ms.radius) || 0),
                depth:Math.max(0,Number(ms.depth) || 0),
                along:Number(ms.along) || 0,
                span:Number(ms.span) || 0,
                cornerWrapArm:ms.cornerWrapArm===true,
                kind:String(ms.kind ?? "")
            })
        }
        const detached=Array.isArray(base?.detachedModules) ? base.detachedModules : []
        for (let i=0;i<detached.length;i++) {
            const dm=detached[i], r=dm?.rect
            if (!dm || !edges.includes(dm.edge) || !Slots.validRect(r)) continue
            const rectCopy={x:r.x,y:r.y,width:r.width,height:r.height,walkable:true,key:"detachedModule"+i,edge:dm.edge}
            blockers.push(rectCopy)
            surfaces.push({key:"detachedModule"+i,edge:dm.edge,joinedEdge:"",rect:rectCopy,
                radius:Math.max(0,Number(dm.radius) || 0),settled:true})
        }
    } else {
        for (const record of records) {
            const horizontal=record.edge==="top" || record.edge==="bottom"
            const depth=Number(base.insets?.[record.edge])
            // Hidden Bar modules retain a zero span in the live layout. They have
            // no painted area; keep their records validated, without an empty
            // collision rectangle invalidating every possible Aqua placement.
            if (record.span===0 || depth===0) continue
            blockers.push(horizontal
                ? {x:record.along,y:record.edge==="top" ? 0 : base.height-depth,width:record.span,height:depth}
                : {x:record.edge==="left" ? 0 : base.width-depth,y:record.along,width:depth,height:record.span})
        }
    }
    const excluded=Array.isArray(base?.excludeKeys) ? base.excludeKeys : []
    const keys=Object.keys(participants ?? {})
    // Capacity overflow fails closed rather than dropping a visible obstacle.
    if (keys.length>40) return Object.assign({},base,{hasContour:hasContour,moduleSurfaces:validModuleSurfaces,records:records,blockers:[],surfaces:null})
    const dockSuppressed=["settings","dashboard","nexus","controls","aux","clipboard","utility"].some(k=>{
        const s=participants?.[k]?.geometry?.surface
        return !!s && s.width>0 && s.height>0
    })
    for (const key of keys) {
        const participant=participants[key]
        const liveGeometry=participant?.geometry, settled=participant?.surfaceSettled===true
        const isExcluded=excluded.includes(key)
        let geometry=liveGeometry
        if (isExcluded && !(key==="dock" && dockSuppressed)) {
            const resting=participant?.restingRecord
            if (resting?.surface?.width>0 && resting?.surface?.height>0)
                geometry=resting
        }
        const r=geometry?.surface
        if (!geometry) continue
        if (!r || ![r.x,r.y,r.width,r.height].every(finite)
                || r.width<0 || r.height<0 || !edges.includes(geometry.edge))
            return Object.assign({},base,{hasContour:hasContour,moduleSurfaces:validModuleSurfaces,records:records,blockers:null,surfaces:[]})
        // Match the shared field's visible-record rule, including custom IPC
        // participants without a body-host progress property.
        if (r.width<=0 || r.height<=0) continue
        const minTopY=(key==="settings" || key==="dashboard" || key==="nexus") && geometry.edge==="bottom" && r.y>0
            ? Math.ceil(Math.max(2,(Number(base.insets?.top)||0)+4*(base.scale||1)+2)+base.hostHeight+Math.max(2,2*(base.scale||1)))
            : r.y
        const topY=r.y<minTopY && r.y+r.height>minTopY+1 ? minTopY : r.y
        blockers.push({x:r.x,y:topY,width:r.width,height:r.height-(topY-r.y),walkable:false,key:key,edge:geometry.edge})
        if (edges.includes(geometry.edge) && !excluded.includes(key))
            surfaces.push({key:key,edge:geometry.edge,
                joinedEdge:edges.includes(geometry.joinedEdge) ? geometry.joinedEdge : "",
                rect:blockers[blockers.length-1],
                radius:Math.max(0,Number(base.rimRadius) || 0),settled:settled})
    }
    const out=Object.assign({},base,{hasContour:hasContour,moduleSurfaces:validModuleSurfaces,records:records,blockers:blockers,surfaces:surfaces})
    if (hasContour && valid(out)) {
        out._workspaceBox=workspaceBox(out)
        const baked=buildContour(out)
        out.contour=baked.contour
        out.contourLength=baked.contourLength
    }
    return out
}
function rect(scene, point) {
    return {x:point.x,y:point.y,width:scene.hostWidth,height:scene.hostHeight}
}
function clearAt(scene, point) {
    if (!valid(scene) || !point || !finite(point.x) || !finite(point.y)
            || point.x<2 || point.y<2 || point.x+scene.hostWidth>scene.width-2
            || point.y+scene.hostHeight>scene.height-2
            || scene.blockers.some(b=>Slots.intersects(rect(scene,point),b,1.5)))
        return false
    if (scene.hasContour) {
        let nx = point.nx, ny = point.ny
        if (!finite(nx) || !finite(ny)) {
            const near = nearestContourPoint(scene, point)
            if (near && near.dist <= 80 * scene.scale) {
                nx = near.sample.nx; ny = near.sample.ny
            } else {
                nx = 0; ny = 0
            }
        }
        const bc = bodyCenterFromPoint(scene, point, nx, ny)
        if (abyssFieldD(scene, bc.bx, bc.by) < CLEAR_RADIUS * scene.scale)
            return false
    }
    return true
}
function normal(edge) {
    return {nx:edge==="left" ? 1 : edge==="right" ? -1 : 0,
        ny:edge==="top" ? 1 : edge==="bottom" ? -1 : 0}
}
function supportAt(scene, point, preferredEdge) {
    if (!clearAt(scene,point)) return null
    const tolerance=Math.max(2.1,3*scene.scale)
    const order=edges.includes(preferredEdge) ? [preferredEdge].concat(edges.filter(e=>e!==preferredEdge)) : ["bottom","top","left","right"]
    function match(key,edge,coordinate,from,to) {
        const horizontal=edge==="top" || edge==="bottom"
        const boundary=edge==="bottom" ? point.y+scene.hostHeight : edge==="top" ? point.y
            : edge==="left" ? point.x : point.x+scene.hostWidth
        const start=horizontal ? point.x : point.y, extent=horizontal ? scene.hostWidth : scene.hostHeight
        if (Math.abs(boundary-coordinate)>tolerance || start<from || start+extent>to) return null
        const n=normal(edge)
        return {key:key,edge:edge,nx:n.nx,ny:n.ny,axis:horizontal ? "x" : "y",
            coordinate:coordinate,y:horizontal ? coordinate : undefined,from:from,to:to}
    }
    for (const edge of order) {
        if (!scene.hasContour) {
            const horizontal=edge==="top" || edge==="bottom"
            const coordinate=edge==="top" ? scene.insets.top : edge==="bottom" ? scene.height-scene.insets.bottom
                : edge==="left" ? scene.insets.left : scene.width-scene.insets.right
            const found=match(edge,edge,coordinate,horizontal ? scene.insets.left+2 : scene.insets.top+2,
                horizontal ? scene.width-scene.insets.right-2 : scene.height-scene.insets.bottom-2)
            if (found) return found
        }
        const horizontal=edge==="top" || edge==="bottom"
        for (const surface of scene.surfaces) {
            const r=surface.rect, margin=Math.min(Number(surface.radius)||0,(horizontal ? r.width : r.height)/2)
            const side=edge==="bottom" ? r.y : edge==="top" ? r.y+r.height : edge==="left" ? r.x+r.width : r.x
            const start=horizontal ? r.x : r.y, extent=horizontal ? r.width : r.height
            const rim=match(surface.key,edge,side,start+margin,start+extent-margin)
            if (rim) return rim
        }
    }
    if (scene.hasContour) {
        const near=nearestContourPoint(scene, point, preferredEdge)
        const contourTol=Math.max(5.5, 6.5 * scene.scale)
        if (near && near.dist <= contourTol) {
            const cp=near.sample
            const horizontal=cp.edge==="top" || cp.edge==="bottom"
            return {key:cp.edge,edge:cp.edge,nx:cp.nx,ny:cp.ny,angle:cp.angle,axis:horizontal ? "x" : "y",
                coordinate:horizontal ? cp.cy : cp.cx,y:horizontal ? cp.cy : undefined,
                from:horizontal ? scene.insets.left+2 : scene.insets.top+2,
                to:horizontal ? scene.width-scene.insets.right-2 : scene.height-scene.insets.bottom-2,
                contourIndex:near.index,contourS:cp.s,contourX:cp.cx,contourY:cp.cy}
        }
    }
    for (let i=0;i<scene.blockers.length;i++) {
        const r=scene.blockers[i]
        if (r.walkable===false) continue
        const rim=match("rim"+i,"bottom",r.y,r.x,r.x+r.width)
        if (rim) return rim
    }
    return null
}
function annotate(scene, point, edge, kind, key) {
    const support=supportAt(scene,point,edge)
    const placement={qualified:true,x:point.x,y:point.y,edge:support?.edge ?? edge,kind:kind,key:key,
        hostWidth:scene.hostWidth,hostHeight:scene.hostHeight,
        grounded:!!support,support:support}
    if (support && finite(support.nx) && finite(support.ny)) {
        placement.nx=support.nx; placement.ny=support.ny
        if (finite(support.angle)) placement.angle=support.angle
    }
    placement.contact=waterContact(scene,placement,point)
    return placement
}
function waterContact(scene, placement, point) {
    if (!valid(scene) || !placement || !point || !clearAt(scene,point)) return null
    const key=placement.kind==="surface" ? placement.key : supportAt(scene,point)?.key
    const surface=scene.surfaces.find(s=>s.key===key)
    const support=supportAt(scene,point,placement.edge)
    if (!surface && support && finite(support.contourX) && finite(support.contourY)) {
        const edge=support.edge
        const horizontal=edge==="top" || edge==="bottom"
        return {x:support.contourX,y:support.contourY,nx:support.nx,ny:support.ny,scale:scene.scale,
            span:(horizontal ? scene.hostWidth : scene.hostHeight)*.7,
            sourceEdge:edge,sourceAlong:horizontal ? support.contourX : support.contourY,key:support.key}
    }
    const edge=surface ? (support?.edge ?? placement.edge) : support?.edge ?? (placement.kind==="edge" ? placement.edge : "")
    if (!edges.includes(edge)) return null
    const horizontal=edge==="top" || edge==="bottom"
    const r=surface?.rect
    const x=horizontal ? point.x+scene.hostWidth/2
        : r ? edge==="left" ? r.x+r.width : r.x : edge==="left" ? scene.insets.left : scene.width-scene.insets.right
    const y=!horizontal ? point.y+scene.hostHeight/2
        : r ? edge==="bottom" ? r.y : r.y+r.height : edge==="top" ? scene.insets.top : scene.height-scene.insets.bottom
    const boundary=horizontal ? edge==="bottom" ? point.y+scene.hostHeight : point.y
        : edge==="left" ? point.x : point.x+scene.hostWidth
    if (Math.abs(boundary-(horizontal ? y : x))>5*scene.scale) return null
    return {x:x,y:y,nx:edge==="left" ? 1 : edge==="right" ? -1 : 0,
        ny:edge==="top" ? 1 : edge==="bottom" ? -1 : 0,scale:scene.scale,
        span:(horizontal ? scene.hostWidth : scene.hostHeight)*.7,
        sourceEdge:surface?.edge ?? edge,
        sourceAlong:(surface?.edge ?? edge)==="top" || (surface?.edge ?? edge)==="bottom" ? x : y,
        key:surface?.key ?? edge}
}
function nearestClearContourPoint(scene, refPoint, preferredEdge) {
    if (!scene.hasContour || !scene.contour || !scene.contour.length) return {qualified:false}
    const contour=scene.contour, s=scene.scale
    let best=null, bestScore=Infinity
    for (let i=0;i<contour.length;i++) {
        const cp=contour[i]
        const dist=Math.hypot(cp.x - refPoint.x, cp.y - refPoint.y)
        const penalty=(preferredEdge && cp.edge!==preferredEdge) ? 18*s : 0
        if (dist + penalty >= bestScore) continue
        const cand={x:cp.x,y:cp.y,nx:cp.nx,ny:cp.ny,angle:cp.angle,edge:cp.edge}
        if (!clearAt(scene,cand)) continue
        bestScore=dist + penalty
        best=cand
    }
    const chosenEdge=best ? (best.edge ?? preferredEdge ?? "bottom") : "bottom"
    return best ? annotate(scene,best,chosenEdge,"edge",chosenEdge) : {qualified:false}
}
function contourStep(scene, startIndex, deltaArc) {
    if (!scene.hasContour || !scene.contour || scene.contour.length < 2 || !finite(deltaArc)) return {qualified:false}
    const contour=scene.contour, n=contour.length
    const idx=((Math.round(startIndex) % n) + n) % n
    const dir=deltaArc >= 0 ? 1 : -1
    const maxArc=Math.abs(deltaArc)
    let walked=0, cur=idx, bestIdx=-1
    for (let steps=0;steps<n-1;steps++) {
        const next=(cur + dir + n) % n
        const seg=Math.hypot(contour[next].bx - contour[cur].bx, contour[next].by - contour[cur].by)
        const cp=contour[next]
        if (!clearAt(scene, cp)) break
        walked += seg
        bestIdx=next
        cur=next
        if (walked >= maxArc) break
    }
    if (bestIdx < 0) return {qualified:false}
    const dest=contour[bestIdx]
    return annotate(scene,{x:dest.x,y:dest.y,nx:dest.nx,ny:dest.ny,angle:dest.angle},dest.edge,"edge",dest.edge)
}
function edgePoint(scene, edge, fraction) {
    if (!valid(scene) || !edges.includes(edge) || !finite(fraction)) return {qualified:false}
    if (scene.hasContour && scene.contour && scene.contour.length > 0) {
        const horizontal=edge==="top" || edge==="bottom"
        const extent=horizontal ? scene.width : scene.height
        const desired=extent*clamp(fraction,.08,.92)
        const cands=[]
        for (let i=0;i<scene.contour.length;i++) {
            const cp=scene.contour[i]
            if (cp.edge !== edge) continue
            const coord=horizontal ? (cp.x + scene.hostWidth * 0.5) : (cp.y + scene.hostHeight * 0.5)
            cands.push({cp:cp,dist:Math.abs(coord - desired)})
        }
        cands.sort((a,b)=>a.dist - b.dist)
        for (let i=0;i<cands.length;i++) {
            const cp=cands[i].cp
            const pt={x:cp.x,y:cp.y,nx:cp.nx,ny:cp.ny,angle:cp.angle}
            if (clearAt(scene, pt))
                return annotate(scene,pt,cp.edge,"edge",cp.edge)
        }
        return {qualified:false}
    }
    const horizontal=edge==="top" || edge==="bottom"
    const span=horizontal ? scene.hostWidth : scene.hostHeight
    const extent=horizontal ? scene.width : scene.height
    const normal=edge==="top" ? Math.max(2,scene.insets.top)
        : edge==="bottom" ? Math.min(scene.height-scene.hostHeight-2,scene.height-scene.insets.bottom-scene.hostHeight)
        : edge==="left" ? Math.max(2,scene.insets.left)
        : Math.min(scene.width-scene.hostWidth-2,scene.width-scene.insets.right-scene.hostWidth)
    const depth=horizontal ? scene.hostHeight : scene.hostWidth
    const reservations=[]
    for (const b of scene.blockers) {
        const start=horizontal ? b.y : b.x, end=start+(horizontal ? b.height : b.width)
        if (start < normal+depth+2 && end+2 > normal)
            reservations.push({edge:edge,along:horizontal ? b.x : b.y,span:horizontal ? b.width : b.height})
    }
    const result=Slots.slot({edge:edge,extent:extent,footprint:span,
        desired:extent*clamp(fraction,.08,.92),clearance:3,sideGuard:8,
        cornerStart:(horizontal ? scene.insets.left : scene.insets.top)+12,
        cornerEnd:(horizontal ? scene.insets.right : scene.insets.bottom)+12,
        maxShift:extent,records:scene.records,reservations:reservations})
    if (!result.qualified) return result
    const point=horizontal ? {x:result.center-span/2,y:normal} : {x:normal,y:result.center-span/2}
    if (!clearAt(scene,point)) return {qualified:false}
    const placement=annotate(scene,point,edge,"edge",edge)
    placement.freeInterval=result.freeInterval
    return placement
}
function homePoint(scene, preferredEdge, preferredAlong) {
    if (!valid(scene)) return {qualified:false}
    const edge=edges.includes(preferredEdge) ? preferredEdge : "bottom"
    const along=finite(preferredAlong) ? clamp(preferredAlong,.08,.92) : .72
    const horizontal=edge==="top" || edge==="bottom"
    const extent=horizontal ? scene.width : scene.height
    const span=horizontal ? scene.hostWidth : scene.hostHeight
    const desired=extent*along, gap=Math.max(2,2*scene.scale)
    for (const surface of scene.surfaces) {
        if ((surface.key!=="settings" && surface.key!=="dashboard" && surface.key!=="nexus") || surface.edge!==edge) continue
        const r=surface.rect, margin=Math.min(Number(surface.radius)||0,(horizontal ? r.width : r.height)/2)
        const start=horizontal ? r.x : r.y, size=horizontal ? r.width : r.height
        if (desired<start || desired>start+size) continue
        const lo=start+margin, hi=start+size-margin-span
        if (hi<lo) continue
        const pos=clamp(desired-span/2,lo,hi)
        const candidate=edge==="bottom" ? {x:pos,y:r.y-scene.hostHeight-gap}
            : edge==="top" ? {x:pos,y:r.y+r.height+gap}
            : edge==="left" ? {x:r.x+r.width+gap,y:pos} : {x:r.x-scene.hostWidth-gap,y:pos}
        if (clearAt(scene,candidate)) return annotate(scene,candidate,edge,"surface",surface.key)
    }
    const primary=edgePoint(scene,edge,along)
    if (primary.qualified && primary.grounded && clearAt(scene,primary)) return primary
    for (const candidateEdge of edges) {
        if (candidateEdge===edge) continue
        const fallback=edgePoint(scene,candidateEdge,along)
        if (fallback.qualified && fallback.grounded && clearAt(scene,fallback)) return fallback
    }
    return appearance(scene,.8,edges.indexOf(edge)/4,along)
}
function surfacePoints(scene) {
    if (!valid(scene)) return []
    const result=[], w=scene.hostWidth, h=scene.hostHeight, gap=Math.max(2,2*scene.scale)
    for (const surface of scene.surfaces) {
        if (surface.settled===false) continue
        const r=surface.rect
        const points={
            right:{x:r.x+r.width+gap,y:r.y+(r.height-h)/2,edge:"left"},
            left:{x:r.x-w-gap,y:r.y+(r.height-h)/2,edge:"right"},
            top:{x:r.x+(r.width-w)/2,y:r.y-h-gap,edge:"bottom"},
            bottom:{x:r.x+(r.width-w)/2,y:r.y+r.height+gap,edge:"top"}
        }
        const order=surface.edge==="left" ? ["right","top","bottom","left"]
            : surface.edge==="right" ? ["left","top","bottom","right"] : ["top","right","left","bottom"]
        for (const side of order) {
            const point=points[side]
            if (clearAt(scene,point)) result.push(annotate(scene,point,point.edge,"surface",surface.key))
        }
    }
    return result
}
// All four edges remain eligible. Any settled Abyss body has a 35% initial
// visit chance, not a preference that pins Aqua to that surface.
function appearance(scene, surfaceRoll, edgeRoll, alongRoll) {
    if (!valid(scene) || ![surfaceRoll,edgeRoll,alongRoll].every(finite)) return {qualified:false}
    const surfaces=surfacePoints(scene)
    if (surfaces.length && surfaceRoll<.35)
        return surfaces[Math.min(surfaces.length-1,Math.floor(clamp(edgeRoll,0,.999999)*surfaces.length))]
    const first=Math.floor(clamp(edgeRoll,0,.999999)*4)
    for (let i=0;i<4;i++) {
        const point=edgePoint(scene,edges[(first+i)%4],alongRoll)
        if (point.qualified) return point
    }
    return {qualified:false}
}

// Exact swept full-host rectangle vs expanded obstacle, including endpoints.
function clearSegment(scene, from, to) {
    if (!clearAt(scene,from) || !clearAt(scene,to)) return false
    const dx=to.x-from.x, dy=to.y-from.y
    for (const b of scene.blockers) {
        // Preserve all four geometry reads before either axis test. These
        // bounds are private scalar values; two staging arrays per obstacle
        // are unnecessary even when an early axis exit is taken.
        const loX=b.x-scene.hostWidth-1.5, loY=b.y-scene.hostHeight-1.5
        const hiX=b.x+b.width+1.5, hiY=b.y+b.height+1.5
        let enter=0, exit=1, hit=true
        for (let axis=0;axis<2;axis++) {
            const start=axis ? from.y : from.x, delta=axis ? dy : dx
            if (Math.abs(delta)<1e-9) {
                if (start<=(axis ? loY : loX) || start>=(axis ? hiY : hiX)) {hit=false;break}
            } else {
                const a=((axis ? loY : loX)-start)/delta, z=((axis ? hiY : hiX)-start)/delta
                enter=Math.max(enter,Math.min(a,z)); exit=Math.min(exit,Math.max(a,z))
                if (enter>=exit-1e-9) {hit=false;break}
            }
        }
        if (hit && enter<exit-1e-9) return false
    }
    return true
}
function distance(a,b) { return Math.hypot(a.x-b.x,a.y-b.y) }
// Conservative full swept envelope, not sparse sampling of a curved path.
// Every authored height lies in [0,1]; the entire host stays inside this box.
function clearArc(scene, from, to, height, nx = 0, ny = -1) {
    if (!finite(height) || height<0 || !clearSegment(scene,from,to)) return false
    if (!finite(nx) || !finite(ny) || Math.abs(Math.hypot(nx,ny)-1)>.001) return false
    const envelope={x:Math.min(from.x,to.x)+Math.min(0,nx*height),y:Math.min(from.y,to.y)+Math.min(0,ny*height),
        width:scene.hostWidth+Math.abs(to.x-from.x)+Math.abs(nx*height),
        height:scene.hostHeight+Math.abs(to.y-from.y)+Math.abs(ny*height)}
    return envelope.x>=2 && envelope.y>=2 && envelope.x+envelope.width<=scene.width-2
        && envelope.y+envelope.height<=scene.height-2
        && !scene.blockers.some(b=>Slots.intersects(envelope,b,1.5))
}
function landingBelow(scene, point) {
    if (!clearAt(scene,point)) return {qualified:false}
    const floors=[]
    if (scene.hasContour && scene.contour && scene.contour.length > 0) {
        let bestCp = null
        for (let i = 0; i < scene.contour.length; i++) {
            const cp = scene.contour[i]
            if (cp.ny >= -0.25) continue
            if (Math.abs(cp.x - point.x) > 14 * scene.scale) continue
            if (cp.y < point.y + 0.1) continue
            if (!bestCp || cp.y < bestCp.y) bestCp = cp
        }
        if (bestCp) {
            floors.push({key:bestCp.edge,y:bestCp.cy,contourSample:bestCp,
                from:Math.min(point.x,bestCp.x),to:Math.max(point.x,bestCp.x)+scene.hostWidth})
        }
    } else {
        floors.push({key:"bottom",y:scene.height-scene.insets.bottom,
            from:scene.insets.left+2,to:scene.width-scene.insets.right-2})
    }
    for (const s of scene.surfaces) {
        const margin=Math.min(Number(s.radius)||0,s.rect.width/2)
        floors.push({key:s.key,y:s.rect.y,from:s.rect.x+margin,to:s.rect.x+s.rect.width-margin})
    }
    for (let i=0;i<scene.blockers.length;i++) {
        const b=scene.blockers[i]
        if (b.walkable!==false) floors.push({key:"rim"+i,y:b.y,from:b.x,to:b.x+b.width})
    }
    floors.sort((a,b)=>a.y-b.y)
    for (const floor of floors) {
        if (floor.contourSample) {
            const cp=floor.contourSample
            const cand={x:cp.x,y:cp.y,nx:cp.nx,ny:cp.ny,angle:cp.angle}
            if (cand.y>=point.y+.1 && clearSegment(scene,point,cand) && supportAt(scene,cand,cp.edge))
                return annotate(scene,cand,cp.edge,"edge",cp.edge)
            continue
        }
        const next={x:point.x,y:floor.y-scene.hostHeight-Math.max(2,2*scene.scale)}
        if (next.y>=point.y+.1 && point.x>=floor.from && point.x+scene.hostWidth<=floor.to
                && clearSegment(scene,point,next) && supportAt(scene,next))
            return annotate(scene,next,"bottom",floor.key==="bottom" ? "edge" : "surface",floor.key)
        if (floor.key==="bottom" && Array.isArray(scene.excludeKeys) && scene.excludeKeys.length>0
                && scene.blockers.some(b=>scene.excludeKeys.includes(b.key) && Slots.intersects(rect(scene,next),b,1.5))) {
            const shifted=edgePoint(scene,"bottom",(point.x+scene.hostWidth/2)/scene.width)
            if (shifted.qualified && shifted.y>=point.y+.1
                    && (clearSegment(scene,point,shifted) || path(scene,point,shifted).qualified)
                    && supportAt(scene,shifted,"bottom"))
                return shifted
        }
    }
    return {qualified:false}
}
function contourArc(scene, a, b, from, to) {
    if (!scene.hasContour || !scene.contour || scene.contour.length < 2) return {qualified:false}
    if (!finite(a?.contourIndex) || !finite(b?.contourIndex)) return {qualified:false}
    const contour = scene.contour, n = contour.length, s = scene.scale
    const idxA = ((a.contourIndex % n) + n) % n
    const idxB = ((b.contourIndex % n) + n) % n
    const endPt = {x:to.x,y:to.y,edge:b.edge,angle:b.angle,nx:b.nx,ny:b.ny}
    if (idxA === idxB) {
        if (!clearSegment(scene, from, endPt)) return {qualified:false}
        return {qualified:true,mode:"walk",points:[endPt],distance:distance(from,endPt)}
    }
    function measureDir(dir) {
        const steps = dir > 0 ? ((idxB - idxA + n) % n) : ((idxA - idxB + n) % n)
        let arcDist = 0, cur = idxA
        for (let k = 0; k < steps; k++) {
            const next = (cur + dir + n) % n
            arcDist += Math.hypot(contour[next].bx - contour[cur].bx, contour[next].by - contour[cur].by)
            cur = next
        }
        return {dir:dir,steps:steps,dist:arcDist}
    }
    const cw = measureDir(1), ccw = measureDir(-1)
    const chosen = cw.dist <= ccw.dist ? cw : ccw
    if (a.edge !== b.edge && chosen.dist > 560 * s) return {qualified:false}
    const dir = chosen.dir, raw = [{x:from.x,y:from.y,edge:a.edge,angle:a.angle,nx:a.nx,ny:a.ny}]
    let cur = idxA
    for (let k = 0; k < chosen.steps; k++) {
        cur = (cur + dir + n) % n
        const cp = contour[cur]
        const pt = {x:cp.x,y:cp.y,edge:cp.edge,angle:cp.angle,nx:cp.nx,ny:cp.ny}
        if (!clearAt(scene, pt)) return {qualified:false}
        raw.push(pt)
    }
    raw.push(endPt)
    // Verify consecutive micro-segments clear every obstacle/convex shoulder (subdividing if needed)
    const dense = [raw[0]]
    for (let i = 1; i < raw.length; i++) {
        const prev = dense[dense.length - 1], curr = raw[i]
        if (distance(prev, curr) < 0.4 && i < raw.length - 1) continue
        if (!clearSegment(scene, prev, curr)) {
            const bcPrev = bodyCenterFromPoint(scene, prev, prev.nx || 0, prev.ny || -1)
            const bcCurr = bodyCenterFromPoint(scene, curr, curr.nx || 0, curr.ny || -1)
            const projMid = newtonProjectWalk(scene, (bcPrev.bx + bcCurr.bx) * 0.5, (bcPrev.by + bcCurr.by) * 0.5, FOOT_OFFSET * s)
            const mid = contourSampleAtBody(scene, projMid.bx, projMid.by)
            const midPt = {x:mid.x,y:mid.y,edge:mid.edge,angle:mid.angle,nx:mid.nx,ny:mid.ny}
            if (!clearAt(scene, midPt) || !clearSegment(scene, prev, midPt) || !clearSegment(scene, midPt, curr))
                return {qualified:false}
            dense.push(midPt)
        }
        dense.push(curr)
    }
    // Simplify flat co-linear runs while preserving ramps, corner curves, and clearSegment safety
    const points = []
    let anchor = dense[0]
    for (let i = 1; i < dense.length - 1; i++) {
        const prev = dense[i], next = dense[i + 1]
        const angDiff = Math.abs(((prev.angle - anchor.angle + 540) % 360) - 180)
        const stepLen = distance(anchor, next)
        const needTurn = angDiff >= 4.0 || stepLen >= 34 * s || !clearSegment(scene, anchor, next)
        if (needTurn) {
            if (distance(anchor, prev) >= 0.8) {
                points.push(prev)
                anchor = prev
            }
        }
    }
    points.push(endPt)
    if (!clearSegment(scene, anchor, endPt)) return {qualified:false}
    let totalDist = 0, p0 = from
    for (let i = 0; i < points.length; i++) {
        totalDist += distance(p0, points[i])
        p0 = points[i]
    }
    return {qualified:true,mode:"walk",points:points,distance:totalDist}
}
function path(scene, from, to) {
    if (!valid(scene) || !clearAt(scene,from) || !clearAt(scene,to)) return {qualified:false}
    const a=supportAt(scene,from,from.edge), b=supportAt(scene,to,to.edge)
    if (scene.hasContour && a && b && finite(a.contourIndex) && finite(b.contourIndex)) {
        const arc=contourArc(scene,a,b,from,to)
        if (arc.qualified) return arc
    }
    const horizontal=a?.axis==="x"
    const walking=!!a && !!b && a.key===b.key && a.edge===b.edge
        && Math.abs((horizontal ? from.y-to.y : from.x-to.x))<.1
        && Math.min(horizontal ? from.x : from.y,horizontal ? to.x : to.y)>=a.from
        && Math.max(horizontal ? from.x : from.y,horizontal ? to.x : to.y)+(horizontal ? scene.hostWidth : scene.hostHeight)<=a.to
    if (clearSegment(scene,from,to))
        return {qualified:true,mode:walking ? "walk" : "fly",points:[to],distance:distance(from,to)}
    // Bounded visibility graph. Every segment checks ALL obstacles; only node
    // construction is capped. Complex/blocked scenes fail closed.
    const nodes=[from,to], gap=2.5
    for (const obstacle of scene.blockers.slice(0,24)) {
        for (const x of [obstacle.x-scene.hostWidth-gap,obstacle.x+obstacle.width+gap])
            for (const y of [obstacle.y-scene.hostHeight-gap,obstacle.y+obstacle.height+gap]) {
                const point={x:x,y:y}
                if (clearAt(scene,point)) nodes.push(point)
            }
    }
    const costs=nodes.map(()=>Infinity), previous=nodes.map(()=>-1), seen=nodes.map(()=>false)
    costs[0]=0
    for (let iteration=0;iteration<nodes.length;iteration++) {
        let index=-1
        for (let i=0;i<nodes.length;i++) if (!seen[i] && (index<0 || costs[i]<costs[index])) index=i
        if (index<0 || !finite(costs[index])) break
        if (index===1) {
            const points=[]
            for (let i=1;i!==0;i=previous[i]) points.unshift(nodes[i])
            return {qualified:true,mode:"fly",points:points,distance:costs[1]}
        }
        seen[index]=true
        for (let i=0;i<nodes.length;i++) {
            if (seen[i] || !clearSegment(scene,nodes[index],nodes[i])) continue
            const cost=costs[index]+distance(nodes[index],nodes[i])
            if (cost<costs[i]) {costs[i]=cost;previous[i]=index}
        }
    }
    return {qualified:false}
}
function nearestWater(scene, point, avoid = null) {
    if (!valid(scene) || !clearAt(scene,point)) return {qualified:false}
    let best=null, candidates=surfacePoints(scene)
    for (const edge of edges) {
        const horizontal=edge==="top" || edge==="bottom"
        const fraction=(horizontal ? point.x+scene.hostWidth/2 : point.y+scene.hostHeight/2)/(horizontal ? scene.width : scene.height)
        const water=edgePoint(scene,edge,fraction)
        if (water.qualified) candidates.push(water)
    }
    candidates.sort((a,b)=>distance(point,a)-distance(point,b))
    for (const water of candidates) {
        if (avoid && distance(water,avoid)<140*scene.scale) continue
        // Straight distance is a lower bound on every routed path. Once it
        // exceeds the best clear route, later candidates cannot improve it.
        if (best && distance(point,water)>=best.route.distance) break
        const route=path(scene,point,water)
        if (route.qualified && (!best || route.distance<best.route.distance)) best={qualified:true,placement:water,route:route}
    }
    return best || {qualified:false}
}
function drop(scene, x, y, previous) {
    if (!valid(scene) || !finite(x) || !finite(y)) return {qualified:false}
    const point={x:clamp(x,2,scene.width-scene.hostWidth-2),y:clamp(y,2,scene.height-scene.hostHeight-2)}
    // Snap to a nearby upper rim, never invent a surface under an airborne drop.
    let snapped = false
    const floors=[]
    if (!scene.hasContour)
        floors.push({y:scene.height-scene.insets.bottom,from:scene.insets.left,to:scene.width-scene.insets.right})
    for (const s of scene.surfaces) {
        const margin=Math.min(Number(s.radius)||0,s.rect.width/2)
        floors.push({y:s.rect.y,from:s.rect.x+margin,to:s.rect.x+s.rect.width-margin})
    }
    const gap=Math.max(2,2*scene.scale)
    for (const floor of floors) {
        const candidate={x:point.x,y:floor.y-scene.hostHeight-gap}
        if (Math.abs(candidate.y-point.y)<18*scene.scale && point.x>=floor.from
            && point.x+scene.hostWidth<=floor.to && clearAt(scene,candidate)) {point.y=candidate.y;snapped=true;break}
    }
    if (!snapped && scene.hasContour && scene.contour && scene.contour.length > 0) {
        let best = null, bestD = 18 * scene.scale
        for (let i = 0; i < scene.contour.length; i++) {
            const cp = scene.contour[i]
            if (cp.ny >= -0.25) continue
            const d = Math.hypot(cp.x - point.x, cp.y - point.y)
            if (d < bestD && clearAt(scene, cp)) {bestD = d; best = cp}
        }
        if (best) {point.x=best.x;point.y=best.y;point.nx=best.nx;point.ny=best.ny;point.angle=best.angle}
    }
    if (!clearAt(scene,point)) return clearAt(scene,previous) ? annotate(scene,previous,"top","drop","drop") : {qualified:false}
    return annotate(scene,point,point.edge ?? "top","drop","drop")
}

// Carry an actor with the painted front of a growing/moving Abyss body. The
// presentation stays revealed; this is a surface attachment, never teleport-
// hide/reappear. The full host and rounded rim still have to fit.
function ride(scene, point, previous) {
    if (!valid(scene) || !previous?.support) return {qualified:false}
    const edge=previous.support.edge, horizontal=edge==="top" || edge==="bottom"
    const oldW=finite(previous.hostWidth) ? previous.hostWidth : scene.hostWidth
    const oldH=finite(previous.hostHeight) ? previous.hostHeight : scene.hostHeight
    const sizeChanged=Math.abs(oldW-scene.hostWidth)>.1 || Math.abs(oldH-scene.hostHeight)>.1
    const candidates=scene.surfaces.filter(s=>Slots.intersects(rect(scene,point),s.rect,1.5)
        || (sizeChanged && s.key===previous.support.key && supportAt(scene,point,edge)?.key===s.key)
        || (s.key===previous.support.key && (s.key==="dock" || (edge==="bottom" && s.edge==="bottom" && s.rect.y>=point.y+oldH-2))))
    const gap=Math.max(2,2*scene.scale)
    for (const surface of candidates) {
        const r=surface.rect
        const pushEdge=(surface.edge===edge && surface.key!=="settings" && surface.key!=="dock"
            && edges.includes(surface.joinedEdge) && surface.joinedEdge!==edge)
            ? surface.joinedEdge : surface.edge
        const pushHorizontal=pushEdge==="top" || pushEdge==="bottom"
        if (pushHorizontal!==horizontal) {
            if (horizontal) {
                const baseY=edges.includes(previous.support.key)
                    ? (edge==="bottom"
                        ? Math.min(scene.height-scene.hostHeight-2,scene.height-scene.insets.bottom-scene.hostHeight)
                        : Math.max(2,scene.insets.top))
                    : point.y
                const pushedX=pushEdge==="right" ? r.x-scene.hostWidth-gap : r.x+r.width+gap
                const dir=pushEdge==="right" ? -1 : 1
                for (let step=0;step<=scene.width;step+=12) {
                    const candidate={x:pushedX+dir*step,y:baseY}
                    if (!clearAt(scene,candidate)) continue
                    const sup=supportAt(scene,candidate,edge)
                    if (sup && sup.edge===edge) {
                        const isEdge=edges.includes(sup.key)
                        return annotate(scene,candidate,edge,isEdge ? "edge" : "surface",sup.key)
                    }
                }
            } else {
                const baseX=edges.includes(previous.support.key)
                    ? (edge==="right"
                        ? Math.min(scene.width-scene.hostWidth-2,scene.width-scene.insets.right-scene.hostWidth)
                        : Math.max(2,scene.insets.left))
                    : point.x
                const pushedY=pushEdge==="bottom" ? r.y-scene.hostHeight-gap : r.y+r.height+gap
                const dir=pushEdge==="bottom" ? -1 : 1
                for (let step=0;step<=scene.height;step+=12) {
                    const candidate={x:baseX,y:pushedY+dir*step}
                    if (!clearAt(scene,candidate)) continue
                    const sup=supportAt(scene,candidate,edge)
                    if (sup && sup.edge===edge) {
                        const isEdge=edges.includes(sup.key)
                        return annotate(scene,candidate,edge,isEdge ? "edge" : "surface",sup.key)
                    }
                }
            }
        }
        const carryEdge=pushHorizontal===horizontal ? pushEdge : edge
        const carryHorizontal=carryEdge==="top" || carryEdge==="bottom"
        const margin=Math.min(Number(surface.radius)||0,(carryHorizontal ? r.width : r.height)/2)
        const extent=carryHorizontal ? scene.hostWidth : scene.hostHeight
        const lo=(carryHorizontal ? r.x : r.y)+margin, hi=(carryHorizontal ? r.x+r.width : r.y+r.height)-margin-extent
        if (hi<lo) continue
        const desired=(carryHorizontal ? point.x+(oldW-scene.hostWidth)/2 : point.y+(oldH-scene.hostHeight)/2)
        const along=clamp(desired,lo,hi)
        const makePoint=pos=>carryEdge==="bottom" ? {x:pos,y:r.y-scene.hostHeight-gap}
            : carryEdge==="top" ? {x:pos,y:r.y+r.height+gap}
            : carryEdge==="left" ? {x:r.x+r.width+gap,y:pos} : {x:r.x-scene.hostWidth-gap,y:pos}
        const candidate=makePoint(along)
        if (clearAt(scene,candidate)) return annotate(scene,candidate,carryEdge,"surface",surface.key)
        for (let step=12;step<=hi-lo;step+=12) {
            for (const dir of [-1,1]) {
                const pos=along+dir*step
                if (pos<lo || pos>hi) continue
                const shifted=makePoint(pos)
                if (clearAt(scene,shifted)) return annotate(scene,shifted,carryEdge,"surface",surface.key)
            }
        }
    }
    if (edges.includes(previous.support.key) && (sizeChanged || !clearAt(scene,point))) {
        const center=horizontal ? point.x+oldW/2 : point.y+oldH/2
        const edgeCandidate=edgePoint(scene,edge,center/(horizontal ? scene.width : scene.height))
        if (edgeCandidate.qualified) return edgeCandidate
    }
    if (((Array.isArray(scene.excludeKeys) && scene.excludeKeys.includes(previous.support.key))
            || previous.support.key==="dock"
            || (!edges.includes(previous.support.key) && edge==="bottom"))
            && edges.includes(edge) && !scene.surfaces.some(s=>s.key===previous.support.key)) {
        const blocker=scene.blockers.find(b=>b.key===previous.support.key)
        const targetEdge=edges.includes(blocker?.edge) ? blocker.edge : edge
        const targetHorizontal=targetEdge==="top" || targetEdge==="bottom"
        const center=targetHorizontal ? point.x+oldW/2 : point.y+oldH/2
        const edgeCandidate=edgePoint(scene,targetEdge,center/(targetHorizontal ? scene.width : scene.height))
        if (edgeCandidate.qualified) return edgeCandidate
    }
    return {qualified:false}
}
