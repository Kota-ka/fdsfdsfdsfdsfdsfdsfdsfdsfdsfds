"""Native tosu settings and a loopback-only live cursor stream."""

from dataclasses import asdict
import copy
import json
from pathlib import Path
import re
import shutil
import time
from urllib.request import Request
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import keyboard
import psutil

from aim_physics import AIM_LIMITS, AIM_PROFILES, AimConfig
from overlay_stream import OverlayStream

FOLDER = 'AimAssist'
OVERLAY_REVISION = '11'
LABELS = {
    'strength': ('Força', 'Fração da correção desejada, de 0 a 1.'),
    'hand_gain': ('Influência da mão', 'Durante a correção: 1 mantém todo o movimento; 0.35 deixa 35% para microajustes. Fora da assistência, o movimento fica livre.'),
    'curvature': ('Curvatura dos jumps', '0 a 1. Com Exigir movimento desativado, planeja curvas até a nota usando o tempo restante e a direção da próxima.'),
    'wiggle': ('Wiggle entre notas', 'Variação suave e diferente por salto. Reduzida em saltos rápidos; desaparece na chegada. Não modifica o caminho de sliders.'),
    'click_margin': ('Raio seguro do clique', 'Fração do raio da nota usada pelo Relax: 0.9 exige entrar um pouco mais no círculo; 1 aceita até a borda.'),
    'damping_ratio': ('Amortecimento da mola', '1 é firme; 0.6 é elástico. O amortecimento aumenta suavemente na chegada à nota.'),
    'coast_ms': ('Deslizamento nas folgas · ms', 'Desacelera a última correção gradualmente, sem laços automáticos. 0 desativa; requer Exigir movimento desativado.'),
    'click_settle_ms': ('Estabilidade antes do clique · ms', 'Tempo real observado dentro da nota antes de disparar; limitado automaticamente pela janela Great. Não é um offset da música.'),
    'slider_smooth_ms': ('Suavização dos sliders · ms', 'Arredonda curvas e reversões com limite pelo raio da nota. 0 mantém o caminho sem esse filtro; a cabeça do slider é preservada.'),
    'idle_dance': ('Dançar nas pausas entre notas', 'Laços suaves durante folgas da partida, com Exigir movimento desativado. A intensidade segue Curvatura. Spinner fica manual.'),
    'smooth_ms': ('Suavização da mola · ms', 'Mola amortecida. Maior = resposta mais gradual; 1 ms deixa a resposta brusca.'),
    'radius': ('FOV / raio de captura', 'Unidades do mapa. Este é o raio realmente usado pelo assist.'),
    'timing_ms': ('Janela da nota · ms', 'Antecedência para capturar a nota. Não é a latência da overlay.'),
    'cone_deg': ('Cobertura angular · graus', '0–180° por lado. Tanto 180° quanto 360° permitem todas as direções, por compatibilidade.'),
    'return_close_ms': ('Liberação perto · ms', 'Libera a correção na posição atual, sem puxar o cursor de volta.'),
    'return_far_ms': ('Liberação longe · ms', 'Libera a correção sem alvo próximo, preservando a posição atual.'),
    'motion_curve': ('Curva da força', 'Progressão ao aproximar a nota. Com força em 1, não reduz a correção.'),
    'max_speed': ('Velocidade máxima', 'Unidades do mapa por segundo, apenas para a correção.'),
    'max_offset': ('Limite local da correção', 'Raio em torno da referência que acompanha o movimento, em unidades do mapa.'),
    'max_accel': ('Aceleração máxima', 'Unidades do mapa por segundo ao quadrado.'),
    'require_motion': ('Exigir movimento da mão', 'Ativo: slider recebe apenas correção lateral. Desativado: acompanha o slider nos dois eixos, mesmo com a mão parada.'),
    'on_note_hold': ('Manter ao entrar na nota', 'Preserva o deslocamento em círculos já alcançados.'),
    'tablet': ('Modo tablet', 'Interpreta novas amostras como coordenadas absolutas.'),
}
EXTRAS = [
    ('enabled', 'Assistência ativa', 'checkbox', True, 'Ativa ou desativa somente o aim assist.'),
    ('autoclicks', 'Cliques automáticos · Relax', 'checkbox', True, 'Desative para manter apenas a assistência de mira.'),
    ('autotiming', 'Calibrar timing pelo jogo', 'checkbox', True, 'Aprende com grupos de 8 acertos. Compensação gradual limitada a ±30 ms do relógio do mapa.'),
    ('holdkey', 'Tecla para ativar · opcional', 'text', '', 'Vazio = ativo durante a partida. Exemplo: right ctrl.'),
    ('key1', 'Tecla de jogo 1 · opcional', 'text', '', 'Use a tecla configurada no osu!. Vazio usa o tosu; no lazer pode ser necessário preencher.'),
    ('key2', 'Tecla de jogo 2 · opcional', 'text', '', 'Segunda tecla configurada no osu!, diferente da primeira.'),
    ('debug', 'Log detalhado no console', 'checkbox', False, 'Mostra os motivos de bloqueio e os movimentos enviados.'),
    ('showfov', 'Mostrar FOV', 'checkbox', True, 'Círculo com o raio de captura em torno do cursor.'),
    ('showlimit', 'Mostrar limite da correção', 'checkbox', True, 'Círculo menor em torno da posição estimada da mão.'),
    ('showtarget', 'Mostrar alvo e ligação', 'checkbox', True, 'Marca o alvo usado pelo cálculo.'),
    ('showpath', 'Mostrar curva do slider', 'checkbox', True, 'Visualiza o caminho do slider selecionado.'),
    ('showstatus', 'Mostrar estado na overlay', 'checkbox', True, 'Indica quando o assist atua ou está bloqueado.'),
    ('fovcolor', 'Cor do FOV', 'color', '#55dfcf', 'Cor do círculo de captura.'),
    ('fovopacity', 'Opacidade do FOV', 'number', .65, 'De 0 a 1.'),
    ('fovwidth', 'Espessura do FOV · px', 'number', 1.5, 'De 0.5 a 8 pixels.'),
]


def schema(config, hold_key=None, debug=False):
    result = []
    for name, value in asdict(config).items():
        title, description = LABELS[name]
        item = dict(uniqueID=name.replace('_', ''), field=name, title=title, description=description,
                    type='checkbox' if isinstance(value, bool) else 'number', value=value, section='Mira')
        if name in AIM_LIMITS:
            item.update(min=AIM_LIMITS[name][0], max=AIM_LIMITS[name][1],
                        step=.01 if name in ('strength', 'motion_curve', 'hand_gain', 'curvature', 'wiggle', 'click_margin', 'damping_ratio') else 1)
        result.append(item)
    for key, title, kind, value, description in EXTRAS:
        if key == 'holdkey':
            value = hold_key or ''
        if key == 'debug':
            value = debug
        item = dict(uniqueID=key, title=title, type=kind, value=value, description=description,
                    section='Visualização' if key.startswith(('show', 'fov')) else 'Ativação')
        if key == 'fovopacity':
            item.update(min=0, max=1, step=.05)
        if key == 'fovwidth':
            item.update(min=.5, max=8, step=.5)
        result.append(item)
    return result


def validate_settings(values, settings):
    clean = {}
    for item in settings:
        key = item['uniqueID']
        value = values.get(key, item['value'])
        if item['type'] == 'checkbox' and not isinstance(value, bool):
            raise ValueError(f'{item["title"]}: esperado verdadeiro ou falso')
        if item['type'] == 'number':
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f'{item["title"]}: esperado um número')
            if not item['min'] <= value <= item['max']:
                raise ValueError(f'{item["title"]}: use {item["min"]} a {item["max"]}')
        clean[key] = value
    if not isinstance(clean['fovcolor'], str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', clean['fovcolor']):
        raise ValueError('Cor do FOV inválida')
    for name in ('holdkey','key1','key2'):
        key = clean[name]
        if not isinstance(key, str) or len(key) > 40:
            raise ValueError('Tecla inválida: '+name)
        if key.strip():
            keyboard.key_to_scan_codes(key.strip())
    config = AimConfig(**{item['field']: clean[item['uniqueID']] for item in settings if 'field' in item})
    return config, clean


def find_tosu(directory=None):
    if directory:
        root = Path(directory).resolve()
        if not (root / 'static').is_dir():
            raise ValueError('A pasta do tosu deve conter a pasta static')
        return root
    for process in psutil.process_iter(['name', 'exe']):
        if (process.info['name'] or '').lower() == 'tosu.exe' and process.info['exe']:
            return Path(process.info['exe']).parent
    raise ValueError('Abra o tosu ou informe --tosu-dir com a pasta dele')


def native_layout(values, width=1920, height=1080):
    """Append one fullscreen layer without changing any existing overlay."""
    result = copy.deepcopy(values)
    profiles = result.setdefault('profiles', [])
    if not profiles:
        profiles.append(dict(id='default', name='default', overlays=[]))
        result.setdefault('ingame_profile', 'default')
        result.setdefault('obs_profile', 'default')
    profile = next((p for p in profiles if p['id'] == result.get('ingame_profile')), None)
    if profile is None:
        raise ValueError('O perfil ativo da overlay do tosu não foi encontrado')
    overlays = profile.setdefault('overlays', [])
    existing = next((item for item in overlays if item.get('folderName') == FOLDER), None)
    if existing is not None:
        parsed = urlsplit(existing.get('url', ''))
        if parsed.path.rstrip('/') != '/' + FOLDER:
            return result, False
        query = parse_qsl(parsed.query, keep_blank_values=True)
        if ('overlayRev', OVERLAY_REVISION) in query:
            return result, False
        query = [(key, value) for key, value in query if key != 'overlayRev']
        query.append(('overlayRev', OVERLAY_REVISION))
        existing['url'] = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))
        return result, True
    overlays.append(dict(_enabled=True, _settings=True, id=max([item.get('id', 0) for item in overlays] + [0]) + 1,
                         folderName=FOLDER, url='tosu://server/' + FOLDER + '/?overlayRev=' + OVERLAY_REVISION, width=width, height=height,
                         top=0, left=0, scale=1, z_index=max([item.get('z_index', 0) for item in overlays] + [0]) + 1))
    return result, True


class TosuOverlay:
    def __init__(self, root, aim, playback):
        self.root = Path(root)
        self.running = True
        self.aim, self.playback = aim, playback
        self.folder = Path(root) / 'static' / FOLDER
        self.values_file = Path(root) / 'settings' / (FOLDER + '.values.json')
        self.settings = schema(aim.physics.config, aim.hold_key, aim.debug)
        self.values = {item['uniqueID']: item['value'] for item in self.settings}
        self.applied = None
        self.error = None
        self.last_poll = self.last_publish = 0.0
        self.folder.mkdir(parents=True, exist_ok=True)
        assets = Path(__file__).resolve().parent / 'overlay'
        for filename in ('index.html', 'app.js', 'app.css', 'metadata.txt'):
            shutil.copyfile(assets / filename, self.folder / filename)
        (self.folder / 'settings.json').write_text(json.dumps(self.settings, ensure_ascii=False), encoding='utf-8')
        presets = {name: {field.replace('_', ''): value for field, value in asdict(AimConfig(**preset)).items() if field != 'tablet'}
                   for name, preset in AIM_PROFILES.items() if name != 'aimbot'}
        (self.folder / 'presets.json').write_text(json.dumps(presets), encoding='utf-8')
        defaults = {item['uniqueID']: item['value'] for item in schema(AimConfig())}
        (self.folder / 'defaults.json').write_text(json.dumps(defaults), encoding='utf-8')
        self.values_file.parent.mkdir(parents=True, exist_ok=True)
        if not self.values_file.exists():
            self.values_file.write_text(json.dumps(self.values), encoding='utf-8')
        self.stream = OverlayStream(self.sample_cursor)
        (self.folder / 'transport.json').write_text(json.dumps(dict(url=self.stream.url)), encoding='utf-8')
        self.poll(force=True)
        self.publish(force=True)

    def sample_cursor(self, data):
        now = time.time()
        data['source_age_ms'] = max(0, (now - data.get('source_updated', now)) * 1000)
        data['updated'] = now
        if data.get('active'):
            if data['source_age_ms'] > 150:
                data.update(active=False, reason='game telemetry stale')
            else:
                with self.aim.motion_lock:
                    if self.aim.user32.GetForegroundWindow() != self.aim.window[0]:
                        data.update(active=False, reason='waiting for osu! focus')
                    else:
                        point = self.aim.cursor()
                        offset = self.aim.physics.offset
                        data['cursor'] = list(point)
                        data['offset'] = list(offset)
                        data['hand'] = [point[i] - offset[i] for i in (0, 1)]
        return data

    def register_native(self, opener):
        url = 'http://127.0.0.1:24050/api/counters/settings/__ingame__'
        with opener.open(url, timeout=2) as response:
            previous = json.load(response)['values']
        with opener.open('http://127.0.0.1:24050/json/v2', timeout=2) as response:
            resolution = json.load(response).get('settings', {}).get('resolution', {})
        suffix = 'Fullscreen' if resolution.get('fullscreen') else ''
        width, height = resolution.get('width' + suffix, 1920), resolution.get('height' + suffix, 1080)
        if not all(isinstance(v, int) and 1 <= v <= 16384 for v in (width, height)):
            width, height = 1920, 1080
        updated, changed = native_layout(previous, width, height)
        if not changed:
            return
        backup = self.root / 'settings' / (FOLDER + '.ingame-backup.json')
        if not backup.exists():
            backup.write_text(json.dumps(previous, ensure_ascii=False), encoding='utf-8')
        request = Request(url, data=json.dumps(updated).encode('utf-8'), headers={'Content-Type': 'application/json'})
        with opener.open(request, timeout=2) as response:
            result = json.load(response)
        if result.get('result') != 'success':
            raise ValueError('O tosu não confirmou a instalação da camada FOV')

    def poll(self, force=False):
        now = time.monotonic()
        if not force and now - self.last_poll < .2:
            return
        self.last_poll = now
        try:
            candidate = json.loads(self.values_file.read_text(encoding='utf-8-sig'))
            if not isinstance(candidate, dict):
                raise ValueError('Configuração inválida')
            if candidate != self.applied:
                config, clean = validate_settings(candidate, self.settings)
                with self.aim.motion_lock:
                    aim_changed = (config != self.aim.physics.config or clean['enabled'] != self.aim.enabled
                                   or (clean['holdkey'].strip() or None) != self.aim.hold_key)
                    self.aim.physics.config = config
                    self.aim.enabled = clean['enabled']
                    self.aim.hold_key = clean['holdkey'].strip() or None
                    self.aim.debug = clean['debug'] or getattr(self.aim,'debug_override',False)
                    self.playback.auto_clicks = clean['autoclicks']
                    overrides = tuple(clean[key].strip().lower() or None for key in ('key1','key2'))
                    if overrides != (getattr(self.playback,'key_overrides',None) or (None,None)):
                        self.playback.release()
                    self.playback.key_overrides = overrides if any(overrides) else None
                    self.playback.auto_timing = clean['autotiming']
                    if aim_changed:
                        self.aim.reset()
                self.values, self.applied = clean, candidate
            self.error = None
        except (OSError, ValueError, TypeError, KeyError) as exc:
            self.error = str(exc)

    def publish(self, force=False):
        now = time.monotonic()
        data = dict(self.aim.telemetry, updated=time.time(), connected=self.running,
                    settings=self.values, settings_error=self.error)
        data['source_updated'] = data['updated']
        self.stream.update(data)
        # A slow snapshot remains for old clients; live frames never wait on disk or HTTP polling.
        if not force and now - self.last_publish < 1:
            return
        self.last_publish = now
        temporary = self.folder / 'state.tmp'
        try:
            temporary.write_text(json.dumps(data, allow_nan=False, ensure_ascii=False), encoding='utf-8')
            temporary.replace(self.folder / 'state.json')
        except (OSError, ValueError) as exc:
            self.error = str(exc)

    def close(self):
        self.running = False
        self.aim.telemetry.update(active=False, reason='program stopped')
        self.publish(force=True)
        self.stream.close()
