/* Hover and keyboard layer. It only repeats what the tables already show, and the page works without it. */
(function () {
  "use strict";
  var tip = document.getElementById("tip");
  var data = document.getElementById("chart-data");
  var charts = data ? JSON.parse(data.textContent) : {};

  function place(x, y) {
    tip.hidden = false;
    var left = Math.min(x + 14, window.innerWidth - tip.offsetWidth - 8);
    var top = Math.min(y + 14, window.innerHeight - tip.offsetHeight - 8);
    tip.style.left = Math.max(8, left) + "px";
    tip.style.top = Math.max(8, top) + "px";
  }

  function hide() { tip.hidden = true; }

  function fill(chart, i) {
    tip.textContent = "";
    var head = document.createElement("div");
    head.className = "tip-head";
    head.textContent = chart.xLabel + " " + chart.x[i];
    tip.appendChild(head);
    chart.series.forEach(function (s) {
      var value = s.values[i];
      if (value === null || value === undefined) return;
      var row = document.createElement("div");
      row.className = "tip-row";
      var key = document.createElement("i");
      key.className = "key " + s.cls;
      var strong = document.createElement("strong");
      strong.textContent = value.toFixed(s.digits);
      var name = document.createElement("span");
      name.textContent = s.name;
      row.appendChild(key);
      row.appendChild(strong);
      row.appendChild(name);
      tip.appendChild(row);
    });
  }

  document.querySelectorAll("svg[data-chart]").forEach(function (svg) {
    var chart = charts[svg.getAttribute("data-chart")];
    if (!chart || !chart.x.length) return;
    var line = svg.querySelector(".crosshair");
    var left = Number(svg.getAttribute("data-left"));
    var first = Number(svg.getAttribute("data-first"));
    var scale = Number(svg.getAttribute("data-scale"));
    var width = svg.viewBox.baseVal.width;
    var index = chart.x.length - 1;

    function nearest(viewX) {
      var value = first + (viewX - left) / scale, best = 0;
      chart.x.forEach(function (x, i) {
        if (Math.abs(x - value) < Math.abs(chart.x[best] - value)) best = i;
      });
      return best;
    }

    function show(i, clientX, clientY) {
      index = i;
      var x = left + (chart.x[i] - first) * scale;
      line.setAttribute("x1", x);
      line.setAttribute("x2", x);
      line.setAttribute("visibility", "visible");
      fill(chart, i);
      place(clientX, clientY);
    }

    function clear() {
      line.setAttribute("visibility", "hidden");
      hide();
    }

    svg.addEventListener("pointermove", function (event) {
      var box = svg.getBoundingClientRect();
      var viewX = (event.clientX - box.left) * width / box.width;
      show(nearest(viewX), event.clientX, event.clientY);
    });
    svg.addEventListener("pointerleave", clear);
    svg.addEventListener("blur", clear);
    function showFromKeyboard(i) {
      var box = svg.getBoundingClientRect();
      show(i, box.left + (left + (chart.x[i] - first) * scale) * box.width / width, box.top + 24);
    }
    svg.addEventListener("focus", function () { showFromKeyboard(index); });
    svg.addEventListener("keydown", function (event) {
      var step = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
      if (!step) return;
      event.preventDefault();
      showFromKeyboard(Math.max(0, Math.min(chart.x.length - 1, index + step)));
    });
  });

  document.querySelectorAll("[data-tip]").forEach(function (el) {
    function show(x, y) {
      tip.textContent = el.getAttribute("data-tip");
      place(x, y);
    }
    el.addEventListener("pointermove", function (event) { show(event.clientX, event.clientY); });
    el.addEventListener("pointerleave", hide);
    el.addEventListener("focus", function () {
      var box = el.getBoundingClientRect();
      show(box.left, box.bottom);
    });
    el.addEventListener("blur", hide);
  });

  var age = document.getElementById("age");
  var stale = document.getElementById("stale");
  function tick() {
    var minutes = Math.round((Date.now() - Date.parse(age.getAttribute("data-built"))) / 60000);
    age.textContent = minutes < 1 ? ", just now" : ", " + minutes + " min ago";
    var every = Number(age.getAttribute("data-every"));
    if (age.getAttribute("data-live") === "1" && minutes * 60 > 3 * every) {
      stale.hidden = false;
      stale.textContent = "No new build for " + minutes + " min. Is dashboard/build.py --watch still running?";
    }
  }
  tick();
  setInterval(tick, 30000);
})();
