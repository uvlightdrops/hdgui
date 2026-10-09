"use strict";
const assert = require("node:assert/strict");
const {clampViewport, zoomViewport, panViewport, adjacentSegments, prioritized, validOverlay, natalBands,
    MIN_TIME, MAX_TIME, MIN_SPAN} = require("../app/static/gate-timeline.js");

const view = {start: Date.UTC(2026, 9, 8), end: Date.UTC(2026, 9, 9)};
const span = view.end - view.start;
assert.deepEqual(clampViewport(view.start, view.end), view);
assert.equal(zoomViewport(view, .5).end - zoomViewport(view, .5).start, span / 2);
const anchored = zoomViewport(view, .5, .25);
assert.equal(anchored.start + (anchored.end - anchored.start) * .25, view.start + span * .25);
assert.equal(zoomViewport(view, 1e-10).end - zoomViewport(view, 1e-10).start, MIN_SPAN);
assert.deepEqual(zoomViewport(view, 1e10), {start: MIN_TIME, end: MAX_TIME});
assert.equal(panViewport(view, -1e20).start, MIN_TIME);
assert.equal(panViewport(view, 1e20).end, MAX_TIME);
assert.equal(panViewport(view, span).start, view.end);
assert.equal(clampViewport(view.start + 123.45, view.end + 123.45).start % 1000, 0);
const order = Array.from({length: 64}, (_, i) => i + 1);
const timestamps = ["2026-10-08T00:00:00Z", "2026-10-08T01:00:00Z", "2026-10-08T02:00:00Z"];
const frame = gate => ({positions: [{body: "Sun", gate, line: 1}]});
const data = new Map([[timestamps[0], frame(64)], [timestamps[1], frame(1)], [timestamps[2], frame(2)]]);
assert.equal(adjacentSegments(timestamps, data, "Sun", order).length, 1, "Do not connect cyclic wrap");
assert.equal(adjacentSegments(timestamps, data, "Moon", order).length, 0, "Bodies are independent");
data.delete(timestamps[1]);
assert.equal(adjacentSegments(timestamps, data, "Sun", order).length, 0, "Never bridge unloaded points");
assert.deepEqual(prioritized([]), []);
assert.deepEqual(prioritized(["one"]), ["one"]);
const sequence = Array.from({length: 64}, (_, i) => `t${i}`);
const priority = prioritized(sequence);
assert.deepEqual(priority.slice(0, 3), ["t0", "t63", "t31"]);
assert.equal(new Set(priority).size, 64);
assert.deepEqual([...priority].sort(), [...sequence].sort());
const overlay = {
    profile: {id: "p1", name: "Anna", birth_utc: "1990-07-15T12:30:00+00:00"},
    personality: [{body: "Sun", gate: 64, line: 2, longitude: 10.5}, {body: "Earth", gate: 47, line: 2, longitude: 190.5}],
    design: [{body: "Sun", gate: 34, line: 5, longitude: 100}, {body: "Earth", gate: 64, line: 6, longitude: 280}],
    channels: [{gates: [64, 47], centers: ["Head", "Ajna"]}], centers: [{id: "Head", label: "Kopf"}]
};
assert(validOverlay(overlay));
assert(!validOverlay({...overlay, design: []}), "Both sources are required");
assert(!validOverlay({...overlay, personality: [{body: "Sun", gate: 65, line: 1, longitude: 1}]}));
assert(!validOverlay({profile: null}));
const bands = natalBands(overlay);
assert.deepEqual([...bands.keys()].sort((a, b) => a - b), [34, 47, 64]);
assert.deepEqual(bands.get(64).personality.map(p => p.body), ["Sun"]);
assert.deepEqual(bands.get(64).design.map(p => p.body), ["Earth"], "Sources stay separate per gate");
console.log("Gate timeline: viewport, bounds, cyclic paths and progressive sampling passed.");

// Exercise the actual browser controller with a minimal DOM, controlled fetches and no framework.
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
class Element {
    constructor(tag = "div") {
        this.dataset = {};
        this.tag = tag;
        this.listeners = new Map();
        this.children = [];
        this.attributes = {};
        this.style = {};
        this.value = "";
        this.clientWidth = 800;
    }
    addEventListener(name, fn) {this.listeners.set(name, fn);}
    fire(name, extra = {}) {this.listeners.get(name)?.({target: this, preventDefault() {}, ...extra});}
    append(...nodes) {for (const child of nodes) {this.children.push(child); child.parentElement = this;}}
    replaceChildren(...nodes) {this.children = []; this.append(...nodes);}
    setAttribute(name, value) {this.attributes[name] = value;}
    setCustomValidity(value) {this.invalid = value;}
    reportValidity() {return !Object.values(this.elements).some(el => el.invalid);}
    getBoundingClientRect() {return {left: 0, width: 800, height: 850};}
    focus() {}
    setPointerCapture() {}
    querySelectorAll() {return [];}
}
async function browserControllerTests() {
    const ids = new Map();
    const get = id => {
        if (!ids.has(id)) ids.set(id, new Element());
        return ids.get(id);
    };
    const form = get("timeline-range");
    form.elements = {start: new Element("input"), end: new Element("input"), samples: new Element("select")};
    form.elements.samples.value = "64";
    const details = new Element("details");
    details.open = false;
    details.append(get("timeline-table"));
    const raf = [];
    const pending = [];
    let active = 0, maximumActive = 0, plans = 0, calls = 0;
    const response = (payload, status = 200) => ({
        ok: status === 200, status, headers: {get: () => "application/json"}, json: async () => payload
    });
    const fetch = async url => {
        const parsed = new URL(url, "http://localhost");
        if (parsed.pathname === "/experiments/timeline") {
            plans++;
            const start = parsed.searchParams.get("start"), end = parsed.searchParams.get("end");
            const middle = new Date((Date.parse(start) + Date.parse(end)) / 2).toISOString();
            return response({start, end, timestamps: [start, middle, end], gate_order: order, step_seconds: 3600});
        }
        assert.equal(parsed.pathname, "/experiments/frame");
        active++;
        calls++;
        maximumActive = Math.max(maximumActive, active);
        return new Promise(resolve => pending.push({
            timestamp: parsed.searchParams.get("timestamp"),
            resolve(payload, status = 200) {active--; resolve(response(payload, status));}
        }));
    };
    const sandbox = {
        document: {getElementById: get, createElement: tag => new Element(tag), createElementNS: (_, tag) => new Element(tag)},
        window: {addEventListener() {}}, fetch, URLSearchParams, Intl, Date, AbortController,
        setTimeout: (fn, ms) => setTimeout(fn, ms === 250 || (ms >= 1000 && ms <= 1300) ? 5 : ms),
        clearTimeout, requestAnimationFrame: fn => {raf.push(fn);}, console
    };
    const flushRender = () => {while (raf.length) raf.shift()();};
    const waitFor = async predicate => {
        for (let i = 0; i < 100; i++) {
            if (predicate()) return;
            await new Promise(resolve => setTimeout(resolve, 5));
        }
        throw new Error("Browser controller did not reach expected state");
    };
    const fulfill = (request, body = "Chiron") => request.resolve({
        timestamp_utc: request.timestamp,
        positions: [{body, gate: 12, line: 3, longitude: 19.2}]
    });
    vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../app/static/gate-timeline.js"), "utf8"), sandbox);
    await waitFor(() => pending.length === 1);
    flushRender();
    assert.match(get("timeline-status").textContent, /Berechnung läuft/);
    const oldRequest = pending.shift();
    get("timeline-forward").fire("click");
    await waitFor(() => plans === 2);
    assert.equal(active, 1, "Viewport change must not start an overlapping frame fetch");
    assert.equal(calls, 1);
    fulfill(oldRequest, "Stale-body");
    await waitFor(() => pending.length === 1);
    const allLabels = () => get("timeline-bodies").children.map(label => label.children[0].attributes["aria-label"]);
    assert(!allLabels().includes("Stale-body"), "Stale responses may not mutate the legend");
    for (let i = 0; i < 3; i++) {
        await waitFor(() => pending.length === 1);
        fulfill(pending.shift());
    }
    await waitFor(() => active === 0 && allLabels().includes("Chiron"));
    await new Promise(resolve => setTimeout(resolve, 5));
    flushRender();
    assert.equal(maximumActive, 1);
    assert.match(get("timeline-status").textContent, /Berechnung vollständig: 3\/3/);
    assert.equal(get("timeline-progress").value, 3);
    get("timeline-chart").fire("keydown", {key: "ArrowDown"});
    assert.match(get("timeline-inspect").textContent, /Chiron: 12\.3/);
    details.open = true;
    details.fire("toggle");
    const tableRows = () => get("timeline-table").children[0].children[2].children;
    assert.equal(tableRows().length, 3, "Accessible table contains actual loaded timestamps");
    const beforeSelection = calls;
    get("timeline-none").fire("click");
    assert(get("timeline-bodies").children.every(label => !label.children[0].checked));
    flushRender();
    assert.equal(tableRows().length, 0, "Accessible table respects multi-selection");
    get("timeline-all").fire("click");
    assert(get("timeline-bodies").children.every(label => label.children[0].checked));
    flushRender();
    assert.equal(calls, beforeSelection, "Selection must not fetch any ephemeris");
    form.elements.start.value = "2026-01-01T00:00:00";
    form.elements.end.value = "2026-01-01T00:30:00";
    form.fire("submit");
    assert.match(form.elements.end.invalid, /Stunde/);
    assert.equal(plans, 2, "Invalid range is rejected before network activity");
    form.elements.end.value = "2026-01-02T00:00:00";
    form.fire("submit");
    await waitFor(() => pending.length === 1);
    const busy = pending.shift();
    busy.resolve({detail: "Berechnung belegt"}, 503);
    await waitFor(() => pending.length === 1);
    assert.equal(pending[0].timestamp, busy.timestamp, "Busy sample is retried at the same timestamp");
    pending.shift().resolve({detail: "Dienst offline"}, 502);
    await new Promise(resolve => setTimeout(resolve, 15));
    flushRender();
    assert.equal(active, 0);
    assert.equal(pending.length, 0, "Service failure stops the remaining queue");
    assert.match(get("timeline-status").textContent, /Dienst offline/);
    assert.equal(get("timeline-status").attributes.role, "alert");
    assert.equal(get("timeline-retry").hidden, false);
    assert.equal(maximumActive, 1);
    get("timeline-retry").fire("click");
    await waitFor(() => pending.length === 1);
    for (let i = 0; i < 3; i++) {
        await waitFor(() => pending.length === 1);
        fulfill(pending.shift());
    }
    await new Promise(resolve => setTimeout(resolve, 5));
    flushRender();
    assert.match(get("timeline-status").textContent, /Berechnung vollständig: 3\/3/);
    assert.equal(get("timeline-retry").hidden, true);
    console.log("Gate timeline browser controller: bounded fetches, stale results, selection, validation, busy retry and errors passed.");
}

async function overlayControllerTests() {
    const ids = new Map();
    const get = id => {
        if (!ids.has(id)) ids.set(id, new Element());
        return ids.get(id);
    };
    const form = get("timeline-range");
    form.elements = {start: new Element("input"), end: new Element("input"), samples: new Element("select")};
    form.elements.samples.value = "64";
    const details = new Element("details");
    details.append(get("timeline-table"));
    const natalDetails = new Element("details");
    natalDetails.append(get("timeline-natal-table"));
    const toggle = get("timeline-natal");
    toggle.dataset = {url: "/profiles/current/overlay", profileId: "p1"};
    const raf = [], overlays = [];
    const counts = {plan: 0, frame: 0, overlay: 0};
    const response = (payload, status = 200) => ({
        ok: status === 200, status, headers: {get: () => "application/json"}, json: async () => payload
    });
    const fetch = async url => {
        const parsed = new URL(url, "http://localhost");
        if (parsed.pathname === "/experiments/timeline") {
            counts.plan++;
            const start = parsed.searchParams.get("start"), end = parsed.searchParams.get("end");
            return response({start, end, timestamps: [start, end], gate_order: order, step_seconds: 3600});
        }
        if (parsed.pathname === "/experiments/frame") {
            counts.frame++;
            return response({timestamp_utc: parsed.searchParams.get("timestamp"),
                positions: [{body: "Sun", gate: 12, line: 3, longitude: 19.2}]});
        }
        assert.equal(parsed.pathname, "/profiles/current/overlay");
        counts.overlay++;
        return new Promise(resolve => overlays.push((payload, status = 200) => resolve(response(payload, status))));
    };
    const sandbox = {
        document: {getElementById: get, createElement: tag => new Element(tag), createElementNS: (_, tag) => new Element(tag)},
        window: {addEventListener() {}}, fetch, URLSearchParams, Intl, Date, AbortController,
        setTimeout: (fn, ms) => setTimeout(fn, ms === 250 ? 5 : ms),
        clearTimeout, requestAnimationFrame: fn => {raf.push(fn);}, console
    };
    const flushRender = () => {while (raf.length) raf.shift()();};
    const tick = () => new Promise(resolve => setTimeout(resolve, 15));
    const svgNodes = () => get("timeline-chart").children[0].children;
    const natalRects = () => svgNodes().filter(el => el.tag === "rect" && el.attributes["data-natal-source"]);
    vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../app/static/gate-timeline.js"), "utf8"), sandbox);
    await tick();
    flushRender();
    assert.equal(counts.overlay, 0, "Overlay is lazy: nothing is fetched on page load");
    assert.equal(natalRects().length, 0);
    const before = {...counts};
    toggle.checked = true;
    toggle.fire("change");
    assert.equal(counts.overlay, 1);
    assert.equal(toggle.attributes["aria-busy"], "true");
    assert.match(get("timeline-natal-status").textContent, /geladen/);
    // Unselecting before the response arrives must discard the stale result.
    toggle.checked = false;
    toggle.fire("change");
    overlays.shift()(overlay);
    await tick();
    flushRender();
    assert.equal(natalRects().length, 0, "Stale overlay response must not render");
    assert.match(get("timeline-natal-status").textContent, /Overlay aus/);
    toggle.checked = true;
    toggle.fire("change");
    assert.equal(counts.overlay, 2);
    overlays.shift()(overlay);
    await tick();
    flushRender();
    assert.equal(counts.plan, before.plan, "Overlay does not replan transit sampling");
    assert.equal(counts.frame, before.frame, "Overlay does not fetch transit frames");
    const sources = natalRects().map(el => `${el.attributes["data-natal-gate"]}:${el.attributes["data-natal-source"]}`).sort();
    assert.deepEqual(sources, ["34:design", "47:personality", "64:design", "64:personality"]);
    const rows = new Set(natalRects().map(el => el.attributes.width));
    assert.equal(rows.size, 1, "Bands span the full time range: no temporal movement");
    assert.match(get("timeline-natal-status").textContent, /Overlay aktiv: Anna · 3 Tore, 1 Kanäle/);
    assert.equal(get("timeline-natal-status").attributes.role, "status");
    assert.equal(get("timeline-natal-table").children[0].children[2].children.length, 4, "Accessible natal table");
    assert.match(get("timeline-natal-table").children[1].textContent, /64–47/);
    assert.match(get("timeline-chart").children[0].attributes["aria-label"], /Geburtsaktivierungen von Anna/);
    get("timeline-forward").fire("click");
    await tick();
    await tick();
    flushRender();
    assert.equal(counts.overlay, 2, "Navigation never recomputes the natal overlay");
    assert(counts.plan > before.plan);
    assert.equal(natalRects().length, 4, "Overlay persists across navigation");
    toggle.checked = false;
    toggle.fire("change");
    flushRender();
    assert.equal(natalRects().length, 0);
    toggle.checked = true;
    toggle.fire("change");
    flushRender();
    assert.equal(counts.overlay, 2, "Re-enabling reuses loaded data");
    assert.equal(natalRects().length, 4);
    console.log("Gate timeline overlay: lazy load, stale discard, bands, table and navigation independence passed.");
}

async function overlayErrorTests() {
    const ids = new Map();
    const get = id => {
        if (!ids.has(id)) ids.set(id, new Element());
        return ids.get(id);
    };
    const form = get("timeline-range");
    form.elements = {start: new Element("input"), end: new Element("input"), samples: new Element("select")};
    new Element("details").append(get("timeline-table"));
    const toggle = get("timeline-natal");
    toggle.dataset = {url: "/profiles/current/overlay", profileId: "p1"};
    const replies = [
        [{detail: "Der Berechnungsdienst ist nicht erreichbar."}, 502],
        [{...{profile: {id: "other", name: "Bert"}}, personality: [], design: [], channels: []}, 200],
        [{profile: null, detail: "Kein aktuelles Geburtsprofil ausgewählt."}, 200],
    ];
    const fetch = async url => {
        const parsed = new URL(url, "http://localhost");
        if (parsed.pathname !== "/profiles/current/overlay") return new Promise(() => {});
        const [payload, status] = replies.shift();
        return {ok: status === 200, status, headers: {get: () => "application/json"}, json: async () => payload};
    };
    const sandbox = {
        document: {getElementById: get, createElement: tag => new Element(tag), createElementNS: (_, tag) => new Element(tag)},
        window: {addEventListener() {}}, fetch, URLSearchParams, Intl, Date, AbortController,
        setTimeout, clearTimeout, requestAnimationFrame: () => {}, console
    };
    vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../app/static/gate-timeline.js"), "utf8"), sandbox);
    for (const expected of [/nicht erreichbar/, /inzwischen gewechselt/, /Kein aktuelles Geburtsprofil/]) {
        toggle.checked = true;
        toggle.fire("change");
        await new Promise(resolve => setTimeout(resolve, 10));
        assert.match(get("timeline-natal-status").textContent, expected);
        assert.equal(get("timeline-natal-status").attributes.role, "alert");
        assert.equal(toggle.checked, false, "Failed overlay is switched off so it can be retried");
        assert.equal(toggle.attributes["aria-busy"], "false");
    }
    console.log("Gate timeline overlay: API failure, changed profile and missing profile passed.");
}

browserControllerTests()
    .then(overlayControllerTests)
    .then(overlayErrorTests)
    .catch(error => {console.error(error); process.exitCode = 1;});
