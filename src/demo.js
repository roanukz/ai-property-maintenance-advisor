/* ===========================================================================
   Recorded demo, version 2. Replays recorded runs from src/fixtures.js
   (window.ADVISOR_DEMO); makes no network request of its own. Every string is
   set with textContent. The briefs are the files the runs wrote, shown in a
   sandboxed frame exactly as a technician would receive them.
   =========================================================================== */

(function () {
  "use strict";

  var D = window.ADVISOR_DEMO;
  var buttons = document.getElementById("caseButtons");
  var flow = document.getElementById("flow");
  var summarySlot = document.getElementById("summary");

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }
  function add(parent, child) { parent.appendChild(child); return child; }
  function txt(parent, s) { parent.appendChild(document.createTextNode(s)); }
  function plural(n, one, many) { return n + " " + (n === 1 ? one : many); }
  /* Money for a business reader: US dollars to four places, the rounding the
     teardown uses too. The exact ledger value goes in a title and, where it
     matters, a caption. */
  function dollars(x) { return x == null ? "not recorded" : "$" + Number(x).toFixed(4); }
  function exactUsd(x) { return x == null ? "not recorded" : Number(x).toFixed(6) + " USD"; }
  function money(parent, x) {
    var s = add(parent, el("span", "money", dollars(x)));
    s.title = "Ledger value: " + exactUsd(x);
    return s;
  }
  function secs(v) { return v == null ? "not recorded" : Number(v).toFixed(1) + " seconds"; }
  function hostPath(url) {
    var m = /^https:\/\/([^\/?#]+)([^?#]*)/.exec(url || "");
    return m ? m[1] + (m[2] && m[2] !== "/" ? m[2] : "") : url;
  }
  function extLink(url, label) {
    var a = el("a", "ext", label || hostPath(url));
    a.href = url;
    a.target = "_blank";
    a.rel = "noreferrer";
    return a;
  }

  if (!D || !D.cases || !D.cases.length) {
    flow.appendChild(el("p", "step-note", "The recorded data did not load, so there is nothing to replay."));
    return;
  }

  var STAND_IN = D.data_status !== "rerun";
  var STAND_IN_NOTE = "stand-in data from a superseded build, not a result";

  /* The stand-in bar is stamped into the page at build time; this is a second
     line of defense in case the page and the data ever disagree. */
  if (STAND_IN && !document.querySelector(".standin-bar")) {
    var bar = el("div", "standin-bar");
    bar.setAttribute("role", "alert");
    var w = add(bar, el("div", "wrap"));
    add(add(w, el("p")), el("strong", null, D.stand_in_label || "STAND-IN DATA, DO NOT PUBLISH"));
    document.body.insertBefore(bar, document.body.firstChild);
  }

  var byId = {};
  D.cases.forEach(function (c) { byId[c.id] = c; });
  function byRole(role) { return D.cases.filter(function (x) { return x.role === role; })[0]; }
  function caseNum(c) { return String(D.cases.indexOf(c) + 1); }

  var ROUTE_NAMES = { research: "Research", graph: "Memory", history: "Property records", top_up: "Top up search" };
  var TIER_NAMES = { manufacturer: "Manufacturer", dealer: "Dealer", forum: "Forum" };
  var VERDICT_NAMES = {
    match: "matches a documented cause", no_match: "matches no documented cause",
    unsure: "could not tell whether a stored cause fits"
  };
  var MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
                "September", "October", "November", "December"];
  function longDate(iso) {
    var m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso || "");
    return m ? Number(m[3]) + " " + MONTHS[Number(m[2]) - 1] + " " + m[1] : (iso || "not recorded");
  }
  var NODE_NAMES = {
    read_plate: "Reading the plate", classifier: "Checking the symptom", research: "Research",
    synthesize: "Writing the brief", validate: "Checking the brief"
  };
  var REFUSAL = {
    model: "The model declined to answer. No rule forced it.",
    rules: "The tool's rules forced the refusal after checking the draft."
  };

  /* Live or replay, always said the same way (the replay label everywhere a
     replay case appears). */
  function isReplay(c) { return c.kind === "replay"; }
  function sourceLabel(c) {
    if (isReplay(c)) return D.replay_label;
    return c.kind_label || ("Live run " + c.run_id);
  }
  function runLabel(c) { return (isReplay(c) ? "replay run " : "live run ") + c.run_id; }
  function modelName(id) {
    var m = /^claude-([a-z]+)-(\d+)-(\d+)/.exec(id || "");
    if (!m) return id || "";
    return "Claude " + m[1].charAt(0).toUpperCase() + m[1].slice(1) + " " + m[2] + "." + m[3];
  }

  /* ---------------------------------------------------------------------- */
  /* Pieces shared by several steps                                          */
  /* ---------------------------------------------------------------------- */

  function chip(cls, text) { return el("span", "chip " + cls, text); }

  function kindChips(c, parent) {
    var row = add(parent, el("p", "chips"));
    if (isReplay(c)) {
      add(row, chip("chip-replay", D.replay_label));
    } else {
      add(row, chip("chip-live", sourceLabel(c)));
      if (c.records_label) add(row, chip("chip-synthetic", c.records_label));
      if (c.recorded) add(row, chip("chip-quiet", c.recorded));
    }
    if (STAND_IN) add(row, chip("chip-standin", "Stand-in data, not a result"));
    return row;
  }

  function badge(conf) { return el("span", "badge badge-" + conf, conf); }

  function fieldTable(caption, rows) {
    var table = el("table", "fields");
    add(table, el("caption", "sr-only", caption));
    var tbody = add(table, el("tbody"));
    rows.forEach(function (r) {
      var tr = add(tbody, el("tr"));
      var th = add(tr, el("th", null, r.label));
      th.scope = "row";
      var td = add(tr, el("td"));
      if (r.value == null || r.value === "") add(td, el("span", "null-val", r.empty || "not readable"));
      else txt(td, r.value);
      if (r.confidence) { txt(td, " "); add(td, badge(r.confidence)); }
    });
    return table;
  }

  function caseLink(targetId, label) {
    var a = el("a", "case-link", label);
    a.href = "#" + targetId;
    a.addEventListener("click", function (e) {
      e.preventDefault();
      select(targetId, true);
    });
    return a;
  }

  function verbatim(parent, label, text) {
    var fig = add(parent, el("figure", "verbatim"));
    add(fig, el("figcaption", null, label));
    add(fig, el("blockquote", null, text));
    return fig;
  }

  function disclose(parent, summary) {
    var det = add(parent, el("details", "disclose"));
    add(det, el("summary", null, summary));
    return det;
  }

  function paused(c) { var k = c.confirmation; return !!(k && k.paused && k.resumed); }

  /* ---------------------------------------------------------------------- */
  /* Steps                                                                   */
  /* ---------------------------------------------------------------------- */

  function stepAsk(c) {
    var a = c.ask;
    var same = a.same_as && byId[a.same_as];
    return {
      title: same ? "The same question, asked again" : "What the owner sends",
      body: function (s) {
        if (same) {
          var p = add(s, el("p", "step-note lead-note"));
          txt(p, "Same photo and the same symptom as ");
          add(p, caseLink(same.id, same.button + " (case " + caseNum(same) + ")"));
          txt(p, paused(c)
            ? ". The plate was read again and the owner confirmed again, because every photo run stops for confirmation."
            : ".");
        }
        if (a.plate) {
          var img = add(s, el("img", same ? "plate-img plate-small" : "plate-img"));
          img.src = a.plate;
          img.alt = "Photograph of the equipment data plate the owner sent";
          img.loading = "lazy";
        } else if (a.typed_identity) {
          var rows = [
            { label: "Manufacturer", value: a.typed_identity.manufacturer, empty: "not typed" },
            { label: "Model", value: a.typed_identity.model, empty: "not typed" }
          ];
          if (a.typed_identity.serial) rows.push({ label: "Serial", value: a.typed_identity.serial });
          add(s, fieldTable(c.records ? "The unit as its property record names it" : "The unit as typed for this case", rows));
          if (c.records) {
            add(s, el("p", "step-note", "No photo for this case. The unit was picked from the property's records, which are synthetic: seeded test rows with a real model name and an invented serial, dates and service history. The run itself was live."));
          } else if (paused(c)) {
            add(s, el("p", "step-note", "No photo for this case. The unit was typed by hand, so there was nothing to read, but the run still paused once to confirm the code it found in the symptom."));
          } else {
            add(s, el("p", "step-note", "No photo for this case. The unit was typed by hand, so there was nothing to read or confirm, and the run went straight to its route."));
          }
        }
        var q = add(s, el("p", "symptom-quote"));
        add(q, el("strong", null, "Symptom: "));
        txt(q, "“" + a.symptom + "”");
      }
    };
  }

  function stepPlate(c) {
    if (!c.plate_read) return null;
    var same = c.ask.same_as && byId[c.ask.same_as];
    var fields = c.plate_read.fields;
    var unreadable = fields.filter(function (f) { return f.confidence === "unreadable"; }).length;
    return {
      title: "What the plate reader read",
      body: function (s) {
        var holder = s;
        if (same) holder = disclose(s, "Show what the plate reader read this time");
        add(holder, fieldTable("Fields read from the plate, with the reader's confidence in each",
          fields.map(function (f) { return { label: f.label, value: f.value, confidence: f.confidence }; })));
        add(s, el("p", "step-note", unreadable
          ? "The photo was too poor to read " + unreadable + " of the " + fields.length + " fields. They come back marked unreadable instead of guessed. A confident wrong model number sends a technician after the wrong part."
          : "Each field comes back with the reader's own confidence. This is exactly what the reader returned, before the owner changed anything."));
      }
    };
  }

  function stepConfirm(c) {
    var k = c.confirmation;
    if (!k || !k.paused) return null;
    if (!k.resumed) {
      var n = c.numbers || {};
      var full = byRole("hot_tub_code_first");
      return {
        title: "Halted at the confirmation step",
        body: function (s) {
          var call = add(s, el("p", "callout callout-notice"));
          txt(call, "The run stopped here and is waiting for the owner to confirm the unit. ");
          if (n.searches === 0) txt(call, "Nothing was searched. ");
          txt(call, "It spent ");
          money(call, n.cost_usd);
          if (full && full !== c && full.numbers) {
            txt(call, ", against ");
            money(call, full.numbers.cost_usd);
            txt(call, " for the full hot tub run in ");
            add(call, caseLink(full.id, "case " + caseNum(full)));
          }
          txt(call, ".");
          var dl = add(s, el("dl", "facts"));
          [["Status", "paused, not finished"], ["Spent reading the photo", dollars(k.spent_before_pause_usd)],
           ["Searches", String(n.searches)], ["Search credits (Tavily)", String(n.tavily_credits)]]
            .forEach(function (p) { add(dl, el("dt", null, p[0])); add(dl, el("dd", null, p[1])); });
          add(s, el("p", "step-note", "Because the run paused instead of ending, it can be finished later with the owner's answer, under the same run ID."));
        }
      };
    }
    if (!k.from_photo) {
      return {
        title: "The confirmation pause",
        body: function (s) {
          add(s, el("p", "callout callout-info", "The run stopped here and spent nothing until the owner confirmed " +
            (k.confirmed_code ? "the code " + k.confirmed_code + ", which the tool had found in the symptom." : "the unit.")));
          var p = add(s, el("p", "step-note"));
          if (!k.spent_before_pause_usd) {
            txt(p, "Nothing was spent before the pause. ");
          } else {
            txt(p, "Spent before the pause: ");
            money(p, k.spent_before_pause_usd);
            txt(p, ". ");
          }
          txt(p, "The owner's answer resumed the run under the same run ID.");
        }
      };
    }
    return {
      title: "The confirmation pause",
      body: function (s) {
        add(s, el("p", "callout callout-info", "The run stopped here and spent nothing more until the owner confirmed the unit" +
          (k.confirmed_code ? " and the code." : ".")));
        var read = {};
        (c.plate_read ? c.plate_read.fields : []).forEach(function (f) { read[f.key] = f.value; });
        var ci = k.confirmed_identity || {};
        var table = add(s, el("table", "compare confirm-table"));
        add(table, el("caption", "sr-only", "What the plate reader read, next to what the owner confirmed"));
        var hr = add(add(table, el("thead")), el("tr"));
        ["Field", "Read from the photo", "Confirmed by the owner"].forEach(function (h) {
          var th = add(hr, el("th", null, h)); th.scope = "col";
        });
        var tb = add(table, el("tbody"));
        [["manufacturer", "Manufacturer"], ["model", "Model"], ["serial", "Serial"], ["manufacture_date", "Manufacture date"], ["code", "Code on the panel"]]
          .forEach(function (row) {
            var r = read[row[0]], v = row[0] === "code" ? k.confirmed_code : ci[row[0]];
            var tr = add(tb, el("tr"));
            var th = add(tr, el("th", null, row[1])); th.scope = "row";
            var a = add(tr, el("td", null, row[0] === "code" ? "not read from the photo" : (r == null ? "not readable" : r)));
            a.setAttribute("data-label", "Read from the photo");
            var b = add(tr, el("td", null, v == null ? "none" : v));
            b.setAttribute("data-label", "Confirmed by the owner");
            if (row[0] !== "code" && r != null && v != null && String(r).toLowerCase() !== String(v).toLowerCase()) {
              tr.className = "changed";
              txt(b, " ");
              add(b, el("span", "badge badge-low", "changed"));
            }
          });
        var p = add(s, el("p", "step-note"));
        txt(p, "Spent before the pause: ");
        money(p, k.spent_before_pause_usd);
        txt(p, ", the plate read alone. The run paused, then was finished later as the same run: the owner's answer resumed it under the same run ID.");
      }
    };
  }

  function factNoun(m) {
    var kinds = (m && m.facts || []).map(function (f) { return f.kind; });
    if (kinds.length && kinds.every(function (k) { return k === "HAS_CODE"; })) return "a verified code";
    if (kinds.length && kinds.every(function (k) { return k === "DOCUMENTED_CAUSE"; })) return "a documented cause";
    return "a verified fact";
  }

  function stepRoute(c) {
    var r = c.route;
    if (!r) return null;
    var m = c.memory;
    var searches = c.numbers ? c.numbers.searches : null;
    var research = r.steps.indexOf("research") >= 0;
    var usedMemory = r.steps.indexOf("graph") >= 0;
    var fromMemory = usedMemory && !research;
    var records = r.steps.indexOf("history") >= 0;
    var topUp = usedMemory && research;
    var title = records && fromMemory && searches === 0 ? "The route: property records and memory, no search"
      : topUp ? "The route: memory first, then a short top up search"
      : research ? "The route: memory first, then a search"
      : fromMemory && searches === 0 ? "The route: memory, no search"
      : fromMemory ? "The route: memory, topped up by a search"
      : "The route and why";
    return {
      title: title,
      body: function (s) {
        var row = add(s, el("p", "chips"));
        r.steps.forEach(function (st) { add(row, chip("chip-route", ROUTE_NAMES[st] || st)); });
        if (r.classifier_verdict) {
          add(row, chip("chip-quiet", "Symptom check: " + (VERDICT_NAMES[r.classifier_verdict] || r.classifier_verdict)));
        }
        var lead = add(s, el("p", "lead-note step-note"));
        if (records && fromMemory && searches === 0) {
          txt(lead, "The tool read this unit's property records first. Memory then held " + factNoun(m) + " that fits, so it answered without a single search.");
        } else if (topUp && m && m.offered_from_memory) {
          txt(lead, "Memory is checked first. It held " + factNoun(m) + " for this unit, but that alone cannot settle a vague symptom, so a short top up search ran with the smaller search limit.");
        } else if (topUp) {
          txt(lead, "Memory is checked first. It held facts for this unit, but the symptom check " +
            (r.classifier_verdict === "unsure" ? "could not tell whether any of them fits" : "could not match one to") +
            " this symptom, so a short top up search ran with the smaller search limit.");
        } else if (research) {
          txt(lead, "Memory is checked first. It had nothing verified that fits, so the tool searched.");
        } else if (fromMemory && searches === 0 && m && m.confirmed_from_memory) {
          txt(lead, "Memory is checked first. It held " + factNoun(m) + " that fits, so the tool answered without a single search.");
        } else if (fromMemory && searches === 0) {
          txt(lead, "Memory had " + factNoun(m) + " for this unit that might explain the symptom, so the tool did not search.");
        } else if (fromMemory) {
          txt(lead, "Memory answered part of this, and " + plural(searches, "search", "searches") + " topped it up.");
        }
        var prior = m && m.prior;
        if (prior) {
          var p = add(s, el("p", "step-note"));
          txt(p, "Memory keeps only facts a run has checked word for word against its source page. The earlier run on this unit, ");
          if (byId[prior.case_id]) add(p, caseLink(prior.case_id, prior.button + " (case " + caseNum(byId[prior.case_id]) + ")"));
          else txt(p, runLabel({ kind: c.kind, run_id: prior.run_id }));
          if (prior.written === 0) {
            txt(p, ", stored no facts" + (prior.dropped ? ", and set aside " + plural(prior.dropped, "fact it could not check", "facts it could not check") : "") +
              ", so there was nothing to reuse and this new symptom needed a full search.");
          } else if (topUp) {
            txt(p, ", stored " + plural(prior.written, "fact", "facts") +
              (prior.dropped ? " and set aside " + plural(prior.dropped, "fact it could not check", "facts it could not check") : "") +
              ". Because memory already held facts for this unit, this run searched with the smaller top up limit instead of the full one.");
          } else {
            txt(p, ", stored " + plural(prior.written, "fact", "facts") + ", but none fits this new symptom, so it needed a full search.");
          }
        }
        var det = disclose(s, "The tool's routing reason, word for word");
        add(det, el("blockquote", "verbatim-plain", r.reason));
      }
    };
  }

  function stepMemory(c) {
    var m = c.memory;
    if (!m || !m.facts.length) return null;
    return {
      title: m.heading,
      body: function (s) {
        m.facts.forEach(function (f) {
          var card = add(s, el("div", "fact-card"));
          var h = add(card, el("p", "fact-head"));
          add(h, el("strong", null, "Stored fact: code " + f.code));
          txt(h, " ");
          add(h, el("span", "badge badge-tier", (TIER_NAMES[f.tier] || f.tier) + " source"));
          var fig = verbatim(card, "Evidence as stored, word for word", f.evidence);
          if (f.evidence_ends_mid_text) {
            var bq = fig.querySelector("blockquote");
            txt(bq, " ");
            add(bq, el("span", "cut-mark", "[stored text ends here]"));
            add(card, el("p", "step-note", "The stored words stop where the quoted span of the source page stopped when the fact was saved. They are shown exactly as stored."));
          }
          if (f.brief_quote_relation === "prefix" && f.brief_quote && f.brief_quote.length < f.evidence.length) {
            add(card, el("p", "step-note", "The brief quotes the first " + f.brief_quote.length + " of the " + f.evidence.length + " stored characters."));
          } else if (f.brief_quote_relation === "within" && f.brief_quote) {
            add(card, el("p", "step-note", "The brief quotes " + f.brief_quote.length + " of the " + f.evidence.length + " stored characters, word for word, leaving out the code label at the start."));
          }
          var dl = add(card, el("dl", "facts"));
          add(dl, el("dt", null, "Source"));
          add(add(dl, el("dd")), extLink(f.source_url, f.host));
          add(dl, el("dt", null, "Page retrieved"));
          add(dl, el("dd", null, f.retrieved));
          add(dl, el("dt", null, "Fact validated"));
          add(dl, el("dd", null, f.validated));
          add(dl, el("dt", null, "First stored by"));
          var dd = add(dl, el("dd"));
          var first = D.cases.filter(function (x) { return x.run_id === f.first_run_id; })[0];
          txt(dd, runLabel({ kind: c.kind, run_id: f.first_run_id }));
          if (first && first.id !== c.id) {
            txt(dd, ", shown in ");
            add(dd, caseLink(first.id, "case " + caseNum(first)));
          }
          if (f.first_run_disagrees && STAND_IN) {
            add(card, el("p", "step-note", "See the builder notes at the top of this case: the first run's own log disagrees with memory about this fact."));
          }
        });
        if (m.searched_instead.length) {
          add(s, el("h4", "sub", "What still needed a search"));
          var ul = add(s, el("ul", "plain-list"));
          m.searched_instead.forEach(function (q) { add(ul, el("li", "mono", q)); });
        } else {
          add(s, el("p", "step-note", "No search was needed. This run sent none."));
        }
        var open = (c.outcome.candidate_list || []).filter(function (x) { return !x.confirmed; });
        if (open.length) {
          add(s, el("h4", "sub", "What memory could not settle"));
          var ul2 = add(s, el("ul", "plain-list"));
          open.forEach(function (x) {
            var li = add(ul2, el("li"));
            add(li, el("strong", null, (x.code ? "Code " + x.code : "A possible cause") + ", offered but not confirmed. "));
            txt(li, "The tool's reason for showing it: “" + x.why_shown + "”");
          });
          add(s, el("p", "step-note", "Memory can say what a code means. It cannot say which cause fits a vague symptom, so the brief lists the possibility and leaves it unconfirmed."));
        }
      }
    };
  }

  function stepRecords(c) {
    var r = c.records;
    if (!r) return null;
    return {
      title: "What the property's records added",
      body: function (s) {
        add(s, el("p", "chips")).appendChild(chip("chip-synthetic", r.label));
        add(s, el("p", "step-note lead-note", "The records attached to this run are synthetic: seeded test rows for an invented property, with a real model name. The run that read them was live, and everything below is what its brief says, word for word."));
        var hb = r.happened_before;
        if (hb) {
          add(s, el("h4", "sub", "This has happened before"));
          verbatim(s, "The brief's summary of the earlier visit", hb.summary);
          add(s, el("p", "step-note", "Cited to service record " + hb.record_id + ", dated " + longDate(hb.record_date) +
            (hb.code ? ", when the panel showed the same code, " + hb.code : "") + ". A technician sees what fixed it last time before driving out."));
        }
        var age = r.age;
        if (age) {
          add(s, el("h4", "sub", "How old the unit is"));
          verbatim(s, "The brief's age statement", age.statement);
          add(s, el("p", "step-note", "Worked out by the tool's code from the install date on property record " + age.record_id +
            " (" + longDate(age.install_date) + ") and the day of the run (" + longDate(age.as_of) + "). The model does not write this line, so it cannot get the arithmetic wrong."));
        }
        var w = r.warranty_terms;
        if (w) {
          add(s, el("h4", "sub", "Warranty terms on file"));
          verbatim(s, "Quoted from property record " + w.record_id, w.terms);
          add(s, el("p", "step-note", "Copied by code from the record, not paraphrased by the model. These terms are synthetic test data, as the record itself says."));
        }
      }
    };
  }

  function stepTrail(c) {
    var t = c.trail || [];
    if (!t.length) return null;
    var sent = t.filter(function (x) { return x.status === "sent"; });
    var searches = sent.filter(function (x) { return x.tool === "search"; }).length;
    var fetches = sent.filter(function (x) { return x.tool === "fetch"; }).length;
    var blocked = t.length - sent.length;
    return {
      title: "The research trail",
      body: function (s) {
        add(s, el("p", "step-note lead-note",
          plural(searches, "search", "searches") + " sent, " + plural(fetches, "page", "pages") + " fetched, " +
          plural(blocked, "call", "calls") + " blocked by the run's search or fetch limit. In the order the research model asked for them."));
        var ol = add(s, el("ol", "trail"));
        t.forEach(function (x) {
          var li = add(ol, el("li", "trail-item trail-" + x.status));
          var head = add(li, el("p", "trail-head"));
          if (x.status === "blocked") {
            add(head, el("span", "badge badge-blocked", "blocked, never sent"));
            txt(head, " ");
          }
          add(head, el("span", "trail-kind", x.tool === "search" ? "Search" : "Fetch"));
          txt(head, " ");
          if (x.tool === "search") add(head, el("span", "mono", x.query));
          else add(head, extLink(x.url));
          var meta = add(li, el("p", "trail-meta"));
          if (x.status === "blocked") {
            txt(meta, "Blocked by the run's search or fetch limit before it was sent. Nothing was spent on it.");
          } else if (x.tool === "search") {
            txt(meta, plural(x.results, "result", "results") + (x.hosts.length ? " from " + x.hosts.join(", ") : ""));
            if (x.credits) txt(meta, ". " + plural(x.credits, "search credit", "search credits"));
          } else {
            add(meta, el("span", "badge " + (x.ok ? "badge-high" : "badge-low"), x.ok ? "fetched" : "failed"));
          }
        });
        if (c.numbers && !c.numbers.per_call_credits_shown) {
          add(s, el("p", "step-note", "Credits per call are left off for this run: its lookup log and its ledger disagree on them. The run's total, from the ledger, is in the numbers."));
        }
        add(s, el("p", "step-note", "Page text and search snippets are not shown here. The only text from these pages that reaches a reader is what the brief quotes as evidence."));
      }
    };
  }

  function stepNra(c) {
    var n = c.outcome && c.outcome.no_reliable_answer;
    if (!n) return null;
    var counts = { sent: 0, blocked: 0, not_in_trail: 0 };
    n.searched.forEach(function (q) { counts[q.status] = (counts[q.status] || 0) + 1; });
    return {
      title: "No reliable answer, and why",
      body: function (s) {
        add(s, el("p", "big-verdict", "NO RELIABLE ANSWER"));
        verbatim(s, "Why it was not enough, in the tool's own words", n.why_insufficient);
        if (c.also_count) {
          var p = add(s, el("p", "step-note lead-note"));
          txt(p, c.also_count.no_reliable_answer + " of " + c.also_count.total + " " + (isReplay(c) ? "replay" : "live") +
            " runs of this question on the same build ended with no reliable answer: ");
          txt(p, c.also_count.runs.map(function (r) { return r.run_id; }).join(", ") + ".");
        }
        add(s, el("p", "step-note", REFUSAL[c.outcome.refusal_origin] || "The run ended without a reliable answer."));
        var sp = add(s, el("p", "step-note"));
        txt(sp, plural(counts.sent, "search", "searches") + " sent, " + counts.blocked + " blocked by the run's search limit");
        if (counts.not_in_trail) txt(sp, ", " + counts.not_in_trail + " listed by the tool but not found in the trail");
        txt(sp, ". The research trail above shows each one.");
        if (c.outcome.stop_reason) add(s, el("p", "step-note", "Why the run stopped: " + c.outcome.stop_reason));
        if (n.found && n.found.length) {
          var det = disclose(s, "What the tool listed as found, in its own words");
          add(det, el("p", "step-note", "Numbers such as “Source 0” refer to the model's own list of the pages it was given, which is not shown here."));
          var ul = add(det, el("ul", "plain-list"));
          n.found.forEach(function (f) { add(ul, el("li", null, f)); });
        }
      }
    };
  }

  function stepCompare(c) {
    var cw = c.compare_with;
    if (!cw) return null;
    var cols = cw.columns.filter(function (col) { return byId[col.case_id]; });
    if (cols.length < 2) return null;
    var colCases = cols.map(function (col) { return byId[col.case_id]; });
    var bothResearched = cols.every(function (col) { return (col.route || "").split(" then ").indexOf("research") >= 0; });
    var rows = [
      ["Route", "route", function (v) { return v ? v.split(" then ").map(function (x) { return ROUTE_NAMES[x] || x; }).join(" then ") : "none"; }],
      ["Searches", "searches", String],
      ["Pages fetched", "fetches", String],
      ["Search credits (Tavily)", "tavily_credits", String],
      ["Cost", "cost_usd", null],
      ["Processing time", "processing_s", secs],
      ["Steps to try first in the brief", "try_first_steps", function (v) { return v == null ? "not recorded" : String(v); }]
    ];
    return {
      title: "Side by side",
      body: function (s) {
        var usedMemory = (c.route && c.route.steps.indexOf("graph") >= 0);
        if (bothResearched && usedMemory) {
          add(s, el("p", "step-note lead-note", "Both runs searched. This one checked what memory held for the unit first, and what it found sent the run to the short top up search instead of the full one. Each source in the brief below is marked as from memory or found by search."));
        } else if (bothResearched) {
          add(s, el("p", "step-note lead-note", "Both runs had to search, so memory saved nothing here. A run that searches costs more or less depending on how many searches and pages it needs."));
        }
        var table = add(s, el("table", "compare side-by-side"));
        add(table, el("caption", "sr-only", "The two runs compared"));
        var hr = add(add(table, el("thead")), el("tr"));
        var th0 = add(hr, el("th", null, "")); th0.scope = "col";
        cols.forEach(function (col, i) {
          var th = add(hr, el("th")); th.scope = "col";
          add(th, el("span", "col-title", col.title + " (case " + caseNum(colCases[i]) + ")"));
          add(th, el("span", "col-run", runLabel(colCases[i])));
        });
        var tb = add(table, el("tbody"));
        rows.forEach(function (r) {
          var tr = add(tb, el("tr"));
          var th = add(tr, el("th", null, r[0])); th.scope = "row";
          cols.forEach(function (col) {
            var td = add(tr, el("td"));
            if (r[2]) txt(td, r[2](col[r[1]])); else money(td, col[r[1]]);
            td.setAttribute("data-label", col.title + " (" + col.run_id + ")");
          });
        });
        add(s, el("p", "step-note", colCases.every(paused)
          ? "Processing time is the time the tool spent working. The owner's confirmation pause is not counted in either run."
          : "Processing time is the time the tool spent working."));
      }
    };
  }

  function stepBrief(c) {
    var o = c.outcome || {};
    return {
      title: "The brief",
      body: function (s) {
        if (!c.brief) {
          add(s, el("p", "callout callout-notice", isReplay(c) ? "No brief file was built for this replay."
            : o.status === "paused" ? "No brief: the run stopped before anything was searched."
            : "No brief file was recorded for this run."));
          return;
        }
        var nra = o.status === "no_reliable_answer";
        var row = add(s, el("p", "chips"));
        add(row, el("span", "verdict " + (nra ? "verdict-nra" : "verdict-ok"), nra ? "No reliable answer"
          : o.confirmed_candidates ? plural(o.confirmed_candidates, "confirmed cause", "confirmed causes")
          : plural(o.candidates, "documented possibility", "documented possibilities")));
        if (o.grounding_status === "verified") {
          add(row, chip("chip-quiet", "Each code checked against the page it cites"));
        }
        if (o.sources && o.sources.length) {
          add(s, el("h4", "sub", "Sources the brief cites"));
          var ul = add(s, el("ul", "plain-list"));
          o.sources.forEach(function (src) {
            var li = add(ul, el("li"));
            add(li, el("span", "badge badge-tier", (TIER_NAMES[src.tier] || src.tier)));
            txt(li, " ");
            add(li, extLink(src.url, src.title || src.host));
            txt(li, src.origin === "graph" ? " (from memory)" : " (found by search)");
          });
        }
        var label = STAND_IN ? " (" + STAND_IN_NOTE + ")" : c.records_label ? " (" + c.records_label.toLowerCase() + ")" : "";
        var fig = add(s, el("figure", "brief-figure"));
        add(fig, el("figcaption", null, "The file a technician would open, unedited" + label));
        /* Built detached, with the sandbox set before src, so the very first
           load is already sandboxed (sandbox flags apply only from the next
           navigation on). */
        var frame = el("iframe", "brief-frame");
        frame.setAttribute("sandbox", "allow-popups allow-popups-to-escape-sandbox");
        frame.setAttribute("loading", "lazy");
        frame.title = "Service brief from " + runLabel(c) + label;
        frame.src = c.brief.share;
        add(fig, frame);
        var actions = add(s, el("p", "brief-actions"));
        var open = add(actions, el("a", null, "Open the brief on its own" + label));
        open.href = c.brief.share;
        open.target = "_blank";
        open.rel = "noreferrer";
      }
    };
  }

  function stepNumbers(c) {
    var n = c.numbers;
    if (!n) {
      if (!isReplay(c)) return null;
      return {
        title: "The run's numbers",
        body: function (s) {
          add(s, el("p", "chips")).appendChild(chip("chip-replay", D.replay_label));
          add(s, el("p", "step-note lead-note", "A replay makes no live call, so it spends nothing and there is no cost to show."));
        }
      };
    }
    return {
      title: "The run's numbers",
      body: function (s) {
        var nrow = add(s, el("p", "chips"));
        nrow.appendChild(isReplay(c) ? chip("chip-replay", D.replay_label)
          : chip("chip-live", "From " + runLabel(c)));
        if (c.records_label) nrow.appendChild(chip("chip-synthetic", c.records_label));
        var m = add(s, el("div", "meter-row"));
        var label = add(m, el("label", null, "Cost against the run's cap"));
        var meter = add(m, el("meter"));
        meter.id = "meter-" + c.id;
        label.htmlFor = meter.id;
        var cap = n.run_cap_usd || 0;
        meter.min = 0;
        meter.max = cap || 1;
        meter.value = n.cost_usd || 0;
        meter.low = cap * 0.6;
        meter.high = cap * 0.9;
        meter.optimum = 0;
        var capWords = n.cap_check === "pass" ? "within the cap" : n.cap_check === "fail" ? "over the cap" : "cap check " + n.cap_check;
        meter.textContent = dollars(n.cost_usd) + " of a " + n.cap_text;
        var mt = add(m, el("p", "meter-text"));
        money(mt, n.cost_usd);
        txt(mt, " of a " + n.cap_text + ", " + capWords);
        add(m, el("p", "exact-note", "Exact ledger total: " + exactUsd(n.cost_usd) + ". Cap: " + exactUsd(n.run_cap_usd) + "."));
        var dl = add(s, el("dl", "facts numbers"));
        [["Searches", String(n.searches)], ["Pages fetched", String(n.fetches)],
         ["Search credits (Tavily)", String(n.tavily_credits)],
         ["Processing time", secs(n.processing_s)]]
          .forEach(function (p) { add(dl, el("dt", null, p[0])); add(dl, el("dd", null, p[1])); });
        add(s, el("p", "step-note", "Search credits are what the web search service, Tavily, charges for this run's lookups. The ledger counts them apart from the dollar cost, which is what Claude charged. These runs used Tavily's free tier, so the credits cost no dollars."));
        add(s, el("p", "step-note", n.processing_note));
        if (n.by_node && n.by_node.length) {
          var det = add(s, el("details", "by-node"));
          add(det, el("summary", null, "Calls by step, from the ledger"));
          var table = add(det, el("table", "compare"));
          add(table, el("caption", "sr-only", "Calls, tokens, credits and cost by step"));
          var hr = add(add(table, el("thead")), el("tr"));
          ["Step", "Service", "Calls", "Tokens in", "Tokens out", "Search credits", "Cost"].forEach(function (h) {
            var th = add(hr, el("th", null, h)); th.scope = "col";
          });
          var tb = add(table, el("tbody"));
          n.by_node.forEach(function (r) {
            var tr = add(tb, el("tr"));
            var th = add(tr, el("th", null, NODE_NAMES[r.node] || r.node)); th.scope = "row";
            var service = r.provider === "tavily" ? "Tavily web search" : modelName(r.model || r.provider);
            [["Service", service], ["Calls", String(r.calls)],
             ["Tokens in", String(r.input_tokens)], ["Tokens out", String(r.output_tokens)],
             ["Search credits", String(r.tavily_credits)], ["Cost", dollars(r.usd)]].forEach(function (p) {
              var td = add(tr, el("td", null, p[1]));
              td.setAttribute("data-label", p[0]);
              if (p[0] === "Service" && r.model) td.title = r.model;
              if (p[0] === "Cost") td.title = "Ledger value: " + exactUsd(r.usd);
            });
          });
          add(det, el("p", "step-note", "Web search is paid in credits, not dollars, in this ledger, so its cost column reads zero."));
        }
      }
    };
  }

  function stepsFor(c) {
    return [stepAsk(c), stepPlate(c), stepConfirm(c), stepRoute(c), stepRecords(c), stepMemory(c), stepTrail(c),
            stepNra(c), stepCompare(c), stepBrief(c), stepNumbers(c)].filter(Boolean);
  }

  /* ---------------------------------------------------------------------- */
  /* All cases at a glance                                                   */
  /* ---------------------------------------------------------------------- */

  function howItEnded(c) {
    var o = c.outcome || {};
    var steps = c.route ? c.route.steps : [];
    if (isReplay(c)) return "Replay, no live call";
    if (o.status === "paused") return "Stopped at confirmation";
    if (o.status === "no_reliable_answer") return "No reliable answer";
    if (steps.indexOf("graph") >= 0 && steps.indexOf("research") >= 0) return "Memory, then a short search";
    if (steps.indexOf("research") >= 0) return "Searched";
    if (steps.indexOf("history") >= 0 && steps.indexOf("graph") >= 0 && !(c.numbers && c.numbers.searches)) return "Records and memory, no search";
    if (steps.indexOf("graph") >= 0) return (c.numbers && c.numbers.searches) ? "Memory, then a search" : "From memory";
    return o.status || "not recorded";
  }

  function renderSummary() {
    if (!summarySlot) return;
    var det = add(summarySlot, el("details", "summary-box"));
    try { if (window.matchMedia("(min-width: 40rem)").matches) det.open = true; } catch (e) { det.open = true; }
    add(det, el("summary", null, "All " + D.cases.length + " cases at a glance" + (STAND_IN ? " (" + STAND_IN_NOTE + ")" : "")));
    var table = add(det, el("table", "compare glance"));
    add(table, el("caption", "sr-only", "Each case's outcome, searches, cost and time"));
    var hr = add(add(table, el("thead")), el("tr"));
    ["Case", "How it ended", "Searches", "Cost", "Time"].forEach(function (h) {
      var th = add(hr, el("th", null, h)); th.scope = "col";
    });
    var tb = add(table, el("tbody"));
    D.cases.forEach(function (c, i) {
      var tr = add(tb, el("tr"));
      var th = add(tr, el("th")); th.scope = "row";
      add(th, caseLink(c.id, (i + 1) + ". " + c.button));
      if (isReplay(c)) { txt(th, " "); add(th, el("span", "case-kind", "(" + D.replay_label + ")")); }
      if (c.records_label) { txt(th, " "); add(th, el("span", "case-kind", "(" + c.records_label + ")")); }
      var n = c.numbers;
      var cells = [["How it ended", howItEnded(c)], ["Searches", n ? String(n.searches) : "none"], ["Cost", null],
                   ["Time", n ? secs(n.processing_s) : "none"]];
      cells.forEach(function (p) {
        var td = add(tr, el("td"));
        td.setAttribute("data-label", p[0]);
        if (p[0] === "Cost") { if (n) money(td, n.cost_usd); else txt(td, "none"); }
        else txt(td, p[1]);
      });
    });
    add(det, el("p", "step-note", "Each row is one " + (D.cases.some(isReplay) ? "run, live or replay as labeled" : "live run") +
      ", named inside its case. Cost is in US dollars, from the ledger, rounded to four places; hover over a cost for the exact ledger value."));
  }

  /* ---------------------------------------------------------------------- */
  /* One case, with its stepper                                             */
  /* ---------------------------------------------------------------------- */

  var state = { showAll: false };

  function renderCase(c) {
    flow.textContent = "";
    flow.setAttribute("aria-labelledby", "tab-" + c.id);

    var head = add(flow, el("div", "case-head"));
    var h2 = add(head, el("h2", "case-title", c.title));
    h2.id = "case-title-" + c.id;
    h2.tabIndex = -1;
    kindChips(c, head);
    if (c.takeaway) add(head, el("p", "takeaway", c.takeaway));
    if (STAND_IN && c.warnings && c.warnings.length) {
      var det = add(head, el("details", "warnings"));
      add(det, el("summary", null, "Builder notes on this stand-in data (" + c.warnings.length + ")"));
      var ul = add(det, el("ul"));
      c.warnings.forEach(function (w) { add(ul, el("li", null, w.charAt(0).toUpperCase() + w.slice(1))); });
    }

    var steps = stepsFor(c);
    var nav = add(flow, el("div", "stepper"));
    nav.setAttribute("role", "group");
    nav.setAttribute("aria-label", "Steps in this case");
    var prev = add(nav, el("button", "step-btn", "Previous"));
    prev.type = "button";
    /* The visible counter is not a live region: moving focus to the step
       heading already announces the step. Only the show all toggle, which
       moves no focus, is announced, through the hidden live region. */
    var status = add(nav, el("p", "step-status"));
    var next = add(nav, el("button", "step-btn", "Next"));
    next.type = "button";
    var all = add(nav, el("button", "step-btn step-all", "Show all steps"));
    all.type = "button";
    var live = add(nav, el("p", "sr-only"));
    live.setAttribute("aria-live", "polite");
    live.setAttribute("aria-atomic", "true");

    var sections = steps.map(function (st, i) {
      var sec = add(flow, el("section", "step"));
      sec.id = c.id + "-step-" + (i + 1);
      var lab = add(sec, el("p", "step-label", "Step " + (i + 1) + " of " + steps.length));
      lab.id = sec.id + "-label";
      var h = add(sec, el("h3", null, st.title));
      h.tabIndex = -1;
      h.setAttribute("aria-describedby", lab.id);
      sec.setAttribute("aria-labelledby", (h.id = sec.id + "-h"));
      st.body(sec);
      return sec;
    });

    var current = 0;
    function show(i, focus, announce) {
      current = Math.max(0, Math.min(steps.length - 1, i));
      sections.forEach(function (sec, j) { sec.hidden = !state.showAll && j !== current; });
      prev.disabled = state.showAll || current === 0;
      next.disabled = state.showAll || current === steps.length - 1;
      all.setAttribute("aria-pressed", state.showAll ? "true" : "false");
      all.textContent = state.showAll ? "Show one step at a time" : "Show all steps";
      status.textContent = state.showAll
        ? "Showing all " + steps.length + " steps"
        : "Step " + (current + 1) + " of " + steps.length + ": " + steps[current].title;
      if (announce) live.textContent = status.textContent;
      if (focus) sections[current].querySelector("h3").focus();
    }
    prev.addEventListener("click", function () { show(current - 1, true, false); });
    next.addEventListener("click", function () { show(current + 1, true, false); });
    all.addEventListener("click", function () { state.showAll = !state.showAll; show(current, false, true); });
    show(0, false, false);
  }

  /* ---------------------------------------------------------------------- */
  /* The case picker: an ARIA tablist with a roving tabindex               */
  /* ---------------------------------------------------------------------- */

  var tabs = [];
  var shown = null;
  function select(id, focusHeading) {
    var c = byId[id] || D.cases[0];
    tabs.forEach(function (t) {
      var on = t.getAttribute("data-case") === c.id;
      t.setAttribute("aria-selected", on ? "true" : "false");
      t.tabIndex = on ? 0 : -1;
    });
    if (shown !== c.id) renderCase(c);
    shown = c.id;
    if (history.replaceState) history.replaceState(null, "", "#" + c.id);
    if (focusHeading) {
      var h = document.getElementById("case-title-" + c.id);
      if (h) h.focus();
    }
  }

  D.cases.forEach(function (c, i) {
    var btn = add(buttons, el("button", "case-btn"));
    btn.type = "button";
    btn.id = "tab-" + c.id;
    btn.setAttribute("role", "tab");
    btn.setAttribute("aria-controls", "flow");
    btn.setAttribute("data-case", c.id);
    add(btn, el("span", "case-num", String(i + 1)));
    txt(btn, " " + c.button);
    if (isReplay(c)) add(btn, el("span", "case-kind", " (" + D.replay_label + ")"));
    if (c.records_label) add(btn, el("span", "case-kind", " (" + c.records_label + ")"));
    btn.addEventListener("click", function () { select(c.id, false); });
    tabs.push(btn);
  });
  flow.setAttribute("role", "tabpanel");
  renderSummary();

  buttons.addEventListener("keydown", function (e) {
    var i = tabs.indexOf(document.activeElement);
    if (i < 0) return;
    var j = null;
    if (e.key === "ArrowRight" || e.key === "ArrowDown") j = (i + 1) % tabs.length;
    else if (e.key === "ArrowLeft" || e.key === "ArrowUp") j = (i - 1 + tabs.length) % tabs.length;
    else if (e.key === "Home") j = 0;
    else if (e.key === "End") j = tabs.length - 1;
    if (j === null) return;
    e.preventDefault();
    tabs[j].focus();
    select(tabs[j].getAttribute("data-case"), false);
  });

  /* Plain links to #c1 and so on (the list under the lede) open that case. */
  Array.prototype.forEach.call(document.querySelectorAll('a[href^="#c"]'), function (a) {
    var id = a.getAttribute("href").slice(1);
    if (!byId[id]) return;
    a.addEventListener("click", function (e) { e.preventDefault(); select(id, true); });
  });
  window.addEventListener("hashchange", function () {
    var id = (location.hash || "").replace("#", "");
    if (byId[id]) select(id, true);
  });

  var start = (location.hash || "").replace("#", "");
  select(byId[start] ? start : D.cases[0].id, false);
})();
