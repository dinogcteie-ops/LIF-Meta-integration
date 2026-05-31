/**
 * LIF App — Core UI JavaScript
 * Sprint 2: Mobile sidebar toggle
 * Sprint 4: Design system interactions
 * Sprint 5: Sparkline rendering
 */

// ── Mobile Sidebar Toggle ─────────────────────────────────────────────────
(function() {
  const toggle = document.getElementById('sidebarToggle');
  const sidebar = document.getElementById('appSidebar');
  const overlay = document.getElementById('sidebarOverlay');

  function openSidebar() {
    sidebar.classList.add('open');
    overlay.classList.add('open');
    document.body.classList.add('sidebar-open');
  }

  function closeSidebar() {
    sidebar.classList.remove('open');
    overlay.classList.remove('open');
    document.body.classList.remove('sidebar-open');
  }

  if (toggle) toggle.addEventListener('click', openSidebar);
  if (overlay) overlay.addEventListener('click', closeSidebar);

  // Close sidebar on link click (mobile)
  document.querySelectorAll('.sidebar-link').forEach(link => {
    link.addEventListener('click', () => {
      if (window.innerWidth < 992) closeSidebar();
    });
  });
})();

// ── Sparkline Renderer ────────────────────────────────────────────────────
function renderSparkline(canvas, data, color) {
  if (!canvas || !data || data.length === 0) return;
  const ctx = canvas.getContext('2d');
  const w = canvas.width = canvas.offsetWidth * 2;
  const h = canvas.height = canvas.offsetHeight * 2;
  ctx.scale(2, 2);  // retina

  const displayW = canvas.offsetWidth;
  const displayH = canvas.offsetHeight;

  const max = Math.max(...data, 1);
  const min = Math.min(...data, 0);
  const range = max - min || 1;
  const padding = 4;

  ctx.beginPath();
  ctx.strokeStyle = color || '#c9a84c';
  ctx.lineWidth = 2;
  ctx.lineJoin = 'round';
  ctx.lineCap = 'round';

  data.forEach((val, i) => {
    const x = padding + (i / (data.length - 1)) * (displayW - padding * 2);
    const y = displayH - padding - ((val - min) / range) * (displayH - padding * 2);
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();

  // Fill gradient below
  const lastX = padding + ((data.length - 1) / (data.length - 1)) * (displayW - padding * 2);
  ctx.lineTo(lastX, displayH);
  ctx.lineTo(padding, displayH);
  ctx.closePath();

  const gradient = ctx.createLinearGradient(0, 0, 0, displayH);
  gradient.addColorStop(0, (color || '#c9a84c') + '30');
  gradient.addColorStop(1, (color || '#c9a84c') + '05');
  ctx.fillStyle = gradient;
  ctx.fill();
}

// Initialize all sparklines on page load
document.addEventListener('DOMContentLoaded', function() {
  document.querySelectorAll('[data-sparkline]').forEach(canvas => {
    try {
      const data = JSON.parse(canvas.dataset.sparkline);
      const color = canvas.dataset.sparklineColor || '#c9a84c';
      renderSparkline(canvas, data, color);
    } catch (e) {}
  });
});

// ── Toast Notifications ───────────────────────────────────────────────────
function showToast(message, type = 'success') {
  const toast = document.createElement('div');
  toast.className = `lif-toast lif-toast-${type}`;
  toast.innerHTML = `
    <i class="bi bi-${type === 'success' ? 'check-circle' : 'exclamation-circle'}"></i>
    <span>${message}</span>
  `;
  document.body.appendChild(toast);
  setTimeout(() => toast.classList.add('show'), 50);
  setTimeout(() => {
    toast.classList.remove('show');
    setTimeout(() => toast.remove(), 300);
  }, 3000);
}

// ── Responsive Tables ─────────────────────────────────────────────────────
// Auto-wrap tables for horizontal scroll on mobile
document.addEventListener('DOMContentLoaded', function() {
  document.querySelectorAll('.content-inner table:not(.table-responsive *)').forEach(table => {
    if (!table.parentElement.classList.contains('table-responsive')) {
      const wrapper = document.createElement('div');
      wrapper.className = 'table-responsive';
      table.parentNode.insertBefore(wrapper, table);
      wrapper.appendChild(table);
    }
  });
});

// ── Top-bar global search ──────────────────────────────────────────────────
// Instant client-side filter of the rows in the current view's data tables.
document.addEventListener('DOMContentLoaded', function () {
  const box = document.getElementById('globalSearch');
  if (!box) return;
  box.addEventListener('input', function () {
    const q = box.value.trim().toLowerCase();
    document.querySelectorAll('.content-inner table tbody').forEach(tbody => {
      tbody.querySelectorAll('tr').forEach(tr => {
        const hit = !q || tr.textContent.toLowerCase().includes(q);
        tr.style.display = hit ? '' : 'none';
      });
    });
  });
});
