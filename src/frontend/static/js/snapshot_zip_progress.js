/**
 * Team snapshot zip: show a compact upload progress bar while the server restores it.
 */
(function () {
  'use strict';

  var ESTIMATED_MS = 3500;
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
   * @param {() => Promise<Response>} doFetch
   * @returns {Promise<Response>}
   */
  function teamSnapshotUploadWithProgress(doFetch) {
    var panel = _ensurePanel();
    var bar = document.getElementById('team-snapshot-progress-bar');
    var pctEl = document.getElementById('team-snapshot-progress-pct');
    var titleEl = document.getElementById('team-snapshot-progress-title');
    var detailEl = document.getElementById('team-snapshot-progress-detail');
    var zh = _langZh();
    titleEl.textContent = zh ? '正在恢复快照…' : 'Restoring team snapshot…';
    detailEl.textContent = zh ? '正在导入成员、技能和定时任务…' : 'Importing members, skills and alarms…';
    var estimated = ESTIMATED_MS;
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
    detailEl.textContent = zh ? '正在上传…' : 'Uploading…';
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
      return teamSnapshotUploadWithProgress(function () {
        return fetch('/teams/snapshot/upload', {
          method: 'POST',
          body: formData,
        });
      });
    });
  }

  window.teamSnapshotUploadWithProgress = teamSnapshotUploadWithProgress;
  window.teamSnapshotUploadZipWithProgress = teamSnapshotUploadZipWithProgress;
})();
