"use strict";
const assert = require("node:assert/strict");
const {intervals, profileVersion} = require("../app/static/center-timeline.js");
const times = [0, 1000, 2000, 3000];
const frame = (...centers) => ({centers: centers.map(id => ({id}))});
const data = new Map([[0, frame("Head")], [1000, frame("Root")], [3000, frame("Head")]]);
assert.deepEqual(intervals(times, data, "Head"), [
    {start: 0, end: 1000, active: true},
    {start: 1000, end: 2000, active: null},
    {start: 2000, end: 3000, active: null},
], "Do not bridge missing samples or misrepresent them as inactive");
data.set(2000, frame("Head"));
assert.deepEqual(intervals(times, data, "Head").map(item => item.active), [true, false, true]);
assert.deepEqual(intervals(times, data, "Root").map(item => item.active), [false, true, false]);
assert.deepEqual(intervals([], data, "Head"), []);
assert.deepEqual(intervals([0], data, "Head"), []);
const base = {
    profile: {id: "p1", birth_utc: "2000-01-01T00:00:00Z"},
    engine_version: "4", natal_centers: [{id: "Head"}],
};
assert.equal(profileVersion(base), profileVersion({...base, centers: [{id: "Head"}]}));
assert.notEqual(profileVersion(base), profileVersion({...base, engine_version: "5"}));
assert.notEqual(profileVersion({...base, calculation_version: "api-a"}),
    profileVersion({...base, calculation_version: "api-b"}));
assert.notEqual(profileVersion(base), profileVersion({...base, profile: {...base.profile, id: "p2"}}));
assert.notEqual(profileVersion(base), profileVersion({...base, profile: {...base.profile, birth_utc: "2001-01-01T00:00:00Z"}}));
console.log("Center timeline: sampled intervals, gaps and profile identity passed.");

const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
class Element {
    constructor() {
        this.dataset = {};
        this.children = [];
        this.listeners = {};
        this.attributes = {};
        this.style = {};
        this.value = "";
        this.textContent = "";
    }
    append(...nodes) { this.children.push(...nodes); }
    replaceChildren(...nodes) { this.children = [...nodes]; }
    setAttribute(key, value) { this.attributes[key] = value; }
    addEventListener(key, callback) { this.listeners[key] = callback; }
    fire(key, extra = {}) {
        this.listeners[key]?.({preventDefault() {}, stopPropagation() {}, ...extra});
    }
    setPointerCapture(pointerId) { this.capturedPointer = pointerId; }
    hasPointerCapture(pointerId) { return this.capturedPointer === pointerId; }
    releasePointerCapture(pointerId) {
        assert.equal(this.capturedPointer, pointerId);
        this.capturedPointer = undefined;
    }
    getBoundingClientRect() { return {left: 0, width: 1000}; }
}

async function controllerTests() {
    const ids = new Map();
    const get = id => {
        if (!ids.has(id)) ids.set(id, new Element());
        return ids.get(id);
    };
    const root = get("center-timeline");
    root.dataset = {
        profileId: "p1",
        centers: JSON.stringify([{id: "Head", label: "Kopf", color: "#fff"}, {id: "Root", label: "Wurzel", color: "#aaa"}]),
    };
    const boxes = ["Head", "Root"].map(id => Object.assign(new Element(), {checked: true, dataset: {center: id}}));
    root.querySelectorAll = query => query === "input[data-center]" ? boxes : [];
    root.querySelector = query => boxes.find(box => query.includes(`"${box.dataset.center}"`));
    const form = get("center-range");
    const fields = {
        start: Object.assign(new Element(), {value: "2026-10-25T00:00"}),
        end: new Element(),
        samples: Object.assign(new Element(), {value: "64"}),
    };
    form.elements = {namedItem: name => fields[name]};
    const pending = [];
    let calls = 0;
    const response = (payload, status = 200) => ({ok: status === 200, status, json: async () => payload});
    const fetch = async url => {
        const parsed = new URL(url, "http://localhost");
        if (parsed.pathname === "/experiments/timeline") {
            const normalize = value => new Date(Math.floor(Date.parse(value) / 1000) * 1000).toISOString();
            const start = normalize(parsed.searchParams.get("start"));
            const end = normalize(parsed.searchParams.get("end"));
            const mid = new Date((Date.parse(start) + Date.parse(end)) / 2).toISOString();
            return response({start, end, timestamps: [start, mid, end], step_seconds: 3600});
        }
        calls++;
        return new Promise(resolve => pending.push({
            timestamp: parsed.searchParams.get("timestamp"), graph: parsed.searchParams.get("graph"),
            resolve: (payload, status) => resolve(response(payload, status)),
        }));
    };
    const waitFor = async predicate => {
        for (let i = 0; i < 100; i++) {
            if (predicate()) return;
            await new Promise(resolve => setTimeout(resolve, 5));
        }
        throw new Error(`Controller did not reach expected state: ${get("center-status").textContent}`);
    };
    const fulfill = (request, changed = false) => request.resolve({
        ...base, profile: {...base.profile, id: changed ? "p2" : "p1"},
        timestamp: request.timestamp, ...frame("Head"), svg: "<svg>selected graph</svg>",
    });
    const fulfillExtension = async expectedCalls => {
        for (let i = 0; i < 200; i++) {
            while (pending.length) fulfill(pending.shift());
            if (calls >= expectedCalls && get("center-fill-status").textContent.includes("gesichert")) return;
            await new Promise(resolve => setTimeout(resolve, 5));
        }
        throw new Error(`Extension did not complete: ${get("center-fill-status").textContent}; ${get("center-view-label").textContent}; zoom disabled=${get("center-zoom-out").disabled}`);
    };
    const documentListeners = {};
    vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../app/static/center-timeline.js"), "utf8"), {
        document: {
            getElementById: get, createElement: () => new Element(),
            addEventListener: (name, callback) => { documentListeners[name] = callback; },
        },
        fetch, URLSearchParams, Date, AbortController,
        setTimeout: callback => setTimeout(callback, 1), clearTimeout,
    });
    for (let i = 0; i < 3; i++) {
        await waitFor(() => pending.length === 1);
        const request = pending.shift();
        assert.equal(request.graph, i === 0 ? "true" : "false");
        fulfill(request);
        await waitFor(() => get("center-status").textContent.startsWith(`${Math.round((i + 1) / 3 * 100)} %`));
    }
    await waitFor(() => get("center-status").textContent.includes("vollständig"));
    assert.match(get("center-status").textContent, /^100 % berechnet/);
    assert.equal(ids.has("center-progress"), false, "Loading uses a percentage without a progress widget");
    assert.equal(boxes[0].checked, false, "Natal-defined centers are hidden by default");
    assert.equal(boxes[1].checked, true, "Centers not defined at birth remain visible");
    assert.equal(get("center-tracks").children.length, 1, "Only non-natal-defined centers are rendered");
    assert.ok(get("center-time-axis").children.length >= 5, "Time axis has labeled unit ticks");
    const fullTicks = get("center-time-axis").children.map(tick => tick.dataset.timestamp);
    const fullView = get("center-view-label").textContent;
    assert.match(fullView, /Einteilung:.*Ansicht:.*UTC/);
    assert.equal(get("center-zoom-out").disabled, false);
    get("center-zoom-in").fire("click");
    assert.notEqual(get("center-view-label").textContent, fullView, "Zoom reports and changes the visible scale");
    assert.notDeepEqual(
        get("center-time-axis").children.map(tick => tick.dataset.timestamp), fullTicks,
        "Zoom updates the marked scale units",
    );
    assert.equal(get("center-zoom-out").disabled, false);
    const axisMarker = () => get("center-time-axis").children
        .find(item => item.className === "center-axis-marker");
    const trackMarker = () => get("center-tracks").children[0].children[1].children
        .find(item => item.className === "center-marker");
    assert.equal(axisMarker().style.left, trackMarker().style.left,
        "Selected time marker aligns on the time axis and center tracks");
    const track = get("center-tracks").children[0].children[1];
    track.fire("pointerdown", {clientX: 500, isPrimary: true, pointerType: "mouse", button: 0, pointerId: 1});
    documentListeners.pointermove({clientX: 400, pointerId: 1});
    documentListeners.pointerup({pointerId: 1});
    assert.notEqual(get("center-time-axis").children[0].dataset.timestamp, fullTicks[0],
        "Dragging a zoomed timeline pans the visible time range");
    await waitFor(() => pending.length === 1);
    fulfill(pending.shift());
    await waitFor(() => get("center-graph-status").textContent.includes("Definiert"));
    const zoomedView = get("center-view-label").textContent;
    const callsBeforeFirstFill = calls;
    get("center-zoom-out").fire("click");
    await fulfillExtension(callsBeforeFirstFill + 2);
    assert.notEqual(get("center-view-label").textContent, zoomedView, "Zoom out expands the visible scale");
    const callsBeforeSecondFill = calls;
    get("center-zoom-out").fire("click");
    await fulfillExtension(callsBeforeSecondFill + 3);
    assert.ok(
        get("center-fill-status").textContent.includes("gesichert"),
        "New samples are loaded and cached when zooming out",
    );
    const callsAfterFill = calls;
    get("center-zoom-in").fire("click");
    get("center-zoom-out").fire("click");
    assert.equal(calls, callsAfterFill, "Already loaded time ranges are not fetched again");
    assert.match(get("center-graph").innerHTML, /selected graph/);
    boxes[0].checked = false;
    boxes[0].fire("change");
    assert.equal(get("center-tracks").children.length, 1);
    assert.equal(calls, callsAfterFill, "Toggling tracks must not recalculate");
    form.fire("submit");
    await waitFor(() => pending.length === 1);
    fulfill(pending.shift());
    await waitFor(() => get("center-status").textContent.includes("vollständig"));
    assert.equal(calls, callsAfterFill + 1, "Reload validates first sample, then reuses cached window samples");

    const ticksBeforeMarkerDrag = get("center-time-axis").children.map(tick => tick.dataset.timestamp);
    let stopped = false;
    trackMarker().fire("pointerdown", {
        clientX: 0, isPrimary: true, pointerType: "mouse", button: 0, pointerId: 2,
        stopPropagation() { stopped = true; },
    });
    assert.ok(stopped, "Marker pointerdown must not start a bar pan");
    assert.equal(root.capturedPointer, 2, "Capture survives replacement of the rendered marker");
    documentListeners.pointermove({clientX: 333.7, pointerId: 9});
    assert.equal(axisMarker().style.left, "0%", "Other pointers cannot move the marker");
    documentListeners.pointermove({clientX: 333.7, pointerId: 2});
    assert.ok(parseFloat(trackMarker().style.left) > 33, "Marker follows the pointer continuously");
    documentListeners.pointermove({clientX: 433.7, pointerId: 2});
    documentListeners.pointerup({pointerId: 9});
    assert.equal(root.capturedPointer, 2, "Other pointers cannot finish a drag");
    documentListeners.pointerup({pointerId: 2});
    assert.equal(root.capturedPointer, undefined, "Marker drag releases capture");
    assert.deepEqual(get("center-time-axis").children.map(tick => tick.dataset.timestamp), ticksBeforeMarkerDrag,
        "Dragging the marker never pans the visible time range");
    assert.equal(axisMarker().style.left, trackMarker().style.left);
    await waitFor(() => pending.length === 1);
    const markerSelection = pending.shift();
    assert.equal(markerSelection.timestamp,
        new Date(Date.parse(fields.start.value + "Z") + Math.round(7 * 86400 * 0.4337) * 1000).toISOString(),
        "Marker requests the exact selected second, not a sampled frame or quantized slider step");
    assert.equal(markerSelection.graph, "true");
    fulfill(markerSelection);
    await waitFor(() => get("center-graph-status").textContent.includes("Definiert"));

    axisMarker().fire("pointerdown", {
        clientX: 433.7, isPrimary: true, pointerType: "mouse", button: 0, pointerId: 3,
    });
    documentListeners.pointermove({clientX: 2000, pointerId: 3});
    assert.equal(trackMarker().style.left, "100%", "Axis marker clamps to the visible range");
    documentListeners.pointercancel({pointerId: 3});
    assert.equal(root.capturedPointer, undefined, "Pointer cancellation releases capture");
    documentListeners.pointermove({clientX: 0, pointerId: 3});
    assert.equal(trackMarker().style.left, "100%", "Cancelled drag no longer moves the marker");
    await waitFor(() => pending.length === 1);
    fulfill(pending.shift());
    await waitFor(() => get("center-graph-status").textContent.includes("Definiert"));

    axisMarker().fire("pointerdown", {
        clientX: 1000, isPrimary: true, pointerType: "mouse", button: 0, pointerId: 5,
    });
    root.capturedPointer = undefined;
    root.fire("lostpointercapture", {pointerId: 5});
    documentListeners.pointermove({clientX: 0, pointerId: 5});
    assert.equal(trackMarker().style.left, "100%", "Losing capture terminates the marker drag");

    const selectTrackTime = clientX => {
        get("center-tracks").children[0].children[1].fire("click", {clientX});
    };
    selectTrackTime(250);
    await waitFor(() => pending.length === 1);
    const oldSelection = pending.shift();
    selectTrackTime(750);
    await waitFor(() => pending.length === 1);
    const newSelection = pending.shift();
    fulfill(oldSelection);
    fulfill(newSelection);
    await waitFor(() => get("center-graph-status").textContent.includes("Definiert"));
    assert.match(get("center-graph-status").textContent, /30\.10\.2026/, "Only the latest cursor response is displayed");

    const pan = delta => {
        get("center-tracks").children[0].children[1].fire("pointerdown", {
            clientX: 500, isPrimary: true, pointerType: "mouse", button: 0, pointerId: 4,
        });
        documentListeners.pointermove({clientX: 500 + delta, pointerId: 4});
        documentListeners.pointerup({pointerId: 4});
    };
    const selectedBeforePan = get("center-time").textContent;
    const callsBeforePan = calls;
    pan(100);
    assert.equal(get("center-time").textContent, selectedBeforePan, "Pan keeps a still-visible selection");
    await fulfillExtension(callsBeforePan + 2);
    assert.equal(calls, callsBeforePan + 2, "Pan loads exposed samples even without changing selection");
    pan(-100);
    pan(100);
    await new Promise(resolve => setTimeout(resolve, 10));
    assert.equal(calls, callsBeforePan + 2, "Returning to loaded periods reuses samples");

    form.fire("submit");
    await waitFor(() => pending.length === 1);
    pending.shift().resolve({detail: "Dienst offline"}, 502);
    await waitFor(() => get("center-status").textContent.includes("Dienst offline"));
    assert.equal(get("center-status").attributes.role, "alert");
    assert.equal(ids.has("center-cursor"), false, "Timeline no longer uses a separate time slider");
    assert.match(get("center-graph").children[0].textContent, /Keine gültigen/);

    fields.start.value = "2027-01-01T00:00";
    fields.end.value = "2027-01-08T00:00";
    form.fire("submit");
    await waitFor(() => pending.length === 1);
    const busy = pending.shift();
    busy.resolve({detail: "belegt"}, 503);
    await waitFor(() => pending.length === 1);
    assert.equal(pending[0].timestamp, busy.timestamp);
    fulfill(pending.shift());
    await waitFor(() => pending.length === 1);
    fulfill(pending.shift(), true);
    await waitFor(() => get("center-status").textContent.includes("Geburtsprofil wurde geändert"));
    const callsAfterProfileChange = calls;
    selectTrackTime(500);
    assert.equal(calls, callsAfterProfileChange, "Selection stays disabled after a profile change");
    assert.match(get("center-graph").children[0].textContent, /Darstellung verworfen/);
    console.log("Center timeline controller: loading, tracks, exact selection, stale requests, retry and profile changes passed.");
}

controllerTests().catch(error => { console.error(error); process.exitCode = 1; });
