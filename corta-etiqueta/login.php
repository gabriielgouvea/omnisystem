<?php
header('Content-Type: application/json');

if ($_SERVER['REQUEST_METHOD'] === 'OPTIONS') { exit; }

// ── Rate limit: máx 5 tentativas por minuto por IP ──────────────
$ip       = $_SERVER['REMOTE_ADDR'] ?? 'unknown';
$rateFile = __DIR__ . '/data/rate_' . md5($ip) . '.json';

@mkdir(__DIR__ . '/data', 0700, true);

$rate = ['count' => 0, 'window_start' => time()];
if (file_exists($rateFile)) {
    $rate = json_decode(file_get_contents($rateFile), true);
}

// Reset janela se passou 1 minuto
if (time() - $rate['window_start'] > 60) {
    $rate = ['count' => 0, 'window_start' => time()];
}

if ($rate['count'] >= 5) {
    $wait = 60 - (time() - $rate['window_start']);
    http_response_code(429);
    echo json_encode(['error' => "Muitas tentativas. Aguarde {$wait}s."]);
    exit;
}

// ── Lê credenciais ───────────────────────────────────────────────
$body = json_decode(file_get_contents('php://input'), true);
$username = trim($body['username'] ?? '');
$password = trim($body['password'] ?? '');

if (!$username || !$password) {
    http_response_code(400);
    echo json_encode(['error' => 'Usuário e senha obrigatórios.']);
    exit;
}

// ── Conta a tentativa ANTES de verificar ────────────────────────
$rate['count']++;
file_put_contents($rateFile, json_encode($rate));

// ── Consulta banco (users.json) ──────────────────────────────────
$usersFile = __DIR__ . '/users.json';
if (!file_exists($usersFile)) {
    http_response_code(500);
    echo json_encode(['error' => 'Banco de usuários não encontrado.']);
    exit;
}

$users = json_decode(file_get_contents($usersFile), true);
$hash  = md5($password);
$found = false;

foreach ($users as $user) {
    if ($user['username'] === $username && $user['password'] === $hash) {
        $found = true;
        break;
    }
}

if (!$found) {
    $restantes = 5 - $rate['count'];
    http_response_code(401);
    echo json_encode(['error' => "Credenciais inválidas. Tentativas restantes: {$restantes}."]);
    exit;
}

// ── Login OK: cria sessão e cookie httpOnly ──────────────────────
session_start();
session_regenerate_id(true);
$_SESSION['user']       = $username;
$_SESSION['logged_in']  = true;
$_SESSION['login_time'] = time();

// Cookie httpOnly adicional com token simples
$token = bin2hex(random_bytes(32));
$_SESSION['token'] = $token;

setcookie('app_token', $token, [
    'expires'  => time() + 3600 * 8,   // 8 horas
    'path'     => '/',
    'httponly' => true,
    'samesite' => 'Strict',
]);

// Reset rate limit após login bem-sucedido
file_put_contents($rateFile, json_encode(['count' => 0, 'window_start' => time()]));

echo json_encode(['ok' => true, 'username' => $username]);
