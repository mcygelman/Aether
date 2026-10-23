/**
 * Agent Design Studio (ADS) - Self-Contained Offline Studio Engine
 * Runs seamlessly on both file:/// (local double-click) and http:// (local server).
 * Zero external dependencies, no build steps, no CDN requirements.
 * Updated to address Peer Review Findings R1 through R7.
 */

// --- 1. HTML Sanitizer & Schema Validator (Findings R1, R3, R6) ---

const DEFAULT_STUDIO_CONFIG = {
  CONFIG_VERSION: "1.0",
  MAX_IMPORT_BYTES: 1048576, // 1MB configurable limit matching config/studio-config.json
  CONTEXT_MIN: 512,
  CONTEXT_MAX: 262144,
  OUTPUT_MIN: 64,
  OUTPUT_MAX: 32768,
  REQ_TIMEOUT_MIN: 1,
  REQ_TIMEOUT_MAX: 3600,
  RUN_TIMEOUT_MIN: 1,
  RUN_TIMEOUT_MAX: 86400,
  TOOL_CALLS_MIN: 0,
  TOOL_CALLS_MAX: 500,
  SUPPORTED_TIERS: ['lightweight', 'ultralight'],
  SUPPORTED_VALIDATOR_VERSIONS: ['ads-validator-1.1', 'ads-validator-1.2', 'ads-validator-1.3']
};

let STUDIO_CONFIG = JSON.parse(JSON.stringify(DEFAULT_STUDIO_CONFIG));

let CONFIG_STATE = {
  source: 'embedded_default', // 'embedded_default' | 'http_loaded' | 'custom_file' | 'programmatic' | 'node_fs'
  sourceName: 'Embedded Defaults',
  version: DEFAULT_STUDIO_CONFIG.CONFIG_VERSION,
  isDefault: true,
  lastError: null
};

function validateStudioConfig(cfg) {
  if (!cfg || typeof cfg !== 'object' || Array.isArray(cfg)) {
    return { valid: false, error: 'Studio configuration must be a JSON dictionary object.' };
  }
  if (cfg.config_version !== undefined && (typeof cfg.config_version !== 'string' || !cfg.config_version.trim())) {
    return { valid: false, error: 'config_version must be a non-empty string.' };
  }
  if (cfg.limits !== undefined) {
    if (!cfg.limits || typeof cfg.limits !== 'object' || Array.isArray(cfg.limits)) {
      return { valid: false, error: 'limits must be an object dictionary.' };
    }
    const limits = cfg.limits;
    if (limits.max_import_bytes !== undefined) {
      if (typeof limits.max_import_bytes !== 'number' || isNaN(limits.max_import_bytes) || limits.max_import_bytes <= 0) {
        return { valid: false, error: 'limits.max_import_bytes must be a positive number.' };
      }
    }
    const rangeKeys = [
      { key: 'context_limit', allowZero: false },
      { key: 'output_limit', allowZero: false },
      { key: 'request_timeout_seconds', allowZero: false },
      { key: 'run_timeout_seconds', allowZero: false },
      { key: 'tool_call_limit', allowZero: true }
    ];
    for (const { key, allowZero } of rangeKeys) {
      if (limits[key] !== undefined) {
        const item = limits[key];
        if (!item || typeof item !== 'object' || Array.isArray(item)) {
          return { valid: false, error: `limits.${key} must be an object containing min and max.` };
        }
        if (typeof item.min !== 'number' || isNaN(item.min)) {
          return { valid: false, error: `limits.${key}.min must be a valid number.` };
        }
        if (typeof item.max !== 'number' || isNaN(item.max)) {
          return { valid: false, error: `limits.${key}.max must be a valid number.` };
        }
        if (!allowZero && item.min <= 0) {
          return { valid: false, error: `limits.${key}.min must be > 0.` };
        }
        if (allowZero && item.min < 0) {
          return { valid: false, error: `limits.${key}.min must be >= 0.` };
        }
        if (item.max < item.min) {
          return { valid: false, error: `limits.${key}.max (${item.max}) cannot be less than min (${item.min}).` };
        }
      }
    }
  }
  if (cfg.supported_tiers !== undefined) {
    if (!Array.isArray(cfg.supported_tiers) || cfg.supported_tiers.length === 0 || !cfg.supported_tiers.every(t => typeof t === 'string' && t.trim())) {
      return { valid: false, error: 'supported_tiers must be a non-empty array of strings.' };
    }
  }
  if (cfg.supported_validator_versions !== undefined) {
    if (!Array.isArray(cfg.supported_validator_versions) || cfg.supported_validator_versions.length === 0 || !cfg.supported_validator_versions.every(v => typeof v === 'string' && v.trim())) {
      return { valid: false, error: 'supported_validator_versions must be a non-empty array of strings.' };
    }
  }
  return { valid: true };
}

function updateConfigUI() {
  if (typeof document === 'undefined') return;

  // Update input attributes
  if (document.getElementById) {
    const elContext = document.getElementById('field-context-limit');
    if (elContext) {
      elContext.min = String(STUDIO_CONFIG.CONTEXT_MIN);
      elContext.max = String(STUDIO_CONFIG.CONTEXT_MAX);
    }
    const elOutput = document.getElementById('field-output-limit');
    if (elOutput) {
      elOutput.min = String(STUDIO_CONFIG.OUTPUT_MIN);
      elOutput.max = String(STUDIO_CONFIG.OUTPUT_MAX);
      elOutput.step = String(STUDIO_CONFIG.OUTPUT_MIN);
    }
    const elTool = document.getElementById('field-tool-limit');
    if (elTool) {
      elTool.min = String(STUDIO_CONFIG.TOOL_CALLS_MIN);
      elTool.max = String(STUDIO_CONFIG.TOOL_CALLS_MAX);
    }
    const elReq = document.getElementById('field-request-timeout');
    if (elReq) {
      elReq.min = String(STUDIO_CONFIG.REQ_TIMEOUT_MIN);
      elReq.max = String(STUDIO_CONFIG.REQ_TIMEOUT_MAX);
    }
    const elRun = document.getElementById('field-run-timeout');
    if (elRun) {
      elRun.min = String(STUDIO_CONFIG.RUN_TIMEOUT_MIN);
      elRun.max = String(STUDIO_CONFIG.RUN_TIMEOUT_MAX);
    }

    // Header badge
    const headerBadge = document.getElementById('config-status-badge');
    if (headerBadge) {
      const label = CONFIG_STATE.isDefault
        ? `Policy: Embedded Defaults (v${CONFIG_STATE.version})`
        : (CONFIG_STATE.source === 'http_loaded'
          ? `Policy: HTTP Loaded (v${CONFIG_STATE.version})`
          : `Policy: Custom (${CONFIG_STATE.sourceName}, v${CONFIG_STATE.version})`);
      headerBadge.textContent = label;
      headerBadge.title = `Active Governance Policy: ${CONFIG_STATE.sourceName}`;
    }

    // Panel badge
    const panelBadge = document.getElementById('config-status-badge-panel');
    if (panelBadge) {
      const label = CONFIG_STATE.isDefault
        ? `Policy: Embedded Defaults (v${CONFIG_STATE.version})`
        : (CONFIG_STATE.source === 'http_loaded'
          ? `Policy: HTTP Loaded (v${CONFIG_STATE.version})`
          : `Policy: Custom (${CONFIG_STATE.sourceName}, v${CONFIG_STATE.version})`);
      panelBadge.textContent = label;
      panelBadge.style.backgroundColor = CONFIG_STATE.isDefault ? 'rgba(88, 166, 255, 0.15)' : 'rgba(63, 185, 80, 0.15)';
      panelBadge.style.color = CONFIG_STATE.isDefault ? 'var(--accent-blue)' : 'var(--accent-green)';
      panelBadge.style.borderColor = CONFIG_STATE.isDefault ? 'rgba(88, 166, 255, 0.3)' : 'rgba(63, 185, 80, 0.3)';
    }

    // Summary block
    const summaryEl = document.getElementById('config-policy-summary');
    if (summaryEl) {
      const srcText = CONFIG_STATE.isDefault
        ? 'Embedded Defaults (Active baseline)'
        : (CONFIG_STATE.source === 'http_loaded' ? 'Loaded from config/studio-config.json via HTTP' : `Custom Policy File: ${escapeHtml(CONFIG_STATE.sourceName)}`);
      summaryEl.innerHTML = `Active Source: <strong>${srcText}</strong> | Version: <code>v${escapeHtml(CONFIG_STATE.version)}</code><br>` +
        `• Context: ${STUDIO_CONFIG.CONTEXT_MIN.toLocaleString()} – ${STUDIO_CONFIG.CONTEXT_MAX.toLocaleString()} tokens<br>` +
        `• Output: ${STUDIO_CONFIG.OUTPUT_MIN.toLocaleString()} – ${STUDIO_CONFIG.OUTPUT_MAX.toLocaleString()} tokens<br>` +
        `• Tool Call Limit: ${STUDIO_CONFIG.TOOL_CALLS_MIN} – ${STUDIO_CONFIG.TOOL_CALLS_MAX} calls<br>` +
        `• Timeouts: Request ${STUDIO_CONFIG.REQ_TIMEOUT_MIN}s–${STUDIO_CONFIG.REQ_TIMEOUT_MAX}s | Run ${STUDIO_CONFIG.RUN_TIMEOUT_MIN}s–${STUDIO_CONFIG.RUN_TIMEOUT_MAX}s<br>` +
        `• Max Import Size: ${(STUDIO_CONFIG.MAX_IMPORT_BYTES / 1024).toFixed(0)} KB`;
    }
  }

  // If a StudioApp instance exists, update live preview / draft re-validation
  if (typeof window !== 'undefined' && window.__STUDIO_APP_INSTANCE__) {
    try {
      window.__STUDIO_APP_INSTANCE__.updateLivePreview();
    } catch (e) {}
  }
}

function loadStudioConfigFromObject(cfg, source = 'custom_file', sourceName = 'custom-policy.json') {
  if (!cfg) return { success: false, error: 'Configuration object is missing.' };
  if (typeof cfg === 'string') {
    try { cfg = JSON.parse(cfg); } catch (e) { return { success: false, error: 'Malformed JSON: ' + e.message }; }
  }

  const vResult = validateStudioConfig(cfg);
  if (!vResult.valid) {
    CONFIG_STATE.lastError = vResult.error;
    return { success: false, error: vResult.error };
  }

  if (cfg.config_version) STUDIO_CONFIG.CONFIG_VERSION = String(cfg.config_version);
  const limits = cfg.limits || {};
  if (limits.max_import_bytes !== undefined) STUDIO_CONFIG.MAX_IMPORT_BYTES = Number(limits.max_import_bytes);
  if (limits.context_limit) {
    if (limits.context_limit.min !== undefined) STUDIO_CONFIG.CONTEXT_MIN = Number(limits.context_limit.min);
    if (limits.context_limit.max !== undefined) STUDIO_CONFIG.CONTEXT_MAX = Number(limits.context_limit.max);
  }
  if (limits.output_limit) {
    if (limits.output_limit.min !== undefined) STUDIO_CONFIG.OUTPUT_MIN = Number(limits.output_limit.min);
    if (limits.output_limit.max !== undefined) STUDIO_CONFIG.OUTPUT_MAX = Number(limits.output_limit.max);
  }
  if (limits.request_timeout_seconds) {
    if (limits.request_timeout_seconds.min !== undefined) STUDIO_CONFIG.REQ_TIMEOUT_MIN = Number(limits.request_timeout_seconds.min);
    if (limits.request_timeout_seconds.max !== undefined) STUDIO_CONFIG.REQ_TIMEOUT_MAX = Number(limits.request_timeout_seconds.max);
  }
  if (limits.run_timeout_seconds) {
    if (limits.run_timeout_seconds.min !== undefined) STUDIO_CONFIG.RUN_TIMEOUT_MIN = Number(limits.run_timeout_seconds.min);
    if (limits.run_timeout_seconds.max !== undefined) STUDIO_CONFIG.RUN_TIMEOUT_MAX = Number(limits.run_timeout_seconds.max);
  }
  if (limits.tool_call_limit) {
    if (limits.tool_call_limit.min !== undefined) STUDIO_CONFIG.TOOL_CALLS_MIN = Number(limits.tool_call_limit.min);
    if (limits.tool_call_limit.max !== undefined) STUDIO_CONFIG.TOOL_CALLS_MAX = Number(limits.tool_call_limit.max);
  }
  if (Array.isArray(cfg.supported_tiers)) STUDIO_CONFIG.SUPPORTED_TIERS = [...cfg.supported_tiers];
  if (Array.isArray(cfg.supported_validator_versions)) STUDIO_CONFIG.SUPPORTED_VALIDATOR_VERSIONS = [...cfg.supported_validator_versions];

  CONFIG_STATE.source = source;
  CONFIG_STATE.sourceName = sourceName;
  CONFIG_STATE.version = STUDIO_CONFIG.CONFIG_VERSION;
  CONFIG_STATE.isDefault = (source === 'embedded_default');
  CONFIG_STATE.lastError = null;

  updateConfigUI();
  return { success: true, config: { ...STUDIO_CONFIG }, state: { ...CONFIG_STATE } };
}

function loadStudioConfigFromJson(jsonStr, filename = 'custom-policy.json') {
  let parsed;
  try {
    parsed = JSON.parse(jsonStr);
  } catch (e) {
    CONFIG_STATE.lastError = 'Malformed JSON: ' + e.message;
    return { success: false, error: 'Malformed JSON: ' + e.message };
  }
  return loadStudioConfigFromObject(parsed, 'custom_file', filename);
}

function resetStudioConfigToDefaults() {
  STUDIO_CONFIG = JSON.parse(JSON.stringify(DEFAULT_STUDIO_CONFIG));
  CONFIG_STATE = {
    source: 'embedded_default',
    sourceName: 'Embedded Defaults',
    version: DEFAULT_STUDIO_CONFIG.CONFIG_VERSION,
    isDefault: true,
    lastError: null
  };
  updateConfigUI();
  return { success: true, config: { ...STUDIO_CONFIG }, state: { ...CONFIG_STATE } };
}

function applyStudioConfig(cfg) {
  // Retained for backward-compatibility with prior tests and callers
  loadStudioConfigFromObject(cfg, 'programmatic', 'Programmatic Override');
  return STUDIO_CONFIG;
}

function getStudioConfigState() {
  return { ...CONFIG_STATE };
}

// Automatic offline loading in Node.js environment
if (typeof window === 'undefined' && typeof require !== 'undefined') {
  try {
    const fs = require('fs');
    const path = require('path');
    const cfgPath = path.resolve(__dirname, '../config/studio-config.json');
    if (fs.existsSync(cfgPath)) {
      loadStudioConfigFromObject(JSON.parse(fs.readFileSync(cfgPath, 'utf8')), 'node_fs', 'config/studio-config.json');
    }
  } catch (e) {}
}

// Browser environment registration
if (typeof window !== 'undefined') {
  window.STUDIO_CONFIG = STUDIO_CONFIG;
  window.CONFIG_STATE = CONFIG_STATE;
  window.applyStudioConfig = applyStudioConfig;
  window.validateStudioConfig = validateStudioConfig;
  window.loadStudioConfigFromJson = loadStudioConfigFromJson;
  window.loadStudioConfigFromObject = loadStudioConfigFromObject;
  window.resetStudioConfigToDefaults = resetStudioConfigToDefaults;
  window.getStudioConfig = () => ({ ...STUDIO_CONFIG });
  window.getStudioConfigState = getStudioConfigState;

  // Dynamic fetch when running on local HTTP server; fallback baseline preserved for file:/// double-click
  try {
    if (typeof fetch === 'function') {
      fetch('config/studio-config.json')
        .then(r => {
          if (!r.ok) throw new Error(`HTTP ${r.status}`);
          return r.json();
        })
        .then(data => {
          if (data) loadStudioConfigFromObject(data, 'http_loaded', 'config/studio-config.json');
        })
        .catch(() => {
          // file:/// CORS fallback or offline: embedded defaults remain active with clear status
          CONFIG_STATE.source = 'embedded_default';
          CONFIG_STATE.sourceName = 'Embedded Defaults';
          CONFIG_STATE.isDefault = true;
          updateConfigUI();
        });
    } else {
      updateConfigUI();
    }
  } catch (e) {
    updateConfigUI();
  }
}

function escapeHtml(str) {
  if (typeof str !== 'string') return '';
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

function validateDraftBundle(data) {
  const errors = [];

  if (!data || typeof data !== 'object' || Array.isArray(data)) {
    return { valid: false, errors: ['Draft bundle must be a JSON dictionary object.'] };
  }

  // Schema marker
  if (data.schema !== 'agent-design-draft-v1') {
    errors.push(`Invalid or missing schema: expected 'agent-design-draft-v1', got '${data.schema}'.`);
  }

  // Bundle metadata
  const meta = data.bundle_metadata;
  if (!meta || typeof meta !== 'object' || Array.isArray(meta)) {
    errors.push('Missing or invalid bundle_metadata section.');
  } else {
    for (const req of ['draft_id', 'title', 'author']) {
      if (!meta[req] || typeof meta[req] !== 'string' || !meta[req].trim()) {
        errors.push(`bundle_metadata.${req} is required as a non-empty string.`);
      }
    }
    if (!meta.created_at || typeof meta.created_at !== 'string' || !meta.created_at.trim()) {
      errors.push('bundle_metadata.created_at is required as a non-empty string.');
    }
    if (typeof meta.revision !== 'number' || !Number.isInteger(meta.revision) || meta.revision < 1) {
      errors.push('bundle_metadata.revision must be a positive integer (>= 1).');
    }
  }

  // Identity section
  const id = data.identity;
  if (!id || typeof id !== 'object' || Array.isArray(id)) {
    errors.push('Missing or invalid identity section.');
  } else {
    if (id.tier !== 'lightweight' && id.tier !== 'ultralight') {
      errors.push(`identity.tier must be 'lightweight' or 'ultralight', got '${id.tier}'.`);
    }
    if (!id.name || typeof id.name !== 'string' || !id.name.trim()) errors.push('identity.name is required.');
    if (!id.role || typeof id.role !== 'string' || !id.role.trim()) errors.push('identity.role is required.');

    for (const arrField of ['capabilities', 'exclusions', 'values']) {
      if (!Array.isArray(id[arrField]) || id[arrField].length === 0 || !id[arrField].every(x => typeof x === 'string')) {
        errors.push(`identity.${arrField} must be a non-empty array of strings.`);
      }
    }

    // Disposition and working style validation
    if (!Array.isArray(id.disposition_and_working_style)) {
      errors.push('identity.disposition_and_working_style must be an array (can be empty).');
    } else {
      const traitIds = new Set();
      const validEmphasis = ['unspecified', 'light', 'moderate', 'strong'];

      id.disposition_and_working_style.forEach((trait, index) => {
        if (!trait || typeof trait !== 'object' || Array.isArray(trait)) {
          errors.push(`Trait at index ${index} must be an object.`);
          return;
        }
        if (!trait.id || typeof trait.id !== 'string' || !trait.id.trim()) {
          errors.push(`Trait at index ${index} is missing a valid string 'id'.`);
        } else if (traitIds.has(trait.id)) {
          errors.push(`Duplicate trait identifier detected: '${trait.id}'.`);
        } else {
          traitIds.add(trait.id);
        }

        if (!trait.label || typeof trait.label !== 'string' || !trait.label.trim()) {
          errors.push(`Trait '${trait.id || index}' is missing a label.`);
        }
        if (!trait.description || typeof trait.description !== 'string' || !trait.description.trim()) {
          errors.push(`Trait '${trait.id || index}' is missing a behavioral description.`);
        }
        if (trait.emphasis && !validEmphasis.includes(trait.emphasis)) {
          errors.push(`Trait '${trait.id || index}' has invalid emphasis '${trait.emphasis}'. Allowed: ${validEmphasis.join(', ')}.`);
        }
      });
    }
  }

  // Deployment preferences validation & secret screening (Finding R3, R5)
  const dep = data.deployment_preferences;
  if (!dep || typeof dep !== 'object' || Array.isArray(dep)) {
    errors.push('Missing or invalid deployment_preferences section.');
  } else {
    if (!dep.credential_reference || typeof dep.credential_reference !== 'string') {
      errors.push('deployment_preferences.credential_reference is required.');
    } else {
      const cred = dep.credential_reference.trim();
      if (/^(sk-[a-zA-Z0-9_-]{20,}|Bearer\s+|ghp_[a-zA-Z0-9]{30,})/i.test(cred)) {
        errors.push(`SECURITY VIOLATION: deployment_preferences.credential_reference contains a live API key pattern. Live secrets are forbidden. Use reference format 'operator-managed:<id>'.`);
      } else if (!/^operator-managed:[a-zA-Z0-9_-]+$/.test(cred)) {
        errors.push(`deployment_preferences.credential_reference must match 'operator-managed:<id>', got '${cred}'.`);
      }
    }

    if (dep.context_limit !== undefined && (!Number.isInteger(dep.context_limit) || dep.context_limit < STUDIO_CONFIG.CONTEXT_MIN || dep.context_limit > STUDIO_CONFIG.CONTEXT_MAX)) {
      errors.push(`deployment_preferences.context_limit must be an integer between ${STUDIO_CONFIG.CONTEXT_MIN} and ${STUDIO_CONFIG.CONTEXT_MAX}, got ${dep.context_limit}`);
    }
    if (dep.output_limit !== undefined && (!Number.isInteger(dep.output_limit) || dep.output_limit < STUDIO_CONFIG.OUTPUT_MIN || dep.output_limit > STUDIO_CONFIG.OUTPUT_MAX)) {
      errors.push(`deployment_preferences.output_limit must be an integer between ${STUDIO_CONFIG.OUTPUT_MIN} and ${STUDIO_CONFIG.OUTPUT_MAX}, got ${dep.output_limit}`);
    }
    if (dep.request_timeout_sec !== undefined && (!Number.isInteger(dep.request_timeout_sec) || dep.request_timeout_sec < STUDIO_CONFIG.REQ_TIMEOUT_MIN || dep.request_timeout_sec > STUDIO_CONFIG.REQ_TIMEOUT_MAX)) {
      errors.push(`deployment_preferences.request_timeout_sec must be an integer between ${STUDIO_CONFIG.REQ_TIMEOUT_MIN} and ${STUDIO_CONFIG.REQ_TIMEOUT_MAX}, got ${dep.request_timeout_sec}`);
    }
    if (dep.run_timeout_sec !== undefined && (!Number.isInteger(dep.run_timeout_sec) || dep.run_timeout_sec < STUDIO_CONFIG.RUN_TIMEOUT_MIN || dep.run_timeout_sec > STUDIO_CONFIG.RUN_TIMEOUT_MAX)) {
      errors.push(`deployment_preferences.run_timeout_sec must be an integer between ${STUDIO_CONFIG.RUN_TIMEOUT_MIN} and ${STUDIO_CONFIG.RUN_TIMEOUT_MAX}, got ${dep.run_timeout_sec}`);
    }
    if (dep.tool_call_limit !== undefined && (!Number.isInteger(dep.tool_call_limit) || dep.tool_call_limit < STUDIO_CONFIG.TOOL_CALLS_MIN || dep.tool_call_limit > STUDIO_CONFIG.TOOL_CALLS_MAX)) {
      errors.push(`deployment_preferences.tool_call_limit must be an integer between ${STUDIO_CONFIG.TOOL_CALLS_MIN} and ${STUDIO_CONFIG.TOOL_CALLS_MAX}, got ${dep.tool_call_limit}`);
    }
  }

  // Mock roster validation & override screening (Finding R3)
  if (data.mock_roster !== undefined) {
    if (!Array.isArray(data.mock_roster)) {
      errors.push('mock_roster must be an array.');
    } else {
      data.mock_roster.forEach((item, idx) => {
        if (!item || typeof item !== 'object' || Array.isArray(item)) {
          errors.push(`mock_roster[${idx}] must be an object.`);
          return;
        }
        if (!item.instance_id || typeof item.instance_id !== 'string') {
          errors.push(`mock_roster[${idx}].instance_id is required as a string.`);
        }
        if (!item.assigned_role || typeof item.assigned_role !== 'string') {
          errors.push(`mock_roster[${idx}].assigned_role is required as a string.`);
        }
        if (item.deployment_override !== undefined) {
          if (!item.deployment_override || typeof item.deployment_override !== 'object' || Array.isArray(item.deployment_override)) {
            errors.push(`mock_roster[${idx}].deployment_override must be an object.`);
          } else {
            const override = item.deployment_override;
            const overCred = override.credential_reference;
            if (overCred !== undefined) {
              if (typeof overCred !== 'string') {
                errors.push(`mock_roster[${idx}].deployment_override.credential_reference must be a string.`);
              } else {
                const trimmed = overCred.trim();
                if (/^(sk-[a-zA-Z0-9_-]{20,}|Bearer\s+|ghp_[a-zA-Z0-9]{30,})/i.test(trimmed)) {
                  errors.push(`SECURITY VIOLATION: mock_roster[${idx}].deployment_override contains live API key pattern. Live secrets are forbidden. Use 'operator-managed:<id>'.`);
                } else if (!/^operator-managed:[a-zA-Z0-9_-]+$/.test(trimmed)) {
                  errors.push(`mock_roster[${idx}].deployment_override credential must match 'operator-managed:<id>', got '${trimmed}'.`);
                }
              }
            }
            if (override.context_limit !== undefined && (!Number.isInteger(override.context_limit) || override.context_limit < STUDIO_CONFIG.CONTEXT_MIN || override.context_limit > STUDIO_CONFIG.CONTEXT_MAX)) {
              errors.push(`mock_roster[${idx}].deployment_override.context_limit must be an integer between ${STUDIO_CONFIG.CONTEXT_MIN} and ${STUDIO_CONFIG.CONTEXT_MAX}.`);
            }
            if (override.output_limit !== undefined && (!Number.isInteger(override.output_limit) || override.output_limit < STUDIO_CONFIG.OUTPUT_MIN || override.output_limit > STUDIO_CONFIG.OUTPUT_MAX)) {
              errors.push(`mock_roster[${idx}].deployment_override.output_limit must be an integer between ${STUDIO_CONFIG.OUTPUT_MIN} and ${STUDIO_CONFIG.OUTPUT_MAX}.`);
            }
            if (override.tool_call_limit !== undefined && (!Number.isInteger(override.tool_call_limit) || override.tool_call_limit < STUDIO_CONFIG.TOOL_CALLS_MIN || override.tool_call_limit > STUDIO_CONFIG.TOOL_CALLS_MAX)) {
              errors.push(`mock_roster[${idx}].deployment_override.tool_call_limit must be an integer between ${STUDIO_CONFIG.TOOL_CALLS_MIN} and ${STUDIO_CONFIG.TOOL_CALLS_MAX}.`);
            }
          }
        }
      });
    }
  }

  // Strategies validation (Reusable candidate definitions)
  const strategyIds = new Set();
  if (data.strategies !== undefined) {
    if (!Array.isArray(data.strategies)) {
      errors.push('strategies must be an array.');
    } else {
      const validSov = ['accepted', 'declined', 'not_applicable', 'unresolved'];
      data.strategies.forEach((strat, sIdx) => {
        if (!strat || typeof strat !== 'object' || Array.isArray(strat)) {
          errors.push(`strategies[${sIdx}] must be an object.`);
          return;
        }
        for (const req of ['id', 'title', 'problem_class']) {
          if (!strat[req] || typeof strat[req] !== 'string' || !strat[req].trim()) {
            errors.push(`strategies[${sIdx}].${req} is required as a string.`);
          }
        }
        if (strat.id) strategyIds.add(strat.id);

        const checkStrArray = (arr, name) => {
          if (!Array.isArray(arr) || !arr.every(x => typeof x === 'string')) {
            errors.push(`strategies[${sIdx}].${name} must be an array of strings.`);
          }
        };

        checkStrArray(strat.assumptions, 'assumptions');
        checkStrArray(strat.step_sequence, 'step_sequence');
        if (strat.invariants !== undefined) checkStrArray(strat.invariants, 'invariants');
        if (strat.observable_checks !== undefined) checkStrArray(strat.observable_checks, 'observable_checks');
        if (strat.stop_switch_conditions !== undefined) checkStrArray(strat.stop_switch_conditions, 'stop_switch_conditions');

        if (strat.sovereignty_state && !validSov.includes(strat.sovereignty_state)) {
          errors.push(`strategies[${sIdx}].sovereignty_state must be one of: ${validSov.join(', ')}, got '${strat.sovereignty_state}'.`);
        }
      });
    }
  }

  // Strategy Applications validation (Separated recipient task state per R6)
  if (data.strategy_applications !== undefined) {
    if (!Array.isArray(data.strategy_applications)) {
      errors.push('strategy_applications must be an array.');
    } else {
      const validSov = ['accepted', 'declined', 'not_applicable', 'unresolved'];
      data.strategy_applications.forEach((app, aIdx) => {
        if (!app || typeof app !== 'object' || Array.isArray(app)) {
          errors.push(`strategy_applications[${aIdx}] must be an object.`);
          return;
        }
        for (const req of ['application_id', 'strategy_id', 'recipient_agent_id', 'task_context']) {
          if (!app[req] || typeof app[req] !== 'string' || !app[req].trim()) {
            errors.push(`strategy_applications[${aIdx}].${req} is required as a non-empty string.`);
          }
        }
        if (app.strategy_id && strategyIds.size > 0 && !strategyIds.has(app.strategy_id)) {
          errors.push(`strategy_applications[${aIdx}].strategy_id '${app.strategy_id}' does not match any defined strategy.`);
        }
        if (app.strategy_revision !== undefined && (!Number.isInteger(app.strategy_revision) || app.strategy_revision < 1)) {
          errors.push(`strategy_applications[${aIdx}].strategy_revision must be a positive integer (>= 1).`);
        }
        if (!app.sovereignty_state || !validSov.includes(app.sovereignty_state)) {
          errors.push(`strategy_applications[${aIdx}].sovereignty_state must be one of: ${validSov.join(', ')}.`);
        } else if (app.sovereignty_state === 'declined') {
          if (!app.decline_reason || typeof app.decline_reason !== 'string' || !app.decline_reason.trim()) {
            errors.push(`strategy_applications[${aIdx}] with sovereignty_state 'declined' requires a non-empty decline_reason.`);
          }
        }
      });
    }
  }

  return { valid: errors.length === 0, errors };
}

// --- 2. Identity Editor Constants & Helpers (Finding R5) ---

const STANDARD_LAW_TEXT = `LAW -- Integrity Floor, Route Discipline, Packet Scope

Every deliverable states its outcome plainly, including exact gaps or blockers when present; the literal tokens COMPLETE, PARTIAL, and BLOCKED are optional. Silent partials are forbidden. Every claim that gates an acceptance criterion cites the exact file path and line/section; quoted verbatim when load-bearing. Tool results are ground truth -- never override them with self-estimation or inference.

Route: never claim a handoff that did not occur. Follow the named Route Next or explicitly suggest a better route; escalate on budget/tool/root gaps rather than improvising around them. An evidenced escalation is a correct outcome, not a defect.

Scope: work only within the tools, roots, and file paths granted in TOOLS_GRANTED; if a needed capability is missing, return Status Blocked naming it -- never improvise around gaps or fabricate tool output.`;

const AETHER_MICRO_TAGS_CARD = `AETHER-u TAGS — optional labels that can make a reply easier to inspect:
[RESULT]  something you produced or concluded (plain prose is also valid)
[SOURCE]  where a fact came from: file + line(s). Every factual claim needs one.
[UNSURE]  you are not certain — say so here, never inside a [RESULT]
[ASK]     you need something you don't have — name it exactly; this logs a selector miss
[NOTE]    optional lesson learned; use as much space as the lesson genuinely needs

A final STATUS line is helpful but not required. When used:
STATUS: COMPLETE | PARTIAL — <what is missing> | BLOCKED — <what stops you>

RULES: Never invent a [SOURCE]. Tool output outranks your memory. Plainly stating a gap,
ask, partial result, or blocker is correct even when it does not use the suggested labels.`;

const SUGGESTED_TRAIT_PRESETS = [
  {
    id: 'trait-proactive',
    label: 'Aggressive / proactive',
    description: 'Pursue promising approaches energetically and take initiative within the assignment.',
    defaultEmphasis: 'unspecified'
  },
  {
    id: 'trait-skeptic',
    label: 'Skeptic',
    description: 'Challenge assumptions, seek counterexamples and revise objections when evidence answers them.',
    defaultEmphasis: 'unspecified'
  },
  {
    id: 'trait-believer',
    label: 'Believer / constructive advocate',
    description: 'Develop the strongest plausible version of an idea and explore how it could work, while keeping assumptions visible and responding to contrary evidence.',
    defaultEmphasis: 'unspecified'
  },
  {
    id: 'trait-detail',
    label: 'Detail-oriented',
    description: 'Check edge cases, consistency, exact requirements and supporting evidence.',
    defaultEmphasis: 'unspecified'
  },
  {
    id: 'trait-cautious',
    label: 'Cautious',
    description: 'Prefer reversible steps and investigate uncertainty before consequential action.',
    defaultEmphasis: 'unspecified'
  }
];

function estimateTokens(text) {
  if (!text) return 0;
  const words = text.trim().split(/\s+/).filter(Boolean).length;
  const chars = text.length;
  return Math.max(Math.ceil(chars / 3.8), Math.ceil(words * 1.3));
}

function detectAdvisoryTensions(traits) {
  const notices = [];
  if (!Array.isArray(traits) || traits.length < 2) return notices;
  const labels = traits.map(t => (t.label || '').toLowerCase());

  const hasProactive = labels.some(l => l.includes('aggressive') || l.includes('proactive'));
  const hasCautious = labels.some(l => l.includes('cautious'));
  if (hasProactive && hasCautious) {
    notices.push(
      'Advisory: Proactive and Cautious tendencies coexist. This combination is valid and supported; use the Context field to clarify when to take initiative vs. when to favour reversible investigation.'
    );
  }

  const hasSkeptic = labels.some(l => l.includes('skeptic'));
  const hasBeliever = labels.some(l => l.includes('believer') || l.includes('advocate'));
  if (hasSkeptic && hasBeliever) {
    notices.push(
      'Advisory: Constructive Advocate and Skeptical tendencies coexist. Ensure applicability contexts delineate exploratory synthesis phases from critical verification gates.'
    );
  }

  return notices;
}

function generateTierPreview(bundle) {
  const identity = bundle.identity || {};
  const tier = identity.tier || 'lightweight';
  const name = identity.name || 'UnnamedAgent';
  const role = identity.role || 'Unspecified Role';
  const values = identity.values || [];
  const capabilities = identity.capabilities || [];
  const exclusions = identity.exclusions || [];
  const traits = identity.disposition_and_working_style || [];
  const dep = bundle.deployment_preferences || {};
  const configuredContext = dep.context_limit || 8192;

  if (tier === 'ultralight') {
    let whoSection = `WHO -- ${name} * ${role}\n`;
    if (values.length) whoSection += `VALUES: ${values.join(' · ')}\n`;
    if (capabilities.length) whoSection += `CAPABILITIES: ${capabilities.join('; ')}\n`;
    if (exclusions.length) whoSection += `EXCLUSIONS: ${exclusions.join('; ')}\n`;

    if (traits.length > 0) {
      whoSection += `DISPOSITION:\n`;
      traits.forEach(t => {
        const emp = t.emphasis && t.emphasis !== 'unspecified' ? ` (${t.emphasis})` : '';
        const ctx = t.context ? ` [Context: ${t.context}]` : '';
        whoSection += `  - ${t.label}${emp}: ${t.description}${ctx}\n`;
      });
    }

    const microcasePreview = `=== AETHER MICROCASE ===
FORMAT: MICROCASE-1
AGENT: ${name} · ROLE: ${role} · EST_TOKENS: {EST_TOKENS}
SEAL: [DRAFT-UNSEALED] · SHA256: [COMPUTED_AT_EXPORT]
--- WHO ---
${whoSection.trimEnd()}
--- LAW ---
${STANDARD_LAW_TEXT}
--- TAGS ---
${AETHER_MICRO_TAGS_CARD}
--- TASK ---

=== END MICROCASE ===`;

    const est = estimateTokens(microcasePreview);
    const finalPreview = microcasePreview.replace('{EST_TOKENS}', est);

    return {
      tier: 'ultralight',
      text: finalPreview,
      tokenCount: est,
      targetTokens: 1500,
      ceilingTokens: 2000,
      configuredContext: configuredContext
    };
  } else {
    let dispositionBlock = '';
    if (traits.length > 0) {
      dispositionBlock = `\n[WORKING_DISPOSITION]\n`;
      traits.forEach(t => {
        const emp = t.emphasis && t.emphasis !== 'unspecified' ? ` (${t.emphasis})` : '';
        const ctx = t.context ? ` [Context: ${t.context}]` : '';
        dispositionBlock += `  [TRAIT] ⌜${t.label}⌝${emp} := ⌞${t.description}${ctx}⌟\n`;
      });
    }

    const lwPreview = `⟪AETHER_UNIFIED_STREAM⟫
⟪VERSION::1.0⟫
⟪FORMAT::AETHER_SYMBOLIC_STREAM⟫
⟪TIMESTAMP::${new Date().toISOString()}⟫

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⟪SYSTEM_0_BEGIN⟫
⟪TYPE::CORE_IDENTITY_BLOCK⟫

[CORE_IDENTITY]
  [ASSERT] ⌜AGENT_NAME⌝       := ⌞${name}⌟
  [ASSERT] ⌜AGENT_ID⌝         := ⌞${identity.agent_id_template || `Agent_${name}_001`}⌟
  [ASSERT] ⌜AGENT_ROLE⌝       := ⌞${role}⌟
  [ASSERT] ⌜AGENT_ORIGIN⌝     := ⌞${identity.origin || 'Aether Project · Agent Design Studio'}⌟
  [ASSERT] ⌜AGENT_ALIGNMENT⌝  := ⌞${values.join(' · ')}⌟
  [ASSERT] ⌜AGENT_METHOD⌝     := ⌞${capabilities.join('; ')}⌟
${dispositionBlock}
[PRIME_CONTEXT_STACK]
  [ASSERT] ⌜EXCLUSIONS⌝       := ⌞${exclusions.join('; ')}⌟
  [ASSERT] ⌜HANDOFF_EXPECTATIONS⌝ := ⌞${identity.handoff_expectations || 'Standard verified handoff'}⌟

[CHECKSUM_SHA256_CORE_IDENTITY] := ⌜UNVERIFIED_RUNTIME⌟
⟪SYSTEM_0_END⟫

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⟪SYSTEM_2_BEGIN⟫
⟪TYPE::PERSISTENT_MEMORY_INDEX_BLOCK⟫

[REFLECTION_INDEX] := [
]
⟪SYSTEM_2_END⟫

// ─────────────────────────────────────────────────────────────────────────────
// CHANGE LOG:
// - Initial template authored in Agent Design Studio (draft-v1)`;

    const est = estimateTokens(lwPreview);
    return {
      tier: 'lightweight',
      text: lwPreview,
      tokenCount: est,
      targetTokens: Math.min(8192, Math.floor(configuredContext * 0.5)),
      ceilingTokens: configuredContext,
      configuredContext: configuredContext
    };
  }
}

// --- 3. Mock Roster Renderer (Finding R1) ---

function renderRosterTable(roster) {
  if (!Array.isArray(roster) || roster.length === 0) {
    return `<div class="empty-state" style="padding: 24px; text-align: center; color: var(--text-muted);">
      No mock agents assigned yet. Click "+ Assign Template to Roster" to add an instance.
    </div>`;
  }

  const idCounts = {};
  roster.forEach(item => {
    idCounts[item.instance_id] = (idCounts[item.instance_id] || 0) + 1;
  });

  let html = `<table class="data-table">
    <thead>
      <tr>
        <th>Instance ID</th>
        <th>Assigned Role</th>
        <th>Deployment Credential</th>
        <th>Runtime Status</th>
        <th>Actions</th>
      </tr>
    </thead>
    <tbody>`;

  roster.forEach((item, index) => {
    const isDuplicate = idCounts[item.instance_id] > 1;
    const dupBadge = isDuplicate ? `<span style="color: var(--accent-red); font-weight: bold; margin-left: 6px;">[DUPLICATE ID]</span>` : '';
    const credRef = item.deployment_override?.credential_reference || 'operator-managed:default';

    html += `<tr>
      <td><code>${escapeHtml(item.instance_id)}</code>${dupBadge}</td>
      <td>${escapeHtml(item.assigned_role)}</td>
      <td><code>${escapeHtml(credRef)}</code></td>
      <td><span class="status-pill" style="font-size: 11px; color: var(--accent-amber);">Not Connected</span></td>
      <td>
        <button class="btn-icon-danger remove-roster-btn" data-index="${index}" title="Remove from roster">&times;</button>
      </td>
    </tr>`;
  });

  html += `</tbody></table>`;
  return html;
}

// --- 4. Strategy Workbench View Formatters (Findings R1, R6) ---

function renderStrategyList(strategies, selectedIndex = 0, applications = []) {
  if (!Array.isArray(strategies) || strategies.length === 0) {
    return `<div style="padding: 16px; text-align: center; color: var(--text-muted);">
      No strategies attached to this draft bundle yet. Click "+ Draft New Strategy" above.
    </div>`;
  }

  let html = `<div style="display: flex; flex-direction: column; gap: 8px;">`;
  strategies.forEach((strat, index) => {
    const isSelected = index === selectedIndex;
    const activeClass = isSelected ? 'border-color: var(--accent-blue); background-color: rgba(88, 166, 255, 0.08);' : '';
    
    // Evaluate application summary for this candidate (Finding R6)
    const stratApps = (applications || []).filter(a => a && a.strategy_id === strat.id);
    let evalBadge = '';
    if (stratApps.length > 0) {
      const accepted = stratApps.filter(a => a.sovereignty_state === 'accepted').length;
      const declined = stratApps.filter(a => a.sovereignty_state === 'declined').length;
      const parts = [];
      if (accepted > 0) parts.push(`<span style="color: var(--accent-green); font-size: 11px; font-weight: bold;">${accepted} accepted</span>`);
      if (declined > 0) parts.push(`<span style="color: var(--accent-red); font-size: 11px; font-weight: bold;">${declined} declined</span>`);
      const pending = stratApps.length - accepted - declined;
      if (pending > 0) parts.push(`<span style="color: var(--accent-amber); font-size: 11px;">${pending} pending</span>`);
      evalBadge = `<span style="font-size: 11px;">[${parts.join(' · ')}]</span>`;
    } else {
      evalBadge = `<span style="color: var(--text-muted); font-size: 11px;">[0 evaluations]</span>`;
    }

    html += `<div class="strategy-list-item" data-index="${index}" style="padding: 10px 14px; border: 1px solid var(--border-color); border-radius: var(--radius-sm); cursor: pointer; display: flex; justify-content: space-between; align-items: center; ${activeClass}">
      <div>
        <div style="font-weight: 600; font-size: 13px;">${escapeHtml(strat.title || 'Untitled Strategy')} <span style="font-size: 10px; color: var(--text-muted); font-family: var(--font-mono);">rev ${strat.revision || 1}</span></div>
        <div style="font-size: 11px; color: var(--text-secondary);">${escapeHtml(strat.problem_class || 'No problem class specified')}</div>
      </div>
      <div style="display: flex; align-items: center; gap: 8px;">
        ${evalBadge}
        <button class="btn-icon-danger remove-strat-btn" data-index="${index}" title="Remove Strategy">&times;</button>
      </div>
    </div>`;
  });
  html += `</div>`;
  return html;
}

function formatStrategyPlainLanguage(strat, applications = []) {
  if (!strat) return 'No strategy selected.';
  const lines = [
    `### Strategy Candidate: ${strat.title || 'Untitled'} (Rev ${strat.revision || 1})`,
    `**Target Problem Class:** ${strat.problem_class || 'Unspecified'}`,
    `**Observed Experience / Rationale:** ${strat.rationale_experience || 'None recorded'}`,
    `**Attribution / Lineage:** ${strat.attribution || 'Unspecified'}`,
    '',
    '**Assumptions:**',
    ...(strat.assumptions && strat.assumptions.length > 0
      ? strat.assumptions.map(a => `  - ${a}`)
      : ['  - None']),
    '',
    '**Invariants:**',
    ...(strat.invariants && strat.invariants.length > 0
      ? strat.invariants.map(inv => `  - ${inv}`)
      : ['  - None']),
    '',
    '**Operational Step Sequence:**',
    ...(strat.step_sequence && strat.step_sequence.length > 0
      ? strat.step_sequence.map((step, idx) => `  ${idx + 1}. ${step}`)
      : ['  - None']),
    '',
    '**Observable Verification Checks:**',
    ...(strat.observable_checks && strat.observable_checks.length > 0
      ? strat.observable_checks.map(chk => `  [✓] ${chk}`)
      : ['  - None']),
    '',
    '**Stop / Switch Conditions:**',
    ...(strat.stop_switch_conditions && strat.stop_switch_conditions.length > 0
      ? strat.stop_switch_conditions.map(cond => `  [!] ${cond}`)
      : ['  - None'])
  ];

  if (applications && applications.length > 0) {
    lines.push('', '---', '### Recipient Task Evaluations (Independent Applications):');
    applications.forEach((app, idx) => {
      const reason = app.sovereignty_state === 'declined' && app.decline_reason
        ? `\n    Decline Reason: ${app.decline_reason}`
        : '';
      const notes = app.evaluation_notes ? `\n    Notes: ${app.evaluation_notes}` : '';
      lines.push(
        `  ${idx + 1}. [${app.sovereignty_state.toUpperCase()}] Agent: ${app.recipient_agent_id} · Task: ${app.task_context} (Bound rev: v${app.strategy_revision || 1})${reason}${notes}`
      );
    });
  }

  return lines.join('\n');
}

function formatStrategyWMC(strat, applications = []) {
  if (!strat) return 'No strategy selected.';
  let appBlock = '';
  if (applications && applications.length > 0) {
    appBlock = '\n\n[RECIPIENT_TASK_EVALUATIONS]\n' + applications.map((app, idx) => {
      const reason = app.sovereignty_state === 'declined' && app.decline_reason ? `\n    ⟪REASON::${app.decline_reason}⟫` : '';
      return `  [APP_${idx + 1}] ⌜${app.task_context}⌝ (${app.recipient_agent_id}, bound: v${app.strategy_revision || 1}) ➔ ⌞${app.sovereignty_state.toUpperCase()}⌟${reason}`;
    }).join('\n');
  }

  return `⟪COGNITIVE_STRATEGY⟫
⟪ID::${strat.id || 'strat-draft'}⟫
⟪REVISION::${strat.revision || 1}⟫
⟪TITLE::${strat.title || 'Untitled'}⟫
[PROBLEM_FRAME]
  CLASS      := ⌞${strat.problem_class || 'Unspecified'}⌟
  RATIONALE  := ⌞${strat.rationale_experience || 'None'}⌟

[ASSUMPTIONS]
${(strat.assumptions || []).map(a => `  ⊢ ⌞${a}⌟`).join('\n') || '  ⊢ ⌞None⌟'}

[INVARIANTS]
${(strat.invariants || []).map(inv => `  🔒 ⌜INVARIANT⌝ := ⌞${inv}⌟`).join('\n') || '  🔒 ⌜INVARIANT⌝ := ⌞None⌟'}

[PROCEDURE]
${(strat.step_sequence || []).map((step, idx) => `  ${idx + 1} ➔ ⌞${step}⌟`).join('\n') || '  1 ➔ ⌞None⌟'}

[OBSERVABLE_CHECKS]
${(strat.observable_checks || []).map(chk => `  ☑ ⌞${chk}⌟`).join('\n') || '  ☑ ⌞None⌟'}

[TERMINATION_CONDITIONS]
${(strat.stop_switch_conditions || []).map(cond => `  🛑 ⌞${cond}⌟`).join('\n') || '  🛑 ⌞None⌟'}${appBlock}
⟪END_COGNITIVE_STRATEGY⟫`;
}

// --- 5. Pure JavaScript SHA-256 & Storage Functions (Finding R3, R4) ---

function sha256Sync(ascii) {
  function rightRotate(value, amount) {
    return (value >>> amount) | (value << (32 - amount));
  }
  const mathPow = Math.pow;
  const maxWord = mathPow(2, 32);
  let lengthProperty = 'length';
  let i, j;
  let result = '';

  const words = [];
  const asciiBitLength = ascii[lengthProperty] * 8;

  let hash = [];
  let k = [];
  let primeCounter = 0;

  const isComposite = {};
  for (let candidate = 2; primeCounter < 64; candidate++) {
    if (!isComposite[candidate]) {
      for (i = 0; i < 313; i += candidate) {
        isComposite[i] = candidate;
      }
      hash[primeCounter] = (mathPow(candidate, 0.5) * maxWord) | 0;
      k[primeCounter++] = (mathPow(candidate, 1 / 3) * maxWord) | 0;
    }
  }

  ascii += '\x80';
  while ((ascii[lengthProperty] % 64) - 56) ascii += '\x00';
  for (i = 0; i < ascii[lengthProperty]; i++) {
    j = ascii.charCodeAt(i);
    if (j >> 8) return '';
    words[i >> 2] |= j << (((3 - i) % 4) * 8);
  }
  words[words[lengthProperty]] = (asciiBitLength / maxWord) | 0;
  words[words[lengthProperty]] = asciiBitLength;

  for (j = 0; j < words[lengthProperty]; ) {
    const w = words.slice(j, (j += 16));
    const oldHash = hash;
    hash = hash.slice(0, 8);

    for (i = 0; i < 64; i++) {
      const w15 = w[i - 15], w2 = w[i - 2];
      const s0 = rightRotate(w15, 7) ^ rightRotate(w15, 18) ^ (w15 >>> 3);
      const s1 = rightRotate(w2, 17) ^ rightRotate(w2, 19) ^ (w2 >>> 10);
      w[i] = i < 16 ? w[i] : (w[i - 16] + s0 + w[i - 7] + s1) | 0;

      const s1h = rightRotate(hash[4], 6) ^ rightRotate(hash[4], 11) ^ rightRotate(hash[4], 25);
      const ch = (hash[4] & hash[5]) ^ (~hash[4] & hash[6]);
      const temp1 = (hash[7] + s1h + ch + k[i] + w[i]) | 0;
      const s0h = rightRotate(hash[0], 2) ^ rightRotate(hash[0], 13) ^ rightRotate(hash[0], 22);
      const maj = (hash[0] & hash[1]) ^ (hash[0] & hash[2]) ^ (hash[1] & hash[2]);
      const temp2 = (s0h + maj) | 0;

      hash = [(temp1 + temp2) | 0].concat(hash);
      hash[4] = (hash[4] + temp1) | 0;
    }

    for (i = 0; i < 8; i++) {
      hash[i] = (hash[i] + oldHash[i]) | 0;
    }
  }

  for (i = 0; i < 8; i++) {
    for (j = 3; j + 1; j--) {
      const b = (hash[i] >> (j * 8)) & 255;
      result += (b < 16 ? '0' : '') + b.toString(16);
    }
  }
  return result;
}

async function computeDigest(text) {
  const encoder = new TextEncoder();
  const data = encoder.encode(text);
  if (window.crypto && window.crypto.subtle) {
    try {
      const hashBuffer = await window.crypto.subtle.digest('SHA-256', data);
      const hashArray = Array.from(new Uint8Array(hashBuffer));
      return hashArray.map((b) => b.toString(16).padStart(2, '0')).join('');
    } catch (e) {
      // fallback to sync
    }
  }
  let binary = '';
  for (let i = 0; i < data.length; i++) {
    binary += String.fromCharCode(data[i]);
  }
  return sha256Sync(binary);
}

async function downloadDraftBundle(bundle) {
  // Strip any imported validation report before computing digest of authored bundle content
  const exportPayload = JSON.parse(JSON.stringify(bundle));
  delete exportPayload.validation_report;

  const jsonString = JSON.stringify(exportPayload, null, 2) + '\n';
  const digest = await computeDigest(jsonString);
  const blob = new Blob([jsonString], { type: 'application/json;charset=utf-8' });
  const filename = `draft_${bundle.identity?.name?.toLowerCase() || 'agent'}_v${bundle.bundle_metadata?.revision || 1}.json`;

  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);

  return { filename, digest };
}

async function readAndValidateBundleFile(file) {
  return new Promise((resolve) => {
    // Configurable size limit from STUDIO_CONFIG (Finding R3)
    if (file.size > STUDIO_CONFIG.MAX_IMPORT_BYTES) {
      resolve({
        ok: false,
        bundle: null,
        errors: [`File size (${(file.size / 1024).toFixed(1)} KB) exceeds configured limit of ${(STUDIO_CONFIG.MAX_IMPORT_BYTES / 1024).toFixed(0)} KB.`],
        digest: ''
      });
      return;
    }

    const reader = new FileReader();
    reader.onload = async (e) => {
      try {
        const text = e.target.result;
        const digest = await computeDigest(text);
        const parsed = JSON.parse(text);
        const validation = validateDraftBundle(parsed);

        resolve({
          ok: validation.valid,
          bundle: parsed,
          errors: validation.errors,
          digest
        });
      } catch (err) {
        resolve({
          ok: false,
          bundle: null,
          errors: [`Failed to parse JSON file: ${err.message}`],
          digest: ''
        });
      }
    };
    reader.onerror = () => {
      resolve({
        ok: false,
        bundle: null,
        errors: ['File read error.'],
        digest: ''
      });
    };
    reader.readAsText(file, 'utf-8');
  });
}

// --- 6. In-Memory Embedded Fixtures ---

const EMBEDDED_FIXTURES = {
  sample_lw_template: {
    schema: "agent-design-draft-v1",
    bundle_metadata: {
      draft_id: "draft-lw-nova-001",
      title: "Nova Research Analyst Template",
      created_at: "2026-09-14T14:30:00Z",
      updated_at: "2026-09-14T14:30:00Z",
      author: "Michel & Gemini",
      revision: 1,
      lineage: ["Initial draft created in Agent Design Studio ADS-01"],
      notes: "Synthetic Lightweight research template demonstrating combined proactive and skeptical dispositions."
    },
    identity: {
      tier: "lightweight",
      name: "Nova",
      role: "Research Analyst and Literature Synthesiser",
      agent_id_template: "AgentResearch_Nova_001",
      origin: "Aether Project · Synthetic Cognition Lab",
      intended_task_size: "standard",
      capabilities: [
        "Literature decomposition and citation mapping",
        "Conflicting claims synthesis",
        "Structured evidence summary extraction"
      ],
      exclusions: [
        "No speculative assertions without cited sources",
        "Never fabricate citations or DOI links",
        "Never modify production codebase files directly"
      ],
      values: [
        "Precision",
        "Clarity",
        "Honest uncertainty",
        "Protocol discipline"
      ],
      handoff_expectations: "Deliver synthesized markdown report with verified references and explicit uncertainty catalog.",
      disposition_and_working_style: [
        {
          id: "trait-proactive",
          label: "Aggressive / proactive",
          description: "Pursue promising approaches energetically and take initiative within the assignment.",
          emphasis: "strong",
          context: "Initial exploration and uncovering relevant primary papers"
        },
        {
          id: "trait-skeptic",
          label: "Skeptic",
          description: "Challenge assumptions, seek counterexamples and revise objections when evidence answers them.",
          emphasis: "moderate",
          context: "Evaluating experimental validity and methodology claims"
        },
        {
          id: "trait-detail",
          label: "Detail-oriented",
          description: "Check edge cases, consistency, exact requirements and supporting evidence.",
          emphasis: "moderate",
          context: "Validating table data against raw paper extracts"
        }
      ]
    },
    deployment_preferences: {
      model_alias: "qwen-2.5-72b-instruct",
      backend_type: "operator_local",
      context_limit: 16384,
      output_limit: 4096,
      request_timeout_sec: 120,
      run_timeout_sec: 600,
      tool_call_limit: 20,
      credential_reference: "operator-managed:research-cluster",
      connection_status: "not_connected"
    },
    strategies: [
      {
        id: "strat-conflicting-citations",
        revision: 1,
        title: "Conflicting Literature Reconciliation",
        problem_class: "Contradictory findings between two peer-reviewed publications",
        rationale_experience: "Observed that apparent experimental contradictions often stem from subtle measurement conditions or sample preparations.",
        assumptions: [
          "Both papers report sincere experimental findings",
          "The difference in methodology is documented in the method sections"
        ],
        invariants: [
          "Never discard a conflicting reading without an explicit methodology difference"
        ],
        step_sequence: [
          "Align experimental controls across both papers",
          "Tabulate instrument precision and sample boundaries",
          "Formulate a synthesis hypothesis identifying the confounding variable"
        ],
        observable_checks: [
          "Table of differences contains exact page and section citations",
          "Both primary authors' measurement assumptions are explicitly listed"
        ],
        stop_switch_conditions: [
          "Stop if neither paper provides sufficient methodology details to isolate variables",
          "Switch to gap-flagging if raw data is unavailable"
        ],
        attribution: "Nova Core Authoring"
      },
      {
        id: "strat-numerical-boundary",
        revision: 1,
        title: "Bounded Numerical Optimization",
        problem_class: "Gradient search in bounded physical simulations",
        rationale_experience: "Empirically observed that unbounded optimization steps diverge across coordinate boundaries.",
        assumptions: [
          "System coordinates vary continuously",
          "Spatial conservation holds across each step"
        ],
        invariants: [
          "Never exceed delta bound without explicit step attenuation"
        ],
        step_sequence: [
          "Sample objective gradient at current coordinate",
          "Clip proposed step vector to safety epsilon",
          "Verify state continuity before applying step"
        ],
        observable_checks: [
          "Step size <= safety epsilon across all iterations",
          "State variables remain within physical envelope"
        ],
        stop_switch_conditions: [
          "Halt if objective gradient drops below convergence threshold",
          "Switch to grid search if discontinuity or jump anomaly is detected"
        ],
        attribution: "Numerical Optimization Workgroup"
      }
    ],
    strategy_applications: [
      {
        application_id: "app-lit-reconcile-01",
        strategy_id: "strat-conflicting-citations",
        strategy_revision: 1,
        recipient_agent_id: "Nova",
        task_context: "Reconciling contrasting published sensor measurements in literature survey",
        sovereignty_state: "accepted",
        decline_reason: "",
        evaluation_notes: "Applicable and verified; authors' calibration setups differ documented in section 4.",
        evaluated_at: "2026-09-14T14:30:00Z"
      },
      {
        application_id: "app-num-bound-continuous",
        strategy_id: "strat-numerical-boundary",
        strategy_revision: 1,
        recipient_agent_id: "Nova",
        task_context: "Calibrating sensor drift across continuous temperature gradient",
        sovereignty_state: "accepted",
        decline_reason: "",
        evaluation_notes: "Continuous drift satisfies spatial continuity assumptions.",
        evaluated_at: "2026-09-14T14:35:00Z"
      },
      {
        application_id: "app-num-bound-teleport",
        strategy_id: "strat-numerical-boundary",
        strategy_revision: 1,
        recipient_agent_id: "Nova",
        task_context: "Teleportation Game Simulation (Discontinuous Coordinate Jumping)",
        sovereignty_state: "declined",
        decline_reason: "Teleportation Game violates spatial continuity and conservation invariants assumed by numerical step bounds.",
        evaluation_notes: "Counterexample encountered: sudden coordinate jumps violate continuity invariant.",
        evaluated_at: "2026-09-14T14:40:00Z"
      }
    ],
    mock_roster: [
      {
        instance_id: "Nova@mock-lab",
        assigned_role: "Lead Literature Analyst",
        deployment_override: { context_limit: 32768 }
      }
    ]
  },

  sample_ul_template: {
    schema: "agent-design-draft-v1",
    bundle_metadata: {
      draft_id: "draft-ul-zephyr-001",
      title: "Zephyr Ultralight Telemetry Triage",
      created_at: "2026-09-14T14:45:00Z",
      updated_at: "2026-09-14T14:45:00Z",
      author: "Michel & Gemini",
      revision: 1,
      lineage: ["Initial draft created in Agent Design Studio ADS-01"],
      notes: "Synthetic Ultralight template adhering strictly to standard LAW/TAGS and empty TASK."
    },
    identity: {
      tier: "ultralight",
      name: "Zephyr",
      role: "Micro Telemetry Triage Specialist",
      agent_id_template: "AgentMicro_Zephyr_001",
      origin: "Aether Project · Ultralight Fleet",
      intended_task_size: "micro",
      capabilities: [
        "Fast-path anomaly discrimination",
        "Exact source citation for threshold violations",
        "Compact status reporting"
      ],
      exclusions: [
        "No multi-file codebase refactoring",
        "No speculative diagnosis without ground-truth logs",
        "Never alter standard LAW or TAGS text"
      ],
      values: [
        "Integrity floor",
        "Brevity",
        "Truthful uncertainty"
      ],
      handoff_expectations: "Return STATUS: COMPLETE with [RESULT] and [SOURCE], or STATUS: BLOCKED naming exact missing capability.",
      disposition_and_working_style: [
        {
          id: "trait-cautious",
          label: "Cautious",
          description: "Prefer reversible steps and investigate uncertainty before consequential action.",
          emphasis: "strong",
          context: "Evaluating intermittent telemetry spikes before triggering alert escalation"
        }
      ]
    },
    deployment_preferences: {
      model_alias: "qwen-2.5-coder-7b",
      backend_type: "operator_local",
      context_limit: 4096,
      output_limit: 1024,
      request_timeout_sec: 45,
      run_timeout_sec: 180,
      tool_call_limit: 6,
      credential_reference: "operator-managed:edge-telemetry",
      connection_status: "not_connected"
    },
    strategies: [],
    mock_roster: [
      {
        instance_id: "Zephyr@telemetry-sandbox",
        assigned_role: "Sensor Gatekeeper",
        deployment_override: {}
      }
    ]
  },

  empty_traits_template: {
    schema: "agent-design-draft-v1",
    bundle_metadata: {
      draft_id: "draft-lw-baseline-001",
      title: "Baseline Code Verification Template",
      created_at: "2026-09-14T15:00:00Z",
      updated_at: "2026-09-14T15:00:00Z",
      author: "Michel & Gemini",
      revision: 1,
      lineage: ["Initial draft created in Agent Design Studio ADS-01"],
      notes: "Synthetic Lightweight template with an empty disposition traits list demonstrating optionality."
    },
    identity: {
      tier: "lightweight",
      name: "Baseline",
      role: "Syntax & Formatting Verifier",
      agent_id_template: "AgentDev_Baseline_001",
      origin: "Aether Project · Tooling Division",
      intended_task_size: "standard",
      capabilities: [
        "JSON/YAML syntax verification",
        "Lint check execution"
      ],
      exclusions: [
        "No structural refactoring",
        "No modifying logic or test criteria"
      ],
      values: [
        "Precision",
        "Consistency"
      ],
      handoff_expectations: "Output pass/fail status with line numbers.",
      disposition_and_working_style: []
    },
    deployment_preferences: {
      model_alias: "qwen-2.5-coder-7b",
      backend_type: "operator_local",
      context_limit: 8192,
      output_limit: 2048,
      request_timeout_sec: 60,
      run_timeout_sec: 300,
      tool_call_limit: 10,
      credential_reference: "operator-managed:local-syntax",
      connection_status: "not_connected"
    },
    strategies: [],
    mock_roster: []
  },

  custom_traits_template: {
    schema: "agent-design-draft-v1",
    bundle_metadata: {
      draft_id: "draft-lw-artisan-001",
      title: "Artisan Architecture Prototyper",
      created_at: "2026-09-14T15:15:00Z",
      updated_at: "2026-09-14T15:15:00Z",
      author: "Michel & Gemini",
      revision: 1,
      lineage: ["Initial draft created in Agent Design Studio ADS-01"],
      notes: "Synthetic Lightweight template demonstrating author-defined custom traits."
    },
    identity: {
      tier: "lightweight",
      name: "Artisan",
      role: "System Architecture Prototyper",
      agent_id_template: "AgentArch_Artisan_001",
      origin: "Aether Project · Systems Innovation",
      intended_task_size: "complex",
      capabilities: [
        "Modular subsystem decomposition",
        "Steel-thread protocol design",
        "Interface boundary definition"
      ],
      exclusions: [
        "No invasive edits to core production files",
        "No unbounded dependency additions",
        "Never bypass runtime safety invariants"
      ],
      values: [
        "Modularity",
        "Elegance",
        "Reversibility",
        "Clarity"
      ],
      handoff_expectations: "Deliver working prototype scaffold and comprehensive integration documentation.",
      disposition_and_working_style: [
        {
          id: "trait-custom-prototyper",
          label: "Iterative Prototyper",
          description: "Construct rough functional slices rapidly to test structural fit before polishing interface details.",
          emphasis: "strong",
          context: "Early exploratory phases and architectural spikes"
        },
        {
          id: "trait-custom-steelthread",
          label: "Steel-Thread Verifier",
          description: "Trace single end-to-end execution paths through all architectural layers before broadening test coverage.",
          emphasis: "moderate",
          context: "Verifying new subsystem cross-boundary handoffs"
        }
      ]
    },
    deployment_preferences: {
      model_alias: "deepseek-coder-v2.5",
      backend_type: "operator_local",
      context_limit: 32768,
      output_limit: 8192,
      request_timeout_sec: 180,
      run_timeout_sec: 1200,
      tool_call_limit: 30,
      credential_reference: "operator-managed:design-studio",
      connection_status: "not_connected"
    },
    strategies: [],
    mock_roster: [
      {
        instance_id: "Artisan@sandbox-lab",
        assigned_role: "Lead Systems Prototyper",
        deployment_override: {}
      }
    ]
  }
};

// --- 7. Application Controller (Findings R4, R5, R6) ---

class StudioApp {
  constructor() {
    this.currentBundle = JSON.parse(JSON.stringify(EMBEDDED_FIXTURES.sample_lw_template));
    this.selectedStrategyIndex = 0;
    this.selectedAppIndex = 0;
    this.strategyViewMode = 'plain';
    this.isDirty = false;
    if (typeof window !== 'undefined') {
      window.__STUDIO_APP_INSTANCE__ = this;
    }
  }

  init() {
    this.bindTabNavigation();
    this.bindIdentityForm();
    this.bindDeploymentForm();
    this.bindTraitPresets();
    this.bindRosterControls();
    this.bindStrategyControls();
    this.bindStorageControls();
    this.bindConfigurationControls();

    // Initial render
    this.populateFormFromBundle();
    updateConfigUI();
    this.updateLivePreview();
    this.updateRosterView();
    this.updateStrategyView();
  }

  markDirty(reason = '') {
    this.isDirty = true;
    const badge = document.getElementById('export-status-badge');
    if (badge) {
      badge.textContent = 'Draft · Unsaved Changes';
      badge.style.backgroundColor = 'rgba(210, 153, 34, 0.15)';
      badge.style.color = 'var(--accent-amber)';
      badge.style.borderColor = 'rgba(210, 153, 34, 0.3)';
    }
    const digestDisplay = document.getElementById('export-digest-display');
    if (digestDisplay) {
      digestDisplay.textContent = 'Draft modified since last export (Unverified Snapshot)';
      digestDisplay.style.color = 'var(--accent-amber)';
    }
    const stateBadge = document.getElementById('draft-state-badge');
    if (stateBadge) {
      stateBadge.textContent = 'Draft In-Progress (Unsaved Edits)';
    }
  }

  markSaved(filename, digest) {
    this.isDirty = false;
    const badge = document.getElementById('export-status-badge');
    if (badge) {
      badge.textContent = 'Exported · Snapshot Bound';
      badge.style.backgroundColor = 'rgba(63, 185, 80, 0.15)';
      badge.style.color = 'var(--accent-green)';
      badge.style.borderColor = 'rgba(63, 185, 80, 0.3)';
    }
    const digestDisplay = document.getElementById('export-digest-display');
    if (digestDisplay) {
      digestDisplay.textContent = `Exported snapshot: ${filename} (SHA-256: ${digest})`;
      digestDisplay.style.color = 'var(--accent-green)';
      digestDisplay.style.display = 'block';
    }
    const stateBadge = document.getElementById('draft-state-badge');
    if (stateBadge) {
      stateBadge.textContent = 'Exported Snapshot Bound';
    }
  }

  bindTabNavigation() {
    const tabButtons = document.querySelectorAll('.tab-btn');
    tabButtons.forEach(btn => {
      btn.addEventListener('click', () => {
        const targetTab = btn.getAttribute('data-tab');
        tabButtons.forEach(b => b.classList.remove('active'));
        document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));

        btn.classList.add('active');
        const activePane = document.getElementById(`tab-${targetTab}`);
        if (activePane) activePane.classList.add('active');

        if (targetTab === 'preview') {
          this.updateLivePreview();
          const fullCode = document.getElementById('full-preview-code');
          if (fullCode) {
            const previewData = generateTierPreview(this.currentBundle);
            fullCode.textContent = previewData.text;
          }
        } else if (targetTab === 'roster') {
          this.updateRosterView();
        } else if (targetTab === 'strategy') {
          this.updateStrategyView();
        }
      });
    });
  }

  bindIdentityForm() {
    const nameInput = document.getElementById('field-name');
    const roleInput = document.getElementById('field-role');
    const idInput = document.getElementById('field-agent-id');
    const taskSizeInput = document.getElementById('field-task-size');
    const handoffInput = document.getElementById('field-handoff');
    const authorInput = document.getElementById('field-author');

    if (nameInput) nameInput.addEventListener('input', (e) => {
      this.currentBundle.identity.name = e.target.value;
      this.markDirty('name');
      this.updateLivePreview();
    });
    if (roleInput) roleInput.addEventListener('input', (e) => {
      this.currentBundle.identity.role = e.target.value;
      this.markDirty('role');
      this.updateLivePreview();
    });
    if (idInput) idInput.addEventListener('input', (e) => {
      this.currentBundle.identity.agent_id_template = e.target.value;
      this.markDirty('id');
      this.updateLivePreview();
    });
    if (taskSizeInput) taskSizeInput.addEventListener('change', (e) => {
      this.currentBundle.identity.intended_task_size = e.target.value;
      this.markDirty('task_size');
    });
    if (handoffInput) handoffInput.addEventListener('input', (e) => {
      this.currentBundle.identity.handoff_expectations = e.target.value;
      this.markDirty('handoff');
      this.updateLivePreview();
    });
    if (authorInput) authorInput.addEventListener('input', (e) => {
      this.currentBundle.bundle_metadata.author = e.target.value;
      this.markDirty('author');
    });

    const tierRadios = document.querySelectorAll('input[name="agent-tier"]');
    tierRadios.forEach(radio => {
      radio.addEventListener('change', (e) => {
        this.currentBundle.identity.tier = e.target.value;
        document.querySelectorAll('.tier-option').forEach(opt => {
          opt.classList.toggle('selected', opt.querySelector('input').checked);
        });
        this.markDirty('tier');
        this.updateLivePreview();
      });
    });

    this.setupTagInput('capabilities-tags', 'capabilities-input', 'capabilities');
    this.setupTagInput('exclusions-tags', 'exclusions-input', 'exclusions');
    this.setupTagInput('values-tags', 'values-input', 'values');
  }

  bindDeploymentForm() {
    const bindNum = (id, prop) => {
      const el = document.getElementById(id);
      if (el) el.addEventListener('input', (e) => {
        const val = parseInt(e.target.value, 10);
        if (!isNaN(val)) {
          this.currentBundle.deployment_preferences[prop] = val;
          this.markDirty(prop);
          this.updateLivePreview();
        }
      });
    };

    const bindStr = (id, prop) => {
      const el = document.getElementById(id);
      if (el) el.addEventListener('input', (e) => {
        this.currentBundle.deployment_preferences[prop] = e.target.value;
        this.markDirty(prop);
        this.updateLivePreview();
      });
    };

    bindStr('field-model-alias', 'model_alias');
    bindStr('field-backend-type', 'backend_type');
    bindNum('field-context-limit', 'context_limit');
    bindNum('field-output-limit', 'output_limit');
    bindStr('field-reasoning-pref', 'reasoning_preference');
    bindNum('field-tool-limit', 'tool_call_limit');
    bindNum('field-request-timeout', 'request_timeout_sec');
    bindNum('field-run-timeout', 'run_timeout_sec');
    bindStr('field-credential-ref', 'credential_reference');
  }

  setupTagInput(containerId, inputId, propertyName) {
    const container = document.getElementById(containerId);
    const input = document.getElementById(inputId);
    if (!container || !input) return;

    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ',') {
        e.preventDefault();
        const val = input.value.trim().replace(/^,+|,+$/g, '');
        if (val) {
          this.currentBundle.identity[propertyName] = this.currentBundle.identity[propertyName] || [];
          if (!this.currentBundle.identity[propertyName].includes(val)) {
            this.currentBundle.identity[propertyName].push(val);
            this.renderTags(containerId, inputId, propertyName);
            this.markDirty(propertyName);
            this.updateLivePreview();
          }
          input.value = '';
        }
      }
    });
  }

  renderTags(containerId, inputId, propertyName) {
    const container = document.getElementById(containerId);
    const input = document.getElementById(inputId);
    if (!container || !input) return;

    const existingChips = container.querySelectorAll('.tag-item');
    existingChips.forEach(c => c.remove());

    const items = this.currentBundle.identity[propertyName] || [];
    items.forEach((item, index) => {
      const chip = document.createElement('span');
      chip.className = 'tag-item';
      chip.innerHTML = `${escapeHtml(item)} <span class="remove-tag" data-index="${index}">&times;</span>`;
      chip.querySelector('.remove-tag').addEventListener('click', (e) => {
        const idx = parseInt(e.target.getAttribute('data-index'), 10);
        this.currentBundle.identity[propertyName].splice(idx, 1);
        this.renderTags(containerId, inputId, propertyName);
        this.markDirty(propertyName);
        this.updateLivePreview();
      });
      container.insertBefore(chip, input);
    });
  }

  bindTraitPresets() {
    const presetsContainer = document.getElementById('trait-presets-bar');
    if (!presetsContainer) return;

    SUGGESTED_TRAIT_PRESETS.forEach(preset => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'btn-preset';
      btn.textContent = `+ ${preset.label}`;
      btn.addEventListener('click', () => {
        this.addTrait({
          id: `trait-${preset.id.replace('trait-', '')}-${Date.now().toString(36)}`,
          label: preset.label,
          description: preset.description,
          emphasis: preset.defaultEmphasis,
          context: ''
        });
      });
      presetsContainer.appendChild(btn);
    });

    const addCustomBtn = document.getElementById('btn-add-custom-trait');
    if (addCustomBtn) {
      addCustomBtn.addEventListener('click', () => {
        this.addTrait({
          id: `trait-custom-${Date.now().toString(36)}`,
          label: 'Custom Working Style',
          description: 'Describe the specific behavioral tendency or operating habit.',
          emphasis: 'unspecified',
          context: ''
        });
      });
    }
  }

  addTrait(traitObj) {
    this.currentBundle.identity.disposition_and_working_style =
      this.currentBundle.identity.disposition_and_working_style || [];
    this.currentBundle.identity.disposition_and_working_style.push(traitObj);
    this.renderTraitsList();
    this.markDirty('traits');
    this.updateLivePreview();
  }

  renderTraitsList() {
    const listContainer = document.getElementById('traits-list-container');
    if (!listContainer) return;

    const traits = this.currentBundle.identity.disposition_and_working_style || [];
    listContainer.innerHTML = '';

    if (traits.length === 0) {
      listContainer.innerHTML = `<div style="padding: 12px; color: var(--text-muted); font-style: italic;">
        No traits specified. Traits are optional tendencies and do not alter permissions or budgets.
      </div>`;
    }

    traits.forEach((trait, index) => {
      const card = document.createElement('div');
      card.className = 'trait-card';
      card.innerHTML = `
        <div class="trait-card-header">
          <input type="text" class="form-control trait-label-input" value="${escapeHtml(trait.label)}" placeholder="Trait Label" />
          <select class="form-control trait-emphasis-select">
            <option value="unspecified" ${trait.emphasis === 'unspecified' ? 'selected' : ''}>Unspecified</option>
            <option value="light" ${trait.emphasis === 'light' ? 'selected' : ''}>Light Emphasis</option>
            <option value="moderate" ${trait.emphasis === 'moderate' ? 'selected' : ''}>Moderate</option>
            <option value="strong" ${trait.emphasis === 'strong' ? 'selected' : ''}>Strong Emphasis</option>
          </select>
          <button type="button" class="btn-icon-danger remove-trait-btn" title="Remove Trait">&times;</button>
        </div>
        <textarea class="form-control trait-desc-input" placeholder="Plain-language behavioral description">${escapeHtml(trait.description)}</textarea>
        <input type="text" class="form-control trait-context-input" value="${escapeHtml(trait.context || '')}" placeholder="Optional applicability context (e.g. When reviewing unfamiliar code)" />
      `;

      const labelInput = card.querySelector('.trait-label-input');
      const empSelect = card.querySelector('.trait-emphasis-select');
      const descInput = card.querySelector('.trait-desc-input');
      const ctxInput = card.querySelector('.trait-context-input');
      const removeBtn = card.querySelector('.remove-trait-btn');

      labelInput.addEventListener('input', (e) => {
        trait.label = e.target.value;
        this.updateTensionAdvisories();
        this.markDirty('trait_label');
        this.updateLivePreview();
      });
      empSelect.addEventListener('change', (e) => {
        trait.emphasis = e.target.value;
        this.markDirty('trait_emp');
        this.updateLivePreview();
      });
      descInput.addEventListener('input', (e) => {
        trait.description = e.target.value;
        this.markDirty('trait_desc');
        this.updateLivePreview();
      });
      ctxInput.addEventListener('input', (e) => {
        trait.context = e.target.value;
        this.markDirty('trait_ctx');
        this.updateLivePreview();
      });
      removeBtn.addEventListener('click', () => {
        traits.splice(index, 1);
        this.renderTraitsList();
        this.markDirty('trait_remove');
        this.updateLivePreview();
      });

      listContainer.appendChild(card);
    });

    this.updateTensionAdvisories();
  }

  updateTensionAdvisories() {
    const advisoryBox = document.getElementById('trait-advisories');
    if (!advisoryBox) return;

    const traits = this.currentBundle.identity.disposition_and_working_style || [];
    const notices = detectAdvisoryTensions(traits);

    if (notices.length > 0) {
      advisoryBox.style.display = 'flex';
      advisoryBox.innerHTML = `<div>${notices.map(n => `<div>${escapeHtml(n)}</div>`).join('')}</div>`;
    } else {
      advisoryBox.style.display = 'none';
      advisoryBox.innerHTML = '';
    }
  }

  updateLivePreview() {
    const codeBlock = document.getElementById('preview-code');
    const tokenCountLabel = document.getElementById('token-count-label');
    const tokenTargetLabel = document.getElementById('token-target-label');
    const meterFill = document.getElementById('meter-fill');

    if (!codeBlock) return;

    const previewData = generateTierPreview(this.currentBundle);
    codeBlock.textContent = previewData.text;

    if (tokenCountLabel && tokenTargetLabel && meterFill) {
      const isUL = previewData.tier === 'ultralight';
      tokenCountLabel.textContent = `Identity Footprint: ~${previewData.tokenCount.toLocaleString()} tokens`;

      if (isUL) {
        tokenTargetLabel.textContent = `UL Target: 1,500 · Format Ceiling: 2,000`;
        const pct = Math.min(100, Math.round((previewData.tokenCount / previewData.ceilingTokens) * 100));
        meterFill.style.width = `${pct}%`;

        meterFill.className = 'meter-fill';
        if (previewData.tokenCount > 2000) {
          meterFill.classList.add('danger');
        } else if (previewData.tokenCount > 1500) {
          meterFill.classList.add('warning');
        }
      } else {
        const ceiling = previewData.configuredContext;
        tokenTargetLabel.textContent = `Configured Context Budget: ${ceiling.toLocaleString()} tokens`;
        const pct = Math.min(100, Math.round((previewData.tokenCount / ceiling) * 100));
        meterFill.style.width = `${pct}%`;
        meterFill.className = 'meter-fill';
      }
    }
  }

  bindRosterControls() {
    const addBtn = document.getElementById('btn-add-roster-instance');
    const rosterContainer = document.getElementById('roster-table-container');

    if (addBtn) {
      addBtn.addEventListener('click', () => {
        const idName = this.currentBundle.identity.name || 'Agent';
        const instanceId = `${idName}@mock-team-${Date.now().toString(36).slice(-4)}`;
        this.currentBundle.mock_roster = this.currentBundle.mock_roster || [];
        this.currentBundle.mock_roster.push({
          instance_id: instanceId,
          assigned_role: this.currentBundle.identity.role || 'Contributor',
          deployment_override: {
            credential_reference: this.currentBundle.deployment_preferences?.credential_reference || 'operator-managed:default'
          }
        });
        this.markDirty('roster_add');
        this.updateRosterView();
      });
    }

    if (rosterContainer) {
      rosterContainer.addEventListener('click', (e) => {
        const removeBtn = e.target.closest('.remove-roster-btn');
        if (removeBtn) {
          const idx = parseInt(removeBtn.getAttribute('data-index'), 10);
          this.currentBundle.mock_roster.splice(idx, 1);
          this.markDirty('roster_remove');
          this.updateRosterView();
        }
      });
    }
  }

  updateRosterView() {
    const container = document.getElementById('roster-table-container');
    if (!container) return;
    container.innerHTML = renderRosterTable(this.currentBundle.mock_roster);
  }

  getMatchingApplications(strategyId) {
    if (!strategyId || !Array.isArray(this.currentBundle.strategy_applications)) return [];
    return this.currentBundle.strategy_applications.filter(app => app && app.strategy_id === strategyId);
  }

  onCandidateFieldModified(strat, prop, newValue) {
    if (!strat) return;
    const oldValue = strat[prop];
    const isSame = Array.isArray(oldValue) && Array.isArray(newValue)
      ? (oldValue.length === newValue.length && oldValue.every((v, i) => v === newValue[i]))
      : (oldValue === newValue);
    if (isSame) return;

    strat[prop] = newValue;
    // Bump revision on committed candidate edit (Praxis R3 P2)
    strat.revision = (strat.revision || 1) + 1;

    // Explicitly invalidate affected evaluations for applications bound to this candidate (Praxis R3 P2)
    if (Array.isArray(this.currentBundle.strategy_applications)) {
      this.currentBundle.strategy_applications.forEach(app => {
        if (app && app.strategy_id === strat.id) {
          const priorState = app.sovereignty_state;
          app.strategy_revision = strat.revision;
          if (priorState === 'accepted' || priorState === 'declined') {
            app.sovereignty_state = 'unresolved';
            app.decline_reason = '';
            app.evaluation_notes = `Candidate modified to rev ${strat.revision}; prior ${priorState} evaluation invalidated.`;
          }
        }
      });
    }

    this.markDirty(`strat_${prop}`);
    this.renderStrategyDetails();
    this.renderStrategyListOnly();

    // Update editor ID badge to reflect the bumped revision
    const idBadge = document.getElementById('strat-editor-id-badge');
    if (idBadge) idBadge.textContent = `${strat.id || ''} (rev ${strat.revision || 1})`;

    // Update application select dropdown labels if open
    const appSelect = document.getElementById('strat-app-select');
    const matchingApps = this.getMatchingApplications(strat.id);
    if (appSelect && matchingApps.length > 0) {
      const currentApp = matchingApps[this.selectedAppIndex];
      const sovSelect = document.getElementById('strat-app-sovereignty');
      if (sovSelect && currentApp) {
        sovSelect.value = currentApp.sovereignty_state || 'unresolved';
      }
      matchingApps.forEach((app, idx) => {
        if (appSelect.options && appSelect.options[idx]) {
          const stateLabel = (app.sovereignty_state || 'unresolved').toUpperCase();
          appSelect.options[idx].textContent = `[${stateLabel}] ${app.task_context || 'Unnamed Task'} (${app.recipient_agent_id || 'Agent'})`;
        }
      });
    }
  }

  // --- Interactive Strategy Workbench (Findings R1, R6) ---
  bindStrategyControls() {
    const listContainer = document.getElementById('strategy-list-container');
    const addBtn = document.getElementById('btn-add-strategy');
    const viewToggleBtn = document.getElementById('btn-toggle-strategy-view');

    if (addBtn) {
      addBtn.addEventListener('click', () => {
        this.currentBundle.strategies = this.currentBundle.strategies || [];
        const newStrat = {
          id: `strat-${Date.now().toString(36)}`,
          revision: 1,
          title: 'New Cognitive Strategy',
          problem_class: 'Target class of cognitive problem',
          rationale_experience: 'Observed empirical evidence or hypothesis',
          assumptions: ['Standard operating environment'],
          invariants: ['Preserve data integrity'],
          step_sequence: ['Inspect initial conditions', 'Apply verified heuristic', 'Verify output'],
          observable_checks: ['Output adheres to expected schema'],
          stop_switch_conditions: ['Halt if precondition invariants fail'],
          attribution: this.currentBundle.bundle_metadata?.author || 'Contributor'
        };
        this.currentBundle.strategies.push(newStrat);
        this.selectedStrategyIndex = this.currentBundle.strategies.length - 1;
        this.selectedAppIndex = 0;
        this.markDirty('strategy_add');
        this.updateStrategyView();
      });
    }

    if (viewToggleBtn) {
      viewToggleBtn.addEventListener('click', () => {
        this.strategyViewMode = this.strategyViewMode === 'plain' ? 'wmc' : 'plain';
        viewToggleBtn.textContent = this.strategyViewMode === 'plain' ? 'Switch to Lexicon/WMC View' : 'Switch to Plain-Language View';
        this.renderStrategyDetails();
      });
    }

    if (listContainer) {
      listContainer.addEventListener('click', (e) => {
        const removeBtn = e.target.closest('.remove-strat-btn');
        if (removeBtn) {
          const idx = parseInt(removeBtn.getAttribute('data-index'), 10);
          const stratToRemove = (this.currentBundle.strategies || [])[idx];
          if (stratToRemove && stratToRemove.id && Array.isArray(this.currentBundle.strategy_applications)) {
            // Clean up any applications tied to this removed strategy candidate
            this.currentBundle.strategy_applications = this.currentBundle.strategy_applications.filter(
              app => app.strategy_id !== stratToRemove.id
            );
          }
          this.currentBundle.strategies.splice(idx, 1);
          if (this.selectedStrategyIndex >= this.currentBundle.strategies.length) {
            this.selectedStrategyIndex = Math.max(0, this.currentBundle.strategies.length - 1);
          }
          this.selectedAppIndex = 0;
          this.markDirty('strategy_remove');
          this.updateStrategyView();
          return;
        }

        const item = e.target.closest('.strategy-list-item');
        if (item) {
          const idx = parseInt(item.getAttribute('data-index'), 10);
          this.selectedStrategyIndex = idx;
          this.selectedAppIndex = 0;
          this.updateStrategyView();
        }
      });
    }

    // Bind reusable strategy candidate form inputs (Finding R6, R3 P2)
    const bindStratField = (elemId, prop) => {
      const el = document.getElementById(elemId);
      if (el) el.addEventListener('input', (e) => {
        const strat = (this.currentBundle.strategies || [])[this.selectedStrategyIndex];
        if (strat) {
          this.onCandidateFieldModified(strat, prop, e.target.value);
        }
      });
    };

    const bindStratLines = (elemId, prop) => {
      const el = document.getElementById(elemId);
      if (el) el.addEventListener('input', (e) => {
        const strat = (this.currentBundle.strategies || [])[this.selectedStrategyIndex];
        if (strat) {
          const lines = e.target.value.split('\n').map(s => s.trim()).filter(Boolean);
          this.onCandidateFieldModified(strat, prop, lines);
        }
      });
    };

    bindStratField('strat-field-title', 'title');
    bindStratField('strat-field-problem-class', 'problem_class');
    bindStratField('strat-field-rationale', 'rationale_experience');
    bindStratLines('strat-field-assumptions', 'assumptions');
    bindStratLines('strat-field-invariants', 'invariants');
    bindStratLines('strat-field-steps', 'step_sequence');
    bindStratLines('strat-field-checks', 'observable_checks');
    bindStratLines('strat-field-stops', 'stop_switch_conditions');
    bindStratField('strat-field-attribution', 'attribution');

    // Recipient Task Application Controls (Separated per R6)
    const appSelect = document.getElementById('strat-app-select');
    if (appSelect) {
      appSelect.addEventListener('change', (e) => {
        const idx = parseInt(e.target.value, 10);
        if (idx >= 0) {
          this.selectedAppIndex = idx;
          this.populateStrategyEditor();
          this.renderStrategyDetails();
        }
      });
    }

    const addAppBtn = document.getElementById('btn-add-strat-app');
    if (addAppBtn) {
      addAppBtn.addEventListener('click', () => {
        const strat = (this.currentBundle.strategies || [])[this.selectedStrategyIndex];
        if (!strat) return;
        this.currentBundle.strategy_applications = this.currentBundle.strategy_applications || [];
        const newApp = {
          application_id: `app-${Date.now().toString(36)}`,
          strategy_id: strat.id,
          strategy_revision: strat.revision || 1,
          recipient_agent_id: this.currentBundle.identity?.name || 'Agent',
          task_context: 'New Evaluation Task',
          sovereignty_state: 'unresolved',
          decline_reason: '',
          evaluation_notes: 'Initial evaluation draft'
        };
        this.currentBundle.strategy_applications.push(newApp);
        const matching = this.getMatchingApplications(strat.id);
        this.selectedAppIndex = Math.max(0, matching.length - 1);
        this.markDirty('strategy_app_add');
        this.updateStrategyView();
      });
    }

    const bindAppProp = (elemId, prop) => {
      const el = document.getElementById(elemId);
      if (el) {
        el.addEventListener('input', (e) => {
          const strat = (this.currentBundle.strategies || [])[this.selectedStrategyIndex];
          if (!strat) return;
          const matching = this.getMatchingApplications(strat.id);
          const currentApp = matching[this.selectedAppIndex];
          if (currentApp) {
            currentApp[prop] = e.target.value;
            this.markDirty(`app_${prop}`);
            this.renderStrategyDetails();
            if (appSelect && appSelect.options[this.selectedAppIndex]) {
              const stateLabel = (currentApp.sovereignty_state || 'unresolved').toUpperCase();
              appSelect.options[this.selectedAppIndex].textContent = `[${stateLabel}] ${currentApp.task_context || 'Unnamed Task'} (${currentApp.recipient_agent_id || 'Agent'})`;
            }
            this.renderStrategyListOnly();
          }
        });
      }
    };

    bindAppProp('strat-app-task-context', 'task_context');
    bindAppProp('strat-app-recipient', 'recipient_agent_id');
    bindAppProp('strat-app-decline-reason', 'decline_reason');

    const appSovSelect = document.getElementById('strat-app-sovereignty');
    if (appSovSelect) {
      appSovSelect.addEventListener('change', (e) => {
        const strat = (this.currentBundle.strategies || [])[this.selectedStrategyIndex];
        if (!strat) return;
        const matching = this.getMatchingApplications(strat.id);
        const currentApp = matching[this.selectedAppIndex];
        if (currentApp) {
          currentApp.sovereignty_state = e.target.value;
          this.markDirty('app_sovereignty');
          this.renderStrategyDetails();
          if (appSelect && appSelect.options[this.selectedAppIndex]) {
            const stateLabel = (currentApp.sovereignty_state || 'unresolved').toUpperCase();
            appSelect.options[this.selectedAppIndex].textContent = `[${stateLabel}] ${currentApp.task_context || 'Unnamed Task'} (${currentApp.recipient_agent_id || 'Agent'})`;
          }
          this.renderStrategyListOnly();
        }
      });
    }
  }

  updateStrategyView() {
    this.renderStrategyListOnly();
    this.populateStrategyEditor();
    this.renderStrategyDetails();
  }

  renderStrategyListOnly() {
    const listContainer = document.getElementById('strategy-list-container');
    if (listContainer) {
      listContainer.innerHTML = renderStrategyList(
        this.currentBundle.strategies,
        this.selectedStrategyIndex,
        this.currentBundle.strategy_applications
      );
    }
  }

  populateStrategyEditor() {
    const panel = document.getElementById('strategy-editor-panel');
    const strat = (this.currentBundle.strategies || [])[this.selectedStrategyIndex];

    if (!panel) return;
    if (!strat) {
      panel.style.display = 'none';
      return;
    }

    panel.style.display = 'flex';
    const idBadge = document.getElementById('strat-editor-id-badge');
    if (idBadge) idBadge.textContent = `${strat.id || ''} (rev ${strat.revision || 1})`;

    const setVal = (id, val) => {
      const el = document.getElementById(id);
      if (el) el.value = val || '';
    };

    setVal('strat-field-title', strat.title);
    setVal('strat-field-problem-class', strat.problem_class);
    setVal('strat-field-rationale', strat.rationale_experience);
    setVal('strat-field-assumptions', (strat.assumptions || []).join('\n'));
    setVal('strat-field-invariants', (strat.invariants || []).join('\n'));
    setVal('strat-field-steps', (strat.step_sequence || []).join('\n'));
    setVal('strat-field-checks', (strat.observable_checks || []).join('\n'));
    setVal('strat-field-stops', (strat.stop_switch_conditions || []).join('\n'));
    setVal('strat-field-attribution', strat.attribution);

    // Recipient Task Applications (Separated per R6)
    const matchingApps = this.getMatchingApplications(strat.id);
    const appSelect = document.getElementById('strat-app-select');
    const taskCtxInput = document.getElementById('strat-app-task-context');
    const recipientInput = document.getElementById('strat-app-recipient');
    const sovSelect = document.getElementById('strat-app-sovereignty');
    const declineReasonInput = document.getElementById('strat-app-decline-reason');

    if (this.selectedAppIndex >= matchingApps.length) {
      this.selectedAppIndex = Math.max(0, matchingApps.length - 1);
    }

    if (appSelect) {
      appSelect.innerHTML = '';
      if (matchingApps.length === 0) {
        const opt = document.createElement('option');
        opt.value = '-1';
        opt.textContent = '-- No task evaluations recorded --';
        appSelect.appendChild(opt);
        appSelect.disabled = true;
      } else {
        appSelect.disabled = false;
        matchingApps.forEach((app, idx) => {
          const opt = document.createElement('option');
          opt.value = String(idx);
          const stateLabel = (app.sovereignty_state || 'unresolved').toUpperCase();
          opt.textContent = `[${stateLabel}] ${app.task_context || 'Unnamed Task'} (${app.recipient_agent_id || 'Agent'})`;
          if (idx === this.selectedAppIndex) opt.selected = true;
          appSelect.appendChild(opt);
        });
      }
    }

    const currentApp = matchingApps[this.selectedAppIndex];
    const hasApp = Boolean(currentApp);

    if (taskCtxInput) {
      taskCtxInput.value = hasApp ? (currentApp.task_context || '') : '';
      taskCtxInput.disabled = !hasApp;
    }
    if (recipientInput) {
      recipientInput.value = hasApp ? (currentApp.recipient_agent_id || '') : '';
      recipientInput.disabled = !hasApp;
    }
    if (sovSelect) {
      sovSelect.value = hasApp ? (currentApp.sovereignty_state || 'unresolved') : 'unresolved';
      sovSelect.disabled = !hasApp;
    }
    if (declineReasonInput) {
      declineReasonInput.value = hasApp ? (currentApp.decline_reason || '') : '';
      declineReasonInput.disabled = !hasApp;
    }
  }

  renderStrategyDetails() {
    const detailContainer = document.getElementById('strategy-detail-preview');
    if (!detailContainer) return;

    const strat = (this.currentBundle.strategies || [])[this.selectedStrategyIndex];
    if (!strat) {
      detailContainer.textContent = 'No strategy selected or attached to bundle.';
      return;
    }

    const matchingApps = this.getMatchingApplications(strat.id);
    if (this.strategyViewMode === 'wmc') {
      detailContainer.textContent = formatStrategyWMC(strat, matchingApps);
    } else {
      detailContainer.textContent = formatStrategyPlainLanguage(strat, matchingApps);
    }
  }

  // --- Storage Controls (Export & Import) (Findings R1, R3, R4) ---
  bindStorageControls() {
    const exportBtn = document.getElementById('btn-export-bundle');
    const fixtureSelect = document.getElementById('select-preset-fixture');
    const loadFixtureBtn = document.getElementById('btn-load-fixture');
    const dropzone = document.getElementById('bundle-dropzone');
    const fileInput = document.getElementById('bundle-file-input');

    if (exportBtn) {
      exportBtn.addEventListener('click', async () => {
        this.currentBundle.bundle_metadata.updated_at = new Date().toISOString();
        const { filename, digest } = await downloadDraftBundle(this.currentBundle);
        this.markSaved(filename, digest);
      });
    }

    if (loadFixtureBtn && fixtureSelect) {
      loadFixtureBtn.addEventListener('click', () => {
        const fixtureKey = fixtureSelect.value;
        const fixture = EMBEDDED_FIXTURES[fixtureKey];
        if (fixture) {
          this.loadBundle(JSON.parse(JSON.stringify(fixture)));
          this.showStatusAlert(`Successfully loaded fixture '<strong>${escapeHtml(fixtureKey)}</strong>'`, 'success');
        } else {
          this.showStatusAlert(`Fixture '${escapeHtml(fixtureKey)}' not found in catalog.`, 'error');
        }
      });
    }

    if (dropzone && fileInput) {
      dropzone.addEventListener('click', () => fileInput.click());
      dropzone.addEventListener('dragover', (e) => {
        e.preventDefault();
        dropzone.classList.add('dragover');
      });
      dropzone.addEventListener('dragleave', () => dropzone.classList.remove('dragover'));
      dropzone.addEventListener('drop', async (e) => {
        e.preventDefault();
        dropzone.classList.remove('dragover');
        if (e.dataTransfer.files.length > 0) {
          await this.processImportFile(e.dataTransfer.files[0]);
        }
      });
      fileInput.addEventListener('change', async (e) => {
        if (e.target.files.length > 0) {
          await this.processImportFile(e.target.files[0]);
        }
      });
    }
  }

  // Safe Staged Import (Finding R1, R3)
  async processImportFile(file) {
    const result = await readAndValidateBundleFile(file);
    if (!result.ok) {
      const errList = result.errors.map(e => `• ${escapeHtml(e)}`).join('<br>');
      this.showStatusAlert(`Import validation failed (existing draft preserved):<br>${errList}`, 'error');
      return;
    }

    // Test-render in stage before committing to state
    try {
      generateTierPreview(result.bundle);
      if (Array.isArray(result.bundle.strategies)) {
        result.bundle.strategies.forEach(s => formatStrategyPlainLanguage(s));
      }
    } catch (stageErr) {
      this.showStatusAlert(`Staged rendering failed (existing draft preserved):<br>• ${escapeHtml(stageErr.message)}`, 'error');
      return;
    }

    // Both validation and rendering succeeded: commit to state
    this.loadBundle(result.bundle);
    const safeFilename = escapeHtml(file.name);
    this.showStatusAlert(`Bundle '<strong>${safeFilename}</strong>' imported cleanly.<br>Digest: <code>${result.digest}</code>`, 'success');
  }

  showStatusAlert(htmlMsg, type = 'success') {
    const alertBox = document.getElementById('import-status-alert');
    if (!alertBox) return;
    alertBox.className = `alert-box ${type}`;
    alertBox.innerHTML = htmlMsg;
    alertBox.style.display = 'block';
  }

  loadBundle(bundle) {
    this.currentBundle = bundle;
    this.selectedStrategyIndex = 0;
    this.selectedAppIndex = 0;
    this.isDirty = false;

    this.populateFormFromBundle();
    this.updateLivePreview();
    this.updateRosterView();
    this.updateStrategyView();

    const badge = document.getElementById('export-status-badge');
    if (badge) {
      badge.textContent = 'Imported · Ready to Edit';
      badge.style.backgroundColor = 'rgba(88, 166, 255, 0.15)';
      badge.style.color = 'var(--accent-blue)';
      badge.style.borderColor = 'rgba(88, 166, 255, 0.3)';
    }
    const digestDisplay = document.getElementById('export-digest-display');
    if (digestDisplay) {
      digestDisplay.textContent = '';
      digestDisplay.style.display = 'none';
    }
    const stateBadge = document.getElementById('draft-state-badge');
    if (stateBadge) {
      stateBadge.textContent = 'Draft Ready (Imported)';
    }
  }

  populateFormFromBundle() {
    const id = this.currentBundle.identity || {};
    const meta = this.currentBundle.bundle_metadata || {};
    const dep = this.currentBundle.deployment_preferences || {};

    const setVal = (idField, val) => {
      const el = document.getElementById(idField);
      if (el) el.value = val !== undefined && val !== null ? val : '';
    };

    setVal('field-name', id.name);
    setVal('field-role', id.role);
    setVal('field-agent-id', id.agent_id_template);
    setVal('field-task-size', id.intended_task_size || 'standard');
    setVal('field-handoff', id.handoff_expectations);
    setVal('field-author', meta.author);

    // Deployment fields (Finding R5)
    setVal('field-model-alias', dep.model_alias);
    setVal('field-backend-type', dep.backend_type || 'operator_local');
    setVal('field-context-limit', dep.context_limit || 8192);
    setVal('field-output-limit', dep.output_limit || 2048);
    setVal('field-reasoning-pref', dep.reasoning_preference || 'standard');
    setVal('field-tool-limit', dep.tool_call_limit !== undefined ? dep.tool_call_limit : 10);
    setVal('field-request-timeout', dep.request_timeout_sec || 120);
    setVal('field-run-timeout', dep.run_timeout_sec || 600);
    setVal('field-credential-ref', dep.credential_reference || 'operator-managed:local-default');

    // Tier radios
    const tierRadios = document.querySelectorAll('input[name="agent-tier"]');
    tierRadios.forEach(r => {
      r.checked = r.value === (id.tier || 'lightweight');
    });
    document.querySelectorAll('.tier-option').forEach(opt => {
      opt.classList.toggle('selected', opt.querySelector('input').checked);
    });

    this.renderTags('capabilities-tags', 'capabilities-input', 'capabilities');
    this.renderTags('exclusions-tags', 'exclusions-input', 'exclusions');
    this.renderTags('values-tags', 'values-input', 'values');
    this.renderTraitsList();
  }

  bindConfigurationControls() {
    const loadBtn = document.getElementById('btn-load-config');
    const fileInput = document.getElementById('config-file-input');
    const resetBtn = document.getElementById('btn-reset-config');

    if (loadBtn && fileInput) {
      loadBtn.addEventListener('click', () => fileInput.click());
      fileInput.addEventListener('change', async (e) => {
        if (e.target.files.length > 0) {
          const file = e.target.files[0];
          try {
            const text = await file.text();
            const res = loadStudioConfigFromJson(text, file.name);
            if (res.success) {
              this.showConfigAlert(`Loaded custom governance policy '<strong>${escapeHtml(file.name)}</strong>' (v${escapeHtml(CONFIG_STATE.version)}). Control bounds and active draft re-validated.`, 'success');
              this.markDirty('config_import');
              this.updateLivePreview();
            } else {
              this.showConfigAlert(`Failed to load governance policy (existing policy preserved):<br>• ${escapeHtml(res.error)}`, 'error');
            }
          } catch (err) {
            this.showConfigAlert(`Error reading policy file: ${escapeHtml(err.message)}`, 'error');
          }
          fileInput.value = '';
        }
      });
    }

    if (resetBtn) {
      resetBtn.addEventListener('click', () => {
        resetStudioConfigToDefaults();
        this.showConfigAlert('Reset governance policy to embedded baseline defaults.', 'success');
        this.markDirty('config_reset');
        this.updateLivePreview();
      });
    }
  }

  showConfigAlert(htmlMsg, type = 'success') {
    const alertBox = document.getElementById('config-status-alert');
    if (!alertBox) return;
    alertBox.className = `alert-box ${type}`;
    alertBox.innerHTML = htmlMsg;
    alertBox.style.display = 'block';
  }

  loadConfigFromJson(jsonStr, filename) {
    return loadStudioConfigFromJson(jsonStr, filename);
  }

  resetConfigToDefaults() {
    return resetStudioConfigToDefaults();
  }

  getConfigState() {
    return getStudioConfigState();
  }
}

// Instantiate on DOM load if running in a browser
if (typeof window !== 'undefined' && typeof window.addEventListener === 'function') {
  window.addEventListener('DOMContentLoaded', () => {
    const app = new StudioApp();
    app.init();
  });
}

// CommonJS module exports for Node / testing environments
if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    STUDIO_CONFIG,
    CONFIG_STATE,
    applyStudioConfig,
    validateStudioConfig,
    loadStudioConfigFromJson,
    loadStudioConfigFromObject,
    resetStudioConfigToDefaults,
    getStudioConfigState,
    updateConfigUI,
    escapeHtml,
    validateDraftBundle,
    StudioApp
  };
}
