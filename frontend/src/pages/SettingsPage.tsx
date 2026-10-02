import React, { useEffect, useState } from 'react';
import { Check, Eye, EyeOff, Save, Sparkles, HardDrive, KeyRound, Cpu } from 'lucide-react';
import { Settings, AIProviderType } from '../types';
import { getSettings, updateSettings, getVisionConnections, testVisionConnection } from '../api';
import { VoicePicker } from '../components/VoicePicker';
import { YouTubeSettings } from '../components/YouTubeSettings';

export const SettingsPage: React.FC<{ onSettingsUpdated: () => void }> = ({ onSettingsUpdated }) => {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [showGeminiKey, setShowGeminiKey] = useState(false);
  const [showOpenAIKey, setShowOpenAIKey] = useState(false);
  const [savedToast, setSavedToast] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [connections, setConnections] = useState<any>(null);
  const [testingVision, setTestingVision] = useState<'gemini' | 'local' | null>(null);
  const [visionTests, setVisionTests] = useState<Record<string, { passed: boolean; message: string }>>({});

  useEffect(() => {
    getSettings().then(setSettings).catch(() => {});
    getVisionConnections().then(setConnections).catch(() => {});
  }, []);

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!settings) return;

    setSaving(true);
    try {
      const updated = await updateSettings(settings);
      setSettings(updated);
      getVisionConnections().then(setConnections).catch(() => {});
      onSettingsUpdated();
      setSavedToast(true);
      setTimeout(() => setSavedToast(false), 3000);
    } catch (err: any) {
      setError(err.message || 'Could not save settings');
    } finally {
      setSaving(false);
    }
  };

  const testVision = async (provider: 'gemini' | 'local') => {
    if (!settings) return;
    setTestingVision(provider);
    try {
      const result = await testVisionConnection(provider, settings);
      setVisionTests(current => ({ ...current, [provider]: result }));
    } catch (err: any) {
      setVisionTests(current => ({ ...current, [provider]: { passed: false, message: err.message } }));
    } finally { setTestingVision(null); }
  };

  if (!settings) {
    return (
      <div className="flex-1 h-screen flex items-center justify-center bg-[#FAF9F6] text-zinc-400 text-xs">
        Loading settings...
      </div>
    );
  }

  return (
    <div className="flex-1 h-screen overflow-y-auto bg-[#FAF9F6] p-8 flex flex-col items-center">
      <form onSubmit={handleSave} className="max-w-3xl w-full flex flex-col gap-6 pb-16">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div className="flex flex-col gap-1">
            <h1 className="text-2xl font-bold tracking-tight text-zinc-900">Settings</h1>
            <p className="text-xs text-zinc-500">
              Configure multi-provider AI routing, API keys, speech synthesis, and hardware options.
            </p>
          </div>

          <button
            type="submit"
            disabled={saving}
            className="h-10 px-5 rounded-xl bg-zinc-900 hover:bg-zinc-800 text-white font-medium text-xs flex items-center gap-2 transition-all shadow-sm active:scale-[0.98]"
          >
            {savedToast ? (
              <>
                <Check className="w-3.5 h-3.5 text-emerald-400" />
                <span>Saved!</span>
              </>
            ) : (
              <>
                <Save className="w-3.5 h-3.5" />
                <span>Save Changes</span>
              </>
            )}
          </button>
        </div>

        {error && <p role="alert" className="text-red-600 text-sm">{error}</p>}
        {/* Section 1: AI Engine Provider */}
        <div className="bg-white rounded-2xl border border-zinc-200/90 shadow-sm p-6 flex flex-col gap-5">
          <div className="flex items-center gap-2.5 pb-2 border-b border-zinc-100">
            <Sparkles className="w-4 h-4 text-zinc-800" />
            <h3 className="text-sm font-semibold text-zinc-900">AI Provider Selection</h3>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {/* Auto Hybrid */}
            <div
              onClick={() => setSettings({ ...settings, ai_provider: 'auto' })}
              className={`p-4 rounded-xl border cursor-pointer transition-all flex flex-col justify-between gap-3 ${
                settings.ai_provider === 'auto'
                  ? 'border-zinc-900 bg-zinc-50/70 ring-1 ring-zinc-900'
                  : 'border-zinc-200 hover:border-zinc-300 bg-white'
              }`}
            >
              <div className="flex flex-col gap-1">
                <span className="text-xs font-semibold text-zinc-900">Auto (Hybrid Router)</span>
                <span className="text-[11px] text-zinc-500 leading-relaxed">
                  Uses a connected vision model. Both workflows require complete-story verification and final rendered video QC.
                </span>
              </div>
              <span className="text-[10px] font-semibold text-emerald-600 uppercase tracking-wider">
                Recommended
              </span>
            </div>

            {/* Google AI Studio */}
            <div
              onClick={() => setSettings({ ...settings, ai_provider: 'gemini' })}
              className={`p-4 rounded-xl border cursor-pointer transition-all flex flex-col justify-between gap-3 ${
                settings.ai_provider === 'gemini'
                  ? 'border-zinc-900 bg-zinc-50/70 ring-1 ring-zinc-900'
                  : 'border-zinc-200 hover:border-zinc-300 bg-white'
              }`}
            >
              <div className="flex flex-col gap-1">
                <span className="text-xs font-semibold text-zinc-900">Google AI Studio</span>
                <span className="text-[11px] text-zinc-500 leading-relaxed">
                  Add your Gemini key to verify subjects, actions, outcomes and clip labels.
                </span>
              </div>
              <span className="text-[10px] font-semibold text-zinc-400 uppercase tracking-wider">
                Gemini
              </span>
            </div>

            {/* OpenAI */}
            <div
              onClick={() => setSettings({ ...settings, ai_provider: 'openai' })}
              className={`p-4 rounded-xl border cursor-pointer transition-all flex flex-col justify-between gap-3 ${
                settings.ai_provider === 'openai'
                  ? 'border-zinc-900 bg-zinc-50/70 ring-1 ring-zinc-900'
                  : 'border-zinc-200 hover:border-zinc-300 bg-white'
              }`}
            >
              <div className="flex flex-col gap-1">
                <span className="text-xs font-semibold text-zinc-900">OpenAI</span>
                <span className="text-[11px] text-zinc-500 leading-relaxed">
                  Cloud intelligence powered by GPT-4o / GPT-4o-mini API key.
                </span>
              </div>
              <span className="text-[10px] font-semibold text-zinc-400 uppercase tracking-wider">
                OpenAI
              </span>
            </div>

            {/* Local AI */}
            <div
              onClick={() => setSettings({ ...settings, ai_provider: 'local' })}
              className={`p-4 rounded-xl border cursor-pointer transition-all flex flex-col justify-between gap-3 ${
                settings.ai_provider === 'local'
                  ? 'border-zinc-900 bg-zinc-50/70 ring-1 ring-zinc-900'
                  : 'border-zinc-200 hover:border-zinc-300 bg-white'
              }`}
            >
              <div className="flex flex-col gap-1">
                <span className="text-xs font-semibold text-zinc-900">Local AI only</span>
                <span className="text-[11px] text-zinc-500 leading-relaxed">
                  Connect a local or remote Ollama endpoint and an installed vision model. Model installation is managed separately.
                </span>
              </div>
              <span className="text-[10px] font-semibold text-zinc-400 uppercase tracking-wider">
                On-Device
              </span>
            </div>
          </div>

          {/* Google AI Studio API Key */}
          <div className="flex flex-col gap-2 pt-2">
            <label className="text-xs font-medium text-zinc-700 flex items-center gap-1.5">
              <KeyRound className="w-3.5 h-3.5 text-zinc-500" />
              <span>Google AI Studio API Key</span>
            </label>
            <div className="relative">
              <input
                type={showGeminiKey ? 'text' : 'password'}
                value={settings.gemini_api_key || ''}
                placeholder={settings.gemini_api_key_configured?'Key saved securely on backend; enter to replace':'Enter Gemini API key'}
                onChange={(e) => setSettings({ ...settings, gemini_api_key: e.target.value })}
                className="w-full h-10 px-3 pr-10 rounded-xl bg-zinc-50 border border-zinc-200 text-xs font-mono text-zinc-900 focus:outline-none focus:ring-2 focus:ring-zinc-900/10 focus:border-zinc-400"
              />
              <button
                type="button"
                onClick={() => setShowGeminiKey(!showGeminiKey)}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-zinc-400 hover:text-zinc-700"
              >
                {showGeminiKey ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
              </button>
            </div>
          </div>

          {/* OpenAI API Key */}
          <div className="flex flex-col gap-2">
            <label className="text-xs font-medium text-zinc-700 flex items-center gap-1.5">
              <KeyRound className="w-3.5 h-3.5 text-zinc-500" />
              <span>OpenAI API Key (Optional)</span>
            </label>
            <div className="relative">
              <input
                type={showOpenAIKey ? 'text' : 'password'}
                value={settings.openai_api_key || ''}
                placeholder={settings.openai_api_key_configured?'Key saved on backend; enter to replace':'Enter OpenAI API key'}
                onChange={(e) => setSettings({ ...settings, openai_api_key: e.target.value })}
                className="w-full h-10 px-3 pr-10 rounded-xl bg-zinc-50 border border-zinc-200 text-xs font-mono text-zinc-900 focus:outline-none focus:ring-2 focus:ring-zinc-900/10 focus:border-zinc-400"
              />
              <button
                type="button"
                onClick={() => setShowOpenAIKey(!showOpenAIKey)}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-zinc-400 hover:text-zinc-700"
              >
                {showOpenAIKey ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
              </button>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <label className="text-xs text-zinc-600">OpenAI model<input value={settings.openai_model} onChange={e => setSettings({ ...settings, openai_model: e.target.value })} className="creator-select mt-2" /></label>
            <label className="text-xs text-zinc-600">Ollama vision model<input value={settings.local_model} onChange={e => setSettings({ ...settings, local_model: e.target.value })} placeholder="qwen3-vl:4b or your installed vision model" className="creator-select mt-2" /></label>
          </div>
          {/* Model Selections */}
          <div className="grid grid-cols-2 gap-4 pt-1">
            <div className="flex flex-col gap-1.5">
              <label className="text-xs font-medium text-zinc-700">Gemini Model</label>
              <input
                value={settings.gemini_model}
                onChange={(e) => setSettings({ ...settings, gemini_model: e.target.value })}
                className="h-10 px-3 rounded-xl bg-zinc-50 border border-zinc-200 text-xs text-zinc-800 focus:outline-none"
                placeholder="Your image-capable Gemini model" />
            </div>

            <div className="flex flex-col gap-1.5">
              <label className="text-xs font-medium text-zinc-700">Local Endpoint (Ollama)</label>
              <input
                type="text"
                value={settings.local_endpoint}
                onChange={(e) => setSettings({ ...settings, local_endpoint: e.target.value })}
                placeholder="http://localhost:11434"
                className="h-10 px-3 rounded-xl bg-zinc-50 border border-zinc-200 text-xs font-mono text-zinc-800 focus:outline-none"
              />
            </div>
          </div>
          <div className="border-t border-zinc-100 pt-4 space-y-3">
            <p className="text-xs font-semibold">Vision connections</p>
            <p className="text-xs text-zinc-500">Enter your connection details, test image reading, then save. The app does not install or download local models.</p>
            <div className="grid md:grid-cols-2 gap-3">{(['gemini', 'local'] as const).map(provider => <div key={provider} className="border border-zinc-200 rounded-xl p-4 space-y-2">
              <p className="text-xs font-semibold">{provider === 'gemini' ? 'Gemini vision' : 'Ollama vision'}</p>
              <p className="text-xs text-zinc-500">{connections?.[provider]?.message || 'Ready for connection details.'}</p>
              {provider === 'local' && connections?.local?.installed_models?.length > 0 && <p className="text-[11px] text-zinc-500">Installed: {connections.local.installed_models.join(', ')}</p>}
              <button type="button" disabled={!!testingVision} onClick={() => testVision(provider)} className="text-xs font-semibold underline disabled:opacity-50">{testingVision === provider ? 'Testing image reading…' : 'Test vision connection'}</button>
              {visionTests[provider] && <p role="status" className={`text-xs ${visionTests[provider].passed ? 'text-emerald-700' : 'text-red-600'}`}>{visionTests[provider].message}</p>}
            </div>)}</div>
          </div>
        </div>

        <YouTubeSettings />
        {/* Section 2: General & Media Options */}
        <div className="bg-white rounded-2xl border border-zinc-200/90 shadow-sm p-6 flex flex-col gap-5">
          <div className="flex items-center gap-2.5 pb-2 border-b border-zinc-100">
            <HardDrive className="w-4 h-4 text-zinc-800" />
            <h3 className="text-sm font-semibold text-zinc-900">Audio & Hardware Settings</h3>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div className="flex flex-col gap-1.5">
              <VoicePicker voice={settings.default_voice} onChange={voice => setSettings({ ...settings, default_voice: voice })} />
              <p className="text-[11px] text-zinc-400">{settings.default_voice.startsWith('pocket:')?'Pocket TTS runs locally on your Mac. No speech API key or internet is needed after setup.':'Online neural speech requires internet. No speech API key needed.'}</p>
            </div>

            <div className="flex flex-col gap-1.5">
              <label className="text-xs font-medium text-zinc-700">Hardware Acceleration</label>
              <div className="h-10 px-3 rounded-xl bg-zinc-50 border border-zinc-200 text-xs text-zinc-800 flex items-center font-semibold">
                {settings.hardware_accel === 'videotoolbox' ? 'VideoToolbox available · H.264 export' : settings.hardware_accel}
              </div>
            </div>
          </div>
        </div>
      </form>
    </div>
  );
};
