/*
  site.js - everything that happens in the visitor's browser:
    - live countdowns
    - the search box, the area filters and the List / Timeline switch
    - favorites (the star buttons), remembered in this browser only

  Favorites are stored in the browser's own storage, not on the server.
  They never leave the visitor's device unless they choose "Get reminders".
*/
(function () {
  var DAY = 86400000;
  var all = function (sel, root) { return [].slice.call((root || document).querySelectorAll(sel)); };

  // ---------------------------------------------------------------- helpers
  function pad(n) { return (n < 10 ? "0" : "") + n; }
  function clock(ms) {
    var s = Math.floor(ms / 1000), d = Math.floor(s / 86400);
    var h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
    return d + "d " + pad(h) + "h " + pad(m) + "m " + pad(s % 60) + "s";
  }
  function rough(ms) {
    var d = Math.floor(ms / DAY);
    if (d >= 2) return "in " + d + " days";
    if (d === 1) return "tomorrow";
    var h = Math.floor(ms / 3600000);
    return h >= 1 ? "in " + h + " hour" + (h === 1 ? "" : "s") : "in under an hour";
  }
  // Short form for the list: "21 days", or "5d 04h" inside the last week.
  function brief(ms) {
    var d = Math.floor(ms / DAY), h = Math.floor((ms % DAY) / 3600000);
    if (d >= 7) return d + " days";
    if (d >= 1) return d + "d " + pad(h) + "h";
    var m = Math.floor((ms % 3600000) / 60000);
    return h + "h " + pad(m) + "m";
  }

  // -------------------------------------------------------------- favorites
  var KEY = "paperddl:favorites";
  function loadFavs() {
    try { var v = JSON.parse(localStorage.getItem(KEY) || "[]"); return Array.isArray(v) ? v : []; }
    catch (e) { return []; }
  }
  function saveFavs(list) { try { localStorage.setItem(KEY, JSON.stringify(list)); } catch (e) { /* private mode */ } }
  var favs = loadFavs();
  function isFav(series) { return favs.indexOf(series) >= 0; }

  // The manage page tells us what this person follows; star those too.
  var sync = document.querySelector("[data-sync-favs]");
  if (sync) {
    sync.getAttribute("data-sync-favs").split(",").forEach(function (s) {
      if (s && !isFav(s)) favs.push(s);
    });
    saveFavs(favs);
  }

  // On the sign-up page, tick the starred conferences if nothing is ticked yet.
  var form = document.getElementById("subscribe-form");
  if (form && favs.length && !form.querySelector("input[type=checkbox]:checked")) {
    var ticked = 0;
    all('input[name="series"]', form).forEach(function (box) {
      if (isFav(box.value)) { box.checked = true; ticked++; }
    });
    var note = document.getElementById("favnote");
    if (note && ticked) note.hidden = false;
  }

  var stars = all(".star");
  function paintStars() {
    stars.forEach(function (b) {
      var on = isFav(b.getAttribute("data-series"));
      b.setAttribute("aria-pressed", on ? "true" : "false");
      b.title = on ? "Remove from favorites" : "Add to favorites";
    });
    var count = document.getElementById("favcount");
    if (count) count.textContent = favs.length ? "(" + favs.length + ")" : "";
    var bar = document.getElementById("favbar");
    if (bar) {
      bar.hidden = favs.length === 0;
      document.getElementById("favtext").textContent =
        "You starred " + favs.length + " conference" + (favs.length === 1 ? "" : "s") + ".";
      document.getElementById("favlink").href =
        "/subscribe?" + favs.map(function (s) { return "c=" + encodeURIComponent(s); }).join("&");
    }
  }
  stars.forEach(function (b) {
    b.addEventListener("click", function () {
      var s = b.getAttribute("data-series"), i = favs.indexOf(s);
      if (i >= 0) favs.splice(i, 1); else favs.push(s);
      saveFavs(favs);
      paintStars();
      applyFilters();
    });
  });

  // ------------------------------------------------- search, filters, views
  var search = document.getElementById("search");
  var rows = all(".conf"), items = all(".tl"), months = all(".month");
  var listView = document.getElementById("view-list"), timeView = document.getElementById("view-timeline");
  var empty = document.getElementById("empty");
  var area = "", view = "list";

  function matches(el, words) {
    if (area === "*fav") { if (!isFav(el.getAttribute("data-series"))) return false; }
    else if (area && (el.getAttribute("data-areas") || "").split("|").indexOf(area) < 0) return false;
    var text = el.getAttribute("data-search") || "";
    return words.every(function (w) { return text.indexOf(w) >= 0; });
  }

  function applyFilters() {
    if (!listView) return;
    var now = Date.now();
    var words = (search.value || "").toLowerCase().split(/\s+/).filter(Boolean);
    var shown = 0;
    rows.forEach(function (r) { var ok = matches(r, words); r.hidden = !ok; if (ok && view === "list") shown++; });
    items.forEach(function (li) {
      var ok = Number(li.getAttribute("data-t")) > now && matches(li, words);
      li.hidden = !ok;
      if (ok && view === "timeline") shown++;
    });
    months.forEach(function (m) {
      var visible = all(".tl", m).filter(function (li) { return !li.hidden; });
      m.hidden = visible.length === 0;
      all(".tl", m).forEach(function (li) { li.classList.remove("first"); });
      if (visible.length) visible[0].classList.add("first");
    });
    listView.hidden = view !== "list" || shown === 0;
    timeView.hidden = view !== "timeline" || shown === 0;
    empty.hidden = shown > 0;
    if (!shown) {
      empty.textContent = area === "*fav" && !favs.length
        ? "No favorites yet. Click the star next to a conference to add one."
        : "Nothing matches. Try a different search or filter.";
    }
  }

  if (search) {
    search.addEventListener("input", applyFilters);
    // Pressing "/" jumps to the search box, as on many sites.
    document.addEventListener("keydown", function (e) {
      if (e.key === "/" && document.activeElement !== search && !/input|textarea/i.test(document.activeElement.tagName)) {
        e.preventDefault(); search.focus();
      }
    });
  }
  all(".filter").forEach(function (b) {
    b.addEventListener("click", function () {
      area = b.getAttribute("data-area");
      all(".filter").forEach(function (x) { x.classList.toggle("on", x === b); });
      applyFilters();
    });
  });
  all(".view").forEach(function (b) {
    b.addEventListener("click", function () {
      view = b.getAttribute("data-view");
      all(".view").forEach(function (x) {
        x.classList.toggle("on", x === b);
        x.setAttribute("aria-pressed", x === b ? "true" : "false");
      });
      try { localStorage.setItem("paperddl:view", view); } catch (e) { /* ignore */ }
      applyFilters();
    });
  });
  try {
    var saved = localStorage.getItem("paperddl:view");
    if (saved === "timeline" && timeView) {
      view = "timeline";
      all(".view").forEach(function (x) {
        var on = x.getAttribute("data-view") === view;
        x.classList.toggle("on", on); x.setAttribute("aria-pressed", on ? "true" : "false");
      });
    }
  } catch (e) { /* ignore */ }

  // ------------------------------------------------------------- countdowns
  rows.forEach(function (r) {
    r._ms = JSON.parse(r.getAttribute("data-milestones") || "[]");
    r._end = Number(r.getAttribute("data-conf-end")) || 0;
    r._what = r.querySelector(".what"); r._when = r.querySelector(".when"); r._left = r.querySelector(".left");
  });
  var cards = all(".card");
  cards.forEach(function (c) {
    c._ms = JSON.parse(c.getAttribute("data-milestones") || "[]");
    c._end = Number(c.getAttribute("data-conf-end")) || 0;
    c._label = c.querySelector(".next-label"); c._count = c.querySelector(".next-count");
    c._box = c.querySelector(".next"); c._rows = all(".row[data-ts]", c);
  });

  function firstFuture(list, now) {
    for (var i = 0; i < list.length; i++) { if (list[i].t > now) return list[i]; }
    return null;
  }

  function tick() {
    var now = Date.now();

    rows.forEach(function (r) {
      var next = firstFuture(r._ms, now);
      r.classList.remove("done");
      r._left.className = "left";
      if (next) {
        var left = next.t - now;
        r._what.textContent = next.l;
        r._when.textContent = next.w;
        if (next.g) {
          var tag = document.createElement("span");
          tag.className = "tag"; tag.textContent = next.g;
          r._when.appendChild(tag);
        }
        r._left.textContent = next.d ? brief(left) : rough(left).replace(/^in /, "");
        if (left < 7 * DAY) r._left.classList.add("soon");
      } else {
        r._left.classList.add("quiet");
        r._when.textContent = "";
        if (r._end && now < r._end) { r._what.textContent = "Conference"; r._left.textContent = "Happening now"; }
        else if (r._end) { r.classList.add("done"); r._what.textContent = "Finished for this cycle"; r._left.textContent = ""; }
        else { r._what.textContent = "Dates not announced yet"; r._left.textContent = "TBA"; }
      }
    });

    items.forEach(function (li) {
      var t = Number(li.getAttribute("data-t")), el = li.querySelector(".left"), left = t - now;
      if (left <= 0) { if (!li.hidden) applyFilters(); return; }
      el.textContent = li.getAttribute("data-d") === "1" && left < 7 * DAY ? brief(left) : rough(left);
      el.className = "left" + (left < 7 * DAY ? " soon" : "");
    });

    cards.forEach(function (c) {
      var next = firstFuture(c._ms, now);
      c._box.className = "next";
      if (next) {
        var left = next.t - now;
        c._label.textContent = "Next: " + next.l;
        c._count.textContent = next.d ? clock(left) : rough(left);
        if (left < 7 * DAY) c._box.classList.add("soon");
      } else if (c._end && now < c._end) {
        c._label.textContent = "Status"; c._count.textContent = "Happening now";
      } else if (c._end) {
        c._box.classList.add("quiet"); c._label.textContent = "Status"; c._count.textContent = "Finished for this cycle";
      } else {
        c._box.classList.add("quiet"); c._label.textContent = "Status"; c._count.textContent = "Dates not announced yet";
      }
      c._rows.forEach(function (r) {
        var end = Number(r.getAttribute("data-ts")), start = Number(r.getAttribute("data-start")) || 0;
        var kind = r.getAttribute("data-kind"), rel = r.querySelector(".rel"), text = "";
        r.classList.remove("past", "live");
        if (now >= end) { r.classList.add("past"); text = kind === "conference" ? "finished" : "passed"; }
        else if (start && now >= start) { r.classList.add("live"); text = kind === "conference" ? "happening now" : "open now, closes " + rough(end - now); }
        else if (start) { text = (kind === "conference" ? "" : "opens ") + rough(start - now); }
        else { text = rough(end - now); }
        rel.textContent = text;
      });
    });
  }

  paintStars();
  applyFilters();
  tick();
  setInterval(tick, 1000);
})();
