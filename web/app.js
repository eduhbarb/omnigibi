/* =========================================================================
 * Omnigibi — interface web
 * Consome a API FastAPI (/api). Sem dependências externas.
 * =======================================================================*/

const state = {
  sources: [],
  selected: new Set(),
  banca: [],
  store: null, // prateleira do navegador (quadrinhos + histórico + avisos)
  filter: "",
  onlyLowest: false,
  spark: new Map(),
};

/* --------------------------------------------------------------- helpers */
const $ = (sel) => document.querySelector(sel);

const money = (value) =>
  value === null || value === undefined
    ? "—"
    : Number(value).toLocaleString("pt-BR", { style: "currency", currency: "BRL" });

const esc = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (char) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char])
  );

const pct = (value) =>
  value === null || value === undefined ? "—" : `${Number(value).toFixed(2).replace(".", ",")}%`;

// Botao "Comprar" que leva direto a loja com o menor preco disponivel.
function buyButton(url) {
  if (!url) {
    return '<button class="btn btn--buy btn--sm" disabled title="Sem oferta disponível no momento">🛒 Comprar</button>';
  }
  return `<a class="btn btn--buy btn--sm" href="${esc(url)}" target="_blank" rel="noopener noreferrer" title="Abrir a loja com o menor preço">🛒 Comprar</a>`;
}

function toDate(value) {
  if (!value) return null;
  const text = String(value);
  const hasTimezone = /Z$|[+-]\d{2}:?\d{2}$/.test(text);
  const date = new Date(hasTimezone ? text : `${text}Z`);
  return Number.isNaN(date.getTime()) ? null : date;
}

function when(value) {
  const date = toDate(value);
  if (!date) return "—";
  const diffMin = Math.round((Date.now() - date.getTime()) / 60000);
  if (diffMin < 1) return "agora";
  if (diffMin < 60) return `${diffMin} min atrás`;
  if (diffMin < 1440) return `${Math.round(diffMin / 60)} h atrás`;
  if (diffMin < 43200) return `${Math.round(diffMin / 1440)} d atrás`;
  return date.toLocaleDateString("pt-BR");
}

function dateTime(value) {
  const date = toDate(value);
  return date ? date.toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" }) : "—";
}

function deltaBadge(delta, deltaPct) {
  if (delta === null || delta === undefined) return '<span class="badge badge--muted">sem variação</span>';
  const drop = Number(delta) < 0;
  const arrow = drop ? "▼" : "▲";
  const cls = drop ? "badge--down" : "badge--up";
  return `<span class="badge ${cls}">${arrow} ${money(Math.abs(Number(delta)))} (${pct(Math.abs(Number(deltaPct || 0)))})</span>`;
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const text = await response.text();
  let payload = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = { detail: text };
    }
  }
  if (!response.ok) {
    const detail = payload?.detail;
    throw new Error(typeof detail === "string" ? detail : `Erro HTTP ${response.status}`);
  }
  return payload;
}

/* ------------------------------------------------ estado no navegador (store)
 * A prateleira vive AQUI, no navegador. O servidor é só motor de scraping e
 * cálculo: recebe o estado, aplica a ação e devolve o estado recalculado.
 * Na Vercel o disco é efêmero e há várias instâncias em paralelo, então guardar
 * a prateleira no servidor fazia os quadrinhos sumirem a cada recarga. */
const STORE_KEY = "omnigibi:state:v1";

function readStore() {
  try {
    const raw = localStorage.getItem(STORE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

// Versões anteriores guardavam a prateleira na chave de backup automático.
// Traz esses dados para a chave nova uma única vez.
function migrateLegacyBackup() {
  if (readStore()) return;
  if (state.store) return;
  try {
    const raw = localStorage.getItem("omnigibi:auto-backup");
    if (!raw) return;
    const legacy = JSON.parse(raw);
    if (legacy?.comics?.length) {
      state.store = legacy;
      writeStore(legacy);
    }
    localStorage.removeItem("omnigibi:auto-backup");
  } catch {
    /* ignora backup inválido */
  }
}

function writeStore(store) {
  if (!store) return;
  try {
    localStorage.setItem(STORE_KEY, JSON.stringify(store));
  } catch {
    toast("Armazenamento do navegador cheio — exporte um backup JSON.", "warn");
  }
}

// Manda o estado atual + a ação pedida; o servidor devolve tudo recalculado.
async function callState(action = "sync", extra = {}) {
  const body = { action, ...extra };
  const store = state.store || readStore();
  if (store) body.state = store;
  const view = await api("/api/state", { method: "POST", body: JSON.stringify(body) });
  applyView(view);
  return view;
}

function applyView(view) {
  if (!view) return;
  if (view.state) {
    state.store = view.state;
    writeStore(view.state);
  }
  if (view.comics) state.banca = view.comics;
  if (view.stats) renderStats(view.stats);
  if (view.changes) renderChanges(view.changes);
  if (view.alerts) renderAlerts(view.alerts);
  renderBanca();
}

function pendingCard(cached) {
  const node = document.createElement("article");
  node.className = "card card--pending";
  node.innerHTML = `
    <div class="card__top">
      ${coverMarkup(cached.image_url, cached.title)}
      <div class="card__body">
        <div class="card__title">${esc(cached.title)}</div>
        <div class="card__meta"><span class="spinner"></span> adicionando à prateleira…</div>
      </div>
    </div>`;
  return node;
}

function toast(message, type = "ok") {
  const node = document.createElement("div");
  node.className = `toast toast--${type === "err" ? "err" : "ok"}`;
  node.textContent = message;
  $("#toasts").appendChild(node);
  setTimeout(() => node.remove(), 4200);
}

function feedback(html, kind = "loading") {
  const node = $("#search-feedback");
  if (!html) {
    node.hidden = true;
    node.innerHTML = "";
    return;
  }
  node.hidden = false;
  node.className = `feedback feedback--${kind}`;
  node.innerHTML = html;
}

function coverMarkup(url, title) {
  if (url) {
    return `<div class="cover"><img src="${esc(url)}" alt="${esc(title)}" loading="lazy"
              onerror="this.replaceWith(document.createTextNode('HQ'))" /></div>`;
  }
  const initials = String(title || "HQ").replace(/[^\p{L}\p{N} ]/gu, "").trim().slice(0, 2).toUpperCase();
  return `<div class="cover">${esc(initials)}</div>`;
}

/* ---------------------------------------------------------------- gráficos */
function prepCanvas(canvas) {
  const ratio = window.devicePixelRatio || 1;
  const width = canvas.clientWidth || 300;
  const height = canvas.clientHeight || 100;
  canvas.width = width * ratio;
  canvas.height = height * ratio;
  const ctx = canvas.getContext("2d");
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  return { ctx, width, height };
}

function drawSpark(canvas, points) {
  const { ctx, width, height } = prepCanvas(canvas);
  ctx.clearRect(0, 0, width, height);
  if (!points || points.length < 2) {
    ctx.fillStyle = "#8b97b0";
    ctx.font = "11px Segoe UI, sans-serif";
    ctx.fillText("histórico em construção…", 8, height / 2 + 4);
    return;
  }

  const prices = points.map((point) => point.price);
  const min = Math.min(...prices);
  const max = Math.max(...prices);
  const span = max - min || 1;
  const pad = 4;
  const x = (index) => pad + (index / (points.length - 1)) * (width - pad * 2);
  const y = (price) => height - pad - ((price - min) / span) * (height - pad * 2);

  const gradient = ctx.createLinearGradient(0, 0, 0, height);
  gradient.addColorStop(0, "rgba(47,213,119,.35)");
  gradient.addColorStop(1, "rgba(47,213,119,0)");

  ctx.beginPath();
  ctx.moveTo(x(0), height);
  points.forEach((point, index) => ctx.lineTo(x(index), y(point.price)));
  ctx.lineTo(x(points.length - 1), height);
  ctx.closePath();
  ctx.fillStyle = gradient;
  ctx.fill();

  ctx.beginPath();
  points.forEach((point, index) => {
    const px = x(index);
    const py = y(point.price);
    if (index === 0) ctx.moveTo(px, py);
    else ctx.lineTo(px, py);
  });
  ctx.strokeStyle = prices[prices.length - 1] <= prices[0] ? "#2fd577" : "#ff5f6d";
  ctx.lineWidth = 1.8;
  ctx.stroke();

  const lastPrice = prices[prices.length - 1];
  ctx.beginPath();
  ctx.arc(x(points.length - 1), y(lastPrice), 3.2, 0, Math.PI * 2);
  ctx.fillStyle = "#ffcc00";
  ctx.fill();
}

function drawChart(canvas, points) {
  if (!canvas) return;
  canvas.__points = points;
  const { ctx, width, height } = prepCanvas(canvas);
  ctx.clearRect(0, 0, width, height);
  if (!points || points.length < 2) {
    ctx.fillStyle = "#8b97b0";
    ctx.font = "13px Segoe UI, sans-serif";
    ctx.fillText("Ainda não há variações suficientes para desenhar o gráfico.", 14, height / 2);
    return;
  }

  const padL = 62;
  const padR = 16;
  const padT = 16;
  const padB = 28;
  const prices = points.map((point) => point.price);
  let min = Math.min(...prices);
  let max = Math.max(...prices);
  const margin = (max - min) * 0.12 || 5;
  min -= margin;
  max += margin;
  const span = max - min || 1;

  const x = (index) => padL + (index / (points.length - 1)) * (width - padL - padR);
  const y = (price) => padT + (1 - (price - min) / span) * (height - padT - padB);

  // grade + eixo Y
  ctx.strokeStyle = "rgba(139,151,176,.18)";
  ctx.fillStyle = "#8b97b0";
  ctx.font = "11px Segoe UI, sans-serif";
  ctx.lineWidth = 1;
  for (let line = 0; line <= 4; line += 1) {
    const value = min + (span * line) / 4;
    const py = y(value);
    ctx.beginPath();
    ctx.moveTo(padL, py);
    ctx.lineTo(width - padR, py);
    ctx.stroke();
    ctx.fillText(money(value).replace("R$", "").trim(), 8, py + 4);
  }

  // série
  const gradient = ctx.createLinearGradient(0, padT, 0, height - padB);
  gradient.addColorStop(0, "rgba(78,168,255,.35)");
  gradient.addColorStop(1, "rgba(78,168,255,0)");

  ctx.beginPath();
  ctx.moveTo(x(0), height - padB);
  points.forEach((point, index) => ctx.lineTo(x(index), y(point.price)));
  ctx.lineTo(x(points.length - 1), height - padB);
  ctx.closePath();
  ctx.fillStyle = gradient;
  ctx.fill();

  ctx.beginPath();
  points.forEach((point, index) => {
    const px = x(index);
    const py = y(point.price);
    if (index === 0) ctx.moveTo(px, py);
    else ctx.lineTo(px, py);
  });
  ctx.strokeStyle = "#4ea8ff";
  ctx.lineWidth = 2.2;
  ctx.stroke();

  // marcadores de queda
  points.forEach((point, index) => {
    if (index === 0) return;
    if (point.price < points[index - 1].price) {
      ctx.beginPath();
      ctx.arc(x(index), y(point.price), 3.4, 0, Math.PI * 2);
      ctx.fillStyle = "#2fd577";
      ctx.fill();
    }
  });

  // marcos X
  ctx.fillStyle = "#8b97b0";
  const first = toDate(points[0].recorded_at);
  const last = toDate(points[points.length - 1].recorded_at);
  if (first) ctx.fillText(first.toLocaleDateString("pt-BR"), padL, height - 8);
  if (last) {
    const label = last.toLocaleDateString("pt-BR");
    ctx.fillText(label, width - padR - ctx.measureText(label).width, height - 8);
  }
}

/* ------------------------------------------------------------------ fontes */
function renderSources() {
  $("#sources").innerHTML = state.sources
    .map(
      (source) => `
      <button type="button" class="chip" data-source="${esc(source.name)}"
              aria-pressed="${state.selected.has(source.name)}"
              title="${esc(source.homepage || "fonte local")}">
        <span class="chip__dot"></span>${esc(source.label)}
      </button>`
    )
    .join("");

  $("#sources")
    .querySelectorAll(".chip")
    .forEach((chip) => {
      chip.addEventListener("click", () => {
        const name = chip.dataset.source;
        if (state.selected.has(name)) state.selected.delete(name);
        else state.selected.add(name);
        if (state.selected.size === 0) state.sources.forEach((s) => state.selected.add(s.name));
        renderSources();
        persistPrefs();
      });
    });
}

function selectedSources() {
  const enabled = state.sources.filter((source) => source.enabled).map((source) => source.name);
  const chosen = [...state.selected];
  if (chosen.length === 0 || chosen.length === state.sources.length) return null;
  return chosen.filter((name) => enabled.includes(name));
}

/* ------------------------------------------------------------------- stats */
function renderStats(data) {
  if (!data) return;
  $("#stat-comics").textContent = data.total_comics;
  $("#stat-offers").textContent = data.total_offers;
  $("#stat-history").textContent = data.total_history;
  $("#stat-drops").textContent = data.drops;
}

function renderChanges(rows) {
  const node = $("#changes");
  if (!node) return;
  rows = rows || [];
  if (!rows.length) {
    node.innerHTML = `<div class="empty">Nenhuma mudança de preço registrada ainda. Use “Atualizar tudo” para começar o histórico.</div>`;
    return;
  }
  node.innerHTML = rows
    .map(
      (row) => `
      <div class="feed__row">
        <div>
          <div><b>${esc(row.comic_title)}</b></div>
          <div class="feed__when">${esc(row.source || "melhor preço")} · ${dateTime(row.recorded_at)}</div>
        </div>
        <div>${money(row.price)}</div>
        <div>${deltaBadge(row.delta, row.delta_pct)}
          ${row.discount_pct ? `<span class="badge badge--info">-${pct(row.discount_pct)}</span>` : ""}
        </div>
      </div>`
    )
    .join("");
}

/* ------------------------------------------------------------------- avisos */
function renderAlerts(data) {
  const node = $("#alerts");
  if (!node) return;
  data = data || { unseen: 0, alerts: [] };
  const unseen = data.unseen || 0;
  const badge = $("#alerts-count");
  if (badge) {
    badge.textContent = unseen;
    badge.hidden = unseen === 0;
  }
  if (!data.alerts.length) {
    node.innerHTML = `<div class="empty">Nenhum aviso ainda. Quando um produto monitorado voltar ao estoque, aparece aqui.</div>`;
    return;
  }
  node.innerHTML = data.alerts
    .map(
      (alert) => `
      <div class="feed__row${alert.seen ? "" : " feed__row--unseen"}">
        <div>
          <div><b>${esc(alert.message)}</b></div>
          <div class="feed__when">${esc(alert.source_label || alert.source || "")} · ${dateTime(alert.created_at)}</div>
        </div>
        <div>${money(alert.price)}</div>
        <div>${alert.url ? `<a class="btn btn--ghost btn--sm" href="${esc(alert.url)}" target="_blank" rel="noopener">abrir</a>` : ""}</div>
      </div>`
    )
    .join("");
}

async function ackAlerts() {
  try {
    const view = await callState("ack_alerts", {});
    toast(view.detail || "Avisos marcados como lidos", "ok");
  } catch (error) {
    toast(`Erro: ${error.message}`, "err");
  }
}

/* ------------------------------------------------------------------- busca */
async function runSearch(event) {
  if (event) event.preventDefault();
  const query = $("#search-input").value.trim();
  if (query.length < 2) {
    feedback("Digite ao menos 2 caracteres para buscar.", "warn");
    return;
  }

  const button = $("#search-form button");
  button.disabled = true;
  feedback('<span class="spinner"></span>Consultando lojas e comparando preços...', "loading");
  $("#search-results").innerHTML = "";

  try {
    const data = await api("/api/search", {
      method: "POST",
      body: JSON.stringify({
        query,
        sources: selectedSources(),
        state: state.store || readStore(),
      }),
    });
    renderSearchResults(data);
  } catch (error) {
    feedback(`Falha na busca: ${esc(error.message)}`, "error");
  } finally {
    button.disabled = false;
  }
}

function renderSearchResults(data) {
  window.__lastSearch = data; // reaproveitado pelo botão "Adicionar à banca"
  const results = data.results || [];
  const related = data.related || [];
  const failures = data.sources_failed || [];

  if (!results.length && !related.length) {
    feedback(
      `Nenhuma oferta relevante encontrada para “${esc(data.query)}”. ` +
        (failures.length
          ? `Fontes indisponíveis: ${failures.map((f) => esc(f.label)).join(", ")}.`
          : "Tente outro termo."),
      "warn"
    );
    return;
  }

  const notes = [`${data.total_offers} oferta(s) analisada(s)`, `${results.length} produto(s) agrupado(s)`];
  if (data.query_volume) notes.push(`edição identificada: vol. ${esc(data.query_volume)}`);
  if (data.query_variants?.length > 1) notes.push(`${data.query_variants.length} variações de busca usadas`);
  if (related.length) notes.push(`${related.length} relacionado(s)`);
  if (failures.length) notes.push(`⚠ indisponíveis: ${failures.map((f) => esc(f.label)).join(", ")}`);
  feedback(notes.join(" · "), failures.length ? "warn" : "ok");

  const container = $("#search-results");
  container.innerHTML =
    results.map((item) => searchCard(item)).join("") +
    (related.length
      ? `<div class="results__divider" id="search-related">
           <span>Outros volumes e títulos relacionados</span>
           <small>o volume pedido é priorizado acima; estes itens <b>não</b> entram no mesmo histórico de preço</small>
         </div>` + related.map((item) => searchCard(item, true)).join("")
      : "");

  container.querySelectorAll("[data-add]").forEach((button) => {
    button.addEventListener("click", () => addToBanca(button.dataset.add, button));
  });
  container.querySelectorAll("[data-open]").forEach((button) => {
    button.addEventListener("click", () => openDetail(Number(button.dataset.open)));
  });
}

function searchCard(result, isRelated = false) {
  const best = result.best;
  const badges = [];

  if (result.volume) {
    const tipo = result.volume_match ? "volume pedido" : "outro volume";
    const classe = result.volume_match ? "badge--volume" : "badge--muted";
    badges.push(`<span class="badge ${classe}">vol. ${esc(result.volume)} · ${tipo}</span>`);
  }
  const oosCount = result.out_of_stock_count || 0;
  if (!result.in_stock) badges.push('<span class="badge badge--oos">⛔ em falta — sem oferta disponível</span>');
  else if (oosCount > 0) badges.push(`<span class="badge badge--warn">⚠ ${oosCount} loja(s) em falta</span>`);
  if (result.preorder) badges.push('<span class="badge badge--pre">📅 pré-venda</span>');
  if (result.is_lowest_ever) badges.push('<span class="badge badge--record">🏆 menor preço já registrado</span>');
  else if (result.lowest_ever) badges.push(`<span class="badge badge--info">menor já visto: ${money(result.lowest_ever)}</span>`);
  if (result.discount_pct > 0) badges.push(`<span class="badge badge--down">-${pct(result.discount_pct)} de desconto</span>`);
  if (result.store_count > 1 && result.savings_vs_max > 0)
    badges.push(`<span class="badge badge--muted">economia até ${money(result.savings_vs_max)}</span>`);

  const publisherLine = result.publisher ? `${esc(result.publisher)} · ` : "";
  const actions = result.comic_id
    ? `${buyButton(best?.url)}<button class="btn btn--ghost btn--sm" data-open="${result.comic_id}">Já está na banca — ver</button>`
    : `${buyButton(best?.url)}<button class="btn btn--primary btn--sm" data-add="${esc(result.title)}">＋ Adicionar à banca</button>`;

  const priceRow = best
    ? `<div class="price-row" style="margin-top:8px">
            <span class="price price--best">${money(best.price)}</span>
            ${best.list_price ? `<span class="price__old">${money(best.list_price)}</span>` : ""}
          </div>
          <div class="price__note">melhor em ${esc(best.source_label || best.source)}${
            best.shipping === 0 ? " · frete grátis" : ""
          }</div>`
    : `<div class="price-row" style="margin-top:8px">
            <span class="price price--oos">em falta</span>
          </div>
          <div class="price__note">nenhuma oferta disponível agora — monitore para avisar quando voltar</div>`;

  return `
    <article class="card${result.volume_match ? "" : " card--related"}${isRelated ? " card--faded" : ""}${!result.in_stock ? " card--oos" : ""}">
      <div class="card__top">
        ${coverMarkup(result.image_url, result.title)}
        <div class="card__body">
          <div class="card__title">${esc(result.title)}</div>
          <div class="card__meta">
            ${publisherLine}${result.offer_count} oferta(s) em ${result.store_count} loja(s)
          </div>
          ${priceRow}
        </div>
      </div>
      <div class="badges">${badges.join("")}</div>
      <div class="mini-stats">
        <span>faixa: <b>${money(result.price_min)}–${money(result.price_max)}</b></span>
        <span>média: <b>${money(result.price_avg)}</b></span>
        <span>relevância: <b>${(result.relevance * 100).toFixed(0)}%</b></span>
      </div>
      <div class="card__actions">${actions}</div>
    </article>`;
}

window.__lastSearch = null;

async function addToBanca(title, button) {
  const cached = [...(window.__lastSearch?.results || []), ...(window.__lastSearch?.related || [])].find(
    (item) => item.title === title
  );
  if (!cached) return;

  button.disabled = true;
  button.textContent = "adicionando...";

  // Feedback instantâneo: cartão provisório até o servidor responder.
  const container = $("#banca");
  container?.querySelector(".empty")?.remove();
  const phantom = container ? pendingCard(cached) : null;
  if (phantom) container.prepend(phantom);

  try {
    // As ofertas já vieram da busca: grava direto. Sem re-scraping síncrono.
    const view = await callState("add", {
      comic: {
        title: cached.title,
        publisher: cached.publisher,
        issue: cached.volume || null,
        volume: cached.volume || null,
        cover_url: cached.image_url,
        reference_price: cached.reference_price || cached.best?.list_price || null,
        offers: cached.offers,
      },
      sources: selectedSources(),
      scrape: false,
    });
    toast(view.detail || "Quadrinho adicionado à banca", "ok");
  } catch (error) {
    phantom?.remove();
    toast(`Erro ao adicionar: ${error.message}`, "err");
    button.disabled = false;
    button.textContent = "＋ Adicionar à banca";
  }
}

/* ------------------------------------------------------------------- banca */
function visibleComics() {
  const needle = state.filter ? state.filter.toLowerCase() : "";
  return state.banca.filter((comic) => {
    if (needle) {
      const haystack = `${comic.title} ${comic.publisher || ""}`.toLowerCase();
      if (!haystack.includes(needle)) return false;
    }
    if (state.onlyLowest && !comic.snapshot?.is_lowest_ever) return false;
    return true;
  });
}

function renderBanca() {
  const container = $("#banca");
  if (!container) return;
  const comics = visibleComics();
  if (!comics.length) {
    container.innerHTML = `<div class="empty">${
      state.banca.length
        ? "Nenhum quadrinho bate com o filtro."
        : "A prateleira está vazia. Busque um quadrinho acima para começar a monitorar."
    }</div>`;
    return;
  }

  container.innerHTML = comics.map(bancaCard).join("");

  container.querySelectorAll("[data-refresh]").forEach((button) =>
    button.addEventListener("click", () => refreshComic(button.dataset.refresh, button))
  );
  container.querySelectorAll("[data-open]").forEach((button) =>
    button.addEventListener("click", () => openDetail(button.dataset.open))
  );
  container.querySelectorAll("[data-remove]").forEach((button) =>
    button.addEventListener("click", () => removeComic(button.dataset.remove))
  );

  // desenha os sparklines
  container.querySelectorAll("canvas.spark").forEach((canvas) => {
    const points = state.spark.get(canvas.dataset.spark);
    if (points) drawSpark(canvas, points);
  });
}

function bancaCard(comic) {
  const snapshot = comic.snapshot || {};
  const badges = [];

  if (!snapshot.in_stock) badges.push('<span class="badge badge--oos">⛔ em falta — sem oferta disponível</span>');
  else if (snapshot.out_of_stock_count > 0)
    badges.push(`<span class="badge badge--warn">⚠ ${snapshot.out_of_stock_count} loja(s) em falta</span>`);
  if (snapshot.preorder) badges.push('<span class="badge badge--pre">📅 pré-venda</span>');
  if (snapshot.target_reached) badges.push('<span class="badge badge--target">🎯 preço alvo atingido</span>');
  if (snapshot.is_lowest_ever) badges.push('<span class="badge badge--record">🏆 no menor preço já registrado</span>');
  if (snapshot.discount_pct > 0)
    badges.push(`<span class="badge badge--down">-${pct(snapshot.discount_pct)} desconto atual</span>`);
  if (snapshot.last_change) {
    const change = snapshot.last_change;
    if (change.change_type === "queda") badges.push(deltaBadge(change.delta, change.delta_pct));
    if (change.change_type === "alta") badges.push(deltaBadge(change.delta, change.delta_pct));
  }

  state.spark.set(comic.key, comic.sparkline || []);

  const priceRow = snapshot.current_price !== null && snapshot.current_price !== undefined
    ? `<div class="price-row" style="margin-top:8px">
            <span class="price price--best">${money(snapshot.current_price)}</span>
            ${snapshot.current_list_price ? `<span class="price__old">${money(snapshot.current_list_price)}</span>` : ""}
          </div>
          <div class="price__note">
            melhor em ${esc(snapshot.current_source_label || "—")}
            ${snapshot.current_shipping === 0 ? " · frete grátis" : ""}
          </div>`
    : `<div class="price-row" style="margin-top:8px"><span class="price price--oos">em falta</span></div>
          <div class="price__note">monitorando — avisamos quando voltar ao estoque</div>`;

  return `
    <article class="card">
      <div class="card__top">
        ${coverMarkup(comic.cover_url, comic.title)}
        <div class="card__body">
          <div class="card__title">${esc(comic.title)}</div>
          <div class="card__meta">
            ${comic.publisher ? `${esc(comic.publisher)}` : ""}${comic.publisher && comic.issue ? " · " : ""}${comic.issue ? `nº ${esc(comic.issue)}` : ""}${(comic.publisher || comic.issue) ? " · " : ""}${snapshot.store_count || 0} loja(s) monitorada(s)
          </div>
          ${priceRow}
        </div>
      </div>

      <div class="badges">${badges.join("") || '<span class="badge badge--muted">monitorando</span>'}</div>

      <div class="mini-stats">
        <span>menor já registrado: <b>${money(snapshot.lowest_ever)}</b></span>
        <span>maior já visto: <b>${money(snapshot.highest_ever)}</b></span>
        <span>registros: <b>${snapshot.history_count}</b></span>
      </div>

      <canvas class="spark" data-spark="${esc(comic.key)}" height="40"></canvas>

      <div class="card__actions">
        ${buyButton(snapshot.current_offer_url)}
        <button class="btn btn--ghost btn--sm" data-refresh="${esc(comic.key)}">↻ Atualizar preços</button>
        <button class="btn btn--sm" data-open="${esc(comic.key)}">📊 Histórico</button>
        <button class="btn btn--danger btn--sm" data-remove="${esc(comic.key)}">Remover</button>
      </div>
    </article>`;
}

async function refreshComic(key, button) {
  button.disabled = true;
  button.textContent = "consultando lojas...";
  try {
    const view = await callState("refresh", { key, sources: selectedSources() });
    toast(view.detail || "Preços atualizados", "ok");
  } catch (error) {
    toast(`Erro: ${error.message}`, "err");
    button.disabled = false;
    button.textContent = "↻ Atualizar preços";
  }
}

async function removeComic(key) {
  try {
    const view = await callState("remove", { key });
    toast(view.detail || "Quadrinho removido da banca", "ok");
  } catch (error) {
    toast(`Erro ao remover: ${error.message}`, "err");
  }
}

/* ------------------------------------------------------------------ modal */
async function openDetail(key) {
  $("#modal").hidden = false;
  $("#modal-title").textContent = "Carregando...";
  $("#modal-body").innerHTML = '<div class="empty"><span class="spinner"></span>Carregando histórico...</div>';

  try {
    const view = await callState("detail", { key });
    renderDetail(view.comic);
  } catch (error) {
    $("#modal-body").innerHTML = `<div class="feedback feedback--error">Erro: ${esc(error.message)}</div>`;
  }
}

function renderDetail(comic) {
  const snapshot = comic.snapshot || {};
  $("#modal-title").textContent = comic.title;

  const history = (comic.history || []).slice().reverse(); // ordem cronológica
  const chartPoints = history.map((row) => ({ price: row.price, recorded_at: row.recorded_at }));

  const offersRows = (comic.offers || [])
    .map(
      (offer) => `
      <tr>
        <td>${esc(offer.source_label || offer.source)}</td>
        <td>${esc(offer.seller || "—")}</td>
        <td class="num">${money(offer.price)}</td>
        <td class="num">${offer.list_price ? money(offer.list_price) : "—"}</td>
        <td class="num">${offer.discount_pct ? `<span class="down">-${pct(offer.discount_pct)}</span>` : "—"}</td>
        <td class="num">${offer.shipping === 0 ? "grátis" : money(offer.shipping)}</td>
        <td>${offer.in_stock ? "disponível" : "sem estoque"}${offer.preorder ? " · 📅 pré-venda" : ""}</td>
        <td>${offer.url ? `<a href="${esc(offer.url)}" target="_blank" rel="noopener">abrir</a>` : "—"}</td>
      </tr>`
    )
    .join("");

  const historyRows = (comic.history || [])
    .map(
      (row) => `
      <tr>
        <td>${dateTime(row.recorded_at)}</td>
        <td>${money(row.previous_price)}</td>
        <td class="num">${money(row.price)}</td>
        <td class="num">${
          row.delta === null || row.delta === undefined
            ? '<span class="badge badge--muted">primeiro registro</span>'
            : deltaBadge(row.delta, row.delta_pct)
        }</td>
        <td class="num">${row.discount_pct ? `<span class="down">-${pct(row.discount_pct)}</span>` : "—"}</td>
        <td class="num">${money(row.lowest_ever)}</td>
      </tr>`
    )
    .join("");

  $("#modal-body").innerHTML = `
    <div class="detail-grid">
      <div class="kpi"><div class="kpi__label">Preço atual</div>
        <div class="kpi__value">${money(snapshot.current_price)}</div>
        <div class="price__note">${esc(snapshot.current_source_label || "—")}</div></div>
      <div class="kpi"><div class="kpi__label">Menor já registrado</div>
        <div class="kpi__value">${money(snapshot.lowest_ever)}</div>
        <div class="price__note">${dateTime(snapshot.lowest_ever_at)}</div></div>
      <div class="kpi"><div class="kpi__label">Desconto atual</div>
        <div class="kpi__value down">-${pct(snapshot.discount_pct)}</div>
        <div class="price__note">capa ${money(snapshot.reference_price)}</div></div>
      <div class="kpi"><div class="kpi__label">Maior já visto</div>
        <div class="kpi__value">${money(snapshot.highest_ever)}</div>
        <div class="price__note">economia ${money(snapshot.savings_from_highest)}</div></div>
      <div class="kpi"><div class="kpi__label">Registros</div>
        <div class="kpi__value">${snapshot.history_count}</div>
        <div class="price__note">${snapshot.offer_count} ofertas ativas</div></div>
    </div>

    <div class="section-title">Evolução do melhor preço</div>
    <div class="chart-wrap"><canvas class="chart" id="detail-chart"></canvas></div>

    <div class="section-title">Ações</div>
    <div class="inline-form">
      <label>Preço alvo (R$)</label>
      <input type="number" step="0.01" min="0" id="target-input"
             value="${snapshot.target_price ?? ""}" placeholder="ex.: 79.90" />
      <button class="btn btn--sm btn--primary" id="save-target">Salvar alerta</button>
      <button class="btn btn--sm btn--ghost" id="modal-refresh">↻ Reconsultar lojas</button>
      ${buyButton(snapshot.current_offer_url)}
      <button class="btn btn--sm btn--ghost" id="export-csv">⬇ Exportar CSV</button>
    </div>

    <div class="section-title">Ofertas por loja (${(comic.offers || []).length})</div>
    <div class="tbl-wrap"><table>
      <thead><tr>
        <th>Loja</th><th>Vendedor</th><th class="num">Preço</th><th class="num">De</th>
        <th class="num">Desconto</th><th class="num">Frete</th><th>Estoque</th><th>Link</th>
      </tr></thead>
      <tbody>${offersRows || '<tr><td colspan="8">Sem ofertas registradas.</td></tr>'}</tbody>
    </table></div>

    <div class="section-title">Histórico de variações (melhor preço)</div>
    <div class="tbl-wrap"><table>
      <thead><tr>
        <th>Data</th><th>Preço anterior</th><th class="num">Novo preço</th>
        <th class="num">Variação</th><th class="num">Desconto</th><th class="num">Menor até aqui</th>
      </tr></thead>
      <tbody>${historyRows || '<tr><td colspan="6">Nenhuma variação registrada ainda.</td></tr>'}</tbody>
    </table></div>
  `;

  drawChart(document.getElementById("detail-chart"), chartPoints);

  const csvBtn = document.getElementById("export-csv");
  if (csvBtn) csvBtn.addEventListener("click", () => exportComicCsv(comic));

  document.getElementById("save-target").addEventListener("click", async () => {
    const value = document.getElementById("target-input").value;
    try {
      await callState("update", {
        key: comic.key,
        patch: { target_price: value === "" ? null : Number(value) },
      });
      toast("Alerta de preço salvo", "ok");
      openDetail(comic.key);
    } catch (error) {
      toast(`Erro: ${error.message}`, "err");
    }
  });

  document.getElementById("modal-refresh").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    button.textContent = "consultando...";
    try {
      const view = await callState("refresh", { key: comic.key, sources: selectedSources() });
      toast(view.detail || "Preços atualizados", "ok");
      openDetail(comic.key);
    } catch (error) {
      toast(`Erro: ${error.message}`, "err");
      button.disabled = false;
      button.textContent = "↻ Reconsultar lojas";
    }
  });
}

function closeModal() {
  $("#modal").hidden = true;
}

/* --------------------------------------------------------------- ações topo */
async function refreshAll(button) {
  button.disabled = true;
  const original = button.textContent;
  button.textContent = "atualizando...";
  try {
    const view = await callState("refresh", { sources: selectedSources() });
    toast(view.detail || "Preços atualizados", "ok");
  } catch (error) {
    toast(`Erro: ${error.message}`, "err");
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

/* -------------------------------------------------- preferências + backup */
const PREFS_KEY = "banca-quadrinhos:v1:prefs";

function loadPrefs() {
  try {
    const raw = localStorage.getItem(PREFS_KEY);
    return raw ? JSON.parse(raw) : {};
  } catch {
    return {};
  }
}

function persistPrefs(patch = {}) {
  const next = {
    ...loadPrefs(),
    ...patch,
    sources_selected: [...state.selected],
    shelf_filter: state.filter,
    only_lowest: state.onlyLowest,
    updated_at: new Date().toISOString(),
  };
  try {
    localStorage.setItem(PREFS_KEY, JSON.stringify(next));
  } catch (error) {
    toast("Não foi possível salvar as preferências (armazenamento cheio?)", "warn");
  }
  return next;
}

function applyPreferences(prefs) {
  if (!prefs) return;
  if (prefs.shelf_filter !== undefined) {
    state.filter = prefs.shelf_filter || "";
    const filterInput = $("#filter-banca");
    if (filterInput) filterInput.value = state.filter;
  }
  if (prefs.only_lowest !== undefined) {
    state.onlyLowest = Boolean(prefs.only_lowest);
    const lowInput = $("#filter-lowest");
    if (lowInput) lowInput.checked = state.onlyLowest;
  }
  if (Array.isArray(prefs.sources_selected) && state.sources.length) {
    const enabled = new Set(state.sources.filter((s) => s.enabled).map((s) => s.name));
    const chosen = prefs.sources_selected.filter((name) => enabled.has(name));
    state.selected = new Set(chosen.length ? chosen : enabled);
    renderSources();
  }
}

function downloadJSON(obj, filename) {
  const blob = new Blob([JSON.stringify(obj, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

async function exportData() {
  const button = $("#btn-export");
  button.disabled = true;
  try {
    if (!state.store) await callState("sync");
    const data = { ...(state.store || { comics: [], alerts: [] }) };
    data.preferences = loadPrefs();
    const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-");
    downloadJSON(data, `omnigibi-${stamp}.json`);
    toast(`${(data.comics || []).length} quadrinho(s) exportado(s)`, "ok");
  } catch (error) {
    toast(`Erro ao exportar: ${error.message}`, "err");
  } finally {
    button.disabled = false;
  }
}

function exportComicCsv(comic) {
  const num = (value) =>
    value === null || value === undefined ? "" : Number(value).toFixed(2).replace(".", ",");
  const lines = ["data;loja;preco;preco_anterior;variacao;variacao_pct;desconto_pct;menor_ate_aqui;tipo"];
  for (const row of (comic.history || []).slice().reverse()) {
    lines.push(
      [
        dateTime(row.recorded_at),
        row.source || "melhor preco",
        num(row.price),
        num(row.previous_price),
        num(row.delta),
        num(row.delta_pct),
        num(row.discount_pct),
        num(row.lowest_ever),
        row.change_type || "",
      ].join(";")
    );
  }
  const blob = new Blob(["\ufeff" + lines.join("\n")], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `historico-${comic.key || "quadrinho"}.csv`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

async function importData(file, applyPrefsFlag) {
  let data;
  try {
    data = JSON.parse(await file.text());
  } catch {
    toast("O arquivo não é um JSON válido", "err");
    return;
  }
  try {
    const view = await callState("import", { import_state: data });
    if (applyPrefsFlag && data.preferences) {
      localStorage.setItem(
        PREFS_KEY,
        JSON.stringify({ ...loadPrefs(), ...data.preferences })
      );
      applyPreferences(data.preferences);
    }
    toast(`Importado — ${view.detail}`, "ok");
  } catch (error) {
    toast(`Erro ao importar: ${error.message}`, "err");
  }
}

/* -------------------------------------------------------------------- init */
async function init() {
  try {
    state.sources = await api("/api/sources");
    state.sources.forEach((source) => source.enabled && state.selected.add(source.name));
    applyPreferences(loadPrefs());
    renderSources();
  } catch (error) {
    toast(`Não foi possível carregar as fontes: ${error.message}`, "err");
  }

  // Uma única chamada: o servidor calcula tudo a partir do estado do navegador.
  migrateLegacyBackup();
  try {
    await callState("sync");
  } catch (error) {
    toast(`Não foi possível carregar a prateleira: ${error.message}`, "err");
  }

  $("#search-form").addEventListener("submit", runSearch);

  $("#btn-refresh-all").addEventListener("click", (event) => refreshAll(event.currentTarget));
  const ackBtn = $("#btn-ack-alerts");
  if (ackBtn) ackBtn.addEventListener("click", ackAlerts);

  $("#btn-export").addEventListener("click", exportData);
  $("#btn-import").addEventListener("click", () => $("#import-file").click());
  $("#import-file").addEventListener("change", (event) => {
    const file = event.target.files && event.target.files[0];
    if (file) importData(file, $("#import-apply-prefs").checked);
    event.target.value = "";
  });

  $("#filter-banca").addEventListener("input", (event) => {
    state.filter = event.target.value.trim();
    persistPrefs();
    debounceBanca();
  });
  $("#filter-lowest").addEventListener("change", (event) => {
    state.onlyLowest = event.target.checked;
    persistPrefs();
    renderBanca();
  });

  $("#modal").addEventListener("click", (event) => {
    if (event.target.dataset.close !== undefined) closeModal();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeModal();
  });
  window.addEventListener("resize", () => {
    document.querySelectorAll("canvas.spark").forEach((canvas) => {
      const points = state.spark.get(Number(canvas.dataset.spark));
      if (points) drawSpark(canvas, points);
    });
    if (!$("#modal").hidden) {
      const canvas = document.getElementById("detail-chart");
      if (canvas) drawChart(canvas, canvas.__points || []);
    }
  });
}

let bancaTimer = null;
function debounceBanca() {
  clearTimeout(bancaTimer);
  bancaTimer = setTimeout(renderBanca, 200);
}

document.addEventListener("DOMContentLoaded", init);
