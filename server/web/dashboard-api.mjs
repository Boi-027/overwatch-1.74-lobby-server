export async function api(path, data) {
  let response;
  try {
    response = await fetch(path, data ? {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data),
    } : {cache: 'no-store'});
  } catch {
    throw new Error('Не удалось связаться с сервером. Проверьте, запущен ли он, и повторите действие.');
  }
  let result;
  try { result = await response.json(); }
  catch { throw new Error('Сервер вернул ответ, который не удалось прочитать.'); }
  if (!response.ok || result.status === 'error' || result.error) {
    throw new Error(result.error || `Ошибка сервера (${response.status}).`);
  }
  return result;
}

export async function selectAccount(name) {
  return api('/api/select_account', {name});
}
