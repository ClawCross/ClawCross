/* Maps for space apps (served by the kit at ./space-map.js, after ./leaflet.js).
 *
 * Inside mainland China the map shows AutoNavi (高德) tiles, which load there and need no key but
 * use GCJ-02 coordinates; elsewhere it shows OpenStreetMap. Apps keep WGS-84 everywhere and convert
 * only for the map: SpaceMap.show(lat, lng) to place a point, SpaceMap.data(lat, lng) for a click.
 * Requests to the tile and search servers carry this site's origin as referrer, never the page URL
 * (it holds the member's entrance).
 */
(function () {
  'use strict';
  const PI = Math.PI, A = 6378245.0, EE = 0.00669342162296594323;
  const outside = (lat, lng) => lng < 72.004 || lng > 137.8347 || lat < 0.8293 || lat > 55.8271;
  function dLat(x, y) {
    let r = -100 + 2 * x + 3 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * Math.sqrt(Math.abs(x));
    r += (20 * Math.sin(6 * x * PI) + 20 * Math.sin(2 * x * PI)) * 2 / 3;
    r += (20 * Math.sin(y * PI) + 40 * Math.sin(y / 3 * PI)) * 2 / 3;
    r += (160 * Math.sin(y / 12 * PI) + 320 * Math.sin(y * PI / 30)) * 2 / 3;
    return r;
  }
  function dLng(x, y) {
    let r = 300 + x + 2 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * Math.sqrt(Math.abs(x));
    r += (20 * Math.sin(6 * x * PI) + 20 * Math.sin(2 * x * PI)) * 2 / 3;
    r += (20 * Math.sin(x * PI) + 40 * Math.sin(x / 3 * PI)) * 2 / 3;
    r += (150 * Math.sin(x / 12 * PI) + 300 * Math.sin(x / 30 * PI)) * 2 / 3;
    return r;
  }
  /** WGS-84 → GCJ-02 (the offset AutoNavi tiles use inside China); unchanged outside it. */
  function gcj(lat, lng) {
    if (outside(lat, lng)) return [lat, lng];
    const rad = lat / 180 * PI;
    let m = Math.sin(rad); m = 1 - EE * m * m;
    const s = Math.sqrt(m);
    return [lat + dLat(lng - 105, lat - 35) * 180 / ((A * (1 - EE)) / (m * s) * PI),
      lng + dLng(lng - 105, lat - 35) * 180 / (A / s * Math.cos(rad) * PI)];
  }

  let china = true;   // which tiles are showing
  const REFERRER = 'strict-origin';
  window.SpaceMap = {
    /** Adds the base map and keeps it right for where the map is looking. */
    tiles(map) {
      const amap = L.tileLayer('https://webrd0{s}.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=8&x={x}&y={y}&z={z}',
        { subdomains: '1234', maxZoom: 18, referrerPolicy: REFERRER, attribution: '© 高德地图' });
      const osm = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',
        { maxZoom: 19, referrerPolicy: REFERRER, attribution: '© OpenStreetMap' });
      // Crossing the border changes where points go: the page hears 'spacemapchange' and redraws.
      const pick = () => {
        const center = map.getCenter(), inside = !outside(center.lat, center.lng), crossed = inside !== china;
        china = inside;
        if (!map.hasLayer(inside ? amap : osm)) { map.removeLayer(inside ? osm : amap); (inside ? amap : osm).addTo(map); }
        if (crossed) map.fire('spacemapchange');
      };
      map.on('moveend', pick);
      map.whenReady(pick);
      return map;
    },
    /** Where a WGS-84 point goes on the map. */
    show(lat, lng) { return china ? gcj(lat, lng) : [lat, lng]; },
    /** The WGS-84 point under a map position (a click). */
    data(lat, lng) {
      if (!china) return [lat, lng];
      const [a, b] = gcj(lat, lng);
      return [lat * 2 - a, lng * 2 - b];
    },
    /** Place search (OpenStreetMap Nominatim), WGS-84 results. */
    async search(query, limit = 5) {
      const url = 'https://nominatim.openstreetmap.org/search?format=json&limit=' + limit + '&accept-language=zh-CN,en&q=' + encodeURIComponent(query);
      return (await fetch(url, { referrerPolicy: REFERRER })).json();
    },
    /** The place name around a point. */
    async reverse(lat, lng) {
      const url = 'https://nominatim.openstreetmap.org/reverse?format=json&zoom=14&accept-language=zh-CN,en&lat=' + lat + '&lon=' + lng;
      return (await fetch(url, { referrerPolicy: REFERRER })).json();
    }
  };
})();
