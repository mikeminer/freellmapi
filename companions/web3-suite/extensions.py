"""Local extensions and MCP stdio transport (2025-11-25 lifecycle)."""
import atexit
import json
import os
from pathlib import Path
import queue
import subprocess
import threading
import time

class MCP:
    def __init__(self, spec, cwd):
        from connections import resolve_env
        spec=resolve_env(spec)
        self.process = subprocess.Popen([spec['command'], *spec.get('args', [])], cwd=cwd,
            env={**os.environ, **spec.get('env', {})}, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding='utf-8', bufsize=1)
        self.inbox = queue.Queue(); self.serial = 0; self.request_lock=threading.RLock()
        threading.Thread(target=self.reader, daemon=True).start()
        atexit.register(self.close)
        try:
            result = self.request('initialize', {'protocolVersion':'2025-11-25', 'capabilities':{},
                'clientInfo':{'name':'freellmapi','version':'2.0'}})
            if result.get('protocolVersion') not in ('2024-11-05','2025-03-26','2025-06-18','2025-11-25'):
                raise ValueError('Versione MCP non supportata: ' + str(result.get('protocolVersion')))
            self.send({'jsonrpc':'2.0','method':'notifications/initialized'})
        except BaseException:
            self.close(); raise

    def reader(self):
        try:
            for line in self.process.stdout:
                try: self.inbox.put(json.loads(line))
                except ValueError: continue
        finally: self.inbox.put(None)

    def send(self, message):
        self.process.stdin.write(json.dumps(message) + '\n'); self.process.stdin.flush()

    def request(self, method, params, timeout=30):
        with self.request_lock: return self._request(method,params,timeout)

    def _request(self, method, params, timeout=30):
        self.serial += 1; ident = self.serial
        self.send({'jsonrpc':'2.0','id':ident,'method':method,'params':params})
        deadline = time.monotonic() + timeout
        while True:
            try: response = self.inbox.get(timeout=max(.01, deadline-time.monotonic()))
            except queue.Empty: raise TimeoutError('MCP timeout: ' + method) from None
            if response is None: raise RuntimeError('Server MCP terminato.')
            if 'method' in response and 'id' in response:
                self.send({'jsonrpc':'2.0','id':response['id'],'error':{'code':-32601,'message':'Client capability not supported'}})
            elif response.get('id') == ident:
                if 'error' in response: raise RuntimeError(str(response['error']))
                return response.get('result', {})
            if time.monotonic() >= deadline: raise TimeoutError('MCP timeout')

    def tools(self):
        result = []; cursor = None
        for _ in range(50):
            page = self.request('tools/list', {'cursor':cursor} if cursor else {})
            result.extend(page.get('tools', []))
            cursor = page.get('nextCursor')
            if not cursor: return result
        raise ValueError('Troppe pagine MCP.')

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try: self.process.wait(timeout=3)
            except subprocess.TimeoutExpired: self.process.kill(); self.process.wait()

class Extensions:
    def __init__(self, home):
        self.home = Path(home); self.clients = {}; self.routes = {}; self.definitions = []
        self.commands = {}; self.instructions = []; self.plugins = []

    def load(self, agent):
        for client in self.clients.values(): client.close()
        self.clients.clear(); self.routes.clear(); self.definitions.clear()
        self.commands.clear(); self.instructions.clear(); self.plugins.clear()
        path = self.home/'extensions.json'
        config = json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else {}
        servers = dict(config.get('mcpServers', {}))
        for entry in config.get('plugins', []):
            path = Path(entry).expanduser().resolve()
            manifest = json.loads((path/'plugin.json').read_text(encoding='utf-8-sig'))
            if not agent.approve('Caricare plugin locale ' + str(path) + '\n' + json.dumps(manifest, ensure_ascii=False)):
                continue
            self.plugins.append(manifest.get('name', path.name))
            self.instructions.append(manifest.get('instructions', ''))
            self.commands.update(manifest.get('commands', {}))
            for name, spec in manifest.get('mcpServers', {}).items():
                servers['plugin_' + path.name + '_' + name] = {**spec, 'cwd':str(path)}
        for name, spec in servers.items():
            if not agent.approve('Avviare server MCP ' + name + ': ' + json.dumps({k:v for k,v in spec.items() if k != 'env'})):
                continue
            client=None
            try:
                client = MCP(spec, spec.get('cwd', str(agent.root)))
                discovered=client.tools()
                self.clients[name] = client
                for tool in discovered:
                    alias = 'mcp_' + str(len(self.routes)) + '_' + ''.join(c if c.isalnum() else '_' for c in tool['name'])[:40]
                    self.routes[alias] = (client, tool['name'])
                    self.definitions.append({'type':'function','function':{'name':alias,
                        'description':f"MCP {name}: {tool.get('description','')}", 'parameters':tool['inputSchema']}})
            except Exception as e:
                if client is not None and name not in self.clients: client.close()
                print(f'MCP {name}: {e}')

    def call(self, agent, name, arguments):
        if not agent.approve('MCP ' + name + '\n' + json.dumps(arguments, ensure_ascii=False)): return 'MCP negato.'
        client, original = self.routes[name]
        return json.dumps(client.request('tools/call', {'name':original,'arguments':arguments}), ensure_ascii=False)[:30000]
