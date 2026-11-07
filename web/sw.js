/* Offline shell only. Never cache API responses, projects, tokens or conversations. */
const CACHE='takumi-app-native-phone-v9';
const SHELL=['/','/index.html','/styles.css','/app.js','/run-stream.js','/mobile.js','/theme.js','/mark-clean.png','/kage.js','/vibe.js','/kage/ren-256.webp','/kage/kiri-256.webp','/kage/hana-256.webp','/kage/tetsu-256.webp','/kage/akane.png','/icon-192.png','/icon-512.png','/manifest.webmanifest'];
self.addEventListener('install', e => e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting())));
self.addEventListener('activate', e => e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k=>k!==CACHE).map(k=>caches.delete(k)))).then(()=>self.clients.claim())));
self.addEventListener('fetch', e => { const u=new URL(e.request.url); if(e.request.method!=='GET'||u.origin!==self.location.origin||u.pathname.startsWith('/api/'))return; if(SHELL.includes(u.pathname)){e.respondWith(caches.match(u.pathname).then(r=>r||fetch(e.request)));return;}e.respondWith(fetch(e.request).catch(()=>Response.error())); });
