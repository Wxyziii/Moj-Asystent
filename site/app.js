const statusLabels = {
  planned: "Planowane",
  in_progress: "W toku",
  blocked: "Zablokowane",
  done: "Gotowe"
};

async function loadProject() {
  const response = await fetch("./data/project.json", { cache: "no-store" });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}

function render(data) {
  const { project, principles, stack, milestones, changelog } = data;

  document.title = `${project.name} — Development`;
  document.getElementById("brand-name").textContent = project.name;
  document.getElementById("hero-stage").textContent = project.stage;
  document.getElementById("hero-tagline").textContent = project.tagline;
  document.getElementById("current-status").textContent = project.status;
  document.getElementById("updated-label").textContent = `Aktualizacja: ${project.updated}`;

  const done = milestones.filter((item) => item.status === "done").length;
  const percent = milestones.length ? Math.round((done / milestones.length) * 100) : 0;
  document.getElementById("progress-number").textContent = `${percent}%`;
  document.getElementById("milestone-summary").textContent = `${done} / ${milestones.length} ukończonych`;
  document.getElementById("progress-ring").style.setProperty("--progress", `${percent * 3.6}deg`);

  document.getElementById("principles").innerHTML = principles
    .map((item) => `<span class="principle">${escapeHtml(item)}</span>`)
    .join("");

  document.getElementById("roadmap-list").innerHTML = milestones
    .map((item) => `
      <article class="roadmap-item">
        <span class="roadmap-number">${String(item.id).padStart(2, "0")}</span>
        <span class="roadmap-name">${escapeHtml(item.name)}</span>
        <span class="roadmap-description">${escapeHtml(item.description)}</span>
        <span class="badge ${item.status}">${statusLabels[item.status] || escapeHtml(item.status)}</span>
      </article>
    `)
    .join("");

  document.getElementById("stack-list").innerHTML = stack
    .map((item) => `
      <div class="stack-row">
        <span>${escapeHtml(item.label)}</span>
        <span>${escapeHtml(item.value)}</span>
      </div>
    `)
    .join("");

  document.getElementById("changelog-list").innerHTML = changelog
    .map((entry) => `
      <article class="changelog-entry">
        <span class="changelog-date">${escapeHtml(entry.date)}</span>
        <h3>${escapeHtml(entry.title)}</h3>
        <ul>${entry.items.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>
      </article>
    `)
    .join("");
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

loadProject()
  .then(render)
  .catch((error) => {
    console.error("Failed to load project data", error);
    const status = document.getElementById("current-status");
    status.textContent = "Nie udało się wczytać danych projektu.";
    document.getElementById("roadmap-list").innerHTML = '<div class="loading-error">Sprawdź plik <code>site/data/project.json</code>.</div>';
  });
