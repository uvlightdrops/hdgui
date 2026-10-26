(() => {
    "use strict";

    function intervals(timestamps, data, center) {
        return timestamps.slice(0, -1).map((start, index) => {
            const end = timestamps[index + 1];
            const left = data.get(start);
            const right = data.get(end);
            return {
                start, end,
                active: left && right ? left.centers.some(item => item.id === center) : null,
            };
        });
    }

    function profileVersion(frame) {
        return JSON.stringify([
            frame.profile.id, frame.profile.birth_utc, frame.engine_version, frame.calculation_version,
        ]);
    }

    if (typeof module !== "undefined" && module.exports) {
        module.exports = {intervals, profileVersion};
    }
    if (typeof document === "undefined") return;
    const root = document.getElementById("center-timeline");
    if (!root?.dataset.profileId) return;
    const centers = JSON.parse(root.dataset.centers);
    const form = document.getElementById("center-range");
    const cursor = document.getElementById("center-cursor");
    const graph = document.getElementById("center-graph");
    const graphStatus = document.getElementById("center-graph-status");
    const status = document.getElementById("center-status");
    const progress = document.getElementById("center-progress");
    const tracks = document.getElementById("center-tracks");
    const time = document.getElementById("center-time");
    const startInput = form.elements.namedItem("start");
    const endInput = form.elements.namedItem("end");
    const samplesInput = form.elements.namedItem("samples");
    const iso = value => new Date(value).toISOString();
    const inputTime = value => iso(value).slice(0, 16);
    const format = value => new Date(value).toLocaleString("de-DE", {timeZone: "UTC"}) + " UTC";
    const data = new Map();
    const previousSamples = new Map();
    let timestamps = [];
    let view = null;
    let selected = 0;
    let version = null;
    let loadController = null;
    let graphController = null;
    let selectionTimer = null;
    endInput.value = inputTime(Date.parse(startInput.value + "Z") + 7 * 86400000);

    function element(tag, text, className) {
        const node = document.createElement(tag);
        if (text !== undefined) node.textContent = text;
        if (className) node.className = className;
        return node;
    }

    async function request(url, signal) {
        for (let attempt = 0; attempt < 10; attempt++) {
            signal.throwIfAborted();
            const response = await fetch(url, {signal, cache: "no-store"});
            const payload = await response.json();
            if (response.status === 503 && attempt < 9) {
                await new Promise(resolve => setTimeout(resolve, 1000));
                continue;
            }
            if (!response.ok) {
                const error = new Error(typeof payload.detail === "string" ? payload.detail : "Ungültige Anfrage.");
                error.profileChanged = response.status === 409;
                throw error;
            }
            return payload;
        }
    }

    function frameUrl(timestamp, withGraph = false) {
        return "/centers/frame?" + new URLSearchParams({
            timestamp: iso(timestamp), profile_id: root.dataset.profileId, graph: String(withGraph),
        });
    }

    function checkVersion(frame) {
        if (frame.profile.id !== root.dataset.profileId || (version && profileVersion(frame) !== version)) {
            const error = new Error("Das Geburtsprofil wurde geändert. Bitte die Seite neu laden.");
            error.profileChanged = true;
            throw error;
        }
        version = profileVersion(frame);
    }

    function remember(frame) {
        const key = `${profileVersion(frame)}|${Date.parse(frame.timestamp)}`;
        previousSamples.delete(key);
        previousSamples.set(key, {frame, savedAt: Date.now()});
        while (previousSamples.size > 512) previousSamples.delete(previousSamples.keys().next().value);
    }

    function showError(error, target) {
        target.textContent = error.message;
        target.setAttribute("role", "alert");
        if (error.profileChanged) {
            loadController?.abort();
            graphController?.abort();
            data.clear();
            graph.replaceChildren(element("p", "Profil geändert; Darstellung verworfen. Bitte Seite neu laden."));
            cursor.disabled = true;
            draw();
            status.textContent = error.message;
            graphStatus.textContent = error.message;
        }
    }

    function showGraph(frame) {
        // SVG is generated locally from the packaged layout and XML-escaped catalog text.
        graph.innerHTML = frame.svg;
        const names = frame.centers.map(center => center.label).join(", ") || "keine";
        graphStatus.setAttribute("role", "status");
        graphStatus.textContent = `${format(frame.timestamp)} · Definiert: ${names}`;
    }

    function draw() {
        tracks.replaceChildren();
        if (!view) return;
        const visible = centers.filter(center => root.querySelector(`input[data-center="${center.id}"]`).checked);
        if (!visible.length) tracks.append(element("p", "Keine Zentrum-Spur eingeblendet."));
        for (const center of visible) {
            const row = element("div", undefined, "center-row");
            row.append(element("span", center.label));
            const track = element("div", undefined, "center-track");
            track.setAttribute("role", "group");
            track.setAttribute("aria-label", `${center.label}: Zeitraum anklicken; alternativ Zeitregler verwenden`);
            for (const interval of intervals(timestamps, data, center.id)) {
                const segment = element("span", undefined, "center-segment" + (
                    interval.active === null ? " center-unknown" : interval.active ? " center-active" : ""
                ));
                segment.style.left = `${(interval.start - view.start) / (view.end - view.start) * 100}%`;
                segment.style.width = `${(interval.end - interval.start) / (view.end - view.start) * 100}%`;
                segment.style.backgroundColor = interval.active ? center.color : "";
                segment.title = `${format(interval.start)} – ${format(interval.end)}: ${
                    interval.active === null ? "nicht berechnet" : interval.active ? "definiert (Abtastung)" : "nicht definiert (Abtastung)"
                }`;
                track.append(segment);
            }
            const marker = element("span", undefined, "center-marker");
            marker.style.left = `${(selected - view.start) / (view.end - view.start) * 100}%`;
            track.append(marker);
            track.addEventListener("click", event => {
                if (cursor.disabled) return;
                const rect = track.getBoundingClientRect();
                cursor.value = String(Math.max(0, Math.min(1000, (event.clientX - rect.left) / rect.width * 1000)));
                selectTime();
            });
            row.append(track);
            tracks.append(row);
        }
        tracks.append(element("p", `${format(view.start)} — ${format(view.end)}`, "timeline-muted"));
    }

    function selectTime() {
        if (!view || cursor.disabled) return;
        selected = Math.round((view.start + (view.end - view.start) * Number(cursor.value) / 1000) / 1000) * 1000;
        time.textContent = format(selected);
        draw();
        clearTimeout(selectionTimer);
        graphController?.abort();
        graph.replaceChildren(element("p", "BodyGraph für den gewählten Zeitpunkt wird berechnet …"));
        graphStatus.textContent = "";
        const controller = new AbortController();
        graphController = controller;
        const timestamp = selected;
        selectionTimer = setTimeout(async () => {
            try {
                const frame = await request(frameUrl(timestamp, true), controller.signal);
                controller.signal.throwIfAborted();
                checkVersion(frame);
                remember(frame);
                showGraph(frame);
            } catch (error) {
                if (controller.signal.aborted) return;
                graph.replaceChildren(element("p", "BodyGraph konnte nicht geladen werden."));
                showError(error, graphStatus);
            }
        }, 180);
    }

    async function load() {
        loadController?.abort();
        graphController?.abort();
        clearTimeout(selectionTimer);
        const controller = new AbortController();
        loadController = controller;
        data.clear();
        timestamps = [];
        view = null;
        version = null;
        cursor.disabled = true;
        progress.value = 0;
        graph.replaceChildren(element("p", "BodyGraph wird geladen …"));
        graphStatus.textContent = "";
        tracks.replaceChildren();
        status.setAttribute("role", "status");
        status.textContent = "Zeitraum wird vorbereitet …";
        try {
            const plan = await request("/experiments/timeline?" + new URLSearchParams({
                start: startInput.value + "Z", end: endInput.value + "Z", samples: samplesInput.value,
            }), controller.signal);
            controller.signal.throwIfAborted();
            timestamps = plan.timestamps.map(Date.parse);
            view = {start: Date.parse(plan.start), end: Date.parse(plan.end)};
            selected = view.start;
            cursor.value = "0";
            time.textContent = format(selected);
            progress.max = timestamps.length;
            draw();
            for (const timestamp of timestamps) {
                const first = timestamp === timestamps[0];
                // Always validate the current profile on the server before reusing a window.
                const cached = first ? null : previousSamples.get(`${version}|${timestamp}`);
                const reusable = cached && Date.now() - cached.savedAt < 3600000;
                const frame = reusable ? cached.frame
                    : await request(frameUrl(timestamp, first), controller.signal);
                controller.signal.throwIfAborted();
                checkVersion(frame);
                if (!reusable) remember(frame);
                data.set(timestamp, frame);
                if (first) {
                    showGraph(frame);
                    cursor.disabled = false;
                }
                progress.value = data.size;
                status.textContent = `${data.size} / ${timestamps.length} Zeitpunkte berechnet · Raster ${
                    plan.step_seconds / 60
                } Minuten`;
                draw();
            }
            status.textContent += " · vollständig geladen (Abtastung).";
        } catch (error) {
            if (controller.signal.aborted) return;
            showError(error, status);
            if (!data.size && !error.profileChanged) {
                graph.replaceChildren(element("p", "Keine gültigen Berechnungsdaten verfügbar."));
            }
            if (!error.profileChanged) status.textContent += " Zum Wiederholen „Zeitraum laden“ wählen.";
        }
    }

    form.addEventListener("submit", event => { event.preventDefault(); load(); });
    cursor.addEventListener("input", selectTime);
    root.querySelectorAll("input[data-center]").forEach(input => input.addEventListener("change", draw));
    root.querySelectorAll("button[data-days], button[data-shift]").forEach(button => {
        button.addEventListener("click", () => {
            let start = Date.parse(startInput.value + "Z");
            let end = Date.parse(endInput.value + "Z");
            if (button.dataset.days) end = start + Number(button.dataset.days) * 86400000;
            else {
                const delta = (end - start) * Number(button.dataset.shift);
                start += delta;
                end += delta;
            }
            if (start < Date.UTC(1900, 0, 1) || end > Date.UTC(2099, 11, 31, 23, 59)) {
                showError(new Error("Zeiträume müssen zwischen 1900 und 2099 liegen."), status);
                return;
            }
            startInput.value = inputTime(start);
            endInput.value = inputTime(end);
            load();
        });
    });
    load();
})();
