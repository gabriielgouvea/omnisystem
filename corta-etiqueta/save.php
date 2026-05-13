<?php

if ($_SERVER['REQUEST_METHOD'] === 'OPTIONS') { exit; }

$data = json_decode(file_get_contents('php://input'), true);

if (empty($data['pdf']) || empty($data['filename'])) {
    http_response_code(400);
    echo json_encode(['error' => 'faltando pdf ou filename']);
    exit;
}

$filename = preg_replace('/[^a-zA-Z0-9._\-]/', '_', basename($data['filename']));
$bytes = base64_decode($data['pdf']);
file_put_contents(__DIR__ . '/outputs/' . $filename, $bytes);

echo json_encode(['ok' => true, 'filename' => $filename]);
