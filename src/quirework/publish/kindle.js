// The in-page half of `quirework publish kindle`: run in a signed-in tab on
// the Amazon store that holds the Kindle library (amazon.com by default).
//
// It defines `window.qwKindle`, which speaks to two of the store's own
// pages from inside the tab: Send to Kindle (/sendtokindle), which uploads a
// file into the library, and Manage Your Content and Devices (/hz/mycd/),
// which lists the library and delivers or removes a document on a device.
// Both are the calls those pages make themselves, so the signed-in session
// is the only credential, and nothing here ever sees a password.
//
// Every identifier stays in the page. A document is named by its title and
// a device by its name and kind; the store's own identifiers (document ids,
// device serial numbers, account ids, tokens) are looked up here, used, and
// never returned, so a transcript of the session carries only what a person
// would read off the screen.
//
// There is deliberately no call that deletes a document from the library.
// That is permanent, and it takes the document's synced notes with it, so it
// is left to the person, on the store's own page.
(() => {
  "use strict";

  const VERSION = 1;
  const MYCD_PAGE = "/hz/mycd/digital-console/contentlist/pdocs/dateDsc/";
  const READING_FAMILIES = new Set(["KINDLE", "KINDLE_APP"]);
  const EPUB = "application/epub+zip";
  const INPUT_ID = "qw-kindle-file";

  const state = { mycdToken: null, docs: new Map(), devices: new Map() };

  class KindleError extends Error {}

  function fail(message) {
    throw new KindleError(`qwKindle: ${message}`);
  }

  // ------------------------------------------------------------------ tokens
  function stkToken() {
    const input = document.querySelector('input[name="csrfToken"]');
    const meta = document.querySelector('meta[name="anti-csrftoken-a2z"]');
    const token = (input && input.value) || (meta && meta.content);
    if (!token) fail("run this on the Send to Kindle page (/sendtokindle)");
    return token;
  }

  async function mycdToken() {
    if (location.pathname.startsWith("/hz/mycd/") && window.csrfToken) {
      return window.csrfToken;
    }
    if (state.mycdToken) return state.mycdToken;
    const page = await fetch(MYCD_PAGE, { credentials: "include" });
    const match = (await page.text()).match(/csrfToken\s*=\s*"([^"]+)"/);
    if (!match) {
      fail(`the content page gave no session token; sign in at ${MYCD_PAGE}`);
    }
    state.mycdToken = match[1];
    return state.mycdToken;
  }

  // ------------------------------------------------- Manage Your Content
  // The page speaks two generations of one endpoint: the listing calls go to
  // the newer one, and delivery and removal still go to the older.
  async function mycdPost(path, form) {
    const response = await fetch(path, {
      method: "POST",
      credentials: "include",
      headers: {
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        Accept: "application/json, text/javascript, */*; q=0.01",
      },
      body: new URLSearchParams(form).toString(),
    });
    const text = await response.text();
    let body;
    try {
      body = JSON.parse(text);
    } catch (_) {
      fail(
        `${path} answered ${response.status} with a page, not data; ` +
          `the session may need a fresh sign-in at ${MYCD_PAGE}`,
      );
    }
    if (!response.ok) fail(`${path} answered ${response.status}`);
    return body;
  }

  async function current(activity, input) {
    const body = await mycdPost("/hz/mycd/digital-console/ajax", {
      activity,
      activityInput: JSON.stringify(input),
      csrfToken: await mycdToken(),
      clientId: "MYCD_WebService",
    });
    const result = body[activity];
    if (!result) fail(`${activity} returned no result`);
    return result;
  }

  async function legacy(activity, input) {
    const body = await mycdPost("/hz/mycd/ajax", {
      data: JSON.stringify({ param: { [activity]: input } }),
      csrfToken: await mycdToken(),
      clientId: "MYCD_WebService",
    });
    const result = body[activity];
    if (!result) fail(`${activity} returned no result`);
    return result;
  }

  function unescape(text) {
    const area = document.createElement("textarea");
    area.innerHTML = text || "";
    return area.value;
  }

  function deviceKey(name, kind) {
    return `${name}\u0000${kind}`;
  }

  async function devices() {
    const result = await current("GetDevicesOverview", { surfaceType: "LargeDesktop" });
    state.devices.clear();
    const shown = [];
    for (const d of result.deviceList || []) {
      if (!READING_FAMILIES.has(d.deviceFamily)) continue;
      // The registration date arrives as a serialized date object.
      const millis = d.lastRegisteredDate && d.lastRegisteredDate.millis;
      const entry = {
        name: unescape(d.deviceName),
        kind: d.deviceTypeString,
        family: d.deviceFamily,
        registered: millis ? new Date(millis).toISOString() : null,
      };
      state.devices.set(deviceKey(entry.name, entry.kind), d);
      shown.push(entry);
    }
    return shown;
  }

  function holders(targets) {
    // A library entry names the devices holding it by their account ids.
    const out = [];
    for (const id of Object.keys(targets || {})) {
      const d = [...state.devices.values()].find((x) => x.deviceAccountID === id);
      out.push(d ? { name: unescape(d.deviceName), kind: d.deviceTypeString } : { name: "?", kind: "unlisted device" });
    }
    return out;
  }

  async function library() {
    if (!state.devices.size) await devices();
    state.docs.clear();
    const shown = [];
    for (let start = 0; ; start += 25) {
      const result = await current("GetContentOwnershipData", {
        contentType: "KindlePDoc",
        contentCategoryReference: "pdocs",
        itemStatusList: ["Active"],
        fetchCriteria: {
          sortOrder: "DESCENDING",
          sortIndex: "DATE",
          startIndex: start,
          batchSize: 25,
          totalContentCount: -1,
        },
        surfaceType: "LargeDesktop",
      });
      for (const item of result.items || []) {
        const title = unescape(item.title);
        if (state.docs.has(title)) {
          if (state.docs.get(title).asin === item.asin) continue;
          state.docs.get(title).duplicate = true;
          continue;
        }
        state.docs.set(title, item);
        shown.push({
          title,
          added: item.acquiredDate || null,
          devices: holders(item.targetDevices),
        });
      }
      if (!result.hasMoreItems) break;
    }
    for (const entry of shown) {
      if (state.docs.get(entry.title).duplicate) entry.duplicate = true;
    }
    return shown;
  }

  async function snapshot() {
    const shown = await devices();
    return { version: VERSION, devices: shown, library: await library() };
  }

  async function documentNamed(title) {
    if (!state.docs.has(title)) await library();
    const item = state.docs.get(title);
    if (!item) fail(`the library holds no document titled ${JSON.stringify(title)}`);
    if (item.duplicate) fail(`two documents are titled ${JSON.stringify(title)}`);
    return item;
  }

  async function devicesNamed(wanted) {
    if (!state.devices.size) await devices();
    if (!Array.isArray(wanted) || !wanted.length) fail("name at least one device");
    return wanted.map(({ name, kind }) => {
      const d = state.devices.get(deviceKey(name, kind));
      if (!d) fail(`no reading device named ${JSON.stringify(name)} (${kind})`);
      return d;
    });
  }

  // --------------------------------------------------------- Send to Kindle
  function fileInput() {
    let input = document.getElementById(INPUT_ID);
    if (!input) {
      input = document.createElement("input");
      input.type = "file";
      input.id = INPUT_ID;
      input.accept = ".epub";
      input.setAttribute("aria-label", "quirework kindle file");
      input.style.cssText = "position:fixed;bottom:0;left:0;opacity:0.01;z-index:2147483647";
      document.body.appendChild(input);
    }
    return { id: INPUT_ID, label: input.getAttribute("aria-label") };
  }

  async function sha256(blob) {
    const digest = await crypto.subtle.digest("SHA-256", await blob.arrayBuffer());
    return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
  }

  async function stkJson(method, path, body) {
    const response = await fetch(path, {
      method,
      credentials: "include",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
        "X-Requested-With": "XMLHttpRequest",
        "anti-csrftoken-a2z": stkToken(),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const text = await response.text();
    if (!response.ok) fail(`${path} answered ${response.status}`);
    try {
      return JSON.parse(text);
    } catch (_) {
      fail(`${path} answered with a page, not data; reload /sendtokindle and sign in`);
    }
  }

  async function send({ title, author, sha256: expected }) {
    if (!title) fail("give the title the library should show");
    const input = document.getElementById(INPUT_ID);
    const picked = input && input.files && input.files[0];
    if (!picked) fail(`load the file into #${INPUT_ID} first (qwKindle.fileInput())`);
    if (!/\.epub$/i.test(picked.name)) fail(`${picked.name} is not an EPUB`);
    const file = new File([picked], picked.name, { type: EPUB });
    const actual = await sha256(file);
    if (expected && actual !== expected) {
      fail(`the loaded file is not the copy: its SHA-256 is ${actual}, not ${expected}`);
    }
    const slot = await stkJson("POST", "/sendtokindle/init", {
      fileSize: file.size,
      contentType: EPUB,
      appVersion: "1.0",
      appName: "drag_drop_web",
      fileExtension: "epub",
    });
    if (!slot.uploadUrl) fail("the upload was given nowhere to go");
    const put = await fetch(slot.uploadUrl, {
      method: "PUT",
      headers: { "Content-Type": EPUB },
      body: file,
    });
    if (!put.ok) fail(`the upload answered ${put.status}`);
    const sent = await stkJson("POST", "/sendtokindle/send-v2", {
      extName: "drag_drop_web",
      inputFormat: "epub",
      extVersion: "1.0",
      stkToken: slot.stkToken,
      title,
      dataType: EPUB,
      stkGuid: "",
      author: author || "",
      // Into the library, which is what keeps a document's notes in sync
      // across devices; delivery to a device is a separate, later step.
      archive: true,
      fileSize: file.size,
      forceConvert: "false",
      inputFileName: file.name,
      batchId: crypto.randomUUID(),
    });
    if (!sent || sent.status !== true) fail("Send to Kindle did not accept the file");
    return { title, bytes: file.size, verified: Boolean(expected) };
  }

  // The list of recently sent files names a file by its title, with the
  // characters a file name cannot carry swapped for look-alikes (a title's
  // parentheses come back as U+FE59 and U+FE5A) and the extension appended.
  // Compatibility normalization folds the look-alikes back.
  function comparable(text) {
    return unescape(String(text || ""))
      .normalize("NFKC")
      .replace(/\.[A-Za-z0-9]{2,5}$/, "")
      .replace(/\s+/g, " ")
      .trim();
  }

  async function status(title) {
    const rows = await stkJson("GET", "/sendtokindle/recent-docs");
    if (!Array.isArray(rows)) fail("the list of recently sent files is not a list");
    const row = rows
      .filter((r) => comparable(r.filename) === comparable(title))
      .sort((a, b) => Number(b.uploadDate) - Number(a.uploadDate))[0];
    if (!row) return { title, status: "NOT_LISTED" };
    // IN_PROGRESS while Amazon converts the file; IN_LIBRARY once it is in
    // the library, which takes a few minutes for a book.
    return {
      title,
      status: row.status,
      failureCode: row.failureCode,
      errorCode: row.errorCode,
      minutes: Math.round((Date.now() - Number(row.uploadDate)) / 6000) / 10,
    };
  }

  // ------------------------------------------------- delivery and removal
  // Both are the calls the page's own "Deliver to device" dialog makes. It
  // names a device two ways in one request, by its account id and by its
  // serial number, and the answer reports success per account id.
  function category(item) {
    if (item.category === "KindleEBook" && item.originType === "Sample") return "KindleEBookSample";
    return item.category;
  }

  async function deliver({ title, devices: wanted }) {
    const item = await documentNamed(title);
    const chosen = await devicesNamed(wanted);
    const asin = item.currentEditionAsin || item.asin;
    const result = await legacy("DeliverToKindle", {
      asinDetails: {
        [asin]: {
          category: category(item),
          title: item.title,
          parentAsin: item.parentAsin,
          originType: item.originType,
          eligibleDeviceList: chosen.map((d) => d.deviceAccountID),
        },
      },
      dsnList: chosen.map((d) => d.deviceSerialNumber),
    });
    const per = (result.resultMap || {})[asin] || {};
    const failed = wanted.filter((_, i) => !(per[chosen[i].deviceAccountID] || {}).success);
    if (failed.length) fail(`delivery failed for ${failed.map((d) => d.name).join(", ")}`);
    return { title, delivered: wanted };
  }

  async function removeFrom({ title, devices: wanted }) {
    // Takes the document off the named devices and leaves it in the library,
    // where its notes stay; the store's own dialog does the same.
    const item = await documentNamed(title);
    const chosen = await devicesNamed(wanted);
    const failed = [];
    for (const [i, d] of chosen.entries()) {
      const result = await legacy("DeleteContentFromDevice", {
        asinDetails: {
          asin: item.currentEditionAsin || item.asin,
          parentAsin: item.parentAsin,
          contentType: category(item),
        },
        targetDeviceId: d.deviceAccountID,
      });
      if (!result.success) failed.push(wanted[i].name);
    }
    if (failed.length) fail(`removal failed for ${failed.join(", ")}`);
    return { title, removedFrom: wanted };
  }

  window.qwKindle = {
    version: VERSION,
    snapshot,
    devices,
    library,
    fileInput,
    send,
    status,
    deliver,
    removeFrom,
  };
  return `qwKindle ${VERSION} ready on ${location.host}`;
})();
