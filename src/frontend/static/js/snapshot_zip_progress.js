/**
 * Team snapshot zip: peek external_agents.json and show a compact upload progress bar.
 * Depends on global JSZip (load jszip.min.js before this file).
 */
(function () {
  'use strict';

  var MS_PER_OPENCLAW = 5000;
  var BASE_MS = 2500;
  /** Above LLM banner (~99999) and mobile overlays (~10k); avoid huge values (some engines clamp oddly). */
  var PANEL_Z = 500000;

  function _langZh() {
    try {
      return (
        String(document.documentElement.lang || '').toLowerCase().indexOf('zh') === 0 ||
        String(localStorage.getItem('clawcross_lang') || '') === 'zh'
      );
    } catch (e) {
      return true;
    }
  }

  /**
   * @param {File|Blob} file
   * @returns {Promise<{ openclaw: number }>}
   */
  function teamSnapshotCountOpenclawInZip(file) {
    if (!file || typeof JSZip === 'undefined') {
      return Promise.resolve({ openclaw: 0 });
    }
    return JSZip.loadAsync(file)
      .then(function (zip) {
        var f = zip.file('external_agents.json');
        if (!f) return { openclaw: 0 };
        return f.async('string').then(function (text) {
          var data;
          try {
            data = JSON.parse(text);
          } catch (e) {
            return { openclaw: 0 };
          }
          if (!Array.isArray(data)) return { openclaw: 0 };
          var openclaw = 0;
          for (var i = 0; i < data.length; i++) {
            var a = data[i];
            if (!a || String(a.tag).toLowerCase() !== 'openclaw') continue;
            openclaw++;
          }
          return { openclaw: openclaw };
        });
      })
      .catch(function (e) {
        // console.warn('[snapshot_zip_progress] zip peek failed', e);
        return { openclaw: 0 };
      });
  }

  function _ensurePanel() {
    var el = document.getElementById('team-snapshot-progress-panel');
    if (el) {
      /* Last child of block body avoids iOS “fixed inside flex” bugs; migrate off html if needed. */
      if (el.parentNode !== document.body) {
        document.body.appendChild(el);
      }
      return el;
    }
    el = document.createElement('div');
    el.id = 'team-snapshot-progress-panel';
    el.setAttribute('role', 'status');
    el.style.cssText =
      'display:none;box-sizing:border-box;position:fixed;left:16px;right:16px;' +
      'bottom:calc(24px + env(safe-area-inset-bottom,0px));' +
      'max-width:360px;width:auto;margin:0 auto;z-index:' +
      PANEL_Z +
      ';' +
      'background:#fff;border-radius:10px;padding:14px 16px 16px;' +
      'box-shadow:0 12px 48px rgba(0,0,0,.22),0 0 0 1px rgba(15,23,42,.08);';
    el.innerHTML =
      '<div id="team-snapshot-progress-title" style="font-weight:600;margin-bottom:6px;font-size:14px;color:#0f172a;"></div>' +
      '<div id="team-snapshot-progress-detail" style="font-size:12px;color:#475569;margin-bottom:12px;line-height:1.45;"></div>' +
      '<div id="team-snapshot-progress-track" style="height:10px;background:#e2e8f0;border-radius:5px;overflow:hidden;border:1px solid #cbd5e1;">' +
      '<div id="team-snapshot-progress-bar" style="height:100%;width:0%;background:#4f46e5;border-radius:4px;transition:width .15s linear;"></div>' +
      '</div>' +
      '<div id="team-snapshot-progress-pct" style="text-align:right;font-size:11px;color:#64748b;margin-top:6px;font-variant-numeric:tabular-nums;">0%</div>';
    document.body.appendChild(el);
    return el;
  }

  function _setPanelVisible(panel, visible) {
    if (visible) {
      panel.style.display = 'block';
      panel.style.visibility = 'visible';
      panel.style.opacity = '1';
    } else {
      panel.style.display = 'none';
      panel.style.visibility = 'hidden';
      panel.style.opacity = '0';
    }
  }

  /**
   * @param {{ openclaw: number }} counts
   * @param {() => Promise<Response>} doFetch
   * @returns {Promise<Response>}
   */
  function teamSnapshotUploadWithProgress(counts, doFetch) {
    var openclawCount = counts && typeof counts.openclaw === 'number' ? counts.openclaw : 0;
    var panel = _ensurePanel();
    var bar = document.getElementById('team-snapshot-progress-bar');
    var pctEl = document.getElementById('team-snapshot-progress-pct');
    var titleEl = document.getElementById('team-snapshot-progress-title');
    var detailEl = document.getElementById('team-snapshot-progress-detail');
    var zh = _langZh();
    titleEl.textContent = zh ? '正在恢复快照…' : 'Restoring team snapshot…';
    detailEl.textContent =
      openclawCount > 0
        ? zh
          ? '共 ' + openclawCount + ' 个外部 Agent（OpenClaw）'
          : openclawCount + ' external agent(s) (OpenClaw)'
        : zh
          ? '未在 zip 中发现 OpenClaw 外部 Agent'
          : 'No OpenClaw external agents in zip';
    var estimated = Math.max(3500, BASE_MS + openclawCount * MS_PER_OPENCLAW);
    _setPanelVisible(panel, true);
    bar.style.width = '8%';
    pctEl.textContent = '8%';
    var t0 = Date.now();
    var tick = setInterval(function () {
      var elapsed = Date.now() - t0;
      var cap = Math.min(94, 8 + (elapsed / estimated) * 86);
      bar.style.width = cap + '%';
      pctEl.textContent = Math.round(cap) + '%';
    }, 120);
    return doFetch()
      .then(function (resp) {
        clearInterval(tick);
        bar.style.width = '100%';
        pctEl.textContent = '100%';
        return new Promise(function (resolve) {
          setTimeout(function () {
            resolve(resp);
          }, 220);
        });
      })
      .catch(function (err) {
        clearInterval(tick);
        detailEl.textContent = zh ? '上传失败' : 'Upload failed';
        return new Promise(function (_, reject) {
          setTimeout(function () {
            _setPanelVisible(panel, false);
            bar.style.width = '0%';
            pctEl.textContent = '0%';
            reject(err);
          }, 450);
        });
      })
      .then(function (resp) {
        _setPanelVisible(panel, false);
        bar.style.width = '0%';
        pctEl.textContent = '0%';
        return resp;
      });
  }

  /**
   * Show panel while reading zip, then run upload + progress (single entry for upload UIs).
   * @param {File} file
   * @param {FormData} formData
   * @returns {Promise<Response>}
   */
  function teamSnapshotUploadZipWithProgress(file, formData) {
    var panel = _ensurePanel();
    var bar = document.getElementById('team-snapshot-progress-bar');
    var pctEl = document.getElementById('team-snapshot-progress-pct');
    var titleEl = document.getElementById('team-snapshot-progress-title');
    var detailEl = document.getElementById('team-snapshot-progress-detail');
    var zh = _langZh();
    titleEl.textContent = zh ? '正在恢复快照…' : 'Restoring team snapshot…';
    detailEl.textContent = zh ? '正在读取 zip…' : 'Reading zip…';
    _setPanelVisible(panel, true);
    bar.style.width = '5%';
    pctEl.textContent = '…';
    /* Let the browser paint the panel before sync/heavy zip work (mobile main thread). */
    return new Promise(function (resolve) {
      requestAnimationFrame(function () {
        requestAnimationFrame(function () {
          resolve();
        });
      });
    }).then(function () {
      return teamSnapshotCountOpenclawInZip(file).then(function (counts) {
        return teamSnapshotUploadWithProgress(counts, function () {
          return fetch('/teams/snapshot/upload', {
            method: 'POST',
            body: formData,
          });
        });
      });
    });
  }

  window.teamSnapshotCountOpenclawInZip = teamSnapshotCountOpenclawInZip;
  window.teamSnapshotUploadWithProgress = teamSnapshotUploadWithProgress;
  window.teamSnapshotUploadZipWithProgress = teamSnapshotUploadZipWithProgress;
})();
