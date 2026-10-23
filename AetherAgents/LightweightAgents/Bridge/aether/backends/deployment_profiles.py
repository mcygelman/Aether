"""DRAFT v3 deployment configuration: Backends' dialect selection for deployment profiles.

The records and their validation moved to the Model Catalog on 2026-10-08
(aether.models.deployment_profiles); every name is re-exported here, so imports from
this module keep working. deployment_dialect uses the same check_dialect table.
"""
from aether.models.deployment_profiles import (  # noqa: F401  re-exported
    ADAPTER_TYPES, AVAILABLE_ADAPTERS, IDENTIFIER, WINDOWS_RESERVED, CredentialRef,
    DeploymentProfile, RuntimeProfile, RuntimeProfileError, check_dialect, endpoint_identity,
    migrate_deployments, validate_deployments, validate_registry)


def deployment_dialect(adapter_type, requested):
    """v3 adapter authority; a hostname never selects a provider dialect."""
    from aether.backends.dialect import DEEPSEEK, OPENAI_ONLY
    check_dialect(adapter_type, requested)
    return DEEPSEEK if adapter_type == 'deepseek' or requested == 'deepseek' else OPENAI_ONLY


def profile_dialect(profile):
    if type(profile) is DeploymentProfile:
        return deployment_dialect(profile.adapter_type, profile.dialect)
    from aether.backends.model import select_dialect
    return select_dialect(profile.endpoint, profile.dialect)
