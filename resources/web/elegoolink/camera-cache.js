// WebKit keeps MJPEG image requests alive after Angular removes the image.
// Give each visible camera an image-only frame: removing that frame cancels its
// loader without stopping printer uploads, fetches, or the page's WebSocket.
(() => {
    if (location.protocol !== 'http:' && location.protocol !== 'https:') return;

    const src = Object.getOwnPropertyDescriptor(HTMLImageElement.prototype, 'src');
    const placeholder = 'data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360"/>';
    const cameras = new Map();
    const stop = camera => {
        if (camera.frame) {
            camera.frame.contentWindow?.stop();
            camera.frame.remove();
            camera.frame = null;
        }
    };
    const update = (img, camera) => {
        if (!img.isConnected || !img.getClientRects().length) {
            stop(camera);
            return;
        }
        if (camera.frame) return;
        const url = new URL(camera.url);
        url.searchParams.set('_orca_stream', Date.now().toString(36) + Math.random().toString(36).slice(2));
        const escaped_url = url.href.replace(/&/g, '&amp;').replace(/"/g, '&quot;');
        const frame = document.createElement('iframe');
        frame.setAttribute('sandbox', 'allow-same-origin');
        frame.setAttribute('aria-hidden', 'true');
        frame.tabIndex = -1;
        frame.style.cssText = 'position:absolute;inset:0;width:100%;height:100%;border:0;pointer-events:none';
        frame.srcdoc = '<style>html,body{margin:0;width:100%;height:100%;background:#000}img{display:block;width:100%;height:100%;object-fit:contain}</style><img src="' + escaped_url + '">';
        if (getComputedStyle(img.parentElement).position === 'static') img.parentElement.style.position = 'relative';
        camera.frame = frame;
        img.parentElement.insertBefore(frame, img.parentElement.firstChild);
    };
    const resize = new ResizeObserver(entries => {
        for (const {target} of entries) {
            const camera = cameras.get(target);
            if (camera) update(target, camera);
        }
    });
    const removed = new MutationObserver(() => {
        for (const [img, camera] of cameras) {
            if (!img.isConnected) {
                stop(camera);
                resize.unobserve(img);
                cameras.delete(img);
            }
        }
    });
    addEventListener('pagehide', () => { for (const camera of cameras.values()) stop(camera); });
    addEventListener('pageshow', () => { for (const [img, camera] of cameras) update(img, camera); });
    Object.defineProperty(HTMLImageElement.prototype, 'src', {
        ...src,
        set(value) {
            if (typeof value === 'string' &&
                (this.classList.contains('video-img') || this.classList.contains('video-img-mobile'))) {
                let url;
                try { url = new URL(value, document.baseURI); } catch (_) { /* Use the native setter below. */ }
                if (url?.protocol === 'http:' && url.hostname === location.hostname &&
                    url.port === '3031' && url.pathname === '/video') {
                    const previous = cameras.get(this);
                    if (previous) stop(previous);
                    const camera = {url: url.href, frame: null};
                    if (!cameras.size) removed.observe(document, {childList: true, subtree: true});
                    cameras.set(this, camera);
                    src.set.call(this, placeholder);
                    resize.observe(this);
                    queueMicrotask(() => { if (cameras.get(this) === camera) update(this, camera); });
                    return;
                }
            }
            const camera = cameras.get(this);
            if (camera) {
                stop(camera);
                resize.unobserve(this);
                cameras.delete(this);
            }
            src.set.call(this, value);
        }
    });
})();
