/* Shared verbatim by model and tool services. Public requests never use admin authentication. */
(() => {
  "use strict";
  const UPLOAD_LIMIT = 10 * 1024 * 1024, TEXT_LIMIT = 256 * 1024, MEDIA_LIMIT = 32 * 1024 * 1024;
  const node = (tag, properties = {}, ...children) => {
    const element = Object.assign(document.createElement(tag), properties);
    element.append(...children);
    return element;
  };
  const fieldId = key => "pg-" + key.replace(/[^a-zA-Z0-9_-]/g, "-");
  const quote = value => "'" + String(value).replace(/'/g, "'\\''") + "'";
  const yieldTurn = () => new Promise(resolve => setTimeout(resolve, 0));
  function put(target, key, value) {
    const parts = key.split(".");
    if (parts.some(part => ["__proto__", "prototype", "constructor"].includes(part))) throw new Error("Invalid field path.");
    let cursor = target;
    parts.forEach((part, index) => {
      if (index === parts.length - 1) cursor[part] = value;
      else cursor = cursor[part] ??= /^\d+$/.test(parts[index + 1]) ? [] : {};
    });
  }

  class Playground {
    static mount(container, descriptor, options = {}) { return new Playground(container, descriptor, options); }

    constructor(container, descriptor, options) {
      this.container = container; this.descriptor = descriptor; this.options = options;
      this.controller = null; this.mediaUrl = null; this.requestUrls = []; this.version = 0;
      if (!document.getElementById("playground-style")) {
        document.head.append(node("style", {id: "playground-style", textContent: `
          #playground .pg-field { margin-top: 14px; }
          #playground input:not([type=checkbox]), #playground textarea, #playground select { width: 100%; font: inherit; color: inherit; background: var(--bg); border: 1px solid var(--border); border-radius: 8px; padding: 9px 12px; }
          #playground textarea { min-height: 108px; resize: vertical; }
          #playground select { min-height: 44px; }
          #playground .pg-actions { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 16px; }
          #playground pre { white-space: pre-wrap; overflow-wrap: anywhere; max-height: 360px; overflow: auto; font: .85em/1.6 ui-monospace, Consolas, monospace; }
          #playground audio, #playground img { display: block; max-width: 100%; margin: 12px 0; }
          #playground img { max-height: 512px; object-fit: contain; }
          #playground a { color: var(--accent); text-underline-offset: 3px; }
          #playground input { caret-color: var(--accent); }
          #pg-status { min-height: 1.5em; margin-top: 12px; font-variant-numeric: tabular-nums; }
          #pg-status[data-error=true] { color: var(--status-failed, var(--error)); }
        `}));
      }
      container.id = "playground"; container.classList.add("card");
      container.setAttribute("aria-labelledby", "pg-title");
      this.token = node("input", {id: "pg-token", type: "password", autocomplete: "off", spellcheck: false, maxLength: 500});
      this.operation = node("select", {id: "pg-operation"});
      descriptor.operations.forEach((operation, index) => this.operation.append(node("option", {value: String(index), textContent: operation.label})));
      this.fields = node("div", {id: "pg-fields"});
      this.send = node("button", {id: "pg-send", className: "primary", type: "submit", textContent: "Send request"});
      this.cancel = node("button", {id: "pg-cancel", type: "button", textContent: "Cancel", disabled: true});
      const reset = node("button", {id: "pg-reset", type: "button", textContent: "Reset"});
      this.cancel.onclick = () => this.controller?.abort();
      reset.onclick = () => { this.reset(); this.renderFields(); };
      this.status = node("p", {id: "pg-status", role: "status"}); this.status.setAttribute("aria-live", "polite");
      this.output = node("pre", {id: "pg-output", tabIndex: 0});
      this.media = node("div", {id: "pg-media"});
      this.curl = node("pre", {id: "pg-curl", tabIndex: 0});
      this.requestDownloads = node("div", {id: "pg-request-downloads"});
      const copy = node("button", {id: "pg-copy", type: "button", textContent: "Copy curl"});
      copy.onclick = async () => {
        try { await navigator.clipboard.writeText(this.curl.textContent); this.status.textContent = "Curl copied. Token remains a placeholder."; }
        catch { this.status.textContent = "Select the curl text below and copy it."; }
      };
      const form = node("form", {id: "pg-form"},
        this.field("Service token", this.token),
        node("p", {className: "description", textContent: "Paste a public service token. Kept only in memory; cleared on reset or sign-out. Requests use the normal quotas, scope and cache. Leave blank only for an unauthenticated local service."}),
        this.field("Operation", this.operation), this.fields,
        node("div", {className: "pg-actions"}, this.send, this.cancel, reset));
      form.onsubmit = event => { event.preventDefault(); this.run(); };
      this.operation.onchange = () => { this.stop(); this.clearResult(); this.renderFields(); };
      container.replaceChildren(node("h2", {id: "pg-title", textContent: "Try it"}), form, this.status, this.output, this.media,
        node("details", {}, node("summary", {textContent: "Equivalent curl"}), this.curl, this.requestDownloads, copy));
      this.renderFields();
      window.addEventListener("pagehide", () => this.reset());
    }

    field(label, input) { return node("div", {className: "pg-field"}, node("label", {htmlFor: input.id, textContent: label}), input); }
    current() { return this.descriptor.operations[Number(this.operation.value)]; }
    redact(value) {
      let text = String(value);
      const variants = [this.token.value, ...(this.options.secrets?.() || [])].filter(Boolean).flatMap(secret => [
        secret, JSON.stringify(secret).slice(1, -1), encodeURIComponent(secret), quote(secret).slice(1, -1)
      ]).sort((a, b) => b.length - a.length);
      for (const secret of variants) text = text.split(secret).join("[REDACTED]");
      return text;
    }
    clearResult() {
      if (this.mediaUrl) URL.revokeObjectURL(this.mediaUrl);
      this.requestUrls.forEach(url => URL.revokeObjectURL(url)); this.requestUrls = [];
      this.requestDownloads.replaceChildren();
      this.mediaUrl = null; this.media.replaceChildren(); this.output.textContent = ""; this.curl.textContent = "";
      this.status.textContent = ""; this.status.dataset.error = "false";
    }
    stop() {
      this.version++; this.controller?.abort(); this.controller = null;
      this.send.disabled = false; this.cancel.disabled = true; this.operation.disabled = false;
      this.token.readOnly = false;
      this.send.removeAttribute("aria-busy");
    }
    reset() { this.stop(); this.token.value = ""; this.clearResult(); }
    renderFields() {
      const operation = this.current();
      this.fields.replaceChildren();
      for (const field of operation.fields || []) {
        const type = field.type || "text";
        const input = node(type === "select" ? "select" : ["json", "textarea"].includes(type) ? "textarea" : "input", {id: fieldId(field.key)});
        if (input.tagName === "INPUT") input.type = type;
        input.required = !!field.required;
        if (type === "select") {
          for (const option of field.options || [""]) input.append(node("option", typeof option === "object" ? {value: String(option.value), textContent: option.label} : {value: String(option), textContent: String(option) || "Service default"}));
        }
        if (type === "checkbox") input.checked = !!field.default;
        else if (type !== "file") input.value = field.default == null ? "" : typeof field.default === "object" ? JSON.stringify(field.default, null, 2) : String(field.default);
        for (const attribute of ["min", "max", "step", "accept"]) if (field[attribute] != null) input[attribute] = field[attribute];
        if (type === "textarea" || type === "text" || type === "json") input.maxLength = field.maxLength || TEXT_LIMIT;
        if (type === "json") { input.spellcheck = false; input.setAttribute("aria-describedby", input.id + "-help"); }
        const box = this.field(field.label, input);
        if (field.help || type === "json") box.append(node("small", {id: input.id + "-help", className: "description", textContent: field.help || "Valid JSON. Leave optional fields empty to use the service default."}));
        this.fields.append(box);
      }
      if (operation.lookup) {
        const load = node("button", {id: "pg-load", type: "button", textContent: operation.lookup.label || "Load choices"});
        load.onclick = () => this.run(true);
        this.fields.append(node("div", {className: "pg-actions"}, load));
      }
      if (operation.help) this.fields.append(node("p", {className: "description", textContent: operation.help}));
    }

    url(route) {
      if (!route.startsWith("/") || route.startsWith("//")) throw new Error("Only this service's public routes are allowed.");
      const url = new URL(route, location.origin);
      if (url.origin !== location.origin) throw new Error("Request must stay on this service.");
      return url;
    }
    async dataUrl(file, signal) {
      return await new Promise((resolve, reject) => {
        const reader = new FileReader();
        const abort = () => reader.abort();
        signal.addEventListener("abort", abort, {once: true});
        reader.onload = () => resolve(reader.result);
        reader.onerror = () => reject(new Error("Could not read the image."));
        reader.onabort = () => reject(new DOMException("Cancelled", "AbortError"));
        reader.onloadend = () => signal.removeEventListener("abort", abort);
        if (signal.aborted) reject(new DOMException("Cancelled", "AbortError"));
        else reader.readAsDataURL(file);
      });
    }
    async pcm(file, signal) {
      if (/\.(pcm|raw)$/i.test(file.name)) {
        if (!file.size || file.size % 2 || file.size > 16000 * 2 * 150) throw new Error("PCM must be 16 kHz mono s16le, nonempty and at most 150 seconds.");
        return file;
      }
      const context = new AudioContext();
      try {
        const decoded = await context.decodeAudioData(await file.arrayBuffer());
        signal.throwIfAborted();
        if (!decoded.length || decoded.duration > 150) throw new Error("Audio must be at most 150 seconds. Choose a shorter recording.");
        const renderer = new OfflineAudioContext(1, Math.ceil(decoded.duration * 16000), 16000);
        const source = renderer.createBufferSource(); source.buffer = decoded; source.connect(renderer.destination); source.start();
        const mono = (await renderer.startRendering()).getChannelData(0);
        signal.throwIfAborted();
        const buffer = new ArrayBuffer(mono.length * 2), view = new DataView(buffer);
        for (let offset = 0; offset < mono.length; offset += 32768) {
          signal.throwIfAborted();
          for (let index = offset; index < Math.min(offset + 32768, mono.length); index++) {
            const sample = Math.max(-1, Math.min(1, mono[index]));
            view.setInt16(index * 2, Math.round(sample * (sample < 0 ? 32768 : 32767)), true);
          }
          await yieldTurn();
        }
        return new Blob([buffer], {type: "application/octet-stream"});
      } finally { await context.close(); }
    }

    async build(operation, signal) {
      const url = this.url(operation.route), headers = {}, body = structuredClone(operation.body || {});
      if (this.token.value) headers["X-Internal-Token"] = this.token.value;
      let upload;
      for (const field of operation.fields || []) {
        const input = document.getElementById(fieldId(field.key));
        let value = field.type === "checkbox" ? input.checked : field.type === "file" ? input.files[0] : input.value;
        if ((value === "" || value == null) && !field.required && field.type !== "checkbox") continue;
        if (field.type === "file") {
          if (!value || !value.size) throw new Error("Choose a nonempty file.");
          if (value.size > UPLOAD_LIMIT) throw new Error("Upload exceeds the 10 MiB playground limit.");
          if (field.transform === "dataUrl") value = await this.dataUrl(value, signal);
          else { upload = value; continue; }
        } else if (field.type === "json") {
          try { value = JSON.parse(value); } catch { throw new Error(field.label + " must contain valid JSON."); }
        } else if (field.type === "number") {
          value = Number(value); if (!Number.isFinite(value)) throw new Error(field.label + " must be a number.");
        }
        if (field.location === "query") url.searchParams.set(field.key, String(value));
        else if (field.location === "header") headers[field.key] = String(value);
        else put(body, field.key, value);
      }
      let payload;
      if (["raw", "pcm"].includes(operation.encoding)) {
        if (!upload) throw new Error("Choose a file to upload.");
        payload = operation.encoding === "pcm" ? await this.pcm(upload, signal) : upload;
        headers["Content-Type"] = operation.encoding === "pcm" ? "application/octet-stream" : upload.type || "application/octet-stream";
        if (operation.encoding === "raw") headers["X-Filename"] = encodeURIComponent(upload.name);
      } else if ((operation.method || "POST") !== "GET") {
        payload = JSON.stringify(body); headers["Content-Type"] = "application/json";
      }
      return {url, headers, method: operation.method || "POST", body: payload, binary: !!upload, pcm: operation.encoding === "pcm"};
    }
    curlFor(request) {
      // Redact after constructing the full command, including user text and query options.
      const parts = ["curl -X " + request.method, quote(request.url.href)];
      for (const [key, value] of Object.entries(request.headers)) parts.push("-H " + quote(key + ": " + (key.toLowerCase() === "x-internal-token" ? "<SERVICE_TOKEN>" : this.redact(value))));
      if (!request.headers["X-Internal-Token"]) parts.push("-H " + quote("X-Internal-Token: <SERVICE_TOKEN>"));
      if (request.body !== undefined) {
        if (request.binary) parts.push("--data-binary " + quote(request.pcm ? "@input.pcm" : "@input-file"));
        else if (request.body.length > TEXT_LIMIT / 4) {
          const filename = "request-" + (this.requestUrls.length + 1) + ".json";
          const url = URL.createObjectURL(new Blob([this.redact(request.body)], {type: "application/json"}));
          this.requestUrls.push(url);
          this.requestDownloads.append(node("p", {}, node("a", {href: url, download: filename, textContent: "Download " + filename + " for this curl command"})));
          parts.push("--data-binary " + quote("@" + filename));
        } else parts.push("--data-raw " + quote(this.redact(request.body)));
      }
      return this.redact(parts.join(" \\\n  ")) + (request.pcm ? "\n# input.pcm: 16 kHz, mono, signed 16-bit little-endian PCM" : "");
    }

    async read(response, type, signal, version) {
      const binary = response.ok && ["audio", "image"].includes(type);
      const limit = binary ? MEDIA_LIMIT : TEXT_LIMIT;
      if (Number(response.headers.get("Content-Length")) > limit) { await response.body?.cancel(); throw new Error("Response exceeds the playground " + (binary ? "32 MiB media" : "256 KiB text") + " limit."); }
      if (!response.body) return "";
      const reader = response.body.getReader(), decoder = new TextDecoder(), chunks = [];
      let bytes = 0, text = "", lastRender = 0;
      const render = final => {
        if (version !== this.version) return;
        const secrets = [this.token.value, ...(this.options.secrets?.() || [])].filter(Boolean);
        const tail = Math.max(0, ...secrets.map(secret => Math.max(secret.length, JSON.stringify(secret).length, encodeURIComponent(secret).length)));
        const safeText = this.redact(text);
        // Redact before clipping: clipping raw text could expose part of a complete credential.
        this.output.textContent = final ? safeText : safeText.slice(0, Math.max(0, safeText.length - tail));
      };
      try {
        while (true) {
          signal.throwIfAborted();
          const {value, done} = await reader.read();
          if (done) break;
          bytes += value.byteLength;
          if (bytes > limit) { await reader.cancel(); throw new Error("Response exceeded the playground output limit; transfer stopped."); }
          if (binary) chunks.push(value);
          else {
            text += decoder.decode(value, {stream: true});
            if (type === "stream" && performance.now() - lastRender > 40) { render(false); lastRender = performance.now(); await yieldTurn(); }
          }
        }
        signal.throwIfAborted();
        if (binary) return new Blob(chunks, {type: response.headers.get("Content-Type") || (type === "audio" ? "audio/wav" : "image/png")});
        text += decoder.decode();
        const original = text; // Follow-up operations parse the full bounded payload, not clipped display text.
        if (type === "json" || !response.ok) {
          try { text = JSON.stringify(JSON.parse(text), null, 2); } catch { /* Keep non-JSON error bodies readable. */ }
        }
        text = this.redact(text).slice(0, TEXT_LIMIT);
        render(true);
        return original;
      } finally {
        await reader.cancel().catch(() => {}); reader.releaseLock();
      }
    }

    async request(request, type, signal, version, started) {
      // No redirects: public credentials must never leave this origin.
      for (const value of request.url.searchParams.values()) {
        if ([this.token.value, ...(this.options.secrets?.() || [])].filter(secret => secret && secret.length >= 8).some(secret => value.includes(secret))) {
          throw new Error("Credentials must not appear in request query values.");
        }
      }
      this.curl.textContent += (this.curl.textContent ? "\n\n" : "") + this.curlFor(request);
      const response = await fetch(request.url, {method: request.method, headers: request.headers, body: request.body, signal, redirect: "error", cache: "no-store", credentials: "omit"});
      signal.throwIfAborted();
      this.codes.push(response.status);
      const status = "HTTP " + this.codes.join(" → HTTP ") + " · " + Math.round(performance.now() - started) + " ms";
      const retry = response.headers.get("Retry-After");
      this.lastStatus = status + (retry ? " · Retry-After: " + retry : "");
      if (version === this.version) this.status.textContent = this.redact(this.lastStatus + " · Receiving…");
      const displayType = type === "stream" && /application\/json(?:;|$)/i.test(response.headers.get("Content-Type") || "") ? "json" : type;
      const result = await this.read(response, displayType, signal, version);
      if (!response.ok) throw new Error(this.lastStatus + " · Request failed. Error body shown below.");
      return result;
    }

    showMedia(blob, type) {
      this.mediaUrl = URL.createObjectURL(blob);
      const view = type === "audio" ? node("audio", {controls: true, src: this.mediaUrl}) : node("img", {src: this.mediaUrl, alt: "Generated image"});
      const extension = type === "image" ? "png" : blob.type.includes("mpeg") ? "mp3" : "wav";
      this.media.replaceChildren(view, node("a", {href: this.mediaUrl, download: "playground." + extension, textContent: "Download " + extension.toUpperCase()}));
    }
    async run(lookupOnly = false) {
      if (this.controller) return;
      const operation = this.current(), controller = new AbortController(), signal = controller.signal;
      const version = ++this.version, started = performance.now();
      this.clearResult(); this.lastStatus = ""; this.codes = []; this.controller = controller;
      this.send.disabled = true; this.cancel.disabled = false; this.operation.disabled = true;
      this.send.setAttribute("aria-busy", "true"); this.status.textContent = "Preparing request…";
      this.token.readOnly = true;
      try {
        if (lookupOnly) {
          const lookup = operation.lookup, headers = {};
          if (this.token.value) headers["X-Internal-Token"] = this.token.value;
          for (const field of operation.fields || []) if (field.location === "header") headers[field.key] = document.getElementById(fieldId(field.key)).value;
          const result = await this.request({url: this.url(lookup.route), method: "GET", headers}, "json", signal, version, started);
          const data = JSON.parse(result), input = document.getElementById(fieldId(lookup.field));
          const choices = data[lookup.key];
          if (!Array.isArray(choices)) throw new Error("Service returned an invalid choices list.");
          input.replaceChildren(node("option", {value: "", textContent: "Service default"}), ...choices.map(choice => node("option", {value: String(choice), textContent: this.redact(choice)})));
          if (choices.includes(data.default)) input.value = data.default;
        } else {
          const request = await this.build(operation, signal);
          signal.throwIfAborted();
          let type = operation.response || "json";
          let result = await this.request(request, type, signal, version, started);
          if (operation.followup) {
            const follow = operation.followup, data = JSON.parse(result);
            let route = follow.route.replace(/\{([a-z_]+)\}/g, (_, key) => encodeURIComponent(data[key]));
            const next = {url: this.url(route), method: follow.method || "GET", headers: {...request.headers}};
            delete next.headers["Content-Type"]; delete next.headers["X-Filename"];
            if (follow.bodyKeys) {
              const body = {};
              for (const key of follow.bodyKeys) { if (data[key] == null) throw new Error("Service omitted " + key + "."); body[key] = data[key]; }
              next.body = JSON.stringify(body); next.headers["Content-Type"] = "application/json";
            }
            type = follow.response;
            result = await this.request(next, type, signal, version, started);
          }
          if (version === this.version && result instanceof Blob) this.showMedia(result, type);
        }
        if (version === this.version) this.status.textContent = this.redact(this.lastStatus.replace(/ · \d+ ms/, " · " + Math.round(performance.now() - started) + " ms") + " · Complete");
      } catch (error) {
        if (version === this.version) {
          this.status.dataset.error = "true";
          const elapsed = Math.round(performance.now() - started);
          const prefix = this.lastStatus ? this.lastStatus.replace(/ · \d+ ms/, " · " + elapsed + " ms") : elapsed + " ms";
          const message = signal.aborted ? "Cancelled" : this.lastStatus && error.message.startsWith(this.lastStatus)
            ? error.message.slice(this.lastStatus.length).replace(/^ · /, "") : error.message;
          this.status.textContent = this.redact(prefix + " · " + message);
        }
      } finally {
        if (version === this.version) {
          this.controller = null; this.send.disabled = false; this.cancel.disabled = true; this.operation.disabled = false;
          this.token.readOnly = false; this.send.removeAttribute("aria-busy");
        }
      }
    }
  }
  window.Playground = Playground;
})();
