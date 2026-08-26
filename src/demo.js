/* ===========================================================================
   Recorded demo. Replays fixtures captured from a live run; makes no network
   request of its own. The briefs are the real generated artifacts, loaded in
   an iframe exactly as a technician would receive them.
   =========================================================================== */

(function () {
  "use strict";

  var F = window.BRIEFCASE_FIXTURES;
  var buttons = document.getElementById("caseButtons");
  var flow = document.getElementById("flow");

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  function fieldRow(label, value, confidence) {
    var tr = el("tr");
    tr.appendChild(el("th", null, label));
    var td = el("td");
    if (value === null || value === "") {
      td.appendChild(el("span", "null-val", "not readable"));
    } else {
      td.appendChild(document.createTextNode(value));
    }
    if (confidence) {
      td.appendChild(document.createTextNode(" "));
      td.appendChild(el("span", "badge badge-" + confidence, confidence));
    }
    tr.appendChild(td);
    return tr;
  }

  function renderCase(c) {
    flow.textContent = "";

    /* --- 1. what the owner sends ------------------------------------- */
    var s1 = el("section", "step");
    s1.appendChild(el("p", "step-label", "Step 1"));
    s1.appendChild(el("h3", null, "What the owner sends"));
    var img = el("img", "plate-img");
    img.src = c.plate;
    img.alt = "Photograph of an equipment data plate";
    img.loading = "lazy";
    s1.appendChild(img);
    var q = el("p", "symptom-quote");
    q.appendChild(el("strong", null, "Symptom: "));
    q.appendChild(document.createTextNode('"' + c.symptom + '"'));
    s1.appendChild(q);
    flow.appendChild(s1);

    /* --- 2. what the plate read returned ------------------------------ */
    var s2 = el("section", "step");
    s2.appendChild(el("p", "step-label", "Step 2"));
    s2.appendChild(el("h3", null, "What the photo gave back"));

    if (c.extraction) {
      var x = c.extraction, cf = x.confidence || {};
      var table = el("table", "fields");
      var tbody = el("tbody");
      tbody.appendChild(fieldRow("Manufacturer", x.manufacturer, cf.manufacturer));
      tbody.appendChild(fieldRow("Model", x.model, cf.model));
      tbody.appendChild(fieldRow("Serial", x.serial, cf.serial));
      tbody.appendChild(fieldRow("Manufacture date", x.manufacture_date, cf.manufacture_date));
      table.appendChild(tbody);
      s2.appendChild(table);

      var unreadable = Object.keys(cf).filter(function (k) { return cf[k] === "unreadable"; });
      s2.appendChild(el("p", "step-note", unreadable.length
        ? "The photo was too poor to read " + unreadable.length + " of the 4 fields. Those come back empty rather than guessed, and the owner types them in. Refusing to complete a half legible model number from memory is the whole point: a confident wrong model number sends a technician after the wrong part."
        : "All four fields legible. The owner still confirms them before anything is searched, because a corrected digit here is cheaper than a wasted search."));
    } else {
      s2.appendChild(el("p", "step-note",
        "Entered by hand for this case, so the search step is what is being shown rather than the photo read."));
      var t2 = el("table", "fields");
      var tb2 = el("tbody");
      tb2.appendChild(fieldRow("Manufacturer", c.brief.identity.manufacturer, null));
      tb2.appendChild(fieldRow("Model", c.brief.identity.model, null));
      t2.appendChild(tb2);
      s2.appendChild(t2);
    }
    flow.appendChild(s2);

    /* --- 3. the brief, or the deliberate stop ------------------------- */
    if (!c.brief) {
      var stop = el("section", "step");
      stop.appendChild(el("p", "step-label", "Step 3"));
      var sh = el("h3", null, "What the technician receives");
      sh.appendChild(el("span", "verdict verdict-nra", "Nothing yet"));
      stop.appendChild(sh);
      stop.appendChild(el("p", "step-note", c.stops_here));
      flow.appendChild(stop);
      return;
    }

    var b = c.brief.brief;
    var nra = b.status === "no_reliable_answer";

    var s3 = el("section", "step");
    s3.appendChild(el("p", "step-label", "Step 3"));
    var h = el("h3", null, "What the technician receives");
    var v = el("span", "verdict " + (nra ? "verdict-nra" : "verdict-ok"),
      nra ? "No reliable answer" : b.candidates.length + " documented candidate" + (b.candidates.length === 1 ? "" : "s"));
    h.appendChild(v);
    s3.appendChild(h);

    s3.appendChild(el("p", "step-note", nra
      ? "This is the case that decides whether the product is trustworthy. The model number is invented, and the tool says so instead of assembling something plausible out of general hot tub advice it had already found."
      : b.observed_code
        ? 'A code was reported on the panel, so the tool stops widening. One confirmed candidate rather than a list of maybes is the difference between a technician who brings the part and one who brings a guess.'
        : "The symptom is vague, so the tool returns the documented possibilities rather than picking one, each labeled with why it surfaced and how good the source is."));

    var frame = el("iframe", "brief-frame");
    frame.src = "briefs/" + c.brief.share;
    frame.title = "The generated service brief";
    frame.loading = "lazy";
    s3.appendChild(frame);

    var actions = el("p", "brief-actions");
    var open = el("a", null, "Open this brief on its own →");
    open.href = "briefs/" + c.brief.share;
    open.target = "_blank";
    open.rel = "noreferrer";
    actions.appendChild(open);
    s3.appendChild(actions);

    var tiers = {};
    b.sources.forEach(function (s) { tiers[s.tier] = (tiers[s.tier] || 0) + 1; });
    var tierText = Object.keys(tiers).map(function (k) { return tiers[k] + " " + k; }).join(", ");

    var meta = el("p", "meta-row");
    [["Sources cited", tierText || "none"],
     ["Searches run", String(c.brief.usage.web_searches)],
     ["Recorded", F.recorded]].forEach(function (pair) {
      var span = el("span");
      span.appendChild(document.createTextNode(pair[0] + ": "));
      span.appendChild(el("b", null, pair[1]));
      meta.appendChild(span);
    });
    s3.appendChild(meta);
    flow.appendChild(s3);
  }

  F.cases.forEach(function (c, i) {
    var btn = el("button", "case-btn", c.label);
    btn.type = "button";
    btn.setAttribute("role", "tab");
    btn.setAttribute("aria-selected", i === 0 ? "true" : "false");
    btn.addEventListener("click", function () {
      Array.prototype.forEach.call(buttons.children, function (b) {
        b.setAttribute("aria-selected", "false");
      });
      btn.setAttribute("aria-selected", "true");
      renderCase(c);
    });
    buttons.appendChild(btn);
  });

  renderCase(F.cases[0]);
})();
