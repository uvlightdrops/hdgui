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
    const graph = document.getElementById("center-graph");
    const graphStatus = document.getElementById("center-graph-status");
    const status = document.getElementById("center-status");
    const fillStatus = document.getElementById("center-fill-status");
    const tracks = document.getElementById("center-tracks");
    const timeAxis = document.getElementById("center-time-axis");
    const viewLabel = document.getElementById("center-view-label");
    const zoomOut = document.getElementById("center-zoom-out");
    const zoomIn = document.getElementById("center-zoom-in");
    const time = document.getElementById("center-time");
    const startInput = form.elements.namedItem("start");
    const endInput = form.elements.namedItem("end");
    const samplesInput = form.elements.namedItem("samples");
    const iso = value => new Date(value).toISOString();
    const inputTime = value => iso(value).slice(0, 16);
    const format = value => new Date(value).toLocaleString("de-DE", {timeZone: "UTC"}) + " UTC";
    const DAY = 86400000;
    const MIN_TIME = Date.UTC(1900, 0, 1);
    const MAX_TIME = Date.UTC(2099, 11, 31, 23, 59, 59);
    const tickSteps = [
        1000, 2000, 5000, 10000, 15000, 30000,
        60000, 120000, 300000, 600000, 900000, 1800000,
        3600000, 7200000, 10800000, 21600000, 43200000,
        DAY, 2 * DAY, 5 * DAY, 10 * DAY, 15 * DAY, 30 * DAY,
        60 * DAY, 90 * DAY, 180 * DAY, 365 * DAY,
    ];
    function tickStep(span) {
        const target = span / 8;
        return tickSteps.find(step => step >= target) || Math.ceil(target / (365 * DAY)) * 365 * DAY;
    }

    function tickUnit(step) {
        const units = [
            [365 * DAY, "Jahr", "Jahre"],
            [30 * DAY, "Monat", "Monate"],
            [DAY, "Tag", "Tage"],
            [3600000, "Stunde", "Stunden"],
            [60000, "Minute", "Minuten"],
            [1000, "Sekunde", "Sekunden"],
        ];
        const [unit, singular, plural] = units.find(([size]) => step >= size);
        const count = Math.round(step / unit);
        return `${count} ${count === 1 ? singular : plural}`;
    }

    function viewSpanLabel(span) {
        const [unit, name] = span >= DAY ? [DAY, "Tage"]
            : span >= 3600000 ? [3600000, "Stunden"]
                : [60000, "Minuten"];
        return `${(span / unit).toLocaleString("de-DE", {maximumFractionDigits: 1})} ${name}`;
    }

    function tickFormat(value, step) {
        const options = step >= 30 * DAY
            ? {year: "numeric", month: "2-digit", timeZone: "UTC"}
            : step >= DAY
                ? {day: "2-digit", month: "2-digit", timeZone: "UTC"}
                : step >= 60000
                    ? {day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit",
                        hourCycle: "h23", timeZone: "UTC"}
                    : {day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit",
                        second: "2-digit", hourCycle: "h23", timeZone: "UTC"};
        return new Date(value).toLocaleString("de-DE", options);
    }
    const data = new Map();
    const previousSamples = new Map();
    let timestamps = [];
    let view = null;
    let fullRange = null;
    let selected = 0;
    let selectionEnabled = false;
    let version = null;
    let loadController = null;
    let graphController = null;
    let selectionTimer = null;
    let drag = null;
    let suppressClick = false;
    let fillController = null;
    let fillPromise = null;
    let requestedFillRange = null;
    let baseLoading = false;
    let defaultCentersApplied = false;
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
            selectionEnabled = false;
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

    function applyDefaultCenterVisibility(frame) {
        if (defaultCentersApplied || !Array.isArray(frame.natal_centers)) return;
        const natalCenters = new Set(frame.natal_centers.map(center => center.id));
        root.querySelectorAll("input[data-center]").forEach(input => {
            input.checked = !natalCenters.has(input.dataset.center);
        });
        defaultCentersApplied = true;
    }

    function draw() {
        tracks.replaceChildren();
        timeAxis.replaceChildren();
        if (!view) return;
        const step = tickStep(view.end - view.start);
        viewLabel.textContent = `Einteilung: ${tickUnit(step)} · Ansicht: ${viewSpanLabel(view.end - view.start)} · UTC · Schraffur = nicht geladen`;
        const fullSpan = fullRange.end - fullRange.start;
        const minimumSpan = Math.min(
            fullSpan, Math.max(60000, timestamps[1] ? timestamps[1] - timestamps[0] : fullSpan),
        );
        zoomIn.disabled = baseLoading || view.end - view.start <= minimumSpan;
        zoomOut.disabled = baseLoading || view.end - view.start >= MAX_TIME - MIN_TIME;
        const ticks = [];
        for (let timestamp = Math.ceil(view.start / step) * step;
             timestamp <= view.end; timestamp += step) {
            ticks.push(timestamp);
        }
        const visible = centers.filter(center => root.querySelector(`input[data-center="${center.id}"]`).checked);
        if (!visible.length) tracks.append(element("p", "Keine Zentrum-Spur eingeblendet."));
        for (const center of visible) {
            const row = element("div", undefined, "center-row");
            row.append(element("span", center.label));
            const track = element("div", undefined, "center-track");
            track.setAttribute("role", "group");
            track.setAttribute(
                "aria-label",
                `${center.label}: Zeitraum anklicken, Zeitmarker ziehen oder Ansicht verschieben`,
            );
            if (view.start < fullRange.start) {
                const unloaded = element("span", undefined, "center-unloaded");
                unloaded.style.left = "0%";
                unloaded.style.width = `${(Math.min(view.end, fullRange.start) - view.start) / (view.end - view.start) * 100}%`;
                unloaded.title = "Außerhalb des geladenen Zeitraums";
                track.append(unloaded);
            }
            for (const interval of intervals(timestamps, data, center.id)) {
                const visibleStart = Math.max(interval.start, view.start);
                const visibleEnd = Math.min(interval.end, view.end);
                if (visibleStart >= visibleEnd) continue;
                const segment = element("span", undefined, "center-segment" + (
                    interval.active === null ? " center-unknown" : interval.active ? " center-active" : ""
                ));
                segment.style.left = `${(visibleStart - view.start) / (view.end - view.start) * 100}%`;
                segment.style.width = `${(visibleEnd - visibleStart) / (view.end - view.start) * 100}%`;
                segment.style.backgroundColor = interval.active ? center.color : "";
                segment.title = `${format(interval.start)} – ${format(interval.end)}: ${
                    interval.active === null ? "nicht berechnet" : interval.active ? "definiert (Abtastung)" : "nicht definiert (Abtastung)"
                }`;
                track.append(segment);
            }
            for (const timestamp of ticks) {
                const gridline = element("span", undefined, "center-gridline");
                gridline.style.left = `${(timestamp - view.start) / (view.end - view.start) * 100}%`;
                gridline.setAttribute("aria-hidden", "true");
                track.append(gridline);
            }
            if (view.end > fullRange.end) {
                const unloaded = element("span", undefined, "center-unloaded");
                unloaded.style.left = `${(Math.max(view.start, fullRange.end) - view.start) / (view.end - view.start) * 100}%`;
                unloaded.style.width = `${(view.end - Math.max(view.start, fullRange.end)) / (view.end - view.start) * 100}%`;
                unloaded.title = "Außerhalb des geladenen Zeitraums";
                track.append(unloaded);
            }
            const marker = element("span", undefined, "center-marker");
            marker.style.left = `${(selected - view.start) / (view.end - view.start) * 100}%`;
            bindMarker(marker, track);
            track.append(marker);
            track.addEventListener("click", event => {
                if (!selectionEnabled) return;
                if (suppressClick) {
                    suppressClick = false;
                    return;
                }
                const rect = track.getBoundingClientRect();
                const fraction = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
                selectTime(view.start + fraction * (view.end - view.start));
            });
            track.addEventListener("pointerdown", event => {
                if (
                    drag || !selectionEnabled || !event.isPrimary
                    || (event.pointerType === "mouse" && event.button !== 0)
                    || view.end - view.start >= MAX_TIME - MIN_TIME
                ) return;
                const rect = track.getBoundingClientRect();
                drag = {
                    kind: "pan",
                    x: event.clientX, start: view.start, end: view.end, width: rect.width,
                    pointerId: event.pointerId, moved: false, selectionChanged: false,
                };
                root.setPointerCapture?.(event.pointerId);
            });
            row.append(track);
            tracks.append(row);
        }
        timeAxis.setAttribute(
            "aria-label",
            `Zeitskala in UTC, Teilstriche alle ${tickUnit(step)}; Schraffur markiert nicht geladene Zeiträume`,
        );
        for (const timestamp of ticks) {
            const tick = element("span", tickFormat(timestamp, step), "center-time-tick");
            tick.style.left = `${(timestamp - view.start) / (view.end - view.start) * 100}%`;
            tick.dataset.timestamp = String(timestamp);
            tick.dataset.edge = timestamp === ticks[0] ? "first"
                : timestamp === ticks[ticks.length - 1] ? "last" : "";
            timeAxis.append(tick);
        }
        const axisMarker = element("span", undefined, "center-axis-marker");
        axisMarker.style.left = `${(selected - view.start) / (view.end - view.start) * 100}%`;
        axisMarker.setAttribute("aria-hidden", "true");
        bindMarker(axisMarker, timeAxis);
        timeAxis.append(axisMarker);
    }

    function bindMarker(marker, container) {
        marker.title = "Gewählten Zeitpunkt ziehen";
        marker.addEventListener("click", event => event.stopPropagation());
        marker.addEventListener("pointerdown", event => {
            event.stopPropagation();
            if (drag || !selectionEnabled || !event.isPrimary
                || (event.pointerType === "mouse" && event.button !== 0)) return;
            event.preventDefault();
            drag = {
                kind: "selection", x: event.clientX, selected,
                start: view.start, end: view.end,
                width: container.getBoundingClientRect().width,
                pointerId: event.pointerId, moved: false,
            };
            root.setPointerCapture?.(event.pointerId);
        });
    }

    function setView(start, end) {
        const previousSelected = selected;
        const fullSpan = fullRange.end - fullRange.start;
        const minimumSpan = Math.min(
            fullSpan, Math.max(60000, timestamps[1] ? timestamps[1] - timestamps[0] : fullSpan),
        );
        const span = Math.min(MAX_TIME - MIN_TIME, Math.max(minimumSpan, end - start));
        start = Math.max(MIN_TIME, Math.min(start, MAX_TIME - span));
        view = {start, end: start + span};
        selected = Math.max(view.start, Math.min(selected, view.end));
        time.textContent = format(selected);
        draw();
        return selected !== previousSelected;
    }

    function ensureVisibleSamples() {
        if (!view || !fullRange) return;
        const start = Math.max(MIN_TIME, Math.min(view.start, fullRange.start));
        const end = Math.min(MAX_TIME, Math.max(view.end, fullRange.end));
        if (start >= fullRange.start && end <= fullRange.end) return;
        requestedFillRange = requestedFillRange
            ? {start: Math.min(start, requestedFillRange.start), end: Math.max(end, requestedFillRange.end)}
            : {start, end};
        fillStatus.textContent = "Zeitraumserweiterung wird vorbereitet …";
        fillStatus.setAttribute("role", "status");
        if (fillPromise) return;

        fillPromise = (async () => {
            while (requestedFillRange) {
                const requested = requestedFillRange;
                requestedFillRange = null;
                const target = {
                    start: Math.min(fullRange.start, requested.start),
                    end: Math.max(fullRange.end, requested.end),
                };
                const controller = new AbortController();
                fillController = controller;
                let loaded = 0;
                try {
                    fillStatus.textContent = "Zeitraumserweiterung wird vorbereitet …";
                    const plan = await request("/experiments/timeline?" + new URLSearchParams({
                        start: iso(target.start), end: iso(target.end), samples: samplesInput.value,
                    }), controller.signal);
                    const planned = plan.timestamps.map(Date.parse);
                    const missing = planned.filter(timestamp => !data.has(timestamp));
                    fillStatus.textContent = `Erweitere Zeitverlauf: ${missing.length ? 0 : 100} %`;
                    for (const timestamp of missing) {
                        const cached = previousSamples.get(`${version}|${timestamp}`);
                        const reusable = cached && Date.now() - cached.savedAt < 3600000;
                        const frame = reusable ? cached.frame
                            : await request(frameUrl(timestamp), controller.signal);
                        controller.signal.throwIfAborted();
                        checkVersion(frame);
                        if (!reusable) remember(frame);
                        data.set(timestamp, frame);
                        loaded++;
                        fillStatus.textContent = `Erweitere Zeitverlauf: ${Math.round(loaded / missing.length * 100)} %`;
                    }
                    fullRange = target;
                    timestamps = [...new Set([...timestamps, ...planned, ...data.keys()])]
                        .sort((left, right) => left - right);
                    draw();
                    fillStatus.textContent = "100 % · Zusätzliche Zeitpunkte geladen und im Transit-Cache gesichert.";
                } catch (error) {
                    if (controller.signal.aborted) return;
                    timestamps = [...new Set([...timestamps, ...data.keys()])].sort((left, right) => left - right);
                    draw();
                    fillStatus.textContent = `Zeitverlaufs-Erweiterung fehlgeschlagen: ${error.message}`;
                    fillStatus.setAttribute("role", "alert");
                    if (error.profileChanged) showError(error, fillStatus);
                    return;
                }
            }
        })().finally(() => {
            fillPromise = null;
            if (requestedFillRange) ensureVisibleSamples();
        });
    }

    function selectTime(timestamp = selected) {
        if (!view || !selectionEnabled) return;
        selected = Math.max(view.start, Math.min(view.end, Math.round(timestamp / 1000) * 1000));
        time.textContent = format(selected);
        draw();
        clearTimeout(selectionTimer);
        graphController?.abort();
        graph.replaceChildren(element("p", "BodyGraph für den gewählten Zeitpunkt wird berechnet …"));
        graphStatus.textContent = "";
        const controller = new AbortController();
        graphController = controller;
        const selectedTimestamp = selected;
        selectionTimer = setTimeout(async () => {
            try {
                const frame = await request(frameUrl(selectedTimestamp, true), controller.signal);
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
        releaseDrag();
        loadController?.abort();
        graphController?.abort();
        fillController?.abort();
        requestedFillRange = null;
        clearTimeout(selectionTimer);
        const controller = new AbortController();
        loadController = controller;
        baseLoading = true;
        data.clear();
        timestamps = [];
        view = null;
        fullRange = null;
        version = null;
        selectionEnabled = false;
        graph.replaceChildren(element("p", "BodyGraph wird geladen …"));
        graphStatus.textContent = "";
        tracks.replaceChildren();
        status.setAttribute("role", "status");
        status.textContent = "0 % · Zeitraum wird vorbereitet …";
        try {
            const plan = await request("/experiments/timeline?" + new URLSearchParams({
                start: startInput.value + "Z", end: endInput.value + "Z", samples: samplesInput.value,
            }), controller.signal);
            controller.signal.throwIfAborted();
            timestamps = plan.timestamps.map(Date.parse);
            fullRange = {start: Date.parse(plan.start), end: Date.parse(plan.end)};
            view = {...fullRange};
            selected = view.start;
            time.textContent = format(selected);
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
                    applyDefaultCenterVisibility(frame);
                    showGraph(frame);
                    selectionEnabled = true;
                }
                status.textContent = `${Math.round(data.size / timestamps.length * 100)} % berechnet · Raster ${
                    plan.step_seconds / 60
                } Minuten`;
                draw();
            }
            status.textContent += " · vollständig geladen (Abtastung).";
            baseLoading = false;
            draw();
        } catch (error) {
            if (controller.signal.aborted) return;
            baseLoading = false;
            draw();
            showError(error, status);
            if (!data.size && !error.profileChanged) {
                graph.replaceChildren(element("p", "Keine gültigen Berechnungsdaten verfügbar."));
            }
            if (!error.profileChanged) status.textContent += " Zum Wiederholen „Zeitraum laden“ wählen.";
        }
    }

    form.addEventListener("submit", event => { event.preventDefault(); load(); });
    zoomIn.addEventListener("click", () => {
        if (!view) return;
        const center = (view.start + view.end) / 2;
        const span = (view.end - view.start) / 1.5;
        if (setView(center - span / 2, center + span / 2)) selectTime();
    });
    zoomOut.addEventListener("click", () => {
        if (!view) return;
        const center = (view.start + view.end) / 2;
        const span = (view.end - view.start) * 1.5;
        if (setView(center - span / 2, center + span / 2)) selectTime();
        ensureVisibleSamples();
    });
    document.addEventListener("pointermove", event => {
        if (!drag || event.pointerId !== drag.pointerId) return;
        const delta = drag.x - event.clientX;
        if (drag.kind === "selection") {
            drag.moved = drag.moved || delta !== 0;
            selectTime(drag.selected - delta / drag.width * (drag.end - drag.start));
            return;
        }
        if (!drag.moved && Math.abs(delta) < 4) return;
        drag.moved = true;
        const span = drag.end - drag.start;
        drag.selectionChanged = setView(
            drag.start + delta / drag.width * span,
            drag.end + delta / drag.width * span,
        ) || drag.selectionChanged;
    });
    function releaseDrag() {
        if (!drag) return null;
        const finished = drag;
        drag = null;
        if (root.hasPointerCapture?.(finished.pointerId)) {
            root.releasePointerCapture(finished.pointerId);
        }
        return finished;
    }
    function finishDrag(event) {
        if (!drag || event.pointerId !== drag.pointerId) return;
        const finished = releaseDrag();
        if (finished.moved) {
            suppressClick = true;
            setTimeout(() => { suppressClick = false; }, 0);
            if (finished.kind === "pan") {
                if (finished.selectionChanged) selectTime();
                ensureVisibleSamples();
            }
        }
    }
    document.addEventListener("pointerup", finishDrag);
    document.addEventListener("pointercancel", finishDrag);
    root.addEventListener("lostpointercapture", finishDrag);
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
