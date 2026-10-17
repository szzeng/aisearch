// Minimal browser DOM semantics needed to run the real shared photo viewer in
// the person-page probes. HTTP responses remain specific to each scenario.
function install(context, Element) {
  const document = context.document;
  function enhance(element) {
    element.dataset ||= {}; element.style ||= {}; element.attributes ||= {};
    element.classList = { add() {}, remove() {}, contains() { return false; }, toggle() {} };
    return element;
  }
  const create = document.createElement, get = document.getElementById;
  document.createElement = tag => enhance(create(tag));
  document.getElementById = id => enhance(get(id));
  document.body = document.createElement("body"); document.head = document.createElement("head");
  document.querySelector = selector => selector === "dialog[open]"
    ? document.body.children.find(child => child.tag === "dialog" && child.open) || null : null;
  context.URL ||= URL;
  context.window.location.href ||= "http://local/persons";
  context.navigator ||= { clipboard: { writeText: async () => {} } };
  Object.defineProperty(Element.prototype, "textContent", {
    configurable: true,
    get() { return (this.text || "") + this.children.map(child => child.textContent || "").join(""); },
    set(value) { this.text = String(value); this.children.forEach(child => { child.parentNode = null; }); this.children = []; },
  });
  Object.defineProperty(Element.prototype, "isConnected", {
    get() { let root = this; while (root.parentNode) root = root.parentNode; return root === document.body || root === document.head; },
  });
  Element.prototype.append = function (...children) {
    children.forEach(child => { if (child.parentNode) child.remove(); child.parentNode = this; this.children.push(child); });
  };
  Element.prototype.replaceChildren = function (...children) {
    this.text = ""; this.children.forEach(child => { child.parentNode = null; }); this.children = []; this.append(...children);
  };
  Element.prototype.remove = function () {
    if (this.parentNode) this.parentNode.children = this.parentNode.children.filter(child => child !== this);
    this.parentNode = null;
  };
  Element.prototype.setAttribute = function (key, value) { (this.attributes ||= {})[key] = String(value); };
  Element.prototype.addEventListener = function (event, callback) { (this.events[event] ||= []).push(callback); };
  Element.prototype.fire = function (event, extra = {}) {
    let result;
    for (const listener of this.events[event] || []) result = listener({ target: this, currentTarget: this, preventDefault() {}, stopPropagation() {}, ...extra });
    return result;
  };
  Element.prototype.getBoundingClientRect = function () { return { left: 0, top: 0, width: 800, height: 600 }; };
}
function all(root, predicate) {
  const result = [];
  function walk(node) { if (predicate(node)) result.push(node); (node.children || []).forEach(walk); }
  walk(root); return result;
}
module.exports = { install, all };
