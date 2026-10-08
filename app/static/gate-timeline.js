/* Pure viewport helpers are also exercised by the dependency-free Node tests. */
(() => {
    "use strict";
    const MIN_TIME = Date.UTC(1900, 0, 1);
    const MAX_TIME = Date.UTC(2099, 11, 31, 23, 59, 59);
    const MIN_SPAN = 3600000;
    const MAX_SPAN = MAX_TIME - MIN_TIME;
    function clampViewport(start, end) {
        const span = Math.min(MAX_SPAN, Math.max(MIN_SPAN, end - start));
        const left = Math.min(MAX_TIME - span, Math.max(MIN_TIME, start));
        return {start: Math.round(left / 1000) * 1000, end: Math.round((left + span) / 1000) * 1000};
    }
    function zoomViewport(view, factor, anchor = .5) {
        const span = Math.min(MAX_SPAN, Math.max(MIN_SPAN, (view.end - view.start) * factor));
        const start = view.start + (view.end - view.start - span) * anchor;
        return clampViewport(start, start + span);
    }
    function panViewport(view, delta) {
        return clampViewport(view.start + delta, view.end + delta);
    }
    function adjacentSegments(timestamps, data, body, order) {
        const segments = [];
        for (let i = 1; i < timestamps.length; i++) {
            const a = data.get(timestamps[i - 1])?.positions.find(p => p.body === body);
            const b = data.get(timestamps[i])?.positions.find(p => p.body === body);
            if (a && b && Math.abs(order.indexOf(a.gate) - order.indexOf(b.gate)) <= order.length / 2) {
                segments.push([timestamps[i - 1], a, timestamps[i], b]);
            }
        }
        return segments;
    }
    function prioritized(timestamps) {
        const result = [];
        if (timestamps.length) result.push(timestamps[0]);
        if (timestamps.length > 1) result.push(timestamps[timestamps.length - 1]);
        const ranges = [[1, timestamps.length - 2]];
        while (ranges.length) {
            const [lo, hi] = ranges.shift();
            if (lo > hi) continue;
            const mid = Math.floor((lo + hi) / 2);
            result.push(timestamps[mid]);
            ranges.push([lo, mid - 1], [mid + 1, hi]);
        }
        return result;
    }
    if (typeof module !== "undefined" && module.exports) {
        module.exports = {clampViewport, zoomViewport, panViewport, adjacentSegments, prioritized, MIN_TIME, MAX_TIME, MIN_SPAN};
    }
    if (typeof document === "undefined") return;
    const root = document.getElementById("gate-timeline");
    if (!root) return;
    let gateOrder = JSON.parse(root.dataset?.gateOrder || "[]");
    const byId = id => document.getElementById(`timeline-${id}`);
    const form = byId("range"), chart = byId("chart"), status = byId("status");
    const knownBodies = [
        ["Sun", "Sonne"], ["Earth", "Erde"], ["Moon", "Mond"],
        ["North_Node", "Nördlicher Mondknoten"], ["South_Node", "Südlicher Mondknoten"],
        ["Mercury", "Merkur"], ["Venus", "Venus"], ["Mars", "Mars"], ["Jupiter", "Jupiter"],
        ["Saturn", "Saturn"], ["Uranus", "Uranus"], ["Neptune", "Neptun"], ["Pluto", "Pluto"]
    ];
    const bodySymbols = {
        Sun: "\u2609", Earth: "\u2295", Moon: "\u263d",
        North_Node: "\u260a", South_Node: "\u260b", Mercury: "\u263f",
        Venus: "\u2640", Mars: "\u2642", Jupiter: "\u2643", Saturn: "\u2644",
        Uranus: "\u2645", Neptune: "\u2646", Pluto: "\u2647"
    };
    const colors = ["#ffdf9c", "#92ce79", "#88dfff", "#e0a8ff", "#ffa8d5", "#ffb780",
        "#a8baff", "#ff8181", "#65e4c1", "#dad477", "#aacfff", "#d4b5ff", "#ffb7a8"];
    const bodies = new Map(), data = new Map(), expires = new Map();
    let defaultSelected = true;
    let view = clampViewport(Date.now() - 43200000, Date.now() + 43200000);
    let plan = null, generation = 0, queue = [], active = false, debounce, planAbort;
    let failed = new Set(), phase = "planning", lastError = "", rendered = false, inspectIndex = 0;
    const format = new Intl.DateTimeFormat("de-DE", {timeZone: "UTC", day: "2-digit",
        month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23"});
    const dateLabel = t => format.format(new Date(t));
    const iso = t => new Date(t).toISOString();
    const localValue = t => iso(t).slice(0, 19);
    const parseInput = input => Date.parse(`${input.value}Z`);
    const duration = milliseconds => {
        const seconds = milliseconds / 1000;
        if (seconds < 3600) return `${Math.round(seconds / 60)} Min.`;
        if (seconds < 86400) return `${(seconds / 3600).toLocaleString("de-DE", {maximumFractionDigits: 1})} Std.`;
        return `${(seconds / 86400).toLocaleString("de-DE", {maximumFractionDigits: 1})} Tage`;
    };
    const node = (tag, text) => {
        const el = document.createElement(tag);
        if (text !== undefined) el.textContent = text;
        return el;
    };
    function addBody(key, label) {
        if (bodies.has(key)) return;
        const index = bodies.size;
        const body = {label: label || key, color: colors[index % colors.length],
            selected: defaultSelected, dash: ["", "7 3", "2 3", "9 3 2 3"][Math.floor(index / colors.length) % 4]};
        const wrapper = node("label"), input = node("input"), swatch = node("span");
        input.type = "checkbox";
        input.checked = body.selected;
        swatch.className = "timeline-swatch";
        swatch.style.backgroundColor = body.color;
        input.setAttribute("aria-label", body.label);
        wrapper.title = body.label;
        wrapper.append(input, swatch, node("span",
            `${bodySymbols[key] || ""} ${body.label.slice(0, 2)}`.trim()));
        input.addEventListener("change", () => {
            body.selected = input.checked;
            scheduleRender();
        });
        body.input = input;
        bodies.set(key, body);
        byId("bodies").append(wrapper);
    }
    knownBodies.forEach(([key, label]) => addBody(key, label));
    function syncControls() {
        form.elements.start.value = localValue(view.start);
        form.elements.end.value = localValue(view.end);
        const span = view.end - view.start;
        byId("zoom").value = 1000 * (1 - Math.log(span / MIN_SPAN) / Math.log(MAX_SPAN / MIN_SPAN));
        byId("duration").textContent = duration(span);
        byId("zoom").setAttribute("aria-valuetext", `Sichtbarer Zeitraum: ${duration(span)}`);
        byId("back").disabled = view.start <= MIN_TIME;
        byId("forward").disabled = view.end >= MAX_TIME;
        byId("in").disabled = span <= MIN_SPAN;
        byId("out").disabled = span >= MAX_SPAN;
    }
    function updateStatus() {
        const total = plan?.timestamps.length || 0;
        const loaded = plan?.timestamps.filter(t => data.has(t)).length || 0;
        byId("progress").max = Math.max(1, total);
        byId("progress").value = loaded;
        const loading = phase === "planning" || (phase === "loading" && queue.length + Number(active) > 0);
        chart.setAttribute("aria-busy", String(loading));
        byId("retry").hidden = !(failed.size || phase === "error");
        status.setAttribute("role", failed.size || phase === "error" ? "alert" : "status");
        const prefix = phase === "planning" ? "Neuer Zeitraum wird vorbereitet … Vorhandene Punkte sind eine Vorschau."
            : phase === "error" ? `Zeitraum konnte nicht geladen werden: ${lastError}`
            : `${loading ? "Berechnung läuft" : failed.size ? "Teilweise berechnet" : "Berechnung vollständig"}: ${loaded}/${total} Zeitpunkte`;
        status.textContent = `${prefix}${plan ? ` · UTC ${dateLabel(plan.start)} – ${dateLabel(plan.end)} · Raster ${duration(plan.step_seconds * 1000)} (Randabstände ggf. kürzer).` : ""}${failed.size ? ` ${failed.size} Zeitpunkte nicht geladen: ${lastError}` : ""}`;
    }
    function scheduleRender() {
        if (rendered) return;
        rendered = true;
        requestAnimationFrame(() => {rendered = false; render();});
    }
    function changeViewport(next) {
        view = next;
        generation++;
        queue = [];
        plan = null;
        failed = new Set();
        phase = "planning";
        planAbort?.abort();
        clearTimeout(debounce);
        syncControls();
        byId("inspect").textContent = "Neuer Zeitraum – Werte werden nachgeladen.";
        scheduleRender();
        debounce = setTimeout(loadPlan, 250);
    }
    async function responseJSON(response) {
        if (!response.ok) {
            let detail;
            if (response.headers.get("content-type")?.includes("application/json")) {
                detail = (await response.json()).detail;
            }
            throw new Error(typeof detail === "string" ? detail : `Abfrage fehlgeschlagen (HTTP ${response.status}).`);
        }
        return response.json();
    }
    async function loadPlan() {
        const version = generation;
        const controller = planAbort = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 15000);
        try {
            for (const [key, expiry] of expires) {
                if (expiry <= Date.now()) {data.delete(key); expires.delete(key);}
            }
            const params = new URLSearchParams({start: iso(view.start), end: iso(view.end), samples: form.elements.samples.value});
            const result = await responseJSON(await fetch(`/experiments/timeline?${params}`, {signal: controller.signal}));
            if (version !== generation) return;
            plan = result;
            gateOrder = plan.gate_order;
            failed = new Set();
            phase = "loading";
            queue = prioritized(plan.timestamps).filter(t => !data.has(t));
            scheduleRender();
            pump();
        } catch (error) {
            if (version !== generation) return;
            phase = "error";
            lastError = error.name === "AbortError" ? "Zeitüberschreitung beim Laden des Zeitraums." : error.message;
            scheduleRender();
        } finally {
            clearTimeout(timeout);
        }
    }
    const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
    async function pump() {
        // One persistent worker: stale in-flight calls finish, but no new calls overlap them.
        if (active) return;
        active = true;
        try {
            while (queue.length) {
                const timestamp = queue.shift(), version = generation;
                try {
                    let response;
                    for (let attempt = 0; attempt < 60; attempt++) {
                        if (version !== generation) break;
                        const controller = new AbortController();
                        const timeout = setTimeout(() => controller.abort(), 75000);
                        try {
                            response = await fetch(`/experiments/frame?${new URLSearchParams({timestamp})}`, {signal: controller.signal});
                        } finally {
                            clearTimeout(timeout);
                        }
                        if (response.status !== 503) break;
                        await delay(1000 + Math.random() * 300);
                    }
                    if (version !== generation) continue;
                    const frame = await responseJSON(response);
                    if (version !== generation) continue;
                    if (!Array.isArray(frame.positions) || !frame.positions.length ||
                        Date.parse(frame.timestamp_utc) !== Date.parse(timestamp)) {
                        throw new Error("Die API liefert keine passenden Transitdaten.");
                    }
                    data.delete(timestamp);
                    data.set(timestamp, frame);
                    expires.set(timestamp, Date.now() + 3600000);
                    for (const position of frame.positions) addBody(position.body);
                    // Keep current points plus a small, bounded browser-side overlap cache.
                    while (data.size > 512) {
                        const key = [...data.keys()].find(t => !plan?.timestamps.includes(t));
                        if (!key) break;
                        data.delete(key);
                        expires.delete(key);
                    }
                } catch (error) {
                    if (version !== generation) continue;
                    failed.add(timestamp);
                    lastError = error.name === "AbortError" ? "Zeitüberschreitung beim Berechnungsdienst." : error.message;
                    // Stop a failing service rather than firing the whole remaining queue.
                    for (const remaining of queue) failed.add(remaining);
                    queue = [];
                }
                scheduleRender();
            }
        } finally {
            active = false;
            scheduleRender();
        }
    }
    function render() {
        updateStatus();
        const width = Math.max(200, chart.clientWidth);
        const height = 850, left = 48, right = width - 18, top = 25, bottom = height - 75;
        const ns = "http://www.w3.org/2000/svg";
        const svg = document.createElementNS(ns, "svg");
        svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
        svg.setAttribute("role", "img");
        svg.setAttribute("aria-label", `Gate-Verlauf, ${dateLabel(view.start)} bis ${dateLabel(view.end)} UTC. Einzelwerte in der Tabelle.`);
        const draw = (tag, attrs, text) => {
            const el = document.createElementNS(ns, tag);
            for (const [key, value] of Object.entries(attrs)) el.setAttribute(key, value);
            if (text !== undefined) el.textContent = text;
            svg.append(el);
            return el;
        };
        const order = gateOrder;
        const visibleTimes = plan?.timestamps || [...data.keys()]
            .filter(t => expires.get(t) > Date.now() && Date.parse(t) >= view.start && Date.parse(t) <= view.end)
            .sort((a, b) => Date.parse(a) - Date.parse(b));
        const x = t => left + (Date.parse(t) - view.start) / (view.end - view.start) * (right - left);
        const y = gate => bottom - order.indexOf(gate) / 63 * (bottom - top);
        for (const [index, gate] of order.entries()) {
            const yy = y(gate);
            draw("line", {x1: left, y1: yy, x2: right, y2: yy, stroke: index % 8 === 0 ? "#52627a" : "#2b384b", "stroke-width": .6});
            draw("text", {x: left - 10, y: yy, fill: "#e3eaf3", "text-anchor": "end", "dominant-baseline": "middle", "font-size": 10}, gate);
        }
        const ticks = Math.max(2, Math.floor(width / 190));
        const tickSteps = [60000, 300000, 900000, 3600000, 21600000, 43200000, 86400000, 604800000, 2592000000, 31536000000];
        const tickStep = tickSteps.find(s => s >= (view.end - view.start) / ticks) ||
            Math.ceil((view.end - view.start) / ticks / 31536000000) * 31536000000;
        for (let t = Math.ceil(view.start / tickStep) * tickStep; t <= view.end; t += tickStep) {
            const xx = x(iso(t));
            draw("line", {x1: xx, y1: top, x2: xx, y2: bottom, stroke: "#52627a", "stroke-dasharray": "3 5"});
            const label = draw("text", {x: xx, y: bottom + 24, fill: "#bac8da", "text-anchor": "middle", "font-size": 10});
            const date = document.createElementNS(ns, "tspan");
            date.textContent = new Date(t).toLocaleDateString("de-DE", {timeZone: "UTC"});
            const time = document.createElementNS(ns, "tspan");
            time.setAttribute("x", xx);
            time.setAttribute("dy", 15);
            time.textContent = new Date(t).toLocaleTimeString("de-DE", {timeZone: "UTC", hour: "2-digit", minute: "2-digit", hourCycle: "h23"});
            label.append(date, time);
        }
        let selected = 0, available = 0;
        for (const [key, body] of bodies) {
            if (!body.selected) continue;
            selected++;
            for (const [ta, a, tb, b] of adjacentSegments(plan?.timestamps || [], data, key, order)) {
                draw("path", {d: `M ${x(ta)} ${y(a.gate)} L ${x(tb)} ${y(b.gate)}`, fill: "none",
                    stroke: body.color, "stroke-width": 1.8, "stroke-dasharray": body.dash, opacity: .85});
            }
            for (const timestamp of visibleTimes) {
                const position = data.get(timestamp)?.positions.find(p => p.body === key);
                if (!position) continue;
                available++;
                const marker = draw("circle", {cx: x(timestamp), cy: y(position.gate), r: 3, fill: body.color, stroke: "#101722", "stroke-width": .6});
                const title = document.createElementNS(ns, "title");
                title.textContent = `${body.label}: Gate ${position.gate}.${position.line}, ${position.longitude.toFixed(3)}°, ${dateLabel(data.get(timestamp).timestamp_utc)} UTC`;
                marker.append(title);
            }
        }
        if (!available) {
            draw("text", {x: width / 2, y: height / 2, fill: "#e3eaf3", "text-anchor": "middle", "font-size": 14},
                !selected ? "Keine Himmelskörper ausgewählt" : phase === "error" || failed.size ? "Keine Daten – siehe Fehlermeldung" :
                    plan && !queue.length && !active ? "Keine API-Positionen für die aktiven Körper" : "Zeitpunkte werden berechnet …");
        }
        chart.replaceChildren(svg);
        renderTable();
    }
    function renderTable() {
        if (!byId("table").parentElement.open) return;
        const table = node("table"), head = node("thead"), row = node("tr"), tbody = node("tbody");
        table.append(node("caption", "Tatsächlich berechnete Zeitpunkte (UTC), nur aktive Himmelskörper"));
        for (const title of ["Zeit (UTC)", "Himmelskörper", "Gate.Linie", "Länge"]) {
            const th = node("th", title);
            th.scope = "col";
            row.append(th);
        }
        head.append(row);
        table.append(head, tbody);
        for (const t of plan?.timestamps || []) {
            const frame = data.get(t);
            if (!frame) continue;
            for (const p of frame.positions) {
                if (!bodies.get(p.body)?.selected) continue;
                const tr = node("tr");
                [dateLabel(frame.timestamp_utc), bodies.get(p.body).label, `${p.gate}.${p.line}`, `${p.longitude.toFixed(3)}°`].forEach(value => tr.append(node("td", value)));
                tbody.append(tr);
            }
        }
        byId("table").replaceChildren(table);
    }
    byId("table").parentElement.addEventListener("toggle", renderTable);
    function inspect(timestamp) {
        const frame = data.get(timestamp);
        if (!frame) return;
        const values = frame.positions.filter(p => bodies.get(p.body)?.selected)
            .map(p => `${bodies.get(p.body).label}: ${p.gate}.${p.line} (${p.longitude.toFixed(3)}°)`);
        byId("inspect").textContent = `${dateLabel(frame.timestamp_utc)} UTC · ${values.join(" · ") || "Keine Körper ausgewählt"}`;
    }
    function inspectAt(clientX) {
        if (!plan) return;
        const rect = chart.getBoundingClientRect();
        const ratio = Math.max(0, Math.min(1, (clientX - rect.left - 48) / Math.max(1, rect.width - 66)));
        const target = view.start + ratio * (view.end - view.start);
        const available = plan.timestamps.filter(t => data.has(t));
        if (available.length) inspect(available.reduce((best, t) => Math.abs(Date.parse(t) - target) < Math.abs(Date.parse(best) - target) ? t : best));
    }
    function applyRange() {
        const start = parseInput(form.elements.start), end = parseInput(form.elements.end);
        form.elements.end.setCustomValidity("");
        if (!Number.isFinite(start) || !Number.isFinite(end) || end - start < MIN_SPAN) {
            form.elements.end.setCustomValidity("Das Ende muss mindestens eine Stunde nach dem Start liegen.");
        }
        if (!form.reportValidity()) return;
        changeViewport(clampViewport(start, end));
    }
    form.addEventListener("submit", event => {event.preventDefault(); applyRange();});
    for (const input of [form.elements.start, form.elements.end]) {
        input.addEventListener("input", () => form.elements.end.setCustomValidity(""));
        input.addEventListener("change", applyRange);
    }
    form.elements.samples.addEventListener("change", () => changeViewport(view));
    byId("retry").addEventListener("click", () => changeViewport(view));
    const zoom = (factor, anchor) => changeViewport(zoomViewport(view, factor, anchor));
    byId("in").addEventListener("click", () => zoom(1 / 1.5));
    byId("out").addEventListener("click", () => zoom(1.5));
    byId("zoom").addEventListener("input", event => {
        const span = MIN_SPAN * Math.pow(MAX_SPAN / MIN_SPAN, 1 - Number(event.target.value) / 1000);
        zoom(span / (view.end - view.start));
    });
    const pan = amount => changeViewport(panViewport(view, (view.end - view.start) * amount));
    const now = () => {
        const span = view.end - view.start, center = Math.floor(Date.now() / 60000) * 60000;
        changeViewport(clampViewport(center - span / 2, center + span / 2));
    };
    byId("back").addEventListener("click", () => pan(-.5));
    byId("forward").addEventListener("click", () => pan(.5));
    byId("now").addEventListener("click", now);
    root.querySelectorAll("[data-days]").forEach(button => button.addEventListener("click", () => {
        const span = Number(button.dataset.days) * 86400000, center = (view.start + view.end) / 2;
        changeViewport(clampViewport(center - span / 2, center + span / 2));
    }));
    function selectAll(selected) {
        defaultSelected = selected;
        for (const body of bodies.values()) {
            body.selected = selected;
            body.input.checked = selected;
        }
        scheduleRender();
    }
    byId("all").addEventListener("click", () => selectAll(true));
    byId("none").addEventListener("click", () => selectAll(false));
    chart.addEventListener("wheel", event => {
        event.preventDefault();
        const rect = chart.getBoundingClientRect();
        const anchor = Math.max(0, Math.min(1, (event.clientX - rect.left - 48) / Math.max(1, rect.width - 66)));
        const delta = event.deltaY * (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? rect.height : 1);
        zoom(Math.exp(Math.max(-1, Math.min(1, delta * .002))), anchor);
    }, {passive: false});
    chart.addEventListener("keydown", event => {
        if (["ArrowLeft", "ArrowRight", "+", "=", "-", "Home", "ArrowUp", "ArrowDown"].includes(event.key)) event.preventDefault();
        if (event.key === "ArrowLeft") pan(-.1);
        if (event.key === "ArrowRight") pan(.1);
        if (event.key === "+" || event.key === "=") zoom(1 / 1.25);
        if (event.key === "-") zoom(1.25);
        if (event.key === "Home") now();
        if (event.key === "ArrowUp" || event.key === "ArrowDown") {
            const available = plan?.timestamps.filter(t => data.has(t)) || [];
            inspectIndex = Math.max(0, Math.min(available.length - 1, inspectIndex + (event.key === "ArrowUp" ? -1 : 1)));
            if (available.length) inspect(available[inspectIndex]);
        }
    });
    const pointers = new Map();
    let gesture;
    function resetGesture() {
        const points = [...pointers.values()];
        gesture = points.length ? {view: {...view}, center: points.reduce((sum, p) => sum + p.x, 0) / points.length,
            distance: points.length === 2 ? Math.hypot(points[0].x - points[1].x, points[0].y - points[1].y) : 0} : null;
    }
    chart.addEventListener("pointerdown", event => {
        if (event.button !== 0 || pointers.size >= 2) return;
        chart.setPointerCapture(event.pointerId);
        chart.focus({preventScroll: true});
        pointers.set(event.pointerId, {x: event.clientX, y: event.clientY});
        resetGesture();
        inspectAt(event.clientX);
    });
    chart.addEventListener("pointermove", event => {
        if (!pointers.has(event.pointerId)) {
            if (event.pointerType === "mouse") inspectAt(event.clientX);
            return;
        }
        pointers.set(event.pointerId, {x: event.clientX, y: event.clientY});
        const points = [...pointers.values()], rect = chart.getBoundingClientRect();
        const center = points.reduce((sum, p) => sum + p.x, 0) / points.length;
        let next = gesture.view;
        if (points.length === 2 && gesture.distance > 0) {
            const distance = Math.max(1, Math.hypot(points[0].x - points[1].x, points[0].y - points[1].y));
            next = zoomViewport(next, gesture.distance / distance, Math.max(0, Math.min(1, (gesture.center - rect.left - 48) / Math.max(1, rect.width - 66))));
        }
        const delta = -(center - gesture.center) / Math.max(1, rect.width - 66) * (next.end - next.start);
        if (Math.abs(center - gesture.center) > 3 || points.length === 2) changeViewport(panViewport(next, delta));
    });
    for (const eventName of ["pointerup", "pointercancel", "lostpointercapture"]) {
        chart.addEventListener(eventName, event => {pointers.delete(event.pointerId); resetGesture();});
    }
    if (typeof ResizeObserver !== "undefined") new ResizeObserver(scheduleRender).observe(chart);
    window.addEventListener("pagehide", () => {
        generation++;
        queue = [];
        clearTimeout(debounce);
        planAbort?.abort();
    });
    now();
})();
