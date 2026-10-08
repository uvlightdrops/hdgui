"use strict";
const assert = require("node:assert/strict");
const {clampViewport, zoomViewport, panViewport, adjacentSegments, prioritized, MIN_TIME, MAX_TIME, MIN_SPAN} =
    require("../app/static/gate-timeline.js");

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
console.log("Gate timeline: viewport, bounds, cyclic paths and progressive sampling passed.");

// Exercise the actual browser controller with a minimal DOM, controlled fetches and no framework.
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
class Element {
    constructor(tag = "div") {
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
browserControllerTests().catch(error => {console.error(error); process.exitCode = 1;});
