"""Opt-in SDK motion-mode comparison, with original-mode recovery record."""
import json
import os
import tempfile


class ModeLease:
    def __init__(self, sdk, record):
        self.sdk = sdk
        self.record = record
        self.original = None

    def acquire(self, requested=0):
        if requested not in (0,1):
            raise ValueError('SDK mode must be 0 or 1')
        if self.record.exists():
            raise RuntimeError(f'Unrestored SDK mode record exists: {self.record}; resolve before another mode test')
        original=self.sdk.get_fresh_mode()
        if original not in (0,1):
            raise RuntimeError(f'Cannot read original SDK mode: {original!r}; no mode change performed')
        # Publish a complete record before the first setter. A crash during JSON
        # writing must not leave an empty record that cannot be restored.
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', dir=self.record.parent,
                                             prefix='.sdk_mode_restore.',
                                             delete=False) as file:
                temporary = file.name
                json.dump({'original_mode': original, 'test_mode': requested,
                           'pid': os.getpid()}, file)
                file.flush()
                os.fsync(file.fileno())
            os.link(temporary, self.record)  # Fails if another lease already exists.
            directory = os.open(self.record.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if temporary is not None:
                os.unlink(temporary)
        self.original=original
        try:
            if original!=requested:
                self.sdk.set_fresh_mode(requested)
            verified=self.sdk.get_fresh_mode()
            if verified!=requested:
                raise RuntimeError(f'SDK mode verification failed: requested {requested}, read {verified!r}')
            return verified
        except BaseException:
            self.restore()
            raise

    def restore(self):
        if self.original is None:
            return
        if self.sdk.get_fresh_mode()!=self.original:
            self.sdk.set_fresh_mode(self.original)
        verified=self.sdk.get_fresh_mode()
        if verified!=self.original:
            raise RuntimeError(f'SDK mode restore failed; retain recovery record {self.record}')
        self.record.unlink()
        self.original=None
