
    const BUCKET_MIN = 5;
    const BUCKET_SEC = BUCKET_MIN * 60;
    // Oxygen severity tiers from the account's settings (defaults until loaded).
    let O2 = window.owletO2(null);
    let rollups = [];
    let deviceName = 'your little one';
    let nightOffset = 0; // 0 = most recent night

    const el = id => document.getElementById(id);
    const pad = n => String(n).padStart(2, '0');
    const fmtClock = date => {
      let h = date.getHours(); const m = pad(date.getMinutes());
      const ap = h >= 12 ? 'PM' : 'AM'; h = h % 12 || 12;
      return `${h}:${m} ${ap}`;
    };
    const fmtDur = seconds => {
      const totalMinutes = Math.round(seconds / 60);
      const h = Math.floor(totalMinutes / 60), m = totalMinutes % 60;
      return h ? `${h}h ${pad(m)}m` : `${m}m`;
    };

    // The user's night window (default 7 PM -> 7 AM); loaded from preferences in boot().
    let NIGHT = { start: 19 * 60, end: 7 * 60 };
    function nightWindowFrom(prefs) {
      const toMin = (value, fallback) => {
        const m = /^([01]?\d|2[0-3]):([0-5]\d)$/.exec(value || '');
        return m ? Number(m[1]) * 60 + Number(m[2]) : fallback;
      };
      const win = { start: toMin(prefs.night_start, 19 * 60), end: toMin(prefs.night_end, 7 * 60) };
      return win.start > win.end ? win : { start: 19 * 60, end: 7 * 60 }; // night must cross midnight
    }

    function nightWindow(offset) {
      // A "night" spans the configured window, local time. The most recent
      // night stays current until the next one begins.
      const now = new Date();
      const anchor = new Date(now);
      if (now.getHours() * 60 + now.getMinutes() < NIGHT.start) anchor.setDate(anchor.getDate() - 1);
      anchor.setDate(anchor.getDate() - offset);
      const start = new Date(anchor);
      start.setHours(Math.floor(NIGHT.start / 60), NIGHT.start % 60, 0, 0);
      const end = new Date(start);
      end.setDate(end.getDate() + 1);
      end.setHours(Math.floor(NIGHT.end / 60), NIGHT.end % 60, 0, 0);
      return { start, end, inProgress: offset === 0 && now < end };
    }

    function bucketsIn({ start, end }) {
      return rollups.filter(row => {
        const t = new Date(row.bucket_start);
        return t >= start && t < end;
      });
    }

    function classify(row) {
      const active = (row.sleep_seconds || 0) + (row.awake_seconds || 0);
      if (!active && (row.avg_heart_rate === null || row.avg_heart_rate === undefined)) return 'nodata';
      if ((row.offline_seconds || 0) > BUCKET_SEC * 0.7) return 'nodata';
      if ((row.sleep_seconds || 0) >= (row.awake_seconds || 0) && (row.sleep_seconds || 0) > 60) {
        return (row.deep_sleep_seconds || 0) >= (row.light_sleep_seconds || 0) ? 'deep' : 'light';
      }
      if ((row.awake_seconds || 0) > 60) return 'awake';
      return 'nodata';
    }

    function nightStats(buckets) {
      let sleep = 0, deep = 0, awake = 0;
      let o2sum = 0, o2n = 0, hrSum = 0, hrN = 0, minO2 = null, minO2At = null;
      buckets.forEach(row => {
        sleep += row.sleep_seconds || 0;
        deep += row.deep_sleep_seconds || 0;
        awake += row.awake_seconds || 0;
        if (row.avg_oxygen_saturation != null) { o2sum += row.avg_oxygen_saturation; o2n++; }
        if (row.avg_heart_rate != null) { hrSum += row.avg_heart_rate; hrN++; }
        if (row.min_oxygen_saturation != null && (minO2 === null || row.min_oxygen_saturation < minO2)) {
          minO2 = row.min_oxygen_saturation; minO2At = new Date(row.bucket_start);
        }
      });
      return { sleep, deep, awake,
        avgO2: o2n ? o2sum / o2n : null, avgHr: hrN ? hrSum / hrN : null,
        minO2, minO2At, coveredSec: (o2n + 0) * BUCKET_SEC };
    }

    function dipEvents(buckets) {
      // A "dip" groups adjacent 5m buckets whose floor fell under the warning
      // tier — but the
      // bucket span wildly overstates a blip, so we carry the actual seconds
      // bucket over-reports, so we take the seconds actually spent under it
      // (low_oxygen_seconds) and report THAT.
      const dips = [];
      let current = null;
      buckets.forEach(row => {
        const low = row.min_oxygen_saturation != null && row.min_oxygen_saturation < O2.warn;
        if (low) {
          const lowSec = row.low_oxygen_seconds != null ? row.low_oxygen_seconds : BUCKET_SEC;
          if (!current) {
            current = { start: new Date(row.bucket_start), min: row.min_oxygen_saturation,
                        minAt: new Date(row.bucket_start), lowSec: 0 };
          } else if (row.min_oxygen_saturation < current.min) {
            current.min = row.min_oxygen_saturation;
            current.minAt = new Date(row.bucket_start);
          }
          current.lowSec += lowSec;
          current.end = new Date(new Date(row.bucket_start).getTime() + BUCKET_SEC * 1000);
        } else if (current) { dips.push(current); current = null; }
      });
      if (current) dips.push(current);
      return dips;
    }
    function dipDepthText(dip) {
      if (dip.lowSec < 50) {
        const secs = Math.max(5, Math.round(dip.lowSec / 5) * 5);
        return `Momentary dip — about ${secs}s under ${O2.warn}%`;
      }
      return `About ${fmtDur(dip.lowSec)} under ${O2.warn}%`;
    }

    function narrative(stats, dips, inProgress) {
      if (!stats.coveredSec || stats.coveredSec < 1800) {
        return `Not enough readings for this night — the sock may not have been on.`;
      }
      const name = `<b>${deviceName}</b>`;
      const sleepText = `<b>${fmtDur(stats.sleep)}</b> of sleep` +
        (stats.deep ? ` (${fmtDur(stats.deep)} deep)` : '');
      let mood;
      if (!dips.length) mood = 'a smooth night — oxygen held steady the whole way';
      else if (dips.length === 1) mood = `one brief dip to <b>${Math.round(dips[0].min)}%</b> around ${fmtClock(dips[0].start)}, otherwise steady`;
      else mood = `<b>${dips.length}</b> dips below ${O2.warn}%, the lowest at <b>${Math.round(Math.min(...dips.map(d => d.min)))}%</b>`;
      const capName = name.charAt(0) === '<' ? name.replace(/<b>(.)/, (m, c) => '<b>' + c.toUpperCase()) : name;
      const opener = inProgress ? `So far tonight, ${name} has logged` : `${capName} logged`;
      return `${opener} ${sleepText}, with ${mood}.`;
    }

    // ---- timeline <-> event list linkage -------------------------------------
    const STAGE_WORDS = { deep: 'deep sleep', light: 'light sleep', awake: 'awake', nodata: 'no signal' };
    const dipTier = dip => dip.min < O2.critical ? 'bad' : 'warn';
    // tier is carried by a glyph and a number as well as the colour
    const dipGlyph = dip => dipTier(dip) === 'bad' ? '\u26A0\uFE0E' : '\u2193';
    const dipUnder = dip => dipTier(dip) === 'bad' ? O2.critical : O2.warn;
    let tl = null;            // what the timeline on screen was drawn from
    let currentDips = [];
    let selectedDip = null;   // start time (ms) of the selected dip; survives refreshes
    let shownOffset = 0;

    function renderTimeline(win, buckets, dips) {
      const t0 = win.start.getTime(), t1 = win.end.getTime(), spanMs = t1 - t0;
      const step = BUCKET_SEC * 1000;
      const byTime = new Map(buckets.map(row => [new Date(row.bucket_start).getTime(), row]));
      tl = { t0, spanMs, step, byTime };
      const pct = ms => (ms - t0) / spanMs * 100;

      // Oxygen scale: 100% at the top, down to just under the lowest reading
      // (and always far enough to show both threshold lines).
      const lows = buckets.map(row => row.min_oxygen_saturation).filter(v => v != null && v > 0);
      const floor = Math.max(50, Math.min(O2.critical - 2, lows.length ? Math.floor(Math.min(...lows)) - 1 : 100));
      const yOf = v => 4 + (100 - Math.max(floor, Math.min(100, v))) / (100 - floor) * 92;

      let spans = '';
      const runs = [];
      let run = [];
      for (let t = t0; t < t1; t += step) {
        const row = byTime.get(t);
        spans += `<span class="${row ? classify(row) : 'nodata'}"></span>`;
        const v = row ? (row.min_oxygen_saturation != null ? row.min_oxygen_saturation : row.avg_oxygen_saturation) : null;
        if (v != null && v > 0) run.push([(t + step / 2 - t0) / spanMs * 1000, yOf(v)]);
        else if (run.length) { runs.push(run); run = []; }
      }
      if (run.length) runs.push(run);
      const half = step / spanMs * 500;
      const trace = runs.map(points => {
        if (points.length === 1) points = [[points[0][0] - half, points[0][1]], [points[0][0] + half, points[0][1]]];
        return `<polyline points="${points.map(p => p[0].toFixed(1) + ',' + p[1].toFixed(1)).join(' ')}"/>`;
      }).join('');
      const tiers = [['warn', O2.warn], ['bad', O2.critical]];
      const thresholdLines = tiers.map(([cls, v]) =>
        `<line class="${cls}" x1="0" x2="1000" y1="${yOf(v).toFixed(1)}" y2="${yOf(v).toFixed(1)}"/>`).join('');
      const thresholdLabels = tiers.map(([cls, v]) =>
        `<span class="tl-th ${cls}" style="top:${yOf(v).toFixed(1)}%">${v}%</span>`).join('');

      // Dip markers sit at the dip's lowest point; layoutMarks() then steps
      // neighbours down a row so they do not cover each other.
      let marks = '', stems = '';
      dips.forEach((dip, i) => {
        const x = pct(dip.minAt.getTime() + step / 2);
        const tier = dipTier(dip);
        const low = Math.round(dip.min);
        marks += `<button type="button" class="tl-dip ${tier}" data-dip="${i}" aria-pressed="false"
          style="left:clamp(14px, ${x.toFixed(2)}%, calc(100% - 14px));top:0"
          aria-label="Oxygen dip to ${low}% at ${fmtClock(dip.start)}, under ${dipUnder(dip)}%. Show it in the event list."><span aria-hidden="true">${dipGlyph(dip)}</span><span class="n">${low}%</span></button>`;
        stems += `<i class="tl-stem ${tier}" style="left:${x.toFixed(2)}%;top:24px"></i>`;
      });
      const marksH = dips.length ? 30 : 0;

      const now = Date.now();
      const live = win.inProgress && now > t0 && now < t1;
      const nowMark = live
        ? `<i class="tl-now" style="left:${pct(now).toFixed(2)}%;top:${marksH}px;bottom:18px"><span style="top:100%;bottom:auto">now</span></i>` : '';

      const axis = [];
      for (let k = 0; k <= 4; k++) {
        const d = new Date(t0 + spanMs * k / 4);
        axis.push(`<span>${fmtClock(d)}</span>`);
      }
      const nightFocus = encodeURIComponent(win.start.toISOString());
      const nightSpan = Math.round(spanMs / 60000);
      const dipSummary = dips.length
        ? `${dips.length} oxygen dip${dips.length === 1 ? '' : 's'} marked; each is listed under Oxygen events below.`
        : `No oxygen dips below ${O2.warn}%.`;
      return `<section class="timeline-card card" aria-labelledby="tlTitle">
        <div class="card-head"><h2 class="card-title" id="tlTitle">The night, minute by minute</h2>
          <a href="/data?focus=${nightFocus}&span=${nightSpan}" data-workbench>Raw trace →</a></div>
        <div class="tl-well">
          <p class="sr-only">Timeline from ${fmtClock(win.start)} to ${fmtClock(win.end)}: oxygen above, sleep stages below. ${dipSummary}</p>
          <p class="tl-readout" id="tlReadout"></p>
          <div class="tl-plot" id="tlPlot" style="--tl-pad:${live ? 18 : 0}px;padding-bottom:var(--tl-pad)">
            <div class="tl-marks" style="height:${marksH}px">${marks}</div>
            <div class="tl-row-label"><span>Oxygen · lowest reading in each 5 minutes</span></div>
            <div class="tl-o2"><svg viewBox="0 0 1000 100" preserveAspectRatio="none" aria-hidden="true">${thresholdLines}${trace}</svg>${thresholdLabels}</div>
            <div class="tl-row-label"><span>Sleep · taller is deeper</span></div>
            <div class="tl-stages" aria-hidden="true">${spans}</div>
            ${stems}
            <i class="tl-band" id="tlBand" style="bottom:var(--tl-pad)" hidden></i>
            ${nowMark}
            <i class="tl-x" id="tlX" style="bottom:var(--tl-pad)" hidden></i>
          </div>
          <div class="tl-axis">${axis.join('')}</div>
        </div>
        <div class="legend">
          <span><i class="sw-o2"></i>Oxygen</span>
          <span><i class="sw-dip">${'\u2193'}</i>Dip under ${O2.warn}%</span>
          <span><i class="sw-dip bad">${'\u26A0\uFE0E'}</i>Dip under ${O2.critical}%</span>
          <span><i class="sw-deep"></i>Deep sleep</span>
          <span><i class="sw-light"></i>Light sleep</span>
          <span><i class="sw-awake"></i>Awake</span>
          <span><i class="sw-nodata"></i>No signal</span>
        </div>
        ${renderEvents(dips)}
        <p class="sr-only" id="tlLive" aria-live="polite"></p>
      </section>`;
    }

    function defaultReadout() {
      const dip = currentDips.find(d => d.start.getTime() === selectedDip);
      if (dip) {
        return `<b>Dip to ${Math.round(dip.min)}%</b> at ${fmtClock(dip.start)} — ${dipDepthText(dip)}. Outlined on the timeline and in the list below.`;
      }
      return currentDips.length
        ? 'Move across the timeline to read any moment. Select a dip marker or an event below to find it in both places.'
        : 'Move across the timeline to read any moment.';
    }

    function layoutMarks() {
      const lane = document.querySelector('#tlPlot .tl-marks');
      if (!lane) return;
      const stems = document.querySelectorAll('#tlPlot .tl-stem');
      const rights = [];   // right edge of the last marker placed in each row
      lane.querySelectorAll('.tl-dip').forEach((pill, i) => {
        const left = pill.offsetLeft - pill.offsetWidth / 2;
        let row = rights.findIndex(right => right + 4 <= left);
        if (row < 0) row = rights.length < 4 ? rights.length : rights.indexOf(Math.min(...rights));
        rights[row] = left + pill.offsetWidth;
        pill.style.top = (row * 30) + 'px';
        if (stems[i]) stems[i].style.top = (row * 30 + 24) + 'px';
      });
      lane.style.height = (rights.length * 30) + 'px';
      const nowMark = document.querySelector('#tlPlot .tl-now');
      if (nowMark) nowMark.style.top = lane.style.height;
    }

    function wireTimeline() {
      const plot = el('tlPlot');
      if (!plot) return;
      layoutMarks();
      el('tlReadout').innerHTML = defaultReadout();
      const read = event => {
        const rect = plot.getBoundingClientRect();
        if (!rect.width) return;
        const frac = Math.max(0, Math.min(0.9999, (event.clientX - rect.left) / rect.width));
        const t = tl.t0 + Math.floor(frac * tl.spanMs / tl.step) * tl.step;
        const row = tl.byTime.get(t);
        const parts = [`<b>${fmtClock(new Date(t))}</b>`];
        if (!row) parts.push('no readings');
        else {
          if (row.min_oxygen_saturation != null) {
            parts.push(`oxygen low ${Math.round(row.min_oxygen_saturation)}%`
              + (row.avg_oxygen_saturation != null ? `, average ${row.avg_oxygen_saturation.toFixed(1)}%` : ''));
          } else if (row.avg_oxygen_saturation != null) {
            parts.push(`oxygen average ${row.avg_oxygen_saturation.toFixed(1)}%`);
          }
          if (row.avg_heart_rate != null) parts.push(`heart rate ${Math.round(row.avg_heart_rate)} bpm`);
          parts.push(STAGE_WORDS[classify(row)]);
        }
        el('tlX').hidden = false;
        el('tlX').style.left = (frac * 100).toFixed(2) + '%';
        el('tlReadout').innerHTML = parts.join(' · ');
      };
      plot.addEventListener('pointermove', read);
      plot.addEventListener('pointerdown', read);
      plot.addEventListener('pointerleave', event => {
        if (event.pointerType !== 'mouse') return;   // a touch reading stays put
        el('tlX').hidden = true;
        el('tlReadout').innerHTML = defaultReadout();
      });
    }

    function applySelection(scrollTo) {
      const idx = currentDips.findIndex(d => d.start.getTime() === selectedDip);
      document.querySelectorAll('#content [data-dip]').forEach(node => {
        const on = Number(node.dataset.dip) === idx;
        node.setAttribute('aria-pressed', String(on));
        const holder = node.closest('.event');
        if (holder) holder.classList.toggle('selected', on);
      });
      const band = el('tlBand');
      if (!band || !tl) return;
      band.hidden = idx < 0;
      if (idx >= 0) {
        const dip = currentDips[idx];
        band.style.left = ((dip.start.getTime() - tl.t0) / tl.spanMs * 100).toFixed(2) + '%';
        band.style.width = ((dip.end - dip.start) / tl.spanMs * 100).toFixed(2) + '%';
      }
      el('tlX').hidden = true;
      el('tlReadout').innerHTML = defaultReadout();
      if (!scrollTo) return;
      el('tlLive').textContent = idx >= 0
        ? `Dip to ${Math.round(currentDips[idx].min)}% at ${fmtClock(currentDips[idx].start)} selected on the timeline and in the event list.`
        : 'Selection cleared.';
      if (idx < 0) return;
      const target = scrollTo === 'timeline' ? el('tlPlot')
        : document.querySelector(`#content .event[data-row="${idx}"]`);
      const calm = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      if (target) target.scrollIntoView({ block: 'nearest', behavior: calm ? 'auto' : 'smooth' });
    }

    function onDipClick(event) {
      const button = event.target.closest('button[data-dip]');
      const dip = button && currentDips[Number(button.dataset.dip)];
      if (!dip) return;
      const key = dip.start.getTime();
      selectedDip = selectedDip === key ? null : key;
      // picked on the timeline -> bring its list row into view, and vice versa
      applySelection(button.closest('.tl-marks') ? 'list' : 'timeline');
    }

    function renderStats(stats, dips) {
      const lowTier = stats.minO2 == null ? '' : stats.minO2 < O2.critical ? 'alert' : stats.minO2 < O2.warn ? 'warnish' : 'o2';
      const lowFlag = lowTier === 'alert' ? `<span class="flag">${'\u26A0\uFE0E'} under ${O2.critical}%</span> · `
        : lowTier === 'warnish' ? `<span class="flag">${'\u2193'} under ${O2.warn}%</span> · ` : '';
      const minO2Text = stats.minO2 != null
        ? `<b>${Math.round(stats.minO2)}%</b><span>lowest oxygen</span><span class="sub">${lowFlag}${stats.minO2At ? 'at ' + fmtClock(stats.minO2At) : ''}</span>` : `<b>—</b><span>lowest oxygen</span>`;
      const worstBad = dips.some(dip => dipTier(dip) === 'bad');
      const dipSub = !dips.length ? '<span class="sub">✓ none to review</span>'
        : `<span class="sub"><span class="flag">${worstBad ? '\u26A0\uFE0E' : '\u2193'}</span> ${worstBad ? `lowest went under ${O2.critical}%` : 'marked on the timeline'}</span>`;
      return `<div class="stats">
        <div class="stat card o2"><b>${stats.avgO2 != null ? stats.avgO2.toFixed(1) + '%' : '—'}</b><span>average oxygen</span></div>
        <div class="stat card ${lowTier}">${minO2Text}</div>
        <div class="stat card ${!dips.length ? 'o2' : worstBad ? 'alert' : 'warnish'}"><b>${dips.length}</b><span>oxygen dips below ${O2.warn}%</span>${dipSub}</div>
        <div class="stat card"><b>${fmtDur(stats.sleep)}</b><span>total sleep</span>
          <span class="sub">${fmtDur(stats.deep)} deep · ${fmtDur(stats.awake)} awake</span></div>
        <div class="stat card hr"><b>${stats.avgHr != null ? Math.round(stats.avgHr) : '—'}</b><span>average heart rate (bpm)</span></div>
      </div>`;
    }

    function renderEvents(dips) {
      if (!dips.length) {
        return `<div class="ev-head"><h3>Oxygen events</h3></div><ul class="events">
          <li class="event fine"><span class="tick" aria-hidden="true">✓</span>No dips below ${O2.warn}% — nothing to review.</li>
        </ul>`;
      }
      const items = dips.map((dip, i) => {
        // center on the run's lowest bucket — the focus modal marks this moment
        const focus = new Date(dip.minAt.getTime() + BUCKET_SEC * 500).toISOString();
        const low = Math.round(dip.min);
        const label = encodeURIComponent(`Dip to ${low}%`);
        return `<li class="event ${dipTier(dip)}" data-row="${i}">
          <button type="button" class="ev-pick" data-dip="${i}" aria-pressed="false"
            aria-label="${fmtClock(dip.start)}: dip to ${low}%, under ${dipUnder(dip)}%. ${dipDepthText(dip)}. Show it on the timeline.">
            <time>${fmtClock(dip.start)}</time>
            <span class="ev-text">${dipDepthText(dip)}</span>
            <span class="ev-on">● on the timeline</span>
            <span class="depth"><span aria-hidden="true">${dipGlyph(dip)}</span> ${low}%</span>
          </button>
          <a class="ev-trace" href="/data?focus=${encodeURIComponent(focus)}&span=45&label=${label}"
            title="Open the raw trace around this dip"
            aria-label="Open the raw trace around the ${fmtClock(dip.start)} dip">Trace →</a></li>`;
      }).join('');
      return `<div class="ev-head"><h3>Oxygen events</h3>
          <span>Select one to find it on the timeline</span></div>
        <ul class="events">${items}</ul>`;
    }

    function renderWeek() {
      const bars = [];
      let maxSleep = 1;
      const nights = [];
      for (let offset = 13; offset >= 0; offset--) {
        const win = nightWindow(offset);
        const stats = nightStats(bucketsIn(win));
        nights.push({ offset, win, stats });
        if (offset <= 6) maxSleep = Math.max(maxSleep, stats.sleep);
      }
      nights.filter(n => n.offset <= 6).forEach(({ offset, win, stats }) => {
        const height = Math.round((stats.sleep / maxSleep) * 100);
        const deepFrac = stats.sleep ? stats.deep / stats.sleep : 0;
        const label = offset === 0 ? 'tonight'
          : win.start.toLocaleDateString([], { weekday: 'short' });
        const detail = stats.sleep
          ? `${fmtDur(stats.deep)} deep · ${fmtDur(Math.max(0, stats.sleep - stats.deep))} light · ${fmtDur(stats.awake)} awake`
          : 'no readings';
        bars.push(`<div class="wb ${offset === 0 ? 'tonight' : ''}${offset === nightOffset ? ' shown' : ''}" title="${detail}">
          <b>${stats.sleep ? fmtDur(stats.sleep) : ''}</b>
          <i style="height:${Math.max(3, height)}%">
            <u class="lt"></u><u class="dp" style="height:${Math.round(deepFrac * 100)}%"></u>
          </i><span>${label}</span></div>`);
      });

      // week-over-week trend lines; only speak when both weeks have real nights
      const covered = n => n.stats.coveredSec >= 1800;
      const thisWeek = nights.filter(n => n.offset <= 6 && covered(n));
      const lastWeek = nights.filter(n => n.offset > 6 && covered(n));
      const mean = (list, pick) => list.length ? list.reduce((a, n) => a + pick(n.stats), 0) / list.length : null;
      const fragment = (label, pick) => {
        const now = mean(thisWeek, pick);
        if (now == null) return '';
        const before = lastWeek.length >= 3 ? mean(lastWeek, pick) : null;
        let delta = '';
        if (before != null && Math.abs(now - before) >= 300) {
          delta = ` (${now > before ? 'up' : 'down'} ${fmtDur(Math.abs(now - before))} on last week)`;
        }
        return `<b>${fmtDur(now)}</b> ${label}${delta}`;
      };
      const provisional = thisWeek.length < 4
        ? `Only ${thisWeek.length} night${thisWeek.length === 1 ? '' : 's'} recorded so far, so these will move around. `
        : '';
      const parts = [
        fragment('of sleep', s => s.sleep),
        fragment('of it deep', s => s.deep),
        fragment('awake overnight', s => s.awake),
      ].filter(Boolean);
      const note = parts.length ? `Averaging ${parts.join(', ')} per night.` : '';
      return `<section><div class="week card"><h2 class="card-title">The last seven nights</h2>
        <div class="week-bars">${bars.join('')}</div>
        <div class="legend">
          <span><i class="sw-deep"></i>Deep</span>
          <span><i class="sw-light"></i>Light</span>
          <span>Underlined: the night shown above</span>
        </div>
        ${note ? `<p class="week-note">${provisional}${note}</p>` : ''}
        </div></section>`;
    }

    function headline(stats, win, enough) {
      if (!enough) return win.inProgress ? 'Nothing recorded yet tonight' : 'No night recorded';
      if (stats.sleep < 60) return win.inProgress ? 'No sleep recorded so far' : 'No sleep recorded';
      const slept = `Slept <b>${fmtDur(stats.sleep)}</b>`;
      if (win.inProgress) return `${slept} so far`;
      return nightOffset === 0 ? `${slept} last night` : slept;
    }

    function render() {
      const win = nightWindow(nightOffset);
      const buckets = bucketsIn(win);
      const stats = nightStats(buckets);
      const dips = dipEvents(buckets);
      const enough = !!stats.coveredSec && stats.coveredSec >= 1800;
      const label = win.start.toLocaleDateString([], { weekday: 'long', month: 'long', day: 'numeric' });
      if (shownOffset !== nightOffset) { selectedDip = null; shownOffset = nightOffset; }
      el('nightKicker').textContent = nightOffset === 0 ? (win.inProgress ? 'Tonight' : 'Last night') : 'The night of';
      el('nightSub').textContent = `${label}${win.inProgress ? ' · still collecting' : ''}`;
      el('nightTitle').innerHTML = headline(stats, win, enough);
      el('lede').innerHTML = narrative(stats, dips, win.inProgress);
      el('nextNight').disabled = nightOffset === 0;
      el('prevNight').disabled = nightOffset >= 6;
      // a background refresh must not drop keyboard focus from a dip control
      const held = document.activeElement && document.activeElement.closest
        && document.activeElement.closest('#content button[data-dip]');
      const heldSelector = held
        ? `#content ${held.classList.contains('tl-dip') ? '.tl-dip' : '.ev-pick'}[data-dip="${held.dataset.dip}"]` : null;
      if (!enough) {
        currentDips = []; tl = null;
        el('content').innerHTML = `<div class="empty card">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round" aria-hidden="true"><path d="M20 14.6A8.6 8.6 0 1 1 9.4 4a6.9 6.9 0 0 0 10.6 10.6z"/></svg>
          <b>No night to report here yet.</b>
          Readings appear once the sock has been worn overnight.</div>` + renderWeek();
        return;
      }
      currentDips = dips;
      el('content').innerHTML = renderTimeline(win, buckets, dips) + renderStats(stats, dips) + renderWeek();
      wireTimeline();
      applySelection(null);
      const again = heldSelector && document.querySelector(heldSelector);
      if (again) again.focus({ preventScroll: true });
    }

    async function boot() {
      try {
        const [rollupData, accounts] = await Promise.all([
          fetch('/api/rollups?bucket=5m&hours=336&limit=100000').then(r => r.json()),
          fetch('/api/accounts').then(r => r.json())
        ]);
        rollups = rollupData.rollups || [];
        const account = (accounts.accounts || [])[0];
        const prefs = (account && account.dashboard_preferences) || {};
        if (prefs.baby_name) deviceName = prefs.baby_name;
        O2 = window.owletO2(prefs);
        NIGHT = nightWindowFrom(prefs);
      } catch (error) {
        el('nightSub').textContent = 'could not load';
        el('lede').textContent = 'Could not load readings — is the collector running?';
        return;
      }
      render();
      el('content').addEventListener('click', onDipClick);
      window.addEventListener('resize', layoutMarks);
      el('prevNight').addEventListener('click', () => { nightOffset = Math.min(6, nightOffset + 1); render(); });
      el('nextNight').addEventListener('click', () => { nightOffset = Math.max(0, nightOffset - 1); render(); });
      setInterval(async () => {   // keep "still collecting" nights fresh
        if (nightOffset !== 0 || document.hidden) return;
        const data = await fetch('/api/rollups?bucket=5m&hours=336&limit=100000').then(r => r.json()).catch(() => null);
        if (data) { rollups = data.rollups || rollups; render(); }
      }, 300000);
    }
    boot();
  