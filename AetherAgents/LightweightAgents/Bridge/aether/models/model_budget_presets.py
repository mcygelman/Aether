"""User-authored budget presets beside a fixed registry; no inferred model limits."""
from aether.config.context_configuration import ContextBudget, ContextConfigurationError, integer
from aether.config.model_settings_store import parse_json
from aether.models.settings_store import SettingsStore

BASIC = ('contextWindow', 'maxOutputTokens', 'timeoutSecs', 'maxToolCalls')


def validate_presets(document):
    if (not isinstance(document, dict) or set(document) != {'schemaVersion', 'presets'}
            or type(document['schemaVersion']) is not int or document['schemaVersion'] != 1
            or not isinstance(document['presets'], dict)):
        raise ContextConfigurationError('Preset file requires schemaVersion 1 and a presets object')
    for name, values in document['presets'].items():
        if not isinstance(name, str) or not name.strip() or name != name.strip():
            raise ContextConfigurationError('Preset name must be nonblank text without outer whitespace')
        if not isinstance(values, dict) or set(values) not in ({*BASIC, 'contextBudget'},{*BASIC,'contextBudget','tokenCountPolicy'}):
            raise ContextConfigurationError(f'Preset {name!r} requires operating values and contextBudget only')
        for key in BASIC: integer(values[key], key, minimum=1)
        ContextBudget.from_mapping(values['contextBudget']).validate_envelope(
            values['contextWindow'], values['maxOutputTokens'])
        if 'tokenCountPolicy' in values:
            from aether.config.token_count_policy import TokenCountPolicy
            TokenCountPolicy.from_mapping(values['tokenCountPolicy'])
    return document


class BudgetPresetStore(SettingsStore):
    """Missing sidecar is an empty catalog; first save still uses revision/lock checks."""
    def __init__(self, registry_path, *, operator_registry_path=None, operators=None):
        super().__init__(registry_path.with_name(registry_path.name + '.budget-presets.json'),
                         operator_registry_path=operator_registry_path, operators=operators)

    @staticmethod
    def document_decoder(raw):
        return validate_presets(parse_json(raw))

    def read_bytes(self):
        try:
            return super().read_bytes()
        except FileNotFoundError:
            return b'{"schemaVersion":1,"presets":{}}\n'

    def validate_transition(self, current, document, migrate):
        if migrate:
            raise ContextConfigurationError('Preset catalogs do not migrate profile formats')
