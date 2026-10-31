// Runs the inline script of voice/web/index.html against a stub DOM and a stub fetch, clicks
// "Start call" and prints the resulting state as JSON. No browser, no LiveKit: it checks the
// page's logic only. Usage: node web_page_harness.js <index.html> <token status> <token body JSON>
const fs = require("fs");
const vm = require("vm");

const [, , pagePath, statusArg, bodyArg] = process.argv;
const html = fs.readFileSync(pagePath, "utf8");
const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)];
const source = scripts[scripts.length - 1][1];

function element() {
  const el = {
    textContent: "",
    disabled: false,
    hidden: false,
    className: "",
    listeners: {},
    addEventListener(name, fn) { this.listeners[name] = fn; },
    appendChild() {},
    remove() {},
  };
  // Elements start hidden iff the markup says so; mirror `hidden` from the HTML below.
  return el;
}

const elements = {};
const document = {
  getElementById(id) {
    if (!elements[id]) {
      elements[id] = element();
      const tag = new RegExp(`<[^>]*id="${id}"[^>]*>`).exec(html);
      elements[id].hidden = !!tag && /\bhidden\b/.test(tag[0]);
    }
    return elements[id];
  },
  createElement: element,
  body: { appendChild() {} },
};

let fetches = 0;
const fetch = async () => {
  fetches += 1;
  return {
    ok: Number(statusArg) >= 200 && Number(statusArg) < 300,
    status: Number(statusArg),
    json: async () => JSON.parse(bodyArg),
  };
};

class Room { on() {} }
const LivekitClient = { Room, RoomEvent: {}, Track: { Kind: {} } };

const context = { document, fetch, LivekitClient, console };
context.window = context;
vm.createContext(context);
vm.runInContext(source, context);

(async () => {
  const button = document.getElementById("toggle");
  await button.listeners.click();
  const fetchedAfterFirstClick = fetches;
  // A browser does not fire click on a disabled button, so neither does the harness.
  if (!button.disabled) await button.listeners.click();
  const out = {
    buttonDisabled: button.disabled,
    buttonText: button.textContent,
    noticeText: document.getElementById("notice").textContent,
    noticeHidden: document.getElementById("notice").hidden,
    statusText: document.getElementById("status").textContent,
    clickAfterwardsFetched: fetches,
    fetchedAfterFirstClick,
  };
  process.stdout.write(JSON.stringify(out));
})();
