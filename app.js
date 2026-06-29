let publications = [];
let currentSort = { key: "year", direction: "desc" };

const fmt = new Intl.NumberFormat("en-US");
const dateFmt = new Intl.DateTimeFormat("en-US", { year: "numeric", month: "short", day: "numeric" });

async function loadData() {
  const [pubsRes, metricsRes] = await Promise.all([
    fetch("data/publications.json", { cache: "no-store" }),
    fetch("data/metrics.json", { cache: "no-store" })
  ]);
  publications = await pubsRes.json();
  const metrics = await metricsRes.json();
  renderMetrics(metrics);
  renderYearChart(metrics.publications_by_year || {});
  renderTable(publications);
}

function renderMetrics(metrics) {
  document.getElementById("publicationCount").textContent = fmt.format(metrics.publication_count || 0);
  document.getElementById("totalCitations").textContent = fmt.format(metrics.total_citations || 0);
  document.getElementById("hIndex").textContent = fmt.format(metrics.h_index || 0);
  document.getElementById("updatedAt").textContent = metrics.updated_at ? dateFmt.format(new Date(metrics.updated_at)) : "—";
  document.getElementById("citationSource").textContent = metrics.citation_source || "";
}

function renderYearChart(byYear) {
  const chart = document.getElementById("yearChart");
  const rows = Object.entries(byYear).filter(([year]) => year !== "Unknown").sort(([a], [b]) => Number(a) - Number(b));
  const max = Math.max(1, ...rows.map(([, count]) => count));
  chart.innerHTML = rows.map(([year, count]) => `
    <div class="bar" title="${year}: ${count} publication${count === 1 ? "" : "s"}">
      <div style="height:${Math.max(4, (count / max) * 180)}px"></div>
      <span>${year}</span>
    </div>`).join("") || "<p>No publications yet. Run the scraper first.</p>";
}

function renderTable(rows) {
  const tbody = document.getElementById("pubTable");
  tbody.innerHTML = rows.map(pub => {
    const url = pub.landing_page_url || pub.openalex_url || "#";
    const doiText = pub.doi || "—";
    return `<tr>
      <td>${pub.year || "—"}</td>
      <td><a class="title" href="${url}" target="_blank" rel="noopener">${escapeHtml(pub.title || "Untitled")}</a></td>
      <td class="authors">${escapeHtml((pub.authors || []).slice(0, 8).join(", "))}${(pub.authors || []).length > 8 ? " et al." : ""}</td>
      <td>${escapeHtml(pub.journal_or_source || "—")}</td>
      <td>${fmt.format(pub.citation_count || 0)}</td>
      <td>${pub.doi ? `<a href="https://doi.org/${pub.doi}" target="_blank" rel="noopener">${escapeHtml(doiText)}</a>` : "—"}</td>
    </tr>`;
  }).join("");
}

function sortRows(rows) {
  const { key, direction } = currentSort;
  return [...rows].sort((a, b) => {
    const av = a[key] || 0;
    const bv = b[key] || 0;
    return direction === "asc" ? av - bv : bv - av;
  });
}

function filterAndRender() {
  const q = document.getElementById("searchBox").value.toLowerCase().trim();
  const filtered = publications.filter(pub => JSON.stringify(pub).toLowerCase().includes(q));
  renderTable(sortRows(filtered));
}

function escapeHtml(value) {
  return String(value).replace(/[&<>'"]/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;","\"":"&quot;"}[char]));
}

document.getElementById("searchBox").addEventListener("input", filterAndRender);
document.querySelectorAll("th[data-sort]").forEach(th => {
  th.addEventListener("click", () => {
    const key = th.dataset.sort;
    currentSort = currentSort.key === key
      ? { key, direction: currentSort.direction === "asc" ? "desc" : "asc" }
      : { key, direction: "desc" };
    filterAndRender();
  });
});

loadData().catch(err => {
  console.error(err);
  document.querySelector("main").insertAdjacentHTML("afterbegin", `<p class="panel">Could not load dashboard data. Run <code>python scripts/scrape_openalex.py</code> first.</p>`);
});
