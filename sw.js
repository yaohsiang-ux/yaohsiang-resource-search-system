/* 臺北市A單位搜尋系統 Service Worker
 * 策略：
 * - HTML（導覽請求）與 data/*.json：network-first（在線永遠最新），離線退快取
 * - 靜態資源（圖片/manifest/icon）：cache-first
 * 快取版本隨 CACHE 名稱失效；資料 JSON 每次成功抓取即更新快取副本
 */
const CACHE = 'yaoxiang-aunit-v1';

const PRECACHE = [
  './',
  'index.html',
  'manifest.json',
  'logo.jpg',
  'icon-192.png',
  'img1_hip.jpg', 'img2_thigh.jpg', 'img3_calf.jpg',
  'img4_shoulder.jpg', 'img5_arm.jpg', 'img6_axilla.jpg',
  'data/a_units.json', 'data/c_stations.json', 'data/ei_products.json',
  'data/workshops.json', 'data/xiaozuosuo.json', 'data/vendors.json',
  'data/nursing_homes.json', 'data/elder.json', 'data/small_multi.json',
  'data/residential.json', 'data/daycare.json',
  'data/address_index.json'
];

self.addEventListener('install', (e) => {
  e.waitUntil(
    caches.open(CACHE)
      .then(c => c.addAll(PRECACHE))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET' || url.origin !== location.origin) return;

  const isHTML = e.request.mode === 'navigate' || url.pathname.endsWith('.html');
  const isData = url.pathname.includes('/data/') && url.pathname.endsWith('.json');

  if (isHTML || isData) {
    // network-first：在線最新、離線退快取
    e.respondWith(
      fetch(e.request)
        .then(res => {
          const copy = res.clone();
          caches.open(CACHE).then(c => c.put(e.request, copy));
          return res;
        })
        .catch(() => caches.match(e.request).then(hit => hit || caches.match('index.html')))
    );
  } else {
    // cache-first：靜態資源
    e.respondWith(
      caches.match(e.request).then(hit => hit || fetch(e.request).then(res => {
        const copy = res.clone();
        caches.open(CACHE).then(c => c.put(e.request, copy));
        return res;
      }))
    );
  }
});
