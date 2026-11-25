(function () {
  var DAY = 86400000;
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

  var cards = [].slice.call(document.querySelectorAll(".card"));
  cards.forEach(function (c) {
    c._ms = JSON.parse(c.getAttribute("data-milestones") || "[]");
    c._end = Number(c.getAttribute("data-conf-end")) || 0;
    c._label = c.querySelector(".next-label");
    c._count = c.querySelector(".next-count");
    c._box = c.querySelector(".next");
    c._rows = [].slice.call(c.querySelectorAll(".row[data-ts]"));
  });
  var upItems = [].slice.call(document.querySelectorAll("#nextup li"));
  var area = "";

  function tick() {
    var now = Date.now();
    cards.forEach(function (c) {
      var next = null;
      for (var i = 0; i < c._ms.length; i++) { if (c._ms[i].t > now) { next = c._ms[i]; break; } }
      c._box.className = "next";
      c.classList.remove("done");
      if (next) {
        var left = next.t - now;
        c._label.textContent = "Next: " + next.l;
        c._count.textContent = next.d ? clock(left) : rough(left);
        if (left < 7 * DAY) c._box.classList.add("soon");
      } else if (c._end && now < c._end) {
        c._label.textContent = "Status"; c._count.textContent = "Happening now";
      } else if (c._end) {
        c._box.classList.add("quiet"); c.classList.add("done");
        c._label.textContent = "Status"; c._count.textContent = "Finished for this cycle";
      } else {
        c._box.classList.add("quiet");
        c._label.textContent = "Status"; c._count.textContent = "Dates not announced yet";
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

    var shown = 0;
    upItems.forEach(function (li) {
      var t = Number(li.getAttribute("data-t"));
      var match = !area || (li.getAttribute("data-areas") || "").split("|").indexOf(area) >= 0;
      var show = t > now && match && shown < 6;
      li.hidden = !show;
      if (show) {
        shown++;
        var el = li.querySelector(".nu-count"), left = t - now;
        el.textContent = li.getAttribute("data-d") === "1" ? clock(left) : rough(left);
        el.className = "nu-count" + (left < 7 * DAY ? " soon" : "");
      }
    });
    var list = document.getElementById("nextup");
    if (!list) return;
    var empty = list.querySelector(".empty");
    if (!shown && !empty) { empty = document.createElement("li"); empty.className = "empty"; empty.textContent = "Nothing with a confirmed date right now."; list.appendChild(empty); }
    if (empty) empty.hidden = shown > 0;
  }

  [].slice.call(document.querySelectorAll(".filter")).forEach(function (b) {
    b.addEventListener("click", function () {
      area = b.getAttribute("data-area");
      [].slice.call(document.querySelectorAll(".filter")).forEach(function (x) { x.classList.toggle("on", x === b); });
      cards.forEach(function (c) {
        c.hidden = !!area && (c.getAttribute("data-areas") || "").split("|").indexOf(area) < 0;
      });
      tick();
    });
  });

  tick();
  setInterval(tick, 1000);
})();
