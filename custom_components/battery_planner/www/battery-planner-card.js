/* Batteriplanerare – Lovelace-kort
 * Följer med komponenten och laddas automatiskt.
 * type: custom:battery-planner-card            plan, laddnivå, planerad laddning, besparing
 *   show_charging: true   (lista med planerade laddtillfällen)
 *   charging_rows: 6
 *   Flikar "Närmaste dygnet", "Imorgon" och "Historik" (vad batteriet gjorde, dag för dag, ca 8 dygn bakåt).
 *   collapsed: true       (allt under raden "Laddar …, används …" fälls ihop bakom "Visa mer")
 * type: custom:battery-planner-insights-card   scenarier, besparing per dag, prognos mot utfall
 * type: custom:battery-planner-fuse-card       säkringsskydd per fas
 * type: custom:battery-planner-health-card     solprognosens källa och batteriets slitage
 * entity: sensor.batteriplanerare_plan   (valfri, standard)
 */
const CARD_VERSION = "0.7.5";

const C = {
  bg: "var(--ha-card-background, var(--card-background-color, #14161b))",
  text: "var(--primary-text-color, #e8e8ea)",
  muted: "var(--secondary-text-color, #9aa0a6)",
  grid: "rgba(127,127,127,0.18)",
  amber: "#f5a524",
  cyan: "#22c3e6",
  price: "rgba(160,160,170,0.35)",
  pricePast: "rgba(160,160,170,0.15)",
  now: "#ff5f6d",
  sell: "#7bd88f",
};

const ACTION_TXT = { charge: "Laddar", discharge: "Levererar", idle: "Väntar" };
const ACTION_ICON = { charge: "mdi:battery-arrow-up", discharge: "mdi:battery-arrow-down", idle: "mdi:battery-clock" };

function fmtKr(v) {
  if (v === null || v === undefined || isNaN(v)) return "–";
  return `${Number(v).toLocaleString("sv-SE", { maximumFractionDigits: 0 })} kr`;
}

function hh(d) {
  return String(d.getHours()).padStart(2, "0");
}

function c_rows(v) {
  const n = parseInt(v, 10);
  return isNaN(n) || n < 1 ? 6 : n;
}

/** Group consecutive hours with the same action into spans: [{action, from, to}] */
function spans(plan) {
  const out = [];
  for (const r of plan) {
    const t = new Date(r.start);
    const last = out[out.length - 1];
    if (last && last.action === r.action && t - last.to === 0) {
      last.to = new Date(t.getTime() + 3600e3);
    } else {
      out.push({ action: r.action, from: t, to: new Date(t.getTime() + 3600e3) });
    }
  }
  return out;
}

function dayLabel(d, now) {
  const a = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const b = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  const diff = Math.round((b - a) / 864e5);
  if (diff === 0) return "Idag";
  if (diff === 1) return "Imorgon";
  return d.toLocaleDateString("sv-SE", { weekday: "short", day: "numeric", month: "numeric" });
}

/** Planned charging sessions: consecutive hours with charge > 0.05 kWh. */
function chargeSessions(plan) {
  const out = [];
  for (const r of plan) {
    const grid = r.grid_charge || 0, sol = r.solar_charge || 0;
    const t = new Date(r.start);
    const last = out[out.length - 1];
    if (grid + sol <= 0.05) continue;
    if (last && t - last.to === 0) {
      last.to = new Date(t.getTime() + 3600e3);
    } else {
      out.push({ from: t, to: new Date(t.getTime() + 3600e3), grid: 0, sol: 0, cost: 0, soc: 0 });
    }
    const s = out[out.length - 1];
    s.grid += grid;
    s.sol += sol;
    s.cost += grid * (r.price || 0);
    s.soc = r.soc_end;
  }
  return out;
}

function isoDay(d) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function sameDay(a, b) {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}

/** Rows of the plan that belong to tomorrow (local time). */
function tomorrowRows(plan, now) {
  const t = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1);
  return plan.filter((r) => sameDay(new Date(r.start), t));
}

/** What the battery does in one hour, finer than r.action: grid/sun charge, sell, to house or idle. */
function hourKind(r) {
  if ((r.grid_charge || 0) > 0.05) return "grid";
  if ((r.solar_charge || 0) > 0.05) return "sun";
  if ((r.to_grid || 0) > 0.05) return "sell";
  if ((r.to_load || 0) > 0.05) return "house";
  return "idle";
}

const KIND_TXT = { grid: "Laddar från nätet", sun: "Laddar med sol", sell: "Säljer till nätet", house: "Driver huset", idle: "Väntar" };
const KIND_CLS = { grid: "ch", sun: "sun", sell: "sell", house: "dis", idle: "idle" };

/** Consecutive hours with the same kind: [{kind, from, to, kwh, cost, income, soc}] */
function kindSpans(plan) {
  const out = [];
  for (const r of plan) {
    const k = hourKind(r);
    const t = new Date(r.start);
    let last = out[out.length - 1];
    if (!(last && last.kind === k && t - last.to === 0)) {
      last = { kind: k, from: t, to: t, kwh: 0, cost: 0, income: 0, soc: 0 };
      out.push(last);
    }
    last.to = new Date(t.getTime() + 3600e3);
    const g = r.grid_charge || 0, s = r.solar_charge || 0, h = r.to_load || 0, x = r.to_grid || 0;
    last.kwh += k === "grid" || k === "sun" ? g + s : h + x;
    last.cost += g * (r.price || 0);
    last.soc = r.soc_end;
  }
  return out;
}

function nextEventText(plan) {
  const sp = spans(plan).filter((s) => s.action !== "idle");
  if (!sp.length) return "Inget planerat de närmaste timmarna";
  const ch = sp.filter((s) => s.action === "charge").slice(0, 2).map((s) => `${hh(s.from)}–${hh(s.to)}`);
  const dis = sp.filter((s) => s.action === "discharge").slice(0, 3).map((s) => `${hh(s.from)}–${hh(s.to)}`);
  const parts = [];
  if (ch.length) parts.push(`Laddar ${ch.join(", ")}`);
  if (dis.length) parts.push(`används ${dis.join(", ")}`);
  const txt = parts.join(", ");
  return txt.charAt(0).toUpperCase() + txt.slice(1);
}

class BatteryPlannerCard extends HTMLElement {
  setConfig(config) {
    this._config = {
      entity: "sensor.batteriplanerare_plan",
      soc_entity: "sensor.batteriplanerare_virtuell_laddniva",
      soc_kwh_entity: "sensor.batteriplanerare_virtuell_energi_i_batteriet",
      today_entity: "sensor.batteriplanerare_besparing_idag",
      month_entity: "sensor.batteriplanerare_besparing_denna_manad",
      error_entity: "sensor.batteriplanerare_prognosfel_senaste_veckan",
      title: "Batteriplanerare",
      hours: 24,
      show_charging: true,
      charging_rows: 6,
      collapsed: true,
      ...config,
    };
    if (!this.shadowRoot) {
      this.attachShadow({ mode: "open" });
      this.shadowRoot.addEventListener("click", (e) => {
        const nav = e.target.closest && e.target.closest("[data-day]");
        if (nav) {
          this._histDay = nav.dataset.day;
          this._render();
          return;
        }
        const more = e.target.closest && e.target.closest("[data-more]");
        if (more) {
          this._open = !this._open;
          this._render();
          return;
        }
        const b = e.target.closest && e.target.closest("[data-view]");
        if (!b) return;
        this._view = b.dataset.view;
        this._render();
      });
    }
    if (!this._view) this._view = "next";
    this._sig = null; // render again with the new config
  }

  set hass(hass) {
    // Home Assistant sets hass on every state change in the whole system. Re-rendering each
    // time replaced the tab buttons between press and release, so clicks were lost.
    // Only render when one of the card's own entities has changed.
    this._hass = hass;
    const c = this._config || {};
    const sig = [c.entity, c.soc_entity, c.soc_kwh_entity, c.today_entity, c.month_entity, c.error_entity]
      .map((id) => (hass.states[id] ? hass.states[id].last_updated : "")).join("|");
    if (sig === this._sig) return;
    this._sig = sig;
    this._render();
  }

  connectedCallback() {
    // Move the "Nu" marker along even when no entity changes
    this._tick = setInterval(() => this._render(), 60e3);
  }

  disconnectedCallback() {
    clearInterval(this._tick);
  }

  getCardSize() {
    return 6;
  }

  static getStubConfig() {
    return { entity: "sensor.batteriplanerare_plan" };
  }

  _num(id) {
    const s = this._hass && this._hass.states[id];
    if (!s) return null;
    const v = parseFloat(s.state);
    return isNaN(v) ? null : v;
  }

  _render() {
    if (!this._hass || !this._config) return;
    const c = this._config;
    const st = this._hass.states[c.entity];
    if (!st) {
      this.shadowRoot.innerHTML = `<ha-card><div style="padding:16px">Hittar inte ${c.entity}</div></ha-card>`;
      return;
    }
    const full = st.attributes.plan || [];
    const now = new Date();
    const plan = full.slice(0, c.hours);
    const cur = plan[0] ? plan[0].action : "idle";
    const tom = tomorrowRows(full, now);
    const view = ["tomorrow", "history"].includes(this._view) ? this._view : "next";
    const shown = view === "tomorrow" ? tom : plan;
    const hist = st.attributes.historik || [];
    const soc = this._num(c.soc_entity);
    const socKwh = this._num(c.soc_kwh_entity);
    const today = this._num(c.today_entity);
    const month = this._num(c.month_entity);
    const err = this._num(c.error_entity);
    const planned = st.attributes.planerad_besparing;
    const todayFc = st.attributes.beraknad_besparing_idag;
    const shadow = st.attributes.lage === "skugga";
    const heat = st.attributes.uppvarmning || {};
    const car = st.attributes.elbil || {};
    const vvb = st.attributes.varmvatten || {};
    const spanTxt = (ps) => (ps || []).slice(0, 3).map((p) => `${hh(new Date(p.start))}–${hh(new Date(p.end))}`).join(", ");

    this.shadowRoot.innerHTML = `
      <style>${this._css()}</style>
      <ha-card>
        <div class="head">
          <div class="title">${c.title}${shadow ? '<span class="badge">Skuggläge</span>' : ""}</div>
          <div class="chip ${cur}"><ha-icon icon="${ACTION_ICON[cur] || ACTION_ICON.idle}"></ha-icon>${ACTION_TXT[cur] || "–"}</div>
        </div>
        <div class="stats">
          ${this._ring(soc)}
          <div class="kv"><div class="k">I batteriet</div><div class="v">${socKwh !== null ? socKwh.toLocaleString("sv-SE", { maximumFractionDigits: 1 }) + " kWh" : "–"}</div></div>
          <div class="kv"><div class="k">Sparat idag</div><div class="v accent">${fmtKr(today)}</div>${todayFc !== undefined && todayFc !== null ? `<div class="fc" title="Sparat hittills plus det planen räknar med under resten av dagen">beräknat ${fmtKr(todayFc)}</div>` : ""}</div>
          <div class="kv"><div class="k">Denna månad</div><div class="v">${fmtKr(month)}</div></div>
        </div>
        <div class="tabs" role="tablist">
          <button role="tab" data-view="next" class="${view === "next" ? "on" : ""}" aria-selected="${view === "next"}">Närmaste dygnet</button>
          <button role="tab" data-view="tomorrow" class="${view === "tomorrow" ? "on" : ""}" aria-selected="${view === "tomorrow"}">Imorgon${tom.length ? "" : " (inga priser än)"}</button>
          <button role="tab" data-view="history" class="${view === "history" ? "on" : ""}" aria-selected="${view === "history"}">Historik</button>
        </div>
        ${view === "history" ? this._history(hist, now) : view === "tomorrow" && !tom.length
          ? `<div class="empty">Morgondagens elpriser har inte kommit än. De brukar publiceras runt kl. 13, sedan planeras morgondagen.</div>`
          : this._chart(shown, view === "next" ? now : null)}
        ${view === "history" ? "" : `<div class="legend">
          <span><i class="sw price"></i>Elpris</span>
          <span><i class="sw ch"></i>Laddning</span>
          <span><i class="sw dis"></i>Urladdning</span>
          <span><i class="sw soc"></i>Laddnivå</span>
          ${view === "next" ? `<span><i class="sw now"></i>Nu</span>` : ""}
        </div>`}
        ${view === "history" ? "" : view === "tomorrow"
          ? this._daySchedule(tom)
          : `<div class="next"><ha-icon icon="mdi:clock-outline"></ha-icon>${nextEventText(plan)}</div>`}
        ${this._more(view, `
        ${c.show_charging && view !== "history" ? this._charging(st.attributes.plan || []) : ""}
        ${(car.perioder && car.perioder.length) || (vvb.perioder && vvb.perioder.length) || car.inkopplad === false ? `<div class="loads">
          ${vvb.perioder ? `<div><ha-icon icon="mdi:water-boiler"></ha-icon><span>Varmvatten</span><b>${vvb.perioder.length ? `${spanTxt(vvb.perioder)} · ${vvb.planerat_kwh} kWh` : "inget planerat"}</b></div>` : ""}
          ${car.laddniva !== undefined ? `<div><ha-icon icon="mdi:car-electric"></ha-icon><span>Elbil</span><b>${car.inkopplad === false ? "inte inkopplad" : car.perioder && car.perioder.length ? `${spanTxt(car.perioder)} · ${car.planerat_kwh} kWh` : "fulladdad"}</b></div>` : ""}
        </div>` : ""}
        ${heat.eldar ? `<div class="fire"><ha-icon icon="mdi:fireplace"></ha-icon>Det eldas – prognosen räknar med att värmepumpen vilar${heat.typisk_eldning_h ? ` (brukar brinna ~${Math.round(heat.typisk_eldning_h)} h)` : ""}</div>` : ""}
        <div class="foot">
          <span>Planerad besparing framåt: <b>${fmtKr(planned)}</b></span>
          <span>Prognosfel: <b>${err !== null ? err.toLocaleString("sv-SE") + " %" : "–"}</b></span>
        </div>
        `)}
      </ha-card>`;
  }

  /** Everything below the summary line, folded away behind "Visa mer" unless collapsed: false. */
  _more(view, html) {
    if (!this._config.collapsed) return html;
    const open = !!this._open;
    return `<button class="more" data-more aria-expanded="${open}">
        <span>${open ? "Visa mindre" : "Visa mer"}</span><ha-icon icon="mdi:chevron-${open ? "up" : "down"}"></ha-icon>
      </button>
      ${open ? `<div class="morebody">${html}</div>` : ""}`;
  }

  _charging(plan) {
    const all = chargeSessions(plan);
    const kwh = (v) => v.toLocaleString("sv-SE", { maximumFractionDigits: 1 });
    if (!all.length) return `<div class="charging"><div class="sub">Planerad laddning</div><div class="none">Ingen laddning planerad i ${plan.length} timmar framåt</div></div>`;
    const now = new Date();
    const rows = all.slice(0, c_rows(this._config.charging_rows)).map((s) => {
      const tot = s.grid + s.sol;
      const src = [];
      const both = s.grid > 0.05 && s.sol > 0.05;
      if (s.grid > 0.05) src.push(`<span class="src grid" title="Från nätet">nät${both ? " " + kwh(s.grid) : ""}</span>`);
      if (s.sol > 0.05) src.push(`<span class="src sun" title="Från solöverskott">sol${both ? " " + kwh(s.sol) : ""}</span>`);
      const avg = s.grid > 0.05 ? `${(s.cost / s.grid).toLocaleString("sv-SE", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} kr/kWh` : "";
      return `<div class="crow">
        <span class="when"><span class="day">${dayLabel(s.from, now)}</span>${hh(s.from)}–${hh(s.to)}</span>
        <span class="amt"><b>${kwh(tot)} kWh</b>${src.join("")}</span>
        <span class="price">${avg}</span>
        <span class="after" title="Energi i batteriet efteråt">→ ${kwh(s.soc)} kWh</span>
      </div>`;
    }).join("");
    const g = all.reduce((a, s) => a + s.grid, 0), sol = all.reduce((a, s) => a + s.sol, 0), cost = all.reduce((a, s) => a + s.cost, 0);
    const more = all.length > c_rows(this._config.charging_rows) ? ` · ${all.length - c_rows(this._config.charging_rows)} till` : "";
    return `<div class="charging">
      <div class="sub">Planerad laddning</div>
      ${rows}
      <div class="ctot">Totalt ${kwh(g + sol)} kWh${g > 0.05 ? ` · ${kwh(g)} kWh från nätet för ca ${fmtKr(cost)}` : ""}${sol > 0.05 ? ` · ${kwh(sol)} kWh sol` : ""}${more}</div>
    </div>`;
  }

  _history(hist, now) {
    const days = [...new Set(hist.map((r) => isoDay(new Date(r.start))))].sort();
    if (!days.length) return `<div class="empty">Ingen historik ännu. Varje avslutad timme sparas från och med version 0.6.5, så imorgon finns gårdagen här.</div>`;
    const yest = isoDay(new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1));
    let day = this._histDay && days.includes(this._histDay) ? this._histDay : days.includes(yest) ? yest : days[days.length - 1];
    const i = days.indexOf(day);
    const rows = hist.filter((r) => isoDay(new Date(r.start)) === day);
    const d = new Date(rows[0].start);
    const sav = rows.reduce((a, r) => a + (r.saving || 0), 0);
    const btn = (k, arrow, label) => `<button ${k ? `data-day="${k}"` : "disabled"} aria-label="${label}">${arrow}</button>`;
    return `<div class="daynav">
        ${btn(days[i - 1], "‹", "Föregående dag")}
        <span><b>${dayLabel(d, now) === "Idag" ? "Idag hittills" : dayLabel(d, now) === "Imorgon" ? "Imorgon" : d.toLocaleDateString("sv-SE", { weekday: "long", day: "numeric", month: "long" })}</b>${sameDay(d, new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1)) ? " · igår" : ""}</span>
        <span class="sav">Sparade <b>${fmtKr(sav)}</b></span>
        ${btn(days[i + 1], "›", "Nästa dag")}
      </div>
      ${this._chart(rows, null)}
      <div class="legend">
        <span><i class="sw price"></i>Elpris</span>
        <span><i class="sw ch"></i>Laddning</span>
        <span><i class="sw dis"></i>Urladdning</span>
        <span><i class="sw soc"></i>Laddnivå</span>
      </div>
      ${this._daySchedule(rows, "Det här gjorde batteriet", "Batteriet stod still hela dagen")}`;
  }

  _daySchedule(rows, title, idleTxt) {
    if (!rows.length) return "";
    const kwh = (v) => v.toLocaleString("sv-SE", { maximumFractionDigits: 1 });
    const sp = kindSpans(rows).filter((s) => s.kind !== "idle");
    const d = new Date(rows[0].start);
    const head = title || `Schema ${d.toLocaleDateString("sv-SE", { weekday: "long", day: "numeric", month: "long" })}`;
    if (!sp.length) return `<div class="charging"><div class="sub">${head}</div><div class="none">${idleTxt || "Batteriet står still hela dagen"}</div></div>`;
    const list = sp.map((s) => `<div class="crow day">
        <span class="when">${hh(s.from)}–${hh(s.to)}</span>
        <span class="amt"><i class="dot ${KIND_CLS[s.kind]}"></i>${KIND_TXT[s.kind]}</span>
        <span class="price">${kwh(s.kwh)} kWh${s.kind === "grid" ? ` · ca ${fmtKr(s.cost)}` : ""}</span>
        <span class="after" title="Energi i batteriet efteråt">→ ${kwh(s.soc)} kWh</span>
      </div>`).join("");
    const sum = (k) => sp.filter((s) => s.kind === k).reduce((a, s) => a + s.kwh, 0);
    const parts = [];
    if (sum("grid") > 0.05) parts.push(`${kwh(sum("grid"))} kWh från nätet`);
    if (sum("sun") > 0.05) parts.push(`${kwh(sum("sun"))} kWh sol`);
    if (sum("house") > 0.05) parts.push(`${kwh(sum("house"))} kWh till huset`);
    if (sum("sell") > 0.05) parts.push(`${kwh(sum("sell"))} kWh sålt`);
    return `<div class="charging">
      <div class="sub">${head}</div>
      ${list}
      <div class="ctot">${parts.join(" · ")}</div>
    </div>`;
  }

  _ring(soc) {
    const r = 26, circ = 2 * Math.PI * r;
    const p = soc === null ? 0 : Math.max(0, Math.min(100, soc));
    return `<svg class="ring" viewBox="0 0 64 64" aria-label="Laddnivå ${p} %">
      <circle cx="32" cy="32" r="${r}" fill="none" stroke="${C.grid}" stroke-width="6"/>
      <circle cx="32" cy="32" r="${r}" fill="none" stroke="${C.cyan}" stroke-width="6" stroke-linecap="round"
        stroke-dasharray="${(circ * p) / 100} ${circ}" transform="rotate(-90 32 32)"/>
      <text x="32" y="37" text-anchor="middle" font-size="15" font-weight="600" fill="currentColor">${soc === null ? "–" : Math.round(p) + "%"}</text>
    </svg>`;
  }

  _chart(plan, now) {
    if (!plan.length) return `<div class="empty">Ingen plan ännu</div>`;
    const W = 600, H = 226, L = 40, R = 40, T = 28, B = 26;
    const n = plan.length, bw = (W - L - R) / n;
    const maxP = Math.max(...plan.map((r) => r.price), 1);
    const maxE = Math.max(...plan.map((r) => Math.max(r.grid_charge + r.solar_charge, r.to_load + r.to_grid)), 1);
    const socMax = Math.max(...plan.map((r) => r.soc_end), 1);
    const midY = T + (H - T - B) * 0.62; // price above, energy bars around baseline
    const py = (v) => midY - (v / maxP) * (midY - T);
    const eh = (v) => (v / maxE) * (H - B - midY - 4);
    const sy = (v) => T + (1 - v / (socMax * 1.05)) * (H - T - B);
    let bars = "", energy = "", labels = "", line = "";
    plan.forEach((r, i) => {
      const x = L + i * bw;
      const t = new Date(r.start);
      bars += `<rect x="${x + bw * 0.12}" y="${py(r.price)}" width="${bw * 0.76}" height="${midY - py(r.price)}" rx="2" fill="${C.price}"><title>${hh(t)}:00 · ${r.price.toLocaleString("sv-SE")} kr/kWh</title></rect>`;
      const ch = r.grid_charge + r.solar_charge, dis = r.to_load + r.to_grid;
      if (ch > 0.05) energy += `<rect x="${x + bw * 0.2}" y="${midY + 2}" width="${bw * 0.6}" height="${eh(ch)}" rx="2" fill="${C.cyan}"><title>${hh(t)}:00 · laddar ${ch.toFixed(1)} kWh</title></rect>`;
      if (dis > 0.05) energy += `<rect x="${x + bw * 0.2}" y="${midY + 2}" width="${bw * 0.6}" height="${eh(dis)}" rx="2" fill="${C.amber}"><title>${hh(t)}:00 · levererar ${dis.toFixed(1)} kWh</title></rect>`;
      if (i % 3 === 0) labels += `<text x="${x + bw / 2}" y="${H - 8}" text-anchor="middle" class="ax">${hh(t)}</text>`;
      if (i > 0 && t.getHours() === 0) {
        labels += `<line x1="${x}" x2="${x}" y1="${T - 6}" y2="${H - B}" stroke="${C.grid}" stroke-dasharray="2 3"/>
          <text x="${x + 4}" y="${T - 8}" class="ax">${dayLabel(t, now || new Date())}</text>`;
      }
      line += `${i ? "L" : "M"}${x + bw / 2} ${sy(r.soc_end)}`;
    });
    const pTicks = [0, maxP / 2, maxP].map((v) => `<text x="${L - 6}" y="${py(v) + 4}" text-anchor="end" class="ax">${v.toLocaleString("sv-SE", { minimumFractionDigits: 1, maximumFractionDigits: 1 })}</text>`).join("");
    let nowMark = "";
    if (now) {
      const t0 = new Date(plan[0].start).getTime();
      const f = (now.getTime() - t0) / 3600e3;
      if (f >= 0 && f <= n) {
        const x = L + f * bw;
        nowMark = `<line x1="${x}" x2="${x}" y1="${T - 4}" y2="${H - B}" stroke="${C.now}" stroke-width="1.5"/>
          <rect x="${x - 14}" y="${T - 20}" width="28" height="15" rx="7" fill="${C.now}"/>
          <text x="${x}" y="${T - 9}" text-anchor="middle" class="nowtxt">Nu</text>`;
      }
    }
    const sTicks = [0, socMax].map((v) => `<text x="${W - R + 6}" y="${sy(v) + 4}" class="ax">${Math.round(v)}</text>`).join("");
    return `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Elpris och planerad laddning per timme">
      <line x1="${L}" x2="${W - R}" y1="${midY}" y2="${midY}" stroke="${C.grid}"/>
      ${bars}${energy}
      <path d="${line}" fill="none" stroke="#ffffff" stroke-opacity="0.85" stroke-width="2"/>
      ${pTicks}${sTicks}${labels}${nowMark}
      <text x="2" y="12" class="ax">kr/kWh</text>
      <text x="${W - 2}" y="12" text-anchor="end" class="ax">kWh</text>
    </svg>`;
  }

  _css() {
    return `
      ha-card { display:block; padding: 16px; color: ${C.text}; }
      .head { display:flex; justify-content:space-between; align-items:center; margin-bottom:12px; }
      .title { font-size:1.15em; font-weight:600; display:flex; align-items:center; gap:8px; }
      .badge { font-size:0.65em; font-weight:500; padding:2px 8px; border-radius:10px; border:1px solid ${C.muted}; color:${C.muted}; }
      .chip { display:flex; align-items:center; gap:6px; padding:4px 12px; border-radius:16px; font-size:0.9em; background:rgba(127,127,127,0.15); }
      .chip.charge { color:${C.cyan}; background:rgba(34,195,230,0.15); }
      .chip.discharge { color:${C.amber}; background:rgba(245,165,36,0.15); }
      .chip ha-icon { --mdc-icon-size:18px; }
      .stats { display:grid; grid-template-columns:64px 1fr 1fr 1fr; gap:12px; align-items:center; margin-bottom:8px; }
      .ring { width:64px; height:64px; color:${C.text}; }
      .kv .k { font-size:0.75em; color:${C.muted}; }
      .kv .v { font-size:1.15em; font-weight:600; }
      .kv .v.accent { color:${C.amber}; }
      .kv .fc { font-size:0.75em; color:${C.muted}; font-variant-numeric:tabular-nums; }
      .chart { width:100%; height:auto; display:block; margin-top:4px; }
      .chart .ax { font-size:11px; fill:${C.muted}; }
      .legend { display:flex; flex-wrap:wrap; gap:14px; font-size:0.75em; color:${C.muted}; margin:4px 0 10px; }
      .sw { display:inline-block; width:10px; height:10px; border-radius:2px; margin-right:5px; vertical-align:-1px; }
      .sw.price { background:${C.price}; } .sw.ch { background:${C.cyan}; } .sw.dis { background:${C.amber}; }
      .sw.soc { background:#fff; height:2px; vertical-align:3px; }
      .sw.now { background:${C.now}; width:2px; }
      .chart .nowtxt { font-size:10px; font-weight:600; fill:#fff; }
      .tabs { display:flex; gap:6px; margin:6px 0 2px; }
      .tabs button { font:inherit; font-size:0.8em; padding:4px 12px; border-radius:14px; border:1px solid ${C.grid}; background:transparent; color:${C.muted}; cursor:pointer; }
      .tabs button.on { background:rgba(34,195,230,0.15); border-color:transparent; color:${C.cyan}; }
      .crow.day { grid-template-columns:70px 1fr auto auto; }
      .daynav { display:flex; align-items:center; gap:8px; margin:8px 0 2px; font-size:0.9em; }
      .daynav span:first-of-type { flex:1; }
      .daynav .sav { color:${C.muted}; } .daynav .sav b { color:${C.amber}; }
      .daynav button { background:transparent; border:1px solid ${C.grid}; border-radius:50%; width:30px; height:30px; display:flex; align-items:center; justify-content:center; color:${C.text}; cursor:pointer; padding:0; font-size:1.3em; line-height:1; }
      .daynav button[disabled] { opacity:0.3; cursor:default; }
      .dot { display:inline-block; width:9px; height:9px; border-radius:50%; margin-right:2px; }
      .dot.ch { background:${C.cyan}; } .dot.sun { background:#ffd54a; } .dot.dis { background:${C.amber}; } .dot.sell { background:${C.sell}; }
      .next { display:flex; align-items:center; gap:8px; padding:10px 12px; border-radius:10px; background:rgba(127,127,127,0.1); font-size:0.95em; }
      .next ha-icon { --mdc-icon-size:18px; color:${C.cyan}; }
      .more { display:flex; align-items:center; justify-content:center; gap:4px; width:100%; margin-top:8px; padding:6px; font:inherit; font-size:0.85em; color:${C.muted}; background:transparent; border:0; border-radius:8px; cursor:pointer; }
      .more:hover { background:rgba(127,127,127,0.1); color:${C.text}; }
      .more ha-icon { --mdc-icon-size:18px; }
      .charging { margin-top:10px; }
      .charging .sub { font-size:0.8em; color:${C.muted}; margin-bottom:4px; }
      .charging .none { font-size:0.9em; color:${C.muted}; }
      .crow { display:grid; grid-template-columns:minmax(110px,auto) 1fr auto auto; gap:10px; align-items:center; padding:6px 0; border-top:1px solid ${C.grid}; font-size:0.9em; font-variant-numeric:tabular-nums; }
      .sub + .crow { border-top:0; }
      .crow .when { font-weight:600; }
      .crow .day { color:${C.muted}; font-weight:400; margin-right:6px; }
      .crow .amt { display:flex; flex-wrap:wrap; align-items:center; gap:6px; }
      .crow .amt b { color:${C.cyan}; font-weight:600; }
      .src { font-size:0.8em; padding:1px 7px; border-radius:9px; background:rgba(127,127,127,0.15); color:${C.muted}; }
      .src.sun { background:rgba(245,165,36,0.15); color:${C.amber}; }
      .crow .price, .crow .after { color:${C.muted}; font-size:0.9em; text-align:right; }
      .ctot { font-size:0.8em; color:${C.muted}; margin-top:4px; }
      .loads { display:flex; flex-direction:column; gap:6px; margin-top:8px; font-size:0.9em; }
      .loads div { display:flex; align-items:center; gap:8px; }
      .loads span { color:${C.muted}; min-width:90px; }
      .loads ha-icon { --mdc-icon-size:18px; color:${C.muted}; }
      .fire { display:flex; align-items:center; gap:8px; padding:8px 12px; margin-top:8px; border-radius:10px; background:rgba(245,120,36,0.14); color:#ff9a4d; font-size:0.9em; }
      .fire ha-icon { --mdc-icon-size:18px; }
      .foot { display:flex; justify-content:space-between; flex-wrap:wrap; gap:8px; margin-top:10px; font-size:0.8em; color:${C.muted}; }
      .foot b { color:${C.text}; font-weight:600; }
      .empty { padding:24px; text-align:center; color:${C.muted}; }
      @media (max-width: 420px) { .stats { grid-template-columns:56px 1fr 1fr; } .stats .kv:last-child { display:none; } .crow { grid-template-columns:1fr auto; } .crow .price { display:none; } }
    `;
  }
}

if (!customElements.get("battery-planner-card")) {
  customElements.define("battery-planner-card", BatteryPlannerCard);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "battery-planner-card",
    name: "Batteriplanerare",
    description: "Visar batteriplanen, laddnivå och besparing",
    preview: true,
  });
  console.info(`%c BATTERY-PLANNER-CARD %c ${CARD_VERSION} `, "background:#f5a524;color:#000", "background:#22c3e6;color:#000");
}


/* ---------- Insikter: scenarier, besparing per dag, prognos mot utfall ---------- */
const PERIODS = { idag: "Idag", manad: "Månaden", totalt: "Totalt" };

class BatteryPlannerInsightsCard extends HTMLElement {
  setConfig(config) {
    this._config = { entity: "sensor.batteriplanerare_plan", title: "Batteriets nytta", days: 14, show_health: false, show_fuse: false, ...config };
    this._period = this._config.period || "manad";
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
  }

  set hass(hass) {
    const st = hass.states[this._config.entity];
    const stamp = st ? st.last_updated : null;
    this._hass = hass;
    if (stamp !== this._stamp) {
      this._stamp = stamp;
      this._render();
    }
  }

  getCardSize() {
    return 7;
  }

  static getStubConfig() {
    return { entity: "sensor.batteriplanerare_plan" };
  }

  _render() {
    const c = this._config;
    const st = this._hass && this._hass.states[c.entity];
    if (!st) {
      this.shadowRoot.innerHTML = `<ha-card><div style="padding:16px">Hittar inte ${c.entity}</div></ha-card>`;
      return;
    }
    const scen = (st.attributes.scenarier || {})[this._period] || {};
    const all = st.attributes.dagar || {};
    const keys = Object.keys(all).sort().slice(-c.days);
    const days = keys.map((k) => ({ day: k, ...all[k] })).filter((d) => d.solar !== undefined);
    const sol = st.attributes.sol || {};
    const hl = st.attributes.halsa || {};
    const fg = st.attributes.sakring || {};

    this.shadowRoot.innerHTML = `
      <style>${this._css()}</style>
      <ha-card>
        <div class="head">
          <div class="title">${c.title}</div>
          <div class="tabs">${Object.entries(PERIODS).map(([k, v]) => `<button data-p="${k}" class="${k === this._period ? "on" : ""}">${v}</button>`).join("")}</div>
        </div>
        ${this._scenarios(scen)}
        <div class="sub">Besparing per dag (kr)</div>
        ${days.length ? this._savingChart(days) : '<div class="empty">Fylls på när första dygnet är bokfört.</div>'}
        <div class="sub">Förbrukning: prognos mot utfall (kWh)</div>
        ${this._pairChart(days, "load_fc", "load_act", "kWh")}
        ${days.some((d) => d.pv_act !== undefined) ? `<div class="sub">Sol: prognos mot utfall (kWh)</div>${this._pairChart(days, "pv_fc", "pv_act", "kWh")}` : ""}
        <div class="legend"><span><i class="o"></i>Prognos vid midnatt</span><span><i class="f"></i>Utfall</span></div>
        ${c.show_health ? this._health(sol, hl) : ""}
        ${c.show_fuse ? this._fuse(fg) : ""}
      </ha-card>`;
    this.shadowRoot.querySelectorAll(".tabs button").forEach((b) =>
      b.addEventListener("click", () => {
        this._period = b.dataset.p;
        this._render();
      })
    );
  }

  _scenarios(s) {
    if (s.sol === undefined) return '<div class="empty">Inga bokförda timmar för perioden än.</div>';
    const rows = [
      { k: "Bara nät", v: s.bara_nat, cls: "grid" },
      { k: "Med sol", v: s.sol, cls: "sun" },
      { k: "Sol + batteri", v: s.sol_batteri, cls: "bat" },
    ].filter((r) => r.v !== null && r.v !== undefined);
    const max = Math.max(1, ...rows.map((r) => Math.abs(r.v)));
    const bars = rows
      .map((r) => `<div class="row"><span class="k">${r.k}</span><div class="track"><div class="bar ${r.cls}" style="width:${Math.max(1.5, (100 * Math.abs(r.v)) / max)}%"></div></div><span class="v">${fmtKr(r.v)}</span></div>`)
      .join("");
    const parts = [];
    if (s.bara_nat !== null && s.bara_nat !== undefined) parts.push(`Solen sparar <b>${fmtKr(s.bara_nat - s.sol)}</b>`);
    parts.push(`batteriet ${parts.length ? "ytterligare " : "sparar "}<b>${fmtKr(s.sol - s.sol_batteri)}</b>`);
    const txt = parts.join(", ");
    return `<div class="scen">${bars}</div><div class="note">${txt.charAt(0).toUpperCase() + txt.slice(1)} på ${s.dagar} ${s.dagar === 1 ? "dag" : "dagar"}. Elkostnad efter såld el.</div>`;
  }

  _axis(days, W, x0, bw, y) {
    const step = Math.max(1, Math.ceil(days.length / 7));
    return days.map((d, i) => (i % step === 0 || i === days.length - 1 ? `<text class="ax" x="${x0 + i * bw + bw / 2}" y="${y}" text-anchor="middle">${Number(d.day.slice(8))}/${Number(d.day.slice(5, 7))}</text>` : "")).join("");
  }

  _savingChart(days) {
    const W = 600, H = 120, x0 = 34, top = 8, bot = 22;
    const vals = days.map((d) => (d.solar || 0) - (d.batt || 0));
    const hi = Math.max(1, ...vals), lo = Math.min(0, ...vals);
    const bw = (W - x0 - 4) / days.length;
    const y = (v) => top + ((hi - v) / (hi - lo)) * (H - top - bot);
    const bars = vals.map((v, i) => `<rect x="${x0 + i * bw + bw * 0.15}" width="${bw * 0.7}" y="${Math.min(y(v), y(0))}" height="${Math.max(1, Math.abs(y(v) - y(0)))}" rx="2" fill="${v >= 0 ? C.cyan : C.amber}"><title>${days[i].day}: ${v.toFixed(1)} kr</title></rect>`).join("");
    return `<svg class="chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
      <line x1="${x0}" x2="${W}" y1="${y(0)}" y2="${y(0)}" stroke="${C.grid}"/>
      <text class="ax" x="${x0 - 6}" y="${y(hi) + 4}" text-anchor="end">${Math.round(hi)}</text>
      <text class="ax" x="${x0 - 6}" y="${y(0) + 4}" text-anchor="end">0</text>
      ${bars}${this._axis(days, W, x0, bw, H - 6)}</svg>`;
  }

  _pairChart(days, fk, ak, unit) {
    const ok = days.filter((d) => d[ak] !== undefined);
    if (!ok.length) return '<div class="empty">Fylls på när första dygnet är bokfört.</div>';
    const W = 600, H = 110, x0 = 34, top = 8, bot = 22;
    const hi = Math.max(1, ...ok.map((d) => Math.max(d[fk] || 0, d[ak] || 0)));
    const bw = (W - x0 - 4) / ok.length;
    const y = (v) => top + ((hi - v) / hi) * (H - top - bot);
    const bars = ok.map((d, i) => {
      const x = x0 + i * bw;
      const f = d[fk] === undefined ? "" : `<rect x="${x + bw * 0.12}" width="${bw * 0.36}" y="${y(d[fk])}" height="${Math.max(1, y(0) - y(d[fk]))}" rx="2" fill="none" stroke="${C.muted}" stroke-width="1.2"><title>${d.day}: prognos ${d[fk]} ${unit}</title></rect>`;
      return `${f}<rect x="${x + bw * 0.52}" width="${bw * 0.36}" y="${y(d[ak])}" height="${Math.max(1, y(0) - y(d[ak]))}" rx="2" fill="${C.muted}"><title>${d.day}: utfall ${Number(d[ak]).toFixed(1)} ${unit}</title></rect>`;
    }).join("");
    return `<svg class="chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
      <line x1="${x0}" x2="${W}" y1="${y(0)}" y2="${y(0)}" stroke="${C.grid}"/>
      <text class="ax" x="${x0 - 6}" y="${y(hi) + 4}" text-anchor="end">${Math.round(hi)}</text>
      ${bars}${this._axis(ok, W, x0, bw, H - 6)}</svg>`;
  }

  /** Where the solar forecast comes from, and how much of the battery's life has been used. */
  _health(sol, hl) {
    return `<div class="sub">Solprognos</div><div class="foot" style="margin-top:2px">
          <span>Solkälla <b>${sol.kalla || "–"}</b></span>
          ${sol.prognos_24h !== null && sol.prognos_24h !== undefined ? `<span>Sol kommande dygn <b>${sol.prognos_24h} kWh</b></span>` : ""}
          ${sol.korrigering ? `<span>Prognosen justeras <b>× ${sol.korrigering}</b></span>` : ""}
        </div>
        ${hl.cykler !== undefined ? `<div class="sub">Batteriets slitage</div><div class="foot" style="margin-top:2px">
          <span>Laddcykler <b>${hl.cykler}</b></span>
          ${hl.cykler_per_ar !== null && hl.cykler_per_ar !== undefined ? `<span>Takt <b>${hl.cykler_per_ar} per år</b></span>` : ""}
          <span>Slitagekostnad <b>${hl.slitagekostnad} kr/kWh</b></span>
          ${hl.garanti_forbrukad_pct !== undefined ? `<span>Garanti förbrukad <b>${hl.garanti_forbrukad_pct} %</b></span>` : ""}
          ${hl.garanti_racker_ar !== undefined ? `<span>Garantin räcker <b>${hl.garanti_racker_ar} år</b> i den takten</span>` : ""}
        </div>` : ""}`;
  }

  _fuse(g) {
    if (!g || !g.faser_a || !g.faser_a.length) return "";
    const top = Math.max((g.mal_a || 15) + 3, ...g.faser_a);
    const bars = g.faser_a
      .map((a, i) => {
        const cls = a > g.mal_a ? "hot" : i + 1 === g.tyngsta_fas ? "warm" : "";
        return `<div class="ph"><span class="k">L${i + 1}</span><div class="track"><div class="bar ${cls}" style="width:${(100 * a) / top}%"></div><i class="lim" style="left:${(100 * g.mal_a) / top}%"></i></div><span class="v">${a.toFixed(1)} A</span></div>`;
      })
      .join("");
    const d = g.idag || {};
    const hist = Object.values(g.dagar || {});
    const sum = (k) => hist.reduce((s, x) => s + (x[k] || 0), 0);
    const lage = g.lage === "skugga" ? " (skuggläge, inget skrivs)" : "";
    return `<div class="sub">Ström per fas${lage} · streck = ${g.mal_a} A</div>
      <div class="scen">${bars}</div>
      <div class="foot" style="margin-top:8px">
        <span>Utrymme nu <b>${g.utrymme_kw ?? "–"} kW</b></span>
        <span>Laddgräns <b>${g.laddgrans_pct} %</b>${g.begransar ? " · begränsar" : ""}</span>
        <span>Idag <b>${d.sankningar || 0} sänkningar</b>, ${Math.round(d.begransad_min || 0)} min, ${(d.forlorad_kwh || 0).toFixed(1)} kWh</span>
        ${hist.length > 1 ? `<span>${hist.length} dagar <b>${sum("sankningar")} sänkningar</b>, ${sum("forlorad_kwh").toFixed(1)} kWh</span>` : ""}
      </div>`;
  }

  _css() {
    return `
      :host { display:block; }
      ha-card { display:block; padding:16px; color:${C.text}; }
      .head { display:flex; justify-content:space-between; align-items:center; gap:8px; margin-bottom:12px; flex-wrap:wrap; }
      .title { font-size:1.15em; font-weight:600; }
      .tabs { display:flex; gap:4px; background:rgba(127,127,127,0.12); border-radius:16px; padding:3px; }
      .tabs button { border:0; background:none; color:${C.muted}; font:inherit; font-size:0.85em; padding:4px 12px; border-radius:13px; cursor:pointer; }
      .tabs button.on { background:rgba(127,127,127,0.28); color:${C.text}; }
      .scen { display:flex; flex-direction:column; gap:8px; }
      .row { display:grid; grid-template-columns:96px 1fr 84px; gap:10px; align-items:center; font-size:0.9em; }
      .row .k { color:${C.muted}; }
      .row .v { text-align:right; font-weight:600; font-variant-numeric:tabular-nums; }
      .track { height:14px; border-radius:7px; background:rgba(127,127,127,0.1); overflow:hidden; }
      .bar { height:100%; border-radius:7px; }
      .ph { display:grid; grid-template-columns:28px 1fr 56px; gap:10px; align-items:center; font-size:0.85em; }
      .ph .k { color:${C.muted}; } .ph .v { text-align:right; font-variant-numeric:tabular-nums; }
      .ph .track { position:relative; height:10px; overflow:visible; }
      .ph .bar { background:${C.muted}; } .ph .bar.warm { background:${C.amber}; } .ph .bar.hot { background:#e5484d; }
      .ph .lim { position:absolute; top:-3px; bottom:-3px; width:2px; background:${C.text}; opacity:.6; }
      .bar.grid { background:${C.muted}; } .bar.sun { background:${C.amber}; } .bar.bat { background:${C.cyan}; }
      .note { margin-top:10px; font-size:0.85em; color:${C.muted}; }
      .note b { color:${C.text}; }
      .sub { margin:16px 0 4px; font-size:0.8em; color:${C.muted}; text-transform:none; }
      .chart { width:100%; height:auto; display:block; }
      .chart .ax { font-size:11px; fill:${C.muted}; }
      .legend { display:flex; gap:14px; font-size:0.75em; color:${C.muted}; margin-top:4px; }
      .legend i { display:inline-block; width:10px; height:10px; border-radius:2px; margin-right:5px; vertical-align:-1px; }
      .legend i.o { border:1.2px solid ${C.muted}; box-sizing:border-box; } .legend i.f { background:${C.muted}; }
      .foot { display:flex; flex-wrap:wrap; gap:14px; margin-top:12px; font-size:0.8em; color:${C.muted}; }
      .foot b { color:${C.text}; font-weight:600; }
      .empty { padding:14px; text-align:center; color:${C.muted}; font-size:0.9em; }
    `;
  }
}

if (!customElements.get("battery-planner-insights-card")) {
  customElements.define("battery-planner-insights-card", BatteryPlannerInsightsCard);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "battery-planner-insights-card",
    name: "Batteriplanerare – insikter",
    description: "Scenarier, besparing per dag och prognos mot utfall",
    preview: true,
  });
}


/* ---------- Säkringsskydd och slitage som egna kort ---------- */
/** Shares look and helpers with the insights card, but shows only one section. */
class BatteryPlannerSectionCard extends BatteryPlannerInsightsCard {
  setConfig(config) {
    this._config = { entity: "sensor.batteriplanerare_plan", title: this.constructor.TITLE, ...config };
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
  }

  getCardSize() {
    return 3;
  }

  _render() {
    const c = this._config;
    const st = this._hass && this._hass.states[c.entity];
    if (!st) {
      this.shadowRoot.innerHTML = `<ha-card><div style="padding:16px">Hittar inte ${c.entity}</div></ha-card>`;
      return;
    }
    const body = this._body(st.attributes) || `<div class="empty">${this.constructor.EMPTY}</div>`;
    this.shadowRoot.innerHTML = `
      <style>${this._css()} .first > .sub:first-child, .first > .foot:first-child { margin-top:0; }</style>
      <ha-card>
        <div class="head"><div class="title">${c.title}</div></div>
        <div class="first">${body}</div>
      </ha-card>`;
  }
}

class BatteryPlannerFuseCard extends BatteryPlannerSectionCard {
  static TITLE = "Säkringsskydd";
  static EMPTY = "Ingen fasström än. Ange sensorer för strömmen per fas under Batteriplanerare → Konfigurera.";
  _body(a) {
    return this._fuse(a.sakring || {});
  }
}

class BatteryPlannerHealthCard extends BatteryPlannerSectionCard {
  static TITLE = "Slitage och solprognos";
  static EMPTY = "Inget att visa än.";
  _body(a) {
    return this._health(a.sol || {}, a.halsa || {});
  }
}

for (const [type, cls, name, description] of [
  ["battery-planner-fuse-card", BatteryPlannerFuseCard, "Batteriplanerare – säkringsskydd", "Strömmen per fas och hur ofta laddningen begränsas"],
  ["battery-planner-health-card", BatteryPlannerHealthCard, "Batteriplanerare – slitage", "Solprognosens källa, laddcykler och garanti"],
]) {
  if (!customElements.get(type)) {
    customElements.define(type, cls);
    window.customCards = window.customCards || [];
    window.customCards.push({ type, name, description, preview: true });
  }
}
