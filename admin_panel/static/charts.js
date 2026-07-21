// Moka panel — hafif, bagimsiz SVG grafikleri (harici kutuphane YOK).
// Dashboard'un kendi ozel cizerleri var; bunlar Musteriler/Raporlar sayfalari
// icin GENEL amaclidir. Girdi: [{label, value}, ...].
(function () {
  function fmt(n) {
    return (Math.round(Number(n) || 0)).toLocaleString("tr-TR");
  }

  // Cizgi grafik: aylik ciro/komisyon trendi gibi zaman serileri.
  window.mokaLineChart = function (slotId, series, opts) {
    var slot = document.getElementById(slotId);
    if (!slot) return;
    opts = opts || {};
    series = series || [];
    var w = 640, h = 200, pad = 34;
    var max = Math.max(1, ...series.map(function (p) { return Number(p.value) || 0; }));
    var stepX = (w - pad * 2) / Math.max(1, series.length - 1);
    var coord = function (p, i) {
      var x = pad + i * stepX;
      var y = h - pad - ((Number(p.value) || 0) / max) * (h - pad * 2);
      return [x, y];
    };
    var pts = series.map(function (p, i) {
      var c = coord(p, i); return c[0].toFixed(1) + "," + c[1].toFixed(1);
    });
    var dots = series.map(function (p, i) {
      var c = coord(p, i);
      return '<circle cx="' + c[0].toFixed(1) + '" cy="' + c[1].toFixed(1) + '" r="3.5" class="dot"/>';
    }).join("");
    var labels = series.map(function (p, i) {
      var x = pad + i * stepX;
      return '<text x="' + x.toFixed(1) + '" y="' + (h - 10) + '" class="tick" text-anchor="middle">' +
             (p.label || "") + '</text>';
    }).join("");
    slot.innerHTML =
      '<svg viewBox="0 0 ' + w + ' ' + h + '" preserveAspectRatio="none">' +
      '<line x1="' + pad + '" y1="' + (h - pad) + '" x2="' + (w - pad) + '" y2="' + (h - pad) + '" class="axis"/>' +
      '<polyline points="' + pts.join(" ") + '" class="line"/>' + dots +
      '<text x="' + pad + '" y="' + (pad - 12) + '" class="tick">' + (opts.prefix || "") + fmt(max) + '</text>' +
      labels + '</svg>';
  };

  // Dikey cubuk grafik: aylik komisyon / islem hacmi gibi.
  window.mokaBarChart = function (slotId, series, opts) {
    var slot = document.getElementById(slotId);
    if (!slot) return;
    opts = opts || {};
    series = series || [];
    var w = 640, h = 200, pad = 34;
    var max = Math.max(1, ...series.map(function (p) { return Number(p.value) || 0; }));
    var barW = (w - pad * 2) / Math.max(1, series.length);
    var bars = series.map(function (p, i) {
      var v = Number(p.value) || 0;
      var bh = (v / max) * (h - pad * 2);
      var x = pad + i * barW;
      var y = h - pad - bh;
      return '<rect x="' + (x + 4).toFixed(1) + '" y="' + y.toFixed(1) +
             '" width="' + Math.max(1, barW - 8).toFixed(1) + '" height="' + Math.max(0.5, bh).toFixed(1) +
             '" rx="3" class="bar office"/>' +
             '<text x="' + (x + barW / 2).toFixed(1) + '" y="' + (h - 10) +
             '" class="tick" text-anchor="middle">' + (p.label || "") + '</text>';
    }).join("");
    slot.innerHTML =
      '<svg viewBox="0 0 ' + w + ' ' + h + '" preserveAspectRatio="none">' + bars +
      '<text x="' + pad + '" y="' + (pad - 12) + '" class="tick">' + (opts.prefix || "") + fmt(max) + '</text>' +
      '</svg>';
  };
})();
