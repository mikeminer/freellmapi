import contextlib
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import Mock, patch

import remote_access
from remote_pairing import request_pair


class LocalPairingTests(unittest.TestCase):
    def test_transient_windows_reply_lock_retries_read_without_another_pair_request(self):
        import remote_pairing
        original=remote_pairing.read_small;locked=[]
        def read(path,*args,**kwargs):
            if Path(path).suffix=='.reply' and Path(path).exists() and not locked:
                locked.append(str(path));raise PermissionError('Synthetic Windows sharing violation')
            return original(path,*args,**kwargs)
        with patch('remote_pairing.read_small',side_effect=read),patch.object(self.remote,'pair',wraps=self.remote.pair) as pair:
            result=request_pair(self.home)
        self.assertEqual(len(locked),1);self.assertEqual(pair.call_count,1)
        self.assertEqual(result['expires_seconds'],120);self.assertTrue(self.remote.inputs.empty())
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name)
        self.context = {'messages': [{'role': 'user', 'content': 'SESSION-CONTEXT-KEEP'}]}
        self.remote = remote_access.Remote(self.home, lambda: self.context).start(port=0, private_vpn=True)
        self.addCleanup(self.remote.close)

    def test_refresh_preserves_context_and_devices_but_invalidates_old_code(self):
        first = self.remote.pair()
        saved = self.remote.pair_device({'code': first['code']})
        previous = self.remote.pair()
        pair = request_pair(self.home, 600)
        self.assertEqual(pair['expires_seconds'], 600)
        self.assertGreater(self.remote.pair_deadline - time.monotonic(), 590)
        self.assertEqual(self.remote.authorize('Bearer ' + saved['token']), saved['device'])
        self.assertEqual(self.remote.state(saved['device'])['messages'], self.context['messages'])
        with self.assertRaises(PermissionError):
            self.remote.pair_device({'code': previous['code']})
        self.remote.pair_device({'code': pair['code']})
        with self.assertRaises(PermissionError):
            self.remote.pair_device({'code': pair['code']})
        self.assertTrue(self.remote.inputs.empty())
        self.assertFalse(list(self.remote.local_pairing.folder.glob('*.reply')))
        self.assertEqual(self.remote.sequence, 0)

    def test_default_stays_short_and_invalid_lifetimes_do_not_replace_code(self):
        pair = request_pair(self.home)
        self.assertEqual(pair['expires_seconds'], 120)
        for value in (0, 119, 601, True, '600', float('inf')):
            with self.subTest(value=value), self.assertRaises(ValueError):
                request_pair(self.home, value)
            self.assertEqual(self.remote.pair_code, pair['code'])
            with self.assertRaises(ValueError):
                self.remote.pair(ttl=value)
            self.assertEqual(self.remote.pair_code, pair['code'])

    def test_extended_pair_still_expires_and_attempt_limit_still_applies(self):
        pair = request_pair(self.home, 600)
        deadline = self.remote.pair_deadline
        with patch('remote_access.time.monotonic', return_value=deadline + 1):
            with self.assertRaises(PermissionError):
                self.remote.pair_device({'code': pair['code']})
        pair = request_pair(self.home, 600)
        for _ in range(8):
            with self.assertRaises(PermissionError):
                self.remote.pair_device({'code': 'invalid'})
        with self.assertRaises(PermissionError):
            self.remote.pair_device({'code': pair['code']})

    def test_cli_returns_before_key_loading_model_api_or_agent_creation(self):
        import freellmapi
        with patch('webbrowser.open',return_value=True) as browser, patch.object(freellmapi, 'HOME', self.home), \
             patch.object(sys, 'argv', ['freellmapi', '--remote-pair', '--pair-seconds', '600']), \
             patch.object(freellmapi, 'Agent', side_effect=AssertionError('Must not create an agent')), \
             patch.object(freellmapi, 'stored_token', side_effect=AssertionError('Must not read API credentials')), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            freellmapi.main()
        self.assertIn('600 secondi', output.getvalue())
        self.assertIn(self.remote.pair_code, output.getvalue())
        browser.assert_called_once()

    def test_refresh_is_not_a_network_endpoint_or_remote_command(self):
        pair = self.remote.pair()
        device = self.remote.pair_device({'code': pair['code']})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        req = urllib.request.Request(self.remote.url + '/v1/pair/new', data=b'{"seconds":600}',
                                     headers={'Content-Type': 'application/json',
                                              'Authorization': 'Bearer ' + device['token']})
        with self.assertRaises(urllib.error.HTTPError) as result:
            opener.open(req, timeout=3)
        self.assertEqual(result.exception.code, 404)
        with self.assertRaises(ValueError):
            self.remote.enqueue(device['device'], {'request_id': 'pairtest01', 'text': '/remote pair'})

    def test_malformed_mailbox_request_is_rejected_without_killing_worker(self):
        folder = self.remote.local_pairing.folder
        path = folder / ('a' * 32 + '.request')
        path.write_text(json.dumps({'seconds': 600, 'command': 'do not execute'}), encoding='utf-8')
        deadline = time.monotonic() + 3
        reply = path.with_suffix('.reply')
        while not reply.exists() and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertIn('error', json.loads(reply.read_text(encoding='utf-8')))
        self.assertEqual(request_pair(self.home)['expires_seconds'], 120)
        self.assertTrue(self.remote.local_pairing.worker.is_alive())

    def test_missing_closed_or_stale_bridge_fails_without_starting_a_new_session(self):
        with self.assertRaises(ValueError):
            request_pair(self.home / 'missing')
        self.remote.close()
        with self.assertRaises(ValueError):
            request_pair(self.home)
        folder = self.home / 'remote/local-pairing' / ('b' * 32)
        folder.mkdir()
        (self.home / 'remote/local-pairing.json').write_text(json.dumps({'instance': 'b' * 32}))
        with patch('remote_pairing.check_instance'), self.assertRaises(TimeoutError):
            request_pair(self.home, timeout=.1)
        self.assertFalse(list(folder.glob('*.request')))

    def test_second_bridge_cannot_bind_the_same_endpoint_or_replace_local_pointer(self):
        pointer = self.remote.local_pairing.pointer.read_bytes()
        port = self.remote.server.server_port
        other = remote_access.Remote(self.home, lambda: {})
        with patch('remote_access._active', None), self.assertRaisesRegex(ValueError, 'Porta Remote già occupata'):
            other.start(port=port, private_vpn=True)
        self.assertEqual(self.remote.local_pairing.pointer.read_bytes(), pointer)
        self.assertEqual(request_pair(self.home)['url'], self.remote.url)

    def test_windows_listener_cannot_be_hijacked_by_legacy_reuse_socket(self):
        self.assertEqual(self.remote.server.socket.getsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR), 0)
        if os.name == 'nt':
            self.assertEqual(self.remote.server.socket.getsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE), 1)
        with socket.socket() as attempt:
            attempt.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            with self.assertRaises(OSError):
                attempt.bind(('127.0.0.1', self.remote.server.server_port))

    def test_refresh_checks_real_listener_before_replacing_a_code(self):
        pair = self.remote.pair()
        pointer = self.remote.local_pairing.pointer
        metadata = json.loads(pointer.read_text(encoding='utf-8'))
        metadata['instance'] = 'c' * 32
        # Keep the selected mailbox real, but make its expected listener differ.
        with patch('remote_pairing.read_small', return_value=metadata), \
             patch('remote_pairing.Path.is_dir', return_value=True), \
             self.assertRaisesRegex(ValueError, 'non coincide'):
            request_pair(self.home)
        self.assertEqual(self.remote.pair_code, pair['code'])
        self.assertFalse(list(self.remote.local_pairing.folder.glob('*.request')))

    def test_https_probe_rejects_wrong_pin_before_generating_code(self):
        home = self.home / 'tls'
        with patch('remote_access._active', None):
            server = remote_access.Remote(home, lambda: {}).start(port=0)
        self.addCleanup(server.close)
        server.pair()
        code = server.pair_code
        pointer = server.local_pairing.pointer
        metadata = json.loads(pointer.read_text(encoding='utf-8'))
        metadata['fingerprint'] = '0' * 64
        pointer.write_text(json.dumps(metadata), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Certificato Remote diverso'):
            request_pair(home)
        self.assertEqual(server.pair_code, code)

    def test_https_probe_checks_known_pin_before_sending_http(self):
        from cryptography import x509
        from cryptography.hazmat.primitives import serialization
        from remote_pairing import check_instance
        _, fingerprint = remote_access.tls_context(self.remote.folder)
        certificate = x509.load_pem_x509_certificate((self.remote.folder / 'server.pem').read_bytes())
        metadata = {'url': 'https://127.0.0.1:31499', 'instance': self.remote.instance,
                    'fingerprint': fingerprint}
        connection = Mock()
        connection.sock.getpeercert.return_value = certificate.public_bytes(serialization.Encoding.DER)
        response = connection.getresponse.return_value
        response.status = 401
        response.getheader.return_value = self.remote.instance
        with patch('remote_pairing.http.client.HTTPSConnection', return_value=connection):
            check_instance(metadata)
            self.assertEqual(connection.request.call_count, 1)
            metadata['fingerprint'] = '0' * 64
            with self.assertRaisesRegex(ValueError, 'Certificato Remote diverso'):
                check_instance(metadata)
            self.assertEqual(connection.request.call_count, 1)


if __name__ == '__main__':
    unittest.main()
