const fs = require('fs');
const vm = require('vm');

class Node {
  constructor(tag) {
    this.tagName = tag;
    this.children = [];
    this.parentNode = null;
    this.style = {};
    this.attributes = {};
    this.listeners = {};
    this.dataset = {};
    this._text = '';
    this._classes = new Set();
    this.classList = {
      add: (name) => this._classes.add(name),
      contains: (name) => this._classes.has(name),
      toggle: (name, force) => {
        const on = force === undefined ? !this._classes.has(name) : force;
        if (on) this._classes.add(name); else this._classes.delete(name);
        return on;
      }
    };
  }
  set className(value) { this._classes = new Set(String(value).split(/\s+/).filter(Boolean)); }
  get className() { return Array.from(this._classes).join(' '); }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text; }
  appendChild(child) { child.parentNode = this; this.children.push(child); return child; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  addEventListener(name, fn) { (this.listeners[name] ||= []).push(fn); }
  querySelectorAll(selector) {
    const wanted = selector.split(',').map((s) => s.trim().slice(1));
    const out = [];
    const walk = (node) => node.children.forEach((child) => {
      if (wanted.some((name) => child.classList.contains(name))) out.push(child);
      walk(child);
    });
    walk(this);
    return out;
  }
  dispatch(name, event = {}) {
    const e = Object.assign({ target: this, relatedTarget: null, pointerType: 'mouse' }, event);
    (this.listeners[name] || []).forEach((fn) => fn(e));
  }
}

const nodes = new Map();
const markers = [];
const requests = [];
const docListeners = {};
function getElement(id) { if (!nodes.has(id)) nodes.set(id, new Node('div')); return nodes.get(id); }
const grid = getElement('grid');
const map = { center: [120, 30], zoom: 7, getSource() { return memberSource; }, isStyleLoaded() { return false; }, getBounds() { return { getWest: () => 119, getEast: () => 122, getSouth: () => 29, getNorth: () => 32 }; } };
const context = {
  URLSearchParams, Promise, Math, JSON, Error, Array, String, Number,
  document: {
    readyState: 'loading', addEventListener() {}, getElementById: getElement,
    createElement(tag) { return new Node(tag); }, querySelectorAll() { return []; },
    addEventListener(name, fn) { (docListeners[name] ||= []).push(fn); }
  },
  window: { maplibregl: { Marker: class {
    constructor(options) { this.element = options.element; this.lngLat = null; markers.push(this); }
    setLngLat(value) { this.lngLat = value.slice(); return this; }
    addTo(target) { this.map = target; return this; }
  } } },
  fetch(url) { requests.push(url); return Promise.reject(new Error('unexpected fetch')); },
  setTimeout, clearTimeout, location: { href: '', search: '' },
  globalThis: null
};
context.globalThis = context;
vm.createContext(context);
let source = fs.readFileSync('src/photo_search/web/map.js', 'utf8');
source = source.slice(0, source.lastIndexOf('})();')) +
  ';globalThis.hoverReview={bindPhotoHover,drawGrid,loadClusterLeaves,clearPhotoHover,openAsset,handleMapError,setMap(m){map=m;},state(){return {mouseHoverOwner,focusHoverOwner,hoverMarker,hoverMarkerElement,keyboardFocusMode};},setClusterPaging(value){clusterPaging=value;},setSequence(id,v){sequence=id;version=v;}};' + source.slice(source.lastIndexOf('})();'));
vm.runInContext(source, context);
const review = context.hoverReview;
review.setMap(map);
review.bindPhotoHover();
review.bindPhotoHover();
review.drawGrid({ assets: [
  { asset_id: 1, longitude: 0, latitude: 0 },
  { asset_id: 2, longitude: -73.5, latitude: 40.7 },
  { asset_id: 3, longitude: null, latitude: 12 }
], next_cursor: 'next' }, true);
const [a, b, invalid] = grid.children;
const aButton = a.children[0];
const state = () => review.state();
const result = {};
result.listenerCounts = { pointerover: grid.listeners.pointerover.length, keydown: docListeners.keydown.length };

grid.dispatch('pointerover', { target: a, pointerType: 'mouse' });
const marker = state().hoverMarker;
result.mouseA = { coords: marker && marker.lngLat, visible: state().hoverMarkerElement.style.display };
aButton.dispatch('pointerover', { relatedTarget: a, pointerType: 'mouse' });
result.sameCardStable = state().hoverMarker === marker;
grid.dispatch('pointerover', { target: b, relatedTarget: a, pointerType: 'mouse' });
grid.dispatch('pointerout', { target: a, relatedTarget: b, pointerType: 'mouse' });
result.mouseB = { coords: marker && marker.lngLat, visible: state().hoverMarkerElement.style.display };
grid.dispatch('pointerout', { target: b, relatedTarget: null, pointerType: 'mouse' });
result.mouseLeft = state().hoverMarkerElement.style.display;
grid.dispatch('pointerover', { target: invalid, pointerType: 'mouse' });
result.invalidGps = state().hoverMarkerElement.style.display;
grid.dispatch('pointerout', { target: invalid, relatedTarget: null, pointerType: 'mouse' });
grid.dispatch('pointerover', { target: a, pointerType: 'mouse' });
docListeners.pointerdown[0]({ pointerType: 'touch' });
result.mouseAfterTouch = state().hoverMarkerElement.style.display;

docListeners.keydown[0]({ key: 'Tab' });
grid.dispatch('focusin', { target: aButton });
const focusMarker = state().hoverMarker;
grid.dispatch('focusout', { target: aButton, relatedTarget: a.children[1] });
result.focusWithinCard = state().hoverMarker === focusMarker && state().hoverMarkerElement.style.display === 'block';
grid.dispatch('pointerover', { target: b, pointerType: 'mouse' });
result.mousePriority = marker.lngLat;
grid.dispatch('pointerout', { target: b, pointerType: 'mouse' });
result.focusRestored = marker.lngLat;
docListeners.pointerdown[0]({ pointerType: 'touch' });
result.touchCleared = state().hoverMarkerElement.style.display;

grid.dispatch('pointerover', { target: a, pointerType: 'mouse' });
review.openAsset(1);
result.viewerCleared = state().hoverMarkerElement.style.display;

review.drawGrid({ assets: [{ asset_id: 4, longitude: 100, latitude: -20 }], next_cursor: null }, true);
const memberSource = { getClusterLeaves() { return Promise.resolve([
  { properties: { asset_id: 5 }, geometry: { coordinates: [121.5, 31.2] } },
  { properties: { asset_id: 6 }, geometry: { coordinates: [121.5, 31.2] } }
]); } };
review.setClusterPaging({ id: 8, offset: 0, seq: 2, version: 3 });
review.setSequence(2, 3);
const opened = [];
context.window.AIPhotoInfo = { open(id) { opened.push(id); } };
review.loadClusterLeaves();
Promise.resolve().then(() => Promise.resolve()).then(() => {
  const member = grid.children[0], sameCoordinateMember = grid.children[1];
  grid.dispatch('pointerover', { target: member, pointerType: 'mouse' });
  const firstCoordinates = marker.lngLat.slice();
  member.onclick();
  grid.dispatch('pointerover', { target: sameCoordinateMember, pointerType: 'mouse' });
  result.cluster = { coords: marker.lngLat, firstCoordinates, visible: marker.element.style.display, memberCount: grid.children.length };
  sameCoordinateMember.onclick();
  result.clusterOpened = opened;
  result.singleMarker = markers.length;
  result.hoverRequests = requests.length;
  result.mapUnchanged = { center: map.center, zoom: map.zoom };
  grid.dispatch('pointerover', { target: member, pointerType: 'mouse' });
  review.handleMapError({ sourceId: 'photos' });
  result.mapErrorCleared = state().hoverMarkerElement.style.display;
  review.clearPhotoHover();
  process.stdout.write(JSON.stringify(result));
});
