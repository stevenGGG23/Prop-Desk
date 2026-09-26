/* =========================================================
   Prop Desk — main.js
   ========================================================= */

// ---------------------------------------------------------
// Chart.js Global Defaults
// ---------------------------------------------------------
(function setupChartDefaults() {
  if (typeof Chart === 'undefined') return;

  Chart.defaults.color = '#94a3b8';
  Chart.defaults.font.family = "'Inter', system-ui, sans-serif";
  Chart.defaults.font.size = 11;
  Chart.defaults.plugins.legend.display = false;
  Chart.defaults.plugins.tooltip.backgroundColor = '#131922';
  Chart.defaults.plugins.tooltip.borderColor = '#222c3a';
  Chart.defaults.plugins.tooltip.borderWidth = 1;
  Chart.defaults.plugins.tooltip.titleColor = '#e8edf5';
  Chart.defaults.plugins.tooltip.bodyColor = '#94a3b8';
  Chart.defaults.plugins.tooltip.padding = 10;
  Chart.defaults.plugins.tooltip.cornerRadius = 4;
  // No shadows globally
  Chart.defaults.elements.line.borderWidth = 2;
  Chart.defaults.elements.point.radius = 0;
  Chart.defaults.elements.point.hoverRadius = 4;
})();

// ---------------------------------------------------------
// initEquityChart(canvasId, equityPoints, floorPoints)
// equityPoints / floorPoints: [{t: isoString, v: float}]
// ---------------------------------------------------------
function initEquityChart(canvasId, equityPoints, floorPoints) {
  const canvas = document.getElementById(canvasId);
  if (!canvas || typeof Chart === 'undefined') return null;

  const labels = equityPoints.map(p => {
    const d = new Date(p.t);
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
  });

  const datasets = [
    {
      label: 'Balance',
      data: equityPoints.map(p => p.v),
      borderColor: '#06b6d4',
      backgroundColor: 'transparent',
      tension: 0.2,
      borderWidth: 2,
    }
  ];

  if (floorPoints && floorPoints.length > 0) {
    datasets.push({
      label: 'Floor',
      data: floorPoints.map(p => p.v),
      borderColor: '#64748b',
      backgroundColor: 'transparent',
      borderDash: [4, 3],
      tension: 0,
      borderWidth: 1.5,
    });
  }

  // Show legend only when multi-series
  const showLegend = datasets.length > 1;

  return new Chart(canvas, {
    type: 'line',
    data: { labels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      plugins: {
        legend: {
          display: showLegend,
          labels: {
            boxWidth: 12,
            padding: 12,
            color: '#94a3b8',
            font: { size: 11 },
          }
        },
        tooltip: {
          mode: 'index',
          intersect: false,
          callbacks: {
            label: function(ctx) {
              return ' ' + ctx.dataset.label + ': $' + ctx.parsed.y.toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2});
            }
          }
        }
      },
      scales: {
        x: {
          grid: { color: 'rgba(34,44,58,0.5)', drawBorder: false },
          ticks: {
            maxRotation: 0,
            maxTicksLimit: 8,
            color: '#64748b',
            font: { size: 10 },
          },
          border: { display: false }
        },
        y: {
          grid: { color: 'rgba(34,44,58,0.7)', drawBorder: false },
          ticks: {
            color: '#64748b',
            font: { size: 10 },
            callback: v => '$' + (v/1000).toFixed(0) + 'k'
          },
          border: { display: false }
        }
      }
    }
  });
}

// ---------------------------------------------------------
// initRMultipleChart(canvasId, rMultiples)
// ---------------------------------------------------------
function initRMultipleChart(canvasId, rMultiples) {
  const canvas = document.getElementById(canvasId);
  if (!canvas || typeof Chart === 'undefined' || !rMultiples || rMultiples.length === 0) return null;

  // Bucket into bins
  const min = Math.floor(Math.min(...rMultiples) * 2) / 2;
  const max = Math.ceil(Math.max(...rMultiples) * 2) / 2;
  const step = 0.5;
  const bins = [];
  const counts = [];

  for (let b = min; b < max; b += step) {
    bins.push(b.toFixed(1) + 'R');
    counts.push(rMultiples.filter(r => r >= b && r < b + step).length);
  }

  const colors = bins.map((_, i) => {
    const center = min + i * step + step / 2;
    return center >= 0 ? 'rgba(22,163,74,0.6)' : 'rgba(220,38,38,0.6)';
  });

  return new Chart(canvas, {
    type: 'bar',
    data: {
      labels: bins,
      datasets: [{
        label: 'Trades',
        data: counts,
        backgroundColor: colors,
        borderColor: 'transparent',
        borderRadius: 2,
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      plugins: {
        legend: { display: false },
      },
      scales: {
        x: {
          grid: { color: 'rgba(34,44,58,0.5)', drawBorder: false },
          ticks: { color: '#64748b', font: { size: 10 } },
          border: { display: false }
        },
        y: {
          grid: { color: 'rgba(34,44,58,0.7)', drawBorder: false },
          ticks: { color: '#64748b', font: { size: 10 }, stepSize: 1 },
          border: { display: false }
        }
      }
    }
  });
}

// ---------------------------------------------------------
// initEquityCurvePortfolio(canvasId, dates, values)
// ---------------------------------------------------------
function initEquityCurvePortfolio(canvasId, dates, values) {
  const canvas = document.getElementById(canvasId);
  if (!canvas || typeof Chart === 'undefined' || !dates || dates.length === 0) return null;

  return new Chart(canvas, {
    type: 'line',
    data: {
      labels: dates,
      datasets: [{
        label: 'Cumulative P&L',
        data: values,
        borderColor: '#06b6d4',
        backgroundColor: 'rgba(6,182,212,0.07)',
        fill: true,
        tension: 0.2,
        borderWidth: 2,
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: ctx => ' $' + ctx.parsed.y.toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2})
          }
        }
      },
      scales: {
        x: {
          grid: { color: 'rgba(34,44,58,0.5)', drawBorder: false },
          ticks: { color: '#64748b', font: { size: 10 }, maxTicksLimit: 10, maxRotation: 0 },
          border: { display: false }
        },
        y: {
          grid: { color: 'rgba(34,44,58,0.7)', drawBorder: false },
          ticks: {
            color: '#64748b',
            font: { size: 10 },
            callback: v => '$' + (v >= 0 ? '' : '-') + Math.abs(v/1000).toFixed(1) + 'k'
          },
          border: { display: false }
        }
      }
    }
  });
}

// ---------------------------------------------------------
// Loading State Helpers
// ---------------------------------------------------------
function setLoading(btn, isLoading) {
  if (!btn) return;
  if (isLoading) {
    btn._origText = btn.innerHTML;
    btn.innerHTML = '<span class="spinner"></span> Running…';
    btn.disabled = true;
  } else {
    btn.innerHTML = btn._origText || 'Simulate';
    btn.disabled = false;
  }
}

// ---------------------------------------------------------
// simulateAccount(accountId, risks, resultContainerId)
// Fetches /api/simulate and writes pass odds into DOM
// ---------------------------------------------------------
function simulateAccount(accountId, risks, resultContainerId, btnEl) {
  if (!accountId || !risks || risks.length === 0) return;

  const container = document.getElementById(resultContainerId);
  setLoading(btnEl, true);

  const params = new URLSearchParams();
  params.set('account_id', accountId);
  risks.forEach(r => params.append('risk[]', r));

  fetch('/api/simulate?' + params.toString())
    .then(res => {
      if (!res.ok) throw new Error('HTTP ' + res.status);
      return res.json();
    })
    .then(data => {
      setLoading(btnEl, false);

      if (data.error) {
        if (container) container.innerHTML = '<span class="sim-result text-dim">' + escHtml(data.error) + '</span>';
        return;
      }

      if (!container) return;

      // data is array of result objects
      const results = Array.isArray(data) ? data : (data.results || []);

      if (results.length === 0) {
        container.innerHTML = '<span class="sim-result text-dim">No results</span>';
        return;
      }

      const lines = results.map(r => {
        const passPct = (r.pass_pct !== undefined ? r.pass_pct : ((r.pass_prob || 0) * 100)).toFixed(1);
        const breachPct = (r.breach_pct !== undefined ? r.breach_pct : ((r.breach_prob || 0) * 100)).toFixed(1);
        const medDays = r.median_calendar_days || r.median_days || '—';
        const risk = r.risk || risks[0];
        return (
          '<span class="sim-result">' +
          '$' + Number(risk).toLocaleString('en-US') + ': ' +
          '<span class="sim-pass">' + passPct + '% pass</span>' +
          ' / ' +
          '<span class="sim-breach">' + breachPct + '% breach</span>' +
          ' — ~' + medDays + ' days' +
          '</span>'
        );
      });

      container.innerHTML = lines.join('<br>');
    })
    .catch(err => {
      setLoading(btnEl, false);
      if (container) container.innerHTML = '<span class="sim-result text-dim">Simulation error: ' + escHtml(err.message) + '</span>';
    });
}

// ---------------------------------------------------------
// loadPortfolioRisk()
// Fetches /api/portfolio-risk and writes into #portfolio-risk-container
// ---------------------------------------------------------
function loadPortfolioRisk() {
  const el = document.getElementById('portfolio-risk-container');
  if (!el) return;

  fetch('/api/portfolio-risk')
    .then(res => {
      if (!res.ok) throw new Error('HTTP ' + res.status);
      return res.json();
    })
    .then(data => {
      if (data.error) {
        el.innerHTML = '<span class="text-dim" style="font-size:12px;">' + escHtml(data.error) + '</span>';
        return;
      }

      const jointPct = data.joint_breach_pct !== undefined ? data.joint_breach_pct : data.joint_breach_prob;
      const anyPct   = data.any_breach_pct;
      const nAcc     = data.n_accounts || data.per_account?.length || '—';
      const nRuns    = data.n_runs || 0;

      let html = '';
      if (jointPct !== undefined && jointPct !== null) {
        const color = jointPct > 50 ? 'var(--red)' : jointPct > 20 ? '#eab308' : 'var(--green)';
        html += '<span class="num" style="font-weight:600;color:' + color + ';">' + jointPct + '%</span>';
        html += '<span class="text-dim" style="font-size:11px;margin-left:6px;">joint wipeout (' + nAcc + ' accts, ' + nRuns.toLocaleString() + ' runs)</span>';
      }
      if (anyPct !== undefined) {
        html += '<br><span style="font-size:12px;color:var(--ink-dim);">Any breach: ' + anyPct + '%</span>';
      }
      el.innerHTML = html || '<span class="text-dim" style="font-size:12px;">No data</span>';
    })
    .catch(err => {
      el.innerHTML = '<span class="text-dim" style="font-size:12px;">' + escHtml(err.message) + '</span>';
    });
}

// ---------------------------------------------------------
// Disclosure toggles (add trade, add payout etc.)
// ---------------------------------------------------------
function initDisclosures() {
  document.querySelectorAll('[data-toggle]').forEach(btn => {
    btn.addEventListener('click', function() {
      const targetId = this.getAttribute('data-toggle');
      const body = document.getElementById(targetId);
      if (!body) return;
      const isOpen = body.classList.contains('is-open');
      body.classList.toggle('is-open', !isOpen);
      this.textContent = isOpen ? (this.getAttribute('data-open-text') || '+ Show form') : (this.getAttribute('data-close-text') || 'Hide');
    });
  });
}

// ---------------------------------------------------------
// Simulate buttons on account cards / detail pages
// ---------------------------------------------------------
function initSimulateButtons() {
  document.querySelectorAll('[data-simulate]').forEach(btn => {
    btn.addEventListener('click', function(e) {
      e.preventDefault();
      const accountId = this.getAttribute('data-account-id');
      const risksRaw  = this.getAttribute('data-risks');
      const resultId  = this.getAttribute('data-result');

      let risks = [];
      try {
        risks = JSON.parse(risksRaw);
      } catch(_) {
        const r = parseFloat(risksRaw);
        if (!isNaN(r)) risks = [r];
      }

      if (!accountId || risks.length === 0) return;
      simulateAccount(parseInt(accountId), risks, resultId, this);
    });
  });
}

// ---------------------------------------------------------
// Flash auto-dismiss (after 5s)
// ---------------------------------------------------------
function initFlashDismiss() {
  setTimeout(function() {
    document.querySelectorAll('.flash').forEach(el => {
      el.style.transition = 'opacity 0.4s';
      el.style.opacity = '0';
      setTimeout(() => el.remove(), 400);
    });
  }, 5000);
}

// ---------------------------------------------------------
// CSV upload handler (stats page)
// ---------------------------------------------------------
function initCsvUpload() {
  const form = document.getElementById('csv-upload-form');
  if (!form) return;

  const resultEl = document.getElementById('csv-upload-result');
  const btn      = form.querySelector('button[type="submit"]');
  let confirmReady = false;

  function resetPreview() {
    confirmReady = false;
    btn.textContent = 'Preview CSV';
    if (resultEl) resultEl.textContent = '';
  }

  form.addEventListener('input', resetPreview);
  form.addEventListener('change', resetPreview);

  form.addEventListener('submit', async function(e) {
    e.preventDefault();
    const fd = new FormData(form);
    const importing = confirmReady;
    fd.set('confirm_import', importing ? '1' : '0');
    setLoading(btn, true);
    try {
      const response = await fetch('/api/import-csv', { method: 'POST', body: fd });
      const data = await response.json();
      setLoading(btn, false);
      if (data.error && !data.preview) {
        if (resultEl) resultEl.innerHTML = '<p class="text-loss">' + escHtml(data.error) + '</p>';
        confirmReady = false;
        btn.textContent = 'Preview CSV';
        return;
      }
      if (data.preview) {
        const errorList = (data.errors || []).map(row =>
          '<li>Row ' + escHtml(row.row) + ': ' + escHtml(row.error) + '</li>'
        ).join('');
        const rows = (data.sample || []).map(row =>
          '<tr><td>' + escHtml(row.date.slice(0, 10)) + '</td><td class="num">$' +
          Number(row.pnl).toLocaleString('en-US', {minimumFractionDigits: 2}) +
          '</td><td>' + escHtml(row.direction || '—') + '</td><td>' +
          escHtml(row.quantity == null ? '—' : row.quantity) + '</td></tr>'
        ).join('');
        if (resultEl) resultEl.innerHTML =
          '<p>' + data.count + ' new trade(s); ' + (data.duplicates || 0) + ' duplicate(s) skipped. Net P&amp;L: $' +
          Number(data.net_pnl).toLocaleString('en-US', {minimumFractionDigits: 2}) +
          '. Projected account balance: $' + Number(data.projected_balance).toLocaleString('en-US', {minimumFractionDigits: 2}) + '.</p>' +
          (errorList ? '<ul class="import-errors">' + errorList + '</ul>' : '') +
          (rows ? '<div class="table-scroll"><table class="table"><thead><tr><th>Date</th><th class="num-col">P&amp;L</th><th>Side</th><th>Qty</th></tr></thead><tbody>' + rows + '</tbody></table></div>' : '');
        confirmReady = data.count > 0 && !(data.errors || []).length;
        btn.textContent = confirmReady ? 'Import ' + data.count + ' trades' : 'Preview CSV';
      } else {
        confirmReady = false;
        btn.textContent = 'Preview CSV';
        const dayMessage = data.closed_days
          ? 'closed ' + data.closed_days + ' day(s).'
          : 'left included day(s) open for more fills.';
        if (resultEl) resultEl.innerHTML =
          '<p class="text-profit">Imported ' + data.imported + ' trade(s), ' + dayMessage +
          ' New balance: $' + Number(data.new_balance).toLocaleString('en-US',
          {minimumFractionDigits: 2}) + '.</p><a class="link" href="/accounts/' +
          encodeURIComponent(fd.get('account_id')) + '">Open account</a>';
      }
    } catch (err) {
      setLoading(btn, false);
      confirmReady = false;
      btn.textContent = 'Preview CSV';
      if (resultEl) resultEl.innerHTML = '<p class="text-loss">' + escHtml(err.message) + '</p>';
    }
  });
}

function initDistributionUpload() {
  const form = document.getElementById('distribution-upload-form');
  if (!form) return;

  const resultEl = document.getElementById('distribution-upload-result');
  const btn = form.querySelector('button[type="submit"]');
  let confirmReady = false;

  function resetPreview() {
    confirmReady = false;
    btn.textContent = 'Preview backtest';
    if (resultEl) resultEl.textContent = '';
  }

  form.addEventListener('input', resetPreview);
  form.addEventListener('change', resetPreview);
  form.addEventListener('submit', async function(event) {
    event.preventDefault();
    const importing = confirmReady;
    const data = new FormData(form);
    data.set('confirm_import', importing ? '1' : '0');
    setLoading(btn, true);
    try {
      const response = await fetch('/api/import-distribution', { method: 'POST', body: data });
      const result = await response.json();
      setLoading(btn, false);
      if (result.error && !result.preview) {
        if (resultEl) resultEl.innerHTML = '<p class="text-loss">' + escHtml(result.error) + '</p>';
        confirmReady = false;
        btn.textContent = 'Preview backtest';
        return;
      }
      if (result.preview) {
        const errors = (result.errors || []).map(row =>
          '<li>Row ' + escHtml(row.row) + ': ' + escHtml(row.error) + '</li>'
        ).join('');
        const rate = result.count ? (result.wins / result.count * 100).toFixed(1) : '0.0';
        if (resultEl) resultEl.innerHTML = '<p>' + result.count + ' outcomes · ' + result.wins +
          ' wins · ' + result.losses + ' losses · ' + rate + '% win rate.</p>' +
          (errors ? '<ul class="import-errors">' + errors + '</ul>' : '');
        confirmReady = result.count > 1 && result.wins > 0 && result.losses > 0 && !errors;
        btn.textContent = confirmReady ? 'Create distribution and link account' : 'Preview backtest';
      } else {
        confirmReady = false;
        btn.textContent = 'Preview backtest';
        if (resultEl) resultEl.innerHTML = '<p class="text-profit">Created ' + escHtml(result.distribution) +
          ' · ' + result.win_rate + '% win rate. The account is linked; live balance was not changed.</p>';
      }
    } catch (error) {
      setLoading(btn, false);
      confirmReady = false;
      btn.textContent = 'Preview backtest';
      if (resultEl) resultEl.innerHTML = '<p class="text-loss">' + escHtml(error.message) + '</p>';
    }
  });
}

document.addEventListener('DOMContentLoaded', function() {
  initFlashDismiss();
  initCsvUpload();
  initDistributionUpload();
});

// ---------------------------------------------------------
// Utility
// ---------------------------------------------------------
function escHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// ---------------------------------------------------------
// DOMContentLoaded bootstrap
// ---------------------------------------------------------
document.addEventListener('DOMContentLoaded', function() {
  initDisclosures();
  initSimulateButtons();
  initFlashDismiss();
  initCsvUpload();

  // Dashboard: load portfolio risk if container present
  if (document.getElementById('portfolio-risk-container')) {
    loadPortfolioRisk();
  }
});
