
    (function themeCharts() {
      // Quiet, token-driven chart chrome so the graphs read like the rest of
      // the app: hairline series, faint horizontal grid only, inverted-card
      // tooltips (same treatment as the sleep-strip tooltip).
      // Colours come only from theme.css; with a token missing Chart.js keeps
      // its own defaults instead of a second, stale palette living here.
      const styles = getComputedStyle(document.documentElement);
      const token = name => styles.getPropertyValue(name).trim();
      const ink = token('--ink'), bg = token('--bg'), dim = token('--dim'), line = token('--surface-line');
      if (window.Chart && ink && bg && dim && line) {
        Chart.defaults.color = dim;
        Chart.defaults.borderColor = line;
        Chart.defaults.font.family = getComputedStyle(document.documentElement).getPropertyValue('--font-sans').trim() || Chart.defaults.font.family;
        Chart.defaults.font.size = 11;
        Chart.defaults.elements.line.borderWidth = 1.7;
        Chart.defaults.elements.line.borderJoinStyle = 'round';
        Chart.defaults.elements.line.borderCapStyle = 'round';
        Chart.defaults.scale.grid.drawTicks = false;
        Chart.defaults.scale.ticks.color = dim;   // small text: --dim, never --faint
        const tooltip = Chart.defaults.plugins.tooltip;
        tooltip.backgroundColor = ink;
        tooltip.titleColor = bg;
        tooltip.bodyColor = bg;
        tooltip.borderColor = line;
        tooltip.borderWidth = 1;
        tooltip.cornerRadius = 10;
        tooltip.padding = 10;
        tooltip.boxPadding = 5;
        tooltip.usePointStyle = true;
        tooltip.titleMarginBottom = 7;
        tooltip.titleFont = { size: 11, weight: '700' };
      }
      window.__onThemeChange = () => window.location.reload();
    })();
    window.__pageOwnsAccountSelect = true;
    // Every chart colour is a theme.css token. No palette literals live here:
    // if a token is ever missing, lines fall back to the page's own text colour
    // (always readable on the page) and band fills pass 'transparent'.
    const TOKEN_FALLBACK = getComputedStyle(document.body).color;
    const CHART_FONT = getComputedStyle(document.documentElement).getPropertyValue('--font-sans').trim() || 'sans-serif';
    const themeToken = (name, fallback = TOKEN_FALLBACK) => getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
    // One colour per vital, the same tokens as every other page and the widget.
    const SERIES = {
      o2: themeToken('--series-o2'),
      hr: themeToken('--series-hr'),
      move: themeToken('--series-move'),
      temp: themeToken('--series-temp'),
      sleep: themeToken('--sleep-light'),   // a line colour: --sleep-deep is under 3:1 on a dark card
      awake: themeToken('--awake'),
      prior: themeToken('--dim'),
      good: themeToken('--good'),
      bad: themeToken('--bad'),
      warn: themeToken('--warn'),
    };
    const seriesAlpha = (color, alpha) => /^#[0-9a-fA-F]{6}$/.test(color) ? color + alpha : color;
    // API_BASE and SHARE_MODE are declared by the inline config <script> that
    // render_dashboard() fills in per request (share links get their own base).
    // Dashboard refresh follows the account's poll interval (capped to a sane floor).
    function refreshSeconds() {
      const interval = Number(currentAccount()?.poll_interval_seconds || 5);
      return Math.max(5, Math.min(300, Number.isFinite(interval) && interval > 0 ? interval : 5));
    }
    const TABLE_ROW_LIMIT = 500;
    const HISTORY_PAN_BUFFER_HOURS = 2;
    const OLDER_HISTORY_CHUNK_HOURS = 24;
    let readings = [];
    let filtered = [];
    let summary = null;
    let insights = null;
    let devices = [];
    let accounts = [];
    let rollups = [];
    let comparisonRows = [];
    let notifications = { items: [], total: 0, limit: 500, offset: 0 };
    let challenges = { items: [], total: 0, limit: 100, offset: 0 };
    const TREND_MAX_SAMPLE_GAP_MS = 5 * 60 * 1000;
    // Consecutive readings farther apart than this mean the collector was not running.
    const COLLECTOR_GAP_MS = 15 * 60 * 1000;
    const TREND_MAX_SOURCE_POINTS_DESKTOP = 800;
    const TREND_MAX_SOURCE_POINTS_MOBILE = 420;
    let vitalsChart = null;
    let hrPanel = null;
    let spo2Panel = null;
    let movePanel = null;
    let oxygenTrendChart = null;
    let challengeDetailChart = null;
    let dailyInsightsChart = null;
    let secondsUntilRefresh = 5;
    let syncInProgress = false;
    let zoomWindow = null;
    let loadedHours = null;
    let loadingOlderHistory = false;
    let lastLatestTimestamp = null;
    let deferredInstallPrompt = null;
    let currentChallengeDetail = null;
    let hoveredStateInterval = null;
    let sleepHighlightEnabled = false;
    let sleepBallparkEnabled = false;
    let challengeBandsEnabled = true;
    let stateStripSegments = [];
    let trendRenderToken = 0;
    let refreshToken = 0;
    let firstLoadComplete = false;
    let refreshInFlight = null;
    let readingsTableSignature = '';
    let applyingAccountPreferences = false;

    const sleepPhaseColors = {
      light: themeToken('--chart-phase-light', 'transparent'),
      deep: themeToken('--chart-phase-deep', 'transparent'),
      awake: themeToken('--chart-phase-awake', 'transparent'),
      inactive: themeToken('--chart-phase-inactive', 'transparent'),
      offline: themeToken('--chart-phase-offline', 'transparent')
    };

    const stateStripColors = {
      light: themeToken('--chart-strip-light'),
      deep: themeToken('--chart-strip-deep'),
      awake: themeToken('--chart-strip-awake'),
      inactive: themeToken('--chart-strip-inactive'),
      offline: themeToken('--chart-strip-offline')
    };

    const PANEL_CANVAS_IDS = ['vitalsChart', 'hrPanelChart', 'spo2PanelChart', 'movePanelChart'];
    const offlineBandsPlugin = {
      id: 'offlineBands',
      beforeDatasetsDraw(chart, _args, options) {
        const intervals = options?.intervals || [];
        if (!intervals.length || !chart.scales?.x) return;
        const { ctx, chartArea, scales } = chart;
        ctx.save();
        ctx.fillStyle = themeToken('--chart-offline-fill', 'transparent');
        ctx.strokeStyle = themeToken('--chart-offline-line');
        intervals.forEach(({ start, end }) => {
          const left = Math.max(chartArea.left, scales.x.getPixelForValue(start));
          const right = Math.min(chartArea.right, scales.x.getPixelForValue(end));
          if (!Number.isFinite(left) || !Number.isFinite(right) || right <= chartArea.left || left >= chartArea.right) return;
          const width = Math.max(2, right - left);
          ctx.fillRect(left, chartArea.top, width, chartArea.bottom - chartArea.top);
          ctx.strokeRect(left, chartArea.top, width, chartArea.bottom - chartArea.top);
        });
        ctx.restore();
      }
    };
    const noDataBandsPlugin = {
      id: 'noDataBands',
      beforeDatasetsDraw(chart, _args, options) {
        const intervals = options?.intervals || [];
        if (!intervals.length || !chart.scales?.x) return;
        const { ctx, chartArea, scales } = chart;
        const height = chartArea.bottom - chartArea.top;
        intervals.forEach(({ start, end }) => {
          const left = Math.max(chartArea.left, scales.x.getPixelForValue(start));
          const right = Math.min(chartArea.right, scales.x.getPixelForValue(end));
          if (!Number.isFinite(left) || !Number.isFinite(right) || right <= chartArea.left || left >= chartArea.right) return;
          const width = Math.max(2, right - left);
          ctx.save();
          ctx.beginPath();
          ctx.rect(left, chartArea.top, width, height);
          ctx.clip();
          ctx.fillStyle = themeToken('--chart-nodata-fill', 'transparent');
          ctx.fillRect(left, chartArea.top, width, height);
          ctx.strokeStyle = themeToken('--chart-nodata-line');
          ctx.lineWidth = 1;
          for (let x = left - height; x < right; x += 14) {
            ctx.beginPath();
            ctx.moveTo(x, chartArea.bottom);
            ctx.lineTo(x + height, chartArea.top);
            ctx.stroke();
          }
          if (width > 78) {
            ctx.fillStyle = themeToken('--chart-nodata-text');
            ctx.font = `600 11px ${CHART_FONT}`;
            ctx.textAlign = 'center';
            ctx.textBaseline = 'top';
            ctx.fillText('collector off', (left + right) / 2, chartArea.top + 4);
          }
          ctx.restore();
        });
      }
    };
    const challengeBandsPlugin = {
      id: 'challengeBands',
      beforeDraw(chart, _args, options) {
        const intervals = options?.intervals || [];
        if (!intervals.length || !chart.scales?.x) return;
        const { ctx, chartArea, scales } = chart;
        const isTrendCompanion = chart.canvas.id === 'oxygenTrendChart';
        ctx.save();
        ctx.fillStyle = isTrendCompanion
          ? themeToken('--chart-challenge-fill-trend', 'transparent')
          : themeToken('--chart-challenge-fill', 'transparent');
        ctx.strokeStyle = isTrendCompanion
          ? themeToken('--chart-challenge-line-trend')
          : themeToken('--chart-challenge-line');
        ctx.lineWidth = isTrendCompanion ? 3 : 2;
        ctx.setLineDash(isTrendCompanion ? [7, 5] : [8, 5]);
        intervals.forEach(({ start, end }) => {
          const left = Math.max(chartArea.left, scales.x.getPixelForValue(start));
          const right = Math.min(chartArea.right, scales.x.getPixelForValue(end));
          if (!Number.isFinite(left) || !Number.isFinite(right) || right <= chartArea.left || left >= chartArea.right) return;
          const width = Math.max(2, right - left);
          ctx.fillRect(left, chartArea.top, width, chartArea.bottom - chartArea.top);
          ctx.strokeRect(left, chartArea.top + 1, width, Math.max(1, chartArea.bottom - chartArea.top - 2));
        });
        ctx.restore();
      }
    };
    const notificationGlyphsPlugin = {
      id: 'notificationGlyphs',
      afterDatasetsDraw(chart) {
        const index = chart.data.datasets.findIndex(dataset => dataset.id === 'notifications');
        if (index < 0) return;
        const meta = chart.getDatasetMeta(index);
        const dataset = chart.data.datasets[index];
        const { ctx } = chart;
        ctx.save();
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.font = `700 12px ${CHART_FONT}`;
        meta.data.forEach((point, i) => {
          const raw = dataset.data[i];
          if (!raw) return;
          ctx.fillStyle = raw.severity === 'critical' ? SERIES.bad : SERIES.warn;
          ctx.fillText('!', point.x, point.y + 1);
        });
        ctx.restore();
      }
    };
    const notificationHoverPlugin = {
      id: 'notificationHoverPriority',
      afterEvent(chart, args) {
        if (!['vitalsChart', 'spo2PanelChart'].includes(chart.canvas.id)) return;
        const event = args.event;
        const datasetIndex = chart.data.datasets.findIndex(dataset => dataset.id === 'notifications');
        if (datasetIndex < 0) return;
        const meta = chart.getDatasetMeta(datasetIndex);
        const isExit = event.type === 'mouseout' || event.type === 'touchend';
        if (isExit) {
          if (chart.$notificationHoverActive) {
            chart.tooltip.setActiveElements([], { x: 0, y: 0 });
            chart.$notificationHoverActive = false;
            chart.canvas.style.cursor = '';
            args.changed = true;
          }
          return;
        }
        if (!['mousemove', 'click', 'touchmove'].includes(event.type)) return;

        const mobile = window.matchMedia('(max-width: 640px)').matches;
        const xRadius = mobile ? 28 : 22;
        const yRadius = mobile ? 44 : 34;
        let best = null;
        meta.data.forEach((point, index) => {
          if (!point || point.skip) return;
          const dx = Math.abs(point.x - event.x);
          const dy = Math.abs(point.y - event.y);
          if (dx > xRadius || dy > yRadius) return;
          const score = dx + dy * 0.35;
          if (!best || score < best.score) best = { index, point, score };
        });

        if (best) {
          chart.tooltip.setActiveElements([{ datasetIndex, index: best.index }], { x: best.point.x, y: best.point.y });
          chart.setActiveElements([{ datasetIndex, index: best.index }]);
          chart.$notificationHoverActive = true;
          chart.canvas.style.cursor = 'help';
          args.changed = true;
        } else if (chart.$notificationHoverActive) {
          chart.tooltip.setActiveElements([], { x: event.x, y: event.y });
          chart.setActiveElements([]);
          chart.$notificationHoverActive = false;
          chart.canvas.style.cursor = '';
          args.changed = true;
        }
      }
    };
    const oxygenCriticalThresholdPlugin = {
      id: 'oxygenCriticalThreshold',
      afterDraw(chart) {
        if (!['vitalsChart', 'spo2PanelChart'].includes(chart.canvas.id)) return;
        const scale = chart.scales?.spo2;
        const { chartArea, ctx } = chart;
        if (!scale || !chartArea) return;
        const critical = window.owletO2(dashboardPreferences()).critical;
        const y = scale.getPixelForValue(critical);
        if (!Number.isFinite(y) || y < chartArea.top || y > chartArea.bottom) return;
        ctx.save();
        ctx.setLineDash([6, 5]);
        ctx.lineWidth = 1.5;
        ctx.strokeStyle = SERIES.bad;
        ctx.beginPath();
        ctx.moveTo(chartArea.left, y);
        ctx.lineTo(chartArea.right, y);
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.fillStyle = SERIES.bad;
        ctx.font = `700 12px ${CHART_FONT}`;
        ctx.textAlign = 'right';
        ctx.textBaseline = 'bottom';
        ctx.fillText(`${critical}% O₂`, chartArea.right - 4, y - 4);
        ctx.restore();
      }
    };
    const sleepBandsPlugin = {
      id: 'sleepBands',
      beforeDatasetsDraw(chart) {
        if (!PANEL_CANVAS_IDS.includes(chart.canvas.id) || !sleepHighlightEnabled || !chart.scales?.x) return;
        const intervals = sleepOverlayIntervals();
        if (!intervals.length) return;
        const { ctx, chartArea, scales } = chart;
        ctx.save();
        intervals.forEach(interval => {
          const left = Math.max(chartArea.left, scales.x.getPixelForValue(interval.start));
          const right = Math.min(chartArea.right, scales.x.getPixelForValue(interval.end));
          if (!Number.isFinite(left) || !Number.isFinite(right) || right <= chartArea.left || left >= chartArea.right) return;
          ctx.fillStyle = sleepPhaseColors[interval.cls] || sleepPhaseColors.inactive;
          ctx.fillRect(left, chartArea.top, Math.max(1, right - left), chartArea.bottom - chartArea.top);
        });
        ctx.restore();
      }
    };
    const stateChartHoverPlugin = {
      id: 'stateChartHover',
      afterEvent(chart, args) {
        if (chart.canvas.id !== 'stateChart' || !chart.scales?.x) return;
        const event = args.event;
        if (event.type === 'mouseout') {
          hoveredStateInterval = null;
          refreshPrimaryCharts();
          return;
        }
        if (!['mousemove', 'click', 'touchmove'].includes(event.type)) return;
        const timestamp = chart.scales.x.getValueForPixel(event.x);
        const interval = rollupIntervalAt(timestamp) || stateIntervalAt(timestamp);
        hoveredStateInterval = interval;
        refreshPrimaryCharts();
      }
    };
    const sleepPhaseHoverPlugin = {
      id: 'sleepPhaseHover',
      beforeDatasetsDraw(chart) {
        if (!PANEL_CANVAS_IDS.includes(chart.canvas.id) || !hoveredStateInterval || !chart.scales?.x) return;
        const { ctx, chartArea, scales } = chart;
        const left = Math.max(chartArea.left, scales.x.getPixelForValue(hoveredStateInterval.start));
        const right = Math.min(chartArea.right, scales.x.getPixelForValue(hoveredStateInterval.end));
        if (!Number.isFinite(left) || !Number.isFinite(right) || right <= left) return;
        ctx.save();
        ctx.fillStyle = sleepPhaseColors[hoveredStateInterval.cls] || sleepPhaseColors.inactive;
        ctx.strokeStyle = ctx.fillStyle.replace('.13', '.34').replace('.14', '.34').replace('.12', '.30');
        ctx.fillRect(left, chartArea.top, right - left, chartArea.bottom - chartArea.top);
        ctx.strokeRect(left, chartArea.top, right - left, chartArea.bottom - chartArea.top);
        ctx.restore();
      }
    };
    const dayBoundaryPlugin = {
      id: 'dayBoundaries',
      afterDraw(chart, _args, options) {
        if (options?.display === false || !chart.scales?.x) return;
        const { ctx, chartArea, scales } = chart;
        const min = Number(scales.x.min);
        const max = Number(scales.x.max);
        if (!Number.isFinite(min) || !Number.isFinite(max) || max <= min) return;
        ctx.save();
        ctx.strokeStyle = themeToken('--chart-day-boundary');
        ctx.lineWidth = 1;
        ctx.setLineDash([2, 5]);
        dayBoundaryTimes({ min, max }).forEach(time => {
          const x = scales.x.getPixelForValue(time);
          if (Number.isFinite(x) && x > chartArea.left && x < chartArea.right) {
            ctx.beginPath();
            ctx.moveTo(x, chartArea.top);
            ctx.lineTo(x, chartArea.bottom);
            ctx.stroke();
          }
        });
        ctx.restore();
      }
    };
    Chart.register(sleepBandsPlugin, challengeBandsPlugin, offlineBandsPlugin, noDataBandsPlugin, dayBoundaryPlugin, sleepPhaseHoverPlugin, stateChartHoverPlugin, notificationGlyphsPlugin, notificationHoverPlugin, oxygenCriticalThresholdPlugin);

    const el = (id) => document.getElementById(id);
    const fmt = (value, suffix = '') => value === null || value === undefined ? '—' : `${value}${suffix}`;
    const compactNumber = (value, digits = 1) => {
      const number = Number(value);
      if (!Number.isFinite(number)) return '—';
      return number.toFixed(digits).replace(/\.0$/, '');
    };
    const chartValue = (value, suffix = '') => value === null || value === undefined || !Number.isFinite(Number(value)) ? '—' : `${compactNumber(value, 1)}${suffix}`;
    const axisTick = (value) => compactNumber(value, 1);
    const num = (value, digits = 1) => value === null || value === undefined ? '—' : Number(value).toFixed(digits).replace(/\.0$/, '');
    const money = (value) => value === null || value === undefined ? '—' : Number(value).toLocaleString([], { style: 'currency', currency: 'USD', maximumFractionDigits: Number(value) >= 100 ? 0 : 2 });
    const pct = (value) => value === null || value === undefined ? '—' : `${Number(value) >= 0 ? '+' : ''}${Number(value).toFixed(1)}%`;
    const changeClass = (value, threshold = 0) => value === null || value === undefined || Math.abs(Number(value)) <= threshold ? 'flat' : (Number(value) > 0 ? 'up' : 'down');
    const hours = (seconds) => seconds ? `${(seconds / 3600).toFixed(1).replace(/\.0$/, '')}h` : '0h';
    const trendClass = (trend) => `trend-${trend || 'unknown'}`;
    const stateLabel = (value) => ({ '0': 'inactive', '1': 'awake', '8': 'light sleep', '15': 'deep sleep' }[String(value)] || `state ${value ?? 'unknown'}`);
    function sleepStageInfo(stage) {
      const key = String(stage || '').toLowerCase().replace(/_/g, ' ');
      if (key.includes('deep') || key === '15') return { name: 'Deep sleep', description: 'Quiet/deeper sleep estimate from Owlet state data.' };
      if (key.includes('light') || key === '8') return { name: 'Light sleep', description: 'Lighter sleep estimate from Owlet state data.' };
      if (key.includes('awake') || key === '1') return { name: 'Awake', description: 'Awake or active period estimate.' };
      if (key.includes('offline') || key.includes('sock')) return { name: 'Offline / sock off', description: 'No reliable physiological signal during this period.' };
      return { name: 'Inactive / unknown', description: 'Owlet did not report a clear sleep stage for this period.' };
    }
    const zeroOrNegative = (value) => value !== null && value !== undefined && Number(value) <= 0;
    const isOffline = (row) => !!(row?.sock_disconnected || row?.sock_off || zeroOrNegative(row?.heart_rate) || zeroOrNegative(row?.oxygen_saturation));
    const durationText = (seconds) => seconds ? `${Math.floor(seconds / 3600)}h ${Math.round((seconds % 3600) / 60)}m`.replace(/^0h /, '') : '0m';
    const signed = (value, suffix = '') => value === null || value === undefined ? '—' : `${Number(value) >= 0 ? '+' : ''}${Number(value).toFixed(1).replace(/\.0$/, '')}${suffix}`;
    const chartList = () => [vitalsChart, hrPanel, spo2Panel, movePanel, oxygenTrendChart].filter(Boolean);
    const primaryCharts = () => [vitalsChart, hrPanel, spo2Panel, movePanel].filter(Boolean);
    const refreshPrimaryCharts = () => primaryCharts().forEach(chart => chart.update('none'));
    const alignmentChart = () => vitalsChart || hrPanel || spo2Panel || movePanel;

    function smoothingMinutes() {
      const value = el('smoothing').value;
      return value === 'raw' ? 0 : Number(value || 0);
    }

    function smoothingLabel() {
      const option = el('smoothing')?.selectedOptions?.[0];
      return option ? option.textContent.replace(' avg', '') : 'Raw';
    }

    function rollupBucket() {
      return '30m';
    }

    function oxygenValueClass(level) {
      const value = Number(level);
      if (!Number.isFinite(value)) return 'unknown';
      const o2 = window.owletO2(dashboardPreferences());
      if (value <= 0) return 'unknown';        // sock off the foot, not a desat
      if (value >= o2.warn) return 'good';
      if (value >= o2.critical) return 'caution';
      return 'danger';
    }

    function updateRefreshButton(text = null) {
      const button = el('refresh');
      if (!button) return;
      const mobile = isMobileViewport();
      if (text) {
        button.textContent = mobile ? (text.startsWith('Refreshing') ? '↻' : '!') : text;
        button.title = text;
        button.setAttribute('aria-label', text);
        return;
      }
      button.textContent = mobile ? '↻' : 'Refresh';
      button.title = `Auto-refresh in ${secondsUntilRefresh}s`;
      button.setAttribute('aria-label', button.title);
    }

    let lastDataAt = Date.now();

    function updateTitleDotProgress() {
      const dot = el('titleStatusDot');
      if (!dot) return;
      if (dot.classList.contains('offline')) {
        dot.style.backgroundColor = '';
        dot.title = 'Device offline / sock off — click to refresh';
        return;
      }
      // Freshness pulse: bright green when data just landed, fading to grey over
      // ~1.5 intervals, then on toward red when multiple heartbeats are missed.
      const totalMs = refreshSeconds() * 1000;
      const age = Math.max(0, Date.now() - lastDataAt);
      const intervals = age / totalMs;
      const mix = (from, to, fade) => Math.round(from + (to - from) * fade);
      // Colours come from theme.css (--live-*), hex only, read on every paint.
      const liveRgb = name => {
        const m = /^#([0-9a-f]{6})$/i.exec(themeToken(name, ''));
        if (!m) return null;
        const n = parseInt(m[1], 16);
        return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
      };
      // Without the --live-* tokens there is nothing to blend: use the end
      // colour of the phase straight from CSS, so "stale" still turns --bad.
      const paint = (from, to, fade, endVar) => {
        dot.style.backgroundColor = from && to
          ? `rgb(${mix(from[0], to[0], fade)}, ${mix(from[1], to[1], fade)}, ${mix(from[2], to[2], fade)})`
          : (fade < .5 ? '' : `var(${endVar})`);
      };
      if (intervals <= 1.5) {
        const fade = intervals / 1.5;
        paint(liveRgb('--live-fresh'), liveRgb('--live-idle'), fade, '--faint');
        dot.title = `Auto-refresh in ${secondsUntilRefresh}s — click to refresh now`;
      } else {
        const fade = Math.min(1, (intervals - 1.5) / 2.5); // fully red by ~4 missed intervals
        paint(liveRgb('--live-idle'), liveRgb('--live-stale'), fade, '--bad');
        dot.title = `No new data for ${Math.round(age / 1000)}s — click to retry`;
      }
    }

    function pingTitleDot() {
      const dot = el('titleStatusDot');
      if (!dot || dot.classList.contains('offline')) return;
      const ping = document.createElement('span');
      ping.className = 'dot-ping';
      ping.setAttribute('aria-hidden', 'true');
      dot.appendChild(ping);
      setTimeout(() => ping.remove(), 900);
    }

    function setInitialLoading(message, kind = '') {
      if (firstLoadComplete) return;
      const loading = el('initialLoading');
      loading.classList.remove('hidden', 'error');
      if (kind) loading.classList.add(kind);
      el('initialLoadingMessage').textContent = message;
    }

    function hideInitialLoading() {
      firstLoadComplete = true;
      el('initialLoading').classList.add('hidden');
    }

    function challengeIntervals() {
      return (challenges.items || []).map(challenge => ({
        start: Date.parse(challenge.start_time),
        end: Date.parse(challenge.effective_end_time || challenge.end_time || new Date().toISOString())
      })).filter(interval => Number.isFinite(interval.start) && Number.isFinite(interval.end) && interval.end >= interval.start);
    }

    function timeInIntervals(time, intervals) {
      return intervals.some(interval => time >= interval.start && time <= interval.end);
    }

    function dayBoundaryTimes(range) {
      if (!range || !Number.isFinite(range.min) || !Number.isFinite(range.max) || range.max <= range.min) return [];
      const times = [];
      const boundary = new Date(range.min);
      boundary.setHours(24, 0, 0, 0);
      while (boundary.getTime() < range.max) {
        times.push(boundary.getTime());
        boundary.setDate(boundary.getDate() + 1);
      }
      return times;
    }

    function isInChallenge(rowOrTime) {
      const time = typeof rowOrTime === 'number' ? rowOrTime : Date.parse(rowOrTime?.recorded_at);
      return timeInIntervals(time, challengeIntervals());
    }

    function selectedHours() {
      const value = el('window').value;
      return value === 'all' ? null : Number(value);
    }

    function isMobileViewport() {
      return window.matchMedia('(max-width: 640px)').matches;
    }

    function historyHoursForSelection(hours = selectedHours()) {
      if (!hours) return null;
      return Math.min(24 * 365, Math.ceil(hours) + HISTORY_PAN_BUFFER_HOURS);
    }

    function selectedWindowMs() {
      const hours = selectedHours();
      return hours ? hours * 60 * 60 * 1000 : null;
    }

    function localTime(iso, compact = false) {
      if (!iso) return '—';
      const date = new Date(iso);
      if (compact) return date.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
      return date.toLocaleString([], { dateStyle: 'short', timeStyle: 'medium' });
    }

    function toDateTimeLocal(iso) {
      if (!iso) return '';
      const date = new Date(iso);
      if (Number.isNaN(date.getTime())) return '';
      const local = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
      return local.toISOString().slice(0, 16);
    }

    function fromDateTimeLocal(value) {
      return value ? new Date(value).toISOString() : null;
    }

    function average(values) {
      const clean = values.filter(value => value !== null && value !== undefined && Number.isFinite(Number(value))).map(Number);
      if (!clean.length) return null;
      return clean.reduce((sum, value) => sum + value, 0) / clean.length;
    }

    function windowAverage(rows, key, start, end) {
      return average(rows.filter(row => !isOffline(row) && !isInChallenge(row) && Date.parse(row.recorded_at) >= start && Date.parse(row.recorded_at) <= end).map(row => row[key]));
    }

    function localDayStartMs(time = Date.now()) {
      const date = new Date(time);
      date.setHours(0, 0, 0, 0);
      return date.getTime();
    }

    function calendarComparisonRange(rows) {
      if (!rows.length) return null;
      const latest = Date.parse(rows[rows.length - 1].recorded_at);
      if (!Number.isFinite(latest)) return null;
      const dayMs = 24 * 60 * 60 * 1000;
      const currentStart = localDayStartMs(latest);
      const elapsed = Math.max(0, latest - currentStart);
      const priorStart = currentStart - dayMs;
      return { currentStart, currentEnd: latest, priorStart, priorEnd: priorStart + elapsed, elapsed };
    }

    function comparisonFor(key, threshold) {
      const rows = comparisonRows.length ? comparisonRows : readings;
      const range = calendarComparisonRange(rows);
      if (!range) return { current: null, prior: null, delta: null, word: 'unknown', css: 'flat' };
      const current = windowAverage(rows, key, range.currentStart, range.currentEnd);
      const prior = windowAverage(rows, key, range.priorStart, range.priorEnd);
      const delta = current === null || prior === null ? null : current - prior;
      const css = changeClass(delta, threshold);
      const oxygen = key === 'oxygen_saturation';
      const word = delta === null ? 'unknown' : (css === 'flat' ? 'stable' : (oxygen ? (delta > 0 ? 'improving' : 'worsening') : (delta > 0 ? 'increasing' : 'decreasing')));
      return { current, prior, delta, word, css, range };
    }

    function comparisonText(result, digits = 1, unit = '') {
      if (result.delta === null || result.prior === null) return 'need 48h';
      const sign = result.delta >= 0 ? '+' : '';
      return `${result.word} ${sign}${result.delta.toFixed(digits).replace(/\.0$/, '')}${unit}`;
    }

    function metricValue(row, key) {
      const value = Number(row?.[key]);
      if (!Number.isFinite(value)) return null;
      if (key === 'skin_temperature' && (isOffline(row) || value <= 0)) return null;
      return value;
    }

    function validMetricValues(rows, key) {
      return rows.map(row => metricValue(row, key)).filter(value => value !== null);
    }

    function summarizeMetric(rows, key) {
      const values = validMetricValues(rows, key);
      if (!values.length) return { avg: null, min: null, max: null };
      return {
        avg: values.reduce((sum, value) => sum + value, 0) / values.length,
        min: Math.min(...values),
        max: Math.max(...values)
      };
    }

    function sleepBucket(row) {
      const state = String(row?.sleep_state ?? '');
      if (state === '8' || state === '15') return 'sleeping';
      if (state === '1') return 'waking';
      return 'unknown';
    }

    function metricCell(summary, key, field) {
      const value = summary?.[key]?.[field];
      if (value === null || value === undefined) return '—';
      const digits = key === 'heart_rate' ? 0 : 1;
      const suffix = key === 'oxygen_saturation' ? '%' : (key === 'skin_temperature' ? '°C' : '');
      return `${Number(value).toFixed(digits).replace(/\.0$/, '')}${suffix}`;
    }

    function dailyInsightPeriods(sourceRows, sourceChallenges, days = 7) {
      if (!sourceRows.length) return [];
      const challengeWindows = (sourceChallenges || []).map(challenge => ({
        start: Date.parse(challenge.start_time),
        end: Date.parse(challenge.effective_end_time || challenge.end_time || new Date().toISOString())
      })).filter(interval => Number.isFinite(interval.start) && Number.isFinite(interval.end));
      const latest = Date.parse(sourceRows[sourceRows.length - 1].recorded_at);
      if (!Number.isFinite(latest)) return [];
      const dayMs = 24 * 60 * 60 * 1000;
      const todayStart = localDayStartMs(latest);
      return Array.from({ length: days }, (_, index) => {
        const start = todayStart - index * dayMs;
        const end = index === 0 ? latest : start + dayMs;
        const rows = sourceRows.filter(row => {
          const time = Date.parse(row.recorded_at);
          return Number.isFinite(time) && time >= start && time <= end && !isOffline(row) && !timeInIntervals(time, challengeWindows);
        });
        const sleeping = rows.filter(row => sleepBucket(row) === 'sleeping');
        const waking = rows.filter(row => sleepBucket(row) === 'waking');
        const summarizeRows = periodRows => ({
          oxygen_saturation: summarizeMetric(periodRows, 'oxygen_saturation'),
          heart_rate: summarizeMetric(periodRows, 'heart_rate'),
          skin_temperature: summarizeMetric(periodRows, 'skin_temperature')
        });
        return {
          index,
          start,
          end,
          incomplete: index === 0,
          rows,
          sleeping,
          waking,
          overall: summarizeRows(rows),
          sleepingStats: summarizeRows(sleeping),
          wakingStats: summarizeRows(waking)
        };
      });
    }

    function dailyInsightLabel(period) {
      if (period.index === 0) return 'Today so far';
      if (period.index === 1) return 'Yesterday';
      return new Date(period.start).toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' });
    }

    function dailyInsightChartValue(period, group, key) {
      const value = period?.[group]?.[key]?.avg;
      return value === null || value === undefined ? null : Number(value.toFixed(1));
    }

    function renderDailyInsightsChart(periods) {
      const chartPeriods = periods.slice().reverse();
      const labels = chartPeriods.map(period => period.index === 0 ? 'Today' : dailyInsightLabel(period));
      const datasets = [
        { id: 'dailyOxygenAvg', label: 'O₂ avg', data: chartPeriods.map(period => dailyInsightChartValue(period, 'overall', 'oxygen_saturation')), yAxisID: 'oxygen', borderColor: SERIES.o2, backgroundColor: seriesAlpha(SERIES.o2, '20'), pointBackgroundColor: SERIES.o2, pointRadius: 4, tension: .28, spanGaps: false },
        { id: 'dailyOxygenSleep', label: 'Sleep O₂', data: chartPeriods.map(period => dailyInsightChartValue(period, 'sleepingStats', 'oxygen_saturation')), yAxisID: 'oxygen', borderColor: SERIES.sleep, backgroundColor: seriesAlpha(SERIES.sleep, '20'), pointBackgroundColor: SERIES.sleep, borderDash: [5, 4], pointRadius: 3, tension: .28, spanGaps: false },
        { id: 'dailyOxygenWake', label: 'Wake O₂', data: chartPeriods.map(period => dailyInsightChartValue(period, 'wakingStats', 'oxygen_saturation')), yAxisID: 'oxygen', borderColor: SERIES.awake, backgroundColor: seriesAlpha(SERIES.awake, '20'), pointBackgroundColor: SERIES.awake, borderDash: [3, 3], pointRadius: 3, tension: .28, spanGaps: false },
        { id: 'dailyHeartRateAvg', label: 'HR avg', data: chartPeriods.map(period => dailyInsightChartValue(period, 'overall', 'heart_rate')), yAxisID: 'heart', borderColor: SERIES.hr, backgroundColor: seriesAlpha(SERIES.hr, '20'), pointBackgroundColor: SERIES.hr, pointRadius: 4, tension: .25, spanGaps: false },
        { id: 'dailySkinTempAvg', label: 'Skin temp °C', data: chartPeriods.map(period => dailyInsightChartValue(period, 'overall', 'skin_temperature')), yAxisID: 'temp', borderColor: SERIES.temp, backgroundColor: seriesAlpha(SERIES.temp, '20'), pointBackgroundColor: SERIES.temp, pointRadius: 4, tension: .25, spanGaps: false }
      ];
      if (dailyInsightsChart) dailyInsightsChart.destroy();
      dailyInsightsChart = new Chart(el('dailyInsightsChart'), {
        type: 'line',
        data: { labels, datasets },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          animation: { duration: 250 },
          interaction: { mode: 'index', intersect: false },
          plugins: {
            legend: { position: 'bottom', labels: { boxWidth: 10, boxHeight: 10, usePointStyle: true } },
            tooltip: { callbacks: { label: context => {
              const suffix = context.dataset.yAxisID === 'oxygen' ? '%' : (context.dataset.yAxisID === 'temp' ? '°C' : ' bpm');
              const digits = context.dataset.yAxisID === 'heart' ? 0 : 1;
              const value = context.parsed?.y;
              return `${context.dataset.label}: ${value === null || value === undefined ? '—' : `${Number(value).toFixed(digits).replace(/\\.0$/, '')}${suffix}`}`;
            } } }
          },
          scales: {
            x: { grid: { display: false }, ticks: { maxRotation: 0, autoSkip: false } },
            oxygen: { type: 'linear', position: 'left', suggestedMin: 88, suggestedMax: 100, title: { display: true, text: 'O₂ %' } },
            heart: { type: 'linear', position: 'right', grid: { drawOnChartArea: false }, title: { display: true, text: 'HR bpm' } },
            temp: { type: 'linear', position: 'right', display: false, suggestedMin: 28, suggestedMax: 38, grid: { drawOnChartArea: false } }
          }
        }
      });
    }

    function renderDailyInsights(periods) {
      const allRows = periods.flatMap(period => period.rows);
      const sleepingRows = periods.flatMap(period => period.sleeping);
      const wakingRows = periods.flatMap(period => period.waking);
      const overall = {
        oxygen_saturation: summarizeMetric(allRows, 'oxygen_saturation'),
        heart_rate: summarizeMetric(allRows, 'heart_rate'),
        skin_temperature: summarizeMetric(allRows, 'skin_temperature')
      };
      const sleepingStats = {
        oxygen_saturation: summarizeMetric(sleepingRows, 'oxygen_saturation'),
        heart_rate: summarizeMetric(sleepingRows, 'heart_rate'),
        skin_temperature: summarizeMetric(sleepingRows, 'skin_temperature')
      };
      const wakingStats = {
        oxygen_saturation: summarizeMetric(wakingRows, 'oxygen_saturation'),
        heart_rate: summarizeMetric(wakingRows, 'heart_rate'),
        skin_temperature: summarizeMetric(wakingRows, 'skin_temperature')
      };
      el('dailyInsightsSummary').innerHTML = `
        <span>Overall O₂ <b>${metricCell(overall, 'oxygen_saturation', 'avg')}</b></span>
        <span>Sleep O₂ <b>${metricCell(sleepingStats, 'oxygen_saturation', 'avg')}</b></span>
        <span>Wake O₂ <b>${metricCell(wakingStats, 'oxygen_saturation', 'avg')}</b></span>
        <span>Overall HR <b>${metricCell(overall, 'heart_rate', 'avg')}</b></span>
        <span>Sleep HR <b>${metricCell(sleepingStats, 'heart_rate', 'avg')}</b></span>
        <span>Wake HR <b>${metricCell(wakingStats, 'heart_rate', 'avg')}</b></span>
        <span>Skin temp <b>${metricCell(overall, 'skin_temperature', 'avg')}</b></span>`;
      renderDailyInsightsChart(periods);
      const rows = periods.map(period => `
        <tr>
          <td><b>${dailyInsightLabel(period)}</b>${period.incomplete ? ' <span class="small">in progress</span>' : ''}<br><span class="small">${localTime(new Date(period.start).toISOString(), true)} → ${localTime(new Date(period.end).toISOString(), true)} · ${period.rows.length} valid</span></td>
          <td>${metricCell(period.overall, 'oxygen_saturation', 'avg')}</td>
          <td>${metricCell(period.sleepingStats, 'oxygen_saturation', 'avg')}</td>
          <td>${metricCell(period.wakingStats, 'oxygen_saturation', 'avg')}</td>
          <td>${metricCell(period.overall, 'oxygen_saturation', 'min')}</td>
          <td>${metricCell(period.overall, 'oxygen_saturation', 'max')}</td>
          <td>${metricCell(period.overall, 'heart_rate', 'avg')}</td>
          <td>${metricCell(period.sleepingStats, 'heart_rate', 'avg')}</td>
          <td>${metricCell(period.wakingStats, 'heart_rate', 'avg')}</td>
          <td>${metricCell(period.overall, 'heart_rate', 'min')}</td>
          <td>${metricCell(period.overall, 'heart_rate', 'max')}</td>
          <td>${metricCell(period.overall, 'skin_temperature', 'avg')}</td>
          <td>${metricCell(period.overall, 'skin_temperature', 'min')}</td>
          <td>${metricCell(period.overall, 'skin_temperature', 'max')}</td>
        </tr>`).join('');
      el('dailyInsightsTable').innerHTML = `<thead><tr><th>Calendar day</th><th>O₂ avg</th><th>O₂ sleep</th><th>O₂ wake</th><th>O₂ low</th><th>O₂ high</th><th>HR avg</th><th>HR sleep</th><th>HR wake</th><th>HR low</th><th>HR high</th><th>Temp avg</th><th>Temp low</th><th>Temp high</th></tr></thead><tbody>${rows || '<tr><td colspan="14" class="empty">No valid readings in the last 7 calendar days.</td></tr>'}</tbody>`;
      const totalSamples = periods.reduce((sum, period) => sum + period.rows.length, 0);
      el('dailyInsightsMeta').textContent = `${periods.length} calendar days · today is in progress · ${totalSamples} valid samples · offline/sock-off and O₂ challenge samples excluded`;
    }

    async function openDailyInsightsModal() {
      el('challengesPanel').classList.add('hidden');
      el('challengesToggle').setAttribute('aria-expanded', 'false');
      el('dailyInsightsModal').classList.remove('hidden');
      if (dailyInsightsChart) {
        dailyInsightsChart.destroy();
        dailyInsightsChart = null;
      }
      el('dailyInsightsSummary').innerHTML = '<span>Loading daily insights…</span>';
      el('dailyInsightsTable').innerHTML = '<tbody><tr><td class="empty">Loading last 7 calendar days…</td></tr></tbody>';
      try {
        const qs = queryParams({}, { hoursOverride: 168 });
        const challengeQs = queryParams({ limit: '500', offset: '0' }, { hoursOverride: 168 });
        const [readingData, challengeData] = await Promise.all([
          fetchJson(`${API_BASE}/api/readings?${qs}`),
          fetchJson(`${API_BASE}/api/oxygen-challenges?${challengeQs}`)
        ]);
        renderDailyInsights(dailyInsightPeriods(readingData || [], challengeData.items || []));
      } catch (error) {
        console.error(error);
        el('dailyInsightsSummary').innerHTML = '<span>Could not load daily insights.</span>';
        el('dailyInsightsTable').innerHTML = '<tbody><tr><td class="empty">Daily insights failed to load. Try refresh and open again.</td></tr></tbody>';
      }
    }

    function todayAverageOxygen() {
      const latest = readings[readings.length - 1];
      if (!latest) return null;
      const latestDate = new Date(latest.recorded_at);
      const sameLocalDay = readings.filter(row => {
        const date = new Date(row.recorded_at);
        return !isOffline(row) && date.toDateString() === latestDate.toDateString();
      });
      return average(sameLocalDay.map(row => row.oxygen_saturation));
    }

    function timeTick(value) {
      const date = new Date(value);
      const hours = selectedHours();
      if (hours && hours <= 24) return date.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
      return date.toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric' });
    }

    function rollupLabel(row) {
      const date = new Date(row.bucket_start);
      const bucket = row.bucket;
      if (bucket === 'day') return date.toLocaleDateString([], { month: 'short', day: 'numeric' });
      if (selectedHours() && selectedHours() <= 24) return date.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
      return date.toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
    }

    function queryParams(extra = {}, options = {}) {
      const window = el('window').value;
      const params = new URLSearchParams({ limit: '100000', ...extra });
      const hours = options.hoursOverride ?? (window === 'all' ? null : window);
      if (hours) params.set('hours', hours);
      const account = selectedAccount();
      if (account) params.set('account', account);
      const device = selectedDevice();
      if (device) params.set('device', device);
      return params.toString();
    }

    function selectedAccount() {
      return el('accountSelect')?.value || new URLSearchParams(window.location.search).get('account') || '';
    }

    function currentAccount() {
      const id = selectedAccount();
      return accounts.find(account => String(account.id) === String(id)) || accounts[0] || null;
    }

    function dashboardPreferences(account = currentAccount()) {
      return account?.dashboard_preferences && typeof account.dashboard_preferences === 'object' ? account.dashboard_preferences : {};
    }

    function chartVisibilityPreferences() {
      return dashboardPreferences().chart_visibility || {};
    }

    function chartSettingsPreferences() {
      return dashboardPreferences().chart_settings || {};
    }

    function preferredDatasetHidden(datasetId, fallback = false) {
      const visibility = chartVisibilityPreferences();
      if (Object.prototype.hasOwnProperty.call(visibility, datasetId)) return !Boolean(visibility[datasetId]);
      return fallback;
    }

    function accountLabel(account = currentAccount()) {
      return account?.display_name || account?.email || (account?.id ? `Account ${account.id}` : 'Owlet profile');
    }

    function accountInitials(account = currentAccount()) {
      const label = accountLabel(account).replace(/[^a-z0-9@.\s_-]/gi, '').trim();
      const emailName = label.includes('@') ? label.split('@')[0] : label;
      const parts = emailName.split(/[\s._-]+/).filter(Boolean);
      const initials = parts.length > 1 ? `${parts[0][0]}${parts[1][0]}` : (emailName.slice(0, 2) || 'O');
      return initials.toUpperCase();
    }

    function renderProfileMenu() {
      if (!el('profileMenuTitle')) return;
      const account = currentAccount();
      const device = currentDevice();
      const deviceName = device?.baby_name || device?.name || 'Owlet sock';
      const label = accountLabel(account);
      const initials = accountInitials(account);
      ['profileAvatar', 'profileMenuAvatar'].forEach(id => { const node = el(id); if (node) node.textContent = initials; });
      el('profileAccountName').textContent = label;
      el('profileMenuTitle').textContent = label;
      el('profileDeviceName').textContent = deviceName;
      const intervalSelect = el('pollIntervalSetting');
      if (intervalSelect) intervalSelect.value = String(account?.poll_interval_seconds || 5);
      el('profileMenuSubtitle').textContent = `${deviceName}${account?.status && account.status !== 'active' ? ` · ${account.status.replace('_', ' ')}` : ''}`;
      el('profileMenuWrap')?.classList.toggle('share-only-hidden', SHARE_MODE);
    }

    function setUrlAccount(account) {
      const url = new URL(window.location.href);
      if (account) url.searchParams.set('account', account);
      else url.searchParams.delete('account');
      url.searchParams.delete('device');
      window.history.replaceState({}, '', url);
    }

    function selectedDevice() {
      return el('deviceSelect')?.value || new URLSearchParams(window.location.search).get('device') || '';
    }

    function setUrlDevice(device) {
      const url = new URL(window.location.href);
      if (device) url.searchParams.set('device', device);
      else url.searchParams.delete('device');
      window.history.replaceState({}, '', url);
    }

    async function loadAccounts() {
      if (SHARE_MODE) return;
      try {
        const data = await fetchJson(`${API_BASE}/api/accounts`);
        accounts = data.accounts || [];
      } catch (error) {
        console.error(error);
        accounts = [];
      }
      const requested = new URLSearchParams(window.location.search).get('account') || '';
      const selected = accounts.some(account => String(account.id) === requested) ? requested : (accounts[0]?.id ? String(accounts[0].id) : '');
      renderAccountOptions(selected);
      if (selected) setUrlAccount(selected);
      applyAccountPreferencesToControls();
    }

    function renderAccountOptions(selected = selectedAccount()) {
      const cluster = el('accountCluster');
      if (!cluster) return;
      cluster.classList.toggle('hidden', SHARE_MODE);
      el('accountSelect').innerHTML = accounts.length
        ? accounts.map(account => {
          const status = account.status && account.status !== 'active' ? ` (${account.status.replace('_', ' ')})` : '';
          const label = account.display_name || account.email || `Account ${account.id}`;
          return `<option value="${account.id}" ${String(account.id) === String(selected) ? 'selected' : ''}>${label}${status}</option>`;
        }).join('')
        : '<option value="">Default</option>';
      renderProfileMenu();
    }

    async function addAccountFromPrompt() {
      const email = prompt('Owlet email');
      if (!email) return;
      const password = prompt('Owlet password (used once to fetch refresh token; not stored)');
      if (!password) return;
      const region = prompt('Owlet region', 'world') || 'world';
      const displayName = prompt('Display name', email) || email;
      try {
        updateRefreshButton('Adding account…');
        const response = await fetch(`${API_BASE}/api/accounts`, {
          method: 'POST',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ email, password, region, display_name: displayName })
        });
        if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
        const data = await response.json();
        await loadAccounts();
        const accountId = data.account?.id ? String(data.account.id) : selectedAccount();
        if (accountId) setUrlAccount(accountId);
        await loadDevices();
        loadedHours = null;
        readingsTableSignature = '';
        safeRefresh({ resetZoom: true, force: true });
      } catch (error) {
        console.error(error);
        alert('Could not validate that Owlet account. Check the email/password/region and try again.');
      }
    }

    async function updateCurrentAccountPreference(patch) {
      const account = currentAccount();
      if (!account?.id) return null;
      const response = await fetch(`${API_BASE}/api/accounts/${account.id}`, {
        method: 'PATCH',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(patch)
      });
      if (!response.ok) throw new Error(`Could not save account settings: ${response.status}`);
      const data = await response.json();
      const index = accounts.findIndex(item => String(item.id) === String(data.account?.id));
      if (index >= 0) accounts[index] = data.account;
      else if (data.account) accounts.push(data.account);
      renderAccountOptions(String(data.account?.id || account.id));
      renderProfileMenu();
      return data.account;
    }

    async function saveDashboardPreferencePatch(patch) {
      if (SHARE_MODE || applyingAccountPreferences || !currentAccount()?.id) return null;
      try {
        return await updateCurrentAccountPreference({ dashboard_preferences: patch });
      } catch (error) {
        console.error(error);
        return null;
      }
    }

    function applyAccountPreferencesToControls() {
      const settings = chartSettingsPreferences();
      applyingAccountPreferences = true;
      try {
        if (settings.window && el('window')?.querySelector(`option[value="${settings.window}"]`)) el('window').value = settings.window;
        if (settings.smoothing && el('smoothing')?.querySelector(`option[value="${settings.smoothing}"]`)) el('smoothing').value = settings.smoothing;
        if (settings.layout && el('chartLayout')?.querySelector(`option[value="${settings.layout}"]`)) el('chartLayout').value = settings.layout;
        if (typeof settings.challenge_bands === 'boolean') challengeBandsEnabled = settings.challenge_bands;
        if (typeof settings.sleep_highlight === 'boolean') sleepHighlightEnabled = settings.sleep_highlight;
        if (typeof settings.sleep_ballpark === 'boolean') sleepBallparkEnabled = settings.sleep_ballpark;
        el('challengeBandsToggle').checked = challengeBandsEnabled;
        el('sleepHighlightToggle').checked = sleepHighlightEnabled;
        el('sleepBallparkToggle').checked = sleepBallparkEnabled;
        el('sleepBallparkToggle').disabled = !sleepHighlightEnabled;
      } finally {
        applyingAccountPreferences = false;
      }
    }

    function persistChartSettings(extra = {}) {
      return saveDashboardPreferencePatch({
        chart_settings: {
          window: el('window')?.value,
          smoothing: el('smoothing')?.value,
          layout: el('chartLayout')?.value,
          challenge_bands: challengeBandsEnabled,
          sleep_highlight: sleepHighlightEnabled,
          sleep_ballpark: sleepBallparkEnabled,
          ...extra
        }
      });
    }

    function persistChartVisibility(chart) {
      if (!chart || applyingAccountPreferences || SHARE_MODE) return;
      const visibility = {};
      chart.data.datasets.forEach((dataset, index) => {
        const key = dataset.id || dataset.label;
        if (!key) return;
        visibility[key] = chart.isDatasetVisible(index);
      });
      saveDashboardPreferencePatch({ chart_visibility: visibility });
    }

    async function loadDevices() {
      try {
        const account = selectedAccount();
        const accountQs = account ? `?account=${encodeURIComponent(account)}` : '';
        const data = await fetchJson(`${API_BASE}/api/devices${accountQs}`);
        devices = data.devices || [];
      } catch (error) {
        console.error(error);
        devices = [];
      }
      const requested = new URLSearchParams(window.location.search).get('device') || '';
      const selected = devices.some(device => device.serial === requested) ? requested : (devices[0]?.serial || '');
      renderDeviceOptions(selected);
      if (selected) setUrlDevice(selected);
      renderBabyName();
    }

    function compactDeviceName(device) {
      const name = device?.name || device?.serial || 'Device';
      const digits = name.match(/(\d{3,})\s*$/)?.[1] || device?.serial?.slice(-4);
      if (device?.baby_name && device.baby_name !== name) return device.baby_name;
      return digits ? `Sock ${digits}` : name;
    }

    function renderDeviceOptions(selected = selectedDevice()) {
      el('deviceSelect').innerHTML = devices.length
        ? devices.map(device => {
          const label = isMobileViewport() ? compactDeviceName(device) : (device.name || device.serial);
          return `<option value="${device.serial}" ${device.serial === selected ? 'selected' : ''}>${label}</option>`;
        }).join('')
        : '<option value="">All devices</option>';
    }

    function currentDevice() {
      const serial = selectedDevice();
      return devices.find(device => device.serial === serial) || devices[0] || null;
    }

    function renderBabyName() {
      renderProfileMenu();
    }

    async function fetchJson(url) {
      const response = await fetch(url, { credentials: 'include' });
      if (!response.ok) throw new Error(`${response.status} ${response.statusText} while fetching ${url}`);
      return response.json();
    }

    async function refresh({ resetZoom = false } = {}) {
      secondsUntilRefresh = refreshSeconds();
      setInitialLoading('Loading readings and notifications…');
      updateRefreshButton('Refreshing…');
      const token = ++refreshToken;
      const previousLatest = lastLatestTimestamp;
      const previousVisible = visibleWindowSnapshot({ resetZoom });
      if (resetZoom || loadedHours === null || selectedHours() === null) loadedHours = historyHoursForSelection();
      const qs = queryParams();
      const dataQs = queryParams({}, { hoursOverride: loadedHours });
      const rollupQs = queryParams({ bucket: rollupBucket() }, { hoursOverride: loadedHours });
      const notificationQs = queryParams({ limit: '500', offset: '0' }, { hoursOverride: loadedHours });
      const challengeQs = queryParams({ limit: '100', offset: '0' }, { hoursOverride: loadedHours });
      const [health, rows, notificationData, challengeData] = await Promise.all([
        fetchJson(`${API_BASE}/api/health`),
        fetchJson(`${API_BASE}/api/readings?${dataQs}`),
        fetchJson(`${API_BASE}/api/notifications?${notificationQs}`),
        fetchJson(`${API_BASE}/api/oxygen-challenges?${challengeQs}`)
      ]);
      if (token !== refreshToken) return;
      readings = rows;
      notifications = notificationData;
      challenges = challengeData;
      lastLatestTimestamp = readings.length ? readings[readings.length - 1].recorded_at : null;
      zoomWindow = refreshedVisibleRange(previousVisible);
      setInitialLoading('Drawing charts and controls…');
      renderStatus(health);
      applyFilter();
      renderCharts({ deferTrend: true });
      renderChallenges();
      updateRefreshButton();
      hideInitialLoading();
      if (previousLatest && lastLatestTimestamp && lastLatestTimestamp !== previousLatest) showNewDataPulse();
      hydrateSecondaryData({ qs, rollupQs, token, compareRows: rows }).catch(error => {
        console.error(error);
        if (el('refresh')) el('refresh').title = 'Some details failed; core readings are still shown.';
      });
    }

    async function hydrateSecondaryData({ qs, rollupQs, token, compareRows }) {
      const [stats, insightData, rollupData] = await Promise.all([
        fetchJson(`${API_BASE}/api/summary?${qs}`),
        fetchJson(`${API_BASE}/api/insights?${qs}`),
        fetchJson(`${API_BASE}/api/rollups?${rollupQs}`)
      ]);
      if (token !== refreshToken) return;
      summary = stats;
      insights = insightData;
      rollups = rollupData.rollups || [];
      renderInsights();
      renderRollups();
    }

    function renderStatus(health) {
      const mode = SHARE_MODE ? 'Shared read-only view' : health.database_path;
      const latest = readings[readings.length - 1];
      const offlineNow = latest && isOffline(latest);
      const label = offlineNow ? 'Device offline / sock off' : (health.collecting ? 'Collecting live' : 'Stored data only');
      const dotClass = offlineNow ? 'offline' : (health.collecting ? 'good' : '');
      const titleDot = el('titleStatusDot');
      if (titleDot) titleDot.className = `status-dot title-status-dot ${dotClass}`;
    }

    function renderInsights() {
      const rawLatest = readings[readings.length - 1];
      const latest = rawLatest ? { ...rawLatest, sleep_state_label: isOffline(rawLatest) ? 'offline / sock off' : stateLabel(rawLatest.sleep_state) } : insights.latest;
      const currentSleep = currentSleepStatus();
      const oxygenCompare = comparisonFor('oxygen_saturation', 0.25);
      const hrCompare = comparisonFor('heart_rate', 1);
      el('latestOxygen').textContent = latest ? fmt(latest.oxygen_saturation, '% O₂') : '—';
      el('latestOxygen').className = `oxygen-value ${oxygenValueClass(latest?.oxygen_saturation)}`;
      el('todayOxygen').textContent = oxygenCompare.current === null ? '—' : `${oxygenCompare.current.toFixed(1).replace(/\.0$/, '')}%`;
      el('todayOxygen').className = `inline-stat oxygen-value ${oxygenValueClass(oxygenCompare.current)}`;
      el('latestHr').textContent = latest ? fmt(latest.heart_rate, ' bpm') : '—';
      el('avgHr').textContent = hrCompare.current === null ? '—' : fmt(hrCompare.current.toFixed(0), ' bpm');
      el('latestState').textContent = latest ? latest.sleep_state_label : '—';
      el('latestMove').textContent = latest ? num(latest.movement) : '—';

      const breathing = insights.breathing;
      el('o2Compare').textContent = `${comparisonText(oxygenCompare, 1, ' pts')} vs same time yesterday`;
      el('o2Compare').className = `inline-stat ${oxygenCompare.css}`;
      el('priorOxygen').textContent = oxygenCompare.prior === null ? fmt(breathing.previous_avg_oxygen, '%') : fmt(oxygenCompare.prior.toFixed(1), '%');
      el('hrCompare').textContent = `${comparisonText(hrCompare, 0, ' bpm')} vs same time yesterday`;
      el('hrCompare').className = `inline-stat ${hrCompare.css}`;

      const sleep = insights.sleep;
      el('sleepTotal').textContent = currentSleep.title;
      el('sleepSummary').textContent = `${hours(sleep.sleep_seconds)} sleep · ${hours(sleep.awake_seconds)} awake · ${currentSleep.detail}`;
      el('lightSleep').textContent = hours(sleep.light_sleep_seconds);
      el('deepSleep').textContent = hours(sleep.deep_sleep_seconds);
      el('awakeTime').textContent = hours(sleep.awake_seconds);
      const total = Math.max(1, sleep.light_sleep_seconds + sleep.deep_sleep_seconds + sleep.awake_seconds);
      el('lightBar').style.width = `${(sleep.light_sleep_seconds / total) * 100}%`;
      el('deepBar').style.width = `${(sleep.deep_sleep_seconds / total) * 100}%`;
      el('awakeBar').style.width = `${(sleep.awake_seconds / total) * 100}%`;
      el('coverage').textContent = `${summary.window} · ${summary.challenge_count || 0} in challenges`;
    }

    function currentSleepStatus() {
      if (!readings.length) return { title: '—', detail: 'No current state yet' };
      const range = dataRange();
      const latest = readings[readings.length - 1];
      const cls = stateClass(latest);
      const latestTime = Date.parse(latest.recorded_at);
      let start = latestTime;
      for (let index = readings.length - 2; index >= 0; index -= 1) {
        const row = readings[index];
        const rowTime = Date.parse(row.recorded_at);
        const nextTime = Date.parse(readings[index + 1].recorded_at);
        if (stateClass(row) !== cls || !Number.isFinite(rowTime) || !Number.isFinite(nextTime) || nextTime - rowTime > TREND_MAX_SAMPLE_GAP_MS) break;
        start = rowTime;
      }
      const end = range?.max && range.max >= latestTime ? range.max + 60 * 1000 : latestTime + 60 * 1000;
      const duration = durationText(Math.max(60, (end - start) / 1000));
      const names = { light: 'Sleeping', deep: 'Deep sleep', awake: 'Awake', offline: 'Offline', inactive: 'Inactive' };
      const name = names[cls] || 'Unknown';
      return {
        title: `${name} (${duration})`,
        detail: `current ${name.toLowerCase()}`,
      };
    }

    function chartPoints(points) {
      const range = visibleRange();
      const clean = points.filter(point => point && Number.isFinite(Number(point.x)));
      return extendPointsToVisibleEdges(clean, range);
    }

    function extendPointsToVisibleEdges(points, range = visibleRange(), sourcePoints = points) {
      if (!range || !points.length || range.max <= range.min) return points;
      const visible = sourcePoints.filter(point => point && Number.isFinite(point.x) && point.x >= range.min && point.x <= range.max);
      if (!visible.length) return points;
      const extended = points.slice();
      const first = visible[0];
      const last = visible[visible.length - 1];
      // Never bridge a collector gap by copying a neighbor's value to the frame edge.
      if (first.x > range.min && !extended.some(point => point.x === range.min)) {
        extended.push(first.x - range.min > COLLECTOR_GAP_MS ? { x: range.min, y: null, reason: 'collector-gap' } : { ...first, x: range.min });
      }
      if (last.x < range.max && !extended.some(point => point.x === range.max)) {
        extended.push(range.max - last.x > COLLECTOR_GAP_MS ? { x: range.max, y: null, reason: 'collector-gap' } : { ...last, x: range.max });
      }
      return extended.sort((a, b) => a.x - b.x);
    }

    function readingSeries(key) {
      const minutes = smoothingMinutes();
      if (minutes > 0) return rollingAverageForKey(key, minutes);
      const source = [];
      readings.forEach((row, index) => {
        const time = Date.parse(row.recorded_at);
        const previous = index > 0 ? Date.parse(readings[index - 1].recorded_at) : null;
        if (previous !== null && time - previous > COLLECTOR_GAP_MS) {
          source.push({ x: previous + 1, y: null, reason: 'collector-gap' });
        }
        const offlineTransition = isOffline(row) && (!isOffline(readings[index - 1]) || !isOffline(readings[index + 1]));
        source.push({ x: time, y: metricValue(row, key), reason: offlineTransition ? 'offline-transition' : undefined });
      });
      return chartPoints(source);
    }

    function rollingAverageForKey(key, minutes) {
      const windowMs = minutes * 60 * 1000;
      const queue = [];
      let sum = 0;
      const points = [];
      let previousValidTime = null;
      const reset = () => { queue.length = 0; sum = 0; };
      readings.forEach(row => {
        const time = Date.parse(row.recorded_at);
        const value = metricValue(row, key);
        if (isOffline(row) || value === null) {
          reset();
          points.push({ x: time, y: key === 'skin_temperature' ? null : 0, reason: key === 'skin_temperature' ? 'missing' : 'offline-zero' });
          previousValidTime = null;
          return;
        }
        if (previousValidTime && time - previousValidTime > TREND_MAX_SAMPLE_GAP_MS) reset();
        previousValidTime = time;
        queue.push({ time, value });
        sum += value;
        while (queue.length && queue[0].time < time - windowMs) sum -= queue.shift().value;
        points.push({ x: time, y: sum / queue.length });
      });
      return chartPoints(points);
    }

    function trendMaxSourcePoints() {
      return isMobileViewport() ? TREND_MAX_SOURCE_POINTS_MOBILE : TREND_MAX_SOURCE_POINTS_DESKTOP;
    }

    function trendSourceRows(maxPoints = trendMaxSourcePoints()) {
      if (readings.length <= maxPoints) return readings;
      const stride = Math.ceil(readings.length / maxPoints);
      return readings.filter((row, index) => {
        const previous = readings[index - 1];
        const next = readings[index + 1];
        const time = Date.parse(row.recorded_at);
        const previousTime = previous ? Date.parse(previous.recorded_at) : null;
        const offlineTransition = isOffline(row) && (!isOffline(previous) || !isOffline(next));
        const gapBoundary = Number.isFinite(time) && Number.isFinite(previousTime) && time - previousTime > TREND_MAX_SAMPLE_GAP_MS;
        return index === 0 || index === readings.length - 1 || offlineTransition || gapBoundary || index % stride === 0;
      });
    }

    function rollingOxygenAverage(minutes, rows = readings) {
      const windowMs = minutes * 60 * 1000;
      const queue = [];
      let sum = 0;
      const points = [];
      let previousValidTime = null;
      let inOfflineGap = false;
      const resetWindow = () => { queue.length = 0; sum = 0; };
      const addGapMarker = (time, reason) => {
        if (!Number.isFinite(time)) return;
        const last = points[points.length - 1];
        if (last && last.y === null && last.reason === reason && Math.abs(last.x - time) < TREND_MAX_SAMPLE_GAP_MS) return;
        points.push({ x: time, y: null, reason });
      };
      rows.forEach(row => {
        const time = Date.parse(row.recorded_at);
        const value = Number(row.oxygen_saturation);
        const offline = isOffline(row) || !Number.isFinite(value);
        if (!Number.isFinite(time)) return;
        if (offline) {
          if (!inOfflineGap) addGapMarker(time, 'offline');
          inOfflineGap = true;
          resetWindow();
          previousValidTime = null;
          return;
        }
        if (inOfflineGap) {
          inOfflineGap = false;
          resetWindow();
        }
        if (previousValidTime !== null && time - previousValidTime > TREND_MAX_SAMPLE_GAP_MS) {
          addGapMarker(previousValidTime + 60 * 1000, 'missing-data');
          resetWindow();
        }
        queue.push({ time, value });
        sum += value;
        while (queue.length && queue[0].time < time - windowMs) sum -= queue.shift().value;
        points.push({ x: time, y: sum / queue.length });
        previousValidTime = time;
      });
      return chartPoints(points);
    }

    function oxygenTrendSignal(short, long) {
      const byTime = new Map(long.map(point => [point.x, point.y]));
      return short.map(point => {
        const baseline = byTime.get(point.x);
        if (baseline === undefined) return null;
        if (point.y === null || baseline === null) return { x: point.x, y: null, reason: point.reason || 'gap' };
        return { x: point.x, y: point.y - baseline };
      }).filter(Boolean);
    }

    function offlineIntervals() {
      const intervals = [];
      let activeStart = null;
      readings.forEach((row, index) => {
        const current = Date.parse(row.recorded_at);
        const next = readings[index + 1] ? Date.parse(readings[index + 1].recorded_at) : current + 60 * 1000;
        if (isOffline(row)) {
          if (activeStart === null) activeStart = current;
        } else if (activeStart !== null) {
          intervals.push({ start: activeStart, end: current });
          activeStart = null;
        }
        if (isOffline(row) && index === readings.length - 1) {
          intervals.push({ start: activeStart, end: Math.max(next, current + 60 * 1000) });
          activeStart = null;
        }
      });
      return intervals;
    }

    function gapIntervals() {
      const intervals = [];
      for (let i = 1; i < readings.length; i += 1) {
        const previous = Date.parse(readings[i - 1].recorded_at);
        const current = Date.parse(readings[i].recorded_at);
        if (current - previous > COLLECTOR_GAP_MS) intervals.push({ start: previous, end: current });
      }
      const range = visibleRange();
      const last = readings.length ? Date.parse(readings[readings.length - 1].recorded_at) : null;
      if (last !== null && range && range.max - last > COLLECTOR_GAP_MS) {
        intervals.push({ start: last, end: range.max });
      }
      return intervals;
    }

    function nearestReadingIndex(timestamp) {
      let bestIndex = -1;
      let bestDelta = Infinity;
      readings.forEach((row, index) => {
        const delta = Math.abs(Date.parse(row.recorded_at) - timestamp);
        if (delta < bestDelta) {
          bestDelta = delta;
          bestIndex = index;
        }
      });
      return bestIndex;
    }

    function surroundingDataLines(timestamp) {
      const index = nearestReadingIndex(timestamp);
      if (index < 0) return [];
      return readings.slice(Math.max(0, index - 1), Math.min(readings.length, index + 2)).map(row => `${localTime(row.recorded_at, true)} · O₂ ${fmt(row.oxygen_saturation, '%')} · HR ${fmt(row.heart_rate, ' bpm')}`);
    }

    function notificationPoints() {
      return (notifications.items || []).slice().reverse().map(item => {
        const timestamp = Date.parse(item.recorded_at);
        const y = item.oxygen_saturation && item.oxygen_saturation > 0 ? item.oxygen_saturation : 89;
        return {
          x: timestamp,
          y,
          severity: item.severity,
          tooltipLines: [
            `${item.title} · ${localTime(item.recorded_at)}`,
            item.message,
            `O₂ ${fmt(item.oxygen_saturation, '%')} · HR ${fmt(item.heart_rate, ' bpm')} · battery ${fmt(item.battery, '%')}`,
            ...surroundingDataLines(timestamp)
          ]
        };
      });
    }

    function tooltipTitle(items) {
      const first = items?.[0];
      const rawX = first?.raw?.x;
      const parsedX = first?.parsed?.x;
      const timestamp = Number.isFinite(Number(parsedX)) ? Number(parsedX) : Number(rawX);
      return Number.isFinite(timestamp) ? localTime(new Date(timestamp).toISOString()) : '';
    }

    function tooltipLabel(context) {
      if (context.dataset.id === 'notifications') return context.raw.tooltipLines;
      if (context.chart?.canvas?.id === 'stateChart') {
        const stage = sleepStageInfo(context.dataset.label);
        const value = context.parsed?.y;
        const amount = Number.isFinite(Number(value)) ? `${Number(value).toFixed(1).replace(/\.0$/, '')}h` : '—';
        return [`${stage.name}: ${amount}`, stage.description];
      }
      if (context.dataset.id === 'o2TrendSignal') {
        const value = context.parsed?.y;
        if (value === null || value === undefined || !Number.isFinite(Number(value))) {
          return 'Trend gap — offline or missing data.';
        }
        const valueText = `${value > 0 ? '+' : ''}${Number(value).toFixed(1)} pts`;
        if (value > 0.25) return `Trend signal ${valueText}: recent O₂ is running above baseline.`;
        if (value < -0.25) return `Trend signal ${valueText}: recent O₂ is running below baseline.`;
        return `Trend signal ${valueText}: recent O₂ is near baseline.`;
      }
      if (context.dataset.id === 'o2Trailing30') {
        return `${context.dataset.label}: ${chartValue(context.parsed.y, '%')}`;
      }
      if (context.dataset.id === 'o2Baseline4h') {
        return `${context.dataset.label}: ${chartValue(context.parsed.y, '%')}`;
      }
      const value = context.parsed?.y;
      return `${context.dataset.label}: ${chartValue(value)}`;
    }

    function legendOptions(overrides = {}) {
      if (overrides.display === false) return { display: false };
      const mobile = window.matchMedia('(max-width: 640px)').matches;
      return {
        position: overrides.position || (mobile ? 'chartArea' : 'bottom'),
        align: overrides.align || 'start',
        labels: { boxWidth: 6, boxHeight: 6, padding: mobile ? 8 : 14, usePointStyle: true, font: { size: mobile ? 10 : 11 } },
        onClick(event, legendItem, legend) {
          Chart.defaults.plugins.legend.onClick.call(this, event, legendItem, legend);
          persistChartVisibility(legend.chart);
        }
      };
    }

    function chartTimeTickLimit() {
      return window.matchMedia('(max-width: 640px)').matches ? 6 : 14;
    }

    function timeTickValues(range, maxTicks = chartTimeTickLimit()) {
      if (!range || range.max <= range.min) return [];
      const count = Math.max(2, maxTicks);
      return Array.from({ length: count }, (_, index) => range.min + ((range.max - range.min) * index) / (count - 1));
    }

    function xScaleOptions({ hideTicks = false } = {}) {
      const range = visibleRange();
      return {
        type: 'linear',
        offset: false,
        min: range?.min,
        max: range?.max,
        ticks: { display: !hideTicks, maxRotation: 0, autoSkip: true, maxTicksLimit: chartTimeTickLimit(), callback: timeTick }
      };
    }

    function zoomOptions() {
      const mobile = isMobileViewport();
      return {
        limits: { x: { min: 'original', max: 'original' } },
        pan: { enabled: true, mode: 'x', threshold: mobile ? 4 : 8, onPanComplete: ({ chart }) => { syncZoomFrom(chart); loadOlderHistoryIfNeeded().catch(console.error); } },
        zoom: {
          drag: { enabled: !mobile, backgroundColor: seriesAlpha(SERIES.o2, '1f'), borderColor: seriesAlpha(SERIES.o2, '8c'), borderWidth: 1 },
          wheel: { enabled: true, speed: 0.08 },
          pinch: { enabled: true },
          mode: 'x',
          onZoomComplete: ({ chart }) => syncZoomFrom(chart)
        }
      };
    }

    function chartOptions(extraScales, options = {}) {
      const mobile = window.matchMedia('(max-width: 640px)').matches;
      const scales = { x: xScaleOptions({ hideTicks: options.hideXTicks }), ...extraScales };
      Object.entries(scales).forEach(([key, scale]) => {
        // Quiet axes everywhere: no spine, no vertical grid, faint horizontal
        // rules only, and no boxed axis titles (the legend carries the units).
        scale.border = { display: false, ...(scale.border || {}) };
        scale.grid = { drawTicks: false, ...(key === 'x' ? { display: false } : {}), ...(scale.grid || {}) };
        if (scale.title && !options.keepAxisTitles) scale.title.display = false;
        if (key === 'x') return;
        scale.ticks = { callback: axisTick, padding: 6, maxTicksLimit: mobile ? 5 : 6, ...(scale.ticks || {}) };
      });
      if (mobile) {
        Object.entries(scales).forEach(([key, scale]) => {
          scale.ticks = { ...(scale.ticks || {}), font: { size: 10 }, padding: 2 };
        });
      }
      return {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        interaction: { mode: 'index', intersect: false },
        plugins: { legend: legendOptions(options.legend || {}), tooltip: { callbacks: { title: tooltipTitle, label: tooltipLabel } }, zoom: zoomOptions(), dayBoundaries: {}, challengeBands: { intervals: challengeBandsEnabled ? challengeIntervals() : [] }, offlineBands: { intervals: offlineIntervals() }, noDataBands: { intervals: gapIntervals() } },
        scales
      };
    }

    function updateChallengeBandOptions() {
      const intervals = challengeBandsEnabled ? challengeIntervals() : [];
      chartList().forEach(chart => {
        chart.options.plugins.challengeBands = { ...(chart.options.plugins.challengeBands || {}), intervals };
        chart.update('none');
      });
    }

    function notificationHit(chart, event) {
      const datasetIndex = chart.data.datasets.findIndex(dataset => dataset.id === 'notifications');
      if (datasetIndex < 0) return null;
      const meta = chart.getDatasetMeta(datasetIndex);
      const rect = chart.canvas.getBoundingClientRect();
      const source = event.touches?.[0] || event.changedTouches?.[0] || event;
      const x = source.clientX - rect.left;
      const y = source.clientY - rect.top;
      const mobile = window.matchMedia('(max-width: 640px)').matches;
      const xRadius = mobile ? 34 : 26;
      const yRadius = mobile ? 52 : 40;
      let best = null;
      meta.data.forEach((point, index) => {
        if (!point || point.skip) return;
        const dx = Math.abs(point.x - x);
        const dy = Math.abs(point.y - y);
        if (dx > xRadius || dy > yRadius) return;
        const score = dx + dy * 0.35;
        if (!best || score < best.score) best = { datasetIndex, index, point, score };
      });
      return best;
    }

    function setNotificationTooltip(chart, hit) {
      if (hit) {
        chart.tooltip.setActiveElements([{ datasetIndex: hit.datasetIndex, index: hit.index }], { x: hit.point.x, y: hit.point.y });
        chart.setActiveElements([{ datasetIndex: hit.datasetIndex, index: hit.index }]);
        chart.$notificationHoverActive = true;
        chart.canvas.style.cursor = 'help';
        chart.update();
      } else if (chart.$notificationHoverActive) {
        chart.tooltip.setActiveElements([], { x: 0, y: 0 });
        chart.setActiveElements([]);
        chart.$notificationHoverActive = false;
        chart.canvas.style.cursor = '';
        chart.update('none');
      }
    }

    function attachNotificationHover(chart) {
      // Flag lives on the canvas (which outlives destroyed charts) and the live
      // chart is resolved per event, so layout toggles never leave stale handlers.
      const canvas = chart.canvas;
      if (canvas.dataset.notificationHoverAttached === 'true') return;
      const live = () => Chart.getChart(canvas);
      const update = event => { const target = live(); if (target) setNotificationTooltip(target, notificationHit(target, event)); };
      canvas.addEventListener('mousemove', update);
      canvas.addEventListener('click', update);
      canvas.addEventListener('touchmove', update, { passive: true });
      canvas.addEventListener('mouseleave', () => { const target = live(); if (target) setNotificationTooltip(target, null); });
      canvas.dataset.notificationHoverAttached = 'true';
    }

    function syncZoomFrom(sourceChart) {
      if (syncInProgress) return;
      const scale = sourceChart.scales.x;
      if (!Number.isFinite(scale.min) || !Number.isFinite(scale.max)) return;
      zoomWindow = { min: scale.min, max: scale.max };
      syncInProgress = true;
      chartList().forEach(chart => {
        if (chart === sourceChart) return;
        chart.options.scales.x.min = zoomWindow.min;
        chart.options.scales.x.max = zoomWindow.max;
        chart.update('none');
      });
      renderStateStrip();
      updatePanControl();
      syncInProgress = false;
    }

    function resetZoom() {
      const nextRange = defaultVisibleRange();
      zoomWindow = nextRange ? { ...nextRange } : null;
      syncInProgress = true;
      chartList().forEach(chart => {
        if (typeof chart.resetZoom === 'function') chart.resetZoom('none');
        chart.options.scales.x.min = zoomWindow?.min;
        chart.options.scales.x.max = zoomWindow?.max;
        chart.update('none');
      });
      syncInProgress = false;
      renderStateStrip();
      updatePanControl();
      loadOlderHistoryIfNeeded().catch(console.error);
    }

    function applyVisibleWindow(range) {
      if (!range || !Number.isFinite(range.min) || !Number.isFinite(range.max) || range.max <= range.min) return;
      zoomWindow = range;
      chartList().forEach(chart => {
        chart.options.scales.x.min = zoomWindow.min;
        chart.options.scales.x.max = zoomWindow.max;
        chart.update('none');
      });
      renderStateStrip();
      updatePanControl();
    }

    function panVisibleWindowByPixels(chart, startRange, deltaX) {
      const full = fullDataRange();
      const area = chart?.chartArea;
      if (!full || !area || !startRange || startRange.max <= startRange.min) return false;
      const width = startRange.max - startRange.min;
      const panSpan = full.max - full.min;
      const pixelWidth = Math.max(1, area.right - area.left);
      if (width >= panSpan - 1000) return false;
      const shiftMs = -(deltaX / pixelWidth) * width;
      const min = Math.max(full.min, Math.min(startRange.min + shiftMs, full.max - width));
      applyVisibleWindow({ min, max: min + width });
      return true;
    }

    function attachMobileDragPan(chart) {
      if (!chart?.canvas || chart.$mobileDragPanAttached) return;
      const state = { tracking: false, active: false, startX: 0, startY: 0, range: null };
      const start = (x, y) => {
        if (!isMobileViewport()) return;
        const range = visibleRange();
        if (!range) return;
        state.tracking = true;
        state.active = false;
        state.startX = x;
        state.startY = y;
        state.range = { ...range };
      };
      const move = (x, y, event) => {
        if (!state.tracking || !isMobileViewport()) return;
        const dx = x - state.startX;
        const dy = y - state.startY;
        if (!state.active) {
          if (Math.abs(dy) > 12 && Math.abs(dy) > Math.abs(dx) * 1.2) {
            state.tracking = false;
            return;
          }
          if (Math.abs(dx) < 8 || Math.abs(dx) < Math.abs(dy) * 1.1) return;
          state.active = true;
        }
        if (panVisibleWindowByPixels(chart, state.range, dx)) event?.preventDefault?.();
      };
      const stop = () => {
        state.tracking = false;
        state.active = false;
        state.range = null;
        loadOlderHistoryIfNeeded().catch(console.error);
      };
      chart.canvas.addEventListener('touchstart', event => {
        if (event.touches?.length === 1) start(event.touches[0].clientX, event.touches[0].clientY);
      }, { passive: true });
      chart.canvas.addEventListener('touchmove', event => {
        if (event.touches?.length === 1) move(event.touches[0].clientX, event.touches[0].clientY, event);
      }, { passive: false });
      chart.canvas.addEventListener('touchend', stop, { passive: true });
      chart.canvas.addEventListener('touchcancel', stop, { passive: true });
      chart.canvas.addEventListener('pointerdown', event => {
        if (event.pointerType === 'touch') start(event.clientX, event.clientY);
      });
      chart.canvas.addEventListener('pointermove', event => {
        if (event.pointerType === 'touch') move(event.clientX, event.clientY, event);
      });
      chart.canvas.addEventListener('pointerup', stop);
      chart.canvas.addEventListener('pointercancel', stop);
      chart.$mobileDragPanAttached = true;
    }

    function upsertChart(existing, canvasId, config) {
      if (!existing) {
        const chart = new Chart(el(canvasId), config);
        attachMobileDragPan(chart);
        return chart;
      }
      const visibility = new Map();
      existing.data.datasets.forEach((dataset, index) => {
        const key = dataset.id || dataset.label;
        if (!key) return;
        const metaHidden = existing.getDatasetMeta(index)?.hidden;
        visibility.set(key, typeof metaHidden === 'boolean' ? metaHidden : !!dataset.hidden);
      });
      config.data.datasets.forEach(dataset => {
        const key = dataset.id || dataset.label;
        if (!key || !visibility.has(key)) return;
        dataset.hidden = visibility.get(key);
      });
      existing.data = config.data;
      existing.options.plugins = config.options.plugins;
      existing.options.scales = config.options.scales;
      existing.update();
      attachMobileDragPan(existing);
      return existing;
    }

    function chartLayoutMode() {
      return el('chartLayout')?.value === 'split' ? 'split' : 'combined';
    }

    // Same zone language as the Today page, off the same configured tiers:
    // amber under the warning bound, red under the critical bound, grey where
    // the sock reported nothing (zeroed no-signal readings).
    const o2ZoneColor = value => value <= 0 ? SERIES.prior
      : value < window.owletO2(dashboardPreferences()).critical ? SERIES.bad
      : value < window.owletO2(dashboardPreferences()).warn ? SERIES.warn
      : SERIES.o2;
    function readingLineDataset(id, label, key, color, axis, hiddenDefault) {
      const dataset = { id, label, data: readingSeries(key), borderColor: color, backgroundColor: `${color}20`, yAxisID: axis, hidden: preferredDatasetHidden(id, hiddenDefault), spanGaps: false, pointRadius: 0, tension: .25 };
      if (key === 'oxygen_saturation') {
        dataset.segment = { borderColor: ctx => o2ZoneColor(Math.min(ctx.p0.parsed.y, ctx.p1.parsed.y)) };
      }
      return dataset;
    }

    function notificationsDataset() {
      return { id: 'notifications', type: 'scatter', label: 'Notifications', data: notificationPoints(), yAxisID: 'spo2', hidden: preferredDatasetHidden('notifications', false), pointStyle: 'triangle', pointRadius: 9, pointHoverRadius: 13, hitRadius: 24, showLine: false, borderWidth: 2, borderColor: SERIES.warn, backgroundColor: seriesAlpha(SERIES.warn, '33') };
    }

    function renderCombinedChart() {
      const skinTempHidden = vitalsChart?.data.datasets.find(dataset => dataset.id === 'skinTemperature')?.hidden ?? preferredDatasetHidden('skinTemperature', true);
      const datasets = [
        readingLineDataset('heartRate', 'Heart rate', 'heart_rate', SERIES.hr, 'hr', false),
        readingLineDataset('oxygen', 'SpO₂', 'oxygen_saturation', SERIES.o2, 'spo2', false),
        { ...readingLineDataset('movement', 'Movement', 'movement', SERIES.move, 'move', false), tension: .2 },
        { ...readingLineDataset('skinTemperature', 'Skin temp °C', 'skin_temperature', SERIES.temp, 'temp', true), hidden: skinTempHidden }
      ];
      datasets.push(notificationsDataset());
      vitalsChart = upsertChart(vitalsChart, 'vitalsChart', {
        type: 'line',
        data: { datasets },
        options: chartOptions({
          hr: { type: 'linear', position: 'left', min: 0, title: { display: true, text: 'BPM' } },
          spo2: { type: 'linear', position: 'right', min: 0, suggestedMax: 100, grid: { drawOnChartArea: false }, title: { display: true, text: 'SpO₂' } },
          temp: { type: 'linear', position: 'right', display: false, suggestedMin: 28, suggestedMax: 38, grid: { drawOnChartArea: false } },
          move: { min: 0, display: false }
        }, { hideXTicks: true, legend: { position: 'top', align: 'end' } })
      });
      attachNotificationHover(vitalsChart);
    }

    function renderSplitPanels() {
      const skinTempHidden = hrPanel?.data.datasets.find(dataset => dataset.id === 'skinTemperature')?.hidden ?? preferredDatasetHidden('skinTemperature', true);
      hrPanel = upsertChart(hrPanel, 'hrPanelChart', {
        type: 'line',
        data: { datasets: [
          readingLineDataset('heartRate', 'Heart rate', 'heart_rate', SERIES.hr, 'hr', false),
          { ...readingLineDataset('skinTemperature', 'Skin temp °C', 'skin_temperature', SERIES.temp, 'temp', true), hidden: skinTempHidden }
        ] },
        options: chartOptions({
          hr: { type: 'linear', position: 'left', min: 0, title: { display: true, text: 'BPM' } },
          temp: { type: 'linear', position: 'right', display: false, suggestedMin: 28, suggestedMax: 38, grid: { drawOnChartArea: false } }
        }, { hideXTicks: true, legend: { position: 'top', align: 'end' } })
      });
      spo2Panel = upsertChart(spo2Panel, 'spo2PanelChart', {
        type: 'line',
        data: { datasets: [
          readingLineDataset('oxygen', 'SpO₂', 'oxygen_saturation', SERIES.o2, 'spo2', false),
          notificationsDataset()
        ] },
        options: chartOptions({
          spo2: { type: 'linear', position: 'left', min: 0, suggestedMax: 100, title: { display: true, text: 'SpO₂' } }
        }, { hideXTicks: true, legend: { position: 'top', align: 'end' } })
      });
      movePanel = upsertChart(movePanel, 'movePanelChart', {
        type: 'line',
        data: { datasets: [
          { ...readingLineDataset('movement', 'Movement', 'movement', SERIES.move, 'move', false), tension: .2 }
        ] },
        options: chartOptions({
          move: { type: 'linear', position: 'left', min: 0, title: { display: true, text: 'Move' } }
        }, { legend: { position: 'top', align: 'end' } })
      });
      attachNotificationHover(spo2Panel);
    }

    function renderCharts({ deferTrend = false } = {}) {
      const split = chartLayoutMode() === 'split';
      el('combinedFrame')?.classList.toggle('hidden', split);
      ['hrFrame', 'spo2Frame', 'moveFrame'].forEach(id => el(id)?.classList.toggle('hidden', !split));
      if (split) {
        if (vitalsChart) { vitalsChart.destroy(); vitalsChart = null; }
        renderSplitPanels();
      } else {
        [hrPanel, spo2Panel, movePanel].forEach(chart => chart?.destroy());
        hrPanel = spo2Panel = movePanel = null;
        renderCombinedChart();
      }
      if (deferTrend) {
        renderOxygenTrendPlaceholder();
        scheduleOxygenTrendChart();
      } else {
        renderOxygenTrendChart();
      }
      renderStateStrip();
      updatePanControl();
    }

    function renderOxygenTrendPlaceholder() {
      if (oxygenTrendChart) return;
      oxygenTrendChart = upsertChart(oxygenTrendChart, 'oxygenTrendChart', {
        type: 'line',
        data: { datasets: [] },
        options: chartOptions({
          oxygen: { type: 'linear', position: 'left', suggestedMin: 88, suggestedMax: 100, title: { display: true, text: 'O₂ avg (%)' } },
          signal: { type: 'linear', position: 'right', grid: { drawOnChartArea: false }, title: { display: true, text: '30m − 4h signal' } }
        }, { legend: { display: false }, keepAxisTitles: true })
      });
    }

    function scheduleOxygenTrendChart() {
      const token = ++trendRenderToken;
      const render = () => {
        if (token !== trendRenderToken) return;
        renderOxygenTrendChart();
      };
      if ('requestIdleCallback' in window) {
        window.requestIdleCallback(render, { timeout: 1400 });
      } else {
        setTimeout(render, 60);
      }
    }

    function dataRange(rows = readings) {
      const times = rows.map(row => Date.parse(row.recorded_at)).filter(Number.isFinite);
      return times.length ? { min: Math.min(...times), max: Math.max(...times) } : null;
    }

    function defaultVisibleRange() {
      const full = dataRange();
      if (!full) return null;
      const width = selectedWindowMs();
      if (!width) return full;
      const floor = full.max - width;
      const visible = dataRange(readings.filter(row => {
        const time = Date.parse(row.recorded_at);
        return Number.isFinite(time) && time >= floor && time <= full.max;
      }));
      if (visible && visible.max > visible.min) return visible;
      return { min: Math.max(full.min, floor), max: full.max };
    }

    function visibleRange() {
      if (zoomWindow?.max > zoomWindow?.min) return zoomWindow;
      return defaultVisibleRange();
    }

    function visibleWindowAtLoadedEnd() {
      const full = dataRange();
      if (!zoomWindow || !full) return true;
      return Math.abs(zoomWindow.max - full.max) < 5 * 60 * 1000;
    }

    function visibleWindowSnapshot({ resetZoom = false } = {}) {
      const full = fullDataRange();
      const visible = visibleRange();
      if (resetZoom || !full || !visible || visible.max <= visible.min) {
        return { resetZoom: true, atLatest: true, width: selectedWindowMs() };
      }
      const width = visible.max - visible.min;
      const slider = el('timePan');
      const sliderAtEnd = slider && !slider.disabled && Number(slider.value) >= 995;
      const distanceFromEnd = Math.max(0, full.max - visible.max);
      const atLatest = sliderAtEnd || distanceFromEnd <= Math.max(60 * 1000, width * 0.01);
      return { min: visible.min, max: visible.max, width, distanceFromEnd, atLatest, resetZoom: false };
    }

    function refreshedVisibleRange(snapshot) {
      const full = dataRange();
      if (!full) return null;
      if (snapshot?.resetZoom || !snapshot || !snapshot.width) return defaultVisibleRange();
      const width = Math.min(snapshot.width, full.max - full.min || snapshot.width);
      if (snapshot.atLatest) {
        return { min: Math.max(full.min, full.max - width), max: full.max };
      }
      const min = Math.max(full.min, Math.min(snapshot.min, full.max - width));
      return { min, max: Math.min(full.max, min + width) };
    }

    function fullDataRange() {
      return dataRange();
    }

    async function loadOlderHistoryIfNeeded() {
      if (SHARE_MODE || loadingOlderHistory || selectedHours() === null) return;
      const slider = el('timePan');
      if (slider.disabled || Number(slider.value) > 20) return;
      const currentHours = loadedHours || historyHoursForSelection();
      const incrementHours = Math.max(OLDER_HISTORY_CHUNK_HOURS, selectedHours() || OLDER_HISTORY_CHUNK_HOURS);
      const nextHours = Math.min(24 * 365, currentHours + incrementHours);
      if (!loadedHours || nextHours <= loadedHours) return;
      loadingOlderHistory = true;
      const previousTitle = slider.title;
      slider.title = `Loading ${nextHours}h of history…`;
      loadedHours = nextHours;
      await safeRefresh();
      slider.title = previousTitle || 'Scroll left for older loaded history; release near the left edge to load more.';
      loadingOlderHistory = false;
    }

    function visibleRows() {
      const range = visibleRange();
      if (!range) return readings;
      return readings.filter(row => {
        const time = Date.parse(row.recorded_at);
        return Number.isFinite(time) && time >= range.min && time <= range.max;
      });
    }

    function updatePrimaryChartAlignment() {
      const chart = alignmentChart();
      if (!chart?.chartArea) return;
      const { left, right } = chart.chartArea;
      const width = chart.width || 0;
      el('stateStripWrap').style.setProperty('--chart-left-pad', `${Math.max(0, left)}px`);
      el('stateStripWrap').style.setProperty('--chart-right-pad', `${Math.max(0, width - right)}px`);
    }

    function updatePanControl() {
      const slider = el('timePan');
      const full = fullDataRange();
      const visible = visibleRange();
      if (!full || !visible || full.max <= full.min) {
        slider.disabled = true;
        el('panStartLabel').textContent = '—';
        el('panEndLabel').textContent = '—';
        return;
      }
      const width = visible.max - visible.min;
      const span = full.max - full.min;
      slider.disabled = width >= span - 1000;
      slider.value = slider.disabled ? 0 : Math.round(((visible.min - full.min) / Math.max(1, span - width)) * 1000);
      slider.title = selectedHours() === null ? 'Showing all loaded history.' : 'Scroll left for older loaded history; release near the left edge to load more.';
      el('panStartLabel').textContent = timeTick(visible.min);
      el('panEndLabel').textContent = timeTick(visible.max);
    }

    function panToSliderValue(value) {
      const full = fullDataRange();
      const visible = visibleRange();
      if (!full || !visible) return;
      const width = visible.max - visible.min;
      const maxStart = full.max - width;
      if (maxStart <= full.min) return;
      const start = full.min + (Number(value) / 1000) * (maxStart - full.min);
      applyVisibleWindow({ min: start, max: start + width });
    }

    function stateClass(row) {
      if (isOffline(row)) return 'offline';
      const state = String(row.sleep_state ?? '');
      if (state === '8') return 'light';
      if (state === '15') return 'deep';
      if (state === '1') return 'awake';
      return 'inactive';
    }

    function bucketDurationMs(bucket = rollupBucket()) {
      return ({ '5m': 5, '15m': 15, '30m': 30, hour: 60, '6h': 360, '12h': 720, day: 1440 }[bucket] || 60) * 60 * 1000;
    }

    function pushMergedInterval(intervals, interval) {
      if (!interval || interval.cls === 'inactive' || interval.cls === 'offline') return;
      const last = intervals[intervals.length - 1];
      if (last && last.cls === interval.cls && Math.abs(last.end - interval.start) < 1000) {
        last.end = Math.max(last.end, interval.end);
        return;
      }
      intervals.push(interval);
    }

    function rawStateIntervals(range = visibleRange(), { includeInactive = true } = {}) {
      if (!range || range.max <= range.min || !readings.length) return [];
      const segments = [];
      readings.forEach((row, index) => {
        const start = Date.parse(row.recorded_at);
        const next = readings[index + 1] ? Date.parse(readings[index + 1].recorded_at) : start + 60 * 1000;
        const left = Math.max(start, range.min);
        const right = Math.min(next, range.max);
        if (!Number.isFinite(left) || !Number.isFinite(right) || right <= left) return;
        const cls = stateClass(row);
        if (!includeInactive && (cls === 'inactive' || cls === 'offline')) return;
        const label = `${isOffline(row) ? 'offline / sock off' : stateLabel(row.sleep_state)} · ${localTime(row.recorded_at)}`;
        const previous = segments[segments.length - 1];
        if (previous && previous.cls === cls && Math.abs(previous.end - left) < 1000) {
          previous.end = right;
          return;
        }
        segments.push({ cls, start: left, end: right, label });
      });
      return segments;
    }

    function stateIntervalAt(timestamp) {
      if (!Number.isFinite(timestamp)) return null;
      const candidates = stateStripSegments.length ? stateStripSegments : rawStateIntervals(visibleRange());
      const segment = candidates.find(item => timestamp >= item.start && timestamp <= item.end);
      return segment ? { start: segment.start, end: segment.end, cls: segment.cls, label: segment.label } : null;
    }

    function stateTooltipText(interval, prefix = '') {
      if (!interval) return null;
      const stage = sleepStageInfo(interval.cls || interval.label);
      const duration = durationText(Math.max(0, (interval.end - interval.start) / 1000));
      return {
        title: `${prefix}${stage.name}`,
        body: `${stage.description} ${localTime(new Date(interval.start).toISOString(), true)} → ${localTime(new Date(interval.end).toISOString(), true)} · ${duration}.`
      };
    }

    function showStateTooltip(interval, event, prefix = '') {
      const tooltip = el('stateTooltip');
      const text = stateTooltipText(interval, prefix);
      if (!text || !event) {
        tooltip.classList.add('hidden');
        return;
      }
      const source = event.touches?.[0] || event.changedTouches?.[0] || event;
      tooltip.innerHTML = `<b>${text.title}</b><span>${text.body}</span>`;
      tooltip.classList.remove('hidden');
      const pad = 12;
      const rect = tooltip.getBoundingClientRect();
      const left = Math.min(window.innerWidth - rect.width - pad, Math.max(pad, source.clientX + 12));
      const top = Math.min(window.innerHeight - rect.height - pad, Math.max(pad, source.clientY + 12));
      tooltip.style.left = `${left}px`;
      tooltip.style.top = `${top}px`;
    }

    function hideStateTooltip() {
      el('stateTooltip').classList.add('hidden');
    }

    function ballparkClass(row) {
      const sleepSeconds = (row.light_sleep_seconds || 0) + (row.deep_sleep_seconds || 0);
      const awakeSeconds = row.awake_seconds || 0;
      const movementSeconds = row.movement_seconds || 0;
      const total = row.duration_seconds || sleepSeconds + awakeSeconds;
      if (total <= 0) return null;
      const awakeLikeSeconds = Math.min(total, awakeSeconds + movementSeconds);
      const movementBurst = movementSeconds >= Math.max(90, total * 0.14);
      if (awakeLikeSeconds >= total * 0.28 || (movementBurst && (row.max_movement || 0) >= (row.movement_awake_threshold || 10))) return 'awake';
      if (awakeSeconds >= total * 0.45) return 'awake';
      if (sleepSeconds >= total * 0.66) return (row.deep_sleep_seconds || 0) > (row.light_sleep_seconds || 0) ? 'deep' : 'light';
      return 'awake';
    }

    function smoothBallparkIntervals(items, bucketMs = bucketDurationMs()) {
      const sorted = items.filter(Boolean).sort((a, b) => a.start - b.start).map(item => ({ ...item }));
      sorted.forEach((item, index) => {
        if (item.cls === 'awake') return;
        const duration = item.end - item.start;
        const prev = sorted[index - 1];
        const next = sorted[index + 1];
        const bridgedByWake = prev?.cls === 'awake' && next?.cls === 'awake' && item.start - prev.end <= bucketMs && next.start - item.end <= bucketMs;
        if (bridgedByWake && duration <= Math.max(bucketMs * 1.75, 45 * 60 * 1000)) item.cls = 'awake';
      });
      return sorted.reduce((merged, item) => {
        const last = merged[merged.length - 1];
        if (last && last.cls === item.cls && item.start - last.end <= Math.max(1000, bucketMs * 0.25)) {
          last.end = Math.max(last.end, item.end);
          return merged;
        }
        merged.push(item);
        return merged;
      }, []);
    }

    function subtractIntervals(interval, blockers) {
      let pieces = [interval];
      blockers.forEach(blocker => {
        pieces = pieces.flatMap(piece => {
          if (blocker.end <= piece.start || blocker.start >= piece.end) return [piece];
          const split = [];
          if (blocker.start > piece.start) split.push({ ...piece, end: Math.min(blocker.start, piece.end) });
          if (blocker.end < piece.end) split.push({ ...piece, start: Math.max(blocker.end, piece.start) });
          return split;
        });
      });
      return pieces.filter(piece => piece.end > piece.start);
    }

    function rollupIntervals(range = visibleRange()) {
      if (!range || !rollups.length) return [];
      const bucketMs = bucketDurationMs();
      const blockers = offlineIntervals();
      const classified = rollups.map((row, index) => {
        const start = Date.parse(row.bucket_start);
        const nextStart = rollups[index + 1] ? Date.parse(rollups[index + 1].bucket_start) : start + bucketMs;
        const end = Number.isFinite(nextStart) && nextStart > start ? nextStart : start + bucketMs;
        const cls = ballparkClass(row);
        if (!Number.isFinite(start) || !Number.isFinite(end) || !cls) return null;
        return { start: Math.max(start, range.min), end: Math.min(end, range.max), cls };
      }).filter(item => item && item.end > item.start);
      return smoothBallparkIntervals(classified, bucketMs)
        .flatMap(item => subtractIntervals(item, blockers))
        .filter(item => item && item.end > item.start);
    }

    function rollupIntervalAt(timestamp) {
      if (!Number.isFinite(timestamp)) return null;
      const interval = rollupIntervals(visibleRange()).find(item => timestamp >= item.start && timestamp <= item.end);
      return interval ? { start: interval.start, end: interval.end, cls: interval.cls } : null;
    }

    function sleepOverlayIntervals() {
      const intervals = [];
      if (sleepBallparkEnabled) return rollupIntervals(visibleRange());
      rawStateIntervals(visibleRange(), { includeInactive: false }).forEach(interval => pushMergedInterval(intervals, interval));
      return intervals;
    }

    function setStateStripHoverFromEvent(event) {
      const range = visibleRange();
      if (!range || range.max <= range.min) return;
      const source = event.touches?.[0] || event.changedTouches?.[0] || event;
      const rect = el('stateStrip').getBoundingClientRect();
      const ratio = Math.min(1, Math.max(0, (source.clientX - rect.left) / Math.max(1, rect.width)));
      const timestamp = range.min + ratio * (range.max - range.min);
      hoveredStateInterval = stateIntervalAt(timestamp);
      showStateTooltip(hoveredStateInterval, event);
      refreshPrimaryCharts();
    }

    function attachStateStripHover() {
      const strip = el('stateStrip');
      if (strip.dataset.hoverAttached === 'true') return;
      strip.addEventListener('mousemove', setStateStripHoverFromEvent);
      strip.addEventListener('click', setStateStripHoverFromEvent);
      strip.addEventListener('touchstart', setStateStripHoverFromEvent, { passive: true });
      strip.addEventListener('touchmove', setStateStripHoverFromEvent, { passive: true });
      strip.addEventListener('mouseleave', () => { hoveredStateInterval = null; hideStateTooltip(); refreshPrimaryCharts(); });
      strip.dataset.hoverAttached = 'true';
    }

    function setStateChartHoverFromEvent(event) {
      if (!stateChart?.scales?.x) return;
      const source = event.touches?.[0] || event.changedTouches?.[0] || event;
      const rect = stateChart.canvas.getBoundingClientRect();
      const timestamp = stateChart.scales.x.getValueForPixel(source.clientX - rect.left);
      hoveredStateInterval = rollupIntervalAt(timestamp) || stateIntervalAt(timestamp);
      showStateTooltip(hoveredStateInterval, event, rollupIntervalAt(timestamp) ? 'Ballpark ' : '');
      refreshPrimaryCharts();
    }

    function attachStateChartHover(chart) {
      if (!chart || chart.$stateHoverAttached) return;
      chart.canvas.addEventListener('mousemove', setStateChartHoverFromEvent);
      chart.canvas.addEventListener('click', setStateChartHoverFromEvent);
      chart.canvas.addEventListener('touchstart', setStateChartHoverFromEvent, { passive: true });
      chart.canvas.addEventListener('touchmove', setStateChartHoverFromEvent, { passive: true });
      chart.canvas.addEventListener('mouseleave', () => { hoveredStateInterval = null; hideStateTooltip(); refreshPrimaryCharts(); });
      chart.$stateHoverAttached = true;
    }

    function renderStateStrip() {
      const range = visibleRange();
      updatePrimaryChartAlignment();
      attachStateStripHover();
      if (!range || range.max <= range.min || !readings.length) {
        stateStripSegments = [];
        el('stateStrip').style.background = themeToken('--nodata');
        el('stateStrip').innerHTML = '';
        el('stateTimeAxis').innerHTML = '';
        return;
      }
      stateStripSegments = rawStateIntervals(range);
      const dayMarkers = dayBoundaryTimes(range).map(time => {
        const position = ((time - range.min) / (range.max - range.min)) * 100;
        return `<span class="state-day-boundary" style="left:${position}%"></span>`;
      }).join('');
      el('stateStrip').innerHTML = dayMarkers;
      const stops = stateStripSegments.flatMap(segment => {
        const left = ((segment.start - range.min) / (range.max - range.min)) * 100;
        const right = ((segment.end - range.min) / (range.max - range.min)) * 100;
        const color = stateStripColors[segment.cls] || stateStripColors.inactive;
        return [`${color} ${Math.max(0, left).toFixed(3)}%`, `${color} ${Math.min(100, right).toFixed(3)}%`];
      });
      el('stateStrip').style.background = stops.length ? `linear-gradient(to right, ${stops.join(', ')})` : themeToken('--nodata');
      const ticks = timeTickValues(range).map(value => {
        const position = ((value - range.min) / (range.max - range.min)) * 100;
        return `<span style="left:${position}%">${timeTick(value)}</span>`;
      }).join('');
      el('stateTimeAxis').innerHTML = ticks;
    }

    function renderOxygenTrendChart() {
      const shortMinutes = smoothingMinutes() || 30;
      const longMinutes = Math.max(240, shortMinutes * 8);
      const shortLabel = `${shortMinutes}m O₂ avg`;
      const signalLabel = `${shortMinutes}m − ${Math.round(longMinutes / 60)}h signal`;
      const trendRows = trendSourceRows();
      const shortAvg = rollingOxygenAverage(shortMinutes, trendRows);
      const longAvg = rollingOxygenAverage(longMinutes, trendRows);
      const signal = oxygenTrendSignal(shortAvg, longAvg);
      oxygenTrendChart = upsertChart(oxygenTrendChart, 'oxygenTrendChart', {
        type: 'line',
        data: {
          datasets: [
            { id: 'o2Trailing30', label: shortLabel, data: shortAvg, borderColor: SERIES.o2, backgroundColor: seriesAlpha(SERIES.o2, '20'), yAxisID: 'oxygen', hidden: preferredDatasetHidden('o2Trailing30', false), tension: .25, pointRadius: 0, spanGaps: false },
            { id: 'o2Baseline4h', label: `Baseline ${Math.round(longMinutes / 60)}h O₂ avg`, data: longAvg, borderColor: SERIES.prior, backgroundColor: seriesAlpha(SERIES.prior, '20'), yAxisID: 'oxygen', hidden: preferredDatasetHidden('o2Baseline4h', false), tension: .25, pointRadius: 0, borderDash: [6, 4], spanGaps: false },
            { id: 'o2TrendSignal', type: 'bar', label: signalLabel, data: signal, yAxisID: 'signal', hidden: preferredDatasetHidden('o2TrendSignal', false), backgroundColor: ctx => (ctx.raw?.y ?? 0) >= 0 ? seriesAlpha(SERIES.good, 'f2') : seriesAlpha(SERIES.bad, 'f2'), borderColor: ctx => (ctx.raw?.y ?? 0) >= 0 ? SERIES.good : SERIES.bad, borderWidth: 1, barThickness: isMobileViewport() ? 3 : 4, maxBarThickness: 8, minBarLength: 2 }
          ]
        },
        options: chartOptions({
          oxygen: { type: 'linear', position: 'left', suggestedMin: 88, suggestedMax: 100, title: { display: true, text: 'O₂ avg (%)' } },
          signal: { type: 'linear', position: 'right', grid: { drawOnChartArea: false }, title: { display: true, text: `${shortMinutes}m signal` }, ticks: { callback: value => `${value > 0 ? '+' : ''}${value}` } }
        }, { legend: { display: false }, keepAxisTitles: true })
      });
    }

    function renderRollups() {
      const rows = rollups.slice().reverse().map((row, index) => `<tr class="${index === 0 ? 'newest-rollup' : ''}"><td>${rollupLabel(row)}</td><td>${row.samples}/${row.total_samples ?? row.samples}</td><td>${fmt(row.avg_oxygen_saturation, '%')}</td><td>${fmt(row.min_oxygen_saturation, '%')}</td><td>${fmt(row.avg_heart_rate, ' bpm')}</td><td>${fmt(row.avg_skin_temperature, '°C')}</td><td>${hours(row.sleep_seconds)}</td><td>${hours(row.awake_seconds)}</td><td>${row.offline_samples || 0}</td></tr>`).join('');
      el('rollupTable').innerHTML = `<thead><tr><th>Window</th><th>Valid/total</th><th>Avg O₂</th><th>Min O₂</th><th>Avg HR</th><th>Avg skin temp</th><th>Sleep</th><th>Awake</th><th>Offline</th></tr></thead><tbody>${rows || '<tr><td colspan="9" class="empty">No readings yet.</td></tr>'}</tbody>`;
    }

    function applyFilter() {
      filtered = readings;
      renderReadingsTable();
    }

    function renderReadingsTable() {
      const first = filtered[0]?.recorded_at || '';
      const last = filtered[filtered.length - 1]?.recorded_at || '';
      const challengeSignature = (challenges.items || []).map(item => `${item.id}:${item.start_time}:${item.effective_end_time || item.end_time || ''}`).join('|');
      const tableStart = Math.max(0, filtered.length - TABLE_ROW_LIMIT);
      const tableRows = filtered.slice(tableStart);
      const nextSignature = `${filtered.length}:${first}:${last}:${tableStart}:${challengeSignature}`;
      el('tableCount').textContent = filtered.length > tableRows.length ? `newest ${tableRows.length} of ${filtered.length} loaded` : `${filtered.length} loaded`;
      if (nextSignature === readingsTableSignature) return;
      readingsTableSignature = nextSignature;
      const indexedRows = tableRows.map((row, index) => ({ row, index: filtered === readings ? tableStart + index : readings.indexOf(row) })).reverse();
      const rows = indexedRows.map(({ row, index }, displayIndex) => `
        <tr data-index="${index}" class="${displayIndex === 0 ? 'latest-row' : ''} ${isOffline(row) ? 'offline-row' : ''} ${isInChallenge(row) ? 'challenge-row' : ''}">
          <td>${localTime(row.recorded_at)}</td>
          <td>${fmt(row.device_serial)}</td>
          <td>${num(row.heart_rate)}</td>
          <td>${num(row.oxygen_saturation)}%</td>
          <td>${num(row.movement)}</td>
          <td>${isOffline(row) ? 'offline / sock off' : stateLabel(row.sleep_state)}</td>
          <td>${fmt(row.battery, '%')}</td>
          <td>${fmt(row.skin_temperature, '°C')}</td>
        </tr>`).join('');
      el('readingsTable').innerHTML = `<thead><tr><th>Time</th><th>Serial</th><th>HR</th><th>O₂</th><th>Move</th><th>State</th><th>Battery</th><th>Skin temp</th></tr></thead><tbody>${rows || '<tr><td colspan="8" class="empty">No readings yet.</td></tr>'}</tbody>`;
    }

    function attachReadingsTableSelection() {
      const table = el('readingsTable');
      if (table.dataset.selectionAttached === 'true') return;
      table.addEventListener('click', event => {
        const tr = event.target.closest('tbody tr[data-index]');
        if (!tr || !table.contains(tr)) return;
        table.querySelectorAll('tr.selected-row').forEach(row => row.classList.remove('selected-row'));
        tr.classList.add('selected-row');
        const row = readings[Number(tr.dataset.index)];
        el('raw').textContent = JSON.stringify(row, null, 2);
      });
      table.dataset.selectionAttached = 'true';
    }

    function renderChallenges() {
      const items = challenges.items || [];
      const total = challenges.total ?? items.length;
      const active = items.find(item => item.active);
      el('challengeCount').textContent = total;
      el('challengePage').textContent = total ? `${total} total${active ? ' · active now' : ''}` : 'none yet';
      el('challengeActions').style.display = SHARE_MODE ? 'none' : '';
      el('endChallenge').disabled = !active;
      const emptyChallengeMarkup = SHARE_MODE
        ? '<div class="empty">No oxygen challenges in this range.</div>'
        : `<div class="empty"><b>No oxygen challenges in this range yet.</b><br />Add one from this popup, or zoom the chart first and use the visible window.
            <div class="challenge-empty-actions">
              <button class="primary" type="button" data-add-challenge-empty>Add new O₂ challenge</button>
              <button type="button" data-visible-challenge-empty>Use visible chart window</button>
            </div>
          </div>`;
      el('challengeList').innerHTML = items.map(item => {
        const summary = item.summary || {};
        const comparison = item.comparison || {};
        return `<article class="challenge-item ${item.active ? 'active' : ''}">
          <div class="challenge-title"><span>${item.active ? '🟢 ' : ''}${item.label || 'Oxygen challenge'}</span><button type="button" data-challenge-id="${item.id}">Open</button></div>
          <div class="notification-meta">${localTime(item.start_time)} → ${item.active ? 'active' : localTime(item.effective_end_time)} · ${durationText(summary.duration_seconds)}</div>
          <div class="challenge-metrics">
            <span>Avg O₂ <b>${fmt(summary.avg_oxygen_saturation, '%')}</b></span>
            <span>Min O₂ <b>${fmt(summary.min_oxygen_saturation, '%')}</b></span>
            <span>Avg HR <b>${fmt(summary.avg_heart_rate, ' bpm')}</b></span>
            <span>Low O₂ <b>${summary.low_oxygen_samples || 0}</b> (${signed(comparison.low_oxygen_delta)})</span>
          </div>
        </article>`;
      }).join('') || emptyChallengeMarkup;
      [...el('challengeList').querySelectorAll('button[data-challenge-id]')].forEach(button => {
        button.addEventListener('click', () => openChallengeDetail(Number(button.dataset.challengeId)));
      });
      el('challengeList').querySelector('[data-add-challenge-empty]')?.addEventListener('click', () => openNewChallengeModal());
      el('challengeList').querySelector('[data-visible-challenge-empty]')?.addEventListener('click', markVisibleChallenge);
    }

    async function saveChallenge(payload) {
      const account = selectedAccount();
      const scopedPayload = account ? { ...payload, account_id: Number(account) } : payload;
      const response = await fetch(`${API_BASE}/api/oxygen-challenges`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(scopedPayload)
      });
      if (!response.ok) throw new Error(`Could not save challenge: ${response.status}`);
      await refresh();
    }

    async function startChallenge() {
      await saveChallenge({ start_time: new Date().toISOString(), label: 'Oxygen challenge', notes: 'Started from dashboard' });
    }

    async function endActiveChallenge() {
      const active = (challenges.items || []).find(item => item.active);
      if (!active) return;
      const response = await fetch(`${API_BASE}/api/oxygen-challenges/${active.id}`, {
        method: 'PATCH', credentials: 'include', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ end_time: new Date().toISOString() })
      });
      if (!response.ok) throw new Error(`Could not end challenge: ${response.status}`);
      await refresh();
    }

    async function markVisibleChallenge() {
      const range = visibleRange();
      if (!range || range.max <= range.min) return;
      openNewChallengeModal({ start: new Date(range.min).toISOString(), end: new Date(range.max).toISOString(), notes: 'Marked from visible chart window' });
    }

    function openNewChallengeModal(prefill = {}) {
      currentChallengeDetail = null;
      el('challengesPanel').classList.add('hidden');
      el('challengesToggle').setAttribute('aria-expanded', 'false');
      el('challengeModalTitle').textContent = 'Add O₂ challenge';
      el('challengeModalMeta').textContent = 'Create an off-oxygen window by choosing start and end times.';
      el('challengeSummary').innerHTML = '';
      el('challengeSummary').style.display = 'none';
      el('challengeDetailChartFrame').style.display = 'none';
      el('challengeNotes').textContent = 'Tip: use “Use visible chart window” after zooming to prefill the exact chart interval.';
      el('challengeEditForm').style.display = 'grid';
      el('challengeEditLabel').value = prefill.label || 'Oxygen challenge';
      el('challengeEditStart').value = toDateTimeLocal(prefill.start || new Date().toISOString());
      el('challengeEditEnd').value = prefill.end ? toDateTimeLocal(prefill.end) : '';
      el('challengeEditNotes').value = prefill.notes || '';
      el('deleteChallenge').style.display = 'none';
      el('saveChallengeEdits').textContent = 'Add challenge';
      if (challengeDetailChart) {
        challengeDetailChart.destroy();
        challengeDetailChart = null;
      }
      el('challengeModal').classList.remove('hidden');
    }

    async function openChallengeDetail(id) {
      el('challengesPanel').classList.add('hidden');
      el('challengesToggle').setAttribute('aria-expanded', 'false');
      const detail = await fetchJson(`${API_BASE}/api/oxygen-challenges/${id}`);
      currentChallengeDetail = detail;
      const summary = detail.summary || {};
      const prior = detail.prior_summary || {};
      const comparison = detail.comparison || {};
      el('challengeModalTitle').textContent = detail.label || 'Oxygen challenge';
      el('challengeModalMeta').textContent = `${localTime(detail.start_time)} → ${detail.active ? 'active' : localTime(detail.effective_end_time)} · ${durationText(summary.duration_seconds)} off oxygen`;
      el('challengeNotes').textContent = detail.notes || 'Challenge data is excluded from normal dashboard averages and compared against the same-length window immediately before oxygen came off.';
      el('challengeSummary').style.display = 'grid';
      el('challengeDetailChartFrame').style.display = 'block';
      el('challengeEditForm').style.display = SHARE_MODE ? 'none' : 'grid';
      el('challengeEditLabel').value = detail.label || 'Oxygen challenge';
      el('challengeEditStart').value = toDateTimeLocal(detail.start_time);
      el('challengeEditEnd').value = detail.end_time ? toDateTimeLocal(detail.end_time) : '';
      el('challengeEditNotes').value = detail.notes || '';
      el('deleteChallenge').style.display = SHARE_MODE ? 'none' : '';
      el('saveChallengeEdits').textContent = 'Save edits';
      el('challengeSummary').innerHTML = [
        ['Avg O₂', fmt(summary.avg_oxygen_saturation, '%'), `vs prior ${fmt(prior.avg_oxygen_saturation, '%')} (${signed(comparison.avg_oxygen_delta, ' pts')})`],
        ['Min O₂', fmt(summary.min_oxygen_saturation, '%'), `vs prior ${fmt(prior.min_oxygen_saturation, '%')} (${signed(comparison.min_oxygen_delta, ' pts')})`],
        ['Avg HR', fmt(summary.avg_heart_rate, ' bpm'), `vs prior ${fmt(prior.avg_heart_rate, ' bpm')} (${signed(comparison.avg_heart_rate_delta, ' bpm')})`],
        ['Events', `${summary.low_oxygen_samples || 0} low · ${summary.critical_oxygen_samples || 0} critical`, `sleep ${durationText(summary.sleep_seconds)} · awake ${durationText(summary.awake_seconds)}`]
      ].map(([label, value, foot]) => `<div class="mini"><span class="eyebrow">${label}</span><b>${value}</b><small>${foot}</small></div>`).join('');
      el('challengeModal').classList.remove('hidden');
      renderChallengeDetailChart(detail);
    }

    async function saveChallengeEdits(event) {
      event.preventDefault();
      const payload = {
        label: el('challengeEditLabel').value || 'Oxygen challenge',
        start_time: fromDateTimeLocal(el('challengeEditStart').value),
        notes: el('challengeEditNotes').value || ''
      };
      if (!payload.start_time) {
        alert('Start time is required.');
        return;
      }
      const endTime = fromDateTimeLocal(el('challengeEditEnd').value);
      payload.end_time = endTime;
      const url = currentChallengeDetail
        ? `${API_BASE}/api/oxygen-challenges/${currentChallengeDetail.id}`
        : `${API_BASE}/api/oxygen-challenges`;
      const response = await fetch(url, {
        method: currentChallengeDetail ? 'PATCH' : 'POST',
        credentials: 'include',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(payload)
      });
      if (!response.ok) throw new Error(`Could not save challenge: ${response.status}`);
      const saved = await response.json();
      await refresh();
      await openChallengeDetail(saved.id);
    }

    async function deleteCurrentChallenge() {
      if (!currentChallengeDetail) return;
      if (!window.confirm('Delete this oxygen challenge? Its readings will return to normal stats.')) return;
      const response = await fetch(`${API_BASE}/api/oxygen-challenges/${currentChallengeDetail.id}`, {
        method: 'DELETE',
        credentials: 'include'
      });
      if (!response.ok) throw new Error(`Could not delete challenge: ${response.status}`);
      currentChallengeDetail = null;
      el('challengeModal').classList.add('hidden');
      await refresh();
    }

    function renderChallengeDetailChart(detail) {
      const toPoint = (row, key) => ({ x: Date.parse(row.recorded_at), y: row[key] });
      if (challengeDetailChart) challengeDetailChart.destroy();
      challengeDetailChart = new Chart(el('challengeDetailChart'), {
        type: 'line',
        data: { datasets: [
          { label: 'Challenge O₂', data: (detail.readings || []).map(row => toPoint(row, 'oxygen_saturation')), yAxisID: 'oxygen', borderColor: SERIES.o2, pointRadius: 1, tension: .2 },
          { label: 'Prior O₂', data: (detail.prior_readings || []).map(row => toPoint(row, 'oxygen_saturation')), yAxisID: 'oxygen', borderColor: SERIES.prior, borderDash: [5, 4], pointRadius: 0, tension: .2 },
          { label: 'Challenge HR', data: (detail.readings || []).map(row => toPoint(row, 'heart_rate')), yAxisID: 'hr', borderColor: SERIES.hr, pointRadius: 1, tension: .2 }
        ] },
        options: chartOptions({
          oxygen: { type: 'linear', position: 'left', suggestedMin: 86, suggestedMax: 100, title: { display: true, text: 'O₂' } },
          hr: { type: 'linear', position: 'right', grid: { drawOnChartArea: false }, title: { display: true, text: 'BPM' } }
        })
      });
    }

    function downloadCsv() {
      const columns = ['recorded_at', 'device_serial', 'heart_rate', 'oxygen_saturation', 'movement', 'sleep_state', 'battery', 'skin_temperature'];
      const lines = [columns.join(',')].concat(filtered.map(row => columns.map(col => JSON.stringify(row[col] ?? '')).join(',')));
      const blob = new Blob([lines.join('\n')], { type: 'text/csv' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `owlet-readings-${new Date().toISOString().slice(0, 10)}.csv`;
      a.click();
      URL.revokeObjectURL(url);
    }

    function showNewDataPulse() {
      const chip = el('updateChip');
      chip.classList.add('show');
      document.querySelectorAll('.latest-row, .newest-rollup').forEach(node => {
        node.classList.remove('pulse-new');
        void node.offsetWidth;
        node.classList.add('pulse-new');
      });
      setTimeout(() => chip.classList.remove('show'), 1800);
    }

    async function safeRefresh(options = {}) {
      const { force = false, ...refreshOptions } = options;
      if (refreshInFlight && !force) return refreshInFlight;
      const run = (async () => {
        try {
          await refresh(refreshOptions);
          lastDataAt = Date.now();
          updateTitleDotProgress();
          pingTitleDot();
          if (window.__focusWindow) {
            applyVisibleWindow(window.__focusWindow);
            window.__focusWindow = null;
          }
        } catch (error) {
          console.error(error);
          if (!firstLoadComplete) setInitialLoading('Could not load dashboard data. Retrying soon…', 'error');
          updateRefreshButton('Refresh failed');
          setTimeout(() => updateRefreshButton(), 1500);
        }
      })();
      let tracked;
      tracked = run.finally(() => {
        if (refreshInFlight === tracked) refreshInFlight = null;
      });
      refreshInFlight = tracked;
      return tracked;
    }

    function tickCountdown() {
      secondsUntilRefresh = Math.max(0, secondsUntilRefresh - 1);
      updateRefreshButton();
      updateTitleDotProgress();
      if (secondsUntilRefresh === 0) safeRefresh();
    }

    function updateInstallButton() {
      // The shell owns the install button now; kept as a no-op because the
      // service-worker registration callback still references it.
    }

    function closeProfileMenu() {
      el('profileMenu')?.classList.add('hidden');
      el('profileMenuToggle')?.setAttribute('aria-expanded', 'false');
    }

    function toggleProfileMenu() {
      const menu = el('profileMenu');
      const hidden = menu.classList.toggle('hidden');
      el('profileMenuToggle')?.setAttribute('aria-expanded', String(!hidden));
    }

    function closeO2AddMenu() {
      el('o2AddMenu')?.classList.add('hidden');
      el('o2AddMenuToggle')?.setAttribute('aria-expanded', 'false');
    }

    function toggleO2AddMenu() {
      const menu = el('o2AddMenu');
      const hidden = menu.classList.toggle('hidden');
      el('o2AddMenuToggle').setAttribute('aria-expanded', String(!hidden));
    }

    if (SHARE_MODE) {
      el('o2AddMenu').innerHTML = '<span class="small">Open the full dashboard to add O₂ challenges.</span>';
      el('o2AddMenuToggle').title = 'Open the full dashboard to add O₂ challenges';
    }




    el('accountSelect')?.addEventListener('change', async event => {
      setUrlAccount(event.target.value);
      applyAccountPreferencesToControls();
      vitalsChart?.destroy();
      oxygenTrendChart?.destroy();
      vitalsChart = null;
      oxygenTrendChart = null;
      devices = [];
      renderDeviceOptions('');
      await loadDevices();
      renderBabyName();
      loadedHours = null;
      readingsTableSignature = '';
      safeRefresh({ resetZoom: true, force: true });
    });
    (async function desktopModeNotice() {
      if (SHARE_MODE || sessionStorage.getItem('owletDesktopNoticeDismissed')) return;
      try {
        const health = await fetchJson(`${API_BASE}/api/health`);
        if (!health.desktop_mode) return;
        const banner = document.createElement('div');
        // Styled by .collector-notice above: --warn plus a warning glyph and a
        // bold lead-in, so the caution never rests on colour alone.
        banner.className = 'collector-notice';
        banner.setAttribute('role', 'note');
        banner.innerHTML = '<span class="cn-icon" aria-hidden="true">\u26A0\uFE0E</span>'
          + '<span><b>Desktop mode:</b> readings are only collected while this app is running. '
          + 'Time it spends closed appears as “collector off” gaps in the charts.</span>'
          + '<button type="button" aria-label="Dismiss">✕</button>';
        banner.querySelector('button').addEventListener('click', () => {
          sessionStorage.setItem('owletDesktopNoticeDismissed', '1');
          banner.remove();
        });
        document.querySelector('main')?.prepend(banner);
      } catch (error) { /* best effort */ }
    })();

    el('deviceSelect').addEventListener('change', event => {
      setUrlDevice(event.target.value);
      renderBabyName();
      loadedHours = null;
      readingsTableSignature = '';
      safeRefresh({ resetZoom: true, force: true });
    });
    el('window').addEventListener('change', () => { readingsTableSignature = ''; persistChartSettings({ window: el('window').value }); safeRefresh({ resetZoom: true, force: true }); });
    el('smoothing').addEventListener('change', () => { persistChartSettings({ smoothing: el('smoothing').value }); renderCharts({ deferTrend: true }); safeRefresh({ resetZoom: false }); });
    el('chartLayout').addEventListener('change', () => { persistChartSettings({ layout: el('chartLayout').value }); renderCharts({ deferTrend: true }); });
    el('refresh')?.addEventListener('click', () => safeRefresh());
    el('titleStatusDot')?.addEventListener('click', () => safeRefresh({ force: true }));
    el('titleStatusDot')?.addEventListener('keydown', event => {
      if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); safeRefresh({ force: true }); }
    });
    el('dailyInsightsToggle').addEventListener('click', openDailyInsightsModal);
    el('closeDailyInsights').addEventListener('click', () => el('dailyInsightsModal').classList.add('hidden'));
    el('download').addEventListener('click', downloadCsv);
    el('resetZoom').addEventListener('click', resetZoom);
    el('resetZoomHeader').addEventListener('click', resetZoom);
    el('challengesToggle').addEventListener('click', () => {
      const panel = el('challengesPanel');
      const hidden = panel.classList.toggle('hidden');
      el('challengesToggle').setAttribute('aria-expanded', String(!hidden));
    });
    el('startChallenge').addEventListener('click', startChallenge);
    el('endChallenge').addEventListener('click', endActiveChallenge);
    el('addChallenge').addEventListener('click', () => openNewChallengeModal());
    el('markVisibleChallenge').addEventListener('click', markVisibleChallenge);
    el('o2AddMenuToggle').addEventListener('click', event => {
      event.stopPropagation();
      toggleO2AddMenu();
    });
    el('menuNewChallenge')?.addEventListener('click', () => { closeO2AddMenu(); openNewChallengeModal(); });
    el('menuVisibleChallenge')?.addEventListener('click', () => { closeO2AddMenu(); markVisibleChallenge(); });
    el('closeChallengesPanel').addEventListener('click', () => { el('challengesPanel').classList.add('hidden'); el('challengesToggle').setAttribute('aria-expanded', 'false'); });
    el('closeChallengeModal').addEventListener('click', () => { currentChallengeDetail = null; el('challengeModal').classList.add('hidden'); });
    el('challengeEditForm').addEventListener('submit', saveChallengeEdits);
    el('deleteChallenge').addEventListener('click', deleteCurrentChallenge);
    el('timePan').addEventListener('input', event => panToSliderValue(event.target.value));
    el('timePan').addEventListener('change', () => loadOlderHistoryIfNeeded().catch(console.error));
    el('challengeBandsToggle').addEventListener('change', event => {
      challengeBandsEnabled = event.target.checked;
      persistChartSettings({ challenge_bands: challengeBandsEnabled });
      updateChallengeBandOptions();
    });
    el('sleepHighlightToggle').addEventListener('change', event => {
      sleepHighlightEnabled = event.target.checked;
      el('sleepBallparkToggle').disabled = !sleepHighlightEnabled;
      persistChartSettings({ sleep_highlight: sleepHighlightEnabled });
      refreshPrimaryCharts();
    });
    el('sleepBallparkToggle').addEventListener('change', event => {
      sleepBallparkEnabled = event.target.checked;
      persistChartSettings({ sleep_ballpark: sleepBallparkEnabled });
      refreshPrimaryCharts();
    });
    document.addEventListener('click', event => {
      if (!el('o2AddWrap').contains(event.target)) closeO2AddMenu();
    });
    ['vitalsChart', 'hrPanelChart', 'spo2PanelChart', 'movePanelChart', 'oxygenTrendChart'].forEach(id => {
      el(id)?.addEventListener('dblclick', resetZoom);
    });
    window.addEventListener('resize', () => { renderDeviceOptions(); renderProfileMenu(); updateRefreshButton(); renderCharts({ deferTrend: true }); renderRollups(); updatePanControl(); });
    if ('serviceWorker' in navigator && !SHARE_MODE) {
      window.addEventListener('load', () => navigator.serviceWorker.register('/sw.js').then(updateInstallButton).catch(updateInstallButton));
    }
    (function applyFocusParam() {
      // Deep-link contract: /data?focus=<ISO>&span=<minutes> zooms to that window.
      const params = new URLSearchParams(window.location.search);
      const focus = params.get('focus');
      if (!focus) return;
      const start = Date.parse(focus);
      if (!Number.isFinite(start)) return;
      const spanMinutes = Math.max(10, Number(params.get('span') || 60));
      window.__focusWindow = { min: start, max: start + spanMinutes * 60000 };
      const ageHours = (Date.now() - start) / 3600000;
      window.__focusFit = [6, 12, 24, 72, 168, 720].find(h => h >= ageHours + spanMinutes / 60) || 720;
    })();
    function applyFocusFitToWindowSelect() {
      if (!window.__focusFit) return;
      const select = el('window');
      if (select && (select.value === 'all' || Number(select.value) < window.__focusFit)) {
        select.value = String(window.__focusFit);
      }
    }
    document.addEventListener('owlet:prefs-changed', async () => {
      await loadAccounts();
      renderCharts({ deferTrend: true });
      secondsUntilRefresh = Math.min(secondsUntilRefresh, refreshSeconds());
    });
    document.addEventListener('owlet:account-added', async event => {
      event.preventDefault();
      await loadAccounts();
      const accountId = event.detail?.id ? String(event.detail.id) : selectedAccount();
      if (accountId) setUrlAccount(accountId);
      await loadDevices();
      loadedHours = null;
      readingsTableSignature = '';
      safeRefresh({ resetZoom: true, force: true });
    });
    updateInstallButton();
    attachReadingsTableSelection();
    (async function bootWorkbench() {
      // Resilient boot: transient failures (server warming, brief 500s) retry
      // instead of leaving the workbench stuck on the loading overlay.
      for (let attempt = 0; attempt < 20; attempt++) {
        try {
          await loadAccounts();
          await loadDevices();
          applyFocusFitToWindowSelect();  // focus deep-links out-rank the saved range
          await safeRefresh({ resetZoom: true, force: true });
          if (firstLoadComplete) return;
        } catch (error) {
          console.error('workbench boot retry', error);
        }
        await new Promise(resolve => setTimeout(resolve, 1500));
      }
    })();
    setInterval(tickCountdown, 1000);
    setInterval(updateTitleDotProgress, 200);
  