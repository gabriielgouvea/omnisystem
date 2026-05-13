<?php
header('Content-Type: application/json');

if ($_SERVER['REQUEST_METHOD'] === 'OPTIONS') { exit; }

session_start();

$token        = $_COOKIE['app_token'] ?? '';
$sessionToken = $_SESSION['token'] ?? '';

if (
    !empty($_SESSION['logged_in']) &&
    !empty($token) &&
    hash_equals($sessionToken, $token)
) {
    echo json_encode([
        'ok'       => true,
        'username' => $_SESSION['user'],
    ]);
} else {
    http_response_code(401);
    echo json_encode(['ok' => false]);
}
