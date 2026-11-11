"""'Today' — the app's home. The ten-second check: live vitals with personal
context, today so far, and doors into the deeper views. The hero charts are
touchable: tap or hold to read a value, pull right to scroll back in time.
Chips open detail sheets, and care events (O₂ on/off, sock off…) can be
logged and show up as markers on the charts."""

from __future__ import annotations

from app.shell import render_shell

NOW_HEAD = """<link rel="manifest" href="/manifest.webmanifest" />
  <style>
    /* Today, night-sky layout (plan v0.2 §7 D4). Page-local custom properties
       live on .today and only reference theme tokens; candidates for theme.css. */
    .shell-main { max-width: 1120px; }
    .today {
      --today-well-radius: 14px;      /* chart wells inside a card */
      --today-box-radius: 16px;       /* bordered boxes that must not be pills */
      --today-meter: linear-gradient(90deg,
        color-mix(in srgb, var(--sleep-deep) 72%, var(--surface)),
        color-mix(in srgb, var(--series-move) 55%, var(--surface)) 60%,
        color-mix(in srgb, var(--awake) 70%, var(--surface)));
    }
    .today svg.ic { width: 17px; height: 17px; flex: none; fill: none; stroke: currentColor;
      stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; }

    /* ---- status sentence + range pills ------------------------------------ */
    .hello { display: flex; align-items: flex-end; justify-content: space-between;
      gap: 18px 24px; flex-wrap: wrap; padding: 14px 4px 28px; }
    .hello-text { flex: 1 1 420px; min-width: 0; }
    .hello h1 { margin: 0; font-size: 38px; font-weight: 700; line-height: 1.15;
      letter-spacing: -.01em; color: var(--ink); }
    .hello.is-low h1, .hello.is-stale h1 { color: var(--warn); }
    .hello.is-critical h1 { color: var(--bad); }
    .hello p { margin: 6px 0 0; color: var(--dim); font-size: 17px; line-height: 1.45;
      max-width: 70ch; }
    .hello p:empty { display: none; }
    .hello p b { color: var(--ink); font-weight: 600; }
    .range-seg, .ms-seg { display: flex; gap: 2px; padding: 4px; border-radius: var(--radius-pill);
      background: var(--pill-track); border: 1px solid var(--surface-line); }
    .range-seg { flex: 0 0 auto; }
    .range-seg button, .ms-seg button { all: unset; cursor: pointer; font-size: 14px;
      font-weight: 600; color: var(--dim); padding: 6px 15px; border-radius: var(--radius-pill);
      font-variant-numeric: tabular-nums; }
    .range-seg button:hover, .ms-seg button:hover { color: var(--ink); }
    .range-seg button.active, .ms-seg button.active, .range-seg .rs-custom {
      background: var(--pill-active); color: var(--ink); box-shadow: var(--shadow-lift); }
    .range-seg .rs-custom { font-size: 14px; font-weight: 600; padding: 6px 15px;
      border-radius: var(--radius-pill); }
    .range-seg .rs-custom[hidden] { display: none; }

    /* ---- cards ------------------------------------------------------------- */
    .grid { display: grid; grid-template-columns: repeat(12, 1fr); gap: 20px; }
    .tcard { padding: 28px 32px; min-width: 0; grid-column: span 12; }
    .tcard h2 { margin: 0; font-size: 21px; font-weight: 700; line-height: 1.25; }
    .tcard .note { color: var(--dim); margin-top: 2px; font-size: 16px; display: flex;
      align-items: center; gap: 4px 7px; flex-wrap: wrap; }
    .tcard .note.warn { color: var(--warn); font-weight: 600; }
    .tcard .note.bad { color: var(--bad); font-weight: 600; }
    .tcard .note b { color: var(--ink); font-weight: 600; text-transform: capitalize; }
    .card-head { display: flex; justify-content: space-between; align-items: flex-start;
      gap: 12px; }
    .live-card { background: var(--surface-live); }
    .live-chip { all: unset; font-size: 13px; font-weight: 600; color: var(--dim);
      white-space: nowrap; padding: 5px 0; }
    .live-chip.paused { cursor: pointer; color: var(--accent); background: var(--accent-soft);
      padding: 5px 13px; border-radius: var(--radius-pill); }

    /* ---- live readings: oxygen leads, pulse beside it ---------------------- */
    .vitals { display: grid; grid-template-columns: 1fr 1fr; gap: 40px; margin-top: 22px; }
    .vital { --c: var(--series-o2); min-width: 0; }
    .vital.hr { --c: var(--series-hr); }
    .reading { display: flex; align-items: center; flex-wrap: wrap; gap: 6px 16px;
      min-height: 84px; }
    .reading svg.v-icon { width: 38px; height: 38px; fill: none; stroke: var(--c);
      stroke-width: 1.7; stroke-linecap: round; stroke-linejoin: round; flex: none; }
    .big { font-size: 84px; font-weight: 700; line-height: 1; letter-spacing: -.01em;
      font-variant-numeric: tabular-nums; color: var(--ink); white-space: nowrap; }
    .big .unit { font-size: 24px; color: var(--dim); margin-left: 8px; font-weight: 400;
      letter-spacing: 0; }
    /* Two severity tiers, both user-tunable: amber warning, red critical.
       "out" (outside this baby's own baseline band) shares the warning tier.
       The tier is never colour alone: the .tier badge beside the number, the
       note under the card title and the page headline all say it in words. */
    .vital.out .big .num, .vital.low .big .num { color: var(--warn); }
    .vital.critical .big .num { color: var(--bad); }
    .vital.stale .big .num { color: var(--dim); }
    .tier { display: inline-flex; align-items: center; gap: 6px; font-size: 15px;
      font-weight: 700; padding: 5px 12px 5px 9px; border-radius: var(--radius-pill);
      white-space: nowrap; color: var(--warn);
      background: color-mix(in srgb, currentColor 14%, transparent); }
    .tier.critical { color: var(--bad); }
    .tier[hidden] { display: none; }
    .vital-tools { margin-left: auto; display: flex; gap: 6px; align-items: center; }
    .vital .label { color: var(--dim); font-size: 18px; margin: 2px 0 14px 54px;
      display: flex; gap: 2px 14px; flex-wrap: wrap; align-items: baseline; }
    .band { font-size: 14.5px; color: var(--dim); }
    .inspecting .band { color: var(--accent); font-weight: 600; }

    /* ---- chart wells ------------------------------------------------------- */
    .plot { position: relative; height: 170px; background: var(--surface-inset);
      border-radius: var(--today-well-radius); overflow: hidden;
      touch-action: pan-y; user-select: none; -webkit-user-select: none; cursor: grab; }
    .plot.dragging { cursor: grabbing; }
    .plot.placing { cursor: crosshair; outline: 2px solid var(--accent); outline-offset: -2px; }
    .chartzone svg { position: absolute; inset: 0; width: 100%; height: 100%; display: block; }
    .chartzone polyline, .ms-chartwrap polyline { fill: none; stroke: var(--accent);
      stroke-width: 2; stroke-linejoin: round; stroke-linecap: round;
      vector-effect: non-scaling-stroke; }
    .ms-chartwrap polyline { stroke-width: 1.6; }
    /* thresholds on the oxygen trace: amber = warning tier, red = critical */
    .threshold { stroke-width: 1; stroke-dasharray: 3 6; opacity: .7;
      vector-effect: non-scaling-stroke; }
    .threshold.warn { stroke: var(--warn); }
    .threshold.crit { stroke: var(--bad); }
    .thlabels { position: absolute; inset: 0; pointer-events: none; }
    .thlabels span { position: absolute; right: 10px; transform: translateY(-115%);
      font-size: 12px; font-weight: 600; white-space: nowrap; padding: 0 5px; border-radius: 6px;
      background: color-mix(in srgb, var(--surface-inset) 78%, transparent); }
    .thlabels span.under { transform: translateY(15%); }
    .thlabels .warn { color: var(--warn); }
    .thlabels .crit { color: var(--bad); }
    .movebar { fill: var(--series-move); }
    .chartzone line.evline, .ms-chartwrap line.evline { stroke: var(--warn);
      stroke-width: 1; stroke-dasharray: 2 3; vector-effect: non-scaling-stroke; opacity: .85; }
    .chartzone .evflag, .ms-chartwrap .evflag { fill: var(--warn); }
    /* offline ghost: holds the last reading's level through a gap, dashed grey
       so it can't be mistaken for live data */
    .chartzone .ghostline, .ms-chartwrap .ghostline { stroke: var(--dim);
      stroke-width: 1.4; stroke-dasharray: 4 4; vector-effect: non-scaling-stroke;
      opacity: .75; }
    /* amber = the sock isn't on her properly (same language as the title dot);
       grey stays for charging and collector-off */
    .chartzone .ghostline.warn, .ms-chartwrap .ghostline.warn { stroke: var(--warn); }
    /* sleep: one band coloured by stage on the page; three lanes in the sheet.
       Butt caps: rounded ones extend past each end and read as overlap. */
    .sleepfill.sleep-awake { fill: var(--awake); }
    .sleepfill.sleep-light { fill: var(--sleep-light); }
    .sleepfill.sleep-deep { fill: var(--sleep-deep); }
    .ms-chartwrap .sleepbar { fill: none; stroke-linecap: butt; vector-effect: non-scaling-stroke; }
    .sleepbar.sleep-awake { stroke: var(--awake); }
    .sleepbar.sleep-light { stroke: var(--sleep-light); }
    .sleepbar.sleep-deep { stroke: var(--sleep-deep); }
    .ms-chartwrap .sleepconn { fill: none; stroke-width: 2px;
      opacity: .45; stroke-linecap: round; vector-effect: non-scaling-stroke; }
    /* gap captions: small inline label naming why the line is dotted there */
    .gaplabels { position: absolute; inset: 0; pointer-events: none; overflow: hidden; }
    .gaplabels span { position: absolute; transform: translateX(-50%);
      font-size: 11px; font-weight: 600; letter-spacing: .06em; text-transform: uppercase;
      color: var(--dim); white-space: nowrap; }
    .gaplabels span.warn { color: var(--warn); }
    .xline { position: absolute; top: 0; bottom: 0; width: 1px; background: var(--ink);
      opacity: .55; pointer-events: none; }
    .ticks { display: flex; justify-content: space-between; color: var(--dim);
      font-size: 13px; margin-top: 8px; font-weight: 600; font-variant-numeric: tabular-nums; }
    .ticks .ts-end.live { color: var(--accent); }
    .vital-expand { all: unset; cursor: pointer; color: var(--dim); width: 34px; height: 34px;
      display: inline-grid; place-items: center; border-radius: var(--radius-pill); flex: none; }
    .vital-expand:hover { color: var(--accent); background: var(--accent-soft); }
    .vital-o2toggle { all: unset; cursor: pointer; font-size: 13px; font-weight: 700;
      padding: 7px 12px; border-radius: var(--radius-pill); color: var(--dim);
      border: 1px solid var(--surface-line); line-height: 1; white-space: nowrap;
      transition: color .35s ease, background-color .35s ease, border-color .35s ease; }
    .vital-o2toggle:hover { color: var(--accent); border-color: var(--accent); }
    .vital-o2toggle.on { color: var(--o2-on); background: color-mix(in srgb, var(--o2-on) 12%, transparent);
      border-color: color-mix(in srgb, var(--o2-on) 40%, transparent); }
    .vital-o2toggle.arming { color: var(--on-accent); background: var(--accent);
      border-color: var(--accent); }
    .place-hint { position: absolute; left: 50%; top: 10px; transform: translateX(-50%);
      z-index: 3; background: var(--accent); color: var(--on-accent); font-size: 13px;
      font-weight: 600; padding: 5px 12px; border-radius: var(--radius-pill); white-space: nowrap;
      pointer-events: none; }
    .place-hint[hidden] { display: none; }

    /* ---- movement: Peaceful - Wiggling meter ------------------------------- */
    .move-card { grid-column: span 5; --c: var(--series-move); }
    .meter { height: 12px; border-radius: var(--radius-pill); margin: 26px 0 12px;
      position: relative; background: var(--today-meter); }
    .meter i { position: absolute; top: -6px; width: 4px; height: 24px; margin-left: -2px;
      border-radius: 2px; background: var(--accent); left: 0; transition: left .4s ease; }
    .meter i[hidden] { display: none; }
    .ends { display: flex; justify-content: space-between; font-weight: 700; font-size: 17px; }
    .ends span:last-child { color: var(--dim); }
    .move-card .plot { margin-top: 20px; height: 56px; background: none; border-radius: 0; }

    /* ---- sleep summary ------------------------------------------------------ */
    .sleep-card { grid-column: span 7; --c: var(--sleep-light); }
    .pair { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin: 20px 0 22px;
      padding-bottom: 22px; border-bottom: 1px solid var(--surface-line); }
    .pair .k { display: block; color: var(--dim); }
    .pair .v { display: block; font-size: 26px; font-weight: 700; line-height: 1.2;
      font-variant-numeric: tabular-nums; }
    .pair-btn { all: unset; cursor: pointer; display: block;
      border-radius: var(--today-box-radius); }
    .pair-btn .more { display: block; color: var(--accent); font-size: 14px; font-weight: 600;
      margin-top: 2px; }
    .pair-btn:hover .more { text-decoration: underline; }
    .sleep-card h3 { margin: 0; font-size: 18px; font-weight: 700; }
    .sleep-card .plot { margin-top: 14px; height: 44px; border-radius: 8px; }
    .sleep-card .plot.live::after { content: ''; position: absolute; top: 0; bottom: 0;
      right: 0; width: 3px; background: var(--accent); }
    .legend { display: flex; gap: 18px; margin-top: 12px; color: var(--dim); font-size: 14px;
      flex-wrap: wrap; }
    .legend i { display: inline-block; width: 10px; height: 10px; border-radius: 3px;
      margin-right: 7px; }
    .legend .lg-light { background: var(--sleep-light); }
    .legend .lg-deep { background: var(--sleep-deep); }
    .legend .lg-awake { background: var(--awake); }

    /* ---- last night's story (serif) ---------------------------------------- */
    .story { grid-column: span 7; text-align: center; padding: 38px 32px; display: flex;
      flex-direction: column; align-items: center; justify-content: center; }
    .moon { width: 34px; height: 34px; border-radius: 50%;
      box-shadow: inset -11px -3px 0 0 var(--star-bright); transform: rotate(-20deg);
      margin-bottom: 18px; }
    .story p { margin: 0; font: 400 30px/1.25 var(--font-serif); color: var(--ink); }
    .story p b { color: var(--sleep-light); font-weight: 400; }
    .story small { display: block; color: var(--dim); font: 16px var(--font-sans);
      margin-top: 10px; }
    .story a { margin-top: 20px; color: var(--accent); font-weight: 600; text-decoration: none; }
    .story a:hover { text-decoration: underline; }

    /* ---- sock status list --------------------------------------------------- */
    .list { grid-column: span 5; padding: 24px 32px; }
    .list h2 { text-align: center; font-size: 19px; }
    .list .note { justify-content: center; font-size: 15px; margin-bottom: 10px; }
    .list dl { margin: 0; }
    .list .lrow { position: relative; display: flex; justify-content: space-between;
      align-items: baseline; gap: 16px; padding: 11px 0;
      border-top: 1px solid var(--surface-line); }
    .list .lrow[hidden] { display: none; }
    .list dt { color: var(--dim); }
    .list dd { margin: 0; font-weight: 600; text-align: right;
      font-variant-numeric: tabular-nums; }
    .list dd small { display: block; font-size: 13px; font-weight: 400; color: var(--dim); }
    .list dd.ok, .list dd .ok { color: var(--good); }
    .list dd.warn, .list dd .warn { color: var(--warn); }
    .list dd.info { color: var(--dim); }
    .list dd.o2-on .val { color: var(--o2-on); }
    /* a row that opens a detail sheet: the button's hit area covers the row */
    .row-btn { all: unset; cursor: pointer; text-align: right; }
    .row-btn::before { content: ''; position: absolute; inset: 0; }
    .row-btn .chev { color: var(--accent); margin-left: 6px; }
    .lrow:has(.row-btn:hover) dt { color: var(--ink); }
    .row-btn:focus-visible { outline: none; }
    .lrow:has(.row-btn:focus-visible) { outline: 2px solid var(--accent); outline-offset: 2px;
      border-radius: 6px; }

    /* ---- below the grid: tonight prep, raw data door ----------------------- */
    #belowHero { display: contents; }
    .prep { grid-column: span 7; }
    .prep-top { display: flex; justify-content: space-between; align-items: baseline;
      gap: 12px; margin-bottom: 12px; }
    .prep-top span { font-size: 14px; color: var(--dim); white-space: nowrap;
      font-variant-numeric: tabular-nums; }
    .prep-facts { display: grid; gap: 6px; font-size: 15px; color: var(--dim); }
    .prep-facts b { color: var(--ink); font-variant-numeric: tabular-nums; }
    .prep-note { font-size: 15px; color: var(--dim); line-height: 1.5; margin: 12px 0 0; }
    .prep-feed-btn { font: inherit; font-size: 13px; font-weight: 600; color: var(--accent);
      background: var(--accent-soft); border: 0; border-radius: var(--radius-pill); padding: 3px 11px;
      margin-left: 8px; cursor: pointer; }
    .prep-feed-btn:hover { filter: brightness(.96); }
    .door { grid-column: span 12; display: flex; flex-direction: column; justify-content: center;
      text-decoration: none; color: var(--ink); }
    .prep + .door { grid-column: span 5; }
    .door b { display: block; font-size: 19px; margin-bottom: 4px; }
    .door span { font-size: 15px; color: var(--dim); line-height: 1.45; }
    .door:hover b { color: var(--accent); }
    .empty { text-align: center; color: var(--dim); padding: 80px 20px; font-size: 15px; }

    @media (max-width: 860px) {
      .hello { padding: 4px 2px 20px; }
      .hello h1 { font-size: 30px; }
      .hello p { font-size: 16px; }
      .range-seg button, .range-seg .rs-custom { padding: 6px 12px; font-size: 13px; }
      .tcard, .list { padding: 22px 20px; border-radius: 22px; }
      .vitals { grid-template-columns: 1fr; gap: 28px; }
      .move-card, .sleep-card, .story, .list, .prep, .prep + .door { grid-column: span 12; }
      .big { font-size: 68px; }
      .reading { min-height: 68px; }
      .plot { height: 140px; }
      .story p { font-size: 26px; }
    }
    @media (max-width: 420px) {
      .grid { gap: 14px; }
      .vital .label { margin-left: 0; }
      .list .lrow { gap: 10px; }
    }

    /* ---- metric detail sheet ---------------------------------------------- */
    .ms-top { display: flex; justify-content: space-between; align-items: baseline;
      gap: 10px; margin-bottom: 10px; min-height: 34px; }
    .ms-value { font-size: 28px; font-weight: 700; letter-spacing: -.02em;
      font-variant-numeric: tabular-nums; }
    .ms-value small { font-size: 14px; color: var(--dim); font-weight: 500; }
    .ms-when { color: var(--dim); font-size: 13px; text-align: right; }
    .ms-seg { margin-bottom: 12px; }
    .ms-seg button { flex: 1; text-align: center; padding: 6px 0; font-size: 13px; }
    .ms-chartwrap { position: relative; touch-action: pan-y; user-select: none;
      -webkit-user-select: none; cursor: crosshair; }
    .ms-chartwrap svg { display: block; width: 100%; height: 150px; }
    .ms-xline { position: absolute; top: 0; bottom: 0; width: 1px; background: var(--dim);
      opacity: .8; pointer-events: none; }
    .ms-axis { display: flex; justify-content: space-between; color: var(--dim);
      font-size: 12px; margin-top: 5px; font-variant-numeric: tabular-nums; }
    .ms-ylab { position: absolute; right: 3px; font-size: 11px; color: var(--dim);
      font-variant-numeric: tabular-nums; pointer-events: none; }
    .ms-stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px;
      margin: 14px 0 4px; }
    .ms-stats div { text-align: center; }
    .ms-stats b { display: block; font-size: 16px; font-variant-numeric: tabular-nums; }
    .ms-stats span { font-size: 11px; color: var(--dim); text-transform: uppercase;
      letter-spacing: .08em; }
    .ms-dips { margin-top: 12px; display: grid; gap: 6px; }
    .ms-dips button { all: unset; box-sizing: border-box; cursor: pointer; display: flex;
      justify-content: space-between; gap: 10px; padding: 9px 14px;
      border: 1px solid var(--surface-line); border-radius: var(--today-box-radius);
      font-size: 13.5px; font-variant-numeric: tabular-nums; }
    .ms-dips button:hover { border-color: var(--accent); }
    .ms-dips b { color: var(--warn); }
    .ms-dips b.deep { color: var(--bad); }
    .ms-link { display: block; margin-top: 14px; text-align: center; color: var(--accent);
      text-decoration: none; font-size: 14px; font-weight: 600; }

    /* ---- detail sheet ---------------------------------------------------- */
    .sheet-backdrop { position: fixed; inset: 0; z-index: 80;
      background: var(--scrim); display: flex; align-items: center;
      justify-content: center; padding: 20px; }
    .sheet-backdrop[hidden] { display: none; }
    .sheet { width: min(460px, 100%); max-height: min(72vh, 620px); overflow-y: auto;
      padding: 22px 24px 24px; background: var(--surface-solid); box-shadow: var(--shadow-pop); }
    @media (max-width: 640px) {
      .sheet-backdrop { align-items: flex-end; padding: 0; }
      .sheet { width: 100%; max-height: 80vh; border-radius: var(--radius-card) var(--radius-card) 0 0;
        padding-bottom: calc(22px + env(safe-area-inset-bottom)); }
    }
    .sheet header { display: flex; align-items: center; justify-content: space-between;
      margin-bottom: 14px; }
    .sheet header b { font-size: 19px; }
    .sheet header button { all: unset; cursor: pointer; color: var(--dim);
      font-size: 15px; padding: 4px 8px; }
    .sheet header button:hover { color: var(--ink); }
    .sheet .row { display: flex; justify-content: space-between; gap: 12px;
      padding: 9px 0; border-bottom: 1px solid var(--surface-line); font-size: 14px; }
    .sheet .row:last-child { border-bottom: 0; }
    .sheet .row b { font-variant-numeric: tabular-nums; }
    .sheet .row .mut { color: var(--dim); }
    .sheet .row a { color: var(--accent); text-decoration: none; font-weight: 600; }
    .sheet .row .dip-low { color: var(--warn); }
    .sheet .row .dip-low.deep { color: var(--bad); }
    .sheet .note { font-size: 13px; color: var(--dim); line-height: 1.5; margin: 10px 0 0; }
    .sheet .ev-del { all: unset; cursor: pointer; color: var(--dim); padding: 0 4px; }
    .sheet .ev-del:hover { color: var(--bad); }
    .sheet .row-del { all: unset; cursor: pointer; color: var(--dim); padding: 0 6px;
      font-size: 12px; flex: none; }
    .sheet .row-del:hover { color: var(--bad); }
    .sheet .row b:empty { display: none; }
    .feed-logrow { display: flex; align-items: center; gap: 12px; margin-top: 14px; }
    .feed-logrow label { display: flex; align-items: center; gap: 7px; font-size: 13px;
      color: var(--dim); flex: none; }
    .feed-logrow input[type="time"] { padding: 9px 12px; border: 1px solid var(--surface-line);
      border-radius: var(--today-box-radius); background: var(--surface-inset); color: var(--ink);
      font: inherit; font-size: 14px; }
    .feed-logrow .o2-action { flex: 1; }
    .feed-note { width: 100%; box-sizing: border-box; padding: 10px 14px;
      border: 1px solid var(--surface-line); border-radius: var(--today-box-radius);
      background: var(--surface-inset); color: var(--ink); font: inherit; font-size: 14px;
      margin-bottom: 2px; }
    .feed-amt-head { display: flex; justify-content: space-between; align-items: baseline;
      margin: 14px 0 6px; }
    .feed-amt-head .section-title { margin: 0; }
    .feed-amt-head b { font-size: 19px; letter-spacing: -.01em; color: var(--accent);
      font-variant-numeric: tabular-nums; }
    .feed-slider { -webkit-appearance: none; appearance: none; width: 100%; height: 26px;
      background: transparent; margin: 0; cursor: pointer; }
    .feed-slider::-webkit-slider-runnable-track { height: 6px; border-radius: 3px;
      background: linear-gradient(to right, var(--accent-soft), var(--accent)); }
    .feed-slider::-webkit-slider-thumb { -webkit-appearance: none; width: 22px; height: 22px;
      border-radius: 50%; background: var(--surface); border: 2.5px solid var(--accent);
      margin-top: -8px; box-shadow: var(--shadow-thumb); }
    .feed-slider::-moz-range-track { height: 6px; border-radius: 3px;
      background: linear-gradient(to right, var(--accent-soft), var(--accent)); }
    .feed-slider::-moz-range-thumb { width: 20px; height: 20px; border-radius: 50%;
      background: var(--surface); border: 2.5px solid var(--accent); }
    .feed-slider-scale { display: flex; justify-content: space-between; font-size: 11px;
      color: var(--dim); margin-top: 2px; font-variant-numeric: tabular-nums; }
    .feed-strip { display: block; width: 100%; height: 30px; margin-top: 4px; }
    .fs-track { stroke: var(--surface-line); stroke-width: 1.5; }
    .fs-now { stroke: var(--faint); stroke-width: 1; stroke-dasharray: 2 3; }
    .fm-bottle { fill: var(--accent); opacity: .85; }
    .fm-nurse { fill: none; stroke: var(--accent); stroke-width: 2; }
    .fm-solid { fill: var(--series-move); opacity: .9; }
    .feed-strip-axis { display: flex; justify-content: space-between; font-size: 11px;
      color: var(--dim); font-variant-numeric: tabular-nums; margin-bottom: 4px; }
    .o2-state { text-align: center; margin-bottom: 14px; }
    .o2-state b { display: block; font-size: 24px; letter-spacing: -.01em; }
    .o2-state.on b { color: var(--o2-on); }
    .o2-state span { font-size: 13px; color: var(--dim); }
    .flow-presets { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px;
      margin-bottom: 12px; }
    .flow-presets button { all: unset; box-sizing: border-box; text-align: center; cursor: pointer;
      padding: 10px 4px; border: 1px solid var(--surface-line);
      border-radius: var(--today-box-radius);
      font-size: 14px; font-weight: 600; color: var(--ink); font-variant-numeric: tabular-nums; }
    .flow-presets button:hover, .flow-presets button.sel { border-color: var(--accent);
      color: var(--accent); background: var(--accent-soft); }
    .o2-action { all: unset; box-sizing: border-box; display: block; width: 100%;
      text-align: center; cursor: pointer; padding: 12px 0;
      border-radius: var(--radius-control); background: var(--accent); color: var(--on-accent);
      font-size: 15px; font-weight: 700; }
    .o2-action.stop { background: transparent; color: var(--bad);
      border: 1px solid color-mix(in srgb, var(--bad) 40%, transparent); }
    .o2-action[disabled] { opacity: .5; cursor: default; }
    .sheet .section-title { margin-top: 18px; }
    @media (prefers-reduced-motion: reduce) {
      .chartzone svg g { transition: none !important; }
      .meter i, .vital-o2toggle { transition: none; }
    }
  </style>"""

_ICON_O2 = (
    '<svg class="v-icon" viewBox="0 0 24 24" aria-hidden="true">'
    '<circle cx="9" cy="11" r="5.5"/><circle cx="17.5" cy="15.5" r="3.5"/></svg>'
)
_ICON_HR = (
    '<svg class="v-icon" viewBox="0 0 24 24" aria-hidden="true">'
    '<path d="M2.5 12.5h4l2.2-6 4.3 11.5 2.4-5.500h6.100"/></svg>'
)
_ICON_EXPAND = (
    '<svg class="ic" viewBox="0 0 24 24" aria-hidden="true">'
    '<path d="M14 4h6v6M20 4l-7 7M10 20H4v-6M4 20l7-7"/></svg>'
)

_EXPAND_BTN = (
    '<button class="vital-expand" data-expand="{key}" type="button" '
    'aria-label="Expand {label} chart" title="Bigger chart &amp; stats">' + _ICON_EXPAND + "</button>"
)

_PLOT = """<div class="plot chartzone" id="{key}Plot">
          <svg viewBox="0 0 100 42" preserveAspectRatio="none" aria-hidden="true"><g id="{key}G"></g></svg>
          <div class="thlabels" id="{key}Th"></div>
          <div class="gaplabels" id="{key}Gaps"></div>
          <div class="xline" id="{key}X" hidden></div>{extra}
        </div>"""

_TICKS = '<div class="ticks"><span class="ts-start"></span><span class="ts-end"></span></div>'

_VITAL = (
    """<div class="vital {key}" id="card-{key}" data-metric="{key}">
        <div class="reading">
          {icon}
          <div class="big"><span class="num" id="{key}Value">—</span><span class="unit" id="{key}Unit">{unit}</span></div>
          <span class="tier" id="{key}Tier" hidden></span>
          <span class="vital-tools">{tools}"""
    + _EXPAND_BTN
    + """</span>
        </div>
        <div class="label"><span>{label}</span><span class="band" id="{key}Band"></span></div>
        """
    + _PLOT
    + """
        """
    + _TICKS
    + """
      </div>"""
)

NOW_BODY = (
    """<div class="today">
    <section class="hello" id="hello">
      <div class="hello-text">
        <h1 class="status-line" id="statusLine">Checking in…</h1>
        <p id="statusDetail"></p>
      </div>
      <div class="range-seg" id="rangeSeg" role="group" aria-label="Chart window">
        <button type="button" data-mins="60">1h</button>
        <button type="button" data-mins="180">3h</button>
        <button type="button" data-mins="360">6h</button>
        <button type="button" data-mins="720">12h</button>
        <button type="button" data-mins="1440">24h</button>
        <span class="rs-custom" id="rangeCustom" hidden>Custom</span>
      </div>
    </section>
    <div class="grid" id="todayGrid">
      <section class="card tcard live-card" aria-labelledby="liveTitle">
        <div class="card-head">
          <div>
            <h2 id="liveTitle">Live health readings</h2>
            <div class="note" id="liveNote" role="status">Waiting for the first reading…</div>
          </div>
          <button class="live-chip" id="tsLive" type="button">● live</button>
        </div>
        <div class="vitals">
      """
    + _VITAL.format(
        key="o2", label="Oxygen", unit="%", icon=_ICON_O2,
        tools=(
            '<button class="vital-o2toggle" id="o2ToggleBtn" type="button" '
            'title="Log supplemental O₂ on/off at a point on the chart">O₂</button>'
        ),
        extra='<div class="place-hint" id="placeHint" hidden></div>',
    )
    + _VITAL.format(key="hr", label="Pulse rate", unit="BPM", icon=_ICON_HR, tools="", extra="")
    + """
        </div>
      </section>

      <section class="card tcard move-card" id="card-move" data-metric="move" aria-labelledby="moveTitle">
        <div class="card-head">
          <div>
            <h2 id="moveTitle">Movement</h2>
            <div class="note"><b id="moveValue">—</b><span class="band" id="moveBand"></span></div>
          </div>
          """
    + _EXPAND_BTN.format(key="move", label="Movement")
    + """
        </div>
        <div class="meter" id="moveMeter" role="img" aria-label="Movement level"><i id="moveMark" hidden></i></div>
        <div class="ends" aria-hidden="true"><span>Peaceful</span><span>Wiggling</span></div>
        """
    + _PLOT.format(key="move", extra="")
    + """
      </section>

      <section class="card tcard sleep-card" id="card-sleep" data-metric="sleep" aria-labelledby="sleepTitle">
        <div class="card-head">
          <div>
            <h2 id="sleepTitle">Sleep summary</h2>
            <div class="note"><b id="sleepValue">—</b><span class="band" id="sleepBand"></span></div>
          </div>
          """
    + _EXPAND_BTN.format(key="sleep", label="Sleep")
    + """
        </div>
        <div class="pair">
          <button type="button" class="pair-btn" data-open="sleep" title="Sleep sessions and stages">
            <span class="k">Time asleep today</span>
            <span class="v" id="sleepToday">—</span>
            <span class="more">Sessions ›</span>
          </button>
          <div><span class="k">Wakings today</span><span class="v" id="wakingsToday">—</span></div>
        </div>
        <h3>Sleep cycle</h3>
        """
    + _PLOT.format(key="sleep", extra="")
    + """
        """
    + _TICKS
    + """
        <div class="legend">
          <span><i class="lg-light"></i>Light</span>
          <span><i class="lg-deep"></i>Deep</span>
          <span><i class="lg-awake"></i>Awake</span>
        </div>
      </section>

      <section class="card tcard story" id="storyCard" aria-label="Last night">
        <div class="moon" aria-hidden="true"></div>
        <p>Reading last night…</p>
        <a href="/night">Last night's report →</a>
      </section>

      <section class="card tcard list" aria-labelledby="sockTitle">
        <h2 id="sockTitle">Sock</h2>
        <div class="note" id="sockSub">Checking…</div>
        <dl id="sockRows"></dl>
        <dl>
          <div class="lrow" id="armedRow" hidden>
            <dt>This PC's alerts</dt><dd id="armedValue" role="status"></dd>
          </div>
        </dl>
      </section>

      <div id="belowHero"></div>
    </div>
    <div class="sheet-backdrop" id="sheetBackdrop" hidden>
      <div class="sheet card" role="dialog" aria-modal="true" aria-labelledby="sheetTitle">
        <header><b id="sheetTitle"></b><button id="sheetClose" type="button" aria-label="Close">✕</button></header>
        <div id="sheetBody"></div>
      </div>
    </div>
    </div>"""
)

NOW_SCRIPTS = """<script src="/insights.js"></script>
  <script>
    const I = window.OwletInsights;
    const el = id => document.getElementById(id);
    const pad = n => String(n).padStart(2, '0');
    const fmtDur = seconds => {
      const totalMinutes = Math.round(seconds / 60);
      const h = Math.floor(totalMinutes / 60), m = totalMinutes % 60;
      return h ? `${h}h ${pad(m)}m` : `${m}m`;
    };
    const fmtClock = date => {
      let h = date.getHours(); const m = pad(date.getMinutes());
      const ap = h >= 12 ? 'PM' : 'AM'; h = h % 12 || 12;
      return `${h}:${m} ${ap}`;
    };
    const esc = text => String(text).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
    let pollSeconds = 5;
    let deviceName = 'your little one';
    const capitalized = s => s.charAt(0).toUpperCase() + s.slice(1);
    let rollups = [];

    const stateLabel = value => ({ '0': 'resting', '1': 'awake', '8': 'in light sleep', '15': 'in deep sleep' }[String(value)] || null);
    const isOffline = row => !!(row?.sock_disconnected || row?.sock_off
      || (row?.heart_rate != null && row.heart_rate <= 0)
      || (row?.oxygen_saturation != null && row.oxygen_saturation <= 0));

    // Resolved from the account's settings once prefs load; defaults until then.
    let O2 = window.owletO2(null);
    // A zeroed reading means the sock is off the foot, not a desaturation —
    // grey it out rather than screaming red (the Data page already did this).
    const o2Zone = value => (value == null || value <= 0) ? 'var(--faint)'
      : value < O2.critical ? 'var(--bad)'
      : value < O2.warn ? 'var(--warn)'
      : 'var(--series-o2)';
    const moveWord = avg => avg < 4 ? 'calm' : avg < 20 ? 'stirring' : 'active';

    const METRICS = [
      { key: 'o2',   label: 'Oxygen',     unit: '%',   fmt: v => Math.round(v), zone: o2Zone, color: 'var(--series-o2)' },
      { key: 'hr',   label: 'Pulse rate', unit: 'bpm', fmt: v => Math.round(v), zone: null, color: 'var(--series-hr)' },
      { key: 'temp', label: 'Skin temp',  unit: '°C',  fmt: v => v.toFixed(1),  zone: null, color: 'var(--series-temp)' },
      { key: 'move', label: 'Movement',   unit: '',    fmt: v => Math.round(v), zone: null, color: 'var(--series-move)' },
    ];
    // Charts on the page: temp lives in the sock list; sleep is a stage band.
    const HERO_KEYS = ['o2', 'hr', 'sleep', 'move'];
    const SLEEP_WORD = { awake: 'awake', light: 'light', deep: 'deep' };

    // ---- data buffers ------------------------------------------------------
    // liveReadings is the always-fresh last hour; histReadings grows on demand
    // as the user pulls back in time. Points arrays hold only valid vitals, so
    // collector-off stretches show up as time gaps in the line.
    const WINDOW_CHOICES = [60, 180, 360, 720, 1440];
    let windowMs = (() => {
      const saved = Number(localStorage.getItem('owletTodayWindow'));
      return WINDOW_CHOICES.includes(saved) ? saved * 60 * 1000 : 60 * 60 * 1000;
    })();
    const GAP_MS = 90 * 1000;
    const HISTORY_STEPS = [3, 6, 12, 24];
    let liveReadings = [], histReadings = [], loadedHours = 1, loadingHistory = false;
    let points = { o2: [], hr: [], temp: [], move: [] };
    let gaps = [];   // {start, end, kind: 'collector' | 'sock'} — why the line breaks
    let bands = { o2: null, hr: null };
    let latest = { o2: null, hr: null, temp: null, move: null, offline: true };
    let tempRangeText = '';
    let baselineState = 'awake';
    let careEvents = [];

    function allReadings() {
      if (!histReadings.length) return liveReadings;
      const cut = liveReadings.length ? Date.parse(liveReadings[0].recorded_at) : Infinity;
      return histReadings.filter(r => Date.parse(r.recorded_at) < cut).concat(liveReadings);
    }
    // Display prefs (settings modal): the chart can ride Owlet's smoothed O₂
    // line or its normalized 0–100 activity level instead of the raw feeds.
    let DISPLAY = { o2: 'raw', move: 'raw' };
    let FEEDS_ON = true;   // feed-tracking preference gates the chip + insights
    function rebuildPoints() {
      const rows = allReadings();
      const valid = rows.filter(r => !isOffline(r));
      const o2Of = r => DISPLAY.o2 === 'smoothed' && r.oxygen_10_av > 0
        ? r.oxygen_10_av : r.oxygen_saturation;
      const moveOf = r => DISPLAY.move === 'bucket' && r.movement_bucket != null
        ? r.movement_bucket : (r.movement || 0);
      points.hr = valid.filter(r => r.heart_rate > 0)
        .map(r => ({ x: Date.parse(r.recorded_at), y: r.heart_rate }));
      points.o2 = valid.filter(r => o2Of(r) > 0)
        .map(r => ({ x: Date.parse(r.recorded_at), y: o2Of(r) }));
      points.temp = valid.filter(r => r.skin_temperature > 0)
        .map(r => ({ x: Date.parse(r.recorded_at), y: r.skin_temperature }));
      points.move = valid.map(r => ({ x: Date.parse(r.recorded_at), y: moveOf(r) }));
      gaps = computeGaps(rows);
      sleepRuns = computeSleepRuns(rows);
    }
    let sleepRuns = [];   // {start, end, level: 'awake' | 'light' | 'deep'}
    function computeSleepRuns(rows) {
      const level = s => { const v = String(s); return v === '8' ? 'light' : v === '15' ? 'deep' : 'awake'; };
      const out = [];
      let run = null;
      for (const r of rows) {
        if (isOffline(r)) { run = null; continue; }
        const t = Date.parse(r.recorded_at);
        const lv = level(r.sleep_state);
        if (run && run.level === lv && t - run.end <= GAP_MS) run.end = t;
        else { run = { start: t, end: t, level: lv }; out.push(run); }
      }
      // drop sub-45s flickers — Owlet's state flaps sample to sample
      return out.filter(r => r.end - r.start >= 45 * 1000);
    }
    // How long the current asleep/awake stretch has run, straight from the
    // readings (rollup sessions lag and often read 'settling in'). Adjacent
    // runs of the same family merge across brief gaps; if the stretch reaches
    // the edge of the loaded buffer, the rollup session extends it further
    // back when it agrees on the state.
    function currentStateSince(rollupRun) {
      if (!sleepRuns.length) return null;
      const last = sleepRuns[sleepRuns.length - 1];
      if (Date.now() - last.end > GAP_MS * 2) return null;   // no live state
      const asleep = last.level !== 'awake';
      let start = last.start;
      for (let i = sleepRuns.length - 2; i >= 0; i--) {
        const run = sleepRuns[i];
        if ((run.level !== 'awake') !== asleep) break;
        if (start - run.end > GAP_MS * 4) break;
        start = run.start;
      }
      if (rollupRun && rollupRun.state !== 'nodata'
        && (rollupRun.state === 'asleep') === asleep
        && rollupRun.start < start && start - loadedStart() < 10 * 60 * 1000) {
        start = rollupRun.start;
      }
      return { asleep, start };
    }
    function sleepLevelAt(t) {
      const run = sleepRuns.find(r => t >= r.start - GAP_MS && t <= r.end + GAP_MS);
      return run ? run.level : null;
    }
    function computeGaps(rows) {
      const out = [];
      let sockRun = null;
      // Rows keep flowing while the sock itself is silent, so we can usually
      // say WHY: the sock's own charging flag (exact), battery climbing as a
      // fallback for old rows without it, explicit disconnect flag, or plain
      // sock-off. No rows at all = the collector wasn't running.
      const finishSockRun = run => {
        run.label = run.charging
          ? 'charging'
          : run.battery0 != null && run.battery1 != null && run.battery1 - run.battery0 > 1
            ? 'charging'
            : (run.disconnected ? 'disconnected' : 'sock off');
        return run;
      };
      for (let i = 0; i < rows.length; i++) {
        const t = Date.parse(rows[i].recorded_at);
        if (i > 0) {
          const prev = Date.parse(rows[i - 1].recorded_at);
          if (t - prev > 2 * 60 * 1000) out.push({ start: prev, end: t, kind: 'collector', label: 'collector off' });
        }
        if (isOffline(rows[i])) {
          const battery = rows[i].battery;
          if (!sockRun) sockRun = { start: t, end: t, kind: 'sock', battery0: battery, battery1: battery, disconnected: false, charging: false };
          else {
            sockRun.end = t;
            if (battery != null) { sockRun.battery1 = battery; if (sockRun.battery0 == null) sockRun.battery0 = battery; }
          }
          if (rows[i].sock_disconnected) sockRun.disconnected = true;
          if (rows[i].charging) sockRun.charging = true;
        } else if (sockRun) { out.push(finishSockRun(sockRun)); sockRun = null; }
      }
      if (sockRun) { sockRun.end = Date.now(); out.push(finishSockRun(sockRun)); }
      if (rows.length) {
        const lastT = Date.parse(rows[rows.length - 1].recorded_at);
        if (Date.now() - lastT > 2 * 60 * 1000) out.push({ start: lastT, end: Date.now(), kind: 'collector', label: 'collector off' });
        const firstT = Date.parse(rows[0].recorded_at);
        if (firstT - loadedStart() > 2 * 60 * 1000) out.unshift({ start: loadedStart(), end: firstT, kind: 'collector', label: 'collector off' });
      }
      return out;
    }
    // Small inline captions naming each gap wide enough to carry one.
    function gapLabelSpans(t0, t1, minFrac, ghostYs) {
      return gaps
        .filter(g => g.end > t0 && g.start < t1
          && (Math.min(g.end, t1) - Math.max(g.start, t0)) / (t1 - t0) >= minFrac)
        .map(g => {
          const mid = (Math.max(g.start, t0) + Math.min(g.end, t1)) / 2;
          const yFrac = ghostYs && ghostYs[g.start] != null ? ghostYs[g.start] : null;
          const top = yFrac == null ? 'top:5px' : `top:max(2px, calc(${(yFrac * 100).toFixed(1)}% - 16px))`;
          return `<span class="${gapWarn(g) ? 'warn' : ''}" style="left:${(((mid - t0) / (t1 - t0)) * 100).toFixed(2)}%;${top}">${esc(g.label)}</span>`;
        }).join('');
    }
    function gapAt(t) { return gaps.find(g => t >= g.start && t <= g.end) || null; }
    const gapWarn = g => g.label === 'disconnected' || g.label === 'sock off';
    function lastPointBefore(pts, t) {
      if (!pts.length || pts[0].x > t) return null;
      let lo = 0, hi = pts.length - 1;
      while (lo < hi) { const mid = (lo + hi + 1) >> 1; (pts[mid].x <= t ? lo = mid : hi = mid - 1); }
      return pts[lo];
    }
    function firstPointAfter(pts, t) {
      if (!pts.length || pts[pts.length - 1].x < t) return null;
      let lo = 0, hi = pts.length - 1;
      while (lo < hi) { const mid = (lo + hi) >> 1; (pts[mid].x >= t ? hi = mid : lo = mid + 1); }
      return pts[lo];
    }
    function loadedStart() { return Date.now() - loadedHours * 3600 * 1000; }
    async function extendHistory() {
      const next = HISTORY_STEPS.find(h => h > loadedHours);
      if (!next || loadingHistory) return;
      loadingHistory = true;
      try {
        const rows = await fetch(`/api/readings?hours=${next}&limit=40000`).then(r => r.json());
        histReadings = rows; loadedHours = next;
        rebuildPoints();
        // Re-render immediately, even mid-drag — pan position derives from
        // viewEnd, so re-anchoring under the finger is seamless, and the
        // freshly loaded points appear while the graph is still moving.
        renderCharts();
      } catch (error) { /* keep what we have */ }
      loadingHistory = false;
    }
    async function ensureHistoryHours(hours) {
      for (let i = 0; i < HISTORY_STEPS.length && loadedHours < hours; i++) {
        const before = loadedHours;
        await extendHistory();
        if (loadedHours === before) break;   // concurrent fetch or nothing left to load
      }
    }
    async function loadEvents() {
      try {
        const data = await fetch('/api/events?hours=336&limit=2000').then(r => r.json());
        careEvents = (data.events || []).map(e => ({ ...e, x: Date.parse(e.at) }));
        computeO2();
        if (!gesture) renderCharts();
      } catch (error) { /* non-fatal */ }
    }

    // ---- supplemental O2: state + intervals from on/off events -------------
    let o2State = { on: false, since: null, flow: '' };
    let o2Spans = [];
    let o2HasLog = false;
    function computeO2() {
      const marks = careEvents
        .filter(e => e.kind === 'O₂ on' || e.kind === 'O₂ off')
        .sort((a, b) => a.x - b.x);
      o2HasLog = marks.length > 0;
      const spans = [];
      let open = null, flow = '';
      for (const mark of marks) {
        if (mark.kind === 'O₂ on') {
          if (open && (mark.note || '') !== flow) {     // flow change while on
            spans.push({ start: open.x, end: mark.x, flow, startId: open.id, endId: null });
            open = mark;
          } else if (!open) open = mark;
          flow = mark.note || flow;
        } else if (open) {
          spans.push({ start: open.x, end: mark.x, flow, startId: open.id, endId: mark.id });
          open = null; flow = '';
        }
      }
      if (open) spans.push({ start: open.x, end: Infinity, flow, startId: open.id, endId: null });
      o2Spans = spans;
      o2State = open
        ? { on: true, since: open.x, flow }
        : { on: false, since: marks.length ? marks[marks.length - 1].x : null, flow: '' };
      paintO2Toggle();
    }
    const onO2At = t => o2Spans.some(s => t >= s.start && t <= s.end);
    // A single rect carries the whole on/off timeline: a horizontal gradient
    // encodes the states (oxygen blue / grey) with soft stops at each
    // transition, and a vertical mask fades it out rising from the floor.
    function o2OverlayMarkup(t0, t1, dataStart, dataEnd, w, h, clip, pid, clipPoly) {
      if (!o2HasLog) return '';
      const xOf = t => ((t - t0) / (t1 - t0)) * w;
      const span = dataEnd - dataStart;
      const fade = Math.max(90 * 1000, span * 0.02);
      const colorFor = state => state === 'on' ? 'var(--o2-on)' : 'var(--faint)';
      const offsetOf = time => Math.max(0, Math.min(1, (time - dataStart) / span)).toFixed(4);
      const marks = [];
      o2Spans.forEach(s => {
        marks.push({ t: s.start, to: 'on' });
        if (s.end !== Infinity) marks.push({ t: s.end, to: 'off' });
      });
      marks.sort((a, b) => a.t - b.t);
      let state = onO2At(dataStart) ? 'on' : 'off';
      let stops = `<stop offset="0" style="stop-color:${colorFor(state)}"/>`;
      marks.filter(m => m.t > dataStart && m.t < dataEnd).forEach(m => {
        stops += `<stop offset="${offsetOf(m.t - fade)}" style="stop-color:${colorFor(state)}"/>`;
        state = m.to;
        stops += `<stop offset="${offsetOf(m.t + fade)}" style="stop-color:${colorFor(state)}"/>`;
      });
      stops += `<stop offset="1" style="stop-color:${colorFor(onO2At(dataEnd) ? 'on' : 'off')}"/>`;
      const x0 = xOf(dataStart), x1 = xOf(Math.min(dataEnd, Date.now()));
      if (x1 - x0 < 0.5) return '';
      const rect = `x="${x0.toFixed(1)}" y="0" width="${(x1 - x0).toFixed(1)}" height="${h}"`;
      // Clipped to the area under the data line when there is one (the wisp
      // "bumps up to each point"); otherwise a short floor-hugging fade.
      const vStops = clipPoly
        ? '<stop offset="0" stop-color="#fff" stop-opacity=".34"/>'
          + '<stop offset=".55" stop-color="#fff" stop-opacity=".14"/>'
          + '<stop offset="1" stop-color="#fff" stop-opacity=".05"/>'
        : '<stop offset="0" stop-color="#fff" stop-opacity=".26"/>'
          + '<stop offset=".28" stop-color="#fff" stop-opacity=".10"/>'
          + '<stop offset=".5" stop-color="#fff" stop-opacity="0"/>';
      const clipDef = clipPoly ? `<clipPath id="${pid}-o2c"><polygon points="${clipPoly}"/></clipPath>` : '';
      const clipAttr = clipPoly ? ` clip-path="url(#${pid}-o2c)"` : '';
      return `<defs>
        <linearGradient id="${pid}-o2h" x1="0" y1="0" x2="1" y2="0">${stops}</linearGradient>
        <linearGradient id="${pid}-o2v" x1="0" y1="1" x2="0" y2="0">${vStops}</linearGradient>
        <mask id="${pid}-o2m"><rect ${rect} fill="url(#${pid}-o2v)"/></mask>
        ${clipDef}
      </defs>
      <rect ${rect} fill="url(#${pid}-o2h)" mask="url(#${pid}-o2m)"${clipAttr}/>`;
    }

    // ---- hero charts: virtualized pan window -------------------------------
    // viewEnd === null means "live" (window pinned to now). While panned, the
    // window is anchored to an absolute time so it stays frozen as new data
    // streams into the buffer behind it.
    let viewEnd = null;
    let anchorEnd = 0;   // window end the SVGs were last drawn against
    const chartEnd = () => viewEnd ?? Date.now();

    function downsample(pts) {
      if (pts.length <= 1500) return pts;
      const buckets = 700, out = [];
      const t0 = pts[0].x, span = pts[pts.length - 1].x - t0 || 1;
      let b = -1, lo = null, hi = null;
      for (const p of pts) {
        const idx = Math.floor(((p.x - t0) / span) * (buckets - 1));
        if (idx !== b) {
          if (lo) out.push(...(lo.x <= hi.x ? [lo, hi] : [hi, lo]).filter((v, i, a) => a.indexOf(v) === i));
          b = idx; lo = hi = p;
        } else {
          if (p.y < lo.y) lo = p;
          if (p.y > hi.y) hi = p;
        }
      }
      if (lo) out.push(...(lo.x <= hi.x ? [lo, hi] : [hi, lo]).filter((v, i, a) => a.indexOf(v) === i));
      return out;
    }

    // Shared SVG builder for hero strips and the detail sheet. Maps the time
    // domain [t0, t1] to x [0, w]; points outside may overflow (the hero pans
    // over its margins, the svg clips). Draws no-data bands, event markers
    // with a flag dot, then zone-colored line segments.
    function buildSeriesMarkup(key, t0, t1, dataStart, dataEnd, w, h, opts = {}) {
      const metric = METRICS.find(m => m.key === key);
      const xOf = t => ((t - t0) / (t1 - t0)) * w;
      const clampX = x => Math.max(0, Math.min(w, x));
      const eventMarks = careEvents
        .filter(e => e.x >= dataStart && e.x <= dataEnd && e.kind !== 'O₂ on' && e.kind !== 'O₂ off')
        .map(e => `<line class="evline" x1="${xOf(e.x).toFixed(2)}" x2="${xOf(e.x).toFixed(2)}" y1="0" y2="${h}"><title>${esc(e.kind)}</title></line>`
          + `<circle class="evflag" cx="${xOf(e.x).toFixed(2)}" cy="3" r="2"/>`)
        .join('');
      const pts = downsample(points[key].filter(p => p.x >= dataStart && p.x <= dataEnd));
      const overlayId = (opts.idPrefix || 'h') + '-' + key;
      const wantsO2 = key === 'o2' || key === 'hr';   // the vitals oxygen influences
      if (pts.length < 2) {
        const bare = wantsO2 ? o2OverlayMarkup(t0, t1, dataStart, dataEnd, w, h, !!opts.clip, overlayId, null) : '';
        return { markup: bare + eventMarks, min: null, max: null, ghostYs: {}, thYs: [] };
      }
      let min = Infinity, max = -Infinity;
      for (const p of pts) { if (p.y < min) min = p.y; if (p.y > max) max = p.y; }
      // Anchor the O₂ domain near the 90% line so a dip to 85 reads as a real
      // plunge instead of vanishing into an auto-fit scale.
      if (key === 'o2') { min = Math.min(min, 91); max = Math.max(max, 99); }
      // Thresholds are drawn on the trace (plan D1.9), so the scale always
      // reaches down past the critical line, wherever the user set it.
      if (key === 'o2' && opts.thresholds) min = Math.min(min, O2.critical - 1);
      const domainMin = min, domainMax = max;
      const padY = Math.max(0.5, (max - min) * 0.1); min -= padY; max += padY;
      const yOf = v => (h - 2) - ((v - min) / (max - min)) * (h - 6);
      let thresholds = '';
      const thYs = [];
      if (key === 'o2' && opts.thresholds) {
        // the hero pans over its margins, so its lines span the drawn data
        const xa = opts.clip ? 0 : xOf(dataStart), xb = opts.clip ? w : xOf(dataEnd);
        [[O2.warn, 'warn'], [O2.critical, 'crit']].filter(([v]) => v > min && v < max).forEach(([v, tier]) => {
          thYs.push({ tier, value: v, y: yOf(v) / h });
          thresholds += `<line class="threshold ${tier}" x1="${xa.toFixed(2)}" x2="${xb.toFixed(2)}" y1="${yOf(v).toFixed(2)}" y2="${yOf(v).toFixed(2)}"/>`;
        });
      }
      // Hold the last reading's level through each offline stretch as a dashed
      // grey line, so the gap reads as "signal lost here" rather than nothing.
      const ghostYs = {};
      const ghosts = gaps
        .filter(g => g.end >= dataStart && g.start <= dataEnd && g.end - g.start > GAP_MS)
        .map(g => {
          // Anchor on the reading before the gap, or the one after it when the
          // earlier history simply isn't loaded yet — the line always draws.
          const anchor = lastPointBefore(points[key], g.start + 1000)
            || firstPointAfter(points[key], g.end - 1000);
          if (!anchor) return '';
          const gy = Math.max(2, Math.min(h - 2, yOf(anchor.y)));
          ghostYs[g.start] = gy / h;
          const x0 = opts.clip ? clampX(xOf(g.start)) : xOf(g.start);
          const x1 = opts.clip ? clampX(xOf(g.end)) : xOf(g.end);
          if (x1 - x0 < 0.5) return '';
          return `<line class="ghostline${gapWarn(g) ? ' warn' : ''}" x1="${x0.toFixed(2)}" x2="${x1.toFixed(2)}" y1="${gy.toFixed(2)}" y2="${gy.toFixed(2)}"/>`;
        }).join('');
      const seriesColor = (metric && metric.color) || 'var(--accent)';
      const zone = (metric && metric.zone) || (() => seriesColor);
      const segs = [];
      let run = { color: zone(pts[0].y), coords: [`${xOf(pts[0].x).toFixed(2)},${yOf(pts[0].y).toFixed(2)}`] };
      for (let i = 1; i < pts.length; i++) {
        const gap = pts[i].x - pts[i - 1].x > GAP_MS;
        const color = zone(pts[i].y);
        const coord = `${xOf(pts[i].x).toFixed(2)},${yOf(pts[i].y).toFixed(2)}`;
        if (gap) { segs.push(run); run = { color, coords: [coord] }; continue; }
        run.coords.push(coord);
        if (color !== run.color) { segs.push(run); run = { color, coords: [coord] }; }
      }
      segs.push(run);
      const lines = segs.filter(s => s.coords.length > 1)
        .map(s => `<polyline points="${s.coords.join(' ')}" style="stroke:${s.color}"/>`)
        .join('');
      const clipPoly = `${xOf(pts[0].x).toFixed(1)},${h} `
        + pts.map(p => `${xOf(p.x).toFixed(1)},${yOf(p.y).toFixed(1)}`).join(' ')
        + ` ${xOf(pts[pts.length - 1].x).toFixed(1)},${h}`;
      const o2Overlay = wantsO2
        ? o2OverlayMarkup(t0, t1, dataStart, dataEnd, w, h, !!opts.clip, overlayId, clipPoly)
        : '';
      // Soft fill under the trace, in the vital's own colour (never the tier's).
      let area = '';
      if (opts.area) {
        const runs = [];
        let cur = [pts[0]];
        for (let i = 1; i < pts.length; i++) {
          if (pts[i].x - pts[i - 1].x > GAP_MS) { runs.push(cur); cur = []; }
          cur.push(pts[i]);
        }
        runs.push(cur);
        area = `<defs><linearGradient id="${overlayId}-area" gradientUnits="userSpaceOnUse" x1="0" x2="0" y1="0" y2="${h}">`
          + `<stop offset="0" style="stop-color:${seriesColor};stop-opacity:.22"/>`
          + `<stop offset="1" style="stop-color:${seriesColor};stop-opacity:0"/></linearGradient></defs>`
          + runs.filter(r => r.length > 1).map(r =>
            `<polygon fill="url(#${overlayId}-area)" points="${xOf(r[0].x).toFixed(2)},${h} `
            + r.map(p => `${xOf(p.x).toFixed(2)},${yOf(p.y).toFixed(2)}`).join(' ')
            + ` ${xOf(r[r.length - 1].x).toFixed(2)},${h}"/>`).join('');
      }
      // Movement reads as bars: the peak of each slice of the window. Slices
      // are anchored to absolute time so they hold still while panning.
      let marks = lines;
      if (opts.bars) {
        const binMs = (t1 - t0) / 96, top = Math.max(domainMax, 20), bins = new Map();
        for (const p of points[key]) {
          if (p.x < dataStart || p.x > dataEnd) continue;
          const b = Math.floor(p.x / binMs);
          if (!(bins.get(b) >= p.y)) bins.set(b, p.y);
        }
        const bw = (binMs / (t1 - t0)) * w;
        marks = [...bins].map(([b, v]) => {
          const bh = Math.max(1.5, Math.min(1, v / top) * (h - 3));
          return `<rect class="movebar" x="${(xOf(b * binMs) + bw * 0.2).toFixed(2)}" width="${(bw * 0.6).toFixed(2)}" y="${(h - bh).toFixed(2)}" height="${bh.toFixed(2)}" opacity="${v > 8 ? 0.85 : 0.35}"/>`;
        }).join('');
      }
      return { markup: area + o2Overlay + thresholds + ghosts + eventMarks + marks, min: domainMin, max: domainMax, ghostYs, thYs };
    }

    // Tracker-style hypnogram: rounded bars in three lanes (awake / light / deep).
    function buildSleepMarkup(t0, t1, dataStart, dataEnd, w, h, opts = {}) {
      const xOf = t => ((t - t0) / (t1 - t0)) * w;
      const clampX = x => Math.max(0, Math.min(w, x));
      const eventMarks = careEvents
        .filter(e => e.x >= dataStart && e.x <= dataEnd && e.kind !== 'O₂ on' && e.kind !== 'O₂ off')
        .map(e => `<line class="evline" x1="${xOf(e.x).toFixed(2)}" x2="${xOf(e.x).toFixed(2)}" y1="0" y2="${h}"><title>${esc(e.kind)}</title></line>`
          + `<circle class="evflag" cx="${xOf(e.x).toFixed(2)}" cy="3" r="2"/>`)
        .join('');
      if (opts.band) {
        // One band coloured by stage. A run is stretched to meet the next one
        // when they are only a sample apart, so no slivers show between them.
        const visible = sleepRuns.filter(r => r.end >= dataStart && r.start <= dataEnd);
        const fills = visible.map((r, i) => {
          const next = visible[i + 1];
          const endT = next && next.start - r.end <= GAP_MS * 3 ? next.start : r.end;
          const x0 = xOf(r.start), x1 = xOf(endT);
          if (x1 - x0 < 0.05) return '';
          return `<rect class="sleepfill sleep-${r.level}" x="${x0.toFixed(2)}" width="${(x1 - x0).toFixed(2)}" y="0" height="${h}"/>`;
        }).join('');
        const bandGhostYs = {};
        const bandGhosts = gaps
          .filter(g => g.end >= dataStart && g.start <= dataEnd && g.end - g.start > GAP_MS)
          .map(g => {
            bandGhostYs[g.start] = 0.86;
            return `<line class="ghostline${gapWarn(g) ? ' warn' : ''}" x1="${xOf(g.start).toFixed(2)}" x2="${xOf(g.end).toFixed(2)}" y1="${(h * 0.86).toFixed(2)}" y2="${(h * 0.86).toFixed(2)}"/>`;
          }).join('');
        return { markup: fills + bandGhosts + eventMarks, ghostYs: bandGhostYs };
      }
      const o2Overlay = '';
      const lanes = { awake: h * 0.2, light: h * 0.5, deep: h * 0.8 };
      // Gradients keyed to lane positions (userSpaceOnUse), so a connector
      // blends from one stage's color into the next no matter its direction —
      // the Apple Health transition look.
      const pid = opts.idPrefix || 'hyp';
      const laneColor = { awake: 'var(--awake)', light: 'var(--sleep-light)', deep: 'var(--sleep-deep)' };
      const pairs = [['awake', 'light'], ['light', 'deep'], ['awake', 'deep']];
      const defs = '<defs>' + pairs.map(([a, b]) =>
        `<linearGradient id="${pid}-${a}-${b}" gradientUnits="userSpaceOnUse" x1="0" x2="0" y1="${lanes[a].toFixed(2)}" y2="${lanes[b].toFixed(2)}">
          <stop offset="0" style="stop-color:${laneColor[a]}"/>
          <stop offset="1" style="stop-color:${laneColor[b]}"/>
        </linearGradient>`).join('') + '</defs>';
      const gradientFor = (a, b) => {
        const pair = pairs.find(p => (p[0] === a && p[1] === b) || (p[0] === b && p[1] === a));
        return `${pid}-${pair[0]}-${pair[1]}`;
      };
      const connectors = [];
      for (let i = 1; i < sleepRuns.length; i++) {
        const a = sleepRuns[i - 1], b = sleepRuns[i];
        if (a.level === b.level || b.start - a.end > GAP_MS * 3) continue;
        const xm = (a.end + b.start) / 2;
        if (xm < dataStart || xm > dataEnd) continue;
        const x = (opts.clip ? clampX(xOf(xm)) : xOf(xm)).toFixed(2);
        connectors.push(`<line class="sleepconn" x1="${x}" x2="${x}" y1="${lanes[a.level].toFixed(2)}" y2="${lanes[b.level].toFixed(2)}" stroke="url(#${gradientFor(a.level, b.level)})"/>`);
      }
      const bars = sleepRuns
        .filter(r => r.end >= dataStart && r.start <= dataEnd)
        .map(r => {
          const x0 = opts.clip ? clampX(xOf(r.start)) : xOf(r.start);
          const x1 = opts.clip ? clampX(xOf(r.end)) : xOf(r.end);
          if (x1 - x0 < 0.4) return '';
          const y = lanes[r.level].toFixed(2);
          return `<line class="sleepbar sleep-${r.level}" x1="${x0.toFixed(2)}" x2="${x1.toFixed(2)}" y1="${y}" y2="${y}" style="stroke-width:${opts.bar || 5}px"/>`;
        }).join('');
      const ghostYs = {};
      const ghosts = gaps
        .filter(g => g.end >= dataStart && g.start <= dataEnd && g.end - g.start > GAP_MS)
        .map(g => {
          const anchor = sleepRuns.filter(r => r.end <= g.start + 1000).pop()
            || sleepRuns.find(r => r.start >= g.end - 1000);
          if (!anchor) return '';
          ghostYs[g.start] = lanes[anchor.level] / h;
          const x0 = opts.clip ? clampX(xOf(g.start)) : xOf(g.start);
          const x1 = opts.clip ? clampX(xOf(g.end)) : xOf(g.end);
          if (x1 - x0 < 0.5) return '';
          const y = lanes[anchor.level].toFixed(2);
          return `<line class="ghostline${gapWarn(g) ? ' warn' : ''}" x1="${x0.toFixed(2)}" x2="${x1.toFixed(2)}" y1="${y}" y2="${y}"/>`;
        }).join('');
      return { markup: defs + o2Overlay + ghosts + eventMarks + connectors.join('') + bars, ghostYs };
    }

    function renderChart(key) {
      const g = el(key + 'G'); if (!g) return;
      const end = chartEnd();
      const built = key === 'sleep'
        ? buildSleepMarkup(end - windowMs, end, end - 2 * windowMs, end + windowMs / 4, 100, 42, { band: true })
        : buildSeriesMarkup(key, end - windowMs, end, end - 2 * windowMs, end + windowMs / 4, 100, 42,
          { thresholds: key === 'o2', area: key !== 'move', bars: key === 'move' });
      g.innerHTML = built.markup;
      g.removeAttribute('transform');
      const labels = el(key + 'Gaps');
      if (labels) { labels.innerHTML = gapLabelSpans(end - windowMs, end, 0.16, built.ghostYs); labels.style.transform = ''; }
      // Threshold captions say the tier in words; they stay put while panning.
      const th = el(key + 'Th');
      if (th) th.innerHTML = (built.thYs || []).map((t, i, all) => {
        const under = t.y < 0.12 || (i > 0 && Math.abs(t.y - all[0].y) < 0.14);
        return `<span class="${t.tier}${under ? ' under' : ''}" style="top:${(t.y * 100).toFixed(1)}%">${t.tier === 'warn' ? 'amber' : 'red'} below ${t.value}</span>`;
      }).join('');
    }
    function renderCharts() {
      anchorEnd = chartEnd();
      HERO_KEYS.forEach(renderChart);
      updateTimescale();
    }
    function applyPan() {
      const shift = ((anchorEnd - chartEnd()) / windowMs) * 100;
      if (Math.abs(shift) > 85) { renderCharts(); return; }
      const t = `translate(${shift.toFixed(3)} 0)`;
      HERO_KEYS.forEach(key => {
        el(key + 'G').setAttribute('transform', t);
        el(key + 'Gaps').style.transform = `translateX(${shift.toFixed(3)}%)`;
      });
      updateTimescale();
    }
    // While the graph is moving, a center reticle reads out whatever passes
    // beneath it — the big numbers become "the value at the middle".
    function panFocus() {
      inspectAtTime(chartEnd() - windowMs / 2, 0.5);
    }
    const fmtTick = date => {
      const label = fmtClock(date);
      return date.toDateString() === new Date().toDateString()
        ? label
        : `${date.getMonth() + 1}/${date.getDate()} ${label}`;
    };
    function updateTimescale() {
      const end = chartEnd();
      const live = viewEnd == null;
      const startText = fmtTick(new Date(end - windowMs)), endText = live ? 'now' : fmtTick(new Date(end));
      document.querySelectorAll('.ts-start').forEach(node => { node.textContent = startText; });
      document.querySelectorAll('.ts-end').forEach(node => { node.textContent = endText; node.classList.toggle('live', live); });
      el('sleepPlot').classList.toggle('live', live);
      const chip = el('tsLive');
      chip.textContent = viewEnd != null ? '⟳ back to now' : tierState.stale ? '○ not live' : '● live';
      chip.classList.toggle('paused', viewEnd != null);
    }
    el('tsLive').addEventListener('click', () => {
      stopMomentum();
      if (viewEnd == null) { clearInspect(); return; }
      viewEnd = null; clearInspect(); renderCharts();
    });

    // ---- window picker (pills, top right): sets the span all four charts show ----
    function paintRangeSeg() {
      let preset = false;
      el('rangeSeg').querySelectorAll('button').forEach(button => {
        const hit = Number(button.dataset.mins) * 60 * 1000 === windowMs;
        button.classList.toggle('active', hit);
        button.setAttribute('aria-pressed', hit ? 'true' : 'false');
        preset = preset || hit;
      });
      el('rangeCustom').hidden = preset;
    }
    paintRangeSeg();
    el('rangeSeg').addEventListener('click', async event => {
      const mins = event.target.dataset && event.target.dataset.mins;
      if (!mins) return;
      stopMomentum();
      windowMs = Number(mins) * 60 * 1000;
      localStorage.setItem('owletTodayWindow', mins);
      viewEnd = null;             // a new span always starts back at "now"
      paintRangeSeg();
      clearInspect();
      renderCharts();
      await ensureHistoryHours(Math.ceil(windowMs / 3600000));
      renderCharts();
    });

    // ---- zoom: wheel and pinch; the picker shows "Custom" off-preset ----
    const MIN_WINDOW_MS = 10 * 60 * 1000, MAX_WINDOW_MS = 24 * 3600 * 1000;
    function zoomAround(anchorT, factor) {
      const next = Math.min(MAX_WINDOW_MS, Math.max(MIN_WINDOW_MS, windowMs * factor));
      if (next === windowMs) return;
      const end = chartEnd();
      const frac = (end - anchorT) / windowMs;   // anchor's distance from the right edge
      windowMs = next;
      let newEnd = anchorT + frac * windowMs;
      const nowMs = Date.now();
      viewEnd = newEnd >= nowMs - 5000 ? null : Math.min(newEnd, nowMs);
      paintRangeSeg();
      renderCharts();
      ensureHistoryHours(Math.ceil((Date.now() - (chartEnd() - windowMs)) / 3600000));
    }
    let pendingZoom = null;
    function scheduleZoom(anchorT, factor) {
      if (pendingZoom) { pendingZoom.factor *= factor; pendingZoom.anchorT = anchorT; return; }
      pendingZoom = { anchorT, factor };
      requestAnimationFrame(() => { const z = pendingZoom; pendingZoom = null; zoomAround(z.anchorT, z.factor); });
    }

    // ---- inspect (tap / hold) ----------------------------------------------
    function nearestPoint(pts, t) {
      if (!pts.length) return null;
      let lo = 0, hi = pts.length - 1;
      while (hi - lo > 1) { const mid = (lo + hi) >> 1; (pts[mid].x < t ? lo = mid : hi = mid); }
      const best = Math.abs(pts[lo].x - t) <= Math.abs(pts[hi].x - t) ? pts[lo] : pts[hi];
      return Math.abs(best.x - t) <= GAP_MS ? best : null;
    }
    function inspectAt(clientX, card) {
      const rect = card.getBoundingClientRect();
      const frac = Math.min(1, Math.max(0, (clientX - rect.left) / rect.width));
      const t = chartEnd() - windowMs + frac * windowMs;
      inspectAtTime(t, frac);
    }
    function inspectAtTime(t, frac) {
      const nearEvent = careEvents.find(e => Math.abs(e.x - t) <= 3 * 60 * 1000
        && e.kind !== 'O₂ on' && e.kind !== 'O₂ off');
      const when = `at ${fmtClock(new Date(t))}`
        + (onO2At(t) ? ' · on O₂' : '')
        + (nearEvent ? ` · ⚑ ${esc(nearEvent.kind)}` : '');
      const gap = gapAt(t);
      const gapLabel = gap ? gap.label : 'no reading';
      HERO_KEYS.forEach(key => {
        if (key === 'sleep') {
          const level = sleepLevelAt(t);
          el('sleepValue').textContent = level ? SLEEP_WORD[level] : '—';
          el('sleepBand').innerHTML = level ? when : `${gapLabel} ${when}`;
        } else {
          const metric = METRICS.find(m => m.key === key);
          const p = nearestPoint(points[key], t);
          el(key + 'Value').textContent = p ? metric.fmt(p.y) : '—';
          el(key + 'Band').innerHTML = p ? when : `${gapLabel} ${when}`;
        }
        el('card-' + key).classList.add('inspecting');
        const x = el(key + 'X');
        x.hidden = false; x.style.left = (frac * 100) + '%';
      });
    }
    function clearInspect() {
      HERO_KEYS.forEach(key => {
        el(key + 'X').hidden = true;
        el('card-' + key).classList.remove('inspecting');
      });
      updateHeroLive();
    }

    // ---- gestures: hold to inspect, pull to pan ------------------------------
    let gesture = null, holdTimer = 0, momentumRaf = 0, inspectFade = 0;
    function stopMomentum() { if (momentumRaf) cancelAnimationFrame(momentumRaf); momentumRaf = 0; }

    function onDown(event, card) {
      if (event.button != null && event.button !== 0) return;
      if (o2Placing && card.id === 'o2Plot') {
        try { card.setPointerCapture(event.pointerId); } catch (error) { /* fine */ }
        gesture = { mode: 'place', card, pointerId: event.pointerId, hist: [] };
        inspectAt(event.clientX, card);
        return;
      }
      stopMomentum(); clearTimeout(inspectFade);
      try { card.setPointerCapture(event.pointerId); } catch (error) { /* keep the gesture even if capture fails */ }
      if (gesture && gesture.card === card && gesture.pointerId !== event.pointerId && gesture.mode !== 'pinch') {
        // Second finger: the gesture becomes a pinch on the shared window.
        clearTimeout(holdTimer); clearInspect();
        card.classList.remove('dragging');
        const rect = card.getBoundingClientRect();
        const firstX = gesture.hist.length ? gesture.hist[gesture.hist.length - 1].x : gesture.x0;
        const midFrac = Math.min(1, Math.max(0, (((firstX + event.clientX) / 2) - rect.left) / rect.width));
        gesture = {
          mode: 'pinch', card, rect,
          pts: new Map([[gesture.pointerId, firstX], [event.pointerId, event.clientX]]),
          startDist: Math.max(24, Math.abs(firstX - event.clientX)),
          startWindow: windowMs,
          anchorT: chartEnd() - windowMs + midFrac * windowMs,
          startEnd: chartEnd(),
        };
        return;
      }
      gesture = {
        mode: 'pending', card, pointerId: event.pointerId,
        x0: event.clientX, y0: event.clientY,
        end0: chartEnd(),
        msPerPx: windowMs / card.getBoundingClientRect().width,
        hist: [{ x: event.clientX, t: performance.now() }],
      };
      inspectAt(event.clientX, card);   // respond on pointer-down, not release
      holdTimer = setTimeout(() => { if (gesture && gesture.mode === 'pending') gesture.mode = 'scrub'; }, 220);
    }
    function onMove(event) {
      if (!gesture) return;
      if (gesture.mode === 'pinch') {
        if (!gesture.pts.has(event.pointerId)) return;
        gesture.pts.set(event.pointerId, event.clientX);
        const xs = [...gesture.pts.values()];
        if (xs.length < 2) return;
        const dist = Math.max(24, Math.abs(xs[0] - xs[1]));
        const next = Math.min(MAX_WINDOW_MS, Math.max(MIN_WINDOW_MS, gesture.startWindow * (gesture.startDist / dist)));
        const frac = (gesture.startEnd - gesture.anchorT) / gesture.startWindow;
        windowMs = next;
        const newEnd = gesture.anchorT + frac * windowMs;
        const nowMs = Date.now();
        viewEnd = newEnd >= nowMs - 5000 ? null : Math.min(newEnd, nowMs);
        paintRangeSeg();
        if (!gesture.renderQueued) {
          gesture.renderQueued = true;
          requestAnimationFrame(() => { if (gesture) gesture.renderQueued = false; renderCharts(); });
        }
        return;
      }
      const dx = event.clientX - gesture.x0, dy = event.clientY - gesture.y0;
      const now = performance.now();
      gesture.hist.push({ x: event.clientX, t: now });
      while (gesture.hist.length > 2 && now - gesture.hist[0].t > 120) gesture.hist.shift();
      if (gesture.mode === 'pending') {
        if (Math.abs(dx) > 10 && Math.abs(dx) > Math.abs(dy)) {
          gesture.mode = 'pan'; clearTimeout(holdTimer); clearInspect();
          gesture.card.classList.add('dragging');
          extendHistory();   // start pulling history the moment a drag begins
        } else if (Math.abs(dy) > 16) {   // vertical intent: hand back to the page
          clearTimeout(holdTimer); clearInspect(); gesture = null; return;
        }
      }
      if (gesture.mode === 'place') { inspectAt(event.clientX, gesture.card); return; }
      if (gesture.mode === 'scrub') { inspectAt(event.clientX, gesture.card); return; }
      if (gesture.mode === 'pan') {
        let end = gesture.end0 - dx * gesture.msPerPx;   // pull right → back in time
        const nowMs = Date.now();
        const minEnd = loadedStart() + windowMs;
        if (end > nowMs) end = nowMs + (end - nowMs) / 3;             // rubber-band at "now"
        if (end < minEnd) { end = minEnd + (end - minEnd) / 3; extendHistory(); }
        else if (end - windowMs < loadedStart() + windowMs / 4) extendHistory();
        viewEnd = end;
        applyPan();
        panFocus();
      }
    }
    function onUp(event) {
      clearTimeout(holdTimer);
      if (!gesture) return;
      if (gesture.mode === 'pinch') {
        gesture.pts.delete(event.pointerId);
        if (gesture.pts.size < 2) {
          gesture = null;
          ensureHistoryHours(Math.ceil((Date.now() - (chartEnd() - windowMs)) / 3600000));
          renderCharts();
        }
        return;
      }
      if (gesture.mode === 'place') {
        gesture = null;
        commitO2At(event.clientX);
        return;
      }
      gesture.card.classList.remove('dragging');
      const mode = gesture.mode, hist = gesture.hist, msPerPx = gesture.msPerPx;
      gesture = null;
      if (mode === 'pending') {           // plain tap: linger, then return to live
        inspectFade = setTimeout(clearInspect, 2500);
        return;
      }
      if (mode === 'scrub') { clearInspect(); return; }
      // pan: hand the finger's velocity to a decaying glide
      let vPx = 0;
      if (hist.length > 1) {
        const a = hist[0], b = hist[hist.length - 1];
        if (b.t > a.t) vPx = ((b.x - a.x) / (b.t - a.t)) * 1000;
      }
      startGlide(-vPx * msPerPx);
    }
    function startGlide(vMs) {   // velocity in window-ms per second
      const settle = () => {
        const nowMs = Date.now();
        let target = viewEnd;
        if (target != null && nowMs - target < 5000) target = null;
        if (target != null && target < loadedStart() + windowMs) target = loadedStart() + windowMs;
        viewEnd = target === null ? null : target;
        clearInspect();
        renderCharts();
      };
      if (Math.abs(vMs) < windowMs * 0.02) { settle(); return; }
      let last = performance.now();
      const step = t => {
        const dt = Math.min(64, t - last); last = t;
        vMs *= Math.pow(0.998, dt);
        let end = chartEnd() + vMs * (dt / 1000);
        const nowMs = Date.now(), minEnd = loadedStart() + windowMs;
        if (end >= nowMs) { viewEnd = null; settle(); return; }
        if (end <= minEnd) { viewEnd = minEnd; extendHistory(); settle(); return; }
        viewEnd = end;
        applyPan();
        panFocus();
        if (Math.abs(vMs) > windowMs * 0.01) momentumRaf = requestAnimationFrame(step);
        else settle();
      };
      momentumRaf = requestAnimationFrame(step);
    }
    HERO_KEYS.forEach(key => {
      const card = el(key + 'Plot');   // the chart well takes the gestures
      card.addEventListener('pointerdown', e => onDown(e, card));
      card.addEventListener('pointermove', onMove);
      card.addEventListener('pointerup', onUp);
      card.addEventListener('pointercancel', onUp);
      card.addEventListener('wheel', event => {
        event.preventDefault();
        const rect = card.getBoundingClientRect();
        const frac = Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width));
        const anchorT = chartEnd() - windowMs + frac * windowMs;
        scheduleZoom(anchorT, Math.exp(event.deltaY * 0.0016));
      }, { passive: false });
    });

    // ---- detail sheet --------------------------------------------------------
    let sheetRerender = null;   // the open sheet's own re-render, for after deletes
    function openSheet(title, bodyHtml) {
      el('sheetTitle').textContent = title;
      el('sheetBody').innerHTML = bodyHtml;
      el('sheetBackdrop').hidden = false;
    }
    function closeSheet() { el('sheetBackdrop').hidden = true; sheetRerender = null; }
    el('sheetClose').addEventListener('click', closeSheet);
    el('sheetBackdrop').addEventListener('click', event => {
      if (event.target === el('sheetBackdrop')) closeSheet();
    });
    document.addEventListener('keydown', event => { if (event.key === 'Escape') closeSheet(); });
    el('sheetBody').addEventListener('click', async event => {   // event-row delete (any sheet open)
      const ids = event.target.dataset && event.target.dataset.del;
      if (!ids) return;
      event.target.disabled = true;
      for (const id of String(ids).split(',')) {
        if (id) await fetch('/api/events/' + id, { method: 'DELETE' }).catch(() => {});
      }
      await loadEvents();
      if (sheetRerender) sheetRerender();   // sheet first — it's what the user is looking at
      refresh();
    });

    function todayWindow() {
      const start = new Date(); start.setHours(0, 0, 0, 0);
      return { start, end: new Date() };
    }

    // ---- the user's night window (default 7 PM -> 7 AM) --------------------
    let NIGHT = { start: 19 * 60, end: 7 * 60 };
    function nightWindowFrom(prefs) {
      const toMin = (value, fallback) => {
        const m = /^([01]?\\d|2[0-3]):([0-5]\\d)$/.exec(value || '');
        return m ? Number(m[1]) * 60 + Number(m[2]) : fallback;
      };
      const win = { start: toMin(prefs.night_start, 19 * 60), end: toMin(prefs.night_end, 7 * 60) };
      return win.start > win.end ? win : { start: 19 * 60, end: 7 * 60 }; // night must cross midnight
    }
    const clockOfMins = mins => {
      let h = Math.floor(mins / 60) % 24; const m = String(mins % 60).padStart(2, '0');
      const ap = h >= 12 ? 'PM' : 'AM'; h = h % 12 || 12;
      return `${h}:${m} ${ap}`;
    };

    // ---- "Ready for tonight?" — has the day built up enough sleep pressure? --
    function prepCardMarkup() {
      const now = new Date();
      const nowMins = now.getHours() * 60 + now.getMinutes();
      if (nowMins < NIGHT.end || nowMins >= NIGHT.start) return ''; // it IS night
      const dayStart = new Date(now);
      dayStart.setHours(Math.floor(NIGHT.end / 60), NIGHT.end % 60, 0, 0);
      const dayStartMs = dayStart.getTime();

      let awakeDay = 0;
      rollups.forEach(row => {
        if (Date.parse(row.bucket_start) >= dayStartMs) awakeDay += row.awake_seconds || 0;
      });

      // typical awake time by this clock hour, from the prior week
      const typicals = [];
      for (let back = 1; back <= 7; back++) {
        const from = dayStartMs - back * 86400000, to = Date.now() - back * 86400000;
        let awake = 0, signal = 0;
        rollups.forEach(row => {
          const t = Date.parse(row.bucket_start);
          if (t < from || t >= to) return;
          awake += row.awake_seconds || 0;
          signal += (row.awake_seconds || 0) + (row.sleep_seconds || 0);
        });
        if (signal >= 3 * 3600) typicals.push(awake);
      }
      const typical = typicals.length >= 3
        ? typicals.reduce((a, b) => a + b, 0) / typicals.length : null;

      const naps = I.sessions(rollups, dayStart, now)
        .filter(run => run.state === 'asleep' && run.buckets >= 3);
      const napTotal = naps.reduce((a, run) => a + (run.end - run.start) / 1000, 0);
      const lastNap = naps.length ? naps[naps.length - 1] : null;

      const feeds = careEvents.filter(e => e.kind === 'Feeding' && e.x >= dayStartMs)
        .sort((a, b) => a.x - b.x);

      let verdict = '';
      if (typical != null) {
        const delta = awakeDay - typical;
        verdict = Math.abs(delta) < 20 * 60
          ? 'Awake time is right in line with a typical day by this hour.'
          : delta > 0
            ? `That's ${fmtDur(delta)} more awake time than typical by this hour — good sleep pressure built up for tonight.`
            : `That's ${fmtDur(-delta)} less awake time than typical by this hour — bedtime may take a little longer to stick.`;
      }

      const untilNight = NIGHT.start - nowMins;
      const nightNote = untilNight <= 0 ? '' :
        untilNight < 60 ? `night starts in ${untilNight}m` : `night starts ${clockOfMins(NIGHT.start)}`;

      const napLine = naps.length
        ? `<span><b>${naps.length}</b> nap${naps.length === 1 ? '' : 's'} · <b>${fmtDur(napTotal)}</b>${lastNap ? `, last ended ${fmtClock(new Date(lastNap.end))}` : ''}</span>`
        : '<span>No naps registered yet today.</span>';
      const feedTotals = feedTotalsText(feeds);
      const feedLine = !FEEDS_ON ? ''
        : feeds.length
          ? `<span><b>${feeds.length}</b> feed${feeds.length === 1 ? '' : 's'}${feedTotals ? ' · <b>' + feedTotals + '</b>' : ''}, last at ${fmtClock(new Date(feeds[feeds.length - 1].x))}<button type="button" class="prep-feed-btn" data-open="feeds">+ feed</button></span>`
          : '<span>No feeds logged today.<button type="button" class="prep-feed-btn" data-open="feeds">+ feed</button></span>';

      return `<section class="prep card tcard">
        <div class="prep-top"><h2>Ready for tonight?</h2><span>${nightNote}</span></div>
        <div class="prep-facts">
          <span><b>${fmtDur(awakeDay)}</b> awake since ${fmtClock(dayStart)}</span>
          ${napLine}
          ${feedLine}
        </div>
        ${verdict ? `<p class="prep-note">${verdict}</p>` : ''}
      </section>`;
    }

    // ---- feeds: first-class logging with amounts ----------------------------
    const ML_PER_OZ = 29.5735;
    const fmtOz = ml => {
      const oz = Math.round((ml / ML_PER_OZ) * 2) / 2;
      return (oz % 1 ? oz.toFixed(1) : String(oz)) + ' oz';
    };
    const feedsSince = ms => careEvents
      .filter(e => e.kind === 'Feeding' && e.x >= ms)
      .sort((a, b) => a.x - b.x);
    function feedTotalsText(feeds) {
      const ml = feeds.reduce((a, f) => a + (f.amount_ml || 0), 0);
      const mins = feeds.reduce((a, f) => a + (f.duration_min || 0), 0);
      const parts = [];
      if (ml) parts.push(fmtOz(ml));
      if (mins) parts.push(Math.round(mins) + 'm nursed');
      return parts.join(' · ');
    }
    let feedType = localStorage.getItem('owletFeedType') || 'bottle';
    let feedOz = Number(localStorage.getItem('owletFeedOz')) || 4;
    let feedMin = Number(localStorage.getItem('owletFeedMin')) || 15;

    function feedWhatText(f) {
      if (f.method === 'nursing') return 'nursed' + (f.duration_min ? ' · ' + Math.round(f.duration_min) + 'm' : '');
      if (f.method === 'solids') return 'solids' + (f.note ? ' · ' + esc(f.note) : '');
      if (f.amount_ml) return 'bottle · ' + fmtOz(f.amount_ml);
      return f.note ? esc(f.note) : 'feed';
    }
    function feedHistoryRows() {
      const feeds = careEvents
        .filter(e => e.kind === 'Feeding' && e.x >= Date.now() - 3 * 86400000)
        .sort((a, b) => b.x - a.x).slice(0, 14);
      return feeds.map(f => {
        const day = new Date(f.x).toDateString() === new Date().toDateString()
          ? '' : new Date(f.x).toLocaleDateString([], { month: 'short', day: 'numeric' }) + ' ';
        return `<div class="row">
          <span>${day}${fmtClock(new Date(f.x))}</span>
          <span class="mut">${feedWhatText(f)}</span>
          <b></b>
          <button type="button" class="row-del" data-del="${f.id}" title="Remove this feed">✕</button>
        </div>`;
      }).join('');
    }

    // One 24h track with a mark per feed — bottle dot sized by ounces, nursing
    // ring sized by minutes, solids as a small square. The Huckleberry glance.
    function feedMarks(feeds, dayStartMs, x, cy) {
      const maxMl = Math.max(...feeds.map(f => f.amount_ml || 0), 4 * ML_PER_OZ);
      return feeds.map(f => {
        const cx = x(f.x).toFixed(1);
        if (f.method === 'nursing') {
          const r = (3.5 + 3.5 * Math.min(1, (f.duration_min || 10) / 30)).toFixed(1);
          return `<circle cx="${cx}" cy="${cy}" r="${r}" class="fm-nurse"><title>${fmtClock(new Date(f.x))} — ${feedWhatText(f)}</title></circle>`;
        }
        if (f.method === 'solids') {
          return `<rect x="${(x(f.x) - 3.2).toFixed(1)}" y="${cy - 3.2}" width="6.4" height="6.4" rx="1.6" class="fm-solid"><title>${fmtClock(new Date(f.x))} — ${feedWhatText(f)}</title></rect>`;
        }
        const r = (3 + 4.5 * Math.sqrt(Math.min(1, (f.amount_ml || 2 * ML_PER_OZ) / maxMl))).toFixed(1);
        return `<circle cx="${cx}" cy="${cy}" r="${r}" class="fm-bottle"><title>${fmtClock(new Date(f.x))} — ${feedWhatText(f)}</title></circle>`;
      }).join('');
    }
    function feedDayStrip(feeds) {
      if (!feeds.length) return '';
      const dayStart = todayWindow().start.getTime();
      const W = 320, H = 30;
      const x = t => ((t - dayStart) / 86400000) * W;
      const nowX = x(Date.now()).toFixed(1);
      return `<svg viewBox="0 0 ${W} ${H}" class="feed-strip" preserveAspectRatio="none">
          <line x1="0" x2="${W}" y1="15" y2="15" class="fs-track"/>
          <line x1="${nowX}" x2="${nowX}" y1="3" y2="27" class="fs-now"/>
          ${feedMarks(feeds, dayStart, x, 15)}
        </svg>
        <div class="feed-strip-axis"><span>12 AM</span><span>6 AM</span><span>12 PM</span><span>6 PM</span><span>12 AM</span></div>`;
    }

    function feedSheet() {
      sheetRerender = feedSheet;
      const today = feedsSince(todayWindow().start.getTime());
      const totals = feedTotalsText(today);
      const last = today.length ? today[today.length - 1] : null;
      const summary = today.length
        ? `<div class="o2-state"><b>${today.length} feed${today.length === 1 ? '' : 's'} today${totals ? ' · ' + totals : ''}</b>
            <span>last at ${fmtClock(new Date(last.x))} · ${fmtDur((Date.now() - last.x) / 1000)} ago</span></div>${feedDayStrip(today)}`
        : `<div class="o2-state"><b>No feeds logged today</b><span>log the first one below</span></div>`;
      const typeSeg = [['bottle', 'Bottle'], ['nursing', 'Nursing'], ['solids', 'Solids']]
        .map(([value, label]) => `<button type="button" data-ftype="${value}" class="${feedType === value ? 'sel' : ''}">${label}</button>`)
        .join('');
      let amountBlock = '';
      if (feedType === 'bottle') {
        amountBlock = `<div class="feed-amt-head"><h3 class="section-title">Amount</h3>
            <b id="feedAmtOut">${feedOz % 1 ? feedOz.toFixed(1) : feedOz} oz</b></div>
          <input type="range" id="feedAmt" class="feed-slider" min="0.5" max="8" step="0.5" value="${feedOz}" />
          <div class="feed-slider-scale"><span>½ oz</span><span>4 oz</span><span>8 oz</span></div>`;
      } else if (feedType === 'nursing') {
        amountBlock = `<div class="feed-amt-head"><h3 class="section-title">Time on</h3>
            <b id="feedAmtOut">${feedMin} min</b></div>
          <input type="range" id="feedAmt" class="feed-slider" min="1" max="45" step="1" value="${feedMin}" />
          <div class="feed-slider-scale"><span>1 min</span><span>20 min</span><span>45 min</span></div>`;
      } else {
        amountBlock = `<h3 class="section-title" style="margin:14px 0 8px">What</h3>
          <input type="text" id="feedNote" class="feed-note" placeholder="oatmeal, pears… (optional)" maxlength="60" />`;
      }
      const now = new Date();
      const timeValue = `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}`;
      const history = feedHistoryRows();
      openSheet('Feeds',
        `${summary}
        <h3 class="section-title" style="margin:14px 0 8px">Type</h3>
        <div class="flow-presets" id="feedTypes">${typeSeg}</div>
        ${amountBlock}
        <div class="feed-logrow">
          <label>at <input type="time" id="feedTime" value="${timeValue}" /></label>
          <button class="o2-action" id="feedLog" type="button">Log feed</button>
        </div>
        ${history ? '<h3 class="section-title" style="margin-top:18px">Last few days</h3>' + history : ''}
        <p class="note">Feeds feed the insights: the prep card counts the day, the evening report
          totals it, and Rhythms watches how O₂ behaves around feeds. ✕ removes a mistake.</p>`);
      el('feedTypes').addEventListener('click', event => {
        const type = event.target.dataset && event.target.dataset.ftype;
        if (!type) return;
        feedType = type;
        localStorage.setItem('owletFeedType', type);
        feedSheet();
      });
      if (el('feedAmt')) el('feedAmt').addEventListener('input', event => {
        const amt = Number(event.target.value);
        if (feedType === 'bottle') {
          feedOz = amt;
          localStorage.setItem('owletFeedOz', String(amt));
          el('feedAmtOut').textContent = (amt % 1 ? amt.toFixed(1) : amt) + ' oz';
        } else {
          feedMin = amt;
          localStorage.setItem('owletFeedMin', String(amt));
          el('feedAmtOut').textContent = amt + ' min';
        }
      });
      el('feedLog').addEventListener('click', async () => {
        el('feedLog').disabled = true;
        const timeText = el('feedTime').value || timeValue;
        const [h, m] = timeText.split(':').map(Number);
        const at = new Date(); at.setHours(h, m, 0, 0);
        if (at.getTime() > Date.now() + 5 * 60 * 1000) at.setDate(at.getDate() - 1); // 11:50 PM logged at 12:10 AM
        const payload = { kind: 'Feeding', at: at.toISOString(), method: feedType };
        if (feedType === 'bottle') payload.amount_ml = Math.round(feedOz * ML_PER_OZ * 10) / 10;
        else if (feedType === 'nursing') payload.duration_min = feedMin;
        else payload.note = (el('feedNote') && el('feedNote').value.trim()) || '';
        try {
          const response = await fetch('/api/events', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
          });
          if (!response.ok) throw new Error(String(response.status));
          await loadEvents();
          feedSheet();     // sheet reflects the new feed immediately
          refresh();       // chips/prep card catch up in the background
        } catch (error) {
          el('feedLog').disabled = false;
          alert('Could not save the feed — try again.');
        }
      });
    }

    function dipsSheet() {
      const today = todayWindow();
      const dips = [];
      let current = null;
      rollups.forEach(row => {
        const t = new Date(row.bucket_start);
        if (t < today.start) return;
        const low = row.min_oxygen_saturation != null && row.min_oxygen_saturation < O2.warn;
        if (low) {
          const lowSec = row.low_oxygen_seconds != null ? row.low_oxygen_seconds : 300;
          if (!current) current = { start: t, end: t, min: row.min_oxygen_saturation, minAt: t, lowSec: 0 };
          else {
            current.end = t;
            if (row.min_oxygen_saturation < current.min) { current.min = row.min_oxygen_saturation; current.minAt = t; }
          }
          current.lowSec += lowSec;
        } else if (current) { dips.push(current); current = null; }
      });
      if (current) dips.push(current);
      if (!dips.length) return openSheet('O₂ dips today', `<p class="note">No dips below ${O2.warn}% today. 🎉</p>`);
      const underText = d => d.lowSec < 50
        ? `~${Math.max(5, Math.round(d.lowSec / 5) * 5)}s under`
        : `${fmtDur(d.lowSec)} under`;
      const rows = dips.map(d =>
        `<div class="row">
          <span>${fmtClock(d.start)}</span>
          <span class="mut">${underText(d)}</span>
          <b class="dip-low${d.min < O2.critical ? ' deep' : ''}">low ${Math.round(d.min)}%</b>
          <a href="/data?focus=${encodeURIComponent(new Date(d.minAt.getTime() + 150000).toISOString())}&span=45&label=${encodeURIComponent('Dip to ' + Math.round(d.min) + '%')}">inspect →</a>
        </div>`).join('');
      openSheet('O₂ dips today', rows +
        `<p class="note">"Under" is the actual time below ${O2.warn}% — a momentary blip counts its seconds, not its whole 5-minute bucket. "Inspect" opens the raw data at that moment.</p>`);
    }

    // ---- supplemental O2 sheet: on/off with a flow setting --------------------
    const FLOW_PRESETS = ['1/32 L', '1/16 L', '1/8 L', '1/4 L', '1/2 L', '1 L'];
    let pendingFlow = localStorage.getItem('owletO2Flow') || '1/16 L';
    async function logO2(kind, note, atMs) {
      const payload = { kind, note: note || '' };
      if (atMs) payload.at = new Date(atMs).toISOString();
      const response = await fetch('/api/events', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (!response.ok) throw new Error(String(response.status));
      await loadEvents();
    }

    // ---- on-chart toggle: arm, tap a moment on the O2 graph, log it ----------
    let o2Placing = false;
    function paintO2Toggle() {
      const button = el('o2ToggleBtn');
      if (!button) return;
      button.textContent = 'O₂ ' + (o2State.on ? 'on' : 'off');
      button.className = 'vital-o2toggle' + (o2Placing ? ' arming' : o2State.on ? ' on' : '');
      const hint = el('placeHint');
      if (hint) {
        hint.hidden = !o2Placing;
        hint.textContent = `tap the moment O₂ went ${o2State.on ? 'off' : 'on'}`;
      }
      el('o2Plot').classList.toggle('placing', o2Placing);
    }
    function exitO2Placing() { o2Placing = false; paintO2Toggle(); }
    function commitO2At(clientX) {
      const card = el('o2Plot');
      const rect = card.getBoundingClientRect();
      const frac = Math.min(1, Math.max(0, (clientX - rect.left) / rect.width));
      let t = chartEnd() - windowMs + frac * windowMs;
      t = Math.min(t, Date.now());
      exitO2Placing();
      clearInspect();
      if (o2State.on) {
        logO2('O₂ off', '', t).catch(() => alert('Could not save — try again.'));
      } else {
        openFlowConfirm(t);
      }
    }
    function openFlowConfirm(atMs) {
      const flows = FLOW_PRESETS.map(flow =>
        `<button type="button" data-flow="${esc(flow)}" class="${flow === pendingFlow ? 'sel' : ''}">${esc(flow)}</button>`).join('');
      openSheet('O₂ on at ' + fmtClock(new Date(atMs)),
        `<h3 class="section-title" style="margin-bottom:8px">Flow</h3>
        <div class="flow-presets" id="confirmFlows">${flows}</div>
        <button class="o2-action" id="confirmO2" type="button">Log O₂ on · ${esc(pendingFlow)}</button>
        <p class="note">Logged at the moment you tapped on the chart.</p>`);
      el('confirmFlows').addEventListener('click', event => {
        const flow = event.target.dataset && event.target.dataset.flow;
        if (!flow) return;
        pendingFlow = flow;
        localStorage.setItem('owletO2Flow', flow);
        el('confirmFlows').querySelectorAll('button').forEach(b => b.classList.toggle('sel', b.dataset.flow === flow));
        el('confirmO2').textContent = 'Log O₂ on · ' + flow;
      });
      el('confirmO2').addEventListener('click', async () => {
        el('confirmO2').disabled = true;
        try { await logO2('O₂ on', pendingFlow, atMs); closeSheet(); }
        catch (error) { el('confirmO2').disabled = false; alert('Could not save — try again.'); }
      });
    }
    function o2HistoryRows() {
      const now = Date.now();
      return o2Spans.slice(-6).reverse().map(span => {
        const endText = span.end === Infinity ? 'now' : fmtClock(new Date(span.end));
        const duration = (span.end === Infinity ? now : span.end) - span.start;
        const day = new Date(span.start).toDateString() === new Date().toDateString()
          ? '' : new Date(span.start).toLocaleDateString([], { month: 'short', day: 'numeric' }) + ' ';
        const ids = [span.startId, span.endId].filter(Boolean).join(',');
        return `<div class="row">
          <span>${day}${fmtClock(new Date(span.start))} – ${endText}</span>
          <span class="mut">${esc(span.flow || '')}</span>
          <b>${fmtDur(duration / 1000)}</b>
          ${ids ? `<button type="button" class="row-del" data-del="${ids}" title="Remove this session from the log">✕</button>` : ''}
        </div>`;
      }).join('');
    }
    function o2Sheet() {
      const stateBlock = o2State.on
        ? `<div class="o2-state on"><b>On oxygen${o2State.flow ? ' · ' + esc(o2State.flow) : ''}</b>
            <span>since ${fmtClock(new Date(o2State.since))} · ${fmtDur((Date.now() - o2State.since) / 1000)}</span></div>`
        : `<div class="o2-state"><b>Off oxygen</b>
            <span>${o2State.since ? 'since ' + fmtClock(new Date(o2State.since)) : 'nothing logged yet'}</span></div>`;
      const flows = FLOW_PRESETS.map(flow =>
        `<button type="button" data-flow="${esc(flow)}" class="${flow === pendingFlow ? 'sel' : ''}">${esc(flow)}</button>`).join('');
      const action = o2State.on
        ? `<button class="o2-action stop" id="o2Action" type="button">Stop oxygen</button>`
        : `<button class="o2-action" id="o2Action" type="button">Start oxygen · ${esc(pendingFlow)}</button>`;
      const flowLabel = o2State.on ? 'Change flow (logs the change)' : 'Flow';
      const history = o2HistoryRows();
      sheetRerender = o2Sheet;
      openSheet('Supplemental O₂',
        `${stateBlock}
        <h3 class="section-title" style="margin-bottom:8px">${flowLabel}</h3>
        <div class="flow-presets" id="flowPresets">${flows}</div>
        ${action}
        ${history ? '<h3 class="section-title" style="margin-top:18px">Recent sessions</h3>' + history : ''}
        <p class="note">Every on/off is a logged event — the charts shade on-O₂ time, and Rhythms
          compares how she does on oxygen vs off. ✕ removes a mislogged session.</p>`);
      el('flowPresets').addEventListener('click', async event => {
        const flow = event.target.dataset && event.target.dataset.flow;
        if (!flow) return;
        pendingFlow = flow;
        localStorage.setItem('owletO2Flow', flow);
        if (o2State.on && flow !== o2State.flow) {
          try { await logO2('O₂ on', flow); } catch (error) { alert('Could not log the flow change.'); }
        }
        o2Sheet();
      });
      el('o2Action').addEventListener('click', async () => {
        el('o2Action').disabled = true;
        try {
          if (o2State.on) await logO2('O₂ off');
          else await logO2('O₂ on', pendingFlow);
          o2Sheet();
        } catch (error) {
          el('o2Action').disabled = false;
          alert('Could not save — try again.');
        }
      });
    }

    // ---- metric detail sheet: bigger chart, stats, dip zoom -------------------    // ---- metric detail sheet: bigger chart, stats, dip zoom -------------------
    const SPANS = [
      { label: '30m', ms: 30 * 60 * 1000 },
      { label: '1h',  ms: 3600 * 1000 },
      { label: '3h',  ms: 3 * 3600 * 1000 },
      { label: '12h', ms: 12 * 3600 * 1000 },
      { label: '24h', ms: 24 * 3600 * 1000 },
    ];
    let msState = null;   // { key, spanMs, center } — center=null means "ends now"
    function msWindow() {
      if (msState.center != null) {
        const t1 = Math.min(Date.now(), msState.center + msState.spanMs / 2);
        return { t0: t1 - msState.spanMs, t1 };
      }
      return { t0: Date.now() - msState.spanMs, t1: Date.now() };
    }
    function openMetricSheet(key) {
      msState = { key, spanMs: 3 * 3600 * 1000, center: null };
      renderMetricSheet();
    }
    async function renderMetricSheet() {
      if (!msState) return;
      const metric = METRICS.find(m => m.key === msState.key);
      await ensureHistoryHours(Math.ceil((Date.now() - msWindow().t0) / 3600000));
      const { t0, t1 } = msWindow();
      const built = buildSeriesMarkup(msState.key, t0, t1, t0, t1, 360, 150, { clip: true, thresholds: true });
      const inWin = points[msState.key].filter(p => p.x >= t0 && p.x <= t1);

      let stats = '';
      if (inWin.length) {
        const values = inWin.map(p => p.y);
        const minV = Math.min(...values), maxV = Math.max(...values);
        const avg = values.reduce((a, b) => a + b, 0) / values.length;
        const fourth = msState.key === 'o2'
          ? `<div><b>${Math.round((values.filter(v => v < 90).length / values.length) * 100)}%</b><span>time &lt;90</span></div>`
          : `<div><b>${values.length}</b><span>samples</span></div>`;
        stats = `<div class="ms-stats">
          <div><b>${metric.fmt(minV)}</b><span>min</span></div>
          <div><b>${metric.fmt(avg)}</b><span>avg</span></div>
          <div><b>${metric.fmt(maxV)}</b><span>max</span></div>
          ${fourth}</div>`;
      }

      // O₂ only: tap a dip to recenter a 30-minute window on it
      let dips = '';
      if (msState.key === 'o2') {
        const dipRuns = [];
        let run = null;
        for (const p of inWin) {
          if (p.y < 90) {
            if (!run) run = { start: p.x, end: p.x, min: p.y, count: 1 };
            else { run.end = p.x; run.min = Math.min(run.min, p.y); run.count += 1; }
          } else if (run) { if (run.count >= 2) dipRuns.push(run); run = null; }
        }
        if (run && run.count >= 2) dipRuns.push(run);
        if (dipRuns.length) {
          dips = '<div class="ms-dips">' + dipRuns.slice(-6).reverse().map(d =>
            `<button type="button" data-dip="${d.start}">
              <span>${fmtClock(new Date(d.start))} · ${fmtDur(Math.max(60, (d.end - d.start) / 1000))}</span>
              <b class="${d.min < O2.critical ? 'deep' : ''}">low ${Math.round(d.min)}%</b>
            </button>`).join('') + '</div>';
        }
      }

      const segButtons = SPANS.map(s =>
        `<button type="button" data-span="${s.ms}" class="${s.ms === msState.spanMs ? 'active' : ''}">${s.label}</button>`).join('');
      const focusIso = new Date(msState.center ?? (t1 - msState.spanMs / 2)).toISOString();
      openSheet(metric.label,
        `<div class="ms-top">
          <div class="ms-value" id="msValue">—<small> ${metric.unit}</small></div>
          <div class="ms-when" id="msWhen">touch the chart to read a moment</div>
        </div>
        <div class="ms-seg" id="msSeg">${segButtons}</div>
        <div class="ms-chartwrap" id="msChart">
          <svg viewBox="0 0 360 150" preserveAspectRatio="none" aria-hidden="true">${built.markup}</svg>
          ${built.max != null ? `<span class="ms-ylab" style="top:2px">${metric.fmt(built.max)}</span><span class="ms-ylab" style="bottom:2px">${metric.fmt(built.min)}</span>` : ''}
          <div class="gaplabels">${gapLabelSpans(t0, t1, 0.08, built.ghostYs)}</div>
          <div class="ms-xline" id="msX" hidden></div>
        </div>
        <div class="ms-axis"><span>${fmtClock(new Date(t0))}</span><span>${msState.center == null ? 'now' : fmtClock(new Date(t1))}</span></div>
        ${stats}${dips}
        <a class="ms-link" data-workbench href="/data?focus=${encodeURIComponent(focusIso)}&span=${Math.round(msState.spanMs / 60000)}">Open in the Data workbench →</a>`);

      el('msSeg').addEventListener('click', event => {
        const span = event.target.dataset && event.target.dataset.span;
        if (!span) return;
        msState.spanMs = Number(span);
        msState.center = null;   // picking a span returns to "ends now"
        renderMetricSheet();
      });
      const dipRack = el('sheetBody').querySelector('.ms-dips');
      if (dipRack) dipRack.addEventListener('click', event => {
        const target = event.target.closest('[data-dip]');
        if (!target) return;
        msState.center = Number(target.dataset.dip);
        msState.spanMs = 30 * 60 * 1000;
        renderMetricSheet();
      });
      const chart = el('msChart');
      const scrub = event => {
        const rect = chart.getBoundingClientRect();
        const frac = Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width));
        const t = t0 + frac * (t1 - t0);
        const p = nearestPoint(points[msState.key], t);
        const gap = gapAt(t);
        el('msValue').innerHTML = (p ? metric.fmt(p.y) : '—') + `<small> ${metric.unit}</small>`;
        el('msWhen').textContent = p
          ? `at ${fmtClock(new Date(t))}`
          : `${gap ? gap.label : 'no reading'} at ${fmtClock(new Date(t))}`;
        const x = el('msX'); x.hidden = false; x.style.left = (frac * 100) + '%';
      };
      chart.addEventListener('pointerdown', event => {
        try { chart.setPointerCapture(event.pointerId); } catch (error) { /* fine */ }
        scrub(event);
      });
      chart.addEventListener('pointermove', scrub);
    }
    if (el('o2ToggleBtn')) {
      el('o2ToggleBtn').addEventListener('pointerdown', event => event.stopPropagation());
      el('o2ToggleBtn').addEventListener('click', event => {
        event.stopPropagation();
        o2Placing = !o2Placing;
        paintO2Toggle();
      });
      document.addEventListener('keydown', event => {
        if (event.key === 'Escape' && o2Placing) exitO2Placing();
      });
    }
    document.querySelectorAll('.vital-expand').forEach(button => {
      button.addEventListener('pointerdown', event => event.stopPropagation());
      button.addEventListener('click', event => {
        event.stopPropagation();
        if (button.dataset.expand === 'sleep') openSleepSheet();
        else openMetricSheet(button.dataset.expand);
      });
    });

    // ---- sleep detail sheet: bigger hypnogram, split, sessions ----------------
    let sleepSheetState = null;
    function openSleepSheet() {
      sleepSheetState = { spanMs: 12 * 3600 * 1000 };
      renderSleepSheet();
    }
    async function renderSleepSheet() {
      if (!sleepSheetState) return;
      await ensureHistoryHours(Math.ceil(sleepSheetState.spanMs / 3600000));
      const t1 = Date.now(), t0 = t1 - sleepSheetState.spanMs;
      const built = buildSleepMarkup(t0, t1, t0, t1, 360, 150, { clip: true, bar: 12, idPrefix: 'hyps' });
      const inWin = sleepRuns.filter(r => r.end > t0 && r.start < t1)
        .map(r => ({ level: r.level, start: Math.max(r.start, t0), end: Math.min(r.end, t1) }));
      const sum = level => inWin.filter(r => r.level === level).reduce((a, r) => a + (r.end - r.start), 0);
      const lightMs = sum('light'), deepMs = sum('deep');
      let wakeUps = 0;
      for (let i = 1; i < inWin.length - 1; i++) {
        if (inWin[i].level === 'awake' && inWin[i - 1].level !== 'awake' && inWin[i + 1].level !== 'awake') wakeUps += 1;
      }
      const stats = `<div class="ms-stats">
        <div><b>${fmtDur((lightMs + deepMs) / 1000)}</b><span>asleep</span></div>
        <div><b>${fmtDur(lightMs / 1000)}</b><span>light</span></div>
        <div><b>${fmtDur(deepMs / 1000)}</b><span>deep</span></div>
        <div><b>${wakeUps}</b><span>wake-ups</span></div></div>`;
      const naps = I.sessions(rollups, new Date(t0), new Date(t1))
        .filter(run => run.state === 'asleep' && run.buckets >= 2);
      const sessionRows = naps.slice(-8).reverse().map(run => {
        const ongoing = (Date.now() - run.end) < 10 * 60 * 1000;
        return `<div class="row">
          <span>${fmtClock(new Date(run.start))} – ${ongoing ? 'now' : fmtClock(new Date(run.end))}</span>
          <b>${fmtDur((run.end - run.start) / 1000)}</b>
        </div>`;
      }).join('');
      const spans = [3, 12, 24].map(h =>
        `<button type="button" data-hours="${h}" class="${h * 3600 * 1000 === sleepSheetState.spanMs ? 'active' : ''}">${h}h</button>`).join('');
      openSheet('Sleep',
        `<div class="ms-top">
          <div class="ms-value" id="msValue">—</div>
          <div class="ms-when" id="msWhen">touch the chart to read a moment</div>
        </div>
        <div class="ms-seg" id="msSeg">${spans}</div>
        <div class="ms-chartwrap" id="msChart">
          <svg viewBox="0 0 360 150" preserveAspectRatio="none" aria-hidden="true">${built.markup}</svg>
          <span class="ms-ylab" style="top:calc(20% - 6px)">awake</span>
          <span class="ms-ylab" style="top:calc(50% - 6px)">light</span>
          <span class="ms-ylab" style="top:calc(80% - 6px)">deep</span>
          <div class="gaplabels">${gapLabelSpans(t0, t1, 0.08, built.ghostYs)}</div>
          <div class="ms-xline" id="msX" hidden></div>
        </div>
        <div class="ms-axis"><span>${fmtClock(new Date(t0))}</span><span>now</span></div>
        ${stats}
        ${sessionRows ? '<h3 class="section-title" style="margin-top:16px">Sleep sessions</h3>' + sessionRows : ''}
        <a class="ms-link" href="/night">Tonight's full report →</a>`);
      el('msSeg').addEventListener('click', event => {
        const hours = event.target.dataset && event.target.dataset.hours;
        if (!hours) return;
        sleepSheetState.spanMs = Number(hours) * 3600 * 1000;
        renderSleepSheet();
      });
      const chart = el('msChart');
      const scrub = event => {
        const rect = chart.getBoundingClientRect();
        const frac = Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width));
        const t = t0 + frac * (t1 - t0);
        const level = sleepLevelAt(t);
        const gap = gapAt(t);
        el('msValue').textContent = level ? SLEEP_WORD[level] : '—';
        el('msWhen').textContent = level
          ? `at ${fmtClock(new Date(t))}`
          : `${gap ? gap.label : 'no reading'} at ${fmtClock(new Date(t))}`;
        const x = el('msX'); x.hidden = false; x.style.left = (frac * 100) + '%';
      };
      chart.addEventListener('pointerdown', event => {
        try { chart.setPointerCapture(event.pointerId); } catch (error) { /* fine */ }
        scrub(event);
      });
      chart.addEventListener('pointermove', scrub);
    }

    // ---- live vitals + narrative --------------------------------------------
    const bandText = (band, unit) => band
      ? `typical ${baselineState} range ${Math.round(band.low)}–${Math.round(band.high)}${unit}`
      : 'building her baseline — needs a couple of days';

    // Icons are drawn for this project (plan D6): a warning triangle and a clock.
    const ICON_WARN = '<svg class="ic" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 4 2.500 20h19z"/><path d="M12 10v4.500M12 17.200v.300"/></svg>';
    const ICON_CLOCK = '<svg class="ic" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8.500"/><path d="M12 7.500V12l3 2"/></svg>';
    // Only touch the DOM when the markup changed: the page re-renders every
    // poll, and rewriting unchanged nodes would drop focus and hover state.
    function setHtml(node, html) {
      if (node && node.__html !== html) { node.__html = html; node.innerHTML = html; }
    }
    // A reading older than this is "last known", not live (same 2 minutes the
    // charts use to call a gap "collector off").
    const STALE_MS = 2 * 60 * 1000;
    const VITAL_STATES = ['critical', 'low', 'out', 'stale'];
    function setVitalState(key, state) {
      const classes = el('card-' + key).classList;
      VITAL_STATES.forEach(name => classes.toggle(name, name === state));
    }
    // The badge beside the big number: the tier in words plus an icon, so the
    // number's colour is never the only signal.
    function setTier(key, kind, text) {
      const node = el(key + 'Tier');
      const sig = kind ? kind + '|' + text : '';
      if (node.dataset.sig !== sig) {
        node.dataset.sig = sig;
        node.className = 'tier' + (kind === 'critical' ? ' critical' : '');
        node.innerHTML = kind ? (kind === 'stale' ? ICON_CLOCK : ICON_WARN) + `<span>${esc(text)}</span>` : '';
      }
      node.hidden = !kind;
    }
    const agoText = ms => `${fmtDur(ms / 1000)} ago`;

    let sleepSessionText = 'watching for the first session';
    let tierState = { o2: '', hr: '', stale: false };
    function updateHeroLive() {
      if (el('card-o2').classList.contains('inspecting')) return;
      el('o2Value').textContent = latest.o2 != null ? Math.round(latest.o2) : '—';
      el('hrValue').textContent = latest.hr != null ? Math.round(latest.hr) : '—';
      el('sleepValue').textContent = latest.sleepLevel ? SLEEP_WORD[latest.sleepLevel] : '—';
      el('moveValue').textContent = latest.move != null ? moveWord(latest.move) : '—';
      el('o2Band').textContent = bandText(bands.o2, '%');
      el('hrBand').textContent = bandText(bands.hr, '');
      el('sleepBand').textContent = sleepSessionText;
      el('moveBand').textContent = 'wiggle level · spikes while awake are normal';
      // Peaceful - Wiggling meter: calm fills the first quarter, stirring the
      // middle, active the rest (same cut-offs as the word beside the title).
      const mark = el('moveMark');
      mark.hidden = latest.move == null;
      if (latest.move != null) {
        const m = latest.move;
        const frac = m < 4 ? (m / 4) * 0.25
          : m < 20 ? 0.25 + ((m - 4) / 16) * 0.35
          : 0.6 + Math.min(1, (m - 20) / 80) * 0.4;
        mark.style.left = (Math.max(0.01, Math.min(0.99, frac)) * 100).toFixed(1) + '%';
      }
      el('moveMeter').setAttribute('aria-label', latest.move != null
        ? `Movement level: ${moveWord(latest.move)}, on a scale from peaceful to wiggling`
        : 'Movement level: no reading');
      // Compare against the ROUNDED bounds — the label prints "91–98%", so a
      // reading of 98 must count as inside even when the true band is 91.2–97.6.
      // Oxygen only warns on the LOW side: above the typical range is fine
      // (higher SpO₂ is good), so a high reading must never tint amber.
      const o2Out = bands.o2 && latest.o2 != null
        && Math.round(latest.o2) < Math.round(bands.o2.low);
      const hrLow = bands.hr && latest.hr != null && Math.round(latest.hr) < Math.round(bands.hr.low);
      const hrHigh = bands.hr && latest.hr != null && Math.round(latest.hr) > Math.round(bands.hr.high);
      const stale = latest.ageMs != null && latest.ageMs > STALE_MS;
      const o2Tier = latest.o2 == null ? ''
        : latest.o2 < O2.critical ? 'critical'
        : latest.o2 < O2.warn ? 'low'
        : o2Out ? 'out' : '';
      const hrTier = hrLow ? 'below' : hrHigh ? 'above' : '';
      tierState = { o2: o2Tier, hr: hrTier, stale };
      // Stale numbers go grey and say "last known"; a low tier keeps its colour.
      setVitalState('o2', o2Tier || (stale && latest.o2 != null ? 'stale' : ''));
      setVitalState('hr', hrTier ? 'out' : (stale && latest.hr != null ? 'stale' : ''));
      const o2Word = { critical: 'Very low', low: 'Low', out: 'Below usual' }[o2Tier] || '';
      const hrWord = hrTier ? (hrTier === 'above' ? 'Above usual' : 'Below usual') : '';
      const known = stale ? `last known, ${agoText(latest.ageMs)}` : '';
      const join = (word, tail) => word && tail ? `${word} · ${tail}` : (word || capitalized(tail));
      setTier('o2', latest.o2 == null ? '' : o2Tier === 'critical' ? 'critical' : o2Tier ? 'warn' : stale ? 'stale' : '',
        join(o2Word, known));
      setTier('hr', latest.hr == null ? '' : hrTier ? 'warn' : stale ? 'stale' : '', join(hrWord, known));
    }

    // "Last night" follows the Tonight page's window: the most recent night
    // stays current until the next one begins.
    let rollupsLoaded = false;
    function storyMarkup() {
      const now = new Date();
      const anchor = new Date(now);
      if (now.getHours() * 60 + now.getMinutes() < NIGHT.start) anchor.setDate(anchor.getDate() - 1);
      const start = new Date(anchor);
      start.setHours(Math.floor(NIGHT.start / 60), NIGHT.start % 60, 0, 0);
      const end = new Date(start);
      end.setDate(end.getDate() + 1);
      end.setHours(Math.floor(NIGHT.end / 60), NIGHT.end % 60, 0, 0);
      const inProgress = now < end;
      let sleep = 0, minO2 = null, dips = 0, inDip = false;
      rollups.forEach(row => {
        const t = new Date(row.bucket_start);
        if (t < start || t >= end) return;
        sleep += row.sleep_seconds || 0;
        if (row.min_oxygen_saturation != null && (minO2 === null || row.min_oxygen_saturation < minO2)) minO2 = row.min_oxygen_saturation;
        const low = row.min_oxygen_saturation != null && row.min_oxygen_saturation < O2.warn;
        if (low && !inDip) dips += 1;
        inDip = low;
      });
      const wakes = I.wakeUps(rollups, start, end).length;
      const moon = '<div class="moon" aria-hidden="true"></div>';
      const link = `<a href="/night">${inProgress ? "Tonight's report" : "Last night's report"} →</a>`;
      if (!rollupsLoaded) return `${moon}<p>Reading last night…</p>${link}`;
      if (!sleep && minO2 === null) {
        return moon + (inProgress
          ? `<p>Tonight's story starts with the first sleep</p><small>Night began ${fmtClock(start)}.</small>`
          : `<p>Nothing recorded last night</p><small>No sock readings between ${fmtClock(start)} and ${fmtClock(end)}.</small>`) + link;
      }
      const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;
      return moon
        + (inProgress ? `<p>Asleep <b>${fmtDur(sleep)}</b> so far tonight</p>` : `<p>Slept <b>${fmtDur(sleep)}</b> last night</p>`)
        + `<small>${plural(wakes, 'wake-up')} · ${plural(dips, 'oxygen dip')}${minO2 !== null ? ` · lowest ${Math.round(minO2)}%` : ''}</small>`
        + link;
    }

    function render(widget) {
      const readings = liveReadings;
      const latestRow = readings[readings.length - 1] || null;
      const offline = latestRow ? isOffline(latestRow) : true;
      const stateText = latestRow && !offline ? stateLabel(latestRow.sleep_state) : null;

      // --- current sleep/wake session ------------------------------------
      const today = todayWindow();
      const runs = I.sessions(rollups, new Date(Date.now() - 18 * 3600 * 1000), new Date());
      const currentRun = runs.length ? runs[runs.length - 1] : null;
      const sleepRunsToday = I.sessions(rollups, today.start, today.end)
        .filter(run => run.state === 'asleep' && run.buckets >= 2).length;

      // --- baselines -------------------------------------------------------
      // The live reading decides asleep vs awake for the baseline bands —
      // rollup sessions lag up to a bucket and load on their own cycle, so
      // two open windows could disagree ("typical awake" on one device,
      // "typical asleep" on the other). Rollups are only the fallback while
      // the sock is off.
      const liveLevel = latestRow && !offline
        ? ({ '8': 'asleep', '15': 'asleep' }[String(latestRow.sleep_state)] || 'awake')
        : null;
      baselineState = liveLevel || (currentRun && currentRun.state === 'asleep' ? 'asleep' : 'awake');
      bands.hr = I.baselineBand(rollups, 'hr', baselineState);
      bands.o2 = I.baselineBand(rollups, 'o2', baselineState);
      latest.hr = latestRow && !offline ? latestRow.heart_rate : null;
      latest.o2 = latestRow && !offline ? latestRow.oxygen_saturation : null;
      latest.temp = latestRow && !offline && latestRow.skin_temperature > 0 ? latestRow.skin_temperature : null;
      latest.sleepLevel = latestRow && !offline
        ? ({ '8': 'light', '15': 'deep' }[String(latestRow.sleep_state)] || 'awake')
        : null;
      const liveRun = currentStateSince(currentRun);
      sleepSessionText = liveRun
        ? `${liveRun.asleep ? 'asleep' : 'awake'} for ${fmtDur((Date.now() - liveRun.start) / 1000)}`
        : currentRun && currentRun.state !== 'nodata'
          ? `${currentRun.state === 'asleep' ? 'asleep' : 'awake'} for ${fmtDur((Date.now() - currentRun.start) / 1000)}`
          : 'settling in';
      const recent = readings.slice(-24).filter(r => !isOffline(r));
      latest.move = !offline && recent.length
        ? recent.reduce((a, r) => a + (r.movement || 0), 0) / recent.length
        : null;
      latest.offline = offline;
      latest.at = latestRow ? Date.parse(latestRow.recorded_at) : null;
      latest.ageMs = latest.at != null ? Date.now() - latest.at : null;
      const temps = rollups.map(r => r.avg_skin_temperature).filter(v => v != null);
      tempRangeText = temps.length
        ? `${Math.min(...temps).toFixed(1)}–${Math.max(...temps).toFixed(1)}° over recent days`
        : '';
      updateHeroLive();

      // --- today so far ----------------------------------------------------
      let sleepToday = 0, dipsToday = 0, inDip = false;
      rollups.forEach(row => {
        const t = new Date(row.bucket_start);
        if (t < today.start) return;
        sleepToday += row.sleep_seconds || 0;
        const low = row.min_oxygen_saturation != null && row.min_oxygen_saturation < O2.warn;
        if (low && !inDip) dipsToday += 1;
        inDip = low;
      });

      // --- bedtime context (evenings) ---------------------------------------
      let bedtimeLine = '';
      const nowDate = new Date();
      const nowDateMins = nowDate.getHours() * 60 + nowDate.getMinutes();
      if (nowDateMins >= NIGHT.start || nowDateMins < NIGHT.end) {
        // Hunt for bedtime from an hour before the configured night start, so
        // an early night still registers; typicals use the same search start.
        const searchMin = Math.max(0, NIGHT.start - 60);
        const nightStart = new Date(nowDate); if (nowDateMins < NIGHT.start) nightStart.setDate(nightStart.getDate() - 1);
        nightStart.setHours(Math.floor(searchMin / 60), searchMin % 60, 0, 0);
        const bed = I.bedtime(rollups, nightStart, new Date());
        const typical = I.typicalBedtimeMinutes(rollups, 7, searchMin);
        if (bed && typical) {
          let minutes = bed.getHours() * 60 + bed.getMinutes(); if (minutes < 720) minutes += 1440;
          const delta = Math.round(minutes - typical.mean);
          if (Math.abs(delta) >= 10) bedtimeLine = ` Fell asleep ${fmtClock(bed)} — about ${Math.abs(delta)} min ${delta < 0 ? 'earlier' : 'later'} than usual.`;
          else bedtimeLine = ` Fell asleep ${fmtClock(bed)}, right on schedule.`;
        } else if (bed) bedtimeLine = ` Fell asleep ${fmtClock(bed)}.`;
      }

      // --- status sentence: plain language first; alert states lead ----------
      const name = esc(capitalized(deviceName));
      const stale = tierState.stale;
      const sockGap = gaps.filter(g => g.kind === 'sock').pop();
      const sockLabel = offline ? (sockGap ? sockGap.label : 'sock off') : '';
      const shortState = liveRun ? (liveRun.asleep ? 'is asleep' : 'is awake')
        : currentRun && currentRun.state === 'asleep' ? 'is asleep'
        : latest.sleepLevel === 'light' || latest.sleepLevel === 'deep' ? 'is asleep'
        : 'is awake';
      let headline, detail, mood = '';
      if (!latestRow) {
        const seen = widget && widget.updated_at ? new Date(widget.updated_at) : null;
        headline = 'No readings in the last hour.';
        mood = 'is-stale';
        detail = (seen ? `The last reading arrived <b>${fmtTick(seen)}</b>. ` : '')
          + 'The collector may be stopped, or the sock may be off or charging.';
      } else if (stale) {
        headline = `No new readings for ${fmtDur(latest.ageMs / 1000)}.`;
        mood = 'is-stale';
        detail = `The numbers below are the last known values, from <b>${fmtClock(new Date(latest.at))}</b>. Check that the collector is running.`;
      } else if (offline) {
        headline = sockLabel === 'charging' ? `${name}'s sock is charging.`
          : sockLabel === 'disconnected' ? `${name}'s sock has lost its signal.`
          : `${name}'s sock isn't reporting.`;
        detail = sockLabel === 'charging' ? 'No readings arrive while it charges.'
          : sockLabel === 'disconnected' ? 'The sock is not connected, so no readings are arriving.'
          : 'It may be off, or not on properly.';
      } else {
        const sessionText = liveRun
          ? `${liveRun.asleep ? 'asleep' : 'awake'} for <b>${fmtDur((Date.now() - liveRun.start) / 1000)}</b>`
          : currentRun && currentRun.state !== 'nodata'
            ? `${currentRun.state === 'asleep' ? 'asleep' : 'awake'} for <b>${fmtDur((Date.now() - currentRun.start) / 1000)}</b>`
            : 'settling in';
        const nth = sleepRunsToday ? ` — sleep #${sleepRunsToday} today` : '';
        const o2Note = o2State.on ? ` On <b>${o2State.flow ? esc(o2State.flow) + ' ' : ''}O₂</b>.` : '';
        const lead = stateText && stateText !== 'awake' ? capitalized(stateText) + ', ' : '';
        const line = `${lead}${lead ? sessionText : capitalized(sessionText)}${nth}.${o2Note}${bedtimeLine}`;
        if (tierState.o2 === 'critical') {
          headline = 'Oxygen is very low right now.';
          mood = 'is-critical';
          detail = `<b>${Math.round(latest.o2)}%</b>, below your red threshold of ${O2.critical}%. ${name} ${shortState}. ${line}`;
        } else if (tierState.o2 === 'low') {
          headline = 'Oxygen is low right now.';
          mood = 'is-low';
          detail = `<b>${Math.round(latest.o2)}%</b>, below your amber threshold of ${O2.warn}%. ${name} ${shortState}. ${line}`;
        } else {
          headline = `${capitalized(deviceName)} ${shortState}.`;
          detail = line;
        }
      }
      if (el('statusLine').textContent !== headline) el('statusLine').textContent = headline;
      setHtml(el('statusDetail'), detail);
      el('hello').className = 'hello' + (mood ? ' ' + mood : '');

      // --- the note under "Live health readings" ----------------------------
      let note, noteTone = '';
      if (!latestRow) { note = ICON_CLOCK + 'No readings in the last hour'; noteTone = 'warn'; }
      else if (stale) {
        note = ICON_CLOCK + `No new readings for ${fmtDur(latest.ageMs / 1000)} · showing the last known values`;
        noteTone = 'warn';
      } else if (offline) {
        if (sockLabel === 'charging') note = 'Sock charging · not monitoring';
        else {
          note = ICON_WARN + (sockLabel === 'disconnected' ? 'Sock signal lost · no readings' : 'Sock off · no readings');
          noteTone = 'warn';
        }
      } else if (tierState.o2 === 'critical') {
        note = ICON_WARN + `Oxygen is below your red threshold (${O2.critical}%)`; noteTone = 'bad';
      } else if (tierState.o2 === 'low') {
        note = ICON_WARN + `Oxygen is below your amber threshold (${O2.warn}%)`; noteTone = 'warn';
      } else if (tierState.o2 === 'out') {
        note = ICON_WARN + `Oxygen is below the usual ${baselineState} range`; noteTone = 'warn';
      } else if (tierState.hr) {
        note = ICON_WARN + `Pulse is ${tierState.hr} the usual ${baselineState} range`; noteTone = 'warn';
      } else {
        note = bands.o2 || bands.hr ? 'All readings are in the usual range' : 'Oxygen is above your alert thresholds';
      }
      updateTimescale();   // the live chip must not say "live" over stale numbers
      setHtml(el('liveNote'), note);
      el('liveNote').className = 'note' + (noteTone ? ' ' + noteTone : '');

      // --- sleep summary + last night's story ---------------------------------
      el('sleepToday').textContent = rollupsLoaded ? fmtDur(sleepToday) : '—';
      el('wakingsToday').textContent = rollupsLoaded ? I.wakeUps(rollups, today.start, today.end).length : '—';
      setHtml(el('storyCard'), storyMarkup());

      // --- sock status list ----------------------------------------------------
      let sockSub, sockTone = '';
      if (!latestRow) { sockSub = ICON_CLOCK + 'No recent readings'; sockTone = 'warn'; }
      else if (stale) { sockSub = ICON_CLOCK + `No new data for ${fmtDur(latest.ageMs / 1000)}`; sockTone = 'warn'; }
      else if (offline) {
        if (sockLabel === 'charging') sockSub = 'Charging · not monitoring';
        else { sockSub = ICON_WARN + (sockLabel === 'disconnected' ? 'Signal lost' : 'Sock off'); sockTone = 'warn'; }
      } else sockSub = 'Receiving readings';
      setHtml(el('sockSub'), sockSub);
      el('sockSub').className = 'note' + (sockTone ? ' ' + sockTone : '');

      const rowBtn = (open, term, text, inner) =>
        `<button type="button" class="row-btn" data-open="${open}" aria-label="${esc(term)}: ${esc(text)}. Open details">${inner}<span class="chev" aria-hidden="true">›</span></button>`;
      const row = (term, dd, cls) => `<div class="lrow"><dt>${term}</dt><dd${cls ? ` class="${cls}"` : ''}>${dd}</dd></div>`;
      const battery = widget && typeof widget.battery === 'number' ? Math.round(widget.battery) + '%' : '—';
      const battMins = widget && typeof widget.battery_minutes === 'number' ? widget.battery_minutes : 0;
      const batteryTail = widget && widget.charging ? ' · charging'
        : battMins >= 90 ? ` · about ${Math.round(battMins / 60)}h`
        : battMins > 0 ? ` · about ${Math.round(battMins)}m` : '';
      const tempText = latest.temp != null ? latest.temp.toFixed(1) + '°' : '—';
      const o2Text = o2State.on
        ? `${o2State.flow ? esc(o2State.flow) : 'On'} since ${fmtClock(new Date(o2State.since))}`
        : 'Off';
      const o2Sub = o2State.on ? `on for ${fmtDur((Date.now() - o2State.since) / 1000)}`
        : o2State.since ? `since ${fmtClock(new Date(o2State.since))}` : 'nothing logged yet';
      let feedRow = '';
      if (FEEDS_ON) {
        const feedsToday = feedsSince(today.start.getTime());
        const totals = feedTotalsText(feedsToday);
        const lastFeed = feedsToday.length ? feedsToday[feedsToday.length - 1] : null;
        const feedSub = lastFeed
          ? `${totals ? totals + ' · ' : ''}last ${fmtDur((Date.now() - lastFeed.x) / 1000)} ago`
          : 'log a feed';
        feedRow = row('Feeds today', rowBtn('feeds', 'Feeds today', String(feedsToday.length),
          `<span class="val">${feedsToday.length}</span>`) + `<small>${feedSub}</small>`);
      }
      setHtml(el('sockRows'),
        row('Sock battery', battery + batteryTail)
        + row('Skin temperature', rowBtn('temp', 'Skin temperature', tempText, `<span class="val">${tempText}</span>`)
          + (tempRangeText ? `<small>${tempRangeText}</small>` : ''))
        + row('Supplemental O₂', rowBtn('o2', 'Supplemental oxygen', o2Text, `<span class="val">${o2Text}</span>`)
          + `<small>${o2Sub}</small>`, o2State.on ? 'o2-on' : '')
        + row('O₂ dips today', rowBtn('dips', 'Oxygen dips today', String(dipsToday),
          `<span class="val${dipsToday ? ' warn' : ''}">${dipsToday}</span>`)
          + (dipsToday ? `<small>below ${O2.warn}%</small>` : ''))
        + feedRow);

      // --- below the grid: tonight prep (daytime only) and the Data door -------
      setHtml(el('belowHero'), `${prepCardMarkup()}
        <a class="door card tcard" href="/data"><b>The raw data →</b>
          <span>Full charts, tables, exports: every reading behind these numbers.</span></a>`);
    }

    // One listener for everything that opens a detail sheet (rows, sessions, + feed).
    const OPENERS = {
      sleep: () => openSleepSheet(), temp: () => openMetricSheet('temp'),
      dips: () => dipsSheet(), o2: () => o2Sheet(), feeds: () => feedSheet(),
    };
    el('todayGrid').addEventListener('click', event => {
      const target = event.target.closest('[data-open]');
      if (target && OPENERS[target.dataset.open]) OPENERS[target.dataset.open]();
    });

    // ---- "This PC's alerts" row: desktop app only ----------------------------
    // Same detection, command and rules as the shell's armed chip (shell.py):
    // Tauri command `armed_state` -> { armed, problems: [{ code, text }] },
    // polled every 5 s. It reports on THIS PC's alert path, never on the baby.
    // Hidden in a browser (no bridge) and until the command has answered once;
    // after that a shell that goes quiet shows "unknown" rather than a stale
    // "Armed". Words always carry the state; colour only repeats it.
    (function () {
      const bridge = window.__TAURI__;
      const invoke = (bridge && bridge.core && typeof bridge.core.invoke === 'function') ? bridge.core.invoke : null;
      if (!invoke) return;
      const POLL_MS = 5000, QUIET_MS = 15000;
      const rowNode = el('armedRow'), valueNode = el('armedValue');
      let everOk = false, lastOkAt = 0, pendingSince = 0;
      function show(kind, text, title) {
        rowNode.hidden = false;
        if (valueNode.textContent !== text) valueNode.textContent = text;   // unchanged: don't re-announce
        valueNode.className = kind;
        rowNode.title = title;
      }
      function paint(state) {
        if (!state || typeof state.armed !== 'boolean') return false;
        const problems = (Array.isArray(state.problems) ? state.problems : [])
          .filter(p => p && typeof p.text === 'string' && p.text);
        // not_collecting (sock off, no account linked, remote server) is "not
        // monitoring", not a fault. Anything else is a fault and wins.
        const faults = problems.filter(p => p.code !== 'not_collecting');
        const all = problems.map(p => p.text).join(' · ');
        if (faults.length || (!state.armed && !problems.length)) {
          show('warn', faults.length ? faults[0].text : 'Not armed',
            'This PC may not sound an alert' + (all ? ': ' + all : '.'));
        } else if (problems.length) {
          show('info', 'Not monitoring · ' + problems[0].text, 'Not monitoring on this PC: ' + all);
        } else {
          show('ok', 'Armed', "This PC's alert path is working. It says nothing about your baby, "
            + 'only that this PC can raise an alert.');
        }
        return true;
      }
      function settle(ok) {
        pendingSince = 0;
        if (ok) { everOk = true; lastOkAt = Date.now(); }
        else if (!everOk) rowNode.hidden = true;
      }
      function tick() {
        const now = Date.now();
        if (everOk && now - lastOkAt > QUIET_MS) {
          show('warn', 'Alert status unknown',
            'The desktop app has stopped reporting whether this PC can sound an alert.');
        }
        if (pendingSince && now - pendingSince < QUIET_MS) return;   // still waiting
        pendingSince = now;
        let call;
        try { call = Promise.resolve(invoke('armed_state')); }
        catch (error) { call = Promise.reject(error); }
        call.then(state => settle(paint(state)), () => settle(false));
      }
      tick();
      setInterval(tick, POLL_MS);
    })();

    let lastWidget = null;
    async function refresh() {
      try {
        const [readings, widget] = await Promise.all([
          fetch('/api/readings?hours=1&limit=2000').then(r => r.json()),
          fetch('/api/widget?hours=24').then(r => r.json())
        ]);
        liveReadings = readings;
        lastWidget = widget;
        rebuildPoints();
        render(widget);
        // a panned window stays frozen; live windows track the newest data
        if (!gesture && !momentumRaf && viewEnd == null) renderCharts();
      } catch (error) {
        // The fetch failed. Keep the last readings, but re-judge them against
        // the clock so old numbers turn into "last known" instead of looking live.
        try {
          rebuildPoints();
          render(lastWidget);
          if (!gesture && !momentumRaf && viewEnd == null) renderCharts();
        } catch (inner) { /* keep last render */ }
      }
    }

    async function boot() {
      // Rollups (baselines/sessions) can take seconds to compute server-side;
      // render live vitals immediately and fold history in when it arrives.
      const rollupsReady = fetch('/api/rollups?bucket=5m&hours=192&limit=100000')
        .then(r => r.json())
        .then(data => { rollups = data.rollups || []; rollupsLoaded = true; })
        .catch(() => {});
      loadEvents();
      try {
        const [devices, accounts] = await Promise.all([
          fetch('/api/devices').then(r => r.json()),
          fetch('/api/accounts').then(r => r.json())
        ]);
        const account = (accounts.accounts || [])[0];
        const prefs = (account && account.dashboard_preferences) || {};
        if (prefs.baby_name) deviceName = prefs.baby_name;
        O2 = window.owletO2(prefs);
        NIGHT = nightWindowFrom(prefs);
        DISPLAY = {
          o2: prefs.o2_display === 'smoothed' ? 'smoothed' : 'raw',
          move: prefs.movement_source === 'bucket' ? 'bucket' : 'raw'
        };
        FEEDS_ON = prefs.feed_tracking !== false;
        if (account && account.poll_interval_seconds) pollSeconds = account.poll_interval_seconds;
      } catch (error) {
        el('statusLine').textContent = 'Could not load readings — is the collector running?';
        el('hello').className = 'hello is-stale';
        el('liveNote').innerHTML = ICON_WARN + 'Could not load readings';
        el('liveNote').className = 'note warn';
        el('sockSub').textContent = 'Unknown';
        return;
      }
      await refresh();
      ensureHistoryHours(Math.max(3, Math.ceil(windowMs / 3600000)));
      rollupsReady.then(refresh);
      setInterval(refresh, Math.max(5, pollSeconds) * 1000);
      setInterval(async () => {  // refresh baselines occasionally
        const data = await fetch('/api/rollups?bucket=5m&hours=192&limit=100000').then(r => r.json()).catch(() => null);
        if (data) rollups = data.rollups || rollups;
      }, 5 * 60 * 1000);
      setInterval(loadEvents, 5 * 60 * 1000);
      document.addEventListener('owlet:prefs-changed', event => {
        const prefs = (event.detail && event.detail.dashboard_preferences) || null;
        if (!prefs) return;
        O2 = window.owletO2(prefs);
        DISPLAY = {
          o2: prefs.o2_display === 'smoothed' ? 'smoothed' : 'raw',
          move: prefs.movement_source === 'bucket' ? 'bucket' : 'raw'
        };
        const feedsWere = FEEDS_ON;
        FEEDS_ON = prefs.feed_tracking !== false;
        rebuildPoints();
        render(lastWidget);     // retint the vitals against the new O₂ tiers at once
        if (!gesture) renderCharts();
        if (feedsWere !== FEEDS_ON) refresh();   // add/remove the feeds chip now
      });
    }
    boot();
  </script>"""


def render_now_page() -> str:
    return render_shell(
        view="now",
        title="Today",
        head=NOW_HEAD,
        body=NOW_BODY,
        scripts=NOW_SCRIPTS,
    )
