.pragma library

function title(value) {
    return typeof value === "string" ? value.replace(/[\u0000-\u001f\u007f]/g, " ").trim().slice(0,180) : ""
}

function day(date) {
    return date.getFullYear()+"-"+String(date.getMonth()+1).padStart(2,"0")+"-"+String(date.getDate()).padStart(2,"0")
}

function collect(now, consent, todo, calendar, journal) {
    if (!now || !Number.isFinite(now.getTime())) return []
    const today = day(now), rows = []
    function add(start, text, kind, source) {
        const label = title(text)
        if (Number.isInteger(start) && start >= 0 && start < 1440 && label)
            rows.push({start:start, title:label, kind:kind, key:source+":"+start})
    }
    if (consent?.todo === true && Array.isArray(todo)) for (const row of todo.slice(0,128)) {
        if (!row || row.done !== false || row.sourceDate !== today || typeof row.startTime !== "string") continue
        const time = /^(\d{1,2}):(\d{2})$/.exec(row.startTime)
        if (time && Number(time[1]) < 24 && Number(time[2]) < 60)
            add(Number(time[1])*60+Number(time[2]),row.content,"task","todo")
    }
    if (consent?.calendar === true && Array.isArray(calendar)) for (const row of calendar.slice(0,32)) {
        if (!row || row.allDay !== false || !(typeof row.startDate === "string" && row.startDate.length <= 80
                || row.startDate instanceof Date)) continue
        const start = new Date(row.startDate)
        if (Number.isFinite(start.getTime()) && day(start) === today)
            add(start.getHours()*60+start.getMinutes(),row.summary ?? row.title,"agenda","calendar")
    }
    if (consent?.journal === true && journal?.date === today && Array.isArray(journal.schedule))
        for (const row of journal.schedule.slice(0,16)) {
            if (row && ["schedule","calisthenics","cardio"].includes(row.kind))
                add(row.start,row.title,row.kind,"journal")
        }
    rows.sort((a,b) => a.start-b.start)
    return rows
}
