"""Secret plumbing for the host only. Never return secrets from the CLI or errors."""
from aether.paths import BRIDGE_ROOT
from pathlib import Path
import argparse
import hmac
import os

from aether.integrity import sealbox


class CredentialError(ValueError):
    pass


def bridge_root():
    return BRIDGE_ROOT


def resolve_reference(reference, *, bridge=None):
    """Resolve one validated v3 reference for a host operation; never fall back."""
    from aether.models.deployment_profiles import CredentialRef
    if reference is None:
        return ''
    try:
        ref = CredentialRef.from_mapping(reference)
        if ref.kind == 'env':
            value = os.environ.get(ref.name, '')
        else:
            directory = (Path(bridge) if bridge is not None else bridge_root()) / 'secrets_local'
            value = sealbox.open_file(directory / (ref.name + '.sealed'),
                                      directory / '.sealbox_key', require_sealed=True)
        if not isinstance(value, str) or not value or any(c.isspace() for c in value):
            raise ValueError
        return value
    except Exception:
        raise CredentialError('Selected credential unavailable; configure its source locally.') from None


def load_deepseek_key(source='sealed', *, bridge=None):
    """Internal transport helper; callers must never log the returned value."""
    try:
        if source == 'env':
            value = os.environ.get('AETHER_API_KEY', '')
        elif source == 'sealed':
            directory = (Path(bridge) if bridge is not None else bridge_root())/'secrets_local'
            value = sealbox.open_file(directory/'deepseek_api_key.sealed',
                                      directory/'.sealbox_key', require_sealed=True)
        else:
            raise CredentialError('Unknown key source')
        if not value or value != value.strip() or any(c.isspace() for c in value):
            raise CredentialError('Missing or malformed key')
        return value
    except Exception:
        raise CredentialError('DeepSeek credential unavailable; configure the selected key source locally.') from None


def seal_deepseek_from_environment(*, bridge=None):
    """Encrypt env bytes in memory, verify, then atomically replace the sealed file."""
    value = load_deepseek_key('env')  # Missing environment cannot change the old store.
    try:
        directory = (Path(bridge) if bridge is not None else bridge_root())/'secrets_local'
        directory.mkdir(parents=True, exist_ok=True)
        keyfile = directory/'.sealbox_key'
        if not keyfile.exists():
            sealbox.provision_key(directory)
        raw = value.encode('utf-8')
        sealed = sealbox.seal_bytes(raw, keyfile)
        if not hmac.compare_digest(raw, sealbox.open_bytes(sealed, keyfile, require_sealed=True)):
            raise CredentialError('Verification failed')
        target = directory/'deepseek_api_key.sealed'
        sealbox.atomic_write_bytes(target, sealed)
        if not hmac.compare_digest(raw, sealbox.open_bytes(target.read_bytes(), keyfile, require_sealed=True)):
            raise CredentialError('Readback verification failed')
    except Exception:
        raise CredentialError('Credential sealing failed; no secret detail is logged.') from None
    finally:
        value = None  # No plaintext file, log, fingerprint or command-line argument.
    return {'status': 'SEALED', 'source': 'AETHER_API_KEY', 'readback_verified': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['seal-deepseek-from-env'])
    parser.parse_args()
    try:
        seal_deepseek_from_environment()
    except CredentialError:
        print('Credential sealing did not complete. Set AETHER_API_KEY locally and verify access to secrets_local; key contents were not logged.')
        return 1
    print('DeepSeek credential sealed from AETHER_API_KEY; read-back verified. No key contents logged.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
