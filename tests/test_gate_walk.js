"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");

class Element {
    constructor() {this.attributes = {}; this.listeners = {}; this.dataset = {};}
    setAttribute(name, value) {this.attributes[name] = String(value);}
    getAttribute(name) {return this.attributes[name];}
    addEventListener(name, fn) {this.listeners[name] = fn;}
    fire(name) {this.listeners[name]({target: this});}
}

const order = [41, 19, 13, 49];
const markers = order.map((gate, index) => {
    const group = new Element(), circle = new Element();
    circle.setAttribute("cx", index * 10);
    circle.setAttribute("cy", index * 10);
    group.querySelector = () => circle;
    return group;
});
const sectors = order.map(() => new Element());
const elements = new Map();
const get = id => {
    if (!elements.has(id)) elements.set(id, new Element());
    return elements.get(id);
};
const root = get("gate-walk");
root.dataset.order = JSON.stringify(order);
root.querySelector = selector => {
    const match = selector.match(/^\[data-(walk-gate|zodiac-gate)="(\d+)"\]$/);
    if (match) return (match[1] === "walk-gate" ? markers : sectors)[order.indexOf(Number(match[2]))];
    return get(selector);
};
get("walk-descriptions").textContent = JSON.stringify(Object.fromEntries(order.map(gate =>
    [gate, {name: "Name", description: "Beschreibung", channel: "Kanal"}])));
get("walk-speed").value = "1";
let callback, nextId = 0;
const sandbox = {
    document: {getElementById: get, addEventListener() {}},
    requestAnimationFrame: fn => {callback = fn; return ++nextId;},
    cancelAnimationFrame: () => {callback = null;},
    console
};
const source = fs.readFileSync(path.join(__dirname, "../app/static/gate-walk.js"), "utf8");
const patterns = [...source.split("const hexagrams = [")[1].split("];")[0].matchAll(/"([01]{6})"/g)]
    .map(match => match[1]);
assert.equal(patterns.length, 64);
assert.equal(new Set(patterns).size, 64);
assert.equal(patterns[0], "111111");
assert.equal(patterns[1], "000000");
assert.equal(patterns[62], "101010");
assert.equal(patterns[63], "010101");
vm.runInNewContext(source, sandbox);
const visited = () => markers.map(marker => marker.getAttribute("data-visited"));
const pattern = () => Array.from({length: 6}, (_, i) =>
    get(`walk-hex-line-${i + 1}`).getAttribute("data-solid") === "true" ? "1" : "0").join("");
const tick = time => {const fn = callback; callback = null; fn(time);};
assert.deepEqual(visited(), ["true", "false", "false", "false"]);
assert.equal(pattern(), "110001", "Gate 41 pattern runs bottom to top");
assert.match(get("walk-hexagram").attributes["aria-label"], /Hexagramm 41.*durchgezogen, durchgezogen, geteilt/);
get("walk-play").fire("click");
tick(0);
tick(1000);
assert.equal(pattern(), "110000", "Gate 19 is shown as soon as it is reached");
tick(2500);
assert.deepEqual(visited(), ["true", "true", "true", "false"], "Skipped gates are also marked");
assert.equal(pattern(), "101111", "Gate 13 is shown during the transition to the next gate");
get("walk-stop").fire("click");
assert.deepEqual(visited(), ["true", "true", "true", "false"], "Stop retains visited gates");
get("walk-play").fire("click");
tick(3000);
tick(5000);
assert.deepEqual(visited(), ["true", "true", "true", "true"], "All gates remain marked after a round");
get("walk-reset").fire("click");
assert.deepEqual(visited(), ["true", "false", "false", "false"], "Reset keeps only the starting gate marked");
assert.equal(pattern(), "110001", "Reset restores the starting hexagram");
console.log("Gate walk: visited markers, skipped steps, completed round and reset passed.");
