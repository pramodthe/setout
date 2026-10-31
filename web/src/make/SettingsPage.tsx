import { useEffect, useState } from "react";
import type { JSX } from "react";
import { makeApi } from "./makeApi";
import type { MakeSettingsProfile } from "./makeApi";

export default function SettingsPage(): JSX.Element {
  const [profiles, setProfiles] = useState<MakeSettingsProfile[]>([]);
  const [profileId, setProfileId] = useState("muse");
  const [baseUrl, setBaseUrl] = useState("");
  const [model, setModel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [keySet, setKeySet] = useState(false);
  const [target, setTarget] = useState("local");
  const [note, setNote] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  function applyProfile(rows: MakeSettingsProfile[], id: string) {
    const row = rows.find((item) => item.id === id) || rows[0];
    if (!row) return;
    setProfileId(row.id);
    setBaseUrl(row.base_url || "");
    setModel(row.model || "");
    setKeySet(row.api_key_set);
    setApiKey("");
  }

  useEffect(() => {
    makeApi.settings().then((body) => {
      setProfiles(body.profiles);
      setTarget(body.blender_target || "local");
      applyProfile(body.profiles, body.profiles.find((item) => item.ready)?.id || body.profiles[0]?.id || "muse");
    }).catch((error: Error) => setErr(error.message));
  }, []);

  function onProfile(id: string) {
    applyProfile(profiles, id);
    setNote(null);
  }

  async function save(clearKey = false) {
    setBusy(true);
    setErr(null);
    setNote(null);
    try {
      const body = await makeApi.saveSettings({
        profile: profileId,
        base_url: baseUrl,
        model,
        ...(apiKey ? { api_key: apiKey } : {}),
        ...(clearKey ? { clear_api_key: true } : {}),
      });
      setProfiles(body.profiles);
      setTarget(body.blender_target || "local");
      applyProfile(body.profiles, profileId);
      setNote(clearKey ? "Key cleared." : "Saved.");
    } catch (error) {
      setErr(error instanceof Error ? error.message : "save failed");
    } finally {
      setBusy(false);
    }
  }

  async function test() {
    setBusy(true);
    setErr(null);
    setNote(null);
    try {
      const result = await makeApi.testSettings({
        profile: profileId,
        base_url: baseUrl,
        model,
        ...(apiKey ? { api_key: apiKey } : {}),
      });
      setNote(result.ok ? "Connection ok." : (result.error || "Connection failed."));
    } catch (error) {
      setErr(error instanceof Error ? error.message : "test failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="make">
      <div className="make-tabs">
        <a className="back" href="#/">← Library</a>
        <a className="make-tab" href="#/make">3D Make</a>
        <span className="make-tab on">Settings</span>
      </div>
      <form className="make-settings" data-make="settings" onSubmit={(event) => { event.preventDefault(); void save(); }}>
        <h1>Model settings</h1>
        <p className="make-muted">The API key stays on the server. This page never receives it back.</p>
        <label>
          Profile
          <select data-make="settings-profile" value={profileId} onChange={(event) => onProfile(event.target.value)}>
            {profiles.map((item) => (
              <option key={item.id} value={item.id}>{item.label}</option>
            ))}
          </select>
        </label>
        <label>
          Base URL
          <input data-make="settings-base" value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} autoComplete="off" />
        </label>
        <label>
          Model name
          <input data-make="settings-model" value={model} onChange={(event) => setModel(event.target.value)} autoComplete="off" />
        </label>
        <label>
          API key
          <input
            data-make="settings-key"
            type="password"
            value={apiKey}
            placeholder={keySet ? "Saved on the server" : "Not set"}
            onChange={(event) => setApiKey(event.target.value)}
            autoComplete="off"
          />
        </label>
        <p className="make-muted" data-make="settings-key-set">{keySet ? "A key is stored." : "No key stored."}</p>
        <p className="make-muted" data-make="blender-target">Blender target: {target}</p>
        <div className="make-settings-actions">
          <button type="submit" className="primary" data-make="settings-save" disabled={busy}>Save</button>
          <button type="button" data-make="settings-test" disabled={busy} onClick={() => { void test(); }}>Test connection</button>
          <button type="button" data-make="settings-clear" disabled={busy || !keySet} onClick={() => { void save(true); }}>Clear key</button>
        </div>
        {note && <p data-make="settings-note">{note}</p>}
        {err && <p className="make-err">{err}</p>}
      </form>
    </div>
  );
}
