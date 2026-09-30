const typesRoot = document.querySelector("#types");
const tickersRoot = document.querySelector("#tickers");
const searchInput = document.querySelector("#search");
const tickerCount = document.querySelector("#ticker-count");
const choice = document.querySelector("#choice");
const syncButton = document.querySelector("#sync");
const summary = document.querySelector("#summary");
const dryRun = document.querySelector("#dry-run");

let options = { symbols: [], event_types: [], dry_run: false, period: "" };

searchInput.addEventListener("input", applySearch);
document.querySelector("#select-visible").addEventListener("click", () => setVisible(true));
document.querySelector("#clear-visible").addEventListener("click", () => setVisible(false));
typesRoot.addEventListener("change", updateChoice);
tickersRoot.addEventListener("change", updateChoice);
syncButton.addEventListener("click", submitSelection);

loadOptions();

async function loadOptions() {
  const response = await fetch("/api/options");
  options = await response.json();
  summary.textContent = options.dry_run
    ? `Preview which ${options.period} events would be synced.`
    : `Sync ${options.period} events to the calendar you choose below. Events from this sync that you leave unchecked are removed from that calendar.`;
  const configured = document.querySelector("#configured-id");
  if (configured && options.configured_calendar_id) {
    configured.textContent = options.configured_calendar_id;
  }
  dryRun.hidden = !options.dry_run;
  syncButton.textContent = options.dry_run ? "Preview sync" : "Sync to calendar";
  renderTypes(options.event_types);
  renderTickers(options.symbols);
  syncButton.disabled = false;
  applySearch();
  updateChoice();
}

function renderTypes(eventTypes) {
  typesRoot.replaceChildren(
    ...eventTypes.map((item) => {
      const label = document.createElement("label");
      const input = document.createElement("input");
      input.type = "checkbox";
      input.value = item.type;
      input.checked = true;
      const text = document.createElement("span");
      text.append(item.type);
      const count = document.createElement("small");
      count.textContent = `${item.count} events`;
      text.append(count);
      label.append(input, text);
      return label;
    })
  );
}

function renderTickers(symbols) {
  tickersRoot.replaceChildren(
    ...symbols.map((item) => {
      const label = document.createElement("label");
      const input = document.createElement("input");
      input.type = "checkbox";
      input.value = item.symbol;
      const text = document.createElement("span");
      text.append(item.symbol);
      const count = document.createElement("small");
      count.textContent = `${item.count} events`;
      text.append(count);
      label.append(input, text);
      return label;
    })
  );
}

function applySearch() {
  const query = searchInput.value.trim().toLowerCase();
  let shown = 0;
  for (const label of tickersRoot.querySelectorAll("label")) {
    const symbol = label.querySelector("input").value.toLowerCase();
    const visible = symbol.includes(query);
    label.hidden = !visible;
    if (visible) {
      shown += 1;
    }
  }
  const total = options.symbols.length;
  tickerCount.textContent = query
    ? `${shown} of ${total} tickers match “${searchInput.value.trim()}”`
    : `${total} tickers`;
}

function setVisible(checked) {
  for (const label of tickersRoot.querySelectorAll("label")) {
    if (!label.hidden) {
      label.querySelector("input").checked = checked;
    }
  }
  updateChoice();
}

function selectedValues(root) {
  return [...root.querySelectorAll("input:checked")].map((input) => input.value);
}

function updateChoice() {
  const count = selectedValues(tickersRoot).length;
  choice.textContent = count === 1 ? "1 ticker selected" : `${count} tickers selected`;
}

async function submitSelection() {
  const symbols = selectedValues(tickersRoot);
  const eventTypes = selectedValues(typesRoot);
  if (symbols.length === 0) {
    const proceed = window.confirm(
      "No tickers are selected. Company events in this period will be removed from the calendar. Continue?"
    );
    if (!proceed) {
      return;
    }
  }

  const calendarTarget =
    document.querySelector('input[name="calendar-target"]:checked')?.value || "owned";

  syncButton.disabled = true;
  const response = await fetch("/api/selection", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      symbols,
      event_types: eventTypes,
      calendar_target: calendarTarget,
    }),
  });
  if (!response.ok) {
    syncButton.disabled = false;
    choice.textContent = "Could not save the selection. Try again.";
    return;
  }

  choice.textContent = options.dry_run
    ? "Preview started. You can close this page."
    : "Sync started. You can close this page.";
  syncButton.textContent = "Submitted";
}
