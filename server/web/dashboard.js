'use strict';
import {api, selectAccount} from './dashboard-api.mjs';

(() => {
  const $ = (selector, scope = document) => scope.querySelector(selector);
  const $$ = (selector, scope = document) => [...scope.querySelectorAll(selector)];
  const number = value => new Intl.NumberFormat('ru-RU').format(Number(value) || 0);
  const titles = {overview: 'Обзор и профиль', events: 'События', boxes: 'Контейнеры', shop: 'Коллекция и магазин', sessions: 'Сервер и подключения'};
  const currencies = {
    credits: {label: 'Кредиты', unit: 'кредитов', symbol: 'C', className: 'credits-icon'},
    comp_points: {label: 'Соревновательные очки', unit: 'соревновательных очков', symbol: '◆', className: 'comp-icon'},
    league_tokens: {label: 'Жетоны лиги', unit: 'жетонов лиги', symbol: 'L', className: 'league-icon'}
  };
  const typeLabels = {Skin: 'Облик', Emote: 'Эмоция', VictoryPose: 'Поза победы', 'Victory Pose': 'Поза победы', VoiceLine: 'Реплика', 'Voice Line': 'Реплика', Spray: 'Граффити', HighlightIntro: 'Лучший момент', 'Highlight Intro': 'Лучший момент', PlayerIcon: 'Значок', 'Player Icon': 'Значок', Weapon: 'Оружие'};
  const rarityLabels = {Common: 'Обычный', Rare: 'Редкий', Epic: 'Эпический', Legendary: 'Легендарный'};
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
    close.type = 'button'; close.setAttribute('aria-label', 'Закрыть уведомление');
    close.addEventListener('click', () => element.remove());
    element.append(close); $('#toasts').append(element);
    setTimeout(() => element.remove(), error ? 10000 : 6500);
  }
  function syncStatus(connected) {
    const status = $('#sync-status'); status.replaceChildren();
    status.append(node('span', `status-dot ${connected ? 'online' : 'offline'}`), document.createTextNode(connected ? 'Данные обновлены' : 'Нет связи с панелью'));
    $('#sidebar-status-dot').className = `status-dot ${connected ? 'online' : 'offline'}`;
    setText('#sidebar-status', connected ? 'Сервер отвечает' : 'Нет связи с панелью');
    if (!connected) setText('#sessions-status', 'Данные могут быть устаревшими');
  }
  function currentAccount() {
    if (!account || !state) throw new Error('Выберите аккаунт и дождитесь загрузки его профиля.');
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
    const event = state?.catalogs.events.find(item => item.id === selectedEvent);
    $('#apply-event').disabled = !state || !eventDirty || eventBusy || event?.scene_status === 'unavailable';
    $('#profile-draft').hidden = !profileDirty.size;
    $('#apply-event').textContent = eventBusy ? 'Применяем…' : event?.scene_status === 'unavailable' ? 'Сцена недоступна в 1.74' : 'Применить событие';
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
    createOptions(lobbyHeroSelect, catalogs.heroes || [], 'name', 'name', 'Случайный герой', 'random');
    const noHeroOption = node('option', '', 'Без героя'); noHeroOption.value = 'none'; lobbyHeroSelect.append(noHeroOption);
    if (previousLobbyHero === 'none') lobbyHeroSelect.value = 'none';
    createOptions($('[name=challenge]', challengeForm), catalogs.challenges || [], 'id', 'title', 'Без испытания');
    createOptions($('[name=hero]', filters), catalogs.heroes || [], 'name', 'name', 'Все герои');
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
    for (const selector of ['#profile-save-account', '#event-account', '#challenge-account', '#box-account', '#shop-account']) setText(selector, `Аккаунт: ${account}`);
  }
  function renderOverview() {
    const profile = state.profile;
    const activeAccount = (state.accounts || []).find(item => item.name === account);
    const event = state.catalogs.events.find(item => item.id === (profile.events || [])[0]);
    const hero = state.catalogs.heroes.find(item => item.name === profile.lobby_hero);
    setText('#overview-title', profile.player_name || account);
    const connection = $('#profile-connection');
    connection.textContent = activeAccount?.online ? 'Подключён к серверу' : 'Клиент не подключён';
    connection.className = `connection-tag${activeAccount?.online ? ' online' : ''}`;
    setText('#profile-level', `Уровень ${number(profile.level)}`);
    setText('#lobby-description', activeAccount?.online ? 'Профиль готов. Управляйте событием, коллекцией и запасом контейнеров.' : 'Настройте профиль, затем подключите игровой клиент к локальному серверу.');
    for (const [selector, field] of [['#credits-balance','credits'], ['#comp-balance','comp_points'], ['#league-balance','league_tokens'], ['#shop-credits','credits'], ['#shop-comp','comp_points'], ['#shop-league','league_tokens']]) setText(selector, number(profile[field]));
    setText('#overview-event', event?.label || (profile.events?.[0] || 'Без события'));
    setText('#overview-hero', profile.lobby_hero === 'random' ? 'Случайный герой' : profile.lobby_hero === 'none' ? 'Без героя' : hero?.name || profile.lobby_hero || '—');
    const challenge = state.catalogs.challenges.find(item => item.id === profile.challenge);
    setText('#overview-challenge', challenge?.title || profile.challenge || 'Без испытания');
    setText('#overview-date', profile.server_date === 'now' || !profile.server_date ? 'Текущая дата' : profile.server_date);
    setText('#overview-boxes', number(profile.loot_boxes_count));
    setText('#boxes-total', number(profile.loot_boxes_count));
    setText('#box-nav-count', number(profile.loot_boxes_count));
    setText('#overview-unlocked', number(profile.unlocked_count));
    setText('#overview-opened', number(profile.stats?.boxes_opened));
    $('#event-nav-dot').hidden = !(profile.events || []).length;
    setText('#events-current', `Сейчас: ${event?.label || (profile.events?.[0] || 'без события')}`);
  }
  function renderEvents() {
    const focusedEvent = document.activeElement?.hasAttribute('data-event') ? document.activeElement.dataset.event : null;
    const container = $('#event-grid'); container.replaceChildren();
    const events = [{id: '', label: 'Без события', description: 'Обычное оформление лобби', category: 'default'}, ...state.catalogs.events];
    const categories = {default: 'Стандартное лобби', seasonal: 'Сезонное событие', special: 'Особое оформление', challenge: 'Испытание героя', league: 'Лига Overwatch'};
    for (const event of events) {
      const button = node('button', `event-card${selectedEvent === event.id ? ' selected' : ''}`);
      button.dataset.event = event.id;
      button.type = 'button'; button.setAttribute('aria-pressed', String(selectedEvent === event.id));
      button.append(node('strong', '', event.label), node('span', '', event.scene_status === 'unavailable' ? 'Сцена недоступна в этой версии' : event.description || 'Оформление лобби'), node('span', 'event-category', categories[event.category] || 'Событие игры'));
      button.addEventListener('click', () => {
        selectedEvent = event.id; eventDirty = selectedEvent !== (state.profile.events?.[0] || '');
        showError('#event-error', ''); renderEvents(); updateButtons();
      });
      container.append(button);
    }
    if (focusedEvent !== null) $$('[data-event]', container).find(button => button.dataset.event === focusedEvent)?.focus({preventScroll: true});
    const selected = events.find(item => item.id === selectedEvent);
    setText('#event-detail-title', selected?.label || 'Событие вне каталога');
    setText('#event-detail-description', selected?.description || 'Выберите доступное событие в списке.');
    const status = $('#event-scene-status');
    const scene = selected?.scene_status;
    const labels = {verified: 'Сцена подтверждена', unverified: 'Сцена не проверена', limited: 'Поддержка сцены ограничена', unavailable: 'Сцена недоступна', supported: 'Сцена поддерживается'};
    status.textContent = !selectedEvent ? 'Стандартное оформление' : labels[scene] || 'Поддержка сцены не подтверждена';
    status.className = `scene-status ${scene === 'verified' || scene === 'supported' ? 'verified' : 'unverified'}`;
    setText('#event-scene-note', selected?.scene_note || (!selectedEvent ? 'Сезонное оформление отключено.' : 'Проверяйте результат в игровом клиенте.'));
    updateButtons();
  }
  function renderChallengeRewards() {
    const challenge = state?.catalogs.challenges.find(item => item.id === $('[name=challenge]', challengeForm).value);
    const container = $('#challenge-rewards'); container.replaceChildren();
    if (!challenge) { container.append(node('span', 'muted', 'Испытание отключено.')); return; }
    if (!challenge.rewards?.length) { container.append(node('span', 'muted', 'Награды не указаны в каталоге.')); return; }
    for (const reward of challenge.rewards) {
      const chip = node('span', 'reward-chip', reward.name || reward.guid || 'Предмет');
      chip.append(node('small', '', typeLabels[reward.type] || reward.type || ''));
      container.append(chip);
    }
  }
  function boxGraphic() {
    const ns = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(ns, 'svg'); svg.setAttribute('viewBox', '0 0 100 90'); svg.setAttribute('aria-hidden', 'true');
    for (const [pathData, className] of [['m10 26 40-18 40 18-40 18Z',''], ['m10 26 40 18v38L10 64Zm40 18 40-18v38L50 82Z',''], ['m27 18 40 18M50 44v17m-9-5 9 5 9-5',''], ['m43 12 14 6-7 3-14-6Z','box-accent'], ['m42 49 8 4 8-4v10l-8 4-8-4Z','box-accent']]) {
      const path = document.createElementNS(ns, 'path'); path.setAttribute('d', pathData); if (className) path.setAttribute('class', className); svg.append(path);
    }
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
      const art = node('div', 'box-art'); art.append(boxGraphic());
      const copy = node('div', 'box-card-copy'); copy.append(node('strong', '', box.label || box.name), node('span', '', box.name || ''));
      const count = node('div', 'box-inventory'); count.append(node('span', '', 'В запасе'), node('b', '', number(boxCount(box.id)))); copy.append(count);
      button.append(art, copy); button.addEventListener('click', () => { selectedBox = box.id; showError('#box-error', ''); renderBoxes(); }); container.append(button);
    }
    if (focusedBox !== null) $$('[data-box]', container).find(button => button.dataset.box === focusedBox)?.focus({preventScroll: true});
    const selected = state.catalogs.box_types.find(box => String(box.id) === String(selectedBox));
    $('#selected-box-art').replaceChildren(boxGraphic());
    setText('#selected-box-name', selected?.label || selected?.name || 'Нет доступных типов');
    setText('#selected-box-inventory', selected ? `В запасе: ${number(boxCount(selected.id))}` : 'Каталог контейнеров пуст');
    updateButtons();
  }
  function duration(value) {
    let seconds = Math.max(0, Math.floor(Number(value) || 0));
    const hours = Math.floor(seconds / 3600); seconds %= 3600;
    const minutes = Math.floor(seconds / 60);
    return hours ? `${hours} ч ${minutes} мин` : minutes ? `${minutes} мин ${seconds % 60} с` : `${seconds} с`;
  }
  function renderSessions() {
    const server = state.server;
    setText('#sidebar-address', `${server.host}:${server.port}`);
    setText('#server-endpoint', `${server.host}:${server.port}`);
    setText('#server-clients', number(server.connected_clients));
    setText('#server-uptime', duration(server.uptime_seconds));
    setText('#sessions-status', 'Сервер отвечает');
    setText('#matchmaking-note', server.matchmaking_supported ? 'Сервер сообщает о поддержке подбора матчей.' : 'Этот сервер поддерживает локальное лобби. Подбор матчей не поддерживается.');
    const accounts = $('#sessions-accounts'); accounts.replaceChildren();
    for (const item of state.accounts || []) {
      const row = node('tr'); row.append(node('td', '', item.name));
      const connection = node('td'); const status = node('span', `table-status${item.online ? ' online' : ''}`);
      status.append(node('span', `status-dot${item.online ? ' online' : ''}`), document.createTextNode(item.online ? 'Подключён' : 'Не подключён'));
      connection.append(status); row.append(connection);
      const current = node('td'); current.append(node('span', item.name === account ? 'selected-badge' : 'muted', item.name === account ? 'Выбран' : '—')); row.append(current); accounts.append(row);
    }
    setText('#accounts-count', `${number((state.accounts || []).length)} аккаунтов`);
    const instances = Array.isArray(server.game_instances) ? server.game_instances : [];
    setText('#instances-count', `${number(instances.length)} процессов`);
    const container = $('#instances-list'); container.replaceChildren();
    for (const instance of instances) {
      const row = node('div', 'instance-row'); row.append(node('span', 'instance-icon', '▣'));
      const copy = node('div');
      if (typeof instance === 'string') copy.append(node('h3', '', instance));
      else {
        const activityLabels = {practice: 'Тренировочная сессия', queue: 'Очередь локального сервера'};
        copy.append(node('h3', '', instance.name || activityLabels[instance.activity] || instance.executable || instance.exe || 'Локальная сессия'));
        const details = [];
        if (instance.pid !== undefined) details.push(`PID ${instance.pid}`);
        if (instance.player || instance.account) details.push(`Аккаунт: ${instance.player || instance.account}`);
        if (instance.host && instance.port !== undefined) details.push(`UDP ${instance.host}:${instance.port}`);
        if (instance.mode) details.push(`Режим: ${instance.mode}`);
        if (instance.path) details.push(instance.path);
        if (details.length) copy.append(node('p', '', details.join(' · ')));
        if (instance.protocol_ready === false) copy.append(node('p', 'instance-protocol', 'Ожидает игрового протокола'));
        else if (instance.protocol_ready === true) copy.append(node('p', 'instance-protocol', 'Игровой протокол активен'));
        if (instance.packets_received !== undefined || instance.bytes_received !== undefined) {
          const counters = [];
          if (instance.packets_received !== undefined) counters.push(`Принято пакетов: ${number(instance.packets_received)}`);
          if (instance.bytes_received !== undefined) counters.push(`Принято байт: ${number(instance.bytes_received)}`);
          copy.append(node('p', '', counters.join(' · ')));
        }
      }
      row.append(copy);
      if (typeof instance === 'object' && (instance.state || instance.status)) {
        const states = {listening: 'UDP-порт прослушивается', stopped: 'Остановлено', failed: 'Ошибка процесса'};
        row.append(node('span', 'current-pill', states[instance.state] || instance.state || instance.status));
      }
      container.append(row);
    }
    $('#instances-empty').hidden = instances.length > 0;
    setText('#last-updated', `Обновлено в ${new Date().toLocaleTimeString('ru-RU', {hour: '2-digit', minute: '2-digit', second: '2-digit'})}`);
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
      state.catalogs.events = state.catalogs.events.filter(event => !['limited', 'unavailable'].includes(event.scene_status));
      if (!account) account = result.accounts?.find(item => item.selected)?.name || result.profile.player_name;
      renderCatalogs(force); renderAccounts(); renderOverview();
      if (force) { profileDirty.clear(); challengeDirty.clear(); eventDirty = false; }
      fillForm(profileForm, state.profile, profileDirty, force);
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
  function renderShop(result) {
    const focusedGuid = document.activeElement?.dataset.purchaseGuid;
    shopItems = result.items || [];
    shopPage = Number(result.page) || 1; shopPages = Math.max(1, Number(result.pages) || 1);
    setText('#shop-result-count', `Найдено предметов: ${number(result.total)}`);
    const container = $('#shop-grid'); container.replaceChildren();
    for (const item of shopItems) {
      const card = node('article', `item-card rarity-${String(item.rarity || '').toLowerCase()}`);
      const art = node('div', 'item-art');
      art.append(node('span', 'item-type', typeLabels[item.type] || item.type || 'Предмет'));
      art.append(node('span', 'item-monogram', (item.hero || item.name || 'OW').replace(/[^\p{L}\p{N} ]/gu, '').split(/\s+/).map(word => word[0]).join('').slice(0, 2)));
      if (item.owned) art.append(node('span', 'owned-badge', '✓ В коллекции'));
      const copy = node('div', 'item-copy'); copy.append(node('span', 'item-hero', item.hero || 'Общий предмет'), node('h3', '', item.name), node('span', 'item-rarity', rarityLabels[item.rarity] || item.rarity || ''));
      const footer = node('div', 'item-footer');
      if (item.purchasable && !item.owned) {
        const price = node('span', 'item-price'); price.setAttribute('aria-label', priceText(item)); price.title = currencies[item.currency]?.label || item.currency;
        price.append(currencyIcon(item.currency), document.createTextNode(number(item.price)));
        const buy = node('button', 'button primary small', 'Купить'); buy.type = 'button'; buy.setAttribute('aria-label', `Купить ${item.name} за ${priceText(item)}`);
        buy.dataset.purchaseGuid = item.guid;
        buy.addEventListener('click', () => openPurchase(item)); footer.append(price, buy);
      } else footer.append(node('span', 'item-unavailable', item.owned ? 'Предмет уже открыт' : 'Недоступен для покупки'));
      card.append(art, copy, footer); container.append(card);
    }
    if (focusedGuid) $$('[data-purchase-guid]', container).find(button => button.dataset.purchaseGuid === focusedGuid)?.focus({preventScroll: true});
    $('#shop-empty').hidden = shopItems.length > 0;
    setText('#page-status', `Страница ${number(shopPage)} из ${number(shopPages)}`);
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
    const params = new URLSearchParams({account, q: filters.elements.q.value.trim(), hero: filters.elements.hero.value, currency: filters.elements.currency.value, page: String(shopPage), page_size: '24'});
    try {
      const result = await api(`/api/shop?${params}`);
      if (request !== shopRequest || requestedAccount !== account) return;
      renderShop(result); showError('#shop-error', '');
    } catch (error) { if (request === shopRequest && requestedAccount === account) showError('#shop-error', error.message); }
    finally { if (request === shopRequest) { shopLoading = false; $('#shop-loading').hidden = true; updatePagination(); } }
  }
  function openPurchase(item) {
    if (!state) return;
    const balance = Number(state.profile[item.currency]) || 0;
    purchase = {item, account, balance};
    setText('#purchase-title', item.name);
    setText('#purchase-detail', `${item.hero || 'Общий предмет'} · ${typeLabels[item.type] || item.type || 'Предмет'}`);
    setText('#purchase-account', account); setText('#purchase-price', priceText(item));
    setText('#purchase-remaining', `${number(balance - Number(item.price))} ${currencies[item.currency]?.unit || item.currency}`);
    showError('#purchase-error', balance < Number(item.price) ? `Недостаточно средств. Баланс: ${number(balance)} ${currencies[item.currency]?.unit || item.currency}.` : '');
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
    const button = $('button[type=submit]', form); const label = button.textContent; button.textContent = 'Сохраняем…';
    try {
      const result = await api('/api/update_profile', {account: targetAccount, ...changed});
      if (result.status !== 'ok') throw new Error('Сервер не подтвердил сохранение профиля.');
      if (account === targetAccount) { for (const name of Object.keys(changed)) dirty.delete(name); if (result.profile) state.profile = result.profile; await refreshState(); }
      toast(`${successMessage} Аккаунт: ${targetAccount}.`);
    } catch (error) { if (account === targetAccount) showError(errorSelector, error.message); else toast(error.message, true); }
    finally { button.textContent = label; lockForm(form, false); }
  }
  profileForm.addEventListener('input', event => markDirty(event, profileDirty, state?.profile));
  profileForm.addEventListener('change', event => markDirty(event, profileDirty, state?.profile));
  profileForm.addEventListener('submit', event => saveForm(event, profileForm, profileDirty, '#profile-error', 'Профиль сохранён.'));
  challengeForm.addEventListener('input', event => markDirty(event, challengeDirty, state?.profile));
  challengeForm.addEventListener('change', event => { markDirty(event, challengeDirty, state?.profile); renderChallengeRewards(); });
  challengeForm.addEventListener('submit', event => saveForm(event, challengeForm, challengeDirty, '#challenge-error', 'Испытание сохранено.'));
  $('#apply-event').addEventListener('click', async () => {
    if (!eventDirty || eventBusy || !state) return;
    const targetAccount = currentAccount(); const eventId = selectedEvent;
    const event = state.catalogs.events.find(item => item.id === eventId);
    if (event?.scene_status === 'unavailable') return;
    eventBusy = true; updateButtons(); showError('#event-error', '');
    try {
      const result = await api('/api/update_profile', {account: targetAccount, events: eventId ? [eventId] : []});
      if (result.status !== 'ok') throw new Error('Сервер не подтвердил сохранение события.');
      if (account === targetAccount) { eventDirty = false; if (result.profile) state.profile = result.profile; await refreshState(); }
      toast(`Событие сохранено: ${event?.label || 'без события'}. Аккаунт: ${targetAccount}.`);
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
    const button = $('button[type=submit]', boxForm); button.textContent = 'Выдаём…';
    try {
      const result = await api('/api/add_boxes', {account: targetAccount, type, count});
      if (result.status !== 'ok') throw new Error('Сервер не подтвердил выдачу контейнеров.');
      if (account === targetAccount) await refreshState();
      toast(`Выдано ${number(result.added)} контейнеров «${selected?.label || selected?.name}». Аккаунт: ${targetAccount}. Всего: ${number(result.total_boxes)}.`);
    } catch (error) { if (account === targetAccount) showError('#box-error', error.message); else toast(error.message, true); }
    finally { button.textContent = 'Выдать контейнеры'; lockForm(boxForm, false); }
  });
  filters.addEventListener('submit', event => { event.preventDefault(); clearTimeout(searchTimer); shopPage = 1; loadShop(); });
  filters.elements.q.addEventListener('input', () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => { shopPage = 1; loadShop(); }, 300); });
  for (const name of ['hero', 'currency']) filters.elements[name].addEventListener('change', () => { shopPage = 1; loadShop(); });
  $('#reset-filters').addEventListener('click', () => { filters.reset(); shopPage = 1; loadShop(); });
  $('#page-prev').addEventListener('click', () => { if (shopPage > 1) { shopPage--; loadShop(); } });
  $('#page-next').addEventListener('click', () => { if (shopPage < shopPages) { shopPage++; loadShop(); } });
  $('#purchase-form').addEventListener('submit', async event => {
    event.preventDefault(); if (!purchase || purchaseBusy || $('#purchase-confirm').disabled) return;
    const target = purchase; purchaseBusy = true;
    for (const selector of ['#purchase-confirm','#purchase-cancel','#purchase-close']) $(selector).disabled = true;
    $('#purchase-confirm').textContent = 'Покупаем…'; showError('#purchase-error', '');
    try {
      const result = await api('/api/purchase', {account: target.account, guid: target.item.guid});
      if (result.status !== 'ok' || !result.receipt) throw new Error('Сервер не подтвердил покупку предмета.');
      $('#purchase-dialog').close();
      toast(`«${target.item.name}» куплен за ${priceText(result.receipt)}. Аккаунт: ${target.account}.`);
      if (account === target.account) { if (result.profile) state.profile = result.profile; await refreshState(); if (view !== 'shop') await loadShop(); }
    } catch (error) { showError('#purchase-error', error.message); }
    finally {
      purchaseBusy = false; for (const selector of ['#purchase-confirm','#purchase-cancel','#purchase-close']) $(selector).disabled = false;
      $('#purchase-confirm').textContent = 'Купить предмет';
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
    setText('#overview-title', 'Загрузка профиля…'); setText('#profile-connection', 'Проверяем подключение');
    $('#profile-connection').className = 'connection-tag';
    profileForm.reset(); challengeForm.reset();
    for (const selector of ['#credits-balance','#comp-balance','#league-balance','#shop-credits','#shop-comp','#shop-league','#overview-boxes','#overview-unlocked','#overview-opened','#boxes-total','#box-nav-count','#overview-event','#overview-hero','#overview-challenge','#overview-date','#selected-box-name','#selected-box-inventory']) setText(selector, '—');
    for (const selector of ['#profile-save-account','#event-account','#challenge-account','#box-account','#shop-account']) setText(selector, `Аккаунт: ${account}`);
    setText('#profile-level', 'Уровень —'); setText('#events-current', 'Загружаем событие…');
    $('#event-grid').replaceChildren(node('div', 'loading-placeholder', 'Загружаем события выбранного аккаунта…'));
    $('#box-grid').replaceChildren(node('div', 'loading-placeholder', 'Загружаем инвентарь выбранного аккаунта…'));
    $('#shop-grid').replaceChildren(node('div', 'loading-placeholder', 'Загружаем коллекцию выбранного аккаунта…'));
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
