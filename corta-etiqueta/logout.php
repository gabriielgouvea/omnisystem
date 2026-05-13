<?php
header('Content-Type: application/json');

if ($_SERVER['REQUEST_METHOD'] === 'OPTIONS') { exit; }

session_start();
session_destroy();

// Expira o cookie
setcookie('app_token', '', [
    'expires'  => time() - 3600,
    'path'     => '/',
    'httponly' => true,
    'samesite' => 'Strict',
]);

echo json_encode(['ok' => true]);
