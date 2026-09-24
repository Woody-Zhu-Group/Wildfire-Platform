/**
 * Collapsible agent ask panel on the Wildfire & Outage Map tab.
 * Single exchange, SSE progress trail, structured answer rendering.
 */
(() => {
  const AgentApi = window.WildfireAgentApi;
  const MapBridge = () => window.WildfireHistoricalMap;

  const root = document.getElementById("historical-agent-panel");
  if (!root || !AgentApi) return;

  const toggleBtn = document.getElementById("historical-agent-toggle");
  const closeBtn = document.getElementById("historical-agent-close");
  const bannerEl = document.getElementById("historical-agent-banner");
  const formEl = document.getElementById("historical-agent-form");
  const inputEl = document.getElementById("historical-agent-question");
  const askBtn = document.getElementById("historical-agent-ask");
  const cancelBtn = document.getElementById("historical-agent-cancel");
  const downloadBtn = document.getElementById("historical-agent-download");
  const trailEl = document.getElementById("historical-agent-trail");
  const resultEl = document.getElementById("historical-agent-result");
  const layoutEl = document.querySelector(".sfps-historical-map-row");

  let abortController = null;
  let agentAvailable = false;
  let lastMapNote = null;
  let lastDownload = null;

  const setBanner = (message, isError) => {
    if (!bannerEl) return;
    if (!message) {
      bannerEl.hidden = true;
      bannerEl.textContent = "";
      bannerEl.classList.remove("is-error", "is-info");
      return;
    }
    bannerEl.hidden = false;
    bannerEl.textContent = message;
    bannerEl.classList.toggle("is-error", Boolean(isError));
    bannerEl.classList.toggle("is-info", !isError);
  };

  const setOpen = (open) => {
    root.classList.toggle("is-open", open);
    root.setAttribute("aria-hidden", open ? "false" : "true");
    if (toggleBtn) {
      toggleBtn.setAttribute("aria-expanded", open ? "true" : "false");
      toggleBtn.classList.toggle("is-active", open);
    }
    if (layoutEl) layoutEl.classList.toggle("agent-open", open);
    const map = MapBridge();
    if (map && typeof map.relayoutCanvas === "function") {
      requestAnimationFrame(() => map.relayoutCanvas(open ? "ask-open" : "ask-close"));
      setTimeout(() => map.relayoutCanvas(open ? "ask-open:settle" : "ask-close:settle"), 180);
    } else if (map && typeof map.invalidateSize === "function") {
      setTimeout(() => map.invalidateSize(), 180);
    }
    if (open) probeHealth();
  };

  async function probeHealth() {
    try {
      const health = await AgentApi.health();
      agentAvailable = true;
      const modelUp = Boolean(health?.model?.available);
      if (!modelUp) {
        setBanner(
          "Counts, maps, and rankings work now; open-ended questions need the language model, which is unavailable.",
          false
        );
        if (askBtn) askBtn.disabled = Boolean(abortController);
        if (inputEl) inputEl.disabled = Boolean(abortController);
        return;
      }
      const degraded = health.status !== "ok";
      setBanner(
        degraded
          ? `Agent reachable at ${AgentApi.apiBase()} (some backends degraded).`
          : "",
        false
      );
      if (askBtn) askBtn.disabled = Boolean(abortController);
      if (inputEl) inputEl.disabled = Boolean(abortController);
    } catch (error) {
      agentAvailable = false;
      setBanner(
        `Agent unavailable at ${AgentApi.apiBase()}. Start it with: uvicorn services.agent.app:app --port 8004 --app-dir . Map still works.`,
        true
      );
      if (askBtn) askBtn.disabled = true;
      if (inputEl) inputEl.disabled = true;
    }
  }

  const clearExchange = () => {
    if (trailEl) trailEl.innerHTML = "";
    if (resultEl) resultEl.innerHTML = "";
    lastMapNote = null;
    lastDownload = null;
    if (downloadBtn) downloadBtn.hidden = true;
  };

  const appendTrail = (title, detail, kind) => {
    if (!trailEl) return;
    const item = document.createElement("li");
    item.className = `sfps-agent-trail-item${kind ? ` is-${kind}` : ""}`;
    const titleEl = document.createElement("div");
    titleEl.className = "sfps-agent-trail-title";
    titleEl.textContent = title;
    item.appendChild(titleEl);
    if (detail) {
      const detailEl = document.createElement("div");
      detailEl.className = "sfps-agent-trail-detail";
      detailEl.textContent = detail;
      item.appendChild(detailEl);
    }
    trailEl.appendChild(item);
    trailEl.scrollTop = trailEl.scrollHeight;
  };

  const humanTool = (tool) =>
    ({
      data_query_records: "Data query",
      data_query_rank: "Ranking",
      data_query_spatial: "Spatial query",
      visualization_create: "Visualization",
      visualization_inspect: "Inspect",
      risk_forecast: "Risk forecast",
      comparison_run: "Comparison",
    }[tool] || tool);

  const argChips = (args) => {
    if (!args || typeof args !== "object") return "";
    const keys = [
      "dataset",
      "kind",
      "year",
      "utility",
      "utilities",
      "county",
      "interval",
      "result_mode",
      "group_by",
      "metric",
      "cell_id",
      "date",
    ];
    const parts = [];
    keys.forEach((key) => {
      const value = args[key];
      if (value === undefined || value === null || value === "") return;
      parts.push(`${key}=${Array.isArray(value) ? value.join(",") : value}`);
    });
    return parts.join(" · ");
  };

  const formatTrailRisk = (value) => {
    const n = Number(value);
    if (!Number.isFinite(n)) return String(value);
    if (n === 0) return "0";
    if (Math.abs(n) >= 0.01) return n.toFixed(4);
    return n.toExponential(2);
  };

  const summarizeResult = (summary) => {
    if (!summary || typeof summary !== "object") return "Done";
    if (summary.total != null) return `${Number(summary.total).toLocaleString()} records`;
    if (summary.total_events != null) {
      return `${Number(summary.total_events).toLocaleString()} events · ${summary.bucket_count || "?"} buckets`;
    }
    if (summary.counts && typeof summary.counts === "object") {
      return Object.entries(summary.counts)
        .map(([k, v]) => `${k}: ${Number(v).toLocaleString()}`)
        .join(" · ");
    }
    if (summary.kind === "map") {
      return `Map layer · ${Number(summary.total || 0).toLocaleString()} features`;
    }
    if (summary.risk != null) {
      const risk = formatTrailRisk(summary.risk);
      if (summary.cell_id != null && summary.cell_id !== "") {
        return `Risk ${risk} (cell ${summary.cell_id})`;
      }
      const cells = Number(summary.cell_count);
      if (Number.isFinite(cells) && cells > 0) {
        return `Risk ${risk} · ${cells} cell${cells === 1 ? "" : "s"}`;
      }
      return `Risk ${risk}`;
    }
    if (Array.isArray(summary.results)) {
      return summary.results
        .map((row) => {
          const label = row.key || row.utility || row.region || row.label || "?";
          if (row.value == null) return `${label}: unavailable`;
          return `${label}: ${Number(row.value).toLocaleString()}`;
        })
        .join(" · ");
    }
    if (summary.kind) return String(summary.kind);
    return "Done";
  };

  const originBadge = (route, status) => {
    const origin = route?.answer_origin;
    if (origin === "synthesis_fallback" || route?.synthesis_fallback) {
      return {
        label: "Tool summary fallback",
        className: "is-fallback",
        title:
          "Tools succeeded, but the model did not compose a grounded answer. The harness rendered the tool summary instead.",
      };
    }
    if (origin === "deterministic" || route?.path === "deterministic") {
      return {
        label: "Deterministic",
        className: "is-deterministic",
        title: "Answered by fixed routing rules without model tool selection.",
      };
    }
    if (status === "clarification") {
      return { label: "Clarification", className: "is-clarify", title: route?.reason || "" };
    }
    if (status === "unsupported") {
      return { label: "Unsupported", className: "is-unsupported", title: route?.reason || "" };
    }
    if (status === "error") {
      return { label: "Error", className: "is-error", title: route?.reason || "" };
    }
    return {
      label: "Model",
      className: "is-model",
      title: "Model selected tools; answer was grounded from tool evidence.",
    };
  };

  const csvEscapeCell = (value) => {
    if (value === undefined || value === null) return "";
    if (typeof value === "object") {
      try {
        value = JSON.stringify(value);
      } catch {
        value = String(value);
      }
    }
    const str = String(value);
    if (/[",\n\r]/.test(str)) return `"${str.replace(/"/g, '""')}"`;
    return str;
  };

  const objectsToCsv = (rows) => {
    const keys = [];
    const seen = new Set();
    rows.forEach((row) => {
      Object.keys(row || {}).forEach((key) => {
        if (seen.has(key)) return;
        seen.add(key);
        keys.push(key);
      });
    });
    const lines = [keys.map(csvEscapeCell).join(",")];
    rows.forEach((row) => {
      lines.push(keys.map((key) => csvEscapeCell(row ? row[key] : "")).join(","));
    });
    return `${lines.join("\n")}\n`;
  };

  const triggerCsvDownload = (filename, csv) => {
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  const flattenPointSummary = (summary) => {
    const iou = summary.iou && typeof summary.iou === "object" ? summary.iou : {};
    const cell =
      summary.grid_cell && typeof summary.grid_cell === "object" ? summary.grid_cell : {};
    return {
      lat: summary.lat,
      lon: summary.lon,
      county: summary.county ?? summary.metadata?.county ?? null,
      iou: iou.utility || summary.iou || null,
      hftd_tier: summary.hftd_tier,
      cell_id: cell.cell_id || summary.grid_cell || null,
    };
  };

  const rowsFromSummary = (summary) => {
    if (!summary || typeof summary !== "object") return [];
    if (summary.result_mode === "records" && Array.isArray(summary.records)) {
      return summary.records;
    }
    if (Array.isArray(summary.results) && summary.results.length) return summary.results;
    if (Array.isArray(summary.top_buckets) && summary.top_buckets.length) {
      return summary.top_buckets;
    }
    if (summary.kind === "point") return [flattenPointSummary(summary)];
    if (summary.kind === "summary" && summary.counts && typeof summary.counts === "object") {
      return Object.entries(summary.counts).map(([metric, count]) => ({ metric, count }));
    }
    if (summary.kind === "periods") {
      return [
        { period: "a", ...(summary.period_a || {}) },
        { period: "b", ...(summary.period_b || {}) },
        { period: "delta", ...(summary.delta || {}) },
      ];
    }
    if (summary.risk != null) {
      return [
        {
          cell_id: summary.cell_id,
          date: summary.date,
          risk: summary.risk,
        },
      ];
    }
    return [];
  };

  const rowsFromArtifactPayload = (payload) => {
    if (!payload || typeof payload !== "object") return [];
    if (Array.isArray(payload.data) && payload.data.length) return payload.data;
    if (Array.isArray(payload.results) && payload.results.length) return payload.results;
    if (Array.isArray(payload.buckets) && payload.buckets.length) return payload.buckets;
    const features = payload.geojson?.features;
    if (Array.isArray(features) && features.length) {
      return features.map((feature) => {
        const props = { ...(feature.properties || {}) };
        const coords = feature.geometry?.coordinates;
        if (feature.geometry?.type === "Point" && Array.isArray(coords) && coords.length >= 2) {
          props.lon = coords[0];
          props.lat = coords[1];
        }
        return props;
      });
    }
    if (payload.counts && typeof payload.counts === "object") {
      return Object.entries(payload.counts).map(([metric, count]) => ({ metric, count }));
    }
    if (payload.lat != null && payload.lon != null) {
      return [flattenPointSummary(payload)];
    }
    if (payload.period_a && payload.period_b) {
      return [
        { period: "a", ...(payload.period_a || {}) },
        { period: "b", ...(payload.period_b || {}) },
        { period: "delta", ...(payload.delta || {}) },
      ];
    }
    if (payload.risk != null || payload.log_lambda != null) {
      return [
        {
          cell_id: payload.cell_id,
          date: payload.date,
          risk: payload.risk ?? payload.log_lambda,
        },
      ];
    }
    return [];
  };

  const hasExportableRows = (payload) => {
    const evidence = (payload.evidence || []).filter((ev) => !ev.qualification_call);
    return evidence.some((ev) => {
      const summary = ev.summary || {};
      // Count tools still attach one illustrative row; that is not a table.
      if (summary.result_mode === "count") return false;
      if (summary.result_mode === "records") {
        return (
          Number(summary.returned) > 0 ||
          (Array.isArray(summary.records) && summary.records.length > 0)
        );
      }
      if (summary.kind === "map") return Number(summary.total) > 0;
      if (summary.kind === "time_series") return Number(summary.bucket_count) > 0;
      return rowsFromSummary(summary).length > 0;
    });
  };

  const setDownloadAvailability = (payload) => {
    const tabular = payload && payload.status === "answer" && hasExportableRows(payload);
    lastDownload = tabular
      ? {
          artifacts: payload.artifacts || [],
          evidence: (payload.evidence || []).filter((ev) => !ev.qualification_call),
        }
      : null;
    if (downloadBtn) downloadBtn.hidden = !lastDownload;
  };

  const downloadAgentCsv = async () => {
    if (!lastDownload) return;
    const tagged = [];
    const artifacts = lastDownload.artifacts || [];
    for (let i = 0; i < artifacts.length; i += 1) {
      const item = artifacts[i];
      const ref = item?.ref;
      if (!ref || typeof AgentApi.getArtifact !== "function") continue;
      try {
        const art = await AgentApi.getArtifact(ref);
        const rows = rowsFromArtifactPayload(art.payload);
        if (rows.length) {
          tagged.push(
            ...rows.map((row) => ({
              source: art.kind || item.kind || `artifact_${i + 1}`,
              ...row,
            }))
          );
        }
      } catch (error) {
        console.warn("Agent artifact download failed:", error);
      }
    }
    if (!tagged.length) {
      lastDownload.evidence.forEach((ev, idx) => {
        const rows = rowsFromSummary(ev.summary || {});
        rows.forEach((row) => {
          tagged.push({ source: ev.tool || `evidence_${idx + 1}`, ...row });
        });
      });
    }
    if (!tagged.length) return;
    const stamp = new Date().toISOString().slice(0, 10);
    triggerCsvDownload(`agent-query-${stamp}.csv`, objectsToCsv(tagged));
  };

  const stripQualificationTail = (text) => {
    const marker = "\n\nQualifications:\n";
    const idx = text.indexOf(marker);
    if (idx === -1) return text;
    return text.slice(0, idx).trim();
  };

  const renderResult = (payload) => {
    if (!resultEl) return;
    resultEl.innerHTML = "";
    const status = payload.status || "error";
    const route = payload.route || {};
    const badge = originBadge(route, status);

    const card = document.createElement("article");
    card.className = `sfps-agent-result is-${status}`;

    const body = document.createElement("p");
    body.className = "sfps-agent-answer-text";
    body.textContent = stripQualificationTail(payload.answer_text || "");
    card.appendChild(body);

    const quals = payload.qualifications || [];
    if (quals.length) {
      const qualBox = document.createElement("section");
      qualBox.className = "sfps-agent-qualifications";
      const qualTitle = document.createElement("h4");
      qualTitle.textContent = "Qualifications";
      qualBox.appendChild(qualTitle);
      const list = document.createElement("ul");
      quals.forEach((q) => {
        const li = document.createElement("li");
        li.textContent = q.text || String(q);
        list.appendChild(li);
      });
      qualBox.appendChild(list);
      card.appendChild(qualBox);
    }

    if (lastMapNote) {
      const note = document.createElement("p");
      note.className = "sfps-agent-map-note";
      note.textContent = lastMapNote;
      card.appendChild(note);
    }

    const audit = document.createElement("details");
    audit.className = "sfps-agent-audit";
    const summary = document.createElement("summary");
    summary.textContent = "How this was answered";
    audit.appendChild(summary);
    const auditBody = document.createElement("div");
    auditBody.className = "sfps-agent-audit-body";
    const originLine = document.createElement("p");
    originLine.textContent = `Origin: ${badge.label}${
      status && status !== "answer" ? ` · ${status}` : ""
    }`;
    if (badge.title) originLine.title = badge.title;
    auditBody.appendChild(originLine);
    const pathLine = document.createElement("p");
    pathLine.textContent = `Route: ${route.path || "?"} (${route.rule || "-"})`;
    auditBody.appendChild(pathLine);
    if (route.reason) {
      const reason = document.createElement("p");
      reason.textContent = route.reason;
      auditBody.appendChild(reason);
    }
    const slots = route.slot_resolution || {};
    const timeSlot = slots.time_resolution || {};
    if (timeSlot.phrase || timeSlot.year != null || (slots.years || []).length) {
      const resolved = document.createElement("p");
      const yearPart =
        timeSlot.year != null
          ? String(timeSlot.year)
          : (slots.years || []).join(", ") || "—";
      const rangePart =
        slots.start_date && slots.end_date
          ? ` (${slots.start_date} → ${slots.end_date})`
          : "";
      resolved.textContent = `Harness time: ${timeSlot.phrase || "explicit"} → ${yearPart}${rangePart}`;
      auditBody.appendChild(resolved);
    }
    if (route.synthesis_fallback) {
      const fb = document.createElement("p");
      fb.textContent =
        "Synthesis fallback: tools succeeded; harness rendered the tool summary because the model did not produce a valid grounded answer.";
      auditBody.appendChild(fb);
    }
    const tools = (payload.evidence || []).filter((e) => !e.qualification_call);
    if (tools.length) {
      const ul = document.createElement("ul");
      tools.forEach((ev) => {
        const li = document.createElement("li");
        li.textContent = `${humanTool(ev.tool)}: ${argChips(ev.arguments) || summarizeResult(ev.summary)}`;
        ul.appendChild(li);
      });
      auditBody.appendChild(ul);
    }
    const riskEv = tools.find((ev) => ev.tool === "risk_forecast");
    const riskSummary = riskEv && riskEv.summary;
    if (riskSummary) {
      const method = document.createElement("p");
      const cells = riskSummary.cell_count != null ? riskSummary.cell_count : 1;
      const expected = Number(riskSummary.expected_count);
      const expectedText = Number.isFinite(expected)
        ? expected.toPrecision(3)
        : "—";
      const note =
        riskSummary.aggregation_note ||
        "1 - exp(-sum(lambda)); independent Poisson cells";
      const sample = [
        riskSummary.local_period,
        riskSummary.local_n != null ? `n=${riskSummary.local_n}` : "",
      ]
        .filter(Boolean)
        .join(", ");
      method.textContent = `Method: P(≥1 ignition) = ${note}. ${cells} cell${
        Number(cells) === 1 ? "" : "s"
      }. expected_count=${expectedText}.${sample ? ` Local sample ${sample}.` : ""}`;
      auditBody.appendChild(method);
    }
    audit.appendChild(auditBody);
    card.appendChild(audit);

    resultEl.appendChild(card);
    setDownloadAvailability(payload);
  };

  const applyCanvasViews = (payload) => {
    const bridge = MapBridge();
    if (!bridge) return;
    const views = payload.views || [];
    const mapSpec = views.find((item) => item.type === "map");
    const seriesSpec = views.find((item) => item.type === "time_series");
    const comparisonSpec = views.find((item) => item.type === "comparison");
    const recordsSpec = views.find((item) => item.type === "record_table");
    const spatialSpec = views.find((item) => item.type === "spatial_context");
    const stats = views.filter((item) => item.type === "stat_card");
    if (typeof bridge.applyCanvasViews === "function") {
      Promise.resolve(bridge.applyCanvasViews(payload)).catch((error) =>
        console.warn("Canvas views failed:", error)
      );
    } else if (payload.status === "answer" && mapSpec && typeof bridge.setMapParams === "function") {
      Promise.resolve(
        bridge.setMapParams(mapSpec.params || {}, payload.view_scope || null)
      ).catch((error) => console.warn("Map setParams failed:", error));
    } else if (typeof bridge.notifyAgentViews === "function") {
      bridge.notifyAgentViews({
        views,
        view_status: payload.view_status,
        view_scope: payload.view_scope,
        status: payload.status,
      });
    }
    if (payload.status === "answer" && mapSpec) {
      lastMapNote = seriesSpec
        ? "Map and asked series are showing this answer."
        : comparisonSpec
          ? "Map and comparison are showing this answer."
          : recordsSpec
            ? "Map and records are showing this answer."
            : spatialSpec
              ? "Map and location are showing this answer."
              : stats.length
                ? "Map and count are showing this answer."
                : "Map is showing this answer (year / layers / utility / county / extent).";
    } else if (payload.status === "answer" && seriesSpec) {
      lastMapNote = stats.length
        ? "This answer is a summary plus the asked series. Use Show map on the canvas to return to the map."
        : "Asked series is on the canvas. Use Show map to return to the map.";
    } else if (payload.status === "answer" && comparisonSpec) {
      lastMapNote = "Comparison is on the canvas. Use Show map to return to the map.";
    } else if (payload.status === "answer" && recordsSpec) {
      lastMapNote = "Records are on the canvas. Use Show map to return to the map.";
    } else if (payload.status === "answer" && spatialSpec) {
      lastMapNote = stats.length
        ? "Location and summary are on the canvas. Use Show map to return to the map."
        : "Location is on the canvas. Use Show map to return to the map.";
    } else if (payload.status === "answer" && views.length && !mapSpec) {
      lastMapNote = stats.length
        ? "This answer is a summary. Use Show map on the canvas to return to the map."
        : "Map was left as you left it (this answer has no map view).";
    } else {
      lastMapNote = null;
    }
  };

  const setBusy = (busy) => {
    if (askBtn) askBtn.disabled = busy || !agentAvailable;
    if (inputEl) inputEl.disabled = busy || !agentAvailable;
    if (cancelBtn) cancelBtn.hidden = !busy;
    const bridge = MapBridge();
    if (bridge && typeof bridge.setAskBusy === "function") {
      bridge.setAskBusy(busy);
    }
  };

  const handleEvent = (name, data, collected) => {
    collected.push({ name, data });
    if (name === "routing") {
      const slow = data.expect_slow
        ? "Model path: may take up to a minute on CPU"
        : "Deterministic path: usually under a second";
      appendTrail(
        `Routing: ${data.path}`,
        `${data.reason || data.rule || ""} · ${slow}`,
        data.path === "model" ? "model" : "det"
      );
      return;
    }
    if (name === "tool_call") {
      appendTrail(
        `Calling ${humanTool(data.tool)}`,
        argChips(data.arguments) || `attempt ${data.attempt || 1}`,
        "tool"
      );
      return;
    }
    if (name === "tool_result") {
      appendTrail(
        data.ok ? `${humanTool(data.tool)} result` : `${humanTool(data.tool)} failed`,
        data.ok
          ? summarizeResult(data.summary)
          : data.error?.message || data.error?.code || "error",
        data.ok ? "ok" : "err"
      );
      return;
    }
    if (name === "retry") {
      appendTrail("Retrying", data.reason || data.error_code || "recoverable error", "retry");
      return;
    }
    if (name === "synthesizing") {
      appendTrail("Synthesizing", data.reason || "Composing grounded answer", "synth");
      return;
    }
    if (name === "answer") {
      if (
        data.status === "answer" ||
        data.status === "clarification" ||
        data.status === "unsupported" ||
        data.status === "error" ||
        data.view_status
      ) {
        applyCanvasViews(data);
      } else {
        lastMapNote = null;
      }
      renderResult(data);
      return;
    }
    if (name === "error") {
      lastMapNote = null;
      applyCanvasViews({ ...data, status: data.status || "error" });
      renderResult(data);
    }
  };

  const humanizeNetworkError = (error) => {
    const raw = String(error?.message || error || "").trim();
    const lower = raw.toLowerCase();
    if (
      lower === "failed to fetch" ||
      lower === "networkerror when attempting to fetch resource" ||
      lower === "network error" ||
      lower.includes("load failed") ||
      lower.includes("networkerror")
    ) {
      return (
        "Could not reach the agent service. Confirm it is running on port 8004 " +
        "(uvicorn services.agent.app:app --port 8004 --app-dir .), that CORS " +
        "allows this page origin, and that the request was not blocked or cancelled."
      );
    }
    if (lower.startsWith("http 502") || lower.startsWith("http 503") || lower.startsWith("http 504")) {
      return (
        "The agent service returned a temporary upstream error. Retry in a moment; " +
        "if it persists, check that data_query (:8000) and sibling backends are up."
      );
    }
    if (lower.startsWith("http 404")) {
      return "Agent endpoint not found. Check WILDFIRE_AGENT_BASE points at the agent API.";
    }
    return raw || "Request failed";
  };

  const cancelAsk = () => {
    if (abortController) {
      abortController.abort();
      abortController = null;
    }
    setBusy(false);
    appendTrail("Cancelled", "Stopped the request. Map remains usable.", "cancel");
  };

  const submitAsk = async (event) => {
    event.preventDefault();
    if (!agentAvailable) {
      await probeHealth();
      if (!agentAvailable) return;
    }
    const question = String(inputEl?.value || "").trim();
    if (question.length < 2) return;

    clearExchange();
    abortController = new AbortController();
    setBusy(true);
    const collected = [];
    try {
      await AgentApi.askStream(question, {
        signal: abortController.signal,
        onEvent: (name, data) => handleEvent(name, data, collected),
      });
    } catch (error) {
      if (error?.name === "AbortError") {
        // cancelAsk already annotated the trail
      } else {
        const message = humanizeNetworkError(error);
        appendTrail("Error", message, "err");
        renderResult({
          status: "error",
          answer_text: message,
          route: { path: "error", answer_origin: "error" },
          qualifications: [],
          evidence: [],
        });
      }
    } finally {
      abortController = null;
      setBusy(false);
    }
  };

  if (toggleBtn) {
    toggleBtn.addEventListener("click", () => {
      setOpen(!root.classList.contains("is-open"));
    });
  }
  if (closeBtn) {
    closeBtn.addEventListener("click", () => setOpen(false));
  }
  if (cancelBtn) {
    cancelBtn.addEventListener("click", cancelAsk);
  }
  if (downloadBtn) {
    downloadBtn.addEventListener("click", () => {
      downloadAgentCsv().catch((error) => console.warn("CSV download failed:", error));
    });
  }
  if (formEl) {
    formEl.addEventListener("submit", submitAsk);
  }

  window.WildfireAgentPanel = { refreshHealth: probeHealth };

  // Degrade quietly until the panel is opened.
  setOpen(false);
  if (askBtn) askBtn.disabled = true;
})();
