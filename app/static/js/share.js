/* Share a DOM section as a PNG image.
 *
 * shareAsImage(elementId, filename):
 *   - renders the element to a canvas via html2canvas (loaded separately),
 *   - hides any [data-share-exclude] children during capture (buttons, filters),
 *   - uses the Web Share API with a file when available (mobile -> WhatsApp, etc.),
 *   - otherwise falls back to downloading the PNG.
 */
async function shareAsImage(elementId, filename) {
  const el = document.getElementById(elementId);
  if (!el) return;
  if (typeof html2canvas === 'undefined') {
    alert('Image sharing is unavailable (html2canvas not loaded).');
    return;
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
    const blob = await new Promise((res) => canvas.toBlob(res, 'image/png'));
    if (!blob) throw new Error('Could not render the image.');
    const file = new File([blob], filename + '.png', { type: 'image/png' });

    if (navigator.canShare && navigator.canShare({ files: [file] })) {
      await navigator.share({ files: [file], title: 'Life in Frame', text: filename });
    } else {
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = file.name;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    }
  } catch (err) {
    if (err && err.name === 'AbortError') return; // user dismissed the share sheet
    console.error('shareAsImage failed', err);
    alert('Could not generate the image: ' + (err && err.message ? err.message : err));
  } finally {
    hidden.forEach((n) => { n.style.display = n.dataset._prevDisplay || ''; delete n.dataset._prevDisplay; });
  }
}
window.shareAsImage = shareAsImage;
