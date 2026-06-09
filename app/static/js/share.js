/* Capture a DOM section as a PNG and either share it or download it.
 *
 *   shareAsImage(elementId, filename)    -> Web Share API (mobile -> WhatsApp), else download
 *   downloadAsImage(elementId, filename) -> always downloads the PNG directly
 *
 * Any [data-share-exclude] children (buttons, filter bars) are hidden during capture.
 * Requires html2canvas to be loaded on the page.
 */
async function _capturePng(elementId) {
  const el = document.getElementById(elementId);
  if (!el) return null;
  if (typeof html2canvas === 'undefined') {
    alert('Image capture is unavailable (html2canvas not loaded).');
    return null;
  }
  const hidden = el.querySelectorAll('[data-share-exclude]');
  hidden.forEach((n) => { n.dataset._prevDisplay = n.style.display; n.style.display = 'none'; });
  try {
    const canvas = await html2canvas(el, {
      backgroundColor: '#ffffff',
      scale: window.devicePixelRatio > 1 ? 2 : 1,
      useCORS: true,
      logging: false,
    });
    return await new Promise((res) => canvas.toBlob(res, 'image/png'));
  } finally {
    hidden.forEach((n) => { n.style.display = n.dataset._prevDisplay || ''; delete n.dataset._prevDisplay; });
  }
}

function _downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename + '.png';
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

async function shareAsImage(elementId, filename) {
  try {
    const blob = await _capturePng(elementId);
    if (!blob) return;
    const file = new File([blob], filename + '.png', { type: 'image/png' });
    if (navigator.canShare && navigator.canShare({ files: [file] })) {
      await navigator.share({ files: [file], title: 'Life in Frame', text: filename });
    } else {
      _downloadBlob(blob, filename); // desktop fallback
    }
  } catch (err) {
    if (err && err.name === 'AbortError') return; // user dismissed the share sheet
    console.error('shareAsImage failed', err);
    alert('Could not generate the image: ' + (err && err.message ? err.message : err));
  }
}

async function downloadAsImage(elementId, filename) {
  try {
    const blob = await _capturePng(elementId);
    if (!blob) return;
    _downloadBlob(blob, filename);
  } catch (err) {
    console.error('downloadAsImage failed', err);
    alert('Could not generate the image: ' + (err && err.message ? err.message : err));
  }
}

window.shareAsImage = shareAsImage;
window.downloadAsImage = downloadAsImage;
