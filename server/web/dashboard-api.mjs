export async function api(path, data) {
  let response;
  try {
    response = await fetch(path, data ? {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data),
    } : {cache: 'no-store'});
  } catch {
    throw new Error('Could not reach the server. Make sure it is running and try again.');
  }
  let result;
  try { result = await response.json(); }
  catch { throw new Error('The server returned a response that could not be read.'); }
  if (!response.ok || result.status === 'error' || result.error) {
    throw new Error(result.error || `Server error (${response.status}).`);
  }
  return result;
}

export async function selectAccount(name) {
  return api('/api/select_account', {name});
}
