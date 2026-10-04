"use strict";

const STORAGE_KEY = "legislativni-harmonogram-state-v1";
const WEEKDAYS = ["pondělí", "úterý", "středa", "čtvrtek", "pátek", "sobota", "neděle"];

/** Formátování dat po česku. */
class Fmt {
  static date(iso) {
    if (!iso) return "—";
    const [y, m, d] = iso.split("-").map(Number);
    return `${d}. ${m}. ${y}`;
  }
  static dayWord(n) {
    const abs = Math.abs(n);
    if (abs === 1) return "den";
    if (abs >= 2 && abs <= 4) return "dny";
    return "dnů";
  }
  static esc(text) {
    return String(text ?? "").replace(/[&<>"']/g, c => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }
}

/**
 * Datum z pole <input type="date"> se nepřepočítává během psaní (prohlížeč
 * hlásí „změnu“ už po každém segmentu, např. rok 0002). Hodnota se potvrdí
 * až Enterem nebo opuštěním pole; neplatné/nedopsané datum se vrátí zpět.
 */
class DateCommit {
  static MIN_YEAR = 1990;
  static MAX_YEAR = 2100;

  static isValid(value) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
    const year = Number(value.slice(0, 4));
    return year >= DateCommit.MIN_YEAR && year <= DateCommit.MAX_YEAR;
  }

  static setValue(el, value) {
    el.dataset.committed = value;
    if (document.activeElement !== el) el.value = value;
  }

  static commit(el, onCommit) {
    const value = el.value;
    if (value === el.dataset.committed) return;
    if (!DateCommit.isValid(value)) {
      el.value = el.dataset.committed || "";
      return;
    }
    el.dataset.committed = value;
    onCommit(value);
  }

  static attach(el, onCommit) {
    el.addEventListener("blur", () => DateCommit.commit(el, onCommit));
    el.addEventListener("keydown", e => { if (e.key === "Enter") { e.preventDefault(); el.blur(); } });
  }

  static delegate(container, selector, onCommit) {
    container.addEventListener("focusout", e => {
      if (e.target.matches(selector)) DateCommit.commit(e.target, value => onCommit(e.target, value));
    });
    container.addEventListener("keydown", e => {
      if (e.key === "Enter" && e.target.matches(selector)) { e.preventDefault(); e.target.blur(); }
    });
  }
}

/** Komunikace s lokálním Python serverem. */
class ApiClient {
  async defaults() {
    return this.#json(await fetch("/api/default"));
  }
  async compute(state) {
    return this.#json(await fetch("/api/compute", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(state),
    }));
  }
  async variants() {
    return this.#json(await fetch("/api/variants"));
  }
  async saveVariants(doc) {
    return this.#json(await fetch("/api/variants", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(doc),
    }));
  }
  async parliamentRefresh(state) {
    return this.#json(await fetch("/api/parliament/refresh", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(state),
    }));
  }
  async parliamentImport(chamber, htmlBase64, name, state) {
    return this.#json(await fetch("/api/parliament/import", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ chamber, html_b64: htmlBase64, name, state }),
    }));
  }
  async docx(payload) {
    const response = await fetch("/api/export/docx", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!response.ok) throw new Error(`Server vrátil chybu ${response.status}`);
    return response.blob();
  }
  async #json(response) {
    if (!response.ok) throw new Error(`Server vrátil chybu ${response.status}`);
    return response.json();
  }
}

/** Uchování stavu v prohlížeči (automatické ukládání). */
class LocalStore {
  load() {
    try { return JSON.parse(localStorage.getItem(STORAGE_KEY) || "null"); }
    catch { return null; }
  }
  save(state) {
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(state)); } catch { /* nevadí */ }
  }
  clear() {
    try { localStorage.removeItem(STORAGE_KEY); } catch { /* nevadí */ }
  }
}

/** Vykreslení tabulky, nastavení a souhrnu. */
class ScheduleView {
  constructor(onChange) {
    this.onChange = onChange;
    this.rows = document.getElementById("rows");
    this.summary = document.getElementById("summary");
    this.startDate = document.getElementById("start-date");
    this.firstPhase = document.getElementById("first-phase");
    this.firstPhase.addEventListener("change", () =>
      this.onChange(s => { s.settings.first_phase = this.firstPhase.value; }));
    this.shiftEnd = document.getElementById("shift-end");
    this.weekdayFields = document.getElementById("weekday-fields");
    this.holidayList = document.getElementById("holiday-list");
    this.docTitle = document.getElementById("doc-title");
    this.docEffective = document.getElementById("doc-effective");
    this.openNotes = new Set();

    DateCommit.attach(this.startDate, value =>
      this.onChange(s => { s.settings.start_date = value; }));
    this.shiftEnd.addEventListener("change", () =>
      this.onChange(s => { s.settings.shift_end_to_working_day = this.shiftEnd.checked; }));
    this.docTitle.addEventListener("change", () =>
      this.onChange(s => { s.settings.title = this.docTitle.value; }));
    this.docEffective.addEventListener("change", () =>
      this.onChange(s => { s.settings.effective = this.docEffective.value; }));
    this.rows.addEventListener("change", e => this.#handleRowChange(e.target));
    DateCommit.delegate(this.rows, "[data-override-date]", (el, value) =>
      this.onChange(s => { s.phases[el.dataset.overrideDate].override = value; }));
    // „toggle“ nebublá – zachytíme jej ve fázi capture a pamatujeme si otevřené poznámky.
    this.rows.addEventListener("toggle", e => {
      const id = e.target.dataset?.notes;
      if (id) e.target.open ? this.openNotes.add(id) : this.openNotes.delete(id);
    }, true);
    this.weekdayFields.addEventListener("change", e => {
      const key = e.target.dataset.weekday;
      if (key) this.onChange(s => { s.settings.weekdays[key] = Number(e.target.value); });
    });
  }

  render(data) {
    const focusedId = document.activeElement?.id;
    this.data = data;
    this.#renderSettings(data);
    this.#renderSummary(data.summary);
    this.rows.innerHTML = data.phases.filter(p => !p.before_start).map((p, i) => this.#row(p, i)).join("");
    this.holidayList.innerHTML = data.holidays
      .map(h => `<li>${Fmt.date(h.date)} (${WEEKDAYS[h.weekday]}) – ${Fmt.esc(h.name)}</li>`)
      .join("");
    if (focusedId) document.getElementById(focusedId)?.focus();
  }

  #renderSettings(data) {
    DateCommit.setValue(this.startDate, data.settings.start_date);
    // Výběr prvního kroku: všechny fáze; oddíly podle Wordu (VPŘ, MPŘ, Vláda, PS…).
    let lastSection = null;
    this.firstPhase.innerHTML = data.phases.map(p => {
      let html = "";
      if (p.section !== lastSection) {
        if (lastSection !== null) html += "</optgroup>";
        html += `<optgroup label="${Fmt.esc(p.section)}">`;
        lastSection = p.section;
      }
      const selected = p.id === data.settings.first_phase ? "selected" : "";
      return html + `<option value="${p.id}" ${selected}>${Fmt.esc(p.name)}</option>`;
    }).join("") + "</optgroup>";
    if (document.activeElement !== this.docTitle) this.docTitle.value = data.settings.title;
    if (document.activeElement !== this.docEffective) this.docEffective.value = data.settings.effective;
    this.shiftEnd.checked = data.settings.shift_end_to_working_day;
    this.weekdayFields.innerHTML = data.settings.weekday_rules.map(rule => `
      <div class="field">
        <label for="wd-${rule.key}">${Fmt.esc(rule.label)}</label>
        <select id="wd-${rule.key}" data-weekday="${rule.key}">
          ${WEEKDAYS.slice(0, 5).map((name, i) =>
            `<option value="${i}" ${i === rule.weekday ? "selected" : ""}>${name}</option>`).join("")}
        </select>
      </div>`).join("");
  }

  #renderSummary(s) {
    const chips = [
      `<span class="chip">Zahájení <b>${Fmt.date(s.start)}</b></span>`,
      `<span class="chip">Vyhlášení <b>${s.end_approximate ? Fmt.esc(s.end_month) : Fmt.date(s.end)}</b>${s.end_approximate ? " (orientačně)" : ""}</span>`,
    ];
    if (s.total_days != null) {
      const months = (s.total_days / 30.44).toFixed(1).replace(".", ",");
      chips.push(`<span class="chip">Celkem <b>${s.total_days} ${Fmt.dayWord(s.total_days)}</b> (≈ ${months} měs.)</span>`);
    }
    if (s.errors) chips.push(`<span class="chip error">${s.errors} × porušení limitu</span>`);
    if (s.warnings) chips.push(`<span class="chip warning">${s.warnings} × upozornění</span>`);
    if (!s.errors && !s.warnings) chips.push(`<span class="chip ok">Bez výhrad</span>`);
    this.summary.innerHTML = chips.join("");
  }

  #row(p, index) {
    const classes = ["phase"];
    if (!p.enabled) classes.push("disabled");
    if (p.override) classes.push("overridden");
    if (p.issues.some(i => i.level === "error")) classes.push("has-error");
    else if (p.issues.some(i => i.level === "warning")) classes.push("has-warning");
    const off = p.enabled ? "" : "disabled";

    const group = p.group_first ? (() => {
      const g = this.data.groups.find(x => x.id === p.group);
      return `<label class="group-toggle"><input type="checkbox" id="g-${g.id}" data-group="${g.id}" ${g.enabled ? "checked" : ""}>
                zahrnout: ${Fmt.esc(g.label)}</label>`;
    })() : "";
    const weekdayTag = p.weekday_label ? `<span class="tag">zpravidla ${p.weekday_label}</span>` : "";
    const sitting = p.sitting ? (() => {
      const chamber = p.sitting.chamber === "psp" ? "schůze PSP" : "plénum Senátu";
      const days = p.sitting.weekdays.length >= 4 ? "út–pá" : p.sitting.weekdays.map(d => WEEKDAYS[d]).join(" nebo ");
      return `<div class="sitting"><span class="tag">${chamber}: ${days}</span>
        <label><input type="checkbox" id="ig-${p.id}" data-ignore="${p.id}" ${p.ignore_customs ? "checked" : ""} ${off}>
        Ignorovat zvyklosti</label></div>`;
    })() : "";

    const rules = p.is_first
      ? `<span class="start-hint">První krok – datum se zadává nahoře („Datum prvního kroku“).</span>`
      : p.constraints.map((c, ci) => this.#rule(p, c, ci, off)).join("");

    let dateCell = `<span class="dow">vynecháno</span>`;
    if (p.enabled) {
      const gap = p.gap_days != null ? `<div class="gap">+${p.gap_days} ${Fmt.dayWord(p.gap_days)}</div>` : "";
      dateCell = p.approximate
        ? `<div class="date-main approx" title="Mimo zveřejněný harmonogram komory – ve výstupech jen měsíc a rok">${Fmt.esc(p.month_label)}</div>
           <div class="dow">orientačně · počítáno s ${Fmt.date(p.date)}</div>${gap}`
        : `<div class="date-main">${Fmt.date(p.date)}</div><div class="dow">${WEEKDAYS[p.weekday]}</div>${gap}`;
    }

    let override = "";
    if (p.enabled && !p.is_first) {
      const checked = p.override ? "checked" : "";
      override = `<label><input type="checkbox" id="o-${p.id}" data-override="${p.id}" ${checked} ${off}> přepsat</label>`;
      if (p.override) {
        override += `<input type="date" id="od-${p.id}" data-override-date="${p.id}" value="${p.override}" data-committed="${p.override}" ${off}>`;
        if (p.computed && p.computed !== p.override) {
          override += `<div class="computed-hint">vypočteno: ${Fmt.date(p.computed)}</div>`;
        }
      }
    }

    const issues = p.issues.map(i => `<div class="issue ${i.level}">${Fmt.esc(i.message)}</div>`).join("");
    const reason = p.enabled
      ? `<div class="reason">${p.override ? "Ručně zadané datum." : Fmt.esc(p.reason)}</div>`
        + (p.sitting_note ? `<div class="sitting-note">${Fmt.esc(p.sitting_note)}</div>` : "") : "";

    return `<tr class="${classes.join(" ")}">
      <td class="num">${p.order ?? ""}</td>
      <td class="name"><div class="phase-name">${Fmt.esc(p.name)}</div>
        ${p.note ? `<div class="note">${Fmt.esc(p.note)}</div>` : ""}${weekdayTag}${sitting}${group}${this.#docNotes(p)}</td>
      <td class="rules">${rules}</td>
      <td class="date">${dateCell}</td>
      <td class="override no-print">${override}</td>
      <td class="status">${issues}${reason}</td>
    </tr>`;
  }

  #docNotes(p) {
    if (!p.enabled) return "";
    const custom = p.doc_note_custom || p.deadline_note_custom;
    const open = this.openNotes.has(p.id) ? "open" : "";
    let body = "";
    if (p.deadline_label) {
      body += `<label for="dn-${p.id}">Řádek „${Fmt.esc(p.deadline_label)}“ (${Fmt.date(p.deadline_date)})</label>
        <textarea id="dn-${p.id}" data-deadline-note="${p.id}">${Fmt.esc(p.deadline_note)}</textarea>`;
    }
    body += `<label for="n-${p.id}">Řádek „${Fmt.esc(p.doc_label)}“</label>
      <textarea id="n-${p.id}" data-note="${p.id}" placeholder="bez poznámky">${Fmt.esc(p.doc_note)}</textarea>`;
    return `<details class="doc-note" data-notes="${p.id}" ${open}>
      <summary>Word: ${Fmt.esc(p.section)} › ${Fmt.esc(p.doc_label)}${custom ? ' <span class="custom">(vlastní poznámka)</span>' : ""}</summary>
      ${body}</details>`;
  }

  #rule(p, c, ci, off) {
    const kind = c.kind === "max" ? "nejpozději do" : "nejdříve po";
    const hard = c.legal_limit != null;
    const bound = hard ? (c.kind === "max" ? `max="${c.legal_limit}"` : `min="${c.legal_limit}"`) : (c.kind === "max" ? `min="1"` : `min="0"`);
    const lock = hard
      ? `<span class="lock" title="Nepřekročitelný zákonný limit">§ ${c.kind === "max" ? "max" : "min"} ${c.legal_limit}</span>` : "";
    const unitDisabled = hard ? "disabled" : off;
    const title = c.note ? `title="${Fmt.esc(c.note)}"` : "";
    return `<div class="rule" ${title}>
      <span class="kind">${kind}</span>
      <input type="number" id="c-${p.id}-${ci}" data-phase="${p.id}" data-constraint="${ci}" value="${c.value}" ${bound} ${off}>
      <select id="u-${p.id}-${ci}" data-phase="${p.id}" data-unit="${ci}" ${unitDisabled}>
        <option value="calendar" ${c.unit === "calendar" ? "selected" : ""}>dnů</option>
        <option value="working" ${c.unit === "working" ? "selected" : ""}>prac. dnů</option>
      </select>
      <span class="ref">od: ${Fmt.esc(c.ref_name)}</span>${lock}
    </div>`;
  }

  #handleRowChange(el) {
    const ds = el.dataset;
    if (ds.group) {
      this.onChange(s => { s.groups[ds.group] = el.checked; });
    } else if (ds.constraint !== undefined) {
      this.onChange(s => { s.phases[ds.phase].constraints[ds.constraint].value = Number(el.value); });
    } else if (ds.unit !== undefined) {
      this.onChange(s => { s.phases[ds.phase].constraints[ds.unit].unit = el.value; });
    } else if (ds.override) {
      const phase = this.data.phases.find(p => p.id === ds.override);
      this.onChange(s => { s.phases[ds.override].override = el.checked ? phase.date : null; });
      if (el.checked) setTimeout(() => document.getElementById(`od-${ds.override}`)?.focus(), 50);
    } else if (ds.ignore) {
      this.onChange(s => { s.phases[ds.ignore].ignore_customs = el.checked; });
    } else if (ds.note) {
      this.onChange(s => { s.phases[ds.note].note = el.value; });
    } else if (ds.deadlineNote) {
      this.onChange(s => { s.phases[ds.deadlineNote].deadline_note = el.value; });
    } else if (ds.overrideDate) {
      // datum se potvrzuje až Enterem nebo opuštěním pole (DateCommit)
    }
  }
}

/** Harmonogramy komor: stav stažení, přepínače, přehled týdnů s ručními opravami. */
class ParliamentView {
  // Popisky podle komory; barvy (třídy wt-*) viz style.css.
  static LABELS = {
    psp: { plenary: "řádná schůze", extra: "mimořádná schůze", variable: "variabilní týden", other: "žádná schůze" },
    senat: { plenary: "plenární schůze", other: "žádná schůze" },
  };
  static MANUAL = { psp: ["plenary", "extra", "other"], senat: ["plenary", "other"] };

  constructor(onChange, actions) {
    this.onChange = onChange;
    this.status = document.getElementById("parl-status");
    this.weeks = document.getElementById("parl-weeks");
    this.useParl = document.getElementById("use-parl");
    this.pspVariable = document.getElementById("psp-variable");
    this.refreshBtn = document.getElementById("btn-parl-refresh");
    this.useParl.addEventListener("change", () =>
      this.onChange(s => { s.settings.use_parliament_calendar = this.useParl.checked; }));
    this.pspVariable.addEventListener("change", () =>
      this.onChange(s => { s.settings.psp_allow_variable = this.pspVariable.checked; }));
    this.refreshBtn.addEventListener("click", actions.refresh);
    document.getElementById("parl-import").addEventListener("change", async e => {
      const file = e.target.files[0];
      e.target.value = "";
      if (file) actions.importHtml(document.getElementById("parl-import-chamber").value, file);
    });
    this.weeks.addEventListener("change", e => {
      const { chamber, monday } = e.target.dataset;
      if (!chamber) return;
      this.onChange(s => {
        const overrides = s.settings.week_overrides[chamber] ||= {};
        if (e.target.value) overrides[monday] = e.target.value;
        else delete overrides[monday];
      });
    });
  }

  render(data) {
    const parl = data.parliament;
    this.useParl.checked = data.settings.use_parliament_calendar;
    this.pspVariable.checked = data.settings.psp_allow_variable;
    this.pspVariable.disabled = !data.settings.use_parliament_calendar;
    this.refreshBtn.disabled = parl.refreshing;
    this.refreshBtn.textContent = parl.refreshing ? "Stahuji…" : "Aktualizovat z psp.cz a senat.cz";

    const lines = Object.values(parl.status).map(st => {
      let text = `<b>${Fmt.esc(st.name)}:</b> `;
      if (st.weeks) {
        text += `${st.weeks} týdnů (${Fmt.date(st.first)} – ${Fmt.date(st.last)})`;
        if (st.fetched_at) text += `, aktualizováno ${Fmt.date(st.fetched_at.slice(0, 10))}`;
        if (st.method) text += ` (${Fmt.esc(st.method)})`;
      } else {
        text += "harmonogram zatím není načten – fáze se plánují bez ohledu na týdny zasedání";
      }
      if (st.error) text += ` <span class="err">⚠ ${Fmt.esc(st.error)}</span>`;
      return `<div>${text}</div>`;
    });
    if (parl.refreshing) lines.unshift(`<div class="busy">Stahuji aktuální harmonogramy…</div>`);
    this.status.innerHTML = lines.join("");

    const overrides = data.state.settings.week_overrides || {};
    this.weeks.innerHTML = parl.weeks.map(w => {
      const start = new Date(w.monday + "T12:00");
      const end = new Date(start.getTime() + 6 * 864e5);
      const label = `${start.getDate()}. ${start.getMonth() + 1}. – ${end.getDate()}. ${end.getMonth() + 1}. ${end.getFullYear()}`;
      return `<tr>
        <td>${label}</td>
        <td>${this.#select("psp", w)}</td>
        <td>${this.#select("senat", w)}</td>
        <td class="wk-phases">${w.phases.map(Fmt.esc).join(", ")}</td></tr>`;
    }).join("");
  }

  #select(chamber, w) {
    const info = w[chamber];
    const labels = ParliamentView.LABELS[chamber];
    // bez údaje z webu: u PSP o prázdninách předpoklad „žádná schůze“
    const auto = info.auto || (info.summer ? "other" : null);
    const autoClass = `wt-${auto || "unknown"}`;
    const autoText = info.auto ? `${labels[info.auto]} (web)`
      : info.summer ? `${labels.other} (prázdniny)` : "neuvedeno";
    const opts = [`<option value="" class="${autoClass}">${autoText}</option>`];
    for (const value of ParliamentView.MANUAL[chamber]) {
      const selected = info.manual === value ? "selected" : "";
      opts.push(`<option value="${value}" class="wt-${value}" ${selected}>${labels[value]} ✎</option>`);
    }
    const effective = info.manual || auto || "unknown";
    return `<select id="wk-${chamber}-${w.monday}" data-chamber="${chamber}" data-monday="${w.monday}"
      class="wt wt-${effective} ${info.manual ? "manual" : ""}"
      title="${info.manual ? "Ručně nastaveno – první položka vrátí údaj z webu" : "Údaj z webu"}">${opts.join("")}</select>`;
  }
}

/**
 * Varianty harmonogramu: {active, variants:[{id, name, state}]}. Ukládá se na server
 * (soubor v domovské složce); když server neodpoví, aspoň do prohlížeče.
 */
class VariantManager {
  constructor(api, local) {
    this.api = api;
    this.local = local;
    this.doc = { active: null, variants: [] };
  }

  static newId() {
    return Math.random().toString(36).slice(2, 10) + Date.now().toString(36).slice(-4);
  }

  async load() {
    try {
      this.doc = await this.api.variants();
    } catch {
      this.doc = this.local.load()?.variants ? this.local.load() : { active: null, variants: [] };
    }
    if (!this.doc.variants.length) {
      // první spuštění: převzít dosavadní stav z prohlížeče (starší verze aplikace)
      const legacy = this.local.load();
      const state = legacy && !legacy.variants ? legacy : null;
      const id = VariantManager.newId();
      this.doc = { active: id, variants: [{ id, name: "Základní varianta", state }] };
      this.persist();
    }
    return this.active;
  }

  get active() {
    return this.doc.variants.find(v => v.id === this.doc.active) || this.doc.variants[0];
  }

  setState(state) {
    const current = this.active;
    if (!current || JSON.stringify(current.state) === JSON.stringify(state)) return;
    current.state = state;
    current.updated = new Date().toISOString();
    this.persist();
  }

  create(name, state) {
    const id = VariantManager.newId();
    this.doc.variants.push({ id, name, state: structuredClone(state) });
    this.doc.active = id;
    this.persist(true);
    return this.active;
  }

  /** Nahradí celou sadu variant (nový harmonogram / načtený soubor). */
  replaceAll(variants, activeIndex = 0) {
    this.doc = {
      variants: variants.map(v => ({ id: VariantManager.newId(), name: v.name, state: v.state })),
      active: null,
    };
    this.doc.active = this.doc.variants[Math.min(activeIndex, this.doc.variants.length - 1)].id;
    this.persist(true);
    return this.active;
  }

  rename(name) {
    this.active.name = name.trim() || this.active.name;
    this.persist(true);
  }

  remove() {
    if (this.doc.variants.length <= 1) return null;
    const index = this.doc.variants.indexOf(this.active);
    this.doc.variants.splice(index, 1);
    this.doc.active = this.doc.variants[Math.max(0, index - 1)].id;
    this.persist(true);
    return this.active;
  }

  select(id) {
    this.doc.active = id;
    this.persist(true);
    return this.active;
  }

  uniqueName(base) {
    const names = new Set(this.doc.variants.map(v => v.name));
    if (!names.has(base)) return base;
    for (let i = 2; ; i++) if (!names.has(`${base} (${i})`)) return `${base} (${i})`;
  }

  persist(now = false) {
    clearTimeout(this.saveTimer);
    const save = async () => {
      try {
        await this.api.saveVariants(this.doc);
      } catch {
        this.local.save(this.doc);
      }
    };
    if (now) save(); else this.saveTimer = setTimeout(save, 600);
  }
}

/** Lišta variant: záložky, název, nová/smazat. */
class VariantBar {
  constructor(actions) {
    this.tabs = document.getElementById("variant-tabs");
    this.name = document.getElementById("variant-name");
    this.removeBtn = document.getElementById("btn-variant-delete");
    this.tabs.addEventListener("click", e => {
      const id = e.target.closest("[data-variant]")?.dataset.variant;
      if (id) actions.select(id);
    });
    document.getElementById("btn-variant-new").addEventListener("click", actions.create);
    this.removeBtn.addEventListener("click", actions.remove);
    this.name.addEventListener("change", () => actions.rename(this.name.value));
    this.name.addEventListener("keydown", e => { if (e.key === "Enter") this.name.blur(); });
  }

  render(doc) {
    this.tabs.innerHTML = doc.variants.map(v => `
      <button type="button" class="variant-tab ${v.id === doc.active ? "active" : ""}" data-variant="${v.id}"
        title="Přepnout na variantu">${Fmt.esc(v.name)}</button>`).join("");
    const active = doc.variants.find(v => v.id === doc.active);
    if (active && document.activeElement !== this.name) this.name.value = active.name;
    this.removeBtn.disabled = doc.variants.length <= 1;
  }
}

/** Exporty: CSV, JSON, tisk. */
class Exporter {
  static fileName(state, ext, variant) {
    const clean = text => (text || "").replace(/[^\p{L}\p{N}]+/gu, "_").replace(/^_+|_+$/g, "").slice(0, 60);
    const title = clean((state.settings.title || "").replace(/^Návrh zákona\s*/i, "").replace("…", ""));
    const parts = ["harmonogram", /^o?$/.test(title) ? "" : title, clean(variant)].filter(Boolean);
    return `${parts.join("_")}.${ext}`;
  }
  static csv(data) {
    const header = ["Pořadí", "Fáze", "Datum", "Den", "Odstup (dny)", "Ručně zadáno", "Upozornění"];
    const lines = data.phases.filter(p => p.enabled).map(p => [
      p.order, p.name, p.approximate ? p.month_label : Fmt.date(p.date),
      p.approximate ? "" : WEEKDAYS[p.weekday], p.gap_days ?? "",
      p.override ? "ano" : "", p.issues.map(i => i.message).join(" | "),
    ]);
    const cell = v => `"${String(v).replace(/"/g, '""')}"`;
    const text = [header, ...lines].map(r => r.map(cell).join(";")).join("\r\n");
    Exporter.#download("﻿" + text, "harmonogram.csv", "text/csv;charset=utf-8");
  }
  /** Celý harmonogram – všechny varianty v jednom souboru. */
  static json(doc, fileName) {
    const content = {
      format: "legislativni-harmonogram",
      version: 2,
      saved: new Date().toISOString(),
      active: Math.max(0, doc.variants.findIndex(v => v.id === doc.active)),
      variants: doc.variants.map(v => ({ name: v.name, state: v.state })),
    };
    Exporter.#download(JSON.stringify(content, null, 2), fileName, "application/json");
  }

  /** Přečte uložený soubor; zvládne i starší formáty (jedna varianta / holý stav). */
  static parseSaved(content, fallbackName) {
    if (Array.isArray(content?.variants) && content.variants.length) {
      return {
        active: Number.isInteger(content.active) ? content.active : 0,
        variants: content.variants.map((v, i) => ({ name: v.name || `Varianta ${i + 1}`, state: v.state || null })),
      };
    }
    const state = content?.state && content.settings === undefined ? content.state : content;
    if (!state || typeof state !== "object" || !state.settings) throw new Error("Neznámý formát souboru.");
    return { active: 0, variants: [{ name: content.name || fallbackName, state }] };
  }
  static blob(blob, name) {
    Exporter.#download(blob, name);
  }
  static #download(content, name, type) {
    const blob = content instanceof Blob ? content : new Blob([content], { type });
    const url = URL.createObjectURL(blob);
    const a = Object.assign(document.createElement("a"), { href: url, download: name });
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
}

/** Řídí aplikaci: stav → server → vykreslení. */
class App {
  constructor() {
    this.api = new ApiClient();
    this.store = new LocalStore();
    this.view = new ScheduleView(mutate => this.update(mutate));
    this.parliament = new ParliamentView(mutate => this.update(mutate), {
      refresh: () => this.refreshParliament(),
      importHtml: (chamber, file) => this.importParliament(chamber, file),
    });
    this.variants = new VariantManager(this.api, this.store);
    this.variantBar = new VariantBar({
      select: id => this.selectVariant(id),
      create: () => this.createVariant(),
      remove: () => this.removeVariant(),
      rename: name => { this.variants.rename(name); this.variantBar.render(this.variants.doc); },
    });
    this.state = null;
    this.data = null;
    this.#bindActions();
  }

  async start() {
    try {
      const variant = await this.variants.load();
      await this.#showVariant(variant);
    } catch (err) {
      this.toast("Nepodařilo se spojit se serverem. Běží aplikace?");
      console.error(err);
    }
  }

  async #showVariant(variant) {
    this.#apply(variant.state ? await this.api.compute(variant.state) : await this.api.defaults());
  }

  async selectVariant(id) {
    if (id === this.variants.doc.active) return;
    await this.#showVariant(this.variants.select(id));
  }

  async createVariant() {
    // Nová varianta = kopie právě naklikaného harmonogramu.
    const name = this.variants.uniqueName(`Varianta ${this.variants.doc.variants.length + 1}`);
    await this.#showVariant(this.variants.create(name, this.state));
    this.toast(`Vytvořena „${name}“ jako kopie předchozí varianty.`);
    this.variantBar.name.focus();
    this.variantBar.name.select();
  }

  async removeVariant() {
    const current = this.variants.active;
    if (this.variants.doc.variants.length <= 1) return;
    if (!confirm(`Smazat variantu „${current.name}“?`)) return;
    await this.#showVariant(this.variants.remove());
    this.toast(`Varianta „${current.name}“ smazána.`);
  }

  async update(mutate) {
    const next = structuredClone(this.state);
    mutate(next);
    try {
      this.#apply(await this.api.compute(next));
    } catch (err) {
      this.toast("Výpočet selhal – server neodpovídá.");
      console.error(err);
    }
  }

  #apply(data) {
    this.data = data;
    this.state = data.state;
    this.variants.setState(this.state);
    this.variantBar.render(this.variants.doc);
    this.view.render(data);
    this.parliament.render(data);
    // Stahování běží na pozadí (po startu aplikace) – po dokončení přepočítat.
    clearTimeout(this.pollTimer);
    if (data.parliament.refreshing) {
      this.pollTimer = setTimeout(() => this.update(() => {}), 2500);
    }
  }

  async refreshParliament() {
    this.parliament.refreshBtn.disabled = true;
    this.parliament.refreshBtn.textContent = "Stahuji…";
    try {
      this.#apply(await this.api.parliamentRefresh(this.state));
      const st = this.data.parliament.status;
      const errors = Object.values(st).filter(s => s.error && !s.weeks);
      this.toast(errors.length ? "Některý harmonogram se nepodařilo stáhnout – viz stav." : "Harmonogramy aktualizovány.");
    } catch (err) {
      this.toast("Aktualizace selhala.");
      console.error(err);
      this.parliament.render(this.data);
    }
  }

  async importParliament(chamber, file) {
    try {
      // Posíláme bajty (base64) – uložená stránka může být ve windows-1250; dekóduje server.
      const bytes = new Uint8Array(await file.arrayBuffer());
      let binary = "";
      for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
      const data = await this.api.parliamentImport(chamber, btoa(binary), file.name, this.state);
      this.#apply(data);
      this.toast(data.imported ? `Načteno ${data.imported} týdnů ze souboru ${file.name}.` : "Ve stránce nebyly nalezeny žádné týdny.");
    } catch (err) {
      this.toast("Stránku se nepodařilo načíst.");
      console.error(err);
    }
  }

  #bindActions() {
    document.getElementById("btn-docx").onclick = async () => {
      if (!this.state) return;
      try {
        // Všechny varianty do jednoho Wordu – každá má vlastní sloupec s termíny.
        const doc = this.variants.doc;
        const variants = doc.variants.map(v => ({ name: v.name, state: v.id === doc.active ? this.state : v.state }));
        const primary = Math.max(0, doc.variants.findIndex(v => v.id === doc.active));
        const single = variants.length === 1;
        Exporter.blob(await this.api.docx({ variants, primary }),
          Exporter.fileName(this.state, "docx", single ? this.variants.active?.name : "varianty"));
      } catch (err) {
        this.toast("Export do Wordu selhal.");
        console.error(err);
      }
    };
    document.getElementById("btn-csv").onclick = () => this.data && Exporter.csv(this.data);
    document.getElementById("btn-save").onclick = () => {
      if (!this.state) return;
      this.variants.setState(this.state);
      Exporter.json(this.variants.doc, Exporter.fileName(this.state, "json"));
      const count = this.variants.doc.variants.length;
      this.toast(count > 1 ? `Uloženo ${count} variant do jednoho souboru.` : "Harmonogram uložen.");
    };
    document.getElementById("btn-print").onclick = () => window.print();
    document.getElementById("btn-reset").onclick = async () => {
      const count = this.variants.doc.variants.length;
      const what = count > 1 ? `všech ${count} variant` : "aktuálního harmonogramu";
      if (!confirm(`Začít nový harmonogram? Dojde ke smazání ${what}.\n(Pokud je chcete zachovat, nejdřív je uložte.)`)) return;
      this.variants.replaceAll([{ name: "Základní varianta", state: null }]);
      this.#apply(await this.api.defaults());
      this.toast("Nový harmonogram.");
    };
    document.getElementById("file-load").onchange = async e => {
      const file = e.target.files[0];
      e.target.value = "";
      if (!file) return;
      try {
        // Soubor = celý harmonogram se všemi variantami; nahradí ten současný.
        const saved = Exporter.parseSaved(JSON.parse(await file.text()), file.name.replace(/\.json$/i, ""));
        const count = this.variants.doc.variants.length;
        if (!confirm(`Načíst „${file.name}“ (${saved.variants.length} var.)? Současný harmonogram `
                     + `(${count} var.) bude nahrazen.`)) return;
        await this.#showVariant(this.variants.replaceAll(saved.variants, saved.active));
        this.toast(`Načteno ${saved.variants.length === 1 ? "1 varianta" : saved.variants.length + " variant"} ze souboru ${file.name}.`);
      } catch {
        this.toast("Soubor se nepodařilo načíst.");
      }
    };
  }

  toast(message) {
    const el = document.getElementById("toast");
    el.textContent = message;
    el.classList.add("show");
    clearTimeout(this.toastTimer);
    this.toastTimer = setTimeout(() => el.classList.remove("show"), 2600);
  }
}

new App().start();
