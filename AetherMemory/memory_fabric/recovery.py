"""Restore a verified full snapshot into a NEW path; never overwrite a mind.

Recovery point is the backup's recorded revision. Work after that snapshot is
not recovered by this operation. Encryption/key recovery is a separate boundary.
"""
from .cartridge import Cartridge, CartridgeError
from .history import revision


def restore_backup(source, destination, *, expected_owner):
    cart = Cartridge.open(source, read_only=True)
    try:
        if cart.agent_id != expected_owner:
            raise CartridgeError('backup owner does not match requested owner')
        if cart.report.status != 'sealed':
            raise CartridgeError('restore requires a clean sealed snapshot; retain damaged files for diagnosis')
        if cart.conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise CartridgeError('backup integrity check failed')
        if cart.conn.execute('PRAGMA foreign_key_check').fetchone() is not None:
            raise CartridgeError('backup relationships failed validation')
        at_revision = revision(cart)
        cart.backup_to(destination)
        return {'destination': destination, 'owner': expected_owner, 'revision': at_revision,
                'text': 'Recovered through this backup revision; later work requires a later backup.'}
    finally:
        cart.close()
