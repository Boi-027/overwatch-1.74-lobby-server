'use strict';
import {api, selectAccount} from './dashboard-api.mjs';

(() => {
  const $ = (selector, scope = document) => scope.querySelector(selector);
  const $$ = (selector, scope = document) => [...scope.querySelectorAll(selector)];
  const number = value => new Intl.NumberFormat('en-US').format(Number(value) || 0);
  const titles = {overview: 'Profile', events: 'Events', boxes: 'Loot boxes', shop: 'Collection', sessions: 'Server'};
  const currencies = {
    credits: {label: 'Credits', unit: 'credits', symbol: 'C', className: 'credits-icon'},
    comp_points: {label: 'Competitive points', unit: 'competitive points', symbol: '◆', className: 'comp-icon'},
    league_tokens: {label: 'League tokens', unit: 'league tokens', symbol: 'L', className: 'league-icon'}
  };
  const typeLabels = {Skin: 'Skin', WeaponSkin: 'Weapon skin', Icon: 'Player icon', Spray: 'Spray', Emote: 'Emote', VictoryPose: 'Victory pose', VoiceLine: 'Voice line', HighlightIntro: 'Highlight intro', PortraitFrame: 'Portrait frame'};
  const rarityLabels = {Common: 'Common', Rare: 'Rare', Epic: 'Epic', Legendary: 'Legendary'};
  let state = null;
  let account = '';
  let view = 'overview';
  let selectedEvent = '';
  let eventDirty = false;
  let selectedBox = null;
  let stateRequest = 0;
  let shopRequest = 0;
  let shopPage = 1;
  let shopPages = 1;
  let shopItems = [];
  let shopLoading = false;
  let searchTimer;
  let purchase = null;
  let purchaseBusy = false;
  let eventBusy = false;
  const profileDirty = new Set();
  const challengeDirty = new Set();
  const busyForms = new Set();
  let catalogSignature = '';
  const profileForm = $('#profile-form');
  const challengeForm = $('#challenge-form');
  const boxForm = $('#box-form');
  const filters = $('#shop-filters');

  function node(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = String(text);
    return element;
  }
  function setText(selector, text) { const element = $(selector); if (element) element.textContent = text; }
  function showError(selector, message) { const element = $(selector); element.textContent = message || ''; element.hidden = !message; }
  function toast(message, error = false) {
    const element = node('div', `toast${error ? ' error' : ''}`);
    element.append(node('span', '', message));
    const close = node('button', '', '×');
    close.type = 'button'; close.setAttribute('aria-label', 'Close');
    close.addEventListener('click', () => element.remove());
    element.append(close); $('#toasts').append(element);
    setTimeout(() => element.remove(), error ? 10000 : 6500);
  }
  function syncStatus(connected) {
    const status = $('#sync-status'); status.replaceChildren();
    status.append(node('span', `status-dot ${connected ? 'online' : 'offline'}`), document.createTextNode(connected ? 'Updated' : 'No connection'));
    $('#sidebar-status-dot').className = `status-dot ${connected ? 'online' : 'offline'}`;
    setText('#sidebar-status', connected ? 'Server is up' : 'No connection');
    if (!connected) setText('#sessions-status', 'Data may be old');
  }
  function currentAccount() {
    if (!account || !state) throw new Error('The profile is still loading.');
    return account;
  }
  function formValue(field) {
    if (field.type === 'checkbox') return field.checked;
    if (field.type === 'number') return Number(field.value);
    return field.value;
  }
  function fillForm(form, profile, dirty, force = false) {
    for (const field of $$('input[name], select[name]', form)) {
      if (!force && dirty.has(field.name)) continue;
      const value = profile[field.name];
      if (field.type === 'checkbox') field.checked = Boolean(value);
      else field.value = value == null ? '' : String(value);
    }
  }
  function lockForm(form, busy) {
    if (busy) busyForms.add(form); else busyForms.delete(form);
    for (const control of $$('button, input, select', form)) control.disabled = busy;
    form.setAttribute('aria-busy', String(busy));
    updateButtons();
  }
  function updateButtons() {
    for (const form of [profileForm, challengeForm, boxForm]) {
      if (!busyForms.has(form)) for (const control of $$('input, select, button[type=button]', form)) control.disabled = !state;
    }
    $('button[type=submit]', profileForm).disabled = !state || !profileDirty.size || busyForms.has(profileForm);
    $('button[type=submit]', challengeForm).disabled = !state || !challengeDirty.size || busyForms.has(challengeForm);
    $('button[type=submit]', boxForm).disabled = !state || selectedBox === null || busyForms.has(boxForm);
    $('#apply-event').disabled = !state || !eventDirty || eventBusy;
    $('#profile-draft').hidden = !profileDirty.size;
    $('#apply-event').textContent = eventBusy ? 'Applying…' : 'Apply event';
  }
  // Level rules: the card shows a level from 1 to 100; every 100 levels add a star (up to 5), and
  // every 600 levels move up a tier (Bronze to Diamond). The total level is what the server keeps.
  // Frames come from the server's table (unlock level -> frame GUID); their GUIDs are not in order.
  const FRAME_TIERS = ['Bronze', 'Silver', 'Gold', 'Platinum', 'Diamond'];
  const MAX_LEVEL = 3000;
  function levelParts(total) {
    const done = Math.min(Math.max(1, total), MAX_LEVEL) - 1;
    return {tier: Math.floor(done / 600), stars: Math.floor(done % 600 / 100), shown: done % 100 + 1};
  }
  function frameForLevel(total) {
    let frame = null;
    for (const row of state?.catalogs?.frames || []) if (row.level <= total) frame = row;
    return frame;
  }
  function frameName(level) {
    const parts = levelParts(level);
    return `${FRAME_TIERS[parts.tier]}${parts.stars ? ' ' + '★'.repeat(parts.stars) : ''}`;
  }
  function renderLevelFrame() {
    const total = Math.max(1, Number(profileForm.elements.level.value) || 1);
    // A changed level brings back the level's frame when saved, so the preview shows that one.
    const chosenGuid = profileDirty.has('level') ? null : state?.profile?.frame;
    const chosen = chosenGuid && (state?.catalogs?.frames || []).find(row => row.guid === chosenGuid);
    const frame = chosen || frameForLevel(total);
    $('#level-frame-img').src = frame ? `/assets/previews/${frame.guid.slice(2).toUpperCase()}.webp` : '';
    setText('#level-frame-number', String(levelParts(total).shown));
    const name = frame ? frameName(Math.max(1, frame.level)) : '—';
    setText('#level-frame-text', chosen ? `Total level ${total}. Frame: ${name}, chosen in Collection → Portrait frames.` : `Total level ${total}. Frame: ${name}.`);
  }
  function syncLevelControls() {
    const parts = levelParts(Number(profileForm.elements.level.value) || 1);
    $('#level-shown').value = String(parts.shown);
    $('#level-tier').value = String(parts.tier);
    $('#level-stars').value = String(parts.stars);
    renderLevelFrame();
  }
  function setLevelFromControls() {
    const shown = Math.min(100, Math.max(1, Number($('#level-shown').value) || 1));
    const field = profileForm.elements.level;
    field.value = String(Number($('#level-tier').value) * 600 + Number($('#level-stars').value) * 100 + shown);
    field.dispatchEvent(new Event('input', {bubbles: true}));
    renderLevelFrame();
  }
  function markDirty(event, set, profile) {
    const field = event.target;
    if (!field.name || !profile) return;
    const original = field.type === 'checkbox' ? Boolean(profile[field.name]) : field.type === 'number' ? Number(profile[field.name]) : String(profile[field.name] ?? '');
    if (formValue(field) === original) set.delete(field.name); else set.add(field.name);
    updateButtons();
  }
  function createOptions(select, rows, valueKey, nameKey, firstLabel, firstValue = '') {
    const previous = select.value;
    select.replaceChildren();
    if (firstLabel) { const option = node('option', '', firstLabel); option.value = firstValue; select.append(option); }
    for (const row of rows) { const option = node('option', '', row[nameKey] || row.name || row[valueKey]); option.value = row[valueKey]; select.append(option); }
    if ([...select.options].some(option => option.value === previous)) select.value = previous;
  }
  function renderCatalogs(force) {
    const catalogs = state.catalogs;
    const signature = JSON.stringify(catalogs);
    if (signature === catalogSignature && !force) return;
    catalogSignature = signature;
    const lobbyHeroSelect = $('[name=lobby_hero]', profileForm);
    const previousLobbyHero = lobbyHeroSelect.value;
    createOptions(lobbyHeroSelect, catalogs.heroes || [], 'name', 'name', 'Random hero', 'random');
    const noHeroOption = node('option', '', 'No hero'); noHeroOption.value = 'none'; lobbyHeroSelect.append(noHeroOption);
    const npcGroup = node('optgroup'); npcGroup.label = 'PvE characters (bind pose)';
    for (const name of catalogs.npcs || []) { const option = node('option', '', name); option.value = name; npcGroup.append(option); }
    lobbyHeroSelect.append(npcGroup);
    if ([...lobbyHeroSelect.options].some(option => option.value === previousLobbyHero)) lobbyHeroSelect.value = previousLobbyHero;
    createOptions($('[name=challenge]', challengeForm), catalogs.challenges || [], 'id', 'title', 'No challenge');
    createOptions($('[name=hero]', filters), catalogs.heroes || [], 'name', 'name', 'All heroes');
    const boxTypes = catalogs.box_types || [];
    if (selectedBox === null || !boxTypes.some(box => String(box.id) === String(selectedBox))) selectedBox = boxTypes[0]?.id ?? null;
  }
  function renderAccounts() {
    const select = $('#account-select');
    const rows = state.accounts || [];
    select.replaceChildren();
    for (const row of rows) { const option = node('option', '', row.name); option.value = row.name; select.append(option); }
    if (!rows.some(row => row.name === account)) { const option = node('option', '', account); option.value = account; select.append(option); }
    select.value = account; select.disabled = false;
    for (const selector of ['#profile-save-account', '#event-account', '#challenge-account', '#box-account', '#shop-account']) setText(selector, `Account: ${account}`);
  }
  function renderOverview() {
    const profile = state.profile;
    const activeAccount = (state.accounts || []).find(item => item.name === account);
    const event = state.catalogs.events.find(item => item.id === (profile.events || [])[0]);
    const hero = state.catalogs.heroes.find(item => item.name === profile.lobby_hero);
    setText('#overview-title', profile.player_name || account);
    const connection = $('#profile-connection');
    connection.textContent = activeAccount?.online ? 'Connected' : 'Not connected';
    connection.className = `connection-tag${activeAccount?.online ? ' online' : ''}`;
    setText('#profile-level', `Level ${number(profile.level)}`);
    setText('#lobby-description', activeAccount?.online ? 'The game is connected.' : 'Start the game to connect it.');
    for (const [selector, field] of [['#credits-balance','credits'], ['#comp-balance','comp_points'], ['#league-balance','league_tokens'], ['#shop-credits','credits'], ['#shop-comp','comp_points'], ['#shop-league','league_tokens']]) setText(selector, number(profile[field]));
    setText('#overview-event', event?.label || (profile.events?.[0] || 'No event'));
    setText('#overview-hero', profile.lobby_hero === 'random' ? 'Random hero' : profile.lobby_hero === 'none' ? 'No hero' : hero?.name || profile.lobby_hero || '—');
    const challenge = state.catalogs.challenges.find(item => item.id === profile.challenge);
    setText('#overview-challenge', challenge?.title || profile.challenge || 'No challenge');
    setText('#overview-date', profile.server_date === 'now' || !profile.server_date ? 'Current date' : profile.server_date);
    setText('#overview-boxes', number(profile.loot_boxes_count));
    setText('#boxes-total', number(profile.loot_boxes_count));
    setText('#box-nav-count', number(profile.loot_boxes_count));
    setText('#overview-unlocked', number(profile.unlocked_count));
    setText('#overview-opened', number(profile.stats?.boxes_opened));
    $('#event-nav-dot').hidden = !(profile.events || []).length;
    setText('#events-current', `Now: ${event?.label || (profile.events?.[0] || 'no event')}`);
  }
  function renderEvents() {
    const focusedEvent = document.activeElement?.hasAttribute('data-event') ? document.activeElement.dataset.event : null;
    const container = $('#event-grid'); container.replaceChildren();
    const events = [{id: '', label: 'No event', description: 'Standard lobby look', category: 'default'}, ...state.catalogs.events];
    const categories = {default: 'Standard lobby', seasonal: 'Seasonal event', special: 'Special look', challenge: 'Hero challenge', league: 'Overwatch League'};
    for (const event of events) {
      const button = node('button', `event-card${selectedEvent === event.id ? ' selected' : ''}`);
      button.dataset.event = event.id;
      button.type = 'button'; button.setAttribute('aria-pressed', String(selectedEvent === event.id));
      button.append(node('strong', '', event.label), node('span', '', event.description || 'Lobby look'), node('span', 'event-category', categories[event.category] || 'Game event'));
      button.addEventListener('click', () => {
        selectedEvent = event.id; eventDirty = selectedEvent !== (state.profile.events?.[0] || '');
        showError('#event-error', ''); renderEvents(); updateButtons();
      });
      container.append(button);
    }
    if (focusedEvent !== null) $$('[data-event]', container).find(button => button.dataset.event === focusedEvent)?.focus({preventScroll: true});
    const selected = events.find(item => item.id === selectedEvent);
    setText('#event-detail-title', selected?.label || 'Unknown event');
    setText('#event-detail-description', selected?.description || 'Pick an event from the list.');
    const status = $('#event-scene-status');
    const scene = selected?.scene_status;
    const labels = {verified: 'Tested', limited: 'Partly works'};
    status.textContent = !selectedEvent ? 'Standard look' : labels[scene] || 'Not tested';
    status.className = `scene-status ${scene === 'verified' ? 'verified' : 'unverified'}`;
    setText('#event-scene-note', selected?.scene_note || (!selectedEvent ? 'Default lobby.' : 'Check it in the game.'));
    updateButtons();
  }
  function renderChallengeRewards() {
    const challenge = state?.catalogs.challenges.find(item => item.id === $('[name=challenge]', challengeForm).value);
    const container = $('#challenge-rewards'); container.replaceChildren();
    if (!challenge) { container.append(node('span', 'muted', 'No challenge.')); return; }
    if (!challenge.rewards?.length) { container.append(node('span', 'muted', 'No rewards listed.')); return; }
    for (const reward of challenge.rewards) {
      const chip = node('span', 'reward-chip', reward.name || reward.guid || 'Item');
      chip.append(node('small', '', typeLabels[reward.type] || reward.type || ''));
      container.append(chip);
    }
  }
  // Overwatch loot box: dark isometric box, glowing event-colored trim, OW logo on the lid.
  // Event colour comes from --box-accent (set per box by boxAccent()).
  const BOX_COLORS = {
    'standard': '#3d7bd6', 'summer games': '#3fb64f', 'halloween': '#f07a1e', 'halloween terror': '#f07a1e',
    'winter wonderland': '#49b6e6', 'lunar new year': '#e0453f', 'archives': '#18b0a6',
    'anniversary': '#b070e0', 'golden': '#e8b13a', 'legendary': '#e8952f',
    'legendary anniversary': '#e8952f', 'wrecking ball': '#e8952f', 'ram': '#c58a4a',
  };
  function boxAccent(box) {
    const key = String(box.label || box.name || '').toLowerCase().trim();
    return BOX_COLORS[key] || Object.entries(BOX_COLORS).find(([k]) => key.includes(k))?.[1] || '#5b82a8';
  }
  // Real rendered loot-box photos (ow174/dashboard/web/assets/boxes/). Missing files fall back to the SVG.
  const BOX_IMAGES = {
    'standard': 'standard.png', 'summer games': 'summer.png', 'halloween': 'halloween.png', 'halloween terror': 'halloween.png',
    'winter wonderland': 'winter.png', 'lunar new year': 'lunar.png', 'archives': 'archives.png', 'anniversary': 'anniversary.png',
    'golden': 'golden.png', 'legendary': 'legendary.png', 'legendary anniversary': 'legendary_anniv.png',
    'ram': 'ham.png', 'wrecking ball': 'ham.png',
  };
  function boxImageFile(box) {
    const key = String(box.label || box.name || '').toLowerCase().trim();
    return BOX_IMAGES[key] || Object.entries(BOX_IMAGES).find(([k]) => key.includes(k))?.[1] || null;
  }
  function fillBoxArt(wrap, box) {
    wrap.replaceChildren();
    wrap.style.setProperty('--box-accent', boxAccent(box));
    const file = box && boxImageFile(box);
    if (file) {
      const img = document.createElement('img'); img.className = 'box-photo'; img.alt = ''; img.loading = 'lazy';
      img.src = '/assets/boxes/' + encodeURIComponent(file);
      img.addEventListener('error', () => { img.remove(); wrap.append(boxGraphic()); });
      wrap.append(img);
    } else {
      wrap.append(boxGraphic());
    }
  }
  // The item's picture when the dashboard has one, else its initials.
  function itemMonogram(item) {
    return node('span', 'item-monogram', (item.hero || item.name || 'OW').replace(/[^\p{L}\p{N} ]/gu, '').split(/\s+/).map(word => word[0]).join('').slice(0, 2));
  }
  function fillItemArt(art, item) {
    if (!item.preview) { art.append(itemMonogram(item)); return; }
    const img = document.createElement('img');
    img.className = 'item-photo'; img.alt = ''; img.loading = 'lazy'; img.src = item.preview;
    img.addEventListener('error', () => img.replaceWith(itemMonogram(item)));
    art.append(img);
  }
  function boxGraphic() {
    const ns = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(ns, 'svg'); svg.setAttribute('viewBox', '0 0 100 98'); svg.setAttribute('aria-hidden', 'true');
    svg.innerHTML = `
      <defs>
        <linearGradient id="lbLid" x1="0" y1="0" x2="0.9" y2="1"><stop offset="0" stop-color="#4b535d"/><stop offset="1" stop-color="#363c45"/></linearGradient>
        <linearGradient id="lbL" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#252b32"/><stop offset="1" stop-color="#151a1f"/></linearGradient>
        <linearGradient id="lbR" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#333a42"/><stop offset="1" stop-color="#1e242a"/></linearGradient>
        <filter id="lbGlow" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="1.7" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
      </defs>
      <ellipse cx="50" cy="90" rx="30" ry="5" fill="#000" opacity="0.20"/>
      <path d="M50 54 13 33v34l37 21z" fill="url(#lbL)" stroke="#0d1114" stroke-width="1"/>
      <path d="M50 54 87 33v34L50 88z" fill="url(#lbR)" stroke="#0d1114" stroke-width="1"/>
      <path d="M50 12 87 33 50 54 13 33z" fill="url(#lbLid)" stroke="#0d1114" stroke-width="1"/>
      <path class="lb-band" d="M13 41 50 62 87 41" fill="none"/>
      <g class="lb-trim" filter="url(#lbGlow)"><path d="M13 33 50 54 87 33" fill="none"/><path d="M50 54v34" fill="none"/></g>
      <g class="lb-logo" transform="translate(50 31.5) scale(0.30) translate(-32 -31)">
        <path class="lb-arc" d="M12 14a27 27 0 0 1 40 0" fill="none"/>
        <path class="lb-arc" d="M8 20a27 27 0 1 0 48 0" fill="none"/>
        <path class="lb-fig" d="m31 21-3 16-14 12h11l7-8 7 8h11L36 37l-3-16Z"/>
      </g>`;
    return svg;
  }
  function boxCount(type) { return state.profile.box_counts?.find(item => String(item.type ?? item.id) === String(type))?.count || 0; }
  function renderBoxes() {
    const focusedBox = document.activeElement?.hasAttribute('data-box') ? document.activeElement.dataset.box : null;
    const container = $('#box-grid'); container.replaceChildren();
    for (const box of state.catalogs.box_types) {
      const button = node('button', `box-card${String(selectedBox) === String(box.id) ? ' selected' : ''}`);
      button.dataset.box = String(box.id);
      button.type = 'button'; button.setAttribute('aria-pressed', String(String(selectedBox) === String(box.id)));
      const art = node('div', 'box-art'); fillBoxArt(art, box);
      const copy = node('div', 'box-card-copy'); copy.append(node('strong', '', box.label || box.name), node('span', '', box.name || ''));
      const count = node('div', 'box-inventory'); count.append(node('span', '', 'In stock'), node('b', '', number(boxCount(box.id)))); copy.append(count);
      button.append(art, copy); button.addEventListener('click', () => { selectedBox = box.id; showError('#box-error', ''); renderBoxes(); }); container.append(button);
    }
    if (focusedBox !== null) $$('[data-box]', container).find(button => button.dataset.box === focusedBox)?.focus({preventScroll: true});
    const selected = state.catalogs.box_types.find(box => String(box.id) === String(selectedBox));
    if (selected) fillBoxArt($('#selected-box-art'), selected);
    else $('#selected-box-art').replaceChildren(boxGraphic());
    setText('#selected-box-name', selected?.label || selected?.name || 'No box types');
    setText('#selected-box-inventory', selected ? `In stock: ${number(boxCount(selected.id))}` : 'No boxes');
    updateButtons();
  }
  function duration(value) {
    let seconds = Math.max(0, Math.floor(Number(value) || 0));
    const hours = Math.floor(seconds / 3600); seconds %= 3600;
    const minutes = Math.floor(seconds / 60);
    return hours ? `${hours} h ${minutes} min` : minutes ? `${minutes} min ${seconds % 60} s` : `${seconds} s`;
  }
  // Log the game in as another account: select it for the next login, show it here, reconnect the game.
  async function playAs(name) {
    try {
      await selectAccount(name);
      await api('/api/reconnect', {});
      const select = $('#account-select');
      if (select.value !== name) { select.value = name; select.dispatchEvent(new Event('change')); }
      toast(`The game logs in again as ${name}.`);
    } catch (error) { toast(error.message, true); }
  }
  function renderSessions() {
    const server = state.server;
    setText('#sidebar-address', `${server.host}:${server.port}`);
    setText('#server-endpoint', `${server.host}:${server.port}`);
    setText('#server-clients', number(server.connected_clients));
    setText('#server-uptime', duration(server.uptime_seconds));
    setText('#sessions-status', 'Server is up');
    setText('#matchmaking-note', server.matchmaking_supported ? 'Matchmaking is on.' : 'Lobby only. Matchmaking does not work.');
    const accounts = $('#sessions-accounts'); accounts.replaceChildren();
    for (const item of state.accounts || []) {
      const row = node('tr'); row.append(node('td', '', item.name));
      const connection = node('td'); const status = node('span', `table-status${item.online ? ' online' : ''}`);
      status.append(node('span', `status-dot${item.online ? ' online' : ''}`), document.createTextNode(item.online ? 'Connected' : 'Not connected'));
      connection.append(status); row.append(connection);
      const current = node('td'); current.append(node('span', item.selected ? 'selected-badge' : 'muted', item.selected ? 'Selected' : '—')); row.append(current);
      const play = node('td'); const button = node('button', 'button secondary small', item.online ? 'Playing' : 'Play as');
      button.type = 'button'; button.disabled = item.online; button.addEventListener('click', () => playAs(item.name));
      play.append(button); row.append(play); accounts.append(row);
    }
    setText('#accounts-count', `${number((state.accounts || []).length)} accounts`);
    const instances = Array.isArray(server.game_instances) ? server.game_instances : [];
    setText('#instances-count', `${number(instances.length)} processes`);
    const container = $('#instances-list'); container.replaceChildren();
    for (const instance of instances) {
      const row = node('div', 'instance-row'); row.append(node('span', 'instance-icon', '▣'));
      const copy = node('div');
      if (typeof instance === 'string') copy.append(node('h3', '', instance));
      else {
        const activityLabels = {practice: 'Practice session', queue: 'Local server queue'};
        copy.append(node('h3', '', instance.name || activityLabels[instance.activity] || instance.executable || instance.exe || 'Local session'));
        const details = [];
        if (instance.pid !== undefined) details.push(`PID ${instance.pid}`);
        if (instance.player || instance.account) details.push(`Account: ${instance.player || instance.account}`);
        if (instance.host && instance.port !== undefined) details.push(`UDP ${instance.host}:${instance.port}`);
        if (instance.mode) details.push(`Mode: ${instance.mode}`);
        if (instance.path) details.push(instance.path);
        if (details.length) copy.append(node('p', '', details.join(' · ')));
        if (instance.protocol_ready === false) copy.append(node('p', 'instance-protocol', 'Waiting for the game protocol'));
        else if (instance.protocol_ready === true) copy.append(node('p', 'instance-protocol', 'Game protocol active'));
        if (instance.packets_received !== undefined || instance.bytes_received !== undefined) {
          const counters = [];
          if (instance.packets_received !== undefined) counters.push(`Packets received: ${number(instance.packets_received)}`);
          if (instance.bytes_received !== undefined) counters.push(`Bytes received: ${number(instance.bytes_received)}`);
          copy.append(node('p', '', counters.join(' · ')));
        }
      }
      row.append(copy);
      if (typeof instance === 'object' && (instance.state || instance.status)) {
        const states = {listening: 'UDP port listening', stopped: 'Stopped', failed: 'Process error'};
        row.append(node('span', 'current-pill', states[instance.state] || instance.state || instance.status));
      }
      container.append(row);
    }
    $('#instances-empty').hidden = instances.length > 0;
    setText('#last-updated', `Updated at ${new Date().toLocaleTimeString('en-US', {hour: '2-digit', minute: '2-digit', second: '2-digit'})}`);
  }
  async function refreshState(force = false) {
    const request = ++stateRequest;
    const requestedAccount = account;
    $('#refresh-button').disabled = true;
    try {
      const result = await api(`/api/state${requestedAccount ? `?account=${encodeURIComponent(requestedAccount)}` : ''}`);
      if (request !== stateRequest || requestedAccount !== account) return;
      state = result;
      state.catalogs = {heroes: [], events: [], box_types: [], challenges: [], ...result.catalogs};
      if (!account) account = result.accounts?.find(item => item.selected)?.name || result.profile.player_name;
      renderCatalogs(force); renderAccounts(); renderOverview();
      if (force) { profileDirty.clear(); challengeDirty.clear(); eventDirty = false; }
      fillForm(profileForm, state.profile, profileDirty, force);
      setText('#battle-tag-hint', `BattleTag for friends: ${result.battle_tag || '—'}`);
      syncLevelControls();
      fillForm(challengeForm, state.profile, challengeDirty, force);
      if (!eventDirty || force) selectedEvent = state.profile.events?.[0] || '';
      renderEvents(); renderBoxes(); renderChallengeRewards(); renderSessions(); updateButtons();
      showError('#global-error', ''); syncStatus(true);
      if (force || view === 'shop') await loadShop();
    } catch (error) {
      if (request !== stateRequest) return;
      showError('#global-error', error.message); syncStatus(false);
    } finally { if (request === stateRequest) $('#refresh-button').disabled = false; }
  }
  function priceText(item) { return `${number(item.price)} ${currencies[item.currency]?.unit || item.currency || ''}`; }
  function currencyIcon(currency) {
    const definition = currencies[currency];
    const icon = node('span', `currency-icon ${definition?.className || ''}`, definition?.symbol || '?');
    icon.setAttribute('aria-hidden', 'true'); return icon;
  }
  // What the player can do with an item: pick a frame, buy it, unlock it for free, or take it back.
  function itemActions(item) {
    const footer = node('div', 'item-footer');
    if (item.frame) { const button = frameButton(item); button.type = 'button'; footer.append(button); return footer; }
    if (item.owned) {
      if (item.removable) {
        const remove = node('button', 'button secondary small', 'Remove'); remove.type = 'button';
        remove.addEventListener('click', () => grantSkin(item, true)); footer.append(remove);
      } else footer.append(node('span', 'item-unavailable', 'In your collection'));
      return footer;
    }
    if (item.purchasable) {
      const price = node('span', 'item-price'); price.setAttribute('aria-label', priceText(item)); price.title = currencies[item.currency]?.label || item.currency;
      price.append(currencyIcon(item.currency), document.createTextNode(number(item.price)));
      const buy = node('button', 'button primary small', 'Buy'); buy.type = 'button'; buy.setAttribute('aria-label', `Buy ${item.name} for ${priceText(item)}`);
      buy.dataset.purchaseGuid = item.guid;
      buy.addEventListener('click', () => openPurchase(item)); footer.append(price, buy);
    }
    const unlock = node('button', item.purchasable ? 'button secondary small' : 'button primary small', 'Unlock'); unlock.type = 'button';
    unlock.title = 'Add it for free'; unlock.addEventListener('click', () => grantSkin(item, false)); footer.append(unlock);
    return footer;
  }
  function renderShop(result) {
    const focusedGuid = document.activeElement?.dataset.purchaseGuid;
    shopItems = result.items || [];
    shopPage = Number(result.page) || 1; shopPages = Math.max(1, Number(result.pages) || 1);
    setText('#shop-result-count', `Items found: ${number(result.total)}`);
    const container = $('#shop-grid'); container.replaceChildren();
    for (const item of shopItems) {
      const card = node('article', `item-card rarity-${String(item.rarity || '').toLowerCase()}`);
      const art = node('div', 'item-art');
      art.append(node('span', 'item-type', typeLabels[item.type] || item.type || 'Item'));
      fillItemArt(art, item);
      if (item.owned || item.in_use) art.append(node('span', 'owned-badge', item.frame ? '✓ In use' : '✓ In collection'));
      const copy = node('div', 'item-copy'); copy.append(node('span', 'item-hero', item.frame ? 'Portrait frame' : item.hero || 'Generic item'), node('h3', '', item.name), node('span', 'item-rarity', rarityLabels[item.rarity] || item.rarity || ''));
      card.append(art, copy, itemActions(item)); container.append(card);
    }
    if (focusedGuid) $$('[data-purchase-guid]', container).find(button => button.dataset.purchaseGuid === focusedGuid)?.focus({preventScroll: true});
    $('#shop-empty').hidden = shopItems.length > 0;
    setText('#page-status', `Page ${number(shopPage)} of ${number(shopPages)}`);
    updatePagination();
  }
  function updatePagination() {
    $('#page-prev').disabled = shopLoading || shopPage <= 1;
    $('#page-next').disabled = shopLoading || shopPage >= shopPages;
  }
  async function loadShop() {
    if (!account || !state) return;
    const request = ++shopRequest; const requestedAccount = account;
    shopLoading = true; $('#shop-loading').hidden = false; updatePagination();
    const params = new URLSearchParams({account, kind: filters.elements.kind.value, q: filters.elements.q.value.trim(), hero: filters.elements.hero.value, currency: filters.elements.currency.value, owl: filters.elements.owl.checked ? '1' : '', page: String(shopPage)});
    try {
      const result = await api(`/api/collection?${params}`);
      if (request !== shopRequest || requestedAccount !== account) return;
      renderShop(result); showError('#shop-error', '');
    } catch (error) { if (request === shopRequest && requestedAccount === account) showError('#shop-error', error.message); }
    finally { if (request === shopRequest) { shopLoading = false; $('#shop-loading').hidden = true; updatePagination(); } }
  }
  // A chosen frame can go back to the one the level gives; the level's own frame is just in use.
  function frameButton(item) {
    if (item.chosen) {
      const button = node('button', 'button secondary small', 'Use level frame');
      button.addEventListener('click', () => setFrame(item, null));
      return button;
    }
    const button = node('button', item.in_use ? 'button secondary small' : 'button primary small', item.in_use ? 'In use' : 'Use');
    button.disabled = item.in_use;
    button.addEventListener('click', () => setFrame(item, item.guid));
    return button;
  }
  async function setFrame(item, guid) {
    try {
      const result = await api('/api/set_frame', {account, guid});
      if (result.status !== 'ok') throw new Error('The change was not saved.');
      toast(guid ? `Frame: ${item.name}` : 'Back to the level frame');
      await refreshState();  // the profile (frame, counts) changed too
    } catch (error) { showError('#shop-error', error.message); toast(error.message, true); }
  }
  async function grantSkin(item, revoke) {
    try {
      const payload = revoke ? {account, guid: item.guid, revoke: true} : {account, guid: item.guid};
      const result = await api('/api/grant_skin', payload);
      if (result.status !== 'ok') throw new Error('The change was not saved.');
      if (result.profile) { state.profile = result.profile; renderOverview(); }
      toast(revoke ? `Removed ${item.name}` : `Granted ${item.name}`);
      await refreshState();  // the profile (frame, counts) changed too
    } catch (error) { showError('#shop-error', error.message); toast(error.message, true); }
  }
  function openPurchase(item) {
    if (!state) return;
    const balance = Number(state.profile[item.currency]) || 0;
    purchase = {item, account, balance};
    setText('#purchase-title', item.name);
    setText('#purchase-detail', `${item.hero || 'Generic item'} · ${typeLabels[item.type] || item.type || 'Item'}`);
    setText('#purchase-account', account); setText('#purchase-price', priceText(item));
    setText('#purchase-remaining', `${number(balance - Number(item.price))} ${currencies[item.currency]?.unit || item.currency}`);
    showError('#purchase-error', balance < Number(item.price) ? `Not enough funds. Balance: ${number(balance)} ${currencies[item.currency]?.unit || item.currency}.` : '');
    $('#purchase-confirm').disabled = balance < Number(item.price);
    $('#purchase-dialog').showModal();
  }
  function setView() {
    const requested = location.hash.slice(1);
    view = Object.hasOwn(titles, requested) ? requested : 'overview';
    for (const section of $$('.view')) section.hidden = section.id !== `view-${view}`;
    for (const link of $$('.nav-link')) {
      const active = link.dataset.view === view; link.classList.toggle('active', active);
      if (active) link.setAttribute('aria-current', 'page'); else link.removeAttribute('aria-current');
    }
    setText('#view-context', titles[view]); document.title = `${titles[view]} · Overwatch 1.74`;
    if (view === 'shop') loadShop();
  }
  async function saveForm(event, form, dirty, errorSelector, successMessage) {
    event.preventDefault(); if (busyForms.has(form) || !dirty.size || !form.reportValidity()) return;
    const targetAccount = currentAccount();
    const changed = {};
    for (const name of dirty) changed[name] = formValue(form.elements[name]);
    lockForm(form, true); showError(errorSelector, '');
    const button = $('button[type=submit]', form); const label = button.textContent; button.textContent = 'Saving…';
    try {
      const result = await api('/api/update_profile', {account: targetAccount, ...changed});
      if (result.status !== 'ok') throw new Error('The profile was not saved.');
      if (account === targetAccount) { for (const name of Object.keys(changed)) dirty.delete(name); if (result.profile) state.profile = result.profile; await refreshState(); }
      toast(successMessage);
    } catch (error) { if (account === targetAccount) showError(errorSelector, error.message); else toast(error.message, true); }
    finally { button.textContent = label; lockForm(form, false); }
  }
  profileForm.addEventListener('input', event => markDirty(event, profileDirty, state?.profile));
  $('#level-shown').addEventListener('input', setLevelFromControls);
  for (const id of ['#level-tier', '#level-stars']) $(id).addEventListener('change', setLevelFromControls);
  profileForm.addEventListener('change', event => markDirty(event, profileDirty, state?.profile));
  profileForm.addEventListener('submit', event => saveForm(event, profileForm, profileDirty, '#profile-error', 'Profile saved.'));
  challengeForm.addEventListener('input', event => markDirty(event, challengeDirty, state?.profile));
  challengeForm.addEventListener('change', event => { markDirty(event, challengeDirty, state?.profile); renderChallengeRewards(); });
  challengeForm.addEventListener('submit', event => saveForm(event, challengeForm, challengeDirty, '#challenge-error', 'Challenge saved.'));
  $('#apply-event').addEventListener('click', async () => {
    if (!eventDirty || eventBusy || !state) return;
    const targetAccount = currentAccount(); const eventId = selectedEvent;
    const event = state.catalogs.events.find(item => item.id === eventId);
    eventBusy = true; updateButtons(); showError('#event-error', '');
    try {
      const result = await api('/api/update_profile', {account: targetAccount, events: eventId ? [eventId] : []});
      if (result.status !== 'ok') throw new Error('The event was not saved.');
      if (account === targetAccount) { eventDirty = false; if (result.profile) state.profile = result.profile; await refreshState(); }
      toast(`Event set: ${event?.label || 'none'}.`);
    } catch (error) { if (account === targetAccount) showError('#event-error', error.message); else toast(error.message, true); }
    finally { eventBusy = false; updateButtons(); }
  });
  function setBoxCount(value) {
    $('#box-count').value = Math.max(1, Math.min(100, Number(value) || 1));
    for (const button of $$('[data-count]')) button.classList.toggle('active', Number(button.dataset.count) === Number($('#box-count').value));
  }
  $('#count-minus').addEventListener('click', () => setBoxCount(Number($('#box-count').value) - 1));
  $('#count-plus').addEventListener('click', () => setBoxCount(Number($('#box-count').value) + 1));
  for (const button of $$('[data-count]')) button.addEventListener('click', () => setBoxCount(button.dataset.count));
  $('#box-count').addEventListener('input', () => { for (const button of $$('[data-count]')) button.classList.toggle('active', Number(button.dataset.count) === Number($('#box-count').value)); });
  boxForm.addEventListener('submit', async event => {
    event.preventDefault(); if (busyForms.has(boxForm) || selectedBox === null || !boxForm.reportValidity()) return;
    const targetAccount = currentAccount(); const count = Number($('#box-count').value); const type = selectedBox;
    const selected = state.catalogs.box_types.find(box => String(box.id) === String(type));
    lockForm(boxForm, true); showError('#box-error', '');
    const button = $('button[type=submit]', boxForm); button.textContent = 'Adding…';
    try {
      const result = await api('/api/add_boxes', {account: targetAccount, type, count});
      if (result.status !== 'ok') throw new Error('The boxes were not added.');
      if (account === targetAccount) await refreshState();
      toast(`Added ${number(result.added)} ${selected?.label || selected?.name} boxes. Total: ${number(result.total_boxes)}.`);
    } catch (error) { if (account === targetAccount) showError('#box-error', error.message); else toast(error.message, true); }
    finally { button.textContent = 'Give boxes'; lockForm(boxForm, false); }
  });
  for (const button of $$('[data-bot-action]')) button.addEventListener('click', async () => {
    if (button.disabled) return;
    button.disabled = true; showError('#bot-error', '');
    try {
      const result = await api('/api/bot_group', {account: currentAccount(), action: button.dataset.botAction});
      toast(result.message);
    } catch (error) { showError('#bot-error', error.message); }
    finally { button.disabled = false; }
  });
  $('#open-all-boxes').addEventListener('click', async event => {
    const button = event.currentTarget; const targetAccount = currentAccount();
    if (button.disabled || !window.confirm('Open every box, like the move to Overwatch 2? The game shows how many on the main menu.')) return;
    button.disabled = true; showError('#box-error', '');
    try {
      const result = await api('/api/open_all_boxes', {account: targetAccount});
      if (result.status !== 'ok') throw new Error('The boxes were not opened.');
      if (account === targetAccount) await refreshState();
      toast(`Opened ${number(result.opened)} boxes, ${number(result.new_items)} new items.`);
    } catch (error) { if (account === targetAccount) showError('#box-error', error.message); else toast(error.message, true); }
    finally { button.disabled = false; }
  });
  filters.addEventListener('submit', event => { event.preventDefault(); clearTimeout(searchTimer); shopPage = 1; loadShop(); });
  filters.elements.q.addEventListener('input', () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => { shopPage = 1; loadShop(); }, 300); });
  for (const name of ['kind', 'hero', 'currency', 'owl']) filters.elements[name].addEventListener('change', () => { shopPage = 1; loadShop(); });
  $('#reset-filters').addEventListener('click', () => { filters.reset(); shopPage = 1; loadShop(); });
  $('#page-prev').addEventListener('click', () => { if (shopPage > 1) { shopPage--; loadShop(); } });
  $('#page-next').addEventListener('click', () => { if (shopPage < shopPages) { shopPage++; loadShop(); } });
  $('#purchase-form').addEventListener('submit', async event => {
    event.preventDefault(); if (!purchase || purchaseBusy || $('#purchase-confirm').disabled) return;
    const target = purchase; purchaseBusy = true;
    for (const selector of ['#purchase-confirm','#purchase-cancel','#purchase-close']) $(selector).disabled = true;
    $('#purchase-confirm').textContent = 'Buying…'; showError('#purchase-error', '');
    try {
      const result = await api('/api/purchase', {account: target.account, guid: target.item.guid});
      if (result.status !== 'ok' || !result.receipt) throw new Error('The purchase did not go through.');
      $('#purchase-dialog').close();
      toast(`Bought ${target.item.name} for ${priceText(result.receipt)}.`);
      if (account === target.account) { if (result.profile) state.profile = result.profile; await refreshState(); if (view !== 'shop') await loadShop(); }
    } catch (error) { showError('#purchase-error', error.message); }
    finally {
      purchaseBusy = false; for (const selector of ['#purchase-confirm','#purchase-cancel','#purchase-close']) $(selector).disabled = false;
      $('#purchase-confirm').textContent = 'Buy item';
    }
  });
  for (const selector of ['#purchase-cancel', '#purchase-close']) $(selector).addEventListener('click', () => { if (!purchaseBusy) $('#purchase-dialog').close(); });
  $('#purchase-dialog').addEventListener('cancel', event => { if (purchaseBusy) event.preventDefault(); });
  $('#purchase-dialog').addEventListener('click', event => { if (event.target === $('#purchase-dialog') && !purchaseBusy) $('#purchase-dialog').close(); });
  $('#account-select').addEventListener('change', async event => {
    const nextAccount = event.target.value;
    $('#account-select').disabled = true;
    try { await selectAccount(nextAccount); }
    catch (error) {
      event.target.value = account; $('#account-select').disabled = false;
      toast(error.message, true); return;
    }
    account = nextAccount; state = null; stateRequest++; shopRequest++; shopItems = []; shopPage = 1;
    profileDirty.clear(); challengeDirty.clear(); eventDirty = false; catalogSignature = '';
    showError('#profile-error', ''); showError('#challenge-error', ''); showError('#event-error', ''); showError('#box-error', '');
    $('#account-select').disabled = true; updateButtons();
    setText('#overview-title', 'Loading profile…'); setText('#profile-connection', 'Checking connection');
    $('#profile-connection').className = 'connection-tag';
    profileForm.reset(); challengeForm.reset();
    for (const selector of ['#credits-balance','#comp-balance','#league-balance','#shop-credits','#shop-comp','#shop-league','#overview-boxes','#overview-unlocked','#overview-opened','#boxes-total','#box-nav-count','#overview-event','#overview-hero','#overview-challenge','#overview-date','#selected-box-name','#selected-box-inventory']) setText(selector, '—');
    for (const selector of ['#profile-save-account','#event-account','#challenge-account','#box-account','#shop-account']) setText(selector, `Account: ${account}`);
    setText('#profile-level', 'Level —'); setText('#events-current', 'Loading event…');
    $('#event-grid').replaceChildren(node('div', 'loading-placeholder', 'Loading…'));
    $('#box-grid').replaceChildren(node('div', 'loading-placeholder', 'Loading…'));
    $('#shop-grid').replaceChildren(node('div', 'loading-placeholder', 'Loading…'));
    $('#shop-empty').hidden = true;
    await refreshState(true);
    $('#account-select').disabled = false;
  });
  $('#refresh-button').addEventListener('click', () => refreshState());
  window.addEventListener('hashchange', setView);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refreshState(); });
  setInterval(() => { if (!document.hidden && !busyForms.size && !eventBusy && !$('#purchase-dialog').open) refreshState(); }, 10000);
  setView(); setBoxCount(5); updateButtons(); refreshState(true);
})();
